import unittest
from datetime import datetime, timezone

from kancollebot.expeditions.planner import (
    ExpeditionDecision,
    ExpeditionFleetSnapshot,
)
from kancollebot.expeditions.rotation import select_next_expedition_fleet


class FleetRotationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)

    def test_selects_lowest_id_when_no_preference_is_given(self):
        snapshots = (
            ExpeditionFleetSnapshot(3, (30,), fuel_ready=True, ammo_ready=True),
            ExpeditionFleetSnapshot(1, (10,), fuel_ready=True, ammo_ready=True),
            ExpeditionFleetSnapshot(2, (20,), fuel_ready=True, ammo_ready=True),
        )
        plan = select_next_expedition_fleet(snapshots, now=self.now)
        self.assertEqual(plan.selected_fleet_id, 1)

    def test_preference_only_selects_among_ready_fleets(self):
        snapshots = (
            ExpeditionFleetSnapshot(1, (10,), fuel_ready=False, ammo_ready=True),
            ExpeditionFleetSnapshot(2, (20,), fuel_ready=True, ammo_ready=True),
        )
        plan = select_next_expedition_fleet(
            snapshots, now=self.now, preferred_fleet_ids=(1, 2)
        )
        self.assertEqual(plan.selected_fleet_id, 2)
        self.assertEqual(plan.evaluated_fleets[0].decision, ExpeditionDecision.NEEDS_RESUPPLY)

    def test_no_ready_fleet_returns_no_selection(self):
        snapshots = (
            ExpeditionFleetSnapshot(1, (), fuel_ready=True, ammo_ready=True),
            ExpeditionFleetSnapshot(2, (20,), mission_id=4),
        )
        plan = select_next_expedition_fleet(snapshots, now=self.now)
        self.assertIsNone(plan.selected_fleet_id)

    def test_duplicate_fleet_ids_are_rejected(self):
        snapshots = (
            ExpeditionFleetSnapshot(1, (10,), fuel_ready=True, ammo_ready=True),
            ExpeditionFleetSnapshot(1, (11,), fuel_ready=True, ammo_ready=True),
        )
        with self.assertRaisesRegex(ValueError, "must be unique"):
            select_next_expedition_fleet(snapshots, now=self.now)

    def test_duplicate_preferences_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "preferred fleet IDs must be unique"):
            select_next_expedition_fleet(
                (), now=self.now, preferred_fleet_ids=(1, 1)
            )

    def test_empty_input_is_safe(self):
        plan = select_next_expedition_fleet((), now=self.now)
        self.assertIsNone(plan.selected_fleet_id)
        self.assertIn("No observed fleet", plan.reason)


if __name__ == "__main__":
    unittest.main()
