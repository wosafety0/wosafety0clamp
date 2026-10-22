import unittest

from kancollebot.core.fleet_parser import parse_fleet_payload
from kancollebot.core.models import ShipState
from kancollebot.safety.fleet import evaluate_fleet_safety


class FleetSafetyTests(unittest.TestCase):
    def setUp(self):
        self.fleet = parse_fleet_payload([
            {"api_id": 2, "api_name": "第2艦隊", "api_ship": [10, 11, -1, -1, -1, -1],
             "api_mission": [0, 0, 0, 0]}
        ])[0]

    def test_healthy_fleet_passes_hp_checks(self):
        ships = {
            10: ShipState(10, "Test A", 30, 30),
            11: ShipState(11, "Test B", 10, 20),
        }
        result = evaluate_fleet_safety(self.fleet, ships)
        self.assertTrue(result.safe)
        self.assertEqual(result.reasons, ())

    def test_missing_ship_state_blocks(self):
        result = evaluate_fleet_safety(self.fleet, {10: ShipState(10, "Test A", 30, 30)})
        self.assertFalse(result.safe)
        self.assertIn("ship 11 has no validated state", result.reasons)

    def test_critical_hp_blocks(self):
        ships = {
            10: ShipState(10, "Test A", 7, 30),
            11: ShipState(11, "Test B", 20, 20),
        }
        result = evaluate_fleet_safety(self.fleet, ships)
        self.assertFalse(result.safe)
        self.assertTrue(any("critical HP threshold" in reason for reason in result.reasons))

    def test_zero_hp_blocks(self):
        ships = {
            10: ShipState(10, "Test A", 0, 30),
            11: ShipState(11, "Test B", 20, 20),
        }
        result = evaluate_fleet_safety(self.fleet, ships)
        self.assertFalse(result.safe)
        self.assertTrue(any("zero HP" in reason for reason in result.reasons))

    def test_empty_fleet_blocks(self):
        fleet = parse_fleet_payload([
            {"api_id": 2, "api_ship": [-1, -1, -1, -1, -1, -1], "api_mission": [0]}
        ])[0]
        result = evaluate_fleet_safety(fleet, {})
        self.assertFalse(result.safe)
        self.assertIn("fleet is empty", result.reasons)

    def test_invalid_threshold_rejected(self):
        for ratio in (True, float("nan"), float("inf"), 0, 1.1, "0.25"):
            with self.subTest(ratio=ratio), self.assertRaises(ValueError):
                evaluate_fleet_safety(self.fleet, {}, critical_hp_ratio=ratio)


if __name__ == "__main__":
    unittest.main()
