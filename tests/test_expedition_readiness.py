"""Offline tests for fail-closed expedition readiness assessment."""

import json
import unittest
from pathlib import Path

from kancollebot.core.expedition_equipment_planner import EquipmentInstance, EquipmentSlotRequirement
from kancollebot.core.expedition_fleet_builder import CandidateShip
from kancollebot.core.expedition_readiness import assess_expedition_readiness

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "data" / "expedition" / "expedition_rules_kca_reference.json"


class ExpeditionReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = json.loads(RULES.read_text(encoding="utf-8"))

    def sample_ships(self):
        return [
            CandidateShip(10, "DD", 20, True, False, False),
            CandidateShip(11, "DD", 15, True, False, False),
            CandidateShip(12, "CL", 18, True, False, False),
            CandidateShip(13, "CA", 25, True, False, False),
        ]

    def test_reference_rule_produces_candidate_but_never_dispatch_authorization(self):
        result = assess_expedition_readiness(
            self.rules, 1, self.sample_ships(), [], live_state_fresh=True,
            morale_verified=True, special_requirements_verified=True,
        )
        self.assertTrue(result.candidate_ship_ids)
        self.assertFalse(result.dispatch_authorized)
        self.assertFalse(result.ready_for_review)
        self.assertTrue(any(gate.passed is not True for gate in result.gates))

    def test_unknown_mission_id_fails_closed(self):
        result = assess_expedition_readiness(self.rules, 9999, self.sample_ships(), [])
        self.assertFalse(result.candidate_ship_ids)
        self.assertFalse(result.ready_for_review)
        self.assertFalse(result.dispatch_authorized)

    def test_equipment_requirement_for_non_candidate_ship_fails(self):
        result = assess_expedition_readiness(
            self.rules, 1, self.sample_ships(), [EquipmentInstance(100, 44, 0)],
            [EquipmentSlotRequirement(999, 0, 44)],
        )
        self.assertFalse(result.ready_for_review)
        self.assertFalse(result.dispatch_authorized)
        self.assertTrue(any(g.name == "equipment_targets" and g.passed is False for g in result.gates))

    def test_invalid_rule_document_fails_closed(self):
        result = assess_expedition_readiness({}, 1, self.sample_ships(), [])
        self.assertFalse(result.ready_for_review)
        self.assertFalse(result.dispatch_authorized)
        self.assertFalse(result.candidate_ship_ids)


if __name__ == "__main__":
    unittest.main()
