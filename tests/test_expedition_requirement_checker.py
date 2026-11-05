"""R3-I: the pure requirement evaluator's complete decision matrix.

All tests are offline. The evaluator must never read a file, open a socket,
open a database, send a click or mutate its inputs while it decides. Every
scenario from the R3-I handoff is covered, plus exact-threshold boundaries and
one real captured ``api_port/port`` payload.
"""
from __future__ import annotations

import copy
import json
import socket
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from kancollebot.core.observed_state import ObservedState
from kancollebot.core.observation_contracts import ObservedFleetSnapshot
from kancollebot.expeditions.expedition_requirement_checker import (
    CheckStatus,
    ExpeditionRequirementChecker,
    NormalizedFleetSnapshot,
    RequirementOutcome,
    SnapshotNormalizationError,
    evaluate_expedition_requirements,
    normalize_observed_fleets,
    normalize_raw_port_payload,
)
from kancollebot.expeditions.expedition_rule_data import (
    DATA_PATH,
    load_rule_set,
)

FIXED_NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
MAX_AGE = timedelta(seconds=30)
SHIP_IDS = (101, 102, 103, 104, 105, 106)
# 1 light cruiser flagship + 5 destroyers: satisfies most 6-ship compositions.
SHIP_TYPES = (3, 2, 2, 2, 2, 2)
DRUM = 75
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "r3i_captured_port_snapshot.json"


def fleet(**overrides) -> NormalizedFleetSnapshot:
    values = dict(
        fleet_id=2,
        ship_ids=SHIP_IDS,
        ship_levels=(10,) * 6,
        ship_types=SHIP_TYPES,
        state_trusted=True,
        state_untrusted_reasons=(),
        snapshot_source="poi-redux:info.fleets+info.ships",
        mission_status=0,
        reserved=0,
        fleet_unlocked=True,
        dispatch_policy_allowed=True,
        fuel_ready=True,
        ammo_ready=True,
        unsupplied_ship_ids=(),
        heavy_damage_ship_ids=(),
        hp_evidence_available=True,
        in_dock_ship_ids=(),
        equipment_by_ship=None,
        total_stats=None,
        conditions=None,
        identity_ambiguous=False,
        identity_reasons=(),
    )
    values.update(overrides)
    return NormalizedFleetSnapshot(**values)


def checker(**kwargs) -> ExpeditionRequirementChecker:
    return ExpeditionRequirementChecker(clock=lambda: FIXED_NOW, **kwargs)


def drums(counts: dict[int, int]) -> dict[int, tuple[int, ...]]:
    return {ship_id: (DRUM,) * counts.get(ship_id, 0) for ship_id in SHIP_IDS}


def mission24_ready(**overrides) -> NormalizedFleetSnapshot:
    """Every mission-24 condition satisfied; override one to isolate a test."""
    values = dict(
        ship_levels=(50, 30, 30, 30, 30, 30),
        equipment_by_ship=drums({101: 2}),
    )
    values.update(overrides)
    return fleet(**values)


def mission101_ready(**overrides) -> NormalizedFleetSnapshot:
    """Every mission-101 condition satisfied; override one to isolate a test."""
    values = dict(
        ship_levels=(25,) * 6,
        total_stats={"firepower": 50, "anti_air": 70, "asw": 180},
    )
    values.update(overrides)
    return fleet(**values)


def custom_rule_set(mutate) -> object:
    """Load a modified copy of the real table from a temporary file."""
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    mutate(payload)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "rules.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return load_rule_set(path)


