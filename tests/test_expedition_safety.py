import unittest

from kancollebot.core.expedition_planner import ExpeditionDecision
from kancollebot.core.expedition_safety import (
    gate_expedition_decision,
    plan_safe_expedition,
)
from kancollebot.core.fleet_parser import parse_fleet_payload
from kancollebot.core.models import ShipState


class ExpeditionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.fleets = parse_fleet_payload([
            {"api_id": 2, "api_name": "第2艦隊", "api_ship": [73, 70, -1, -1, -1, -1],
             "api_mission": [0, 0, 0, 0]},
        ])
        self.ships = {
            73: ShipState(ship_id=73, name="赤城", hp=50, max_hp=69),
            70: ShipState(ship_id=70, name="加賀", hp=60, max_hp=79),
        }
        self.dispatch = ExpeditionDecision(
            action="dispatch", fleet_id=2, mission_id=5, reason="candidate"
        )
        self.all_gates = {
            "supply_verified": True,
            "repair_status_verified": True,
            "mission_composition_verified": True,
            "rotation_verified": True,
        }

    def test_dispatch_is_blocked_by_default(self):
        result = gate_expedition_decision(self.dispatch, self.fleets, self.ships)
        self.assertEqual(result.action, "wait")
        self.assertIn("supply state has not been verified", result.reason)

    def test_dispatch_passes_only_when_every_gate_passes(self):
        result = gate_expedition_decision(
            self.dispatch, self.fleets, self.ships, **self.all_gates
        )
        self.assertEqual(result, self.dispatch)

    def test_missing_planned_fleet_is_blocked(self):
        result = gate_expedition_decision(
            self.dispatch, [], self.ships, **self.all_gates
        )
        self.assertEqual(result.action, "wait")
        self.assertIn("absent from current observations", result.reason)

    def test_unsafe_hp_blocks_even_when_other_gates_pass(self):
        ships = dict(self.ships)
        ships[73] = ShipState(ship_id=73, name="赤城", hp=10, max_hp=69)
        result = gate_expedition_decision(
            self.dispatch, self.fleets, ships, **self.all_gates
        )
        self.assertEqual(result.action, "wait")
        self.assertIn("critical HP", result.reason)

    def test_dispatch_is_blocked_if_fleet_is_no_longer_idle(self):
        underway = parse_fleet_payload([
            {"api_id": 2, "api_name": "第2艦隊", "api_ship": [73, 70, -1, -1, -1, -1],
             "api_mission": [1, 5, 123, 0]},
        ])
        result = gate_expedition_decision(
            self.dispatch, underway, self.ships, **self.all_gates
        )
        self.assertEqual(result.action, "wait")
        self.assertIn("not observed idle", result.reason)

    def test_dispatch_is_blocked_if_mission_state_is_unknown(self):
        unknown = parse_fleet_payload([
            {"api_id": 2, "api_name": "第2艦隊", "api_ship": [73, 70, -1, -1, -1, -1]},
        ])
        result = gate_expedition_decision(
            self.dispatch, unknown, self.ships, **self.all_gates
        )
        self.assertEqual(result.action, "wait")
        self.assertIn("not observed idle", result.reason)

    def test_invalid_mission_id_is_blocked(self):
        invalid = ExpeditionDecision(
            action="dispatch", fleet_id=2, mission_id=0, reason="bad candidate"
        )
        result = gate_expedition_decision(
            invalid, self.fleets, self.ships, **self.all_gates
        )
        self.assertEqual(result.action, "wait")
        self.assertIn("mission ID is missing or invalid", result.reason)

    def test_orchestrator_blocks_dispatch_when_gates_are_unverified(self):
        result = plan_safe_expedition(
            self.fleets, self.ships, mission_by_fleet={2: 5}
        )
        self.assertEqual(result.action, "wait")
        self.assertIn("supply state has not been verified", result.reason)

    def test_orchestrator_preserves_completed_expedition_collection_priority(self):
        completed = parse_fleet_payload([
            {"api_id": 2, "api_name": "第2艦隊", "api_ship": [73, 70, -1, -1, -1, -1],
             "api_mission": [2, 5, 0, 0]},
        ])
        result = plan_safe_expedition(
            completed, self.ships, mission_by_fleet={2: 5}
        )
        self.assertEqual(result.action, "collect")
        self.assertEqual(result.fleet_id, 2)

    def test_orchestrator_allows_recommendation_only_when_all_gates_pass(self):
        result = plan_safe_expedition(
            self.fleets, self.ships, mission_by_fleet={2: 5}, **self.all_gates
        )
        self.assertEqual(result.action, "dispatch")

    def test_non_dispatch_recommendations_are_unchanged(self):
        wait = ExpeditionDecision(action="wait", fleet_id=None, mission_id=None, reason="wait")
        collect = ExpeditionDecision(action="collect", fleet_id=2, mission_id=5, reason="complete")
        self.assertEqual(gate_expedition_decision(wait, self.fleets, self.ships), wait)
        self.assertEqual(gate_expedition_decision(collect, self.fleets, self.ships), collect)


if __name__ == "__main__":
    unittest.main()
