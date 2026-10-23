"""Offline tests for fail-closed expedition readiness assessment."""

import json
import unittest
from pathlib import Path

from kancollebot.core.expedition_equipment_planner import EquipmentInstance, EquipmentSlotRequirement
from kancollebot.core.expedition_fleet_builder import CandidateShip
from kancollebot.core.expedition_readiness import ShipSupplyObservation, assess_expedition_readiness

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


    def test_drum_mission_stays_blocked_without_known_drum_model(self):
        ships = [
            CandidateShip(20, "CL", 20, True, False, False),
            CandidateShip(21, "DD", 20, True, False, False),
            CandidateShip(22, "DD", 20, True, False, False),
            CandidateShip(23, "DD", 20, True, False, False),
            CandidateShip(24, "DD", 20, True, False, False),
        ]
        result = assess_expedition_readiness(
            self.rules, 21, ships, [], live_state_fresh=True,
            morale_verified=True, special_requirements_verified=True,
        )
        drum_gate = next(g for g in result.gates if g.name == "drum_requirements")
        self.assertFalse(drum_gate.passed)
        self.assertFalse(result.ready_for_review)
        self.assertFalse(result.dispatch_authorized)

    def test_drum_slots_do_not_prove_carrier_compatibility(self):
        ships = [
            CandidateShip(20, "CL", 20, True, False, False),
            CandidateShip(21, "DD", 20, True, False, False),
            CandidateShip(22, "DD", 20, True, False, False),
            CandidateShip(23, "DD", 20, True, False, False),
            CandidateShip(24, "DD", 20, True, False, False),
        ]
        slots = [
            EquipmentSlotRequirement(21, 0, 44),
            EquipmentSlotRequirement(22, 0, 44),
            EquipmentSlotRequirement(23, 0, 44),
            EquipmentSlotRequirement(24, 0, 44),
        ]
        result = assess_expedition_readiness(
            self.rules, 21, ships,
            [EquipmentInstance(100 + i, 44, 0) for i in range(4)],
            slots, drum_model_id=44, live_state_fresh=True,
            morale_verified=True, special_requirements_verified=True,
        )
        drum_gate = next(g for g in result.gates if g.name == "drum_requirements")
        self.assertFalse(drum_gate.passed)
        self.assertFalse(result.dispatch_authorized)

    def test_full_supply_is_verified_for_planned_ships(self):
        supply = [ShipSupplyObservation(i, 15, 15, 20, 20) for i in (10, 11, 12, 13)]
        result = assess_expedition_readiness(
            self.rules, 1, self.sample_ships(), [], supply_observations=supply,
        )
        gate = next(item for item in result.gates if item.name == "ship_supply")
        self.assertTrue(gate.passed, gate.detail)
        self.assertFalse(result.dispatch_authorized)

    def test_missing_supply_observation_blocks_readiness(self):
        result = assess_expedition_readiness(
            self.rules, 1, self.sample_ships(), [], supply_observations=(),
        )
        gate = next(item for item in result.gates if item.name == "ship_supply")
        self.assertFalse(gate.passed)
        self.assertIn("missing", gate.detail)
        self.assertFalse(result.dispatch_authorized)

    def test_partial_supply_blocks_readiness(self):
        supply = [ShipSupplyObservation(i, 15, 15, 10, 20) for i in (10, 11, 12, 13)]
        result = assess_expedition_readiness(
            self.rules, 1, self.sample_ships(), [], supply_observations=supply,
        )
        gate = next(item for item in result.gates if item.name == "ship_supply")
        self.assertFalse(gate.passed)
        self.assertIn("not fully supplied", gate.detail)


if __name__ == "__main__":
    unittest.main()
