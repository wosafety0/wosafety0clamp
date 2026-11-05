"""P0.2: the dispatch identity boundary and its single production binding.

Two separate claims are pinned here.

**Who may say "this row belongs to dispatch X".** Only the runtime. The envelope
refuses ``dispatch_id`` / ``action_id`` / ``battle_id`` / ``correlation_id`` from
emitters outright, because no production emitter passes any of them: refusing
them is free, and accepting them would be the one way a caller could fake a
correlation the composition root never made.

**Where that identity is minted.** One place only - the supervisor's dispatch
loop, around the single call that runs one whole attempt (baseline read, gate,
intent, click, home port, confirmation). Every component shares one sink object,
so the rows the controller and the UI driver emit inside that call inherit it.

The tests are offline: a fake scheduler/controller and a real run log in a
temporary directory.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from kancollebot.expeditions.continuous_scheduler import FleetPhase  # noqa: E402
from kancollebot.expeditions.continuous_supervisor import (  # noqa: E402
    ContinuousExpeditionSupervisor,
)
from kancollebot.observability.event_envelope import (  # noqa: E402
    CorrelationContext,
    build_event_record,
)
from kancollebot.observability.run_log import RunLogPolicy, RunLogStore  # noqa: E402
from kancollebot.observability.sink import build_run_log_sink  # noqa: E402
from tests.test_continuous_supervisor import (  # noqa: E402
    FakeMonitor,
    FakeScheduler,
)


class DispatchError(RuntimeError):
    """Stands in for ``ContinuousDispatchError`` raised by the real gate."""


class RecordingController:
    """A controller double that emits the real dispatch event sequence.

    It deliberately emits through ``self.event_sink`` - the same object the
    supervisor and (in production) the UI driver hold - because that shared
    object is the whole reason one correlation scope can cover a whole attempt.
    """

    def __init__(self, scheduler, state_reader, event_sink, *, fail_at=None):
        self.scheduler = scheduler
        self.state_reader = state_reader
        self.event_sink = event_sink
        self.fail_at = fail_at
        self.allow_ui_actions = True
        self.attempts: list[int] = []

    def _emit(self, event, fields):
        """Emit only when the injected sink can take it (a sentinel cannot)."""
        if callable(self.event_sink):
            self.event_sink(event, fields)

    def dispatch_one_ready(self, fleet_id):
        self.attempts.append(fleet_id)
        self._emit(
            "dispatch_requirement_evaluation",
            {"fleet_id": fleet_id, "overall_result": "PASS"},
        )
        if self.fail_at == "gate":
            self._emit(
                "dispatch_blocked_requirement_check",
                {"fleet_id": fleet_id, "overall_result": "REJECT", "dispatch_clicks_sent": 0},
            )
            raise DispatchError(f"fleet {fleet_id} rejected")
        self._emit(
            "dispatch_intent_persisted",
            {"fleet_id": fleet_id, "mission_id": 1, "ship_ids": (1, 2, 3, 4)},
        )
        self._emit(
            "continuous_ui_click_attempted",
            {"fleet_id": fleet_id, "action": "expedition_start"},
        )
        self._emit(
            "continuous_ui_click_sent",
            {"fleet_id": fleet_id, "action": "expedition_start"},
        )
        if self.fail_at == "click":
            raise DispatchError(f"fleet {fleet_id} click failed")
        self._emit(
            "dispatch_confirmed_by_poi",
            {"fleet_id": fleet_id, "mission_id": 1, "ship_ids": (1, 2, 3, 4)},
        )
        state = self.scheduler.states[fleet_id]
        state.phase = FleetPhase.EXPEDITION_ACTIVE
        state.home_port_verified = True
        return SimpleNamespace(fleet_id=fleet_id)


class AccumulatingSink:
    """A sink that records and, unlike a plain function, can express correlation."""

    def __init__(self) -> None:
        self.context = CorrelationContext(
            run_id="run-20261106T120000Z-aaaaaaaaaaaa", task_kind="expedition"
        )
        self.rows: list[dict] = []

    def __call__(self, event: str, fields) -> None:
        self.rows.append(
            build_event_record(
                event=event,
                fields=fields,
                context=self.context,
                event_id=f"event-{len(self.rows):04d}",
                timestamp_utc="2026-11-06T12:00:00+00:00",
            )
        )

    def correlation(self, **updates):
        return _Scope(self, updates)


class _Scope:
    def __init__(self, sink: AccumulatingSink, updates: dict) -> None:
        self._sink = sink
        self._updates = updates
        self._previous = sink.context

    def __enter__(self) -> None:
        self._sink.context = self._previous.with_(**self._updates)
        return self._sink.context

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._sink.context = self._previous
        return False


class IdentityRefusedFromEmittersTests(unittest.TestCase):
    """The trust boundary itself, at the envelope level."""

    def make_record(self, fields):
        return build_event_record(
            event="continuous_ui_click_sent",
            fields=fields,
            context=CorrelationContext(
                run_id="run-20261106T120000Z-aaaaaaaaaaaa", task_kind="expedition"
            ),
            event_id="event-0001",
            timestamp_utc="2026-11-06T12:00:00+00:00",
        )

    def test_an_emitter_cannot_invent_a_correlation(self):
        record = self.make_record(
            {
                "dispatch_id": "dispatch-20261106T120000Z-deadbeef0000",
                "action_id": "action-20261106T120000Z-deadbeef0000",
                "battle_id": "battle-20261106T120000Z-deadbeef0000",
                "correlation_id": "corr-20261106T120000Z-deadbeef0000",
                "mission_id": 1,
            }
        )
        for field in ("dispatch_id", "action_id", "battle_id", "correlation_id"):
            self.assertNotIn(field, record, f"{field} must not be forgeable")
        # The rest of the payload still survives: refusing the identity is not
        # refusing the diagnostics.
        self.assertEqual(record["mission_id"], 1)

    def test_the_runtime_value_still_wins_when_both_are_present(self):
        record = build_event_record(
            event="continuous_ui_click_sent",
            fields={"dispatch_id": "dispatch-forged", "mission_id": 1},
            context=CorrelationContext(
                run_id="run-20261106T120000Z-aaaaaaaaaaaa",
                task_kind="expedition",
                dispatch_id="dispatch-real",
            ),
            event_id="event-0002",
            timestamp_utc="2026-11-06T12:00:00+00:00",
        )
        self.assertEqual(record["dispatch_id"], "dispatch-real")

    def test_a_malformed_fleet_id_is_not_promoted(self):
        for bad in ("4", True, 0, -1, 4.0, None):
            with self.subTest(value=bad):
                record = self.make_record({"fleet_id": bad})
                self.assertNotIn("fleet_id", record)
        self.assertEqual(self.make_record({"fleet_id": 4})["fleet_id"], 4)


class DispatchAttemptIdentityTests(unittest.TestCase):
    """One attempt is one identity, minted by the supervisor and nowhere else."""

    def make_supervisor(self, phases, sink, *, fail_at=None):
        scheduler = FakeScheduler(phases, {2: True, 3: True})
        reader = object()
        monitor = FakeMonitor(scheduler, state_reader=reader, event_sink=sink)
        controller = RecordingController(
            scheduler, reader, sink, fail_at=fail_at
        )
        return ContinuousExpeditionSupervisor(
            monitor=monitor, dispatch_controller=controller
        )

    def test_every_row_of_one_attempt_shares_its_identifier(self):
        sink = AccumulatingSink()
        supervisor = self.make_supervisor(
            {2: FleetPhase.READY, 3: FleetPhase.EXPEDITION_ACTIVE}, sink
        )
        supervisor.tick()

        attempt_rows = [
            row
            for row in sink.rows
            if row["event"].startswith(("dispatch_", "continuous_ui_"))
        ]
        self.assertEqual(
            [row["event"] for row in attempt_rows],
            [
                "dispatch_requirement_evaluation",
                "dispatch_intent_persisted",
                "continuous_ui_click_attempted",
                "continuous_ui_click_sent",
                "dispatch_confirmed_by_poi",
            ],
        )
        identifiers = {row["dispatch_id"] for row in attempt_rows}
        self.assertEqual(len(identifiers), 1)
        self.assertIsNotNone(identifiers.pop())
        # All four describe the one fleet this attempt acted on.
        self.assertEqual({row["fleet_id"] for row in attempt_rows}, {2})

    def test_two_fleets_get_two_identifiers_in_the_same_tick(self):
        sink = AccumulatingSink()
        supervisor = self.make_supervisor(
            {2: FleetPhase.READY, 3: FleetPhase.READY}, sink
        )
        result = supervisor.tick()

        self.assertIsNone(result.paused_reason)
        self.assertEqual(len(result.dispatches), 2)
        intents = [row for row in sink.rows if row["event"] == "dispatch_intent_persisted"]
        self.assertEqual(len(intents), 2)
        first, second = intents
        self.assertNotEqual(first["dispatch_id"], second["dispatch_id"])
        self.assertEqual((first["fleet_id"], second["fleet_id"]), (2, 3))

    def test_a_refused_attempt_is_identified_but_never_claims_a_dispatch(self):
        # The id is minted before the gate, so a REJECT still has one attempt
        # identity - but it must never carry the row that means "a dispatch
        # happened". A reader that treats dispatch_id as proof would be wrong,
        # and this test is what keeps the difference visible.
        sink = AccumulatingSink()
        supervisor = self.make_supervisor(
            {2: FleetPhase.READY, 3: FleetPhase.EXPEDITION_ACTIVE},
            sink,
            fail_at="gate",
        )
        with self.assertRaises(DispatchError):
            supervisor.tick()

        names = [row["event"] for row in sink.rows]
        self.assertIn("dispatch_requirement_evaluation", names)
        self.assertIn("dispatch_blocked_requirement_check", names)
        self.assertNotIn("dispatch_intent_persisted", names)
        blocked = [row for row in sink.rows if row["event"] == "dispatch_blocked_requirement_check"][0]
        self.assertIsNotNone(blocked["dispatch_id"])

    def test_the_identity_does_not_leak_into_the_tick_row(self):
        sink = AccumulatingSink()
        supervisor = self.make_supervisor(
            {2: FleetPhase.READY, 3: FleetPhase.EXPEDITION_ACTIVE}, sink
        )
        supervisor.tick()

        ticks = [row for row in sink.rows if row["event"] == "supervisor_tick"]
        self.assertEqual(len(ticks), 1)
        self.assertNotIn("dispatch_id", ticks[0])
        # The attempt rows came first; the tick row is written after the scope
        # closed, which is why it cannot claim the attempt's identity.
        self.assertEqual(sink.rows[0]["event"], "dispatch_requirement_evaluation")
        self.assertEqual(sink.rows[-1]["event"], "supervisor_tick")

    def test_the_scope_closes_even_when_the_attempt_raises(self):
        sink = AccumulatingSink()
        supervisor = self.make_supervisor(
            {2: FleetPhase.READY, 3: FleetPhase.EXPEDITION_ACTIVE},
            sink,
            fail_at="click",
        )
        with self.assertRaises(DispatchError):
            supervisor.tick()
        self.assertIsNone(sink.context.dispatch_id, "the attempt scope must not leak")

    def test_a_sink_that_cannot_correlate_still_dispatches(self):
        # Offline doubles and the SQLite ledger have no correlation(). Logging
        # degrades to a null dispatch_id instead of blocking the dispatch.
        rows: list[tuple[str, dict]] = []

        def plain_sink(event, fields):
            rows.append((event, dict(fields)))

        supervisor = self.make_supervisor(
            {2: FleetPhase.READY, 3: FleetPhase.EXPEDITION_ACTIVE}, plain_sink
        )
        result = supervisor.tick()

        self.assertIsNone(result.paused_reason)
        self.assertIn("dispatch_intent_persisted", [name for name, _ in rows])
        self.assertIn("supervisor_tick", [name for name, _ in rows])

    def test_a_monitor_without_a_callable_sink_still_dispatches(self):
        supervisor = self.make_supervisor(
            {2: FleetPhase.READY, 3: FleetPhase.EXPEDITION_ACTIVE}, object()
        )
        self.assertIsNone(supervisor.tick().paused_reason)


class DispatchIdentityOnDiskTests(unittest.TestCase):
    """The same claims, read back from a real JSONL run log."""

    RUN_ID = "run-20261106T120000Z-0000000000d1"

    def test_a_dispatch_attempt_is_reconstructable_from_the_log(self):
        with tempfile.TemporaryDirectory() as directory:
            sink = build_run_log_sink(
                directory=directory,
                task_kind="expedition",
                run_id=self.RUN_ID,
                prune_interval_seconds=0,
            )
            scheduler = FakeScheduler(
                {2: FleetPhase.READY, 3: FleetPhase.EXPEDITION_ACTIVE},
                {2: True, 3: True},
            )
            reader = object()
            monitor = FakeMonitor(scheduler, state_reader=reader, event_sink=sink)
            controller = RecordingController(scheduler, reader, sink)
            supervisor = ContinuousExpeditionSupervisor(
                monitor=monitor, dispatch_controller=controller
            )
            supervisor.tick()

            store = RunLogStore(RunLogPolicy(directory=directory))
            records = store.read_events(run_id=self.RUN_ID)
            correlated = [row for row in records if row.get("dispatch_id")]
            self.assertEqual(len(correlated), 5)
            self.assertEqual({row["dispatch_id"] for row in correlated}, {correlated[0]["dispatch_id"]})
            self.assertEqual([row["event"] for row in correlated][-1], "dispatch_confirmed_by_poi")
            # Every row still carries the run identity the composition root set.
            self.assertEqual({row["run_id"] for row in records}, {self.RUN_ID})


if __name__ == "__main__":
    unittest.main()
