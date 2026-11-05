"""R3-I: the single static rule table must be traceable and fail closed.

Every test here is offline: no Poi, no CDP, no SQLite and no game input. The
static table is community reference data, so its provenance and internal
consistency are part of the contract, not documentation.
"""
from __future__ import annotations

import copy
import json
import tempfile
import tomllib
import unittest
from pathlib import Path

from kancollebot.calibration.expedition_catalog import (
    DEFAULT_CATALOG,
    NUMBER_BY_MISSION_ID,
)
from kancollebot.expeditions.expedition_rule_data import (
    DATA_PATH,
    DEFAULT_RULE_SET,
    ExpeditionRuleDataError,
    ExpeditionRuleSet,
    load_rule_set,
    mission_composition_requirement_for,
    requirements_for_assignments,
)


def _payload() -> dict:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def _write(payload: dict, directory: str) -> Path:
    path = Path(directory) / "rules.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class RuleTableProvenanceTests(unittest.TestCase):
    def test_the_default_table_has_a_version_and_a_pinned_upstream_commit(self):
        summary = DEFAULT_RULE_SET.provenance_summary()
        self.assertEqual(summary["schema_version"], "r3i-expedition-requirements-1")
        self.assertEqual(summary["rule_version"], "2026-11-05.1")
        self.assertEqual(summary["kind"], "static_reference")
        self.assertEqual(summary["type_code_space"], "api_stype")
        self.assertEqual(summary["drum_equipment_master_id"], 75)
        self.assertEqual(summary["rule_count"], 47)
        self.assertIn("XVs32/kcauto_custom", DEFAULT_RULE_SET.rule_source)
        self.assertIn("18d61ab3fe91", DEFAULT_RULE_SET.rule_source)
        self.assertEqual(
            DEFAULT_RULE_SET.provenance.primary_commit,
            "18d61ab3fe91235ea2476ea186bad5c516572a66",
        )
        self.assertTrue(str(summary["primary_url"]).startswith("https://github.com/"))
        self.assertEqual(summary["primary_confidence"], "community_static_single_source")

    def test_every_rule_has_provenance_and_matches_the_in_repo_catalogue(self):
        seen = set()
        for rule in DEFAULT_RULE_SET.requirements:
            with self.subTest(mission=rule.mission_id):
                self.assertNotIn(rule.mission_id, seen)
                seen.add(rule.mission_id)
                self.assertEqual(rule.catalog_number, NUMBER_BY_MISSION_ID[rule.mission_id])
                self.assertEqual(
                    rule.catalog_name,
                    DEFAULT_CATALOG.entry_of(rule.catalog_number).name,
                )
                self.assertTrue(rule.source_refs)
                self.assertTrue(rule.evidence_class)
                self.assertIn(rule.confidence, ("single_source", "cross_checked"))
                self.assertLessEqual(rule.minimum_ships, 6)
                self.assertGreaterEqual(rule.minimum_ships, 1)
                self.assertEqual(
                    sum(group.count for group in rule.required_stype_groups) + rule.free_slots,
                    rule.minimum_ships,
                )
        self.assertEqual(len(seen), len(DEFAULT_RULE_SET))
        self.assertEqual(DEFAULT_RULE_SET.requirement_for(9999), None)

    def test_conflicts_and_unstructured_compositions_are_recorded_not_hidden(self):
        conflicts = tuple(
            rule.mission_id for rule in DEFAULT_RULE_SET.requirements
            if rule.has_source_conflict
        )
        self.assertEqual(conflicts, (21, 37, 38))
        for rule in DEFAULT_RULE_SET.requirements:
            for conflict in rule.source_conflicts:
                self.assertGreaterEqual(len(conflict.values), 2)
                self.assertTrue(conflict.note)
        unstructured = tuple(
            rule.mission_id for rule in DEFAULT_RULE_SET.requirements
            if rule.composition_unstructured
        )
        self.assertEqual(unstructured, (102, 103, 104, 105))
        with_stats = tuple(
            rule.mission_id for rule in DEFAULT_RULE_SET.requirements
            if rule.requires_stat_evidence
        )
        self.assertEqual(with_stats, (101, 102, 103, 104, 105))
        with_equipment = tuple(
            rule.mission_id for rule in DEFAULT_RULE_SET.requirements
            if rule.requires_equipment_evidence
        )
        self.assertTrue(set((21, 24, 37, 38, 40)).issubset(set(with_equipment)))

    def test_a_reread_of_the_same_file_is_deterministic(self):
        again = load_rule_set()
        self.assertEqual(again.rule_version, DEFAULT_RULE_SET.rule_version)
        self.assertEqual(again.rule_source, DEFAULT_RULE_SET.rule_source)
        self.assertEqual(again.known_mission_ids, DEFAULT_RULE_SET.known_mission_ids)
        self.assertEqual(
            [rule.source_refs for rule in again.requirements],
            [rule.source_refs for rule in DEFAULT_RULE_SET.requirements],
        )


