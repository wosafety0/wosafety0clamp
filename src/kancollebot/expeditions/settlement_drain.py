"""P0.1 - page-driven global settlement drain (interface + offline contract).

The drain walks whatever settlement UI is currently on screen and stops as soon
as the visible pages are exhausted or an unrecognised page appears. It
deliberately knows nothing about fleets or results:

* no ``fleet_id`` is accepted, requested or inferred;
* no result attribution and no fleet-state reconciliation;
* a fleet that still reads ``status == 2`` in Poi never stalls the UI drain.

Every step is ``observe -> confirm stable -> one action -> re-observe``. A page
only counts as advanced once the *new* page has been identified for
``stable_frame_count`` consecutive observations. Elapsed time alone is never
treated as success, and the page just acted on is never acted on twice in a row.

Scope: P0.1 ships the interface, the enums, request validation and the offline
mocks/tests only. The production supervisor, the existing
``ContinuousSettlementController`` and the mission-result witness are untouched;
the legacy ``other_returns`` guard stays exactly where it is until P0.2.

A driver or observer exception is never converted into a :class:`DrainResult`:
the session cannot know whether the click landed, so the exception propagates
and no further action is attempted.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from kancollebot.observability.page_observation import MotionTracker

__all__ = [
    "DEFAULT_FRAME_POLL_INTERVAL_SECONDS",
    "DrainClock",
    "DrainErrorType",
    "DrainPageObserver",
    "DrainPageState",
    "DrainResult",
    "DrainUiDriver",
    "SettlementDrainRequest",
    "SettlementDrainSession",
]

#: How often the session re-samples the visible UI while waiting for a page to
#: become stable (or to change after an action). Kept out of the request so the
#: public request contract stays exactly the four documented parameters.
DEFAULT_FRAME_POLL_INTERVAL_SECONDS = 0.05


class DrainPageState(Enum):
    """A page the drain can recognise from the visible UI alone."""

    HOME_PORT = "home_port"
    #: ``RETURN_NOTICE`` is the "fleet has returned" notice. It is a settlement
    #: page, not an idle home port and not an unknown screen.
    RETURN_NOTICE = "return_notice"
    EXPEDITION_RESULT = "expedition_result"
    REWARD_PANEL = "reward_panel"
    NETWORK_ERROR_POPUP = "network_error_popup"
    UNKNOWN_DIALOG = "unknown_dialog"
    UNKNOWN = "unknown"


class DrainErrorType(Enum):
    """Why a drain stopped before reaching a stable home port."""

    MAX_ACTIONS_REACHED = "max_actions"
    TIMEOUT = "timeout"
    UI_STUCK = "ui_stuck"
    UNKNOWN_DIALOG = "unknown_dialog"
    NETWORK_ERROR = "network_error"
    UNKNOWN_PAGE = "unknown_page"
    #: The screen kept animating for the whole post-action observation budget, so
    #: neither "it advanced" nor "it is frozen" could be proven. Nothing is stuck
    #: and nothing failed: the client was simply still transitioning.
    OBSERVATION_BUDGET_EXHAUSTED = "observation_budget"
    #: The server already reported the settlement as collected while the client
    #: was still displaying the settlement page. Refreshing it is UI-only now:
    #: no further settlement click may be sent.
    SETTLED_UI_PENDING = "settled_ui_pending"


#: Pages that may be advanced with exactly one action.
_ADVANCEABLE_PAGES: frozenset[DrainPageState] = frozenset(
    {
        DrainPageState.RETURN_NOTICE,
        DrainPageState.EXPEDITION_RESULT,
        DrainPageState.REWARD_PANEL,
    }
)

#: The single page whose click *claims* the expedition reward. Clicking it a
#: second time is the double-claim the server guard exists to prevent; the other
#: advanceable pages are the reward screens it opens, and walking those is how
#: the client gets back to home port.
_CLAIM_PAGES: frozenset[DrainPageState] = frozenset({DrainPageState.RETURN_NOTICE})

#: Pages that must never be clicked and the error they map to.
_TERMINAL_PAGE_ERRORS: dict[DrainPageState, DrainErrorType] = {
    DrainPageState.NETWORK_ERROR_POPUP: DrainErrorType.NETWORK_ERROR,
    DrainPageState.UNKNOWN_DIALOG: DrainErrorType.UNKNOWN_DIALOG,
    DrainPageState.UNKNOWN: DrainErrorType.UNKNOWN_PAGE,
}

#: Errors after which a human must look at the client before anything else runs.
#: ``MAX_ACTIONS_REACHED`` is deliberately absent: it is a deliberate budget
#: stop with a still-recognisable page, so a fresh bounded drain may continue.
_MANUAL_INTERVENTION_ERRORS: frozenset[DrainErrorType] = frozenset(
    {
        DrainErrorType.TIMEOUT,
        DrainErrorType.UI_STUCK,
        DrainErrorType.UNKNOWN_DIALOG,
        DrainErrorType.NETWORK_ERROR,
        DrainErrorType.UNKNOWN_PAGE,
    }
)

def _require_positive_int(name: str, value: object) -> int:
    """Reject bools (``bool`` is an ``int`` subclass) and non-ints."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int, got {type(value).__name__}")
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _require_finite_float(name: str, value: object, *, allow_zero: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    if allow_zero:
        if number < 0:
            raise ValueError(f"{name} must not be negative")
    elif number <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return number


@dataclass(frozen=True, slots=True)
class SettlementDrainRequest:
    """Bounded budget for one drain attempt.

    ``max_actions`` and ``timeout_seconds`` are both mandatory limits: whichever
    is reached first stops the drain. ``action_interval_seconds`` is the bounded
    observation window after a single action - it must be positive, because a
    zero-length window could never confirm a page transition and would let
    elapsed time masquerade as success. ``stable_frame_count`` is how many
    consecutive identical observations make a page trustworthy.
    """

    max_actions: int
    timeout_seconds: float
    action_interval_seconds: float
    stable_frame_count: int
    #: Upper bound for the *post-action* observation, in seconds. ``None`` means
    #: "same as action_interval_seconds", i.e. the historical behaviour. When it
    #: is larger, the interval becomes the point after which only a *provably
    #: frozen* screen counts as stuck; an animating screen keeps being observed
    #: until this bound. It is not a blanket timeout replacement: a stuck UI is
    #: still reported as soon as the frames stop changing.
    action_observation_seconds: float | None = None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Re-check every field; safe to call more than once."""
        _require_positive_int("max_actions", self.max_actions)
        _require_finite_float("timeout_seconds", self.timeout_seconds, allow_zero=False)
        _require_finite_float(
            "action_interval_seconds", self.action_interval_seconds, allow_zero=False
        )
        _require_positive_int("stable_frame_count", self.stable_frame_count)
        if self.action_observation_seconds is not None:
            observation = _require_finite_float(
                "action_observation_seconds",
                self.action_observation_seconds,
                allow_zero=False,
            )
            if observation < float(self.action_interval_seconds):
                raise ValueError(
                    "action_observation_seconds must not be shorter than "
                    "action_interval_seconds"
                )

    @property
    def observation_budget_seconds(self) -> float:
        """How long one action's result may be observed before giving up."""
        if self.action_observation_seconds is None:
            return float(self.action_interval_seconds)
        return float(self.action_observation_seconds)


@dataclass(frozen=True, slots=True)
class DrainResult:
    """What one drain attempt proved about the visible UI.

    ``reached_home=True`` means a home port was *stably* identified after the
    settlement pages were exhausted. It says nothing about fleet state: the
    fleet ledger still has to be re-read and reconciled afterwards.

    ``completed_pages`` counts settlement pages that were successfully advanced
    past; the home port is never counted. ``actions_performed`` counts the UI
    actions actually dispatched by the driver.
    """

    reached_home: bool
    completed_pages: int
    actions_performed: int
    final_page: DrainPageState
    error_encountered: DrainErrorType | None
    requires_manual_intervention: bool

    def __post_init__(self) -> None:
        if self.completed_pages < 0 or self.actions_performed < 0:
            raise ValueError("completed_pages and actions_performed must not be negative")
        if self.reached_home:
            if self.error_encountered is not None:
                raise ValueError("reached_home cannot carry an error")
            if self.final_page is not DrainPageState.HOME_PORT:
                raise ValueError("reached_home requires final_page=HOME_PORT")
        elif self.error_encountered is None:
            raise ValueError("a drain that did not reach home must report an error")


class DrainPageObserver(Protocol):
    """Read the currently visible page once, without side effects."""

    def observe(self) -> DrainPageState:
        """Return the page recognised *right now*; never click or navigate."""
        ...

    # ``sample()`` is optional. An observer that can also return a fingerprint of
    # the same frame lets the session distinguish "still animating" from "frozen
    # on the same page"; without it the session keeps the historical verdict.


class DrainUiDriver(Protocol):
    """Perform exactly one visible-UI action for the given page.

    ``advance`` must dispatch the single input that the page accepts (for
    example tapping the result panel) and then return immediately - it must not
    wait for the page to change and must not retry. It must never construct,
    send or replay a game API request, and it must never receive a fleet id.
    """

    def advance(self, page: DrainPageState) -> None:
        ...


class DrainClock(Protocol):
    """Monotonic time source plus a sleep, so tests need no real waiting."""

    def monotonic(self) -> float:
        ...

    def sleep(self, seconds: float) -> None:
        ...


class _SystemClock:
    """Default clock: real monotonic time."""

    __slots__ = ()

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@dataclass(frozen=True, slots=True)
class DrainSample:
    """One observation of the settlement UI: the page plus its frame descriptor.

    ``fingerprint`` is the grid descriptor of the *same* frame, so the answer to
    "did the picture change" and the answer to "which page is this" always
    describe one capture. ``None`` means the observer has no frame access.
    """

    page: DrainPageState
    fingerprint: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.page, DrainPageState):
            raise ValueError("page must be a DrainPageState")


