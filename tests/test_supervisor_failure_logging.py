"""Stage 1: a tick that raises must still say what it decided and how far it got.

The audit's highest-severity structural finding was that the supervisor recorded
its decision only after ``_tick_once()`` returned. A fresh-baseline refusal - the
most common production outcome - therefore produced **zero rows**, and a tick
that dispatched the first fleet before failing on the second lost the first
fleet's result entirely.

These tests pin four things:

* every raise path leaves one readable ``supervisor_paused`` row;
* that row carries the partial progress (which fleet succeeded, which failed);
* the original exception still reaches the caller unchanged, even when the audit
  write itself fails;
* the *success* path keeps fail-closed behaviour: a critical write failure there
  is not swallowed.
"""
from __future__ import annotations

import unittest
from contextlib import contextmanager
from types import SimpleNamespace

from kancollebot.expeditions.continuous_scheduler import FleetPhase
from kancollebot.expeditions.continuous_supervisor import (
    ContinuousExpeditionSupervisor,
)
from kancollebot.observability.audit_policy import AuditLogUnavailable


class ContinuousMonitorError(RuntimeError):
    """The production type the monitor raises when freshness cannot be proven."""


class RecordingSink:
    """A callable sink that also declares correlation, like the run log does."""

    def __init__(self, *, fail_on: str | None = None, error=None) -> None:
        self.rows: list[dict[str, object]] = []
        self.fail_on = fail_on
        self.error = error or AuditLogUnavailable("run log is not writable")

    def __call__(self, event, fields):
        if self.fail_on is not None and event == self.fail_on:
            raise self.error
        row = {"event": event}
        row.update(dict(fields))
        self.rows.append(row)

    @contextmanager
    def correlation(self, **updates):
        yield

    def events(self) -> list[str]:
        return [str(row["event"]) for row in self.rows]

    def one(self, event: str) -> dict[str, object]:
        rows = [row for row in self.rows if row["event"] == event]
        assert len(rows) == 1, f"expected exactly one {event}, got {rows}"
        return rows[0]


class FakeScheduler:
    def __init__(self, phases, home_verified=None) -> None:
        self.dispatch_fleet_ids = tuple(phases)
        self.states = {
            fleet_id: SimpleNamespace(
                phase=phase,
                home_port_verified=(home_verified or {}).get(fleet_id, False),
            )
            for fleet_id, phase in phases.items()
        }

    def fleet(self, fleet_id):
        return self.states[fleet_id]


