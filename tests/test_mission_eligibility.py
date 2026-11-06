import unittest

from kancollebot.expeditions.mission_eligibility import (
    FleetCompositionSnapshot,
    MissionCompositionRequirement,
    MissionEligibilityDecision,
    evaluate_mission_composition,
)


class FleetCompositionSnapshotTests(unittest.TestCase):
    def test_rejects_mismatched_ship_fields(self):
        with self.assertRaisesRegex(ValueError, "matching lengths"):
            FleetCompositionSnapshot((1, 2), (3,), (2, 2))

    def test_rejects_duplicate_ship_ids(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            FleetCompositionSnapshot((1, 1), (3, 4), (2, 2))

    def test_rejects_boolean_ship_level(self):
        with self.assertRaisesRegex(ValueError, "positive integers"):
            FleetCompositionSnapshot((1,), (True,), (2,))

    def test_rejects_non_tuple_observations(self):
        with self.assertRaisesRegex(ValueError, "must be tuples"):
            FleetCompositionSnapshot([1], (3,), (2,))


class MissionEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.mission_1 = MissionCompositionRequirement(1, 2, 1)
        self.mission_3 = MissionCompositionRequirement(3, 3, 3)

    def test_fleet_2_observed_composition_meets_mission_1(self):
        fleet = FleetCompositionSnapshot((1, 4, 8, 25), (8, 4, 4, 4), (2, 2, 2, 2))
        plan = evaluate_mission_composition(fleet, self.mission_1)
        self.assertEqual(plan.decision, MissionEligibilityDecision.ELIGIBLE)

    def test_fleet_3_current_flagship_level_blocks_mission_3(self):
        fleet = FleetCompositionSnapshot((6, 52, 32, 63), (2, 1, 1, 1), (2, 2, 2, 2))
        plan = evaluate_mission_composition(fleet, self.mission_3)
        self.assertEqual(plan.decision, MissionEligibilityDecision.BLOCKED_FLAGSHIP_LEVEL)

    def test_promoting_level_4_ship_to_flagship_meets_mission_3(self):
        fleet = FleetCompositionSnapshot((4, 6, 52, 32), (4, 2, 1, 1), (2, 2, 2, 2))
        plan = evaluate_mission_composition(fleet, self.mission_3)
        self.assertEqual(plan.decision, MissionEligibilityDecision.ELIGIBLE)

    def test_too_few_ships_blocks_mission(self):
        fleet = FleetCompositionSnapshot((1, 4), (8, 4), (2, 2))
        plan = evaluate_mission_composition(fleet, self.mission_3)
        self.assertEqual(plan.decision, MissionEligibilityDecision.BLOCKED_TOO_FEW_SHIPS)

    def test_disallowed_ship_type_blocks_mission(self):
        fleet = FleetCompositionSnapshot((1, 4, 8), (8, 4, 4), (2, 3, 2))
        destroyer_only = MissionCompositionRequirement(3, 3, 3, (2,))
        plan = evaluate_mission_composition(fleet, destroyer_only)
        self.assertEqual(plan.decision, MissionEligibilityDecision.BLOCKED_SHIP_TYPE)

    def test_empty_fleet_is_blocked(self):
        fleet = FleetCompositionSnapshot((), (), ())
        plan = evaluate_mission_composition(fleet, self.mission_1)
        self.assertEqual(plan.decision, MissionEligibilityDecision.BLOCKED_EMPTY_FLEET)


if __name__ == "__main__":
    unittest.main()
