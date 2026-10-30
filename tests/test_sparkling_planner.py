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

    def test_rejects_boolean_and_non_integer_ship_ids(self):
        for ship_id in (True, False, 1.0, 1.5, "1"):
            with self.subTest(ship_id=ship_id):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    SparkleShipSnapshot(ship_id=ship_id, morale=30)

    def test_rejects_boolean_and_non_integer_morale(self):
        for morale in (True, False, 1.0, 1.5, "20"):
            with self.subTest(morale=morale):
                with self.assertRaisesRegex(ValueError, "integer between 0 and 100"):
                    SparkleShipSnapshot(ship_id=1, morale=morale)

    def test_eligible_flag_must_be_boolean(self):
        for eligible in (0, 1, "false", None):
            with self.subTest(eligible=eligible):
                with self.assertRaisesRegex(ValueError, "eligible must be a boolean"):
                    SparkleShipSnapshot(ship_id=1, morale=30, eligible=eligible)


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

    def test_policy_values_must_be_integers_not_booleans(self):
        for value in (True, False, 50.0, "50"):
            with self.subTest(target_morale=value):
                with self.assertRaisesRegex(ValueError, "target_morale"):
                    build_sparkle_plan((), target_morale=value)
        for value in (True, False, 10.0, "10"):
            with self.subTest(estimated_gain_per_sortie=value):
                with self.assertRaisesRegex(ValueError, "estimated_gain_per_sortie"):
                    build_sparkle_plan((), estimated_gain_per_sortie=value)
        for value in (True, False, 3.0, "3"):
            with self.subTest(max_sorties_per_ship=value):
                with self.assertRaisesRegex(ValueError, "max_sorties_per_ship"):
                    build_sparkle_plan((), max_sorties_per_ship=value)


if __name__ == "__main__":
    unittest.main()