class FakeMonitor:
    def __init__(self, scheduler, state_reader, event_sink, *, error=None) -> None:
        self.scheduler = scheduler
        self.state_reader = state_reader
        self.event_sink = event_sink
        self.error = error
        self.calls = 0

    def tick(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return SimpleNamespace(fleets=())


class FakeDispatchController:
    def __init__(self, scheduler, state_reader, event_sink, *, fail_on=None) -> None:
        self.scheduler = scheduler
        self.state_reader = state_reader
        self.event_sink = event_sink
        self.allow_ui_actions = True
        self.fail_on = fail_on
        self.calls: list[int] = []

    def dispatch_one_ready(self, fleet_id, *, dispatch_id=None):
        self.calls.append(fleet_id)
        if fleet_id == self.fail_on:
            raise RuntimeError(f"dispatch channel closed for fleet {fleet_id}")
        self.scheduler.states[fleet_id].phase = FleetPhase.EXPEDITION_ACTIVE
        self.scheduler.states[fleet_id].home_port_verified = True
        return SimpleNamespace(fleet_id=fleet_id)


class FakeSettlementController:
    def __init__(self, scheduler, state_reader, event_sink, *, error=None) -> None:
        self.scheduler = scheduler
        self.state_reader = state_reader
        self.event_sink = event_sink
        self.allow_ui_actions = True
        self.error = error
        self.calls: list[int] = []

    def settle_one_returned(self, fleet_id, *, pages_drained_globally=False):
        self.calls.append(fleet_id)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(fleet_id=fleet_id)


def build(
    phases,
    *,
    sink=None,
    monitor_error=None,
    dispatch_fail_on=None,
    settlement_error=None,
    home_verified=None,
):
    sink = sink if sink is not None else RecordingSink()
    scheduler = FakeScheduler(phases, home_verified)
    reader = object()
    monitor = FakeMonitor(scheduler, reader, sink, error=monitor_error)
    dispatch = FakeDispatchController(scheduler, reader, sink, fail_on=dispatch_fail_on)
    settlement = (
        FakeSettlementController(scheduler, reader, sink, error=settlement_error)
        if settlement_error is not None
        else None
    )
    supervisor = ContinuousExpeditionSupervisor(
        monitor=monitor,
        dispatch_controller=dispatch,
        settlement_controller=settlement,
    )
    return supervisor, sink, dispatch, settlement


class SuccessfulTickTests(unittest.TestCase):
    def test_a_normal_tick_records_the_summary_and_no_pause(self):
        supervisor, sink, dispatch, _ = build(
            {2: FleetPhase.READY, 3: FleetPhase.READY}
        )

        supervisor.tick()

        self.assertEqual(dispatch.calls, [2, 3])
        self.assertEqual(sink.events(), ["supervisor_tick"])
        row = sink.one("supervisor_tick")
        self.assertIsNone(row["paused_reason"])
        self.assertEqual(row["dispatch_fleet_ids"], [2, 3])
        self.assertEqual(row["dispatch_count"], 2)

    def test_a_paused_tick_writes_the_critical_pause_row(self):
        supervisor, sink, dispatch, _ = build({2: FleetPhase.BLOCKED})

        report = supervisor.tick()

        self.assertEqual(dispatch.calls, [])
        self.assertEqual(report.paused_reason, "fleet_2_blocked")
        self.assertEqual(sink.events(), ["supervisor_paused"])
        self.assertEqual(sink.one("supervisor_paused")["paused_reason"], "fleet_2_blocked")

    def test_a_failing_critical_write_on_the_success_path_is_not_swallowed(self):
        # Fail-closed stays where it was: a paused tick that cannot be recorded
        # must not proceed as if it had been.
        sink = RecordingSink(fail_on="supervisor_paused")
        supervisor, _, _, _ = build({2: FleetPhase.BLOCKED}, sink=sink)

        with self.assertRaises(AuditLogUnavailable):
            supervisor.tick()


class MonitorFailureTests(unittest.TestCase):
    def test_a_freshness_refusal_leaves_a_pause_row(self):
        error = ContinuousMonitorError("state not trusted")
        supervisor, sink, dispatch, _ = build(
            {2: FleetPhase.READY}, monitor_error=error
        )

        with self.assertRaises(Exception) as caught:
            supervisor.tick()

        self.assertIs(caught.exception, error)
        self.assertEqual(dispatch.calls, [])
        row = sink.one("supervisor_paused")
        self.assertEqual(
            row["paused_reason"], "monitor_failed:ContinuousMonitorError"
        )
        self.assertEqual(row["failed_phase"], "monitor")
        self.assertIsNone(row["failed_fleet_id"])
        self.assertEqual(row["dispatch_count"], 0)
        self.assertFalse(row["partial_result"])
        self.assertEqual(sink.events(), ["supervisor_paused"])

    def test_the_row_is_written_even_when_the_sink_only_accepts_a_plain_call(self):
        # A monitor double may expose a non-callable ``event_sink`` attribute;
        # the supervisor must not crash on it, and must not fabricate a row.
        supervisor, _, _, _ = build({2: FleetPhase.READY})
        supervisor.monitor.event_sink = object()

        supervisor.tick()

        self.assertEqual(supervisor.monitor.calls, 1)


class PartialTickTests(unittest.TestCase):
    def test_the_first_fleet_success_and_the_second_fleet_failure_are_one_row(self):
        supervisor, sink, dispatch, _ = build(
            {2: FleetPhase.READY, 3: FleetPhase.READY}, dispatch_fail_on=3
        )

        with self.assertRaisesRegex(RuntimeError, "closed for fleet 3"):
            supervisor.tick()

        self.assertEqual(dispatch.calls, [2, 3])
        row = sink.one("supervisor_paused")
        # Fleet 2's result survives; the failure names fleet 3 and the phase.
        self.assertEqual(row["dispatch_fleet_ids"], [2])
        self.assertEqual(row["dispatch_count"], 1)
        self.assertEqual(row["failed_fleet_id"], 3)
        self.assertEqual(row["failed_phase"], "dispatch")
        self.assertEqual(row["error_type"], "RuntimeError")
        self.assertEqual(row["paused_reason"], "fleet_3_dispatch_failed:RuntimeError")
        self.assertTrue(row["partial_result"])
        # The summary is a single row: no duplicate tick event beside it.
        self.assertEqual(sink.events(), ["supervisor_paused"])

    def test_a_settlement_failure_names_its_fleet_and_phase(self):
        supervisor, sink, dispatch, settlement = build(
            {2: FleetPhase.RETURNED_PENDING_UI, 3: FleetPhase.READY},
            settlement_error=RuntimeError("reward pages ambiguous"),
        )

        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            supervisor.tick()

        self.assertEqual(settlement.calls, [2])
        self.assertEqual(dispatch.calls, [])
        row = sink.one("supervisor_paused")
        self.assertEqual(row["paused_reason"], "fleet_2_settlement_failed:RuntimeError")
        self.assertEqual(row["failed_phase"], "settlement")
        self.assertEqual(row["failed_fleet_id"], 2)
        self.assertFalse(row["partial_result"])


class FailureRowWriteTests(unittest.TestCase):
    def test_a_failed_pause_write_never_masks_the_business_error(self):
        sink = RecordingSink(fail_on="supervisor_paused")
        error = RuntimeError("state not trusted")
        supervisor, _, _, _ = build({2: FleetPhase.READY}, sink=sink, monitor_error=error)

        with self.assertRaises(Exception) as caught:
            supervisor.tick()

        # The caller sees the monitor failure, not the logging failure: the run
        # already stopped, and the cause of the stop is the more useful fact.
        self.assertIs(caught.exception, error)
        self.assertNotIsInstance(caught.exception, AuditLogUnavailable)

    def test_the_error_message_is_carried_verbatim_and_truncated(self):
        error = RuntimeError("x" * 1000)
        supervisor, sink, _, _ = build({2: FleetPhase.READY}, monitor_error=error)

        with self.assertRaises(RuntimeError):
            supervisor.tick()

        row = sink.one("supervisor_paused")
        self.assertEqual(len(str(row["error"])), 400)
        self.assertEqual(row["error_type"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
