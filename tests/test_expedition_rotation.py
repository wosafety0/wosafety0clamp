import unittest
from datetime import datetime, timezone

from kancollebot.expeditions import (
    ExpeditionDecision,
    ExpeditionFleetSnapshot,
    select_next_expedition_fleet,
)


NOW = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)


def ready(fleet_id):
    return ExpeditionFleetSnapshot(
        fleet_id=fleet_id,
        ship_ids=(fleet_id * 10,),
        fuel_ready=True,
        ammo_ready=True,
    )


class FleetRotationTests(unittest.TestCase):
    def test_lowest_ready_fleet_id_wins_by_default(self):
        result = select_next_expedition_fleet((ready(3), ready(1), ready(2)), now=NOW)
        self.assertEqual(result.selected_fleet_id, 1)

    def test_preference_order_breaks_ties(self):
        result = select_next_expedition_fleet(
            (ready(1), ready(3), ready(2)),
            now=NOW,
            preferred_fleet_ids=(3, 2),
        )
        self.assertEqual(result.selected_fleet_id, 3)

    def test_unready_fleet_is_not_selected_even_if_preferred(self):
        unready = ExpeditionFleetSnapshot(
            fleet_id=3, ship_ids=(30,), fuel_ready=False, ammo_ready=True
        )
        result = select_next_expedition_fleet(
            (ready(1), unready), now=NOW, preferred_fleet_ids=(3,)
        )
        self.assertEqual(result.selected_fleet_id, 1)
        self.assertEqual(result.evaluated_fleets[1].decision, ExpeditionDecision.NEEDS_RESUPPLY)

    def test_no_ready_fleet_returns_no_selection(self):
        result = select_next_expedition_fleet(
            (ExpeditionFleetSnapshot(fleet_id=1, ship_ids=(10,)),), now=NOW
        )
        self.assertIsNone(result.selected_fleet_id)

    def test_duplicate_fleet_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "IDs must be unique"):
            select_next_expedition_fleet((ready(1), ready(1)), now=NOW)

    def test_duplicate_preferences_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "preferred fleet IDs must be unique"):
            select_next_expedition_fleet(
                (ready(1),), now=NOW, preferred_fleet_ids=(1, 1)
            )


if __name__ == "__main__":
    unittest.main()
