"""Multi-task scoping: expedition policy must not become a global game law."""
from __future__ import annotations

import importlib.util
import inspect
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from kancollebot import cli
from kancollebot.expeditions.continuous_dispatch_controller import (
    SqliteDispatchEventLog,
)
from kancollebot.expeditions.fleet_availability import (
    FleetAvailabilityError,
    resolve_fleet_availability,
)
from kancollebot.runtime_config import RUNTIME_CONFIG
from kancollebot.task_scope import (
    SHARED_CAPABILITIES,
    CapabilityBoundary,
    SharedCapability,
    TaskKind,
    TaskScopeError,
    coerce_task_kind,
    declared_task_kinds,
    describe_shared_capabilities,
    fleet_policy_for,
    implemented_task_kinds,
    require_task_enabled,
    require_task_neutral_capability,
    shared_capabilities,
    task_event_fields,
)


ROADMAP_KINDS = (
    TaskKind.COMBAT,
    TaskKind.SPARKLE,
    TaskKind.COMPOSITION,
    TaskKind.QUESTS,
)


class TaskKindTests(unittest.TestCase):
    def test_roadmap_tasks_are_declared_but_only_expedition_is_implemented(self):
        self.assertEqual(
            set(declared_task_kinds()),
            {TaskKind.EXPEDITION, *ROADMAP_KINDS},
        )
        self.assertEqual(implemented_task_kinds(), (TaskKind.EXPEDITION,))

    def test_unknown_task_kind_is_rejected_fail_closed(self):
        for bad in ("battle", "expedition2", "", 7, None):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(TaskScopeError, "unknown task kind|must be a TaskKind"):
                    coerce_task_kind(bad)

    def test_string_values_are_accepted(self):
        self.assertIs(coerce_task_kind("expedition"), TaskKind.EXPEDITION)
        self.assertIs(coerce_task_kind(TaskKind.COMBAT), TaskKind.COMBAT)


class FleetPolicyTests(unittest.TestCase):
    def test_expedition_policy_is_derived_not_copied_from_runtime_config(self):
        policy = fleet_policy_for(TaskKind.EXPEDITION)
        self.assertEqual(
            policy.dispatchable_fleet_ids, RUNTIME_CONFIG.dispatchable_fleet_ids
        )
        self.assertEqual(
            policy.reserved_fleet_ids, RUNTIME_CONFIG.non_dispatchable_fleet_ids
        )
        self.assertEqual(
            policy.resupply_verified_fleet_ids,
            RUNTIME_CONFIG.resupply_verified_fleet_ids,
        )
        self.assertTrue(policy.implemented)
        self.assertTrue(policy.ui_click_capable)

    def test_fleet_1_is_reserved_for_expeditions_only_not_globally(self):
        """The regression this module exists to prevent.

        Fleet 1 is the sortie/combat fleet. Forbidding it in the expedition
        policy is correct; a future combat task must NOT inherit that rule.
        """
        expedition = fleet_policy_for(TaskKind.EXPEDITION)
        self.assertIn(1, expedition.reserved_fleet_ids)
        self.assertNotIn(1, expedition.dispatchable_fleet_ids)

        combat = fleet_policy_for(TaskKind.COMBAT)
        self.assertEqual(combat.reserved_fleet_ids, ())
        self.assertEqual(combat.dispatchable_fleet_ids, ())
        self.assertNotEqual(
            combat.dispatchable_fleet_ids, expedition.dispatchable_fleet_ids
        )
        self.assertIn("sortie/combat", expedition.note)

    def test_unimplemented_tasks_have_no_calibrated_fleet_policy(self):
        for kind in ROADMAP_KINDS:
            with self.subTest(kind=kind):
                policy = fleet_policy_for(kind)
                self.assertFalse(policy.implemented)
                self.assertFalse(policy.ui_click_capable)
                self.assertEqual(policy.dispatchable_fleet_ids, ())
                self.assertEqual(policy.reserved_fleet_ids, ())
                self.assertEqual(policy.resupply_verified_fleet_ids, ())
                self.assertIn("not implemented", policy.note)
                self.assertIn("must not be reused", policy.note)

    def test_mis_scoped_runtime_config_is_rejected(self):
        config = replace(RUNTIME_CONFIG, dispatch_policy_task_kind="combat")
        with self.assertRaisesRegex(TaskScopeError, "not 'expedition'"):
            fleet_policy_for(TaskKind.EXPEDITION, config=config)