@dataclass(frozen=True, slots=True)
class _Wait:
    """Outcome of one observation loop."""

    page: DrainPageState | None
    error: DrainErrorType | None
    last_seen: DrainPageState | None


def _failed(error: DrainErrorType, last_seen: DrainPageState | None) -> _Wait:
    return _Wait(page=None, error=error, last_seen=last_seen)


class SettlementDrainSession:
    """Advance the visible settlement pages until the UI is back at home port.

    One session is bound to one UI observer/driver pair and keeps no state
    between :meth:`drain` calls, so a caller may run a fresh bounded attempt
    after a deliberate ``MAX_ACTIONS_REACHED`` stop.

    ``event_sink`` is optional and purely observational. When supplied it
    receives the drain's own lifecycle (started / one row per action / page
    change / finished) so a run can be rebuilt from the log alone. A sink
    failure is **not** swallowed: the row describing an action is written
    before :meth:`DrainUiDriver.advance`, so an unwritable audit log stops the
    drain instead of clicking unlogged.
    """

    __slots__ = (
        "_observer",
        "_driver",
        "_clock",
        "_frame_poll_interval",
        "_event_sink",
        "_started_at",
        "_settlement_settled",
    )

    def __init__(
        self,
        observer: DrainPageObserver,
        driver: DrainUiDriver,
        *,
        clock: DrainClock | None = None,
        frame_poll_interval_seconds: float = DEFAULT_FRAME_POLL_INTERVAL_SECONDS,
        event_sink: Callable[[str, Mapping[str, object]], None] | None = None,
        # Read-only server-side check: "has the settlement already been
        # collected?" It is consulted *before* every action and while waiting.
        # Once it is true the drain will not repeat the *claim* click: clicking
        # the return notice a second time is exactly the double-claim this guard
        # exists to prevent. Advancing an already-open reward page is still
        # allowed, because that only walks the UI back to home port - refusing
        # it would leave the result page on screen and block the next dispatch.
        settlement_settled: Callable[[], bool] | None = None,
    ) -> None:
        if not callable(getattr(observer, "observe", None)):
            raise ValueError("observer must expose a callable observe()")
        if not callable(getattr(driver, "advance", None)):
            raise ValueError("driver must expose a callable advance(page)")
        if event_sink is not None and not callable(event_sink):
            raise ValueError("event_sink must be callable when given")
        if settlement_settled is not None and not callable(settlement_settled):
            raise ValueError("settlement_settled must be callable when given")
        self._observer = observer
        self._driver = driver
        self._clock: DrainClock = clock if clock is not None else _SystemClock()
        self._frame_poll_interval = _require_finite_float(
            "frame_poll_interval_seconds", frame_poll_interval_seconds, allow_zero=False
        )
        self._event_sink = event_sink
        self._started_at: float | None = None
        self._settlement_settled = settlement_settled

    # -- logging ------------------------------------------------------------

    def _emit(self, event: str, fields: Mapping[str, object]) -> None:
        if self._event_sink is None:
            return
        self._event_sink(event, dict(fields))

    def _sample(self) -> DrainSample:
        """Observe once, asking for a fingerprint when the observer has one."""
        sampler = getattr(self._observer, "sample", None)
        if callable(sampler):
            sampled = sampler()
            if isinstance(sampled, DrainSample):
                return sampled
            page = getattr(sampled, "page", None)
            if isinstance(page, DrainPageState):
                return DrainSample(
                    page=page, fingerprint=getattr(sampled, "fingerprint", None)
                )
        return DrainSample(page=self._observer.observe())

    def _settled(self) -> bool:
        """True when the server already reports the settlement as collected."""
        if self._settlement_settled is None:
            return False
        try:
            return bool(self._settlement_settled())
        except Exception as exc:  # noqa: BLE001 - unknown server state is not "settled"
            self._emit("settlement_drain_settled_check_failed", {
                "error_type": type(exc).__name__,
                "clicks_sent": 0,
                "interpretation": "treated as not settled; no click is authorised by a failed check",
            })
            return False

    # -- public API ---------------------------------------------------------

    def drain(self, request: SettlementDrainRequest) -> DrainResult:
        """Drain the visible settlement UI within ``request``'s limits."""
        if not isinstance(request, SettlementDrainRequest):
            raise ValueError("request must be a SettlementDrainRequest")
        request.validate()

        deadline = self._clock.monotonic() + float(request.timeout_seconds)
        started_at = self._clock.monotonic()
        self._started_at = started_at
        self._emit("settlement_drain_started", {
            "max_actions": request.max_actions,
            "timeout_seconds": request.timeout_seconds,
            "action_interval_seconds": request.action_interval_seconds,
            "stable_frame_count": request.stable_frame_count,
        })

        initial = self._wait_for_stable(request, deadline=deadline)
        if initial.error is not None:
            return self._result(
                completed_pages=0,
                actions_performed=0,
                final_page=initial.last_seen or DrainPageState.UNKNOWN,
                error=initial.error,
            )

        page = initial.page
        assert page is not None  # guaranteed when error is None
        completed_pages = 0
        actions_performed = 0

        # ``_wait_for_change`` never returns the page that was just acted on
        # (it reports UI_STUCK instead), so no page can be clicked twice in a
        # row - the guarantee is structural, not a defensive branch.
        while True:
            terminal_error = _TERMINAL_PAGE_ERRORS.get(page)
            if terminal_error is not None:
                # Never click an error popup or an unrecognised screen.
                return self._result(
                    completed_pages=completed_pages,
                    actions_performed=actions_performed,
                    final_page=page,
                    error=terminal_error,
                )
            if page is DrainPageState.HOME_PORT:
                return self._result(
                    completed_pages=completed_pages,
                    actions_performed=actions_performed,
                    final_page=page,
                    error=None,
                    reached_home=True,
                )
            if actions_performed >= request.max_actions:
                return self._result(
                    completed_pages=completed_pages,
                    actions_performed=actions_performed,
                    final_page=page,
                    error=DrainErrorType.MAX_ACTIONS_REACHED,
                )
            if self._clock.monotonic() >= deadline:
                return self._result(
                    completed_pages=completed_pages,
                    actions_performed=actions_performed,
                    final_page=page,
                    error=DrainErrorType.TIMEOUT,
                )

            if self._settled():
                # The server already shows no pending settlement. Only the claim
                # click can claim twice, so only that one is forbidden; refusing
                # every click here would leave an already-open reward page on
                # screen forever, and a stuck result page blocks the next
                # dispatch far more effectively than a second "next" ever could.
                if page in _CLAIM_PAGES:
                    self._emit("settlement_drain_ui_only_wait", {
                        "page": page.value,
                        "actions_performed": actions_performed,
                        "clicks_sent": 0,
                        "error": DrainErrorType.SETTLED_UI_PENDING.value,
                        "reason": (
                            "the server reports no pending settlement; only the "
                            "UI is still catching up, so the claim click is not "
                            "repeated"
                        ),
                    })
                    return self._result(
                        completed_pages=completed_pages,
                        actions_performed=actions_performed,
                        final_page=page,
                        error=DrainErrorType.SETTLED_UI_PENDING,
                    )
                self._emit("settlement_drain_advance_after_settled", {
                    "page": page.value,
                    "actions_performed": actions_performed,
                    "clicks_sent": 1,
                    "reason": (
                        "the reward page is already open: advancing it only "
                        "walks the UI to home port and cannot claim again"
                    ),
                })

            # The row describing the action is written BEFORE the input, so an
            # unwritable audit log stops the drain here instead of clicking.
            self._emit("settlement_drain_action", {
                "page": page.value,
                "action_index": actions_performed + 1,
                "max_actions": request.max_actions,
                "elapsed_seconds": round(self._clock.monotonic() - started_at, 3),
            })
            self._driver.advance(page)
            actions_performed += 1

            waited = self._wait_for_change(
                request,
                previous=page,
                bound=self._clock.monotonic() + float(request.action_interval_seconds),
                deadline=deadline,
            )
            if waited.error is not None:
                return self._result(
                    completed_pages=completed_pages,
                    actions_performed=actions_performed,
                    final_page=waited.last_seen or page,
                    error=waited.error,
                )

            next_page = waited.page
            assert next_page is not None  # guaranteed when error is None
            self._emit("settlement_drain_page_changed", {
                "from_page": page.value,
                "to_page": next_page.value,
                "elapsed_seconds": round(self._clock.monotonic() - started_at, 3),
            })
            if (
                next_page in _ADVANCEABLE_PAGES
                or next_page is DrainPageState.HOME_PORT
            ):
                # A recognised follow-up page means the settlement page was
                # completed; landing on an error popup does not count.
                completed_pages += 1
            page = next_page

    # -- internals ----------------------------------------------------------

    def _result(
        self,
        *,
        completed_pages: int,
        actions_performed: int,
        final_page: DrainPageState,
        error: DrainErrorType | None,
        reached_home: bool = False,
    ) -> DrainResult:
        result = DrainResult(
            reached_home=reached_home,
            completed_pages=completed_pages,
            actions_performed=actions_performed,
            final_page=final_page,
            error_encountered=error,
            requires_manual_intervention=(
                error is not None and error in _MANUAL_INTERVENTION_ERRORS
            ),
        )
        elapsed = (
            None
            if self._started_at is None
            else round(self._clock.monotonic() - self._started_at, 3)
        )
        self._emit("settlement_drain_finished", {
            "reached_home": result.reached_home,
            "completed_pages": result.completed_pages,
            "actions_performed": result.actions_performed,
            "final_page": result.final_page.value,
            "error": None if error is None else error.value,
            "requires_manual_intervention": result.requires_manual_intervention,
            "elapsed_seconds": elapsed,
        })
        return result

    def _wait_for_stable(
        self, request: SettlementDrainRequest, *, deadline: float
    ) -> _Wait:
        """Sample until one page is seen ``stable_frame_count`` times in a row."""
        last: DrainPageState | None = None
        run = 0
        while True:
            if self._clock.monotonic() >= deadline:
                return _failed(DrainErrorType.TIMEOUT, last)
            page = self._observer.observe()
            if page is last:
                run += 1
            else:
                last = page
                run = 1
            if run >= request.stable_frame_count:
                return _Wait(page=page, error=None, last_seen=page)
            self._clock.sleep(self._frame_poll_interval)

    def _wait_for_change(
        self,
        request: SettlementDrainRequest,
        *,
        previous: DrainPageState,
        bound: float,
        deadline: float,
    ) -> _Wait:
        """Wait for a *different* page, bounding only what can be proven.

        ``bound`` used to mean "nothing happened by now, so the UI is stuck". It
        no longer does. Live 2026-11-07 the drain clicked a return notice, saw the
        same page 2.5 s later and stopped the whole run with ``ui_stuck`` while
        the client was still animating. A fixed window cannot tell "still
        transitioning" from "input was swallowed" from "really stuck"; the frames
        can:

        * frames still changing -> keep observing, up to the post-action budget;
        * frames provably frozen on the acted-on page -> ``UI_STUCK``;
        * server already reports the settlement collected -> observe only, never
          click, and stop with ``SETTLED_UI_PENDING`` when the budget runs out;
        * moving but never naming a page -> ``OBSERVATION_BUDGET_EXHAUSTED``.

        ``UNKNOWN`` is never returned as the "changed" page: the classifier not
        answering is not the screen arriving somewhere. Only an observer without
        any fingerprint falls back to the historical ``unknown_page`` verdict,
        because then there is nothing better to base the answer on.

        With an observer that exposes no fingerprint the historical verdict is
        kept verbatim, because there is nothing better to base it on.
        """
        last = previous
        seen_change = False
        run = 0
        reverted_run = 0
        motion = MotionTracker()
        observation_deadline = min(
            deadline,
            self._clock.monotonic() + request.observation_budget_seconds,
        )
        while True:
            now = self._clock.monotonic()
            if now >= deadline:
                return _failed(DrainErrorType.TIMEOUT, last)

            sample = self._sample()
            movement = motion.observe(sample.fingerprint)
            page = sample.page

            if page is previous:
                last = page
                if not seen_change:
                    if now >= bound:
                        if self._settled():
                            # The account owes nothing more; only the client is
                            # catching up. Waiting is allowed, clicking is not.
                            if now >= observation_deadline:
                                return _failed(
                                    DrainErrorType.SETTLED_UI_PENDING, last
                                )
                            self._emit("settlement_drain_ui_only_wait", {
                                "page": page.value,
                                "still_run": movement.still_run,
                                "frame_changed": movement.changed,
                                "clicks_sent": 0,
                                "reason": "server reports no pending settlement; UI is catching up",
                            })
                            self._clock.sleep(self._frame_poll_interval)
                            continue
                        if movement.changed is None:
                            # No motion signal: this is exactly the historical
                            # situation, and the historical verdict stands.
                            return _failed(DrainErrorType.UI_STUCK, last)
                        if movement.still_run >= request.stable_frame_count:
                            # Provably frozen on the page we just acted on: the
                            # input did not take, and clicking again would repeat
                            # an action on an unchanged page.
                            self._emit("settlement_drain_ui_frozen", {
                                "page": page.value,
                                "still_run": movement.still_run,
                                "clicks_sent": 0,
                            })
                            return _failed(DrainErrorType.UI_STUCK, last)
                        if now >= observation_deadline:
                            return _failed(
                                DrainErrorType.OBSERVATION_BUDGET_EXHAUSTED, last
                            )
                else:
                    # The page bounced back to the one just acted on. If that
                    # persists, clicking again would repeat an action on an
                    # unchanged page, so stop instead.
                    run = 0
                    reverted_run += 1
                    if reverted_run >= request.stable_frame_count:
                        return _failed(DrainErrorType.UI_STUCK, previous)
                self._clock.sleep(self._frame_poll_interval)
                continue

            reverted_run = 0
            seen_change = True
            if page is DrainPageState.UNKNOWN and sample.fingerprint is not None:
                # UNKNOWN is the classifier declining to answer, not a page that
                # the screen moved to. Returning it as one ended a live drain as
                # "unknown_page" five seconds after the click (2026-11-07): the
                # notice had just been dismissed, home port showed for one frame,
                # and the incoming result page was still animating at 0.8265, so
                # two UNKNOWN frames in a row looked "stable". Keep observing -
                # only the budget may end this - and let a real page, or the
                # server guard, decide what happens next.
                last = page
                run = 0
                if now >= observation_deadline:
                    if self._settled():
                        return _failed(DrainErrorType.SETTLED_UI_PENDING, last)
                    return _failed(
                        DrainErrorType.OBSERVATION_BUDGET_EXHAUSTED, last
                    )
                self._clock.sleep(self._frame_poll_interval)
                continue
            if page is last and run > 0:
                run += 1
            else:
                # A new page, or the candidate page is still flickering.
                last = page
                run = 1
            if run >= request.stable_frame_count:
                return _Wait(page=page, error=None, last_seen=page)
            self._clock.sleep(self._frame_poll_interval)