class PassAndExplainabilityTests(unittest.TestCase):
    def test_a_fully_satisfied_fleet_passes_and_the_verdict_is_explainable(self):
        verdict = checker().evaluate(fleet(), 1)
        self.assertIs(verdict.overall_result, RequirementOutcome.PASS)
        self.assertTrue(verdict.allows_dispatch)
        self.assertEqual(verdict.fleet_id, 2)
        self.assertEqual(verdict.expedition_id, 1)
        self.assertEqual(verdict.checked_at, FIXED_NOW)
        self.assertEqual(verdict.failed_checks, ())
        self.assertEqual(verdict.unknown_checks, ())
        self.assertEqual(verdict.rejection_reasons, ())
        self.assertIn("state_trust", verdict.passed_checks)
        self.assertIn("rule_source", verdict.passed_checks)
        self.assertIn("ship_count", verdict.passed_checks)
        self.assertIn("supply", verdict.passed_checks)
        # NOT_APPLICABLE is reported as a resolved check, never as UNKNOWN.
        self.assertIn("equipment", verdict.passed_checks)
        self.assertIn("fleet_stats", verdict.passed_checks)
        self.assertTrue(verdict.rule_source)
        self.assertTrue(verdict.rule_version)
        self.assertTrue(verdict.rule_source_refs)
        for check in verdict.checks:
            self.assertIsInstance(check.status, CheckStatus)
            self.assertTrue(check.detail)

    def test_the_event_payload_is_json_safe_and_carries_all_required_fields(self):
        payload = checker().evaluate(fleet(), 1).as_dict()
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self.assertEqual(json.loads(encoded), payload)
        for key in (
            "fleet_id", "expedition_id", "overall_result", "checked_at",
            "rule_source", "rule_version", "passed_checks", "failed_checks",
            "unknown_checks", "rejection_reasons", "allows_dispatch", "safety",
        ):
            self.assertIn(key, payload)
        safety = payload["safety"]
        self.assertEqual(safety["game_clicks_sent"], 0)
        self.assertEqual(safety["game_api_requests_sent"], 0)
        self.assertFalse(safety["game_input_sent"])
        self.assertFalse(safety["fleet_composition_changes_proposed"])
        self.assertFalse(safety["ship_state_changes_proposed"])
        self.assertTrue(safety["evaluation_read_only"])
        self.assertFalse(payload["sparkle_assessment"]["used_as_dispatch_gate"])

    def test_only_pass_allows_dispatch(self):
        self.assertTrue(checker().evaluate(fleet(), 1).allows_dispatch)
        self.assertFalse(checker().evaluate(fleet(), 9999).allows_dispatch)
        self.assertFalse(checker().evaluate(fleet(mission_status=1), 1).allows_dispatch)
        self.assertFalse(
            checker().evaluate(
                fleet(state_trusted=False, state_untrusted_reasons=("stale",)), 1
            ).allows_dispatch
        )

    def test_the_module_level_wrapper_uses_the_same_contract(self):
        verdict = evaluate_expedition_requirements(fleet(), 1, checker=checker())
        self.assertIs(verdict.overall_result, RequirementOutcome.PASS)


class RejectionTests(unittest.TestCase):
    def assert_reject(self, verdict, *expected_failed):
        self.assertIs(verdict.overall_result, RequirementOutcome.REJECT)
        self.assertFalse(verdict.allows_dispatch)
        for name in expected_failed:
            self.assertIn(name, verdict.failed_checks)
        self.assertTrue(verdict.rejection_reasons)
        self.assertEqual(verdict.safety["game_clicks_sent"], 0)

    def test_too_few_ships(self):
        three_ship = fleet(ship_ids=SHIP_IDS[:2], ship_levels=(10, 10), ship_types=(3, 2))
        self.assert_reject(checker().evaluate(three_ship, 3), "ship_count")

    def test_missing_required_ship_type(self):
        all_dd = fleet(ship_types=(2,) * 6)
        self.assert_reject(checker().evaluate(all_dd, 4), "ship_type_composition")

    def test_flagship_level_below_requirement(self):
        below = fleet(ship_levels=(2,) + (10,) * 5)
        self.assert_reject(checker().evaluate(below, 3), "flagship_level")

    def test_flagship_ship_type_mismatch_is_rejected(self):
        # Mission 24 requires a light cruiser (api_stype 3) as flagship.
        verdict = checker().evaluate(mission24_ready(ship_types=(2,) * 6), 24)
        self.assertIs(verdict.overall_result, RequirementOutcome.REJECT)
        self.assertIn("flagship_valid", verdict.failed_checks)
        self.assertFalse(verdict.allows_dispatch)

    def test_fleet_total_level_below_requirement(self):
        low = fleet(
            ship_levels=(50, 20, 20, 20, 20, 20),
            equipment_by_ship=drums({101: 2}),
        )
        self.assert_reject(checker().evaluate(low, 24), "fleet_total_level")

    def test_missing_equipment_is_rejected_when_evidence_exists(self):
        no_drums = fleet(equipment_by_ship=drums({}))
        self.assert_reject(checker().evaluate(no_drums, 24), "equipment")

    def test_stat_totals_below_requirement_are_rejected(self):
        low = fleet(
            ship_levels=(25,) * 6,
            total_stats={"firepower": 50, "anti_air": 70, "asw": 179},
        )
        self.assert_reject(checker().evaluate(low, 101), "fleet_stats")

    def test_fuel_and_ammunition_shortfalls_are_rejected(self):
        for override in (
            {"fuel_ready": False, "unsupplied_ship_ids": (101,)},
            {"ammo_ready": False},
            {"fuel_ready": False, "ammo_ready": False},
        ):
            with self.subTest(override=override):
                self.assert_reject(checker().evaluate(fleet(**override), 1), "supply")

    def test_unavailable_fleet_states_are_rejected(self):
        for override in (
            {"mission_status": 1},
            {"mission_status": 2},
            {"mission_status": None},
            {"reserved": 1},
            {"fleet_unlocked": False},
            {"dispatch_policy_allowed": False},
            {"in_dock_ship_ids": (101,)},
        ):
            with self.subTest(override=override):
                self.assert_reject(
                    checker().evaluate(fleet(**override), 1), "fleet_available"
                )

    def test_heavy_damage_is_rejected(self):
        self.assert_reject(
            checker().evaluate(fleet(heavy_damage_ship_ids=(101,)), 1), "damage"
        )

    def test_no_free_slots_means_exactly_the_required_ship_count(self):
        def mutate(payload):
            for rule in payload["rules"]:
                if rule["mission_id"] == 1:
                    rule["allow_extra_ships"] = False

        strict = checker().rule_set
        active = ExpeditionRequirementChecker(
            custom_rule_set(mutate), clock=lambda: FIXED_NOW
        )
        self.assertIsNot(active.rule_set, strict)
        verdict = active.evaluate(fleet(), 1)
        self.assert_reject(verdict, "ship_count")
        self.assertIn("exactly", next(c.detail for c in verdict.checks if c.name == "ship_count"))