class TaskGateTests(unittest.TestCase):
    def test_expedition_gate_passes(self):
        self.assertIs(
            require_task_enabled(TaskKind.EXPEDITION).task_kind,
            TaskKind.EXPEDITION,
        )

    def test_roadmap_tasks_fail_closed_before_any_click(self):
        for kind in ROADMAP_KINDS:
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(TaskScopeError, "nothing was clicked"):
                    require_task_enabled(kind)

    def test_fleet_availability_for_roadmap_task_fails_closed(self):
        for kind in ROADMAP_KINDS:
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(FleetAvailabilityError, "not implemented"):
                    resolve_fleet_availability(
                        (1, 2, 3, 4), (2,), strict=True, task_kind=kind
                    )

    def test_default_fleet_resolution_remains_expedition_and_is_attributed(self):
        availability = resolve_fleet_availability((1, 2, 3), (2, 3))
        self.assertEqual(availability.task_kind, TaskKind.EXPEDITION.value)
        self.assertEqual(availability.effective_dispatch_fleet_ids, (2, 3))

    def test_expedition_resolution_still_refuses_fleet_1(self):
        with self.assertRaisesRegex(FleetAvailabilityError, "not permitted by policy"):
            resolve_fleet_availability((1, 2, 3, 4), (1,), strict=True)


