"""Return-detection with observation rounds ("generation tokens").

Layering note: this module lives in ``core`` because both the Poi adapter
(which owns the navigation) and the expedition layer (which owns the fleet
lifecycle) need it, and adapters may not import a task implementation. It is
task-neutral in substance - it reasons about observation freshness and
observation rounds, not about expeditions - but return detection is the only
thing that uses it today.

The problem this solves is specific and was measured on the real client: Poi's
``mission_status`` only advances when the client sends ``api_port/port``, and a
fleet that is out at sea never makes the client send one. So the return timer
expiring changes nothing in the data - a read-only poll can sit at
``status == 1`` forever. The only lawful way to learn that a fleet is home is a
*visible UI navigation* that makes the client refresh its own port data.

That navigation costs clicks, which makes two things dangerous:

* treating "the timer expired" as "the fleet is home" - it is not; it is only
  the earliest moment a refresh can be worth sending;
* letting a late callback from an older observation round send the clicks
  while a newer round is already in flight.

This module therefore separates five states that used to blur into one, and
stamps every round with a monotonically increasing ``generation``:

===============  =====================================================
state            meaning
===============  =====================================================
expected_return  the timer value only; proves nothing
state_before     what the last observation said, before any refresh
refresh_action   the one navigation that was actually sent, if any
state_after      what a *newer* observation said after that action
confirmed_return the only state that may be acted on downstream
===============  =====================================================

A refresh is refused unless its generation is still the newest one at the
moment of the click, and a post-refresh observation is only accepted when it
is strictly newer than the navigation. Everything else - a stale round, a
cooldown, a network failure, an unrecognised page - ends the round without
claiming a return.

The module is transport-free: it takes an ``observe`` callable and a
``refresh`` callable, so it is fully testable offline and cannot itself create
a game request.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

__all__ = [
    "ReturnObservation",
    "ReturnRefreshCoordinator",
    "ReturnRefreshOutcome",
    "ReturnRefreshRefused",
    "ReturnWatch",
]

#: Classified states of one observed fleet, from the return-detection point of
#: view. They are deliberately coarse: anything else is ``unknown``.
STATE_AT_SEA = "at_sea"
STATE_RETURNED_PENDING = "returned_pending"
STATE_IDLE = "idle"
STATE_UNKNOWN = "unknown"


class ReturnRefreshRefused(RuntimeError):
    """A refresh could not be attempted safely; nothing was clicked."""


class ReturnObservation(Protocol):
    """The parts of one Poi observation this module is allowed to look at."""

    def __getattr__(self, name: str) -> Any:  # pragma: no cover - structural
        ...


@dataclass(frozen=True, slots=True)
class ReturnWatch:
    """One observation round.

    ``generation`` is what makes a late callback harmless: an action is only
    ever sent on behalf of the newest round.
    """

    generation: int
    fleet_id: int
    expected_return_at: datetime | None
    started_at: datetime


@dataclass(frozen=True, slots=True)
class ReturnRefreshOutcome:
    """What one round proved, stated in the order the facts happened."""

    fleet_id: int
    generation: int
    expected_return_at: datetime | None
    state_before: str
    refresh_requested: bool
    refresh_performed: bool
    state_after: str
    confirmed_return: bool
    reason: str
    stale_generation: bool = False
    observation_after_refresh_verified: bool = False
    refreshed_at: datetime | None = None
    return_time_reached: bool | None = None

    def as_event_fields(self) -> dict[str, object]:
        return {
            "fleet_id": self.fleet_id,
            "generation": self.generation,
            "expected_return_at": (
                self.expected_return_at.isoformat() if self.expected_return_at else None
            ),
            "return_time_reached": self.return_time_reached,
            "state_before_refresh": self.state_before,
            "refresh_requested": self.refresh_requested,
            "refresh_performed": self.refresh_performed,
            "refreshed_at": self.refreshed_at.isoformat() if self.refreshed_at else None,
            "state_after_refresh": self.state_after,
            "observation_after_refresh_verified": self.observation_after_refresh_verified,
            "confirmed_return": self.confirmed_return,
            "return_confirmation_reason": self.reason,
            "stale_generation": self.stale_generation,
        }


def _classify(observation: Any) -> str:
    """Coarse state of one observed fleet; never guesses a return."""
    status = getattr(observation, "mission_status", None)
    if status == 1:
        return STATE_AT_SEA
    if status == 2:
        return STATE_RETURNED_PENDING
    if status == 0:
        mission_id = getattr(observation, "mission_id", None)
        return STATE_IDLE if mission_id is None else STATE_UNKNOWN
    return STATE_UNKNOWN


def _trusted(observation: Any) -> bool:
    return bool(getattr(observation, "source_freshness_verified", False))


def _observed_at(observation: Any) -> datetime | None:
    value = getattr(observation, "observed_at", None)
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value
    return None


class ReturnRefreshCoordinator:
    """Own the observation rounds and decide when a refresh may be sent.

    ``clock`` supplies wall-clock time (for timestamps and the return timer),
    ``monotonic``/``sleep`` supply the cooldown. Neither is used to claim a
    return: only a *newer* observation can do that.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        event_sink: Callable[[str, Mapping[str, object]], None] | None = None,
        grace_seconds: float = 5.0,
        min_refresh_interval_seconds: float = 30.0,
    ) -> None:
        for label, value in (
            ("grace_seconds", grace_seconds),
            ("min_refresh_interval_seconds", min_refresh_interval_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{label} must be a number")
            if not math.isfinite(float(value)) or float(value) < 0:
                raise ValueError(f"{label} must be finite and non-negative")
        if not callable(monotonic) or not callable(sleep):
            raise ValueError("monotonic and sleep must be callable")
        if event_sink is not None and not callable(event_sink):
            raise ValueError("event_sink must be callable when given")
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic
        self._sleep = sleep
        self._event_sink = event_sink
        self._grace = timedelta(seconds=float(grace_seconds))
        self._min_refresh_interval = float(min_refresh_interval_seconds)
        self._generation = 0
        self._last_refresh_at: float | None = None
        self._invalidate_reason: str | None = None

    # -- rounds -------------------------------------------------------------

    @property
    def generation(self) -> int:
        return self._generation

    def begin_watch(
        self, *, fleet_id: int, expected_return_at: datetime | None = None
    ) -> ReturnWatch:
        """Start a new round; every older round becomes stale immediately."""
        if isinstance(fleet_id, bool) or not isinstance(fleet_id, int) or fleet_id < 1:
            raise ValueError("fleet_id must be a positive integer")
        if expected_return_at is not None and (
            not isinstance(expected_return_at, datetime)
            or expected_return_at.tzinfo is None
            or expected_return_at.utcoffset() is None
        ):
            raise ValueError("expected_return_at must be a timezone-aware datetime")
        self._generation += 1
        self._invalidate_reason = None
        return ReturnWatch(
            generation=self._generation,
            fleet_id=fleet_id,
            expected_return_at=expected_return_at,
            started_at=self._clock(),
        )

    def invalidate(self, reason: str) -> int:
        """Make every in-flight round stale (timeout, cancel, shutdown).

        Called before releasing listeners or stopping a process: a callback
        that fires afterwards is guaranteed to see ``is_current() is False``
        and must not click.
        """
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be a non-empty string")
        self._generation += 1
        self._invalidate_reason = reason.strip()
        self._emit("return_refresh_invalidated", {
            "generation": self._generation,
            "reason": self._invalidate_reason,
            "clicks_sent": 0,
        })
        return self._generation

    def is_current(self, watch: ReturnWatch) -> bool:
        return isinstance(watch, ReturnWatch) and watch.generation == self._generation

    def require_current(self, watch: ReturnWatch) -> None:
        """Raise before any UI action that a stale round would make unsafe."""
        if not self.is_current(watch):
            raise ReturnRefreshRefused(
                f"observation round {getattr(watch, 'generation', None)} is stale; "
                f"the current round is {self._generation}"
            )

    # -- the one action -----------------------------------------------------

    def refresh_and_confirm(
        self,
        *,
        watch: ReturnWatch,
        observe: Callable[[int], Any],
        refresh: Callable[[], None],
    ) -> ReturnRefreshOutcome:
        """Decide whether to refresh, then prove (or refuse) the return.

        ``observe(fleet_id)`` must be read-only. ``refresh()`` must perform the
        single visible navigation and return; this module never retries it.
        """
        if not isinstance(watch, ReturnWatch):
            raise ValueError("watch must be a ReturnWatch")
        if not callable(observe) or not callable(refresh):
            raise ValueError("observe and refresh must be callable")
        if not self.is_current(watch):
            return self._outcome(
                watch,
                state_before=STATE_UNKNOWN,
                refresh_requested=False,
                refresh_performed=False,
                state_after=STATE_UNKNOWN,
                confirmed=False,
                reason=f"stale_generation:{self._invalidate_reason or 'newer round started'}",
                stale=True,
            )

        before = observe(watch.fleet_id)
        state_before = _classify(before)
        self._emit("return_refresh_observed", {
            "fleet_id": watch.fleet_id,
            "generation": watch.generation,
            "phase": "before_refresh",
            "state": state_before,
            "source_freshness_verified": _trusted(before),
            "observed_at": (
                _observed_at(before).isoformat() if _observed_at(before) else None
            ),
        })

        # A *fresh* status-2 reading already is the proof; no click is needed.
        if state_before == STATE_RETURNED_PENDING and _trusted(before):
            return self._outcome(
                watch,
                state_before=state_before,
                refresh_requested=False,
                refresh_performed=False,
                state_after=state_before,
                confirmed=True,
                reason="returned_observed_without_refresh",
                observation_verified=True,
            )

        now = self._clock()
        if watch.expected_return_at is not None:
            if now < watch.expected_return_at + self._grace:
                # The timer is the earliest moment a refresh can be worth
                # sending - never a claim that the fleet is home.
                self._emit("return_refresh_time_not_reached", {
                    "fleet_id": watch.fleet_id,
                    "generation": watch.generation,
                    "expected_return_at": watch.expected_return_at.isoformat(),
                    "seconds_remaining": round(
                        (watch.expected_return_at + self._grace - now).total_seconds(), 3
                    ),
                    "clicks_sent": 0,
                })
                return self._outcome(
                    watch,
                    state_before=state_before,
                    refresh_requested=False,
                    refresh_performed=False,
                    state_after=state_before,
                    confirmed=False,
                    reason="return_time_not_reached",
                )

        if not self._cooldown_elapsed():
            return self._outcome(
                watch,
                state_before=state_before,
                refresh_requested=False,
                refresh_performed=False,
                state_after=state_before,
                confirmed=False,
                reason="refresh_cooldown",
            )

        # Re-check right before the click: the time gate and the event sink
        # above both yield, and a newer round may have started meanwhile.
        if not self.is_current(watch):
            return self._outcome(
                watch,
                state_before=state_before,
                refresh_requested=True,
                refresh_performed=False,
                state_after=state_before,
                confirmed=False,
                reason="stale_generation_before_refresh",
                stale=True,
            )

        self._emit("return_refresh_action", {
            "fleet_id": watch.fleet_id,
            "generation": watch.generation,
            "state_before_refresh": state_before,
            "refresh_origin": "return_detection",
        })
        action_started_at = self._clock()
        # Set the cooldown before touching the UI: an ambiguous click must not
        # be replayed by the next round either.
        self._last_refresh_at = self._monotonic()
        refresh()

        after = observe(watch.fleet_id)
        state_after = _classify(after)
        after_at = _observed_at(after)
        verified_after = (
            _trusted(after) and after_at is not None and after_at > action_started_at
        )
        self._emit("return_refresh_observed", {
            "fleet_id": watch.fleet_id,
            "generation": watch.generation,
            "phase": "after_refresh",
            "state": state_after,
            "source_freshness_verified": _trusted(after),
            "observation_crossed_action_boundary": verified_after,
            "observed_at": after_at.isoformat() if after_at else None,
        })

        if not self.is_current(watch):
            # A newer round took over while the navigation was in flight.
            return self._outcome(
                watch,
                state_before=state_before,
                refresh_requested=True,
                refresh_performed=True,
                state_after=state_after,
                confirmed=False,
                reason="stale_generation_after_refresh",
                stale=True,
            )

        if not verified_after:
            return self._outcome(
                watch,
                state_before=state_before,
                refresh_requested=True,
                refresh_performed=True,
                state_after=state_after,
                confirmed=False,
                reason="observation_after_refresh_not_fresh",
                refreshed_at=action_started_at,
            )
        if state_after != STATE_RETURNED_PENDING:
            return self._outcome(
                watch,
                state_before=state_before,
                refresh_requested=True,
                refresh_performed=True,
                state_after=state_after,
                confirmed=False,
                reason="refresh_did_not_confirm_return",
                observation_verified=True,
                refreshed_at=action_started_at,
            )
        return self._outcome(
            watch,
            state_before=state_before,
            refresh_requested=True,
            refresh_performed=True,
            state_after=state_after,
            confirmed=True,
            reason="returned_confirmed_by_fresh_observation",
            observation_verified=True,
            refreshed_at=action_started_at,
        )

    # -- internals ----------------------------------------------------------

    def _cooldown_elapsed(self) -> bool:
        now = self._monotonic()
        if self._last_refresh_at is None:
            return True
        return (now - self._last_refresh_at) >= self._min_refresh_interval

    def _emit(self, event: str, fields: Mapping[str, object]) -> None:
        if self._event_sink is None:
            return
        try:
            self._event_sink(event, dict(fields))
        except Exception:
            # Diagnostics must not change return-detection policy.
            pass

    def _outcome(
        self,
        watch: ReturnWatch,
        *,
        state_before: str,
        refresh_requested: bool,
        refresh_performed: bool,
        state_after: str,
        confirmed: bool,
        reason: str,
        stale: bool = False,
        observation_verified: bool = False,
        refreshed_at: datetime | None = None,
    ) -> ReturnRefreshOutcome:
        outcome = ReturnRefreshOutcome(
            fleet_id=watch.fleet_id,
            generation=watch.generation,
            expected_return_at=watch.expected_return_at,
            state_before=state_before,
            refresh_requested=refresh_requested,
            refresh_performed=refresh_performed,
            state_after=state_after,
            confirmed_return=confirmed,
            reason=reason,
            stale_generation=stale,
            observation_after_refresh_verified=observation_verified,
            refreshed_at=refreshed_at,
            return_time_reached=(
                None
                if watch.expected_return_at is None
                else self._clock() >= watch.expected_return_at
            ),
        )
        self._emit("return_refresh_completed", outcome.as_event_fields())
        return outcome