class UnknownDataTests(unittest.TestCase):
    def assert_unknown(self, verdict, *expected_unknown):
        self.assertIs(verdict.overall_result, RequirementOutcome.UNKNOWN)
        self.assertFalse(verdict.allows_dispatch)
        for name in expected_unknown:
            self.assertIn(name, verdict.unknown_checks)
        self.assertTrue(verdict.rejection_reasons)
        self.assertEqual(verdict.safety["game_clicks_sent"], 0)

    def test_stale_poi_data_forces_unknown_even_when_a_failure_is_visible(self):
        stale = fleet(
            state_trusted=False,
            state_untrusted_reasons=("Poi observation is stale or future-dated",),
            mission_status=1,
            fuel_ready=False,
        )
        verdict = checker().evaluate(stale, 1)
        self.assert_unknown(verdict, "state_trust")
        # The visible failures are still listed, but cannot be trusted as a
        # definitive REJECT when the observation itself is untrusted.
        self.assertIn("fleet_available", verdict.failed_checks)
        self.assertIn("supply", verdict.failed_checks)
        self.assertIn("Poi observation is stale", verdict.rejection_reasons[0])

    def test_missing_ship_key_fields_force_unknown(self):
        for override in (
            {"ship_levels": ()},
            {"ship_types": ()},
            {"fuel_ready": None},
            {"ammo_ready": None},
            {"hp_evidence_available": False},
        ):
            with self.subTest(override=override):
                self.assert_unknown(checker().evaluate(fleet(**override), 1))

    def test_missing_mission_rule_forces_unknown(self):
        verdict = checker().evaluate(fleet(), 9999)
        self.assert_unknown(verdict, "rule_source", "ship_count")
        self.assertEqual(verdict.rule_source_refs, ())
        self.assertIsNone(verdict.rule_confidence)

    def test_a_source_conflict_forces_unknown_even_for_a_perfect_fleet(self):
        perfect = fleet(
            ship_levels=(50, 30, 30, 30, 30, 30),
            equipment_by_ship=drums({101: 3, 102: 1, 103: 1}),
        )
        for mission_id in (21, 37, 38):
            with self.subTest(mission=mission_id):
                verdict = checker().evaluate(perfect, mission_id)
                self.assert_unknown(verdict, "rule_source")
                self.assertIn("conflicts", next(
                    c.detail for c in verdict.checks if c.name == "rule_source"
                ))
        # Mission 37's other conditions are all satisfied: the ONLY reason it
        # cannot dispatch is the unresolved upstream disagreement.
        verdict = checker().evaluate(perfect, 37)
        self.assertEqual(verdict.failed_checks, ())

    def test_two_rule_sets_that_disagree_force_unknown_without_picking_one(self):
        def mutate(payload):
            for rule in payload["rules"]:
                if rule["mission_id"] == 1:
                    rule["minimum_ships"] = 3
                    rule["free_slots"] = 3

        other = custom_rule_set(mutate)
        self.assert_unknown(
            checker(corroborating_rule_set=other).evaluate(fleet(), 1), "rule_source"
        )
        self.assertIs(
            checker(corroborating_rule_set=load_rule_set()).evaluate(fleet(), 1).overall_result,
            RequirementOutcome.PASS,
        )

    def test_a_single_source_rule_can_be_held_to_a_cross_checked_standard(self):
        strict = checker(minimum_confidence="cross_checked")
        self.assert_unknown(strict.evaluate(fleet(), 24), "rule_source")
        self.assertIs(strict.evaluate(fleet(), 1).overall_result, RequirementOutcome.PASS)

    def test_unstructured_alternative_compositions_are_never_guessed(self):
        # Every other mission-103 condition is satisfied; the only unresolved
        # item is the unmodelled alternative composition, so it must be UNKNOWN.
        ready = fleet(
            ship_levels=(40,) * 6,
            total_stats={"firepower": 300, "anti_air": 200, "asw": 200, "los": 120},
        )
        verdict = checker().evaluate(ready, 103)
        self.assert_unknown(verdict, "ship_type_composition")
        self.assertEqual(verdict.failed_checks, ())

    def test_equipment_and_stat_evidence_absent_forces_unknown_not_pass(self):
        # All other conditions are satisfied, so the missing evidence alone
        # decides: it must be UNKNOWN, never PASS and never a silent exemption.
        self.assert_unknown(
            checker().evaluate(mission24_ready(equipment_by_ship=None), 24), "equipment"
        )
        self.assert_unknown(
            checker().evaluate(mission101_ready(total_stats=None), 101), "fleet_stats"
        )
        self.assert_unknown(
            checker().evaluate(mission101_ready(total_stats={"firepower": 99}), 101),
            "fleet_stats",
        )
        self.assert_unknown(
            checker().evaluate(
                mission24_ready(equipment_by_ship={101: (DRUM, DRUM)}), 24
            ),
            "equipment",
        )

    def test_non_target_fleet_snapshot_is_never_used_to_clear_another_fleet(self):
        other_fleet = fleet(fleet_id=3)
        verdict = checker().evaluate(other_fleet, 1, fleet_id=2)
        self.assert_unknown(verdict, "fleet_identity")
        self.assertEqual(verdict.fleet_id, 2)

    def test_ambiguous_fleet_identity_is_unknown(self):
        self.assert_unknown(
            checker().evaluate(
                fleet(identity_ambiguous=True, identity_reasons=("two fleet records",)), 1
            ),
            "fleet_identity",
        )


