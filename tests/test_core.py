import unittest

from kancollebot.core.models import FleetState, ShipState
from kancollebot.safety.policy import SafetyPolicy


class ShipStateTests(unittest.TestCase):
    def test_accepts_valid_ship(self):
        ship = ShipState(ship_id=1, name="Test Ship", hp=10, max_hp=15)
        self.assertEqual(ship.hp, 10)

    def test_rejects_hp_above_maximum(self):
        with self.assertRaises(ValueError):
            ShipState(ship_id=1, name="Test Ship", hp=16, max_hp=15)


class FleetStateTests(unittest.TestCase):
    def test_accepts_valid_fleet(self):
        fleet = FleetState(fleet_id=1, ship_ids=(10, 11))
        self.assertEqual(len(fleet.ship_ids), 2)

    def test_rejects_duplicate_ship_ids(self):
        with self.assertRaises(ValueError):
            FleetState(fleet_id=1, ship_ids=(10, 10))

    def test_rejects_more_than_six_ships(self):
        with self.assertRaises(ValueError):
            FleetState(fleet_id=1, ship_ids=(1, 2, 3, 4, 5, 6, 7))


class SafetyPolicyTests(unittest.TestCase):
    def test_live_actions_are_disabled_by_default(self):
        with self.assertRaises(PermissionError):
            SafetyPolicy().authorize_live_action(confirmed=True)

    def test_enabled_live_action_requires_confirmation(self):
        policy = SafetyPolicy(allow_live_actions=True)
        with self.assertRaises(PermissionError):
            policy.authorize_live_action()
        policy.authorize_live_action(confirmed=True)


if __name__ == "__main__":
    unittest.main()
