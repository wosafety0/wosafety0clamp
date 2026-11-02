import unittest
from datetime import datetime, timedelta, timezone

from kancollebot.adapters.poi.full_supply import (
    FullSupplyPreflightError,
    build_full_supply_plan,
    verify_full_supply_complete,
    verify_fleet_supply_complete,
)


NOW = datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc)


def sample():
    fleets = [
        {"api_id": 1, "api_mission": [0, 0, 0, 0], "api_ship": [11, -1]},
        {"api_id": 2, "api_mission": [0, 0, 0, 0], "api_ship": [12, 13]},
        {"api_id": 3, "api_mission": [0, 0, 0, 0], "api_ship": [14]},
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


class FullSupplyPreflightTests(unittest.TestCase):
    def plan(self, fleets=None, ships=None, masters=None, resources=None, **kwargs):
        base = sample()
        values = [fleets, ships, masters, resources]
        defaults = list(base)
        values = [value if value is not None else defaults[index] for index, value in enumerate(values)]
        return build_full_supply_plan(
            *values,
            source_freshness_verified=kwargs.pop("source_freshness_verified", True),
            observed_at=kwargs.pop("observed_at", NOW),
            now=kwargs.pop("now", NOW),
            **kwargs,
        )

    def test_calculates_complete_all_fleet_cost(self):
        plan = self.plan()
        self.assertEqual(plan.fleet_ids, (1, 2, 3))
        self.assertEqual(plan.ship_count, 4)
        self.assertEqual(plan.fuel_required, 10)
        self.assertEqual(plan.ammo_required, 10)
        self.assertEqual([item.fleet_id for item in plan.fleet_costs], [1, 2, 3])

    def test_rejects_unverified_stale_or_future_witness(self):
        with self.assertRaisesRegex(FullSupplyPreflightError, "not verified"):
            self.plan(source_freshness_verified=False)
        with self.assertRaisesRegex(FullSupplyPreflightError, "stale"):
            self.plan(observed_at=NOW - timedelta(seconds=31))
        with self.assertRaisesRegex(FullSupplyPreflightError, "stale"):
            self.plan(observed_at=NOW + timedelta(seconds=1))

    def test_rejects_unknown_or_changed_fleet_set(self):
        fleets, ships, masters, resources = sample()
        with self.assertRaisesRegex(FullSupplyPreflightError, "fleet set"):
            self.plan(fleets=fleets[:2])
        with self.assertRaisesRegex(FullSupplyPreflightError, "fleet set"):
            self.plan(expected_fleet_ids=(1, 2, 3, 4))

    def test_rejects_non_idle_fleet(self):
        fleets, ships, masters, resources = sample()
        fleets[1]["api_mission"] = [1, 5, 123456789, 0]
        with self.assertRaisesRegex(FullSupplyPreflightError, "not idle"):
            self.plan(fleets=fleets)

    def test_rejects_missing_ship_or_master(self):
        fleets, ships, masters, resources = sample()
        del ships["14"]
        with self.assertRaisesRegex(FullSupplyPreflightError, "missing from"):
            self.plan(ships=ships)
        fleets, ships, masters, resources = sample()
        del masters["104"]
        with self.assertRaisesRegex(FullSupplyPreflightError, "master data"):
            self.plan(masters=masters)

    def test_rejects_duplicate_ship_and_insufficient_resources(self):
        fleets, ships, masters, resources = sample()
        fleets[0]["api_ship"] = [11, 12]
        with self.assertRaisesRegex(FullSupplyPreflightError, "assigned more than once"):
            self.plan(fleets=fleets)
        resources[0] = 9
        with self.assertRaisesRegex(FullSupplyPreflightError, "insufficient"):
            self.plan(resources=resources)

    def test_per_fleet_verification_does_not_claim_other_fleets_are_supplied(self):
        fleets, ships, masters, resources = sample()
        ship = ships["11"]
        master = masters[str(ship["api_ship_id"])]
        ship["api_fuel"] = master["api_fuel_max"]
        ship["api_bull"] = master["api_bull_max"]
        self.assertTrue(verify_fleet_supply_complete(1, fleets, ships, masters))
        self.assertFalse(verify_fleet_supply_complete(2, fleets, ships, masters))
        self.assertFalse(verify_fleet_supply_complete(3, fleets, ships, masters))

    def test_post_action_verification_requires_every_ship_full(self):
        plan = self.plan()
        fleets, ships, masters, resources = sample()
        self.assertFalse(verify_full_supply_complete(plan, fleets, ships, masters))
        for ship in ships.values():
            master = masters[str(ship["api_ship_id"])]
            ship["api_fuel"] = master["api_fuel_max"]
            ship["api_bull"] = master["api_bull_max"]
        self.assertTrue(verify_full_supply_complete(plan, fleets, ships, masters))


if __name__ == "__main__":
    unittest.main()
