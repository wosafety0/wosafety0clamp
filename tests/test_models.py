from datetime import datetime, timezone
import unittest

from kancollebot.models import FleetState, GameSnapshot, ShipState


class ShipStateTests(unittest.TestCase):
    def test_accepts_valid_ship(self):
        ship = ShipState(1, "吹雪", 15, 15, condition=49, fuel=15, ammo=20)
        self.assertEqual(ship.ship_id, 1)

    def test_rejects_hp_above_maximum(self):
        with self.assertRaisesRegex(ValueError, "hp must be"):
            ShipState(1, "吹雪", 16, 15)

    def test_rejects_negative_fuel(self):
        with self.assertRaisesRegex(ValueError, "fuel must"):
            ShipState(1, "吹雪", 15, 15, fuel=-1)


class FleetStateTests(unittest.TestCase):
    def test_rejects_duplicate_ship_ids(self):
        with self.assertRaisesRegex(ValueError, "duplicates"):
            FleetState(1, "第1艦隊", (1, 1))

    def test_accepts_empty_fleet(self):
        fleet = FleetState(1, "第1艦隊", ())
        self.assertEqual(fleet.ship_ids, ())


class GameSnapshotTests(unittest.TestCase):
    def test_requires_timezone_aware_timestamp(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            GameSnapshot(datetime(2026, 10, 23), (), ())

    def test_rejects_duplicate_ship_ids(self):
        ship = ShipState(1, "吹雪", 15, 15)
        with self.assertRaisesRegex(ValueError, "duplicate ship IDs"):
            GameSnapshot(datetime.now(timezone.utc), (ship, ship), ())

    def test_is_read_only_immutable_data(self):
        snapshot = GameSnapshot(
            datetime.now(timezone.utc),
            (ShipState(1, "吹雪", 15, 15),),
            (FleetState(1, "第1艦隊", (1,)),),
        )
        with self.assertRaises(AttributeError):
            snapshot.source = "mutated"


if __name__ == "__main__":
    unittest.main()
