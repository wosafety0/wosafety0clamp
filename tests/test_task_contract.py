"""Stability guard for the published task contract.

``docs/contracts/task-contract-v1.json`` is a hand-editable file, which makes it
exactly the kind of document that silently rots. This test derives the contract
from ``kancollebot.task_scope`` and compares it field by field, so changing a
fleet policy without updating the contract fails CI instead of shipping a stale
promise to an external decision layer.

It deliberately does NOT test behaviour: this repository has no runtime consumer
of the contract, and pretending otherwise would overstate what exists.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from kancollebot.runtime_config import RUNTIME_CONFIG
from kancollebot.task_scope import (
    TaskKind,
    declared_task_kinds,
    fleet_policy_for,
    task_event_fields,
)

CONTRACT_PATH = Path(__file__).resolve().parents[1] / "docs" / "contracts" / "task-contract-v1.json"

# The one endpoint the contract is allowed to name. A second Poi MCP would be a
# new data channel that bypasses the freshness evidence chain.
ALLOWED_ENDPOINT = "127.0.0.1:12450"
FORBIDDEN_ENDPOINT_FRAGMENTS = ("39271", "poi-plugin", "poi_plugin")


def _load() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _derive_tasks() -> list[dict]:
    derived: list[dict] = []
    for kind in declared_task_kinds():
        policy = fleet_policy_for(kind)
        derived.append(
            {
                "task_kind": policy.task_kind.value,
                "implemented": policy.implemented,
                "ui_click_capable": policy.ui_click_capable,
                # An intent is only accepted by a task that is both implemented
                # AND has a calibrated click path. Everything else fails closed.
                "accepts_intent": policy.implemented and policy.ui_click_capable,
                "dispatchable_fleet_ids": list(policy.dispatchable_fleet_ids),
                "reserved_fleet_ids": list(policy.reserved_fleet_ids),
                "resupply_verified_fleet_ids": list(policy.resupply_verified_fleet_ids),
            }
        )
    return derived


class TaskContractFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(
            CONTRACT_PATH.is_file(), f"contract file is missing: {CONTRACT_PATH}"
        )
        self.data = _load()

    def test_header_declares_a_read_only_consumer(self) -> None:
        self.assertEqual(self.data["contract"], "kancollebot.task-contract")
        self.assertEqual(self.data["role"], "consumer")
        self.assertEqual(self.data["direction"], "inbound")
        self.assertIs(self.data["read_only"], True)
        self.assertEqual(self.data["generated_from"], "kancollebot.task_scope")

    def test_version_is_semantic(self) -> None:
        parts = str(self.data["version"]).split(".")
        self.assertEqual(len(parts), 3, "version must be MAJOR.MINOR.PATCH")
        for part in parts:
            self.assertTrue(part.isdigit(), f"non-numeric version segment: {part!r}")

    def test_authority_keeps_execution_inside_this_repository(self) -> None:
        authority = self.data["authority"]
        self.assertIn("consumer", authority["wosafety0clamp"])
        self.assertIn("no execution authority", authority["external_decision_layer"])
        # The decision layer must not be granted a session or a click path.
        self.assertIn("no game session", authority["external_decision_layer"])

    def test_hard_constraints_are_present_and_name_the_single_endpoint(self) -> None:
        constraints = self.data["hard_constraints"]
        self.assertGreaterEqual(len(constraints), 5)
        joined = " ".join(constraints)
        self.assertIn(ALLOWED_ENDPOINT, joined)
        self.assertIn("consumes decisions and never produces them", joined)
        self.assertIn("declaring a task is not implementing it", joined)

    def test_no_second_poi_endpoint_is_smuggled_into_the_contract(self) -> None:
        raw = CONTRACT_PATH.read_text(encoding="utf-8")
        for fragment in FORBIDDEN_ENDPOINT_FRAGMENTS:
            self.assertNotIn(
                fragment,
                raw,
                "the contract must not reference another Poi MCP service; a "
                "second data channel bypasses the freshness evidence chain",
            )


class TaskContractContentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _load()

    def test_tasks_match_the_code_exactly(self) -> None:
        self.assertEqual(
            self.data["tasks"],
            _derive_tasks(),
            "docs/contracts/task-contract-v1.json is out of sync with "
            "task_scope.fleet_policy_for(); update the contract or revert the policy",
        )

    def test_every_declared_task_appears_exactly_once(self) -> None:
        listed = [entry["task_kind"] for entry in self.data["tasks"]]
        self.assertEqual(sorted(listed), sorted(kind.value for kind in TaskKind))
        self.assertEqual(len(listed), len(set(listed)))

    def test_only_expedition_accepts_an_intent(self) -> None:
        accepting = [e["task_kind"] for e in self.data["tasks"] if e["accepts_intent"]]
        self.assertEqual(accepting, [TaskKind.EXPEDITION.value])

    def test_unimplemented_tasks_advertise_no_capability_and_no_fleet(self) -> None:
        for entry in self.data["tasks"]:
            if entry["task_kind"] == TaskKind.EXPEDITION.value:
                continue
            with self.subTest(task=entry["task_kind"]):
                self.assertFalse(entry["implemented"])
                self.assertFalse(entry["ui_click_capable"])
                self.assertFalse(entry["accepts_intent"])
                self.assertEqual(entry["dispatchable_fleet_ids"], [])
                self.assertEqual(entry["reserved_fleet_ids"], [])
                self.assertEqual(entry["resupply_verified_fleet_ids"], [])

    def test_accepts_intent_never_outruns_the_gates(self) -> None:
        for entry in self.data["tasks"]:
            with self.subTest(task=entry["task_kind"]):
                if entry["accepts_intent"]:
                    self.assertTrue(entry["implemented"])
                    self.assertTrue(entry["ui_click_capable"])

    def test_expedition_fleet_policy_is_derived_not_diverged(self) -> None:
        expedition = next(
            e for e in self.data["tasks"] if e["task_kind"] == TaskKind.EXPEDITION.value
        )
        self.assertEqual(
            expedition["dispatchable_fleet_ids"],
            list(RUNTIME_CONFIG.dispatchable_fleet_ids),
        )
        self.assertEqual(
            expedition["reserved_fleet_ids"],
            list(RUNTIME_CONFIG.non_dispatchable_fleet_ids),
        )
        self.assertEqual(
            expedition["resupply_verified_fleet_ids"],
            list(RUNTIME_CONFIG.resupply_verified_fleet_ids),
        )

    def test_reserved_fleet_is_expedition_policy_not_a_global_rule(self) -> None:
        # Fleet 1 is reserved FOR EXPEDITIONS because it is the sortie fleet.
        # The contract must not present that as a rule every task inherits.
        for entry in self.data["tasks"]:
            if entry["task_kind"] == TaskKind.EXPEDITION.value:
                continue
            with self.subTest(task=entry["task_kind"]):
                self.assertNotIn(1, entry["reserved_fleet_ids"])

    def test_event_attribution_keys_match_the_runtime_helper(self) -> None:
        self.assertEqual(
            self.data["event_attribution_keys"],
            list(task_event_fields(TaskKind.EXPEDITION).keys()),
        )


if __name__ == "__main__":
    unittest.main()
