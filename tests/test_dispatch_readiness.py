import unittest

from kancollebot.core.fleet_parser import parse_fleet_payload
from kancollebot.core.models import ShipState
from kancollebot.safety.dispatch import evaluate_dispatch_readiness


class DispatchReadinessTests(unittest.TestCase):
    def setUp(self):
        self.fleet = parse_fleet_payload([
            {"api_id": 2, "api_name": "第2艦隊", "api_ship": [73, 70, -1, -1, -1, -1],
             "api_mission": [0, 0, 0, 0]},
        ])[0]
        self.ships = {
            73: ShipState(ship_id=73, name="赤城", hp=50, max_hp=69),
            70: ShipState(ship_id=70, name="加賀", hp=60, max_hp=79),
        }

    def test_defaults_fail_closed_for_unverified_gates(self):
        result = evaluate_dispatch_readiness(self.fleet, self.ships)
        self.assertFalse(result.ready)
        self.assertEqual(len(result.reasons), 4)
        self.assertTrue(any("supply" in reason for reason in result.reasons))
        self.assertTrue(any("repair/docking" in reason for reason in result.reasons))
        self.assertTrue(any("mission fleet requirements" in reason for reason in result.reasons))
        self.assertTrue(any("fleet rotation" in reason for reason in result.reasons))

    def test_all_verified_gates_and_healthy_ships_pass(self):
        result = evaluate_dispatch_readiness(
            self.fleet,
            self.ships,
            supply_verified=True,
            repair_status_verified=True,
            mission_composition_verified=True,
            rotation_verified=True,
        )
        self.assertTrue(result.ready)
        self.assertEqual(result.reasons, ())

    def test_truthy_non_boolean_flags_do_not_bypass_gates(self):
        result = evaluate_dispatch_readiness(
            self.fleet,
            self.ships,
            supply_verified="yes",
            repair_status_verified=1,
            mission_composition_verified=True,
            rotation_verified=True,
        )
        self.assertFalse(result.ready)
        self.assertTrue(any("supply state" in reason and "boolean True" in reason
                            for reason in result.reasons))
        self.assertTrue(any("repair/docking" in reason and "boolean True" in reason
                            for reason in result.reasons))

    def test_false_string_is_not_treated_as_passed_evidence(self):
        result = evaluate_dispatch_readiness(
            self.fleet,
            self.ships,
            supply_verified="false",
            repair_status_verified=True,
            mission_composition_verified=True,
            rotation_verified=True,
        )
        self.assertFalse(result.ready)
        self.assertTrue(any("supply state" in reason for reason in result.reasons))

    def test_invalid_hp_ratio_rejected(self):
        for ratio in (True, float("nan"), float("inf"), 0, 1.1, "0.25"):
            with self.subTest(ratio=ratio), self.assertRaises(ValueError):
                evaluate_dispatch_readiness(
                    self.fleet, self.ships, critical_hp_ratio=ratio
                )

    def test_critical_hp_still_blocks_when_other_gates_pass(self):
        ships = dict(self.ships)
        ships[73] = ShipState(ship_id=73, name="赤城", hp=17, max_hp=69)
        result = evaluate_dispatch_readiness(
            self.fleet,
            ships,
            supply_verified=True,
            repair_status_verified=True,
            mission_composition_verified=True,
            rotation_verified=True,
        )
        self.assertFalse(result.ready)
        self.assertTrue(any("critical HP" in reason for reason in result.reasons))

    def test_missing_ship_state_blocks_even_when_other_gates_pass(self):
        result = evaluate_dispatch_readiness(
            self.fleet,
            {},
            supply_verified=True,
            repair_status_verified=True,
            mission_composition_verified=True,
            rotation_verified=True,
        )
        self.assertFalse(result.ready)
        self.assertTrue(any("no validated state" in reason for reason in result.reasons))


if __name__ == "__main__":
    unittest.main()
