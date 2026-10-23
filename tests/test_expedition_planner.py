import unittest
from datetime import datetime, timedelta, timezone

from kancollebot.expeditions.planner import (
    ExpeditionDecision,
    ExpeditionFleetSnapshot,
    evaluate_expedition_fleet,
)


NOW = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)


class ExpeditionFleetSnapshotTests(unittest.TestCase):
    def test_rejects_naive_return_time(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            ExpeditionFleetSnapshot(
                fleet_id=1,
                ship_ids=(1,),
                mission_id=2,
                return_at=datetime(2026, 10, 23, 10, 0),
            )

    def test_return_time_requires_active_mission(self):
        with self.assertRaisesRegex(ValueError, "requires an active mission"):
            ExpeditionFleetSnapshot(
                fleet_id=1,
                ship_ids=(1,),
                return_at=NOW + timedelta(minutes=1),
            )

    def test_damaged_ship_must_belong_to_fleet(self):
        with self.assertRaisesRegex(ValueError, "belong to this fleet"):
            ExpeditionFleetSnapshot(
                fleet_id=1, ship_ids=(1,), damaged_ship_ids=(2,)
            )

    def test_rejects_non_boolean_readiness(self):
        with self.assertRaisesRegex(ValueError, "fuel_ready must be"):
            ExpeditionFleetSnapshot(
                fleet_id=1, ship_ids=(1,), fuel_ready=1, ammo_ready=True
            )

    def test_rejects_more_than_six_ships(self):
        with self.assertRaisesRegex(ValueError, "six ships"):
            ExpeditionFleetSnapshot(
                fleet_id=1,
                ship_ids=(1, 2, 3, 4, 5, 6, 7),
                fuel_ready=True,
                ammo_ready=True,
            )


class ExpeditionPlannerTests(unittest.TestCase):
    def test_ready_fleet_is_only_recommended_not_dispatched(self):
        snapshot = ExpeditionFleetSnapshot(
            fleet_id=2, ship_ids=(11, 12), fuel_ready=True, ammo_ready=True
        )
        plan = evaluate_expedition_fleet(snapshot, now=NOW)
        self.assertEqual(plan.decision, ExpeditionDecision.READY_TO_DISPATCH)
        self.assertEqual(plan.fleet_id, 2)

    def test_empty_fleet_is_blocked(self):
        snapshot = ExpeditionFleetSnapshot(
            fleet_id=1, ship_ids=(), fuel_ready=True, ammo_ready=True
        )
        self.assertEqual(
            evaluate_expedition_fleet(snapshot, now=NOW).decision,
            ExpeditionDecision.BLOCKED_EMPTY_FLEET,
        )

    def test_damaged_ship_blocks_dispatch(self):
        snapshot = ExpeditionFleetSnapshot(
            fleet_id=1,
            ship_ids=(1, 2),
            fuel_ready=True,
            ammo_ready=True,
            damaged_ship_ids=(2,),
        )
        self.assertEqual(
            evaluate_expedition_fleet(snapshot, now=NOW).decision,
            ExpeditionDecision.BLOCKED_DAMAGED_SHIPS,
        )

    def test_unknown_supply_is_not_treated_as_ready_or_empty(self):
        snapshot = ExpeditionFleetSnapshot(fleet_id=1, ship_ids=(1,))
        self.assertEqual(
            evaluate_expedition_fleet(snapshot, now=NOW).decision,
            ExpeditionDecision.BLOCKED_UNKNOWN_READINESS,
        )

    def test_missing_fuel_or_ammunition_requires_resupply(self):
        snapshot = ExpeditionFleetSnapshot(
            fleet_id=1, ship_ids=(1,), fuel_ready=False, ammo_ready=True
        )
        plan = evaluate_expedition_fleet(snapshot, now=NOW)
        self.assertEqual(plan.decision, ExpeditionDecision.NEEDS_RESUPPLY)
        self.assertIn("fuel", plan.reason)

    def test_active_mission_waits_until_return_time(self):
        snapshot = ExpeditionFleetSnapshot(
            fleet_id=1,
            ship_ids=(1,),
            mission_id=3,
            return_at=NOW + timedelta(minutes=5),
            fuel_ready=True,
            ammo_ready=True,
        )
        self.assertEqual(
            evaluate_expedition_fleet(snapshot, now=NOW).decision,
            ExpeditionDecision.WAITING_FOR_RETURN,
        )

    def test_elapsed_return_time_requires_state_refresh(self):
        snapshot = ExpeditionFleetSnapshot(
            fleet_id=1,
            ship_ids=(1,),
            mission_id=3,
            return_at=NOW - timedelta(seconds=1),
            fuel_ready=True,
            ammo_ready=True,
        )
        self.assertEqual(
            evaluate_expedition_fleet(snapshot, now=NOW).decision,
            ExpeditionDecision.RETURNED_AWAITING_REFRESH,
        )

    def test_active_mission_without_return_time_is_blocked(self):
        snapshot = ExpeditionFleetSnapshot(
            fleet_id=1, ship_ids=(1,), mission_id=3
        )
        self.assertEqual(
            evaluate_expedition_fleet(snapshot, now=NOW).decision,
            ExpeditionDecision.BLOCKED_UNKNOWN_RETURN_TIME,
        )

    def test_naive_now_is_rejected(self):
        snapshot = ExpeditionFleetSnapshot(
            fleet_id=1, ship_ids=(1,), fuel_ready=True, ammo_ready=True
        )
        with self.assertRaisesRegex(ValueError, "now must be timezone-aware"):
            evaluate_expedition_fleet(snapshot, now=datetime(2026, 10, 23, 9, 0))


if __name__ == "__main__":
    unittest.main()