class RuleTableValidationTests(unittest.TestCase):
    def assert_rejected(self, mutate, fragment: str) -> None:
        payload = _payload()
        mutate(payload)
        with tempfile.TemporaryDirectory() as directory:
            path = _write(payload, directory)
            with self.assertRaisesRegex(ExpeditionRuleDataError, fragment):
                load_rule_set(path)

    def test_unsupported_schema_is_refused(self):
        self.assert_rejected(
            lambda p: p.update(schema_version="unknown"), "unsupported rule schema"
        )

    def test_a_wrong_catalogue_identity_is_refused(self):
        self.assert_rejected(
            lambda p: p["rules"][0].update(catalog_name="架空遠征"),
            "catalogue identity does not match",
        )

    def test_a_mission_outside_the_catalogue_is_refused(self):
        self.assert_rejected(
            lambda p: p["rules"][0].update(mission_id=9999), "not in the in-repo catalogue"
        )

    def test_mismatched_composition_slots_are_refused(self):
        self.assert_rejected(
            lambda p: p["rules"][0].update(free_slots=99), "composition slots do not sum"
        )

    def test_a_duplicate_mission_is_refused(self):
        def duplicate(payload):
            payload["rules"].append(copy.deepcopy(payload["rules"][0]))

        self.assert_rejected(duplicate, "duplicate rule")

    def test_a_wrong_type_code_space_is_refused(self):
        self.assert_rejected(
            lambda p: p["provenance"].update(type_code_space="keji8-typeID"),
            "api_stype code space",
        )

    def test_an_unknown_stat_field_is_refused(self):
        self.assert_rejected(
            lambda p: p["rules"][0].update(minimum_total_stats={"luck": 10}),
            "unknown stat field",
        )

    def test_a_rule_without_source_refs_is_refused(self):
        self.assert_rejected(
            lambda p: p["rules"][0].update(source_refs=[]), "needs at least one source_ref"
        )

    def test_an_unknown_confidence_is_refused(self):
        self.assert_rejected(
            lambda p: p["rules"][0].update(confidence="trust_me"),
            "confidence must be",
        )

    def test_a_missing_rule_table_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ExpeditionRuleDataError, "cannot read expedition rule table"):
                load_rule_set(Path(directory) / "absent.json")

    def test_a_malformed_json_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaisesRegex(ExpeditionRuleDataError, "cannot read expedition rule table"):
                load_rule_set(path)

    def test_a_directly_constructed_rule_set_rejects_a_non_mapping(self):
        with self.assertRaisesRegex(ExpeditionRuleDataError, "must be an object"):
            ExpeditionRuleSet(["not", "a", "mapping"])


class RuleTablePackagingTests(unittest.TestCase):
    def test_the_static_table_is_declared_as_package_data(self):
        # The module loads the JSON at import time via Path(__file__); a wheel
        # without package-data would fail closed on import in every consumer.
        pyproject = DATA_PATH.parents[4] / "pyproject.toml"
        with pyproject.open("rb") as handle:
            config = tomllib.load(handle)
        package_data = config["tool"]["setuptools"]["package-data"]
        self.assertIn("kancollebot.expeditions", package_data)
        self.assertIn("data/*.json", package_data["kancollebot.expeditions"])
        self.assertTrue(DATA_PATH.is_file())


class LegacyAdapterTests(unittest.TestCase):
    def test_known_missions_are_adapted_from_the_static_table(self):
        self.assertEqual(
            (
                mission_composition_requirement_for(1).minimum_ships,
                mission_composition_requirement_for(1).minimum_flagship_level,
            ),
            (2, 1),
        )
        self.assertEqual(
            (
                mission_composition_requirement_for(3).minimum_ships,
                mission_composition_requirement_for(3).minimum_flagship_level,
            ),
            (3, 3),
        )
        self.assertEqual(
            (
                mission_composition_requirement_for(5).minimum_ships,
                mission_composition_requirement_for(5).minimum_flagship_level,
            ),
            (4, 3),
        )
        # A precise group requirement must NOT be collapsed into a whitelist.
        self.assertIsNone(mission_composition_requirement_for(5).allowed_ship_types)

    def test_an_unknown_mission_is_never_invented(self):
        with self.assertRaisesRegex(ExpeditionRuleDataError, "refusing to invent one"):
            mission_composition_requirement_for(9999)

    def test_assignments_build_the_same_numbers_for_every_fleet(self):
        requirements = requirements_for_assignments({2: 1, 3: 3, 4: 1})
        self.assertEqual(set(requirements), {2, 3, 4})
        self.assertEqual(requirements[2].minimum_ships, 2)
        self.assertEqual(requirements[3].minimum_flagship_level, 3)
        self.assertEqual(requirements[4].minimum_ships, 2)

    def test_invalid_assignments_are_refused(self):
        for assignments, fragment in (
            ({1: 1}, "fleet ids must be integers >= 2"),
            ({2: 0}, "mission ids must be positive integers"),
            ({2: 9999}, "refusing to invent one"),
            ([(2, 1)], "assignments must be a mapping"),
        ):
            with self.subTest(assignments=assignments):
                with self.assertRaisesRegex(ExpeditionRuleDataError, fragment):
                    requirements_for_assignments(assignments)


if __name__ == "__main__":
    unittest.main()
