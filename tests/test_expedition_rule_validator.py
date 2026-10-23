"""Offline tests for fail-closed imported expedition rule validation."""

import copy
import json
import unittest
from pathlib import Path

from kancollebot.core.expedition_rule_validator import validate_expedition_rules


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "expedition" / "expedition_rules_kca_reference.json"


class ExpeditionRuleValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = json.loads(DATA.read_text(encoding="utf-8"))

    def test_reference_structure_valid_but_never_authorizes_dispatch(self):
        result = validate_expedition_rules(self.reference)
        self.assertTrue(result.valid, result.errors)
        self.assertFalse(result.dispatch_authorized)
        self.assertTrue(result.warnings)

    def test_duplicate_id_rejected(self):
        data = copy.deepcopy(self.reference)
        data["expeditions"][1]["id"] = data["expeditions"][0]["id"]
        self.assertFalse(validate_expedition_rules(data).valid)

    def test_count_mismatch_rejected(self):
        data = copy.deepcopy(self.reference)
        data["record_count"] = 1
        self.assertFalse(validate_expedition_rules(data).valid)

    def test_bad_composition_rejected(self):
        data = copy.deepcopy(self.reference)
        data["expeditions"][0]["requirements"]["composition_raw"] = "999DD"
        self.assertFalse(validate_expedition_rules(data).valid)

    def test_missing_provenance_rejected(self):
        data = copy.deepcopy(self.reference)
        del data["expeditions"][0]["source"]
        self.assertFalse(validate_expedition_rules(data).valid)

    def test_unknown_conditions_remain_non_authorizing(self):
        result = validate_expedition_rules(self.reference)
        self.assertFalse(result.dispatch_authorized)

    def test_malformed_document_rejected(self):
        self.assertFalse(validate_expedition_rules(None).valid)


if __name__ == "__main__":
    unittest.main()
