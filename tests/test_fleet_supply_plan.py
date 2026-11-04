"""Tests for the single-fleet supply plan (the narrow contract)."""
import unittest
from datetime import datetime, timedelta, timezone

from kancollebot.adapters.poi.full_supply import (
    FullSupplyPreflightError,
    build_fleet_supply_plan,
)


NOW = datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)


def sample():
    """Fleet 2 is idle with a real deficit; fleet 3 is out on an expedition."""
    fleets = [
        {"api_id": 1, "api_mission": [0, 0, 0, 0], "api_ship": [11, -1]},
        {"api_id": 2, "api_mission": [0, 0, 0, 0], "api_ship": [12, 13]},
        {"api_id": 3, "api_mission": [1, 3, 0, 0], "api_ship": [14]},
    ]
    ships = {
        "11": {"api_id": 11, "api_ship_id": 101, "api_fuel": 10, "api_bull": 10},
        "12": {"api_id": 12, "api_ship_id": 102, "api_fuel": 5, "api_bull": 10},
        "13": {"api_id": 13, "api_ship_id": 103, "api_fuel": 8, "api_bull": 6},
        "14": {"api_id": 14, "api_ship_id": 104, "api_fuel": 7, "api_bull": 4},
    }
    masters = {
        "101": {"api_fuel_max": 10, "api_bull_max": 10},
        "102": {"api_fuel_max": 10, "api_bull_max": 10},
        "103": {"api_fuel_max": 10, "api_bull_max": 10},
        "104": {"api_fuel_max": 10, "api_bull_max": 10},
    }
    resources = [100, 100, 0, 0, 0, 0, 0, 0]
    return fleets, ships, masters, resources


def plan(fleet_id=2, **overrides):
    fleets, ships, masters, resources = sample()
    values = {
        "fleets": fleets,
        "ships": ships,
        "master_ships": masters,
        "resources": resources,
        "source_freshness_verified": True,
        "observed_at": NOW,
        "now": NOW,
    }
    values.update(overrides)
    return build_fleet_supply_plan(fleet_id, **values)


class FleetSupplyPlanTests(unittest.TestCase):
    def test_costs_only_the_named_fleet(self):
        result = plan(2)
        self.assertEqual(result.fleet_id, 2)
        self.assertEqual(result.ship_ids, (12, 13))
        self.assertEqual(result.fuel_required, 7)   # (10-5) + (10-8)
        self.assertEqual(result.ammo_required, 4)   # (10-10) + (10-6)
        self.assertTrue(result.needs_supply)

    def test_a_fleet_on_expedition_does_not_block_a_single_fleet_plan(self):
        # Fleet 3 has mission status 1 and would fail the all-fleet plan, yet
        # fleet 2's own plan is unaffected by it.
        result = plan(2)
        self.assertEqual(result.fleet_id, 2)
        self.assertEqual(result.ship_ids, (12, 13))

    def test_the_target_fleet_must_be_idle(self):
        with self.assertRaisesRegex(FullSupplyPreflightError, "not idle"):
            plan(3)

    def test_an_already_full_fleet_needs_nothing(self):
        result = plan(1)  # ship 11 is full
        self.assertEqual(result.fuel_required, 0)
        self.assertEqual(result.ammo_required, 0)
        self.assertFalse(result.needs_supply)

    def test_an_empty_fleet_is_refused(self):
        fleets, ships, masters, resources = sample()
        fleets[1]["api_ship"] = [-1, -1]
        with self.assertRaisesRegex(FullSupplyPreflightError, "is empty"):
            build_fleet_supply_plan(
                2, fleets, ships, masters, resources,
                source_freshness_verified=True, observed_at=NOW, now=NOW,
            )

    def test_insufficient_materials_are_refused(self):
        with self.assertRaisesRegex(FullSupplyPreflightError, "insufficient"):
            plan(2, resources=[3, 100, 0, 0, 0, 0, 0, 0])

    def test_an_unwitnessed_snapshot_is_refused(self):
        with self.assertRaisesRegex(FullSupplyPreflightError, "not verified"):
            plan(2, source_freshness_verified=False)

    def test_a_stale_snapshot_is_refused(self):
        with self.assertRaisesRegex(FullSupplyPreflightError, "stale"):
            plan(2, observed_at=NOW - timedelta(minutes=5))

    def test_a_future_snapshot_is_refused(self):
        with self.assertRaisesRegex(FullSupplyPreflightError, "stale or future"):
            plan(2, observed_at=NOW + timedelta(seconds=5))

    def test_a_ship_missing_from_the_snapshot_is_refused(self):
        fleets, ships, masters, resources = sample()
        del ships["13"]
        with self.assertRaisesRegex(FullSupplyPreflightError, "missing from the live snapshot"):
            build_fleet_supply_plan(
                2, fleets, ships, masters, resources,
                source_freshness_verified=True, observed_at=NOW, now=NOW,
            )

    def test_a_missing_fleet_is_refused(self):
        with self.assertRaisesRegex(FullSupplyPreflightError, "missing or duplicated"):
            plan(9)

    def test_an_invalid_fleet_id_is_a_value_error(self):
        with self.assertRaises(ValueError):
            plan(0)


if __name__ == "__main__":
    unittest.main()
