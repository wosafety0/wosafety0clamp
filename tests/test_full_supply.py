import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tools.full_supply_ui_action import LoopError, _is_supply_page
from kancollebot.adapters.poi.ui_state_gate import UiState

from kancollebot.adapters.poi.full_supply import (
    FullSupplyPreflightError,
    build_full_supply_plan,
    select_full_supply_targets,
    validate_full_supply_progress,
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


    def test_selects_all_supported_fleets_with_deficits_in_one_batch(self):
        plan = self.plan()
        targets = select_full_supply_targets(plan)
        self.assertEqual([item.fleet_id for item in targets], [2, 3])
        self.assertEqual(sum(item.fuel_required for item in targets), 10)
        self.assertEqual(sum(item.ammo_required for item in targets), 10)

    def test_batch_progress_accepts_sequential_fleet_wide_supply(self):
        initial = self.plan()
        fleets, ships, masters, resources = sample()
        for ship_id in (12, 13):
            ship = ships[str(ship_id)]
            master = masters[str(ship["api_ship_id"])]
            ship["api_fuel"] = master["api_fuel_max"]
            ship["api_bull"] = master["api_bull_max"]
        resources[0] -= 7
        resources[1] -= 4
        after_fleet_two = self.plan(fleets=fleets, ships=ships, masters=masters, resources=resources)
        remaining = validate_full_supply_progress(initial, after_fleet_two, (2,))
        self.assertEqual([item.fleet_id for item in remaining], [3])
        self.assertEqual(remaining[0].fuel_required, 3)
        self.assertEqual(remaining[0].ammo_required, 6)

        ship = ships["14"]
        master = masters[str(ship["api_ship_id"])]
        ship["api_fuel"] = master["api_fuel_max"]
        ship["api_bull"] = master["api_bull_max"]
        resources[0] -= 3
        resources[1] -= 6
        after_all = self.plan(fleets=fleets, ships=ships, masters=masters, resources=resources)
        self.assertEqual(validate_full_supply_progress(initial, after_all, (2, 3)), ())

    def test_batch_progress_rejects_unexpected_material_delta(self):
        initial = self.plan()
        fleets, ships, masters, resources = sample()
        for ship_id in (12, 13):
            ship = ships[str(ship_id)]
            master = masters[str(ship["api_ship_id"])]
            ship["api_fuel"] = master["api_fuel_max"]
            ship["api_bull"] = master["api_bull_max"]
        resources[0] -= 8
        resources[1] -= 4
        current = self.plan(fleets=fleets, ships=ships, masters=masters, resources=resources)
        with self.assertRaisesRegex(FullSupplyPreflightError, "inventory delta"):
            validate_full_supply_progress(initial, current, (2,))

    def test_batch_preflight_rejects_unsupported_fleet_before_any_action(self):
        fleets, ships, masters, resources = sample()
        ships["11"]["api_fuel"] -= 1
        plan = self.plan(fleets=fleets, ships=ships, masters=masters, resources=resources)
        with self.assertRaisesRegex(FullSupplyPreflightError, "lack independently verified UI selection gates: 1"):
            select_full_supply_targets(plan)

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


class SupplyPageDetectionTests(unittest.TestCase):
    def test_home_port_fast_path_skips_supply_tab_scans(self):
        session = object()
        with patch("tools.full_supply_ui_action._require_ui_state") as require_state, patch(
            "tools.full_supply_ui_action._require_supply_page"
        ) as require_supply_page:
            self.assertFalse(_is_supply_page(session))
        require_state.assert_called_once_with(session, UiState.HOME_PORT)
        require_supply_page.assert_not_called()

    def test_supply_page_is_checked_only_after_home_port_rejection(self):
        session = object()
        with patch("tools.full_supply_ui_action._require_ui_state", side_effect=LoopError("not home")) as require_state, patch(
            "tools.full_supply_ui_action._require_supply_page", return_value=UiState.SUPPLY_MENU
        ) as require_supply_page:
            self.assertTrue(_is_supply_page(session))
        require_state.assert_called_once_with(session, UiState.HOME_PORT)
        require_supply_page.assert_called_once_with(session)


if __name__ == "__main__":
    unittest.main()
