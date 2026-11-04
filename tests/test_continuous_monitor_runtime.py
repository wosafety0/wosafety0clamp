from __future__ import annotations

import json
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kancollebot.adapters.poi.visible_ui_refresh import (  # noqa: E402
    FreshnessRefreshingStateReader,
)
from kancollebot.expeditions.continuous_dispatch_controller import (  # noqa: E402
    UiStateEvidence,
)
from kancollebot.expeditions.continuous_monitor import (  # noqa: E402
    ContinuousMonitorError,
    ContinuousMonitorReport,
    FleetMonitorResult,
)
from kancollebot.expeditions.continuous_scheduler import FleetPhase, UiOperationArbiter  # noqa: E402
from tools.run_continuous_expedition_monitor import (  # noqa: E402
    MonitorRuntime,
    _build_ui_state_verifier,
    build_runtime,
    run_monitor,
)


class FakeObserver:
    last_status = "not_started"

    def capture(self):
        return None


class FakeUiInput:
    def __init__(self):
        self.clicks = []

    def click(self, point):
        self.clicks.append(point)


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
                self.assertEqual(runtime.monitor.return_grace_delay.total_seconds(), 10.0)
                self.assertEqual(
                    runtime.monitor.mission_requirements[2].mission_id, 1
                )
                self.assertEqual(
                    runtime.monitor.mission_requirements[3].mission_id, 3
                )
                self.assertEqual(runtime.unresolved_dispatch_fleets, ())
                self.assertTrue(database.is_file())
                self.assertFalse(hasattr(runtime, "dispatch_controller"))
                self.assertFalse(runtime.ui_refresh_enabled)
                self.assertIsNone(runtime.ui_arbiter)
                self.assertIsNone(runtime.ui_refresh_driver)
            finally:
                runtime.provider.close(join_timeout_seconds=1)

    def test_build_runtime_rejects_negative_return_grace_delay(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "return_grace_delay_seconds"):
                build_runtime(
                    database_path=Path(temporary) / "monitor.sqlite3",
                    client=object(),
                    observer=FakeObserver(),
                    return_grace_delay_seconds=-0.1,
                )

    def test_build_runtime_can_scope_the_production_tick_to_one_verified_fleet(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "runtime" / "single-fleet.sqlite3"
            runtime = build_runtime(
                database_path=database,
                client=object(),
                observer=FakeObserver(),
                dispatch_fleet_ids=(3,),
            )
            try:
                self.assertEqual(runtime.scheduler.dispatch_fleet_ids, (3,))
                self.assertEqual(runtime.scheduler.read_only_fleet_ids, (1, 2))
                self.assertEqual(
                    set(runtime.monitor.mission_requirements),
                    {3},
                )
                self.assertEqual(
                    runtime.monitor.mission_requirements[3].mission_id,
                    3,
                )
            finally:
                runtime.provider.close(join_timeout_seconds=1)

    def test_ui_state_verifier_wraps_the_live_two_frame_checkpoint(self):
        from tools import one_expedition_ui_loop
        from kancollebot.adapters.poi.ui_state_gate import UiState

        game_session = object()
        with patch.object(
            one_expedition_ui_loop,
            "_require_ui_state",
            return_value="two_consecutive_ooi_frames",
        ) as checkpoint:
            verifier = _build_ui_state_verifier(game_session)
            evidence = verifier("HOME_PORT")

        checkpoint.assert_called_once_with(
            game_session, UiState.HOME_PORT, visual_attempts=6
        )
        self.assertEqual(evidence.state, "HOME_PORT")
        self.assertEqual(evidence.consecutive_states, ("HOME_PORT", "HOME_PORT"))
        self.assertEqual(evidence.evidence, "two_consecutive_ooi_frames")

    def test_build_runtime_wires_ui_refresh_only_when_explicitly_enabled(self):
        with tempfile.TemporaryDirectory() as temporary:
            game_input = FakeUiInput()
            database = Path(temporary) / "runtime" / "monitor.sqlite3"
            runtime = build_runtime(
                database_path=database,
                client=object(),
                observer=FakeObserver(),
                enable_ui_refresh=True,
                ui_input=game_input,
                ui_state_verifier=lambda expected: UiStateEvidence(
                    state=expected,
                    consecutive_states=(expected, expected),
                    evidence="two_consecutive_fake_frames",
                ),
            )
            try:
                self.assertTrue(runtime.ui_refresh_enabled)
                self.assertIsInstance(
                    runtime.monitor.state_reader, FreshnessRefreshingStateReader
                )
                self.assertIsInstance(runtime.ui_arbiter, UiOperationArbiter)
                self.assertIs(
                    runtime.ui_refresh_driver,
                    runtime.monitor.state_reader._refresh_driver,
                )
                self.assertIs(
                    runtime.monitor.state_reader._reader._port_snapshot_provider,
                    runtime.provider,
                )
                self.assertEqual(game_input.clicks, [])
                self.assertFalse(hasattr(runtime, "dispatch_controller"))
            finally:
                runtime.provider.close(join_timeout_seconds=1)

    def test_build_runtime_rejects_ui_refresh_without_input_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "open game input session"):
                build_runtime(
                    database_path=Path(temporary) / "monitor.sqlite3",
                    client=object(),
                    observer=FakeObserver(),
                    enable_ui_refresh=True,
                )

    def test_build_runtime_rejects_ui_input_when_refresh_is_disabled(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "require enable_ui_refresh"):
                build_runtime(
                    database_path=Path(temporary) / "monitor.sqlite3",
                    client=object(),
                    observer=FakeObserver(),
                    ui_input=FakeUiInput(),
                    ui_state_verifier=lambda expected: UiStateEvidence(
                        state=expected,
                        consecutive_states=(expected, expected),
                        evidence="two_consecutive_fake_frames",
                    ),
                )

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
        self.assertFalse(payload["ui_navigation_enabled"])
        self.assertEqual(payload["fleets"][0]["fleet_id"], 2)

    def test_ui_refresh_runtime_reports_navigation_without_game_actions(self):
        now = datetime.now(timezone.utc)
        report = ContinuousMonitorReport(
            observed_at=now,
            source="poi-redux:test",
            fleets=(),
        )
        runtime = MonitorRuntime(
            monitor=FakeMonitor(result=report),
            provider=SimpleNamespace(last_status="captured", is_running=False),
            scheduler=object(),
            unresolved_dispatch_fleets=(),
            ui_refresh_enabled=True,
            ui_arbiter=object(),
        )
        output = []

        code = run_monitor(runtime, once=True, output=output.append)

        self.assertEqual(code, 0)
        payload = json.loads(output[0])
        self.assertFalse(payload["game_actions_enabled"])
        self.assertTrue(payload["ui_navigation_enabled"])

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
        self.assertTrue(
            all(json.loads(item)["ui_navigation_enabled"] is False for item in output)
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
        self.assertFalse(payload["ui_navigation_enabled"])


if __name__ == "__main__":
    unittest.main()
