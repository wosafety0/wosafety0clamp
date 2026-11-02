from __future__ import annotations

import json
import sys
import tempfile
import unittest
from types import SimpleNamespace
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kancollebot.expeditions.continuous_monitor import (  # noqa: E402
    ContinuousMonitorError,
    ContinuousMonitorReport,
    FleetMonitorResult,
)
from kancollebot.expeditions.continuous_scheduler import FleetPhase  # noqa: E402
from tools.run_continuous_expedition_monitor import (  # noqa: E402
    MonitorRuntime,
    build_runtime,
    run_monitor,
)


class FakeObserver:
    last_status = "not_started"

    def capture(self):
        return None


class FakeMonitor:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    def tick(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


class ContinuousMonitorRuntimeTests(unittest.TestCase):
    def test_build_runtime_wires_background_provider_into_read_only_reader(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "runtime" / "monitor.sqlite3"
            runtime = build_runtime(
                database_path=database,
                client=object(),
                observer=FakeObserver(),
                observer_timeout_seconds=2,
                max_state_age_seconds=15,
            )
            try:
                reader = runtime.monitor.state_reader
                self.assertIs(reader._port_snapshot_provider, runtime.provider)
                self.assertEqual(runtime.scheduler.dispatch_fleet_ids, (2, 3))
                self.assertEqual(runtime.scheduler.read_only_fleet_ids, (1,))
                self.assertEqual(runtime.scheduler.deferred_fleet_ids, (4,))
                self.assertEqual(
                    runtime.monitor.mission_requirements[2].mission_id, 1
                )
                self.assertEqual(
                    runtime.monitor.mission_requirements[3].mission_id, 3
                )
                self.assertEqual(runtime.unresolved_dispatch_fleets, ())
                self.assertTrue(database.is_file())
                self.assertFalse(hasattr(runtime, "dispatch_controller"))
            finally:
                runtime.provider.close(join_timeout_seconds=1)

    def test_successful_tick_emits_only_monitor_state_and_exits(self):
        now = datetime.now(timezone.utc)
        report = ContinuousMonitorReport(
            observed_at=now,
            source="poi-redux:test",
            fleets=(
                FleetMonitorResult(
                    fleet_id=2,
                    previous_phase=FleetPhase.IDLE,
                    current_phase=FleetPhase.IDLE,
                    action="not_ready",
                    reason="ship damage blocks readiness",
                ),
            ),
        )
        monitor = FakeMonitor(result=report)
        output = []
        runtime = MonitorRuntime(
            monitor=monitor,
            provider=SimpleNamespace(last_status="captured", is_running=True),
            scheduler=object(),
            unresolved_dispatch_fleets=(),
        )

        code = run_monitor(runtime, once=True, output=output.append)

        self.assertEqual(code, 0)
        self.assertEqual(monitor.calls, 1)
        payload = json.loads(output[0])
        self.assertEqual(payload["status"], "MONITOR_TICK_OK")
        self.assertEqual(payload["observer_status"], "captured")
        self.assertTrue(payload["observer_running"])
        self.assertFalse(payload["game_actions_enabled"])
        self.assertEqual(payload["fleets"][0]["fleet_id"], 2)

    def test_unverified_freshness_retries_without_changing_state(self):
        monitor = FakeMonitor(
            error=ContinuousMonitorError(
                "Poi observation is not trusted: source freshness is unverified"
            )
        )
        output = []
        sleeps = []
        runtime = MonitorRuntime(
            monitor=monitor,
            provider=SimpleNamespace(last_status="natural_response_timeout", is_running=True),
            scheduler=object(),
            unresolved_dispatch_fleets=(),
        )

        code = run_monitor(
            runtime,
            poll_seconds=0.5,
            max_ticks=2,
            sleep=sleeps.append,
            output=output.append,
        )

        self.assertEqual(code, 2)
        self.assertEqual(monitor.calls, 2)
        self.assertEqual(sleeps, [0.5])
        self.assertEqual(
            [json.loads(item)["status"] for item in output],
            ["FRESHNESS_UNVERIFIED", "FRESHNESS_UNVERIFIED"],
        )
        self.assertTrue(
            all(json.loads(item)["observer_status"] == "natural_response_timeout" for item in output)
        )
        self.assertTrue(
            all(json.loads(item)["observer_running"] for item in output)
        )
        self.assertTrue(
            all(json.loads(item)["game_actions_enabled"] is False for item in output)
        )

    def test_unexpected_runtime_error_stops_fail_closed(self):
        monitor = FakeMonitor(error=RuntimeError("unexpected"))
        output = []
        runtime = MonitorRuntime(
            monitor=monitor,
            provider=object(),
            scheduler=object(),
            unresolved_dispatch_fleets=(),
        )

        code = run_monitor(runtime, output=output.append)

        self.assertEqual(code, 2)
        self.assertEqual(monitor.calls, 1)
        payload = json.loads(output[0])
        self.assertEqual(payload["status"], "MONITOR_TICK_FAILED")
        self.assertFalse(payload["game_actions_enabled"])


if __name__ == "__main__":
    unittest.main()