class DeterminismAndBoundariesTests(unittest.TestCase):
    def test_input_order_does_not_change_the_verdict(self):
        ordered = fleet(
            equipment_by_ship=drums({101: 2}),
            total_stats={"firepower": 50, "anti_air": 70, "asw": 180},
            ship_levels=(25,) * 6,
            conditions={101: 53, 102: 49, 103: 49, 104: 49, 105: 49, 106: 49},
        )
        reversed_evidence = fleet(
            equipment_by_ship=dict(reversed(list(drums({101: 2}).items()))),
            total_stats=dict(reversed(list(
                {"firepower": 50, "anti_air": 70, "asw": 180}.items()
            ))),
            ship_levels=(25,) * 6,
            conditions=dict(reversed(list(
                {101: 53, 102: 49, 103: 49, 104: 49, 105: 49, 106: 49}.items()
            ))),
        )
        first = checker().evaluate(ordered, 101).as_dict()
        second = checker().evaluate(reversed_evidence, 101).as_dict()
        self.assertEqual(first, second)
        self.assertEqual(first["sparkle_assessment"]["sparkled_ship_ids"], [101])

    def test_repeated_evaluation_has_no_side_effects_on_inputs_or_state(self):
        snapshot = fleet(
            equipment_by_ship=drums({101: 2}),
            total_stats={"firepower": 50, "anti_air": 70, "asw": 180},
            ship_levels=(25,) * 6,
        )
        before = copy.deepcopy(snapshot)
        active = checker()
        verdicts = [active.evaluate(snapshot, 101).as_dict() for _ in range(3)]
        self.assertEqual(verdicts[0], verdicts[1])
        self.assertEqual(verdicts[1], verdicts[2])
        self.assertEqual(snapshot, before)
        self.assertEqual(snapshot.equipment_by_ship, before.equipment_by_ship)
        self.assertEqual(active.rule_set.rule_version, "2026-11-05.1")

    def test_evaluate_performs_no_file_socket_or_database_io(self):
        active = checker()
        with patch("builtins.open", side_effect=AssertionError("file I/O")), \
                patch.object(sqlite3, "connect", side_effect=AssertionError("db I/O")), \
                patch.object(socket, "socket", side_effect=AssertionError("socket I/O")):
            verdict = active.evaluate(fleet(), 1)
        self.assertIs(verdict.overall_result, RequirementOutcome.PASS)

    def test_exact_threshold_ship_count_and_flagship_level_pass(self):
        exactly = fleet(
            ship_ids=SHIP_IDS[:3],
            ship_levels=(3, 1, 1),
            ship_types=(3, 2, 2),
        )
        verdict = checker().evaluate(exactly, 3)
        self.assertIs(verdict.overall_result, RequirementOutcome.PASS)
        self.assertIn("ship_count", verdict.passed_checks)
        self.assertIn("flagship_level", verdict.passed_checks)

    def test_exact_threshold_total_level_stats_and_drums_pass(self):
        exact = fleet(
            ship_levels=(50, 30, 30, 30, 30, 30),  # total 200, flagship 50
            equipment_by_ship=drums({101: 2}),      # 2 drums on 1 carrier
            total_stats={"firepower": 50, "anti_air": 70, "asw": 180},
        )
        self.assertEqual(sum(exact.ship_levels), 200)
        level_verdict = checker().evaluate(exact, 24)
        self.assertIs(level_verdict.overall_result, RequirementOutcome.PASS)
        self.assertIn("fleet_total_level", level_verdict.passed_checks)
        self.assertIn("equipment", level_verdict.passed_checks)
        stat_verdict = checker().evaluate(fleet(
            ship_levels=(20,) * 6,
            total_stats={"firepower": 50, "anti_air": 70, "asw": 180},
        ), 101)
        self.assertIs(stat_verdict.overall_result, RequirementOutcome.PASS)
        self.assertIn("fleet_stats", stat_verdict.passed_checks)

    def test_one_below_each_threshold_is_rejected(self):
        self.assertIn(
            "fleet_total_level",
            checker().evaluate(fleet(
                ship_levels=(50, 30, 30, 30, 30, 29),
                equipment_by_ship=drums({101: 2}),
            ), 24).failed_checks,
        )
        self.assertIn(
            "flagship_level",
            checker().evaluate(fleet(ship_levels=(2,) + (10,) * 5), 3).failed_checks,
        )
        self.assertIn(
            "equipment",
            checker().evaluate(fleet(
                ship_levels=(50,) + (30,) * 5,
                equipment_by_ship=drums({101: 1}),
            ), 24).failed_checks,
        )

    def test_sparkle_is_reported_read_only_and_never_a_gate(self):
        verdict = checker().evaluate(fleet(
            conditions={ship_id: 49 for ship_id in SHIP_IDS}
        ), 1)
        self.assertIs(verdict.overall_result, RequirementOutcome.PASS)
        self.assertTrue(verdict.sparkle_assessment.conditions_available)
        self.assertFalse(verdict.sparkle_assessment.all_sparkled)
        self.assertEqual(verdict.sparkle_assessment.sparkled_ship_ids, ())
        self.assertFalse(verdict.as_dict()["sparkle_assessment"]["used_as_dispatch_gate"])