class AttributionTests(unittest.TestCase):
    def test_task_event_fields_are_structured(self):
        for kind in declared_task_kinds():
            with self.subTest(kind=kind):
                fields = task_event_fields(kind)
                self.assertEqual(fields["task_kind"], kind.value)
                self.assertEqual(
                    fields["task_implemented"],
                    kind in implemented_task_kinds(),
                )

    def test_sqlite_event_attribution_is_opt_in_and_backward_compatible(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = str(Path(temporary) / "events.sqlite3")
            plain = SqliteDispatchEventLog(path)
            plain("legacy_event", {"fleet_id": 2})
            tagged = SqliteDispatchEventLog(path, task_kind=TaskKind.EXPEDITION.value)
            tagged("task_event", {"fleet_id": 2})

            rows = tagged.load_all()
            self.assertEqual(len(rows), 2)
            # No task_kind supplied -> payload is byte-for-byte unchanged.
            self.assertEqual(rows[0]["fields"], {"fleet_id": 2})
            self.assertEqual(
                rows[1]["fields"],
                {"fleet_id": 2, "task_kind": "expedition"},
            )

    def test_cli_tasks_lists_status_without_composing_a_runtime(self):
        buffer = io.StringIO()
        with patch.object(cli.importlib, "import_module") as importer:
            with redirect_stdout(buffer):
                self.assertEqual(cli.main(["tasks"]), 0)
        importer.assert_not_called()
        output = buffer.getvalue()
        for name in ("expedition", "combat", "sparkle", "composition", "quests"):
            self.assertIn(name, output)
        self.assertIn("IMPLEMENTED", output)
        self.assertIn("NOT IMPLEMENTED (fail-closed)", output)
        self.assertIn("ui_operation_lock", output)


class SharedCapabilityBoundaryTests(unittest.TestCase):
    """The inventory is per SYMBOL and classified, not a flat 'shared' list."""

    def test_every_declared_module_path_resolves(self):
        for item in SHARED_CAPABILITIES:
            with self.subTest(name=item.name):
                self.assertIsNotNone(
                    importlib.util.find_spec(item.module),
                    f"{item.name} points at a module that does not exist",
                )
                self.assertIsInstance(item, SharedCapability)
                self.assertTrue(item.symbols)
                self.assertTrue(item.rationale)

    def test_only_three_symbols_are_task_neutral_today(self):
        neutral = {item.name for item in shared_capabilities(boundary=CapabilityBoundary.TASK_NEUTRAL)}
        self.assertEqual(
            neutral,
            {"poi_ui_mutex", "visual_dependency_gate", "append_only_event_sink"},
        )

    def test_expedition_specific_items_are_not_advertised_as_shared(self):
        """Regression: the old list called these whole modules 'task-neutral'."""
        specific = {item.name for item in shared_capabilities(boundary="task_specific")}
        self.assertEqual(
            specific,
            {"expedition_composition_checks", "expedition_dispatch_transaction"},
        )
        decouple = {item.name for item in shared_capabilities(boundary="needs_decoupling")}
        self.assertEqual(
            decouple,
            {"fleet_snapshot", "resupply_preflight", "supply_tab_coordinates"},
        )
        self.assertEqual(len(SHARED_CAPABILITIES), 8)

    def test_boundary_is_symbol_level_within_one_module(self):
        """One module can contain both a shared primitive and expedition logic."""
        same_module = [
            item
            for item in SHARED_CAPABILITIES
            if item.module == "kancollebot.expeditions.continuous_dispatch_controller"
        ]
        self.assertEqual(len(same_module), 2)
        boundaries = {item.boundary for item in same_module}
        self.assertEqual(
            boundaries,
            {CapabilityBoundary.TASK_NEUTRAL, CapabilityBoundary.TASK_SPECIFIC},
        )

    def test_task_neutral_capability_gate_is_executable(self):
        item = require_task_neutral_capability("poi_ui_mutex")
        self.assertIs(item.boundary, CapabilityBoundary.TASK_NEUTRAL)

    def test_polluted_or_specific_capabilities_fail_closed(self):
        for name in ("fleet_snapshot", "resupply_preflight", "supply_tab_coordinates",
                     "expedition_composition_checks", "expedition_dispatch_transaction"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(TaskScopeError, "nothing was clicked"):
                    require_task_neutral_capability(name)

    def test_unknown_capability_and_boundary_are_rejected(self):
        with self.assertRaisesRegex(TaskScopeError, "unknown capability"):
            require_task_neutral_capability("does_not_exist")
        with self.assertRaisesRegex(TaskScopeError, "unknown capability boundary"):
            shared_capabilities(boundary="not_a_boundary")
        with self.assertRaisesRegex(TaskScopeError, "must be a CapabilityBoundary"):
            shared_capabilities(boundary=7)


class PolicyIsolationTests(unittest.TestCase):
    """A policy decided for one task must be unreachable from another."""

    def test_each_roadmap_policy_is_empty_and_independent(self):
        expedition = fleet_policy_for(TaskKind.EXPEDITION)
        policies = [fleet_policy_for(kind) for kind in ROADMAP_KINDS]
        self.assertEqual(len({id(p) for p in policies}), len(policies))
        for policy in policies:
            self.assertEqual(policy.dispatchable_fleet_ids, ())
            self.assertEqual(policy.reserved_fleet_ids, ())
            self.assertNotEqual(policy.note, expedition.note)
            self.assertFalse(policy.implemented)

    def test_changing_expedition_config_does_not_leak_into_other_tasks(self):
        altered = replace(
            RUNTIME_CONFIG,
            all_fleet_ids=(1, 2, 3, 4, 5),
            non_dispatchable_fleet_ids=(1, 5),
            resupply_verified_fleet_ids=(2, 3, 4),
            monitor_candidate_fleet_ids=(2, 3, 4),
        )
        changed = fleet_policy_for(TaskKind.EXPEDITION, config=altered)
        self.assertEqual(changed.reserved_fleet_ids, (1, 5))
        self.assertEqual(changed.dispatchable_fleet_ids, (2, 3, 4))
        self.assertEqual(
            changed.resupply_verified_fleet_ids,
            altered.resupply_verified_fleet_ids,
        )
        for kind in ROADMAP_KINDS:
            with self.subTest(kind=kind):
                untouched = fleet_policy_for(kind, config=altered)
                self.assertEqual(fleet_policy_for(kind), untouched)
                self.assertEqual(untouched.dispatchable_fleet_ids, ())

    def test_a_forged_mismatched_policy_is_detectable(self):
        """`frozen=True` does not stop `replace` from forging another kind's policy.

        That is exactly why TaskRegistration validates the (kind, policy)
        pairing itself; see tests/test_multi_task_runtime.py.
        """
        expedition = fleet_policy_for(TaskKind.EXPEDITION)
        self.assertIs(expedition.task_kind, TaskKind.EXPEDITION)
        forged = replace(expedition, task_kind=TaskKind.COMBAT)
        self.assertIsNot(forged, expedition)
        self.assertIs(forged.task_kind, TaskKind.COMBAT)
        self.assertEqual(forged.dispatchable_fleet_ids, expedition.dispatchable_fleet_ids)


class DefaultCompatibilityTests(unittest.TestCase):
    """New scoping must not silently change any existing default behaviour."""

    def test_describe_shared_capabilities_keeps_its_legacy_string_shape(self):
        lines = describe_shared_capabilities()
        self.assertEqual(len(lines), len(SHARED_CAPABILITIES))
        self.assertTrue(all(isinstance(line, str) and ":" in line for line in lines))
        self.assertTrue(any("ui_operation_lock" in line for line in lines))
        self.assertTrue(any("full_supply" in line for line in lines))

    def test_default_fleet_resolution_is_expedition_with_and_without_the_argument(self):
        implicit = resolve_fleet_availability((1, 2, 3, 4), (2, 3))
        explicit = resolve_fleet_availability(
            (1, 2, 3, 4), (2, 3), task_kind=TaskKind.EXPEDITION
        )
        self.assertEqual(implicit, explicit)
        self.assertEqual(implicit.task_kind, TaskKind.EXPEDITION.value)

    def test_build_runtime_and_monitor_runtime_default_to_expedition(self):
        from run_continuous_expedition_monitor import MonitorRuntime, build_runtime

        self.assertIs(
            inspect.signature(build_runtime).parameters["task_kind"].default,
            TaskKind.EXPEDITION,
        )
        self.assertEqual(MonitorRuntime.__dataclass_fields__["task_kind"].default,
                         TaskKind.EXPEDITION.value)

    def test_legacy_event_payload_is_byte_identical_without_task_kind(self):
        # Complementary to the disk-backed test above: the in-memory payload
        # contract is that no task_kind is injected unless explicitly requested.
        with tempfile.TemporaryDirectory() as temporary:
            sink = SqliteDispatchEventLog(str(Path(temporary) / "compat.sqlite3"))
            sink("legacy", {"fleet_id": 2})
            self.assertEqual(sink.load_all()[0]["fields"], {"fleet_id": 2})

    def test_tasks_command_still_lists_the_legacy_keywords(self):
        buffer = io.StringIO()
        with patch.object(cli.importlib, "import_module") as importer:
            with redirect_stdout(buffer):
                self.assertEqual(cli.main(["tasks"]), 0)
        importer.assert_not_called()
        output = buffer.getvalue()
        for keyword in ("expedition", "combat", "sparkle", "composition", "quests",
                        "IMPLEMENTED", "NOT IMPLEMENTED (fail-closed)",
                        "ui_operation_lock", "full_supply", "task_neutral",
                        "needs_decoupling", "task_specific"):
            self.assertIn(keyword, output)


if __name__ == "__main__":
    unittest.main()
