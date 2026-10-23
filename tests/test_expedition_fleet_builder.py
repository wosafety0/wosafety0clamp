"""Offline tests for exact-type expedition fleet composition."""
import unittest

from kancollebot.core.expedition_fleet_builder import CandidateShip, build_expedition_fleet


def ship(number, kind, level, **kwargs):
    return CandidateShip(number, kind, level, kwargs.get("locked", True),
                         kwargs.get("heavily_damaged", False),
                         kwargs.get("repairing", False))


class FleetBuilderTests(unittest.TestCase):
    def test_selects_exact_types_and_level(self):
        result = build_expedition_fleet(
            [ship(1, "DD", 3), ship(2, "DD", 20), ship(3, "CL", 15)],
            {"DD": 1, "CL": 1}, min_flagship_level=10, min_total_level=25)
        self.assertTrue(result.eligible)
        self.assertEqual(set(result.ship_ids), {2, 3})

    def test_excludes_reserved_damaged_unlocked_and_repairing(self):
        ships = [ship(1, "DD", 80), ship(2, "DD", 70, heavily_damaged=True),
                 ship(3, "DD", 60, repairing=True),
                 ship(4, "DD", 50, locked=False), ship(5, "DD", 10)]
        result = build_expedition_fleet(ships, {"DD": 1}, reserved_ship_ids=[1])
        self.assertEqual(result.ship_ids, (5,))

    def test_missing_type_fails_closed(self):
        self.assertFalse(build_expedition_fleet([ship(1, "DD", 99)], {"CL": 1}).eligible)

    def test_total_level_fails_closed(self):
        self.assertFalse(build_expedition_fleet([ship(1, "DD", 2)], {"DD": 1},
                                                min_total_level=10).eligible)

    def test_duplicate_ids_fail_closed(self):
        self.assertFalse(build_expedition_fleet([ship(1, "DD", 5), ship(1, "CL", 5)],
                                                {"DD": 1}).eligible)

    def test_invalid_constraints_fail_closed(self):
        self.assertFalse(build_expedition_fleet([ship(1, "DD", 5)], {"DD": True}).eligible)
        self.assertFalse(build_expedition_fleet([ship(1, "DD", 5)], {"DD": 7}).eligible)

    def test_repeatable_and_prefers_lower_total_level(self):
        ships = [ship(3, "DD", 30), ship(1, "DD", 5), ship(2, "DD", 8)]
        a = build_expedition_fleet(ships, {"DD": 2}, min_flagship_level=5)
        b = build_expedition_fleet(list(reversed(ships)), {"DD": 2}, min_flagship_level=5)
        self.assertEqual(a.ship_ids, b.ship_ids)
        self.assertEqual(set(a.ship_ids), {1, 2})


if __name__ == "__main__":
    unittest.main()