class NormalizerTests(unittest.TestCase):
    def observed(self, **overrides) -> ObservedState:
        target = ObservedFleetSnapshot(
            fleet_id=2,
            ship_ids=SHIP_IDS[:4],
            ship_levels=(10, 10, 10, 10),
            ship_types=(3, 2, 2, 2),
            mission_status=0,
            reserved=0,
            fuel_ready=True,
            ammo_ready=True,
            **overrides,
        )
        other = ObservedFleetSnapshot(
            fleet_id=3,
            ship_ids=(201, 202),
            ship_levels=(10, 10),
            ship_types=(2, 2),
        )
        return ObservedState(
            (target, other),
            FIXED_NOW - timedelta(seconds=2),
            "poi-redux:info.fleets+info.ships",
            source_freshness_verified=True,
        )

    def test_a_fresh_production_observation_can_pass(self):
        normalized = normalize_observed_fleets(
            self.observed(), 2, now=FIXED_NOW, max_state_age=MAX_AGE
        )
        self.assertTrue(normalized.state_trusted)
        self.assertEqual(normalized.ship_ids, SHIP_IDS[:4])
        self.assertEqual(normalized.ship_types, (3, 2, 2, 2))
        self.assertIs(
            checker().evaluate(normalized, 1).overall_result,
            RequirementOutcome.PASS,
        )

    def test_equipment_and_stat_evidence_is_taken_from_the_same_record(self):
        equipment = {
            ship_id: (DRUM, DRUM) if index == 0 else ()
            for index, ship_id in enumerate(SHIP_IDS[:4])
        }
        stats = {"firepower": 300, "anti_air": 220, "asw": 240, "los": 150}
        normalized = normalize_observed_fleets(
            self.observed(
                equipment_by_ship=equipment,
                total_stats=stats,
                conditions={ship_id: 49 for ship_id in SHIP_IDS[:4]},
            ),
            2, now=FIXED_NOW, max_state_age=MAX_AGE,
        )
        self.assertEqual(normalized.equipment_by_ship, equipment)
        self.assertEqual(dict(normalized.total_stats), stats)
        # No keyword override was passed: the production gate can only ever see
        # evidence carried by the very record whose freshness it checked.
        self.assertEqual(
            dict(normalized.conditions), {s: 49 for s in SHIP_IDS[:4]}
        )

    def test_a_stale_observation_cannot_be_made_trusted_by_injecting_evidence(self):
        equipment = {ship_id: () for ship_id in SHIP_IDS[:4]}
        normalized = normalize_observed_fleets(
            self.observed(equipment_by_ship=equipment), 2,
            now=FIXED_NOW + MAX_AGE + timedelta(seconds=1),
            max_state_age=MAX_AGE,
        )
        self.assertFalse(normalized.state_trusted)
        self.assertIs(
            checker().evaluate(normalized, 1).overall_result,
            RequirementOutcome.UNKNOWN,
        )

    def test_an_explicit_override_still_wins_for_offline_tests(self):
        override = {ship_id: () for ship_id in SHIP_IDS[:4]}
        normalized = normalize_observed_fleets(
            self.observed(), 2, now=FIXED_NOW, max_state_age=MAX_AGE,
            equipment_by_ship=override,
        )
        self.assertEqual(normalized.equipment_by_ship, override)

    def test_in_dock_evidence_from_the_same_record_blocks_dispatch(self):
        normalized = normalize_observed_fleets(
            self.observed(in_dock_ship_ids=(SHIP_IDS[0],)),
            2, now=FIXED_NOW, max_state_age=MAX_AGE,
        )
        self.assertEqual(normalized.in_dock_ship_ids, (SHIP_IDS[0],))
        verdict = checker().evaluate(normalized, 1)
        self.assertIs(verdict.overall_result, RequirementOutcome.REJECT)
        self.assertIn("fleet_available", verdict.failed_checks)

    def test_a_record_without_evidence_does_not_invent_it(self):
        normalized = normalize_observed_fleets(
            self.observed(), 2, now=FIXED_NOW, max_state_age=MAX_AGE
        )
        self.assertIsNone(normalized.equipment_by_ship)
        self.assertIsNone(normalized.total_stats)
        verdict = checker().evaluate(normalized, 24)
        # A 4-ship level-10 fleet also has definite composition failures, so
        # REJECT dominates the non-blocking equipment UNKNOWN. What matters is
        # that the equipment condition itself is UNKNOWN and nothing is allowed.
        self.assertIn("equipment", verdict.unknown_checks)
        self.assertFalse(verdict.allows_dispatch)
        equipment_check = next(c for c in verdict.checks if c.name == "equipment")
        self.assertIs(equipment_check.status, CheckStatus.UNKNOWN)

    def test_with_equipment_evidence_the_drum_condition_is_no_longer_unknown(self):
        equipment = {ship_id: () for ship_id in SHIP_IDS[:4]}
        normalized = normalize_observed_fleets(
            self.observed(equipment_by_ship=equipment),
            2, now=FIXED_NOW, max_state_age=MAX_AGE,
        )
        verdict = checker().evaluate(normalized, 24)
        # The condition is now DECIDED (REJECT: zero drums) instead of the
        # permanent UNKNOWN it returned before a trusted observation existed.
        self.assertNotIn("equipment", verdict.unknown_checks)
        self.assertIn("equipment", verdict.failed_checks)

    def test_stale_unverified_or_foreign_observations_are_unknown(self):
        base = self.observed()
        cases = (
            ("unverified source", replace(base, source_freshness_verified=False)),
            ("foreign source", replace(base, source="hand-typed-note")),
            ("stale timestamp", replace(
                base, observed_at=FIXED_NOW - timedelta(seconds=120)
            )),
            ("future-dated timestamp", replace(
                base, observed_at=FIXED_NOW + timedelta(seconds=120)
            )),
        )
        for label, observation in cases:
            with self.subTest(case=label):
                normalized = normalize_observed_fleets(
                    observation, 2, now=FIXED_NOW, max_state_age=MAX_AGE
                )
                self.assertFalse(normalized.state_trusted)
                self.assertTrue(normalized.state_untrusted_reasons)
                verdict = checker().evaluate(normalized, 1)
                self.assertIs(verdict.overall_result, RequirementOutcome.UNKNOWN)
                self.assertIn("state_trust", verdict.unknown_checks)

    def test_duplicate_or_missing_fleet_records_are_ambiguous(self):
        observation = self.observed()
        target = observation.payload[0]
        duplicated = replace(observation, payload=(target, target, observation.payload[1]))
        normalized = normalize_observed_fleets(
            duplicated, 2, now=FIXED_NOW, max_state_age=MAX_AGE
        )
        self.assertTrue(normalized.identity_ambiguous)
        self.assertIs(
            checker().evaluate(normalized, 1).overall_result,
            RequirementOutcome.UNKNOWN,
        )
        missing = replace(observation, payload=(observation.payload[1],))
        normalized_missing = normalize_observed_fleets(
            missing, 2, now=FIXED_NOW, max_state_age=MAX_AGE
        )
        self.assertTrue(normalized_missing.identity_ambiguous)
        self.assertEqual(normalized_missing.ship_ids, ())

    def test_ship_ids_shared_with_another_fleet_are_ambiguous(self):
        observation = self.observed()
        contaminated = replace(
            observation,
            payload=(
                observation.payload[0],
                ObservedFleetSnapshot(
                    fleet_id=3, ship_ids=(101, 202),
                    ship_levels=(10, 10), ship_types=(2, 2),
                ),
            ),
        )
        normalized = normalize_observed_fleets(
            contaminated, 2, now=FIXED_NOW, max_state_age=MAX_AGE
        )
        self.assertTrue(normalized.identity_ambiguous)
        self.assertIs(
            checker().evaluate(normalized, 1, fleet_id=2).overall_result,
            RequirementOutcome.UNKNOWN,
        )

    def test_fleet_record_order_does_not_change_the_verdict(self):
        observation = self.observed()
        forward = checker().evaluate(normalize_observed_fleets(
            observation, 2, now=FIXED_NOW, max_state_age=MAX_AGE
        ), 1).as_dict()
        reversed_order = replace(observation, payload=tuple(reversed(observation.payload)))
        backward = checker().evaluate(normalize_observed_fleets(
            reversed_order, 2, now=FIXED_NOW, max_state_age=MAX_AGE
        ), 1).as_dict()
        self.assertEqual(forward, backward)

    def test_normalizer_rejects_invalid_time_arguments(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            normalize_observed_fleets(
                self.observed(), 2,
                now=FIXED_NOW.replace(tzinfo=None), max_state_age=MAX_AGE,
            )
        with self.assertRaisesRegex(ValueError, "must not be negative"):
            normalize_observed_fleets(
                self.observed(), 2, now=FIXED_NOW, max_state_age=timedelta(seconds=-1)
            )

    def test_checker_rejects_invalid_arguments(self):
        active = checker()
        with self.assertRaisesRegex(ValueError, "must be a NormalizedFleetSnapshot"):
            active.evaluate("not a snapshot", 1)
        for bad in (0, -1, True, "1"):
            with self.subTest(mission=bad):
                with self.assertRaisesRegex(ValueError, "mission_id must be"):
                    active.evaluate(fleet(), bad)
        with self.assertRaisesRegex(ValueError, "fleet_id must be"):
            active.evaluate(fleet(), 1, fleet_id=0)
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            ExpeditionRequirementChecker(
                clock=lambda: datetime(2026, 11, 5, 12, 0)
            ).evaluate(fleet(), 1)

    def test_normalized_snapshot_rejects_impossible_shapes(self):
        for override in (
            {"fleet_id": 0},
            {"ship_ids": (101, 101, 102, 103, 104, 105)},
            {"ship_ids": (101,) * 7},
            {"ship_levels": (10, 10)},
            {"ship_types": (0, 2, 2, 2, 2, 2)},
            {"total_stats": {"luck": 99}},
        ):
            with self.subTest(override=override):
                with self.assertRaises(SnapshotNormalizationError):
                    fleet(**override)


class CapturedPortPayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_fixture_is_a_real_read_only_capture(self):
        provenance = self.port["provenance"]
        self.assertEqual(provenance["verificationStatus"], "CAPTURED_UNDER_STATICALLY_REVIEWED_PROBE")
        self.assertEqual(provenance["source"]["path"], "/kcsapi/api_port/port")
        self.assertFalse(provenance["safety"]["gameInputSent"])
        self.assertFalse(provenance["safety"]["gameApiRequestInitiated"])

    def test_real_fleet_2_is_normalized_without_inventing_missing_fields(self):
        normalized = normalize_raw_port_payload(
            self.port["api_deck_port"], self.port["api_ship"], 2,
            state_trusted=True,
        )
        # The real payload's -1 sentinels are dropped, not treated as ships.
        self.assertEqual(normalized.ship_ids, (1, 4, 8, 25))
        self.assertEqual(normalized.mission_status, 0)
        self.assertEqual(normalized.reserved, 0)
        # A bare port payload has no master max_fuel/max_bull, levels or stypes.
        self.assertIsNone(normalized.fuel_ready)
        self.assertIsNone(normalized.ammo_ready)
        self.assertEqual(normalized.ship_levels, ())
        self.assertEqual(normalized.ship_types, ())
        self.assertTrue(normalized.hp_evidence_available)
        self.assertEqual(normalized.heavy_damage_ship_ids, ())
        self.assertEqual(normalized.conditions[1], 53)
        self.assertEqual(tuple(sorted(normalized.conditions)), (1, 4, 8, 25))

    def test_real_payload_cannot_pass_a_dispatch_gate(self):
        normalized = normalize_raw_port_payload(
            self.port["api_deck_port"], self.port["api_ship"], 2,
            state_trusted=True,
        )
        verdict = checker().evaluate(normalized, 1)
        self.assertIs(verdict.overall_result, RequirementOutcome.UNKNOWN)
        self.assertFalse(verdict.allows_dispatch)
        self.assertIn("supply", verdict.unknown_checks)
        self.assertIn("flagship_level", verdict.unknown_checks)
        self.assertIn("flagship_valid", verdict.unknown_checks)
        self.assertEqual(verdict.safety["game_clicks_sent"], 0)

    def test_a_captured_payload_marked_stale_is_still_unknown(self):
        normalized = normalize_raw_port_payload(
            self.port["api_deck_port"], self.port["api_ship"], 2,
            state_trusted=False,
            state_untrusted_reasons=("captured 2026-10-29, not a live witness",),
        )
        verdict = checker().evaluate(normalized, 1)
        self.assertIs(verdict.overall_result, RequirementOutcome.UNKNOWN)
        self.assertIn("state_trust", verdict.unknown_checks)

    def test_mixed_ship_ids_between_fleets_are_flagged(self):
        contaminated = copy.deepcopy(self.port["api_deck_port"])
        contaminated[2]["api_ship"] = [1, 7, 33, 2, -1, -1]  # id 1 belongs to Fleet 2
        normalized = normalize_raw_port_payload(
            contaminated, self.port["api_ship"], 2, state_trusted=True
        )
        self.assertTrue(normalized.identity_ambiguous)
        self.assertIs(
            checker().evaluate(normalized, 1).overall_result,
            RequirementOutcome.UNKNOWN,
        )

    def test_a_missing_fleet_record_is_unknown_not_an_empty_pass(self):
        normalized = normalize_raw_port_payload(
            self.port["api_deck_port"], self.port["api_ship"], 4,
            state_trusted=True,
        )
        self.assertEqual(normalized.ship_ids, ())
        self.assertTrue(normalized.identity_ambiguous)
        self.assertFalse(
            checker().evaluate(normalized, 1).allows_dispatch
        )


if __name__ == "__main__":
    unittest.main()
