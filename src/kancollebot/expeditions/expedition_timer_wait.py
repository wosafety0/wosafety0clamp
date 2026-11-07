"""Blocking expedition-timer sleep for the production expedition loop.

A fleet whose expedition timer has not expired has *nothing* an observation can
advance.  Polling Poi, CDP, screenshots or image classification during that time
is pure cost and, worse, can send a navigation while the game is still away.
The old ``--wait-for-return`` loop slept ``tick_interval_seconds`` between full
supervisor ticks, which is a busy-loop by another name: every tick re-read the
fleet, re-classified screenshots and re-checked the state gate.

This module replaces that loop with one blocking sleep straight to the earliest
unexpired ``final_time_ms`` plus a fixed post-expiry grace.  While it sleeps no
code in this process runs, so no probe can fire; the process-wide probe counters
are snapshotted before and after and a non-zero delta is a hard error rather
than a log line.

A timer expiring is *never* return evidence.  The only thing it permits is the
start of the visible ``出击 -> 母港`` refresh, which the supervisor still owns.
"""
from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from kancollebot.observability.probe_counter import (
    PROBE_COUNTER,
    ProbeCounter,
    total as _total_delta,
)

DEFAULT_POST_EXPIRY_GRACE_SECONDS = 3.0


class TimerSleepError(RuntimeError):
    """The timer sleep cannot be started or completed safely."""


class ProbeDuringSleepError(TimerSleepError):
    """An external observation probe fired while the timer sleep was in force."""


@dataclass(frozen=True, slots=True)
class FleetTimer:
    """One fleet's Poi-authored expedition completion time."""

    fleet_id: int
    deadline_epoch_ms: int
    source: str = "poi_final_time_ms"

    def __post_init__(self) -> None:
        if isinstance(self.fleet_id, bool) or not isinstance(self.fleet_id, int) or self.fleet_id < 1:
            raise ValueError("fleet_id must be a positive integer")
        if (
            isinstance(self.deadline_epoch_ms, bool)
            or not isinstance(self.deadline_epoch_ms, int)
            or self.deadline_epoch_ms < 1
        ):
            raise ValueError("deadline_epoch_ms must be a positive integer")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("timer source must be named")


@dataclass(frozen=True, slots=True)
class TimerSleepPlan:
    """Why the loop is (or is not) about to sleep, and for how long."""

    should_sleep: bool
    fleet_id: int | None
    deadline_epoch_ms: int | None
    sleep_seconds: float
    reason: str
    grace_seconds: float
    timers: tuple[FleetTimer, ...]
    pending_fleet_ids: tuple[int, ...]
    max_sleep_seconds: float | None = None

    @property
    def deadline_iso(self) -> str | None:
        if self.deadline_epoch_ms is None:
            return None
        return datetime.fromtimestamp(
            self.deadline_epoch_ms / 1000, tz=timezone.utc
        ).isoformat()


@dataclass(frozen=True, slots=True)
class TimerSleepResult:
    """Everything the run log needs to reconstruct one sleep."""

    slept: bool
    fleet_id: int | None
    deadline_epoch_ms: int | None
    deadline_iso: str | None
    requested_sleep_seconds: float
    actual_sleep_seconds: float
    start_reason: str
    end_reason: str
    probe_calls_during_sleep: Mapping[str, int]
    grace_seconds: float
    timer_is_return_evidence: bool = False
    returned: bool = False
    dispatches_sent: int = 0


def plan_timer_sleep(
    *,
    timers: Mapping[int, int] | Iterable[FleetTimer],
    now_epoch_ms: int,
    grace_seconds: float = DEFAULT_POST_EXPIRY_GRACE_SECONDS,
    pending_fleet_ids: Iterable[int] = (),
    enabled: bool = True,
    max_sleep_seconds: float | None = None,
) -> TimerSleepPlan:
    """Decide whether to sleep, and to *which* fleet's timer.

    ``timers`` are Poi ``api_mission`` completion times (epoch milliseconds).
    The earliest unexpired one wins; fleets with pending settlement/resupply
    work are never slept past, because that work is executable right now.
    """
    if isinstance(now_epoch_ms, bool) or not isinstance(now_epoch_ms, int):
        raise ValueError("now_epoch_ms must be an integer")
    if (
        isinstance(grace_seconds, bool)
        or not isinstance(grace_seconds, (int, float))
        or not math.isfinite(grace_seconds)
        or grace_seconds < 0
    ):
        raise ValueError("grace_seconds must be a finite non-negative number")
    if max_sleep_seconds is not None and (
        isinstance(max_sleep_seconds, bool)
        or not isinstance(max_sleep_seconds, (int, float))
        or not math.isfinite(max_sleep_seconds)
        or max_sleep_seconds <= 0
    ):
        raise ValueError("max_sleep_seconds must be a positive finite number or None")

    if isinstance(timers, Mapping):
        parsed = tuple(
            FleetTimer(fleet_id=int(fleet_id), deadline_epoch_ms=int(deadline))
            for fleet_id, deadline in sorted(timers.items())
        )
    else:
        parsed = tuple(timers)
        if any(not isinstance(item, FleetTimer) for item in parsed):
            raise ValueError("timers must be FleetTimer values or a fleet->deadline mapping")
    for item in parsed:
        if item.deadline_epoch_ms > 2**53:
            raise ValueError("timer deadline is outside the safe integer range")
    pending = tuple(int(item) for item in pending_fleet_ids)

    if not enabled:
        return TimerSleepPlan(False, None, None, 0.0, "timer_wait_disabled", grace_seconds, parsed, pending, max_sleep_seconds)
    if pending:
        return TimerSleepPlan(False, None, None, 0.0, "pending_work", grace_seconds, parsed, pending, max_sleep_seconds)
    future = tuple(item for item in parsed if item.deadline_epoch_ms > now_epoch_ms)
    if not future:
        return TimerSleepPlan(
            False, None, None, 0.0, "timer_expired_refresh_may_start",
            grace_seconds, parsed, pending, max_sleep_seconds,
        )
    target = min(future, key=lambda item: item.deadline_epoch_ms)
    seconds = (target.deadline_epoch_ms - now_epoch_ms) / 1000.0 + grace_seconds
    reason = "timer_pending"
    if max_sleep_seconds is not None and seconds > max_sleep_seconds:
        seconds = float(max_sleep_seconds)
        reason = "timer_pending_bounded_by_max_sleep"
    return TimerSleepPlan(
        True, target.fleet_id, target.deadline_epoch_ms, seconds, reason,
        grace_seconds, parsed, pending, max_sleep_seconds,
    )


