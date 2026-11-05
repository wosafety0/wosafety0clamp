"""P0.2 run log: the supervisor's scheduling decision reaches the log.

The supervisor used to be the one component that emitted nothing at all, so a
run could not answer "why did nothing get dispatched this tick". These tests pin
the new rows and, just as importantly, that a monitor double without a callable
sink is tolerated instead of crashed on.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from kancollebot.expeditions.continuous_scheduler import FleetPhase  # noqa: E402
from kancollebot.expeditions.continuous_supervisor import (  # noqa: E402
    ContinuousExpeditionSupervisor,
)
from kancollebot.observability.run_log import RunLogPolicy, RunLogStore  # noqa: E402
from kancollebot.observability.sink import build_run_log_sink  # noqa: E402
from tests.test_continuous_supervisor import (  # noqa: E402
    FakeController,
    FakeMonitor,
    FakeScheduler,
)


class SupervisorDecisionLoggingTests(unittest.TestCase):
    def make_supervisor(self, phases, sink, *, home_verified=None):
        scheduler = FakeScheduler(phases, home_verified)
        reader = object()
        monitor = FakeMonitor(scheduler, state_reader=reader, event_sink=sink)
        controller = FakeController(scheduler, reader, sink)
        return ContinuousExpeditionSupervisor(monitor=monitor, dispatch_controller=controller)

    def test_a_running_tick_is_recorded_with_the_fleet_phases(self):
        events: list[tuple[str, dict]] = []
        supervisor = self.make_supervisor(
            {2: FleetPhase.EXPEDITION_ACTIVE, 3: FleetPhase.READY},
            lambda name, fields: events.append((name, dict(fields))),
            home_verified={2: True},
        )
        result = supervisor.tick()

        self.assertIsNone(result.paused_reason)
        names = [name for name, _ in events]
        self.assertIn("supervisor_tick", names)
        fields = [item for name, item in events if name == "supervisor_tick"][0]
        # Phases are read after the tick, so the fleet that was just dispatched
        # is already active - that is the state the next tick will start from.
        self.assertEqual(
            fields["fleet_phases"], {"2": "EXPEDITION_ACTIVE", "3": "EXPEDITION_ACTIVE"}
        )
        self.assertEqual(fields["dispatch_fleet_ids"], [3])
        self.assertEqual(fields["dispatch_count"], 1)
        self.assertIsNone(fields["paused_reason"])

    def test_a_deliberate_pause_is_recorded_with_its_reason(self):
        events: list[tuple[str, dict]] = []
        supervisor = self.make_supervisor(
            {2: FleetPhase.RETURNED_PENDING_UI, 3: FleetPhase.EXPEDITION_ACTIVE},
            lambda name, fields: events.append((name, dict(fields))),
            home_verified={3: True},
        )
        result = supervisor.tick()

        self.assertEqual(result.paused_reason, "fleet_2_settlement_pending")
        names = [name for name, _ in events]
        self.assertIn("supervisor_paused", names)
        fields = [item for name, item in events if name == "supervisor_paused"][0]
        self.assertEqual(fields["paused_reason"], "fleet_2_settlement_pending")
        self.assertEqual(fields["settlement_count"], 0)

    def test_a_monitor_without_a_callable_sink_is_tolerated(self):
        # The supervisor contract only requires the injected sinks to be the
        # SAME object; offline doubles use a sentinel. Logging must not turn
        # that into a crash.
        supervisor = self.make_supervisor(
            {2: FleetPhase.EXPEDITION_ACTIVE, 3: FleetPhase.EXPEDITION_ACTIVE},
            object(),
            home_verified={2: True, 3: True},
        )
        result = supervisor.tick()
        self.assertIsNone(result.paused_reason)

    def test_the_scheduling_decision_lands_in_the_real_run_log(self):
        with tempfile.TemporaryDirectory() as directory:
            sink = build_run_log_sink(
                directory=directory,
                task_kind="expedition",
                run_id="run-20261106T120000Z-0000000000c1",
                prune_interval_seconds=0,
            )
            supervisor = self.make_supervisor(
                {2: FleetPhase.BLOCKED, 3: FleetPhase.EXPEDITION_ACTIVE},
                sink,
                home_verified={3: True},
            )
            result = supervisor.tick()
            self.assertEqual(result.paused_reason, "fleet_2_blocked")

            store = RunLogStore(RunLogPolicy(directory=directory))
            records = store.read_events(run_id="run-20261106T120000Z-0000000000c1")
            paused = [item for item in records if item["event"] == "supervisor_paused"]
            self.assertEqual(len(paused), 1)
            self.assertEqual(paused[0]["paused_reason"], "fleet_2_blocked")
            self.assertEqual(paused[0]["task_kind"], "expedition")
            # The file on disk is still plain JSONL, one object per line.
            path = sorted(Path(directory).glob("run-*.jsonl"))[0]
            for line in path.read_text(encoding="utf-8").splitlines():
                self.assertIsInstance(json.loads(line), dict)


if __name__ == "__main__":
    unittest.main()
