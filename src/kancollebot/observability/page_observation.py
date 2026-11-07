"""Bounded page observation shared by the return refresh and the settlement drain.

Three live failures (2026-11-07) had one cause: the automation acted on a screen
that was still transitioning, then declared the run dead after a short, fixed
window.

* the return refresh clicked 出撃, saw six UNKNOWN frames in ~8 s and failed
  closed while the client needed ~18 s to reach the next page;
* the post-dispatch observation spent 66 s asking the wrong question;
* the settlement drain clicked once and reported ``ui_stuck`` after 2.5 s.

A fixed window cannot separate "the client is still animating" from "the click
was swallowed" from "this screen is genuinely stuck". Two facts can:

* whether the frame in front of the camera is still changing (grid
  fingerprints, the same descriptor the classifier already uses);
* which calibrated page is on screen, and whether that answer was stable.

This module owns exactly those two facts plus their budgets. It never clicks and
never decides that time passing means success: a page only counts once it was
identified ``settle_samples`` times in a row.

Budgets, all explicit and configurable:

* ``total_seconds`` bounds one observation run (default 120 s);
* ``changed_unknown_limit`` bounds how many *changing* unrecognised frames are
  tolerated (default 20). A frame that is provably identical to the previous one
  does not consume that budget - a frozen but unrecognised screen is a
  transition, not a failure - but it stays bounded by ``total_seconds``;
* the sampling interval backs off 1.5 s -> 2 s -> 3 s while the screen is still,
  and returns to 1.5 s as soon as it changes again.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from kancollebot.adapters.poi.visual_fingerprint import fingerprint_similarity

__all__ = [
    "DEFAULT_CHANGED_UNKNOWN_LIMIT",
    "DEFAULT_INITIAL_INTERVAL_SECONDS",
    "DEFAULT_MAX_INTERVAL_SECONDS",
    "DEFAULT_SETTLE_SAMPLES",
    "DEFAULT_STILL_SIMILARITY",
    "DEFAULT_TOTAL_BUDGET_SECONDS",
    "MotionSample",
    "MotionTracker",
    "ObservationOutcome",
    "ObservationVerdict",
    "PageObservationBudget",
    "PageSample",
    "UNKNOWN_STATE",
    "BoundedPageObservation",
]

#: The classifier's fail-closed answer. It is not a page and never authorises an
#: action; it only ever means "keep observing".
UNKNOWN_STATE = "UNKNOWN"

DEFAULT_TOTAL_BUDGET_SECONDS = 120.0
DEFAULT_CHANGED_UNKNOWN_LIMIT = 20
DEFAULT_SETTLE_SAMPLES = 2
#: Two grid fingerprints at or above this similarity are the same picture. Live
#: animation phases of the same page score well below it; identical frames score
#: 1.0.
DEFAULT_STILL_SIMILARITY = 0.995
DEFAULT_INITIAL_INTERVAL_SECONDS = 1.5
DEFAULT_MAX_INTERVAL_SECONDS = 3.0


def _require_finite(name: str, value: object, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and number < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return number


@dataclass(frozen=True, slots=True)
class PageSample:
    """One observed frame: the classified page plus its motion descriptor.

    ``state`` is a calibrated page name or ``"UNKNOWN"``. ``fingerprint`` is the
    grid descriptor of the *same* frame, so motion and identity always describe
    one picture instead of two captures taken at different instants.
    """

    state: str
    scores: Mapping[str, float] | None = None
    fingerprint: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, str) or not self.state:
            raise ValueError("state must be a non-empty string")


@dataclass(frozen=True, slots=True)
class MotionSample:
    """Whether the current frame differs from the previous one."""

    #: ``None`` when no fingerprint was available, so motion is unknown.
    changed: bool | None
    similarity: float | None
    #: How many consecutive frames (including this one) were provably identical.
    still_run: int
    interval_seconds: float


class MotionTracker:
    """Decide "did the picture change" and how long to wait before looking again.

    Kept separate from the verdict logic because the settlement drain needs the
    same motion judgement with a different control flow.
    """

    __slots__ = (
        "_still_similarity",
        "_initial_interval",
        "_max_interval",
        "_previous",
        "_still_run",
    )

    def __init__(
        self,
        *,
        still_similarity: float = DEFAULT_STILL_SIMILARITY,
        initial_interval_seconds: float = DEFAULT_INITIAL_INTERVAL_SECONDS,
        max_interval_seconds: float = DEFAULT_MAX_INTERVAL_SECONDS,
    ) -> None:
        similarity = _require_finite("still_similarity", still_similarity)
        if not 0 < similarity <= 1:
            raise ValueError("still_similarity must be in (0, 1]")
        initial = _require_finite(
            "initial_interval_seconds", initial_interval_seconds, minimum=0.0
        )
        maximum = _require_finite(
            "max_interval_seconds", max_interval_seconds, minimum=0.0
        )
        if maximum and initial > maximum:
            raise ValueError("initial_interval_seconds must not exceed the maximum")
        self._still_similarity = similarity
        self._initial_interval = initial
        self._max_interval = maximum
        self._previous: tuple[int, ...] | None = None
        self._still_run = 0

    @property
    def still_similarity(self) -> float:
        return self._still_similarity

    def observe(self, fingerprint: Sequence[int] | None) -> MotionSample:
        similarity: float | None = None
        changed: bool | None = None
        if fingerprint is None or self._previous is None:
            # No comparable pair: motion is unknown, never "changed" and never
            # "still". Callers treat unknown motion conservatively.
            self._still_run = 0
        else:
            similarity = fingerprint_similarity(self._previous, fingerprint)
            changed = similarity < self._still_similarity
            self._still_run = 0 if changed else self._still_run + 1
        if fingerprint is not None:
            self._previous = tuple(fingerprint)
        if changed is not False:
            interval = self._initial_interval
        else:
            # 1.5 s, then 2 s, then the 3 s ceiling: a frozen screen costs fewer
            # captures, a moving one is sampled closely again immediately.
            if self._still_run <= 0:
                interval = self._initial_interval
            elif self._still_run == 1:
                interval = min(self._max_interval, max(self._initial_interval, 2.0))
            else:
                interval = self._max_interval
        return MotionSample(
            changed=changed,
            similarity=similarity,
            still_run=self._still_run,
            interval_seconds=interval,
        )

    def reset(self) -> None:
        self._previous = None
        self._still_run = 0


class ObservationVerdict(Enum):
    """What one bounded observation run proved."""

    TARGET_REACHED = "target_reached"
    SAFE_START_CONFIRMED = "safe_start_confirmed"
    OTHER_PAGE = "other_page"
    CHANGED_UNKNOWN_EXHAUSTED = "changed_unknown_exhausted"
    TIME_EXHAUSTED = "time_exhausted"


@dataclass(frozen=True, slots=True)
class ObservationOutcome:
    """Result of one bounded observation run, with the evidence behind it."""

    verdict: ObservationVerdict
    state: str | None
    stable_state: str | None
    samples: int
    changed_samples: int
    still_unknown_samples: int
    changed_unknown_samples: int
    elapsed_seconds: float
    reason: str

    @property
    def reached_target(self) -> bool:
        return self.verdict is ObservationVerdict.TARGET_REACHED

    @property
    def at_safe_start(self) -> bool:
        return self.verdict is ObservationVerdict.SAFE_START_CONFIRMED

    def as_log_fields(self) -> dict[str, object]:
        return {
            "verdict": self.verdict.value,
            "state": self.state,
            "stable_state": self.stable_state,
            "samples": self.samples,
            "changed_samples": self.changed_samples,
            "still_unknown_samples": self.still_unknown_samples,
            "changed_unknown_samples": self.changed_unknown_samples,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class PageObservationBudget:
    """Configuration for one bounded observation run."""

    total_seconds: float = DEFAULT_TOTAL_BUDGET_SECONDS
    changed_unknown_limit: int = DEFAULT_CHANGED_UNKNOWN_LIMIT
    settle_samples: int = DEFAULT_SETTLE_SAMPLES
    still_similarity: float = DEFAULT_STILL_SIMILARITY
    initial_interval_seconds: float = DEFAULT_INITIAL_INTERVAL_SECONDS
    max_interval_seconds: float = DEFAULT_MAX_INTERVAL_SECONDS

    def __post_init__(self) -> None:
        _require_finite("total_seconds", self.total_seconds, minimum=0.0)
        if self.total_seconds <= 0:
            raise ValueError("total_seconds must be greater than zero")
        for name in ("changed_unknown_limit", "settle_samples"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive int")
        _require_finite("still_similarity", self.still_similarity)
        _require_finite(
            "initial_interval_seconds", self.initial_interval_seconds, minimum=0.0
        )
        _require_finite(
            "max_interval_seconds", self.max_interval_seconds, minimum=0.0
        )

    def as_log_fields(self) -> dict[str, object]:
        return {
            "total_seconds": self.total_seconds,
            "changed_unknown_limit": self.changed_unknown_limit,
            "settle_samples": self.settle_samples,
            "still_similarity": self.still_similarity,
            "initial_interval_seconds": self.initial_interval_seconds,
            "max_interval_seconds": self.max_interval_seconds,
        }


class BoundedPageObservation:
    """Observe until a page is proven, the budget runs out, or the screen moves on.

    The caller supplies ``sample()``, which must capture ONE frame and return
    both its classified page and its fingerprint. This class never captures,
    never clicks and never navigates.
    """

    __slots__ = ("_budget", "_monotonic", "_sleep", "_event_sink")

    def __init__(
        self,
        *,
        budget: PageObservationBudget | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        event_sink: Callable[[str, Mapping[str, object]], None] | None = None,
    ) -> None:
        if not callable(monotonic) or not callable(sleep):
            raise ValueError("monotonic and sleep must be callable")
        if event_sink is not None and not callable(event_sink):
            raise ValueError("event_sink must be callable when given")
        self._budget = budget if budget is not None else PageObservationBudget()
        if not isinstance(self._budget, PageObservationBudget):
            raise ValueError("budget must be a PageObservationBudget")
        self._monotonic = monotonic
        self._sleep = sleep
        self._event_sink = event_sink

    @property
    def budget(self) -> PageObservationBudget:
        return self._budget

    def _emit(self, event: str, fields: Mapping[str, object]) -> None:
        if self._event_sink is not None:
            self._event_sink(event, dict(fields))

    def run(
        self,
        sample: Callable[[], PageSample],
        *,
        target: str,
        safe_start: str | None = None,
        known_pages: Sequence[str] = (),
        label: str = "page-observation",
        event_prefix: str = "page_observation",
    ) -> ObservationOutcome:
        """Observe until ``target`` is stable, or until a budget is exhausted.

        ``safe_start`` is the page the action was sent from. A *stable*
        confirmation of it is the only evidence that the action provably did
        nothing, and it is returned as ``SAFE_START_CONFIRMED`` so the caller -
        not this class - decides whether one repeat is allowed.

        ``known_pages`` are the other calibrated pages. A stable identification
        of one of them is ``OTHER_PAGE``: the screen moved somewhere real, so
        waiting longer or clicking again would be blind.
        """
        if not callable(sample):
            raise ValueError("sample must be callable")
        if not isinstance(target, str) or not target:
            raise ValueError("target must be a non-empty page name")
        if safe_start is not None and not isinstance(safe_start, str):
            raise ValueError("safe_start must be a page name or None")
        known = tuple(str(page) for page in known_pages)

        budget = self._budget
        span = budget.total_seconds
        tracker = MotionTracker(
            still_similarity=budget.still_similarity,
            initial_interval_seconds=budget.initial_interval_seconds,
            max_interval_seconds=budget.max_interval_seconds,
        )
        started = self._monotonic()
        deadline = started + span

        samples = 0
        changed_samples = 0
        still_unknown = 0
        changed_unknown = 0
        run_state: str | None = None
        run_length = 0
        last_state: str | None = None

        self._emit(f"{event_prefix}_started", {
            "label": label,
            "target": target,
            "safe_start": safe_start,
            "known_pages": list(known),
            "total_seconds": span,
            **budget.as_log_fields(),
        })

        while True:
            now = self._monotonic()
            if now >= deadline:
                return self._finish(
                    ObservationVerdict.TIME_EXHAUSTED,
                    state=last_state,
                    stable_state=None,
                    samples=samples,
                    changed_samples=changed_samples,
                    still_unknown=still_unknown,
                    changed_unknown=changed_unknown,
                    started=started,
                    reason=(
                        f"no stable {target} after {round(now - started, 3)}s "
                        f"({samples} samples); observation budget exhausted"
                    ),
                    label=label,
                    event_prefix=event_prefix,
                )

            observed = sample()
            samples += 1
            state = observed.state
            last_state = state
            motion = tracker.observe(observed.fingerprint)
            if motion.changed:
                changed_samples += 1

            if state == run_state:
                run_length += 1
            else:
                run_state = state
                run_length = 1
            stable = run_length >= budget.settle_samples

            if state == UNKNOWN_STATE:
                if motion.changed is False:
                    # Provably frozen and unrecognised: a transition, not a
                    # failure. It must not consume the changing-frame budget,
                    # and it must never be mistaken for a safe start.
                    still_unknown += 1
                else:
                    changed_unknown += 1

            self._emit(f"{event_prefix}_sample", {
                "label": label,
                "sample": samples,
                "state": state,
                "stable": stable,
                "run_length": run_length,
                "motion_changed": motion.changed,
                "frame_similarity": (
                    None if motion.similarity is None else round(motion.similarity, 4)
                ),
                "still_run": motion.still_run,
                "interval_seconds": motion.interval_seconds,
                "changed_samples": changed_samples,
                "still_unknown_samples": still_unknown,
                "changed_unknown_samples": changed_unknown,
                "changed_unknown_remaining": max(
                    0, budget.changed_unknown_limit - changed_unknown
                ),
                "seconds_remaining": round(max(0.0, deadline - now), 3),
                "top_score": (
                    None
                    if not observed.scores
                    else round(max(observed.scores.values()), 4)
                ),
            })

            if state == target and stable:
                return self._finish(
                    ObservationVerdict.TARGET_REACHED,
                    state=state,
                    stable_state=state,
                    samples=samples,
                    changed_samples=changed_samples,
                    still_unknown=still_unknown,
                    changed_unknown=changed_unknown,
                    started=started,
                    reason=f"{target} confirmed on {run_length} consecutive frames",
                    label=label,
                    event_prefix=event_prefix,
                )

            if (
                state != UNKNOWN_STATE
                and stable
                and safe_start is not None
                and state == safe_start
            ):
                return self._finish(
                    ObservationVerdict.SAFE_START_CONFIRMED,
                    state=state,
                    stable_state=state,
                    samples=samples,
                    changed_samples=changed_samples,
                    still_unknown=still_unknown,
                    changed_unknown=changed_unknown,
                    started=started,
                    reason=(
                        f"{safe_start} still stably displayed after the action: "
                        "the input provably had no effect"
                    ),
                    label=label,
                    event_prefix=event_prefix,
                )

            if (
                state != UNKNOWN_STATE
                and stable
                and state != target
                and state != safe_start
                and state in known
            ):
                return self._finish(
                    ObservationVerdict.OTHER_PAGE,
                    state=state,
                    stable_state=state,
                    samples=samples,
                    changed_samples=changed_samples,
                    still_unknown=still_unknown,
                    changed_unknown=changed_unknown,
                    started=started,
                    reason=(
                        f"a different calibrated page ({state}) was stable on "
                        f"{run_length} consecutive frames"
                    ),
                    label=label,
                    event_prefix=event_prefix,
                )

            if changed_unknown >= budget.changed_unknown_limit:
                return self._finish(
                    ObservationVerdict.CHANGED_UNKNOWN_EXHAUSTED,
                    state=last_state,
                    stable_state=None,
                    samples=samples,
                    changed_samples=changed_samples,
                    still_unknown=still_unknown,
                    changed_unknown=changed_unknown,
                    started=started,
                    reason=(
                        f"the screen kept changing but was never recognised: "
                        f"{changed_unknown} changing UNKNOWN frames "
                        f"(limit {budget.changed_unknown_limit})"
                    ),
                    label=label,
                    event_prefix=event_prefix,
                )

            self._sleep(motion.interval_seconds)

    def _finish(
        self,
        verdict: ObservationVerdict,
        *,
        state: str | None,
        stable_state: str | None,
        samples: int,
        changed_samples: int,
        still_unknown: int,
        changed_unknown: int,
        started: float,
        reason: str,
        label: str,
        event_prefix: str,
    ) -> ObservationOutcome:
        outcome = ObservationOutcome(
            verdict=verdict,
            state=state,
            stable_state=stable_state,
            samples=samples,
            changed_samples=changed_samples,
            still_unknown_samples=still_unknown,
            changed_unknown_samples=changed_unknown,
            elapsed_seconds=max(0.0, self._monotonic() - started),
            reason=reason,
        )
        self._emit(f"{event_prefix}_finished", {"label": label, **outcome.as_log_fields()})
        return outcome