class ExpeditionTimerSleeper:
    """One blocking sleep to a fleet timer, with a hard no-probes invariant."""

    def __init__(
        self,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        cancel_event: threading.Event | None = None,
        probe_counter: ProbeCounter = PROBE_COUNTER,
        event_sink: Callable[[str, Mapping[str, object]], None] | None = None,
    ) -> None:
        if not callable(sleep) or not callable(monotonic):
            raise ValueError("sleep and monotonic must be callable")
        if cancel_event is not None and not callable(getattr(cancel_event, "wait", None)):
            raise ValueError("cancel_event must expose wait(timeout)")
        if event_sink is not None and not callable(event_sink):
            raise ValueError("event_sink must be callable")
        self._sleep = sleep
        self._monotonic = monotonic
        self._cancel_event = cancel_event
        self._counter = probe_counter
        self._event_sink = event_sink

    def _emit(self, event: str, fields: Mapping[str, object]) -> None:
        if self._event_sink is None:
            return
        try:
            self._event_sink(event, dict(fields))
        except Exception:
            pass

    def wait(self, plan: TimerSleepPlan) -> TimerSleepResult:
        """Block until the planned timer expires (+ grace), or a cancel arrives.

        There is exactly one blocking call and no loop.  ``Event.wait`` is used
        when a cancel signal is configured: it is a real blocking wait that a
        shutdown can wake, not a periodic screenshot/status poll.
        """
        if not isinstance(plan, TimerSleepPlan):
            raise ValueError("wait requires a TimerSleepPlan")
        before = self._counter.snapshot()
        started_at = self._monotonic()
        self._emit("expedition_timer_sleep_started", {
            "fleet_id": plan.fleet_id,
            "deadline_epoch_ms": plan.deadline_epoch_ms,
            "deadline_iso": plan.deadline_iso,
            "requested_sleep_seconds": round(plan.sleep_seconds, 3),
            "grace_seconds": plan.grace_seconds,
            "start_reason": plan.reason,
            "fleet_timer_count": len(plan.timers),
            "fleet_timers": [
                {"fleet_id": item.fleet_id, "deadline_epoch_ms": item.deadline_epoch_ms,
                 "source": item.source}
                for item in plan.timers
            ],
            "pending_fleet_ids": list(plan.pending_fleet_ids),
            "probe_calls_before_sleep": dict(before),
            "timer_is_return_evidence": False,
            "dispatches_sent": 0,
        })

        cancelled = False
        if not plan.should_sleep:
            end_reason = plan.reason
        elif self._cancel_event is not None:
            # A single interruptible blocking wait; no polling wake-up.
            cancelled = bool(self._cancel_event.wait(timeout=plan.sleep_seconds))
            end_reason = "shutdown_cancelled" if cancelled else "timer_expired"
        else:
            self._sleep(plan.sleep_seconds)
            end_reason = "timer_expired"

        after = self._counter.snapshot()
        delta = self._counter.delta(before)
        actual = max(0.0, self._monotonic() - started_at)
        violation = _total_delta(delta) > 0
        self._emit("expedition_timer_sleep_finished", {
            "fleet_id": plan.fleet_id,
            "deadline_epoch_ms": plan.deadline_epoch_ms,
            "deadline_iso": plan.deadline_iso,
            "requested_sleep_seconds": round(plan.sleep_seconds, 3),
            "actual_sleep_seconds": round(actual, 3),
            "start_reason": plan.reason,
            "end_reason": end_reason,
            "probe_calls_during_sleep": dict(delta),
            "probe_calls_total_during_sleep": _total_delta(delta),
            "probe_violation": violation,
            "timer_is_return_evidence": False,
            "returned": False,
            "dispatches_sent": 0,
            "next_step": (
                "abort" if violation else
                "stop_no_clicks" if cancelled else
                "visible_sortie_home_refresh_then_fresh_poi_read"
                if plan.should_sleep else plan.reason
            ),
        })
        if violation:
            raise ProbeDuringSleepError(
                f"external probes fired during the timer sleep: {dict(delta)}"
            )
        return TimerSleepResult(
            slept=plan.should_sleep,
            fleet_id=plan.fleet_id,
            deadline_epoch_ms=plan.deadline_epoch_ms,
            deadline_iso=plan.deadline_iso,
            requested_sleep_seconds=plan.sleep_seconds,
            actual_sleep_seconds=actual,
            start_reason=plan.reason,
            end_reason=end_reason,
            probe_calls_during_sleep=delta,
            grace_seconds=plan.grace_seconds,
            timer_is_return_evidence=False,
            returned=False,
            dispatches_sent=0,
        )


def event_sink_from_stdout(emit: Callable[..., Any]) -> Callable[[str, Mapping[str, object]], None]:
    """Adapt the production ``_print_event(event, **fields)`` helper."""
    def sink(event: str, fields: Mapping[str, object]) -> None:
        emit(event, **dict(fields))
    return sink
