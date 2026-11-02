from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kancollebot.core.observed_state import ObservedState
from kancollebot.expeditions.continuous_scheduler import (
    ContinuousExpeditionScheduler,
    FleetPhase,
    MissionTuple,
)
from kancollebot.expeditions.dispatch_recovery import (
    DispatchIntent,
    DispatchIntentError,
    DurableDispatchCoordinator,
    SqliteDispatchIntentStore,
)
from kancollebot.expeditions.fleet_state_store import SqliteFleetStateStore
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot


UTC = timezone.utc
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
SHIPS = (11, 22, 33, 44)


class DurableDispatchRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "dispatch.sqlite3"
        self.fleet_store = SqliteFleetStateStore(self.db)
        self.intent_store = SqliteDispatchIntentStore(self.db)
        self.scheduler = ContinuousExpeditionScheduler(state_store=self.fleet_store)
        self.scheduler.mark_ready(2)
        self.coordinator = DurableDispatchCoordinator(self.scheduler, self.intent_store)

    def tearDown(self):
        self.temp.cleanup()

    def observation(self, fleet, *, at, fresh=True):
        return ObservedState((fleet,), at, "poi-redux:info.fleets+info.ships", fresh)

    def idle_fleet(self, fleet_id=2, ships=SHIPS, *, fuel_ready=True, ammo_ready=True):
        return ExpeditionFleetSnapshot(
            fleet_id, ships, mission_id=None, return_at=None,
            fuel_ready=fuel_ready, ammo_ready=ammo_ready, mission_status=0, reserved=0,
        )

    def active_fleet(self, *, at, status=1, mission_id=3, ships=SHIPS):
        return ExpeditionFleetSnapshot(
            2, ships, mission_id=mission_id, return_at=at,
            fuel_ready=True, ammo_ready=True, mission_status=status, reserved=0,
        )

    def prepare(self):
        baseline = self.observation(
            self.idle_fleet(), at=T0 - timedelta(seconds=1)
        )
        return self.coordinator.prepare_dispatch(
            2, 3, baseline, now=T0, reserved=0
        )

    def test_dispatch_intent_rejects_boolean_baseline_slots(self):
        with self.assertRaisesRegex(ValueError, "integer idle baseline"):
            DispatchIntent(
                2, 3, SHIPS, (False, 0, 0, 0),
                T0 - timedelta(seconds=1), T0,
            )

    def test_intent_is_durable_before_dispatch_state_advances(self):
        intent = self.prepare()
        self.assertEqual(self.scheduler.fleet(2).phase, FleetPhase.DISPATCHING)
        self.assertEqual(self.intent_store.get(2), intent)

    def test_action_completion_marker_survives_store_reload(self):
        self.prepare()
        completed = T0 + timedelta(seconds=2)
        self.coordinator.mark_ui_action_completed(2, completed_at=completed)
        restored = SqliteDispatchIntentStore(self.db).get(2)
        self.assertEqual(restored.action_completed_at, completed)

    def test_intent_rejects_non_datetime_or_naive_action_completion(self):
        invalid_values = (
            "not-a-datetime",
            datetime(2026, 11, 1, 12, 0),
        )
        for value in invalid_values:
            with self.subTest(value=type(value).__name__):
                with self.assertRaisesRegex(
                    ValueError, "action_completed_at must be timezone-aware"
                ):
                    DispatchIntent(
                        2, 3, SHIPS, (0, 0, 0, 0),
                        T0 - timedelta(seconds=1), T0,
                        action_completed_at=value,
                    )

    def test_store_rejects_invalid_action_completion_without_mutating_intent(self):
        self.prepare()
        invalid_values = (
            None,
            "not-a-datetime",
            datetime(2026, 11, 1, 12, 0),
        )
        for value in invalid_values:
            with self.subTest(value=type(value).__name__):
                with self.assertRaisesRegex(
                    DispatchIntentError, "timezone-aware datetime"
                ):
                    self.coordinator.mark_ui_action_completed(2, completed_at=value)
        self.assertIsNone(self.intent_store.get(2).action_completed_at)
        self.assertEqual(self.scheduler.fleet(2).phase, FleetPhase.DISPATCHING)

    def test_prepare_dispatch_requires_boolean_ready_supply_before_persisting(self):
        invalid_readiness = (
            {"fuel_ready": False},
            {"ammo_ready": False},
            {"fuel_ready": 1},
            {"ammo_ready": 1},
        )
        for readiness in invalid_readiness:
            with self.subTest(readiness=readiness):
                baseline = self.observation(
                    self.idle_fleet(**readiness),
                    at=T0 - timedelta(seconds=1),
                )
                with self.assertRaises(DispatchIntentError):
                    self.coordinator.prepare_dispatch(2, 3, baseline, now=T0)
                self.assertEqual(self.scheduler.fleet(2).phase, FleetPhase.READY)
                self.assertIsNone(self.intent_store.get(2))

    def test_fresh_matching_active_mission_reconciles_without_home_port_bypass(self):
        self.prepare()
        self.coordinator.mark_ui_action_completed(
            2, completed_at=T0 + timedelta(seconds=1)
        )
        observed_at = T0 + timedelta(seconds=3)
        observation = self.observation(
            self.active_fleet(at=T0 + timedelta(minutes=15)),
            at=observed_at,
        )
        state = self.coordinator.reconcile_after_restart(
            2, observation, now=observed_at + timedelta(seconds=1)
        )
        self.assertEqual(state.phase, FleetPhase.EXPEDITION_ACTIVE)
        self.assertFalse(state.home_port_verified)
        self.assertFalse(self.scheduler.may_wait_for_return(2))
        self.assertIsNone(self.intent_store.get(2))

    def test_idle_state_after_interrupted_click_blocks_without_retry(self):
        self.prepare()
        observed_at = T0 + timedelta(seconds=3)
        state = self.coordinator.reconcile_after_restart(
            2, self.observation(self.idle_fleet(), at=observed_at),
            now=observed_at + timedelta(seconds=1),
        )
        self.assertEqual(state.phase, FleetPhase.BLOCKED)
        self.assertIn("automatic retry is forbidden", state.blocked_reason)
        self.assertIsNotNone(self.intent_store.get(2))

    def test_mismatched_mission_or_roster_blocks(self):
        self.prepare()
        observed_at = T0 + timedelta(seconds=3)
        state = self.coordinator.reconcile_after_restart(
            2, self.observation(
                self.active_fleet(at=T0 + timedelta(minutes=15), mission_id=1),
                at=observed_at,
            ), now=observed_at + timedelta(seconds=1),
        )
        self.assertEqual(state.phase, FleetPhase.BLOCKED)
        self.assertIsNotNone(self.intent_store.get(2))

    def test_already_returned_mission_requires_manual_settlement(self):
        self.prepare()
        observed_at = T0 + timedelta(minutes=20)
        state = self.coordinator.reconcile_after_restart(
            2, self.observation(
                self.active_fleet(at=T0 + timedelta(minutes=15), status=2),
                at=observed_at,
            ), now=observed_at + timedelta(seconds=1),
        )
        self.assertEqual(state.phase, FleetPhase.BLOCKED)
        self.assertIn("manual UI settlement", state.blocked_reason)

    def test_unverified_or_stale_observation_is_rejected_without_state_change(self):
        self.prepare()
        observed_at = T0 + timedelta(seconds=3)
        unverified = self.observation(
            self.active_fleet(at=T0 + timedelta(minutes=15)),
            at=observed_at, fresh=False,
        )
        with self.assertRaisesRegex(DispatchIntentError, "not trusted"):
            self.coordinator.reconcile_after_restart(
                2, unverified, now=observed_at + timedelta(seconds=1)
            )
        stale = self.observation(
            self.active_fleet(at=T0 + timedelta(minutes=15)),
            at=T0 - timedelta(seconds=1),
        )
        with self.assertRaisesRegex(DispatchIntentError, "not trusted"):
            self.coordinator.reconcile_after_restart(
                2, stale, now=T0 + timedelta(minutes=1), max_age=timedelta(seconds=5)
            )
        self.assertEqual(self.scheduler.fleet(2).phase, FleetPhase.DISPATCHING)

    def test_reconciliation_observation_must_follow_recorded_ui_action_completion(self):
        self.prepare()
        completed_at = T0 + timedelta(seconds=5)
        self.coordinator.mark_ui_action_completed(2, completed_at=completed_at)
        observed_at = T0 + timedelta(seconds=4)
        observation = self.observation(
            self.active_fleet(at=T0 + timedelta(minutes=15)),
            at=observed_at,
        )
        with self.assertRaisesRegex(DispatchIntentError, "predates the persisted UI action"):
            self.coordinator.reconcile_after_restart(
                2, observation, now=T0 + timedelta(seconds=6)
            )
        self.assertEqual(self.scheduler.fleet(2).phase, FleetPhase.DISPATCHING)

    def test_observation_must_be_from_poi_reader(self):
        self.prepare()
        at = T0 + timedelta(seconds=3)
        observation = ObservedState(
            (self.active_fleet(at=T0 + timedelta(minutes=15)),),
            at, "synthetic", True,
        )
        with self.assertRaisesRegex(DispatchIntentError, "Poi Redux"):
            self.coordinator.reconcile_after_restart(
                2, observation, now=at + timedelta(seconds=1)
            )

    def test_orphan_intent_before_scheduler_transition_blocks_on_restart(self):
        intent = DispatchIntent(
            2, 3, SHIPS, (0, 0, 0, 0), T0 - timedelta(seconds=1), T0,
        )
        self.intent_store.save(intent)
        unresolved = self.coordinator.recover_pending_intents()
        self.assertEqual(unresolved, (2,))
        self.assertEqual(self.scheduler.fleet(2).phase, FleetPhase.BLOCKED)

    def test_intent_left_after_scheduler_confirmation_is_reconciled(self):
        intent = self.prepare()
        mission = MissionTuple(1, intent.mission_id, int((T0 + timedelta(minutes=15)).timestamp() * 1000),
                               intent.reserved, intent.ship_ids)
        self.scheduler.confirm_dispatch(2, mission)
        unresolved = self.coordinator.recover_pending_intents()
        self.assertEqual(unresolved, ())
        self.assertIsNone(self.intent_store.get(2))

    def test_prepare_dispatch_blocks_when_an_orphan_intent_already_exists(self):
        self.intent_store.save(DispatchIntent(
            2, 3, SHIPS, (0, 0, 0, 0), T0 - timedelta(seconds=1), T0,
        ))
        baseline = self.observation(self.idle_fleet(), at=T0 - timedelta(seconds=1))
        with self.assertRaisesRegex(DispatchIntentError, "pending dispatch intent"):
            self.coordinator.prepare_dispatch(2, 3, baseline, now=T0)
        self.assertEqual(self.scheduler.fleet(2).phase, FleetPhase.BLOCKED)

    def test_intent_store_rejects_duplicate_pending_dispatch(self):
        self.prepare()
        with self.assertRaises(DispatchIntentError):
            self.intent_store.save(self.intent_store.get(2))


if __name__ == "__main__":
    unittest.main()
