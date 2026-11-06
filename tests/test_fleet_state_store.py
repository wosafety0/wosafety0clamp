from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kancollebot.expeditions.continuous_scheduler import (
    ContinuousExpeditionScheduler,
    FleetPhase,
    FleetRuntime,
    MissionTuple,
    SchedulerInvariantError,
)
from kancollebot.expeditions.fleet_state_store import (
    FleetStateStoreError,
    SqliteFleetStateStore,
)


class FleetStateStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "fleet-state.sqlite3"
        self.mission = MissionTuple(1, 3, 1793531044934, 0, (4, 52, 32, 63))

    def tearDown(self):
        self.temp_dir.cleanup()

    def returned(self):
        return MissionTuple(2, self.mission.mission_id, self.mission.final_time_ms,
                            self.mission.reserved, self.mission.ship_ids)

    def dispatch_and_return_home(self, scheduler):
        scheduler.mark_ready(3)
        scheduler.begin_dispatch(3)
        scheduler.confirm_dispatch(3, self.mission)
        scheduler.confirm_home_port_after_dispatch(
            3, ui_state="HOME_PORT", observed_mission=self.mission
        )

    def test_restart_restores_active_fleet_and_home_port_gate(self):
        store = SqliteFleetStateStore(self.db_path)
        scheduler = ContinuousExpeditionScheduler(state_store=store)
        self.dispatch_and_return_home(scheduler)

        restarted = ContinuousExpeditionScheduler(
            state_store=SqliteFleetStateStore(self.db_path)
        )
        state = restarted.fleet(3)
        self.assertEqual(state.phase, FleetPhase.EXPEDITION_ACTIVE)
        self.assertEqual(state.mission, self.mission)
        self.assertTrue(state.home_port_verified)
        self.assertTrue(restarted.may_wait_for_return(3))

    def test_interrupted_dispatch_is_restored_without_retry_permission(self):
        scheduler = ContinuousExpeditionScheduler(
            state_store=SqliteFleetStateStore(self.db_path)
        )
        scheduler.mark_ready(2)
        scheduler.begin_dispatch(2)

        restarted = ContinuousExpeditionScheduler(
            state_store=SqliteFleetStateStore(self.db_path)
        )
        self.assertEqual(restarted.fleet(2).phase, FleetPhase.DISPATCHING)
        with self.assertRaisesRegex(SchedulerInvariantError, "READY"):
            restarted.begin_dispatch(2)

    def test_blocked_state_and_reason_survive_restart(self):
        scheduler = ContinuousExpeditionScheduler(
            state_store=SqliteFleetStateStore(self.db_path)
        )
        scheduler.block(3, "ambiguous UI state")

        restarted = ContinuousExpeditionScheduler(
            state_store=SqliteFleetStateStore(self.db_path)
        )
        state = restarted.fleet(3)
        self.assertEqual(state.phase, FleetPhase.BLOCKED)
        self.assertEqual(state.blocked_reason, "ambiguous UI state")

    def test_returned_state_and_status_two_survive_restart(self):
        scheduler = ContinuousExpeditionScheduler(
            state_store=SqliteFleetStateStore(self.db_path)
        )
        self.dispatch_and_return_home(scheduler)
        scheduler.confirm_returned(3, self.returned())

        restarted = ContinuousExpeditionScheduler(
            state_store=SqliteFleetStateStore(self.db_path)
        )
        state = restarted.fleet(3)
        self.assertEqual(state.phase, FleetPhase.RETURNED_PENDING_UI)
        self.assertEqual(state.mission.mission_status, 2)
        self.assertFalse(state.home_port_verified)

    def test_corrupt_persisted_mission_tuple_fails_closed(self):
        store = SqliteFleetStateStore(self.db_path)
        scheduler = ContinuousExpeditionScheduler(state_store=store)
        scheduler.mark_ready(2)
        scheduler.begin_dispatch(2)
        scheduler.confirm_dispatch(2, self.mission)
        with store._connect() as db:
            db.execute(
                "UPDATE fleet_runtime SET ship_ids_json = ? WHERE fleet_id = 2",
                ("not-json",),
            )
        with self.assertRaises(FleetStateStoreError):
            store.load_all()

    def test_persisted_fleet_outside_configured_scope_fails_closed(self):
        store = SqliteFleetStateStore(self.db_path)
        store.save(FleetRuntime(4))
        with self.assertRaisesRegex(SchedulerInvariantError, "outside"):
            ContinuousExpeditionScheduler(state_store=store)

    def test_store_failure_does_not_advance_in_memory_state(self):
        class BrokenStore:
            def load_all(self):
                return ()

            def save(self, state):
                raise OSError("disk unavailable")

        scheduler = ContinuousExpeditionScheduler(state_store=BrokenStore())
        with self.assertRaises(OSError):
            scheduler.mark_ready(2)
        self.assertEqual(scheduler.fleet(2).phase, FleetPhase.IDLE)


if __name__ == "__main__":
    unittest.main()
