import unittest

from kancollebot.expeditions.fleet_composition_planner import (
    FlagshipExchangeDecision,
    plan_flagship_exchange,
)
from kancollebot.expeditions.mission_eligibility import FleetCompositionSnapshot, MissionCompositionRequirement


class FlagshipExchangePlannerTests(unittest.TestCase):
    def setUp(self):
        self.fleet_2_req = MissionCompositionRequirement(1, 2, 1)
        self.fleet_3_req = MissionCompositionRequirement(3, 3, 3)
        self.fleet_2 = FleetCompositionSnapshot((1, 4, 8, 25), (8, 4, 4, 4), (2, 2, 2, 2))
        self.fleet_3 = FleetCompositionSnapshot((6, 52, 32, 63), (2, 1, 1, 1), (2, 2, 2, 2))

    def test_suggests_lowest_level_non_flagship_and_preserves_both_requirements(self):
        plan = plan_flagship_exchange(self.fleet_2, self.fleet_3, self.fleet_2_req, self.fleet_3_req)
        self.assertEqual(plan.decision, FlagshipExchangeDecision.EXCHANGE_AVAILABLE)
        self.assertEqual(plan.donor_ship_id, 4)
        self.assertEqual(plan.receiver_flagship_id, 6)
        self.assertEqual(plan.donor_after.ship_levels, (8, 2, 4, 4))
        self.assertEqual(plan.receiver_after.ship_levels, (4, 1, 1, 1))

    def test_does_not_exchange_current_donor_flagship(self):
        donor = FleetCompositionSnapshot((1, 4), (8, 2), (2, 2))
        receiver = FleetCompositionSnapshot((6, 52, 32), (2, 1, 1), (2, 2, 2))
        plan = plan_flagship_exchange(donor, receiver, self.fleet_2_req, self.fleet_3_req)
        self.assertEqual(plan.decision, FlagshipExchangeDecision.NO_QUALIFYING_DONOR)

    def test_no_exchange_when_receiver_already_eligible(self):
        receiver = FleetCompositionSnapshot((40, 52, 32), (4, 1, 1), (2, 2, 2))
        plan = plan_flagship_exchange(self.fleet_2, receiver, self.fleet_2_req, self.fleet_3_req)
        self.assertEqual(plan.decision, FlagshipExchangeDecision.NO_EXCHANGE_NEEDED)

    def test_rejects_donor_that_would_not_meet_its_own_mission(self):
        donor = FleetCompositionSnapshot((1,), (8,), (2,))
        plan = plan_flagship_exchange(donor, self.fleet_3, self.fleet_2_req, self.fleet_3_req)
        self.assertEqual(plan.decision, FlagshipExchangeDecision.BLOCKED_DONOR_COMPOSITION)

    def test_rejects_receiver_blocked_by_ship_count_not_flagship(self):
        receiver = FleetCompositionSnapshot((6, 52), (2, 1), (2, 2))
        plan = plan_flagship_exchange(self.fleet_2, receiver, self.fleet_2_req, self.fleet_3_req)
        self.assertEqual(plan.decision, FlagshipExchangeDecision.BLOCKED_RECEIVER_COMPOSITION)

    def test_reports_no_candidate_when_donor_non_flagships_are_too_low(self):
        donor = FleetCompositionSnapshot((1, 4, 8), (8, 2, 1), (2, 2, 2))
        plan = plan_flagship_exchange(donor, self.fleet_3, self.fleet_2_req, self.fleet_3_req)
        self.assertEqual(plan.decision, FlagshipExchangeDecision.NO_QUALIFYING_DONOR)

    def test_rejects_overlapping_fleet_membership(self):
        receiver = FleetCompositionSnapshot((4, 52, 32), (2, 1, 1), (2, 2, 2))
        plan = plan_flagship_exchange(self.fleet_2, receiver, self.fleet_2_req, self.fleet_3_req)
        self.assertEqual(plan.decision, FlagshipExchangeDecision.BLOCKED_OVERLAPPING_FLEETS)


if __name__ == "__main__":
    unittest.main()
