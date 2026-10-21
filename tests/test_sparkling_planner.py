"""Offline tests for the sparkle-farming planner."""

import unittest

from kancollebot.sparkling.planner import (
    SparkleDecision,
    SparkleShipSnapshot,
    build_sparkle_plan,
)


class SparkleShipSnapshotTests(unittest.TestCase):
    def test_rejects_invalid_morale(self):
        with self.assertRaisesRegex(ValueError, "between 0 and 100"):
            SparkleShipSnapshot(ship_id=1, morale=101)

    def test_rejects_non_positive_ship_id(self):
        with self.assertRaisesRegex(ValueError, "positive"):
            SparkleShipSnapshot(ship_id=0, morale=30)


class SparklePlannerTests(unittest.TestCase):
    def test_orders_lowest_morale_first(self):
        plan = build_sparkle_plan((
            SparkleShipSnapshot(1, 40),
            SparkleShipSnapshot(2, 20),
            SparkleShipSnapshot(3, 35),
        ))
        self.assertEqual(plan.pending_ship_ids, (2, 3, 1))

    def test_target_reached_requires_no_sorties(self):
        plan = build_sparkle_plan((SparkleShipSnapshot(1, 50),))
        self.assertEqual(plan.ships[0].decision, SparkleDecision.TARGET_REACHED)
        self.assertEqual(plan.ships[0].estimated_sorties, 0)

    def test_unknown_morale_is_not_treated_as_zero(self):
        plan = build_sparkle_plan((SparkleShipSnapshot(1, None),))
        self.assertEqual(
            plan.ships[0].decision, SparkleDecision.BLOCKED_UNKNOWN_MORALE
        )
        self.assertIsNone(plan.ships[0].estimated_sorties)

    def test_ineligible_ship_is_blocked(self):
        plan = build_sparkle_plan((SparkleShipSnapshot(1, 20, eligible=False),))
        self.assertEqual(plan.ships[0].decision, SparkleDecision.BLOCKED_INELIGIBLE)

    def test_sortie_estimate_uses_caller_supplied_gain(self):
        plan = build_sparkle_plan(
            (SparkleShipSnapshot(1, 30),),
            target_morale=50,
            estimated_gain_per_sortie=5,
        )
        self.assertEqual(plan.ships[0].estimated_sorties, 3)

    def test_excessive_estimate_is_capped_to_bounded_batch(self):
        plan = build_sparkle_plan(
            (SparkleShipSnapshot(1, 10),),
            target_morale=50,
            estimated_gain_per_sortie=5,
            max_sorties_per_ship=3,
        )
        self.assertEqual(plan.ships[0].estimated_sorties, 3)
        self.assertIn("exceed", plan.ships[0].reason)

    def test_duplicate_ship_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            build_sparkle_plan((
                SparkleShipSnapshot(1, 20),
                SparkleShipSnapshot(1, 30),
            ))

    def test_rejects_invalid_policy_values(self):
        with self.assertRaisesRegex(ValueError, "target_morale"):
            build_sparkle_plan((), target_morale=0)
        with self.assertRaisesRegex(ValueError, "estimated_gain_per_sortie"):
            build_sparkle_plan((), estimated_gain_per_sortie=0)
        with self.assertRaisesRegex(ValueError, "max_sorties_per_ship"):
            build_sparkle_plan((), max_sorties_per_ship=0)


if __name__ == "__main__":
    unittest.main()
