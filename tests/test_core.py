import unittest
from datetime import datetime, timedelta, timezone

from kancollebot.core.models import FleetState, ShipState
from kancollebot.core.observed_state import ObservedState
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


class ObservedStateTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 21, 14, 0, tzinfo=timezone.utc)
        self.max_age = timedelta(seconds=30)

    def test_accepts_fresh_timezone_aware_state(self):
        state = ObservedState(
            payload={"fleet_id": 1},
            observed_at=self.now - timedelta(seconds=20),
            source="test-fixture",
            source_freshness_verified=True,
        )
        self.assertTrue(state.is_fresh(now=self.now, max_age=self.max_age))
        state.require_fresh(now=self.now, max_age=self.max_age)

    def test_rejects_stale_state(self):
        state = ObservedState(
            payload={},
            observed_at=self.now - timedelta(seconds=31),
            source="test-fixture",
        )
        self.assertFalse(state.is_fresh(now=self.now, max_age=self.max_age))
        with self.assertRaisesRegex(ValueError, "stale"):
            state.require_fresh(now=self.now, max_age=self.max_age)

    def test_rejects_future_dated_state(self):
        state = ObservedState(
            payload={},
            observed_at=self.now + timedelta(seconds=1),
            source="test-fixture",
        )
        self.assertFalse(state.is_fresh(now=self.now, max_age=self.max_age))

    def test_rejects_naive_observation_time(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            ObservedState(payload={}, observed_at=datetime(2026, 10, 21), source="test")

    def test_rejects_negative_max_age(self):
        state = ObservedState(payload={}, observed_at=self.now, source="test")
        with self.assertRaisesRegex(ValueError, "negative"):
            state.is_fresh(now=self.now, max_age=timedelta(seconds=-1))


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
