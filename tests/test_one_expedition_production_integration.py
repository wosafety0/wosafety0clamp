from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import one_expedition_ui_loop as entry
import run_continuous_expedition_monitor as monitor_runtime
from kancollebot.core.observed_state import ObservedState
from kancollebot.expeditions.continuous_monitor import ContinuousExpeditionMonitor
from kancollebot.expeditions.continuous_scheduler import (
    ContinuousExpeditionScheduler,
    FleetPhase,
)
from kancollebot.expeditions.mission_eligibility import MissionCompositionRequirement
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot


class FakeReader:
    def __init__(self, snapshots, *, fresh=True):
        self.snapshots = list(snapshots)
        self.fresh = fresh
        self.read_count = 0

    def read_fleets(self):
        self.read_count += 1
        if not self.snapshots:
            raise AssertionError("unexpected extra Poi observation")
        return ObservedState(
            (self.snapshots.pop(0),),
            datetime.now(timezone.utc),
            "poi-redux:production-integration-test",
            source_freshness_verified=self.fresh,
        )


class FakeGameInput:
    def __init__(self):
        self.clicks = []

    def click(self, point):
        self.clicks.append(point.label)


class FakeProvider:
    def __init__(self):
        self.closed = False

    def close(self, *, join_timeout_seconds):
        self.closed = True


def make_snapshot(*, status=0, mission_id=None, return_at=None):
    return ExpeditionFleetSnapshot(
        fleet_id=3,
        ship_ids=(31, 32, 33),
        mission_id=mission_id,
        return_at=return_at,
        fuel_ready=True,
        ammo_ready=True,
        damaged_ship_ids=(),
        mission_status=status,
        reserved=0,
        ship_levels=(3, 1, 1),
        ship_types=(1, 1, 1),
    )


class OneExpeditionProductionIntegrationTests(unittest.TestCase):
    def make_runtime(self, snapshots, *, fresh=True):
        scheduler = ContinuousExpeditionScheduler(
            dispatch_fleet_ids=(3,),
            read_only_fleet_ids=(1, 2),
            deferred_fleet_ids=(4,),
        )
        reader = FakeReader(snapshots, fresh=fresh)
        event_sink = lambda _name, _fields: None
        monitor = ContinuousExpeditionMonitor(
            scheduler=scheduler,
            state_reader=reader,
            mission_requirements={
                3: MissionCompositionRequirement(
                    mission_id=3,
                    minimum_ships=3,
                    minimum_flagship_level=3,
                )
            },
            event_sink=event_sink,
            max_state_age=timedelta(seconds=30),
        )
        provider = FakeProvider()
        runtime = SimpleNamespace(
            scheduler=scheduler,
            monitor=monitor,
            provider=provider,
        )
        return runtime, reader, provider

    def make_args(self, database):
        args = MagicMock()
        args.authorize_ui_actions = True
        args.poll_seconds = 5.0
        args.timeout_seconds = 1800.0
        args.grace_delay_seconds = 10.0
        args.database = database
        args.observer_script = ROOT / "tools" / "observer.js"
        args.observer_timeout_seconds = 5.0
        args.max_state_age_seconds = 30.0
        return args

    def test_entry_wires_real_monitor_controller_supervisor_and_cdp_driver(self):
        return_at = datetime.now(timezone.utc) + timedelta(minutes=20)
        idle = make_snapshot()
        active = make_snapshot(status=1, mission_id=3, return_at=return_at)
        runtime, reader, provider = self.make_runtime([idle, idle, active, active])

        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "production" / "state.sqlite3"
            database.parent.mkdir(parents=True)
            args = self.make_args(database)
            game_session = MagicMock()
            game_session.input = FakeGameInput()
            game_session.__enter__.return_value = game_session
            game_session.__exit__.return_value = False

            with (
                patch.object(entry, "connect_game_webview", return_value=game_session),
                patch.object(entry, "_capture_home_reference"),
                patch.object(entry, "_require_ui_state",
                             return_value="two_consecutive_in_memory_ooi_fingerprints"),
                patch.object(entry, "_print_event"),
                patch.object(monitor_runtime, "build_runtime", return_value=runtime) as build,
            ):
                self.assertEqual(entry.run(args), 0)

            build.assert_called_once()
            self.assertEqual(build.call_args.kwargs["dispatch_fleet_ids"], (3,))
            self.assertFalse(build.call_args.kwargs["enable_ui_refresh"])
            self.assertEqual(
                game_session.input.clicks,
                [
                    "sortie",
                    "expedition-mode",
                    "mission-3-row",
                    "mission-confirm",
                    "fleet-3-tab",
                    "expedition-start",
                    "home-port",
                ],
            )
            self.assertEqual(reader.read_count, 4)
            self.assertIs(runtime.scheduler.fleet(3).phase, FleetPhase.EXPEDITION_ACTIVE)
            self.assertTrue(runtime.scheduler.fleet(3).home_port_verified)
            self.assertTrue(provider.closed)

    def test_unfresh_monitor_observation_stops_before_any_ui_click(self):
        runtime, reader, provider = self.make_runtime([make_snapshot()], fresh=False)
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "production" / "state.sqlite3"
            database.parent.mkdir(parents=True)
            args = self.make_args(database)
            game_session = MagicMock()
            game_session.input = FakeGameInput()
            game_session.__enter__.return_value = game_session
            game_session.__exit__.return_value = False

            with (
                patch.object(entry, "connect_game_webview", return_value=game_session),
                patch.object(entry, "_capture_home_reference"),
                patch.object(entry, "_require_ui_state",
                             return_value="two_consecutive_in_memory_ooi_fingerprints"),
                patch.object(entry, "_print_event"),
                patch.object(monitor_runtime, "build_runtime", return_value=runtime),
            ):
                with self.assertRaisesRegex(entry.LoopError, "Poi observation is not trusted"):
                    entry.run(args)

            self.assertEqual(game_session.input.clicks, [])
            self.assertEqual(reader.read_count, 1)
            self.assertTrue(provider.closed)


if __name__ == "__main__":
    unittest.main()
