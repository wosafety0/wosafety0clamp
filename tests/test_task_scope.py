"""Multi-task scoping: expedition policy must not become a global game law."""
from __future__ import annotations

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
    TaskKind,
    TaskScopeError,
    coerce_task_kind,
    declared_task_kinds,
    describe_shared_capabilities,
    fleet_policy_for,
    implemented_task_kinds,
    require_task_enabled,
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


class SharedCapabilityTests(unittest.TestCase):
    def test_shared_infrastructure_is_declared_and_task_neutral(self):
        lines = describe_shared_capabilities()
        self.assertEqual(len(lines), 6)
        self.assertTrue(any("ui_operation_lock" in line for line in lines))
        self.assertTrue(any("full_supply" in line for line in lines))
        self.assertTrue(all(":" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
