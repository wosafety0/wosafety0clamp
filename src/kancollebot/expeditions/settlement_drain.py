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
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

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


#: Pages that may be advanced with exactly one action.
_ADVANCEABLE_PAGES: frozenset[DrainPageState] = frozenset(
    {
        DrainPageState.RETURN_NOTICE,
        DrainPageState.EXPEDITION_RESULT,
        DrainPageState.REWARD_PANEL,
    }
)

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
    """

    __slots__ = ("_observer", "_driver", "_clock", "_frame_poll_interval")

    def __init__(
        self,
        observer: DrainPageObserver,
        driver: DrainUiDriver,
        *,
        clock: DrainClock | None = None,
        frame_poll_interval_seconds: float = DEFAULT_FRAME_POLL_INTERVAL_SECONDS,
    ) -> None:
        if not callable(getattr(observer, "observe", None)):
            raise ValueError("observer must expose a callable observe()")
        if not callable(getattr(driver, "advance", None)):
            raise ValueError("driver must expose a callable advance(page)")
        self._observer = observer
        self._driver = driver
        self._clock: DrainClock = clock if clock is not None else _SystemClock()
        self._frame_poll_interval = _require_finite_float(
            "frame_poll_interval_seconds", frame_poll_interval_seconds, allow_zero=False
        )

    # -- public API ---------------------------------------------------------

    def drain(self, request: SettlementDrainRequest) -> DrainResult:
        """Drain the visible settlement UI within ``request``'s limits."""
        if not isinstance(request, SettlementDrainRequest):
            raise ValueError("request must be a SettlementDrainRequest")
        request.validate()

        deadline = self._clock.monotonic() + float(request.timeout_seconds)

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
        return DrainResult(
            reached_home=reached_home,
            completed_pages=completed_pages,
            actions_performed=actions_performed,
            final_page=final_page,
            error_encountered=error,
            requires_manual_intervention=(
                error is not None and error in _MANUAL_INTERVENTION_ERRORS
            ),
        )

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
        """Wait for a *different* page, then require it to be stable.

        The bound only limits "nothing changed at all". Once the page does move,
        stabilising the new page is bounded by the whole-drain timeout instead,
        so a slow animation cannot be mistaken for a stuck UI.
        """
        last = previous
        seen_change = False
        run = 0
        reverted_run = 0
        while True:
            now = self._clock.monotonic()
            if now >= deadline:
                return _failed(DrainErrorType.TIMEOUT, last)
            if not seen_change and now >= bound:
                # Nothing moved at all within the bounded observation window.
                return _failed(DrainErrorType.UI_STUCK, last)
            page = self._observer.observe()
            if page is previous:
                if not seen_change:
                    # Still the page we just acted on: nothing to confirm yet.
                    last = page
                else:
                    # The page bounced back to the one just acted on. If that
                    # persists, clicking again would repeat an action on an
                    # unchanged page, so stop instead.
                    last = page
                    run = 0
                    reverted_run += 1
                    if reverted_run >= request.stable_frame_count:
                        return _failed(DrainErrorType.UI_STUCK, previous)
                self._clock.sleep(self._frame_poll_interval)
                continue
            reverted_run = 0
            seen_change = True
            if page is last and run > 0:
                run += 1
            else:
                # A new page, or the candidate page is still flickering.
                last = page
                run = 1
            if run >= request.stable_frame_count:
                return _Wait(page=page, error=None, last_seen=page)
            self._clock.sleep(self._frame_poll_interval)
