"""Offline tests for the assembled task-runtime composition root (step D-0).

Nothing here connects to Poi/CDP, performs a click, holds the cross-process UI
mutex or writes a production SQLite database. The last test asserts that
explicitly. See docs/DESIGN_multi_task_runtime.md.
"""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from kancollebot import cli
from kancollebot.task_runtime import (
    FleetLeaseError,
    FleetLeaseTable,
    InMemoryUiArbiter,
    RegistrationStatus,
    TaskRegistration,
    TaskRegistry,
    TaskRegistryError,
    TaskRuntimeContext,
    UiDecision,
    UiPriority,
    UiRequest,
    build_task_runtime_context,
)
from kancollebot.task_scope import (
    TaskKind,
    TaskScopeError,
    declared_task_kinds,
    fleet_policy_for,
    require_task_enabled,
)


def _lease_kwargs() -> dict[str, object]:
    return {
        "owner_run_id": "run-1",
        "purpose": "offline verification",
        "ttl_seconds": 60.0,
    }


def _request(kind: TaskKind = TaskKind.EXPEDITION) -> UiRequest:
    return UiRequest(
        task_kind=kind,
        operation="dispatch",
        priority=UiPriority.AUTOMATION,
        run_id="run-1",
        visual_gate_verified=True,
    )


class AssemblyTests(unittest.TestCase):
    def test_every_declared_task_is_registered_with_its_own_derived_policy(self):
        context = build_task_runtime_context()
        self.assertEqual(len(context.registry), len(declared_task_kinds()))
        for kind in declared_task_kinds():
            registration = context.registry.get(kind)
            self.assertIsNotNone(registration)
            self.assertEqual(registration.status, RegistrationStatus.DECLARED)
            # No task inherits the expedition fleet policy.
            self.assertEqual(registration.fleet_policy, fleet_policy_for(kind))
            self.assertFalse(registration.visual_gates_verified)
            self.assertEqual(registration.screen_ids, ())

    def test_three_interfaces_share_exactly_one_registry(self):
        context = build_task_runtime_context()
        self.assertIs(context.leases._registry, context.registry)
        self.assertIs(context.arbiter._registry, context.registry)

    def test_mismatched_registry_is_rejected(self):
        with self.assertRaises(ValueError):
            TaskRuntimeContext(
                registry=TaskRegistry(),
                leases=FleetLeaseTable(),
                arbiter=InMemoryUiArbiter(),
            )

    def test_no_task_is_enabled_without_real_client_evidence(self):
        context = build_task_runtime_context()
        for kind in declared_task_kinds():
            with self.assertRaises(TaskRegistryError):
                context.registry.require_enabled(kind)

    def test_production_task_gate_is_unchanged_and_still_delegates(self):
        context = build_task_runtime_context()
        self.assertEqual(
            context.require_task_policy(TaskKind.EXPEDITION),
            require_task_enabled(TaskKind.EXPEDITION),
        )
        with self.assertRaises(TaskScopeError):
            context.require_task_policy(TaskKind("combat"))

    def test_declared_registry_blocks_leases_and_ui_admission(self):
        context = build_task_runtime_context()
        with self.assertRaises(FleetLeaseError):
            context.acquire_lease(TaskKind.EXPEDITION, 2, **_lease_kwargs())
        result = context.arbitrate(_request())
        self.assertEqual(result.decision, UiDecision.REJECTED_TASK_DISABLED)
        self.assertFalse(result.may_interrupt_in_flight_click)
        self.assertEqual(len(context.leases.active_leases()), 0)

    def test_read_only_summary_reports_no_wired_click_path(self):
        context = build_task_runtime_context()
        summary = "\n".join(context.describe())
        self.assertIn("declared", summary)
        self.assertIn("active_leases=0", summary)
        self.assertIn("click_path_wired=False", summary)

    def test_offline_enabled_expedition_drives_the_whole_chain(self):
        # Offline-only construction. No production entry point marks a task
        # ENABLED; this proves the assembled chain works once evidence exists.
        registry = TaskRegistry()
        registry.register(
            TaskRegistration(
                task_kind=TaskKind.EXPEDITION,
                status=RegistrationStatus.ENABLED,
                fleet_policy=fleet_policy_for(TaskKind.EXPEDITION),
                screen_ids=("HOME_PORT", "EXPEDITION_LIST"),
                visual_gates_verified=True,
                required_capabilities=(
                    "poi_ui_mutex",
                    "visual_dependency_gate",
                    "append_only_event_sink",
                ),
            )
        )
        context = TaskRuntimeContext(
            registry=registry,
            leases=FleetLeaseTable(registry=registry),
            arbiter=InMemoryUiArbiter(registry=registry),
        )
        self.assertEqual(context.arbitrate(_request()).decision, UiDecision.GRANTED)
        lease = context.acquire_lease(TaskKind.EXPEDITION, 2, **_lease_kwargs())
        self.assertEqual(lease.task_kind, TaskKind.EXPEDITION)
        self.assertEqual(len(context.leases.active_leases()), 1)
        # The mutual-exclusion key is fleet_id, not task_kind.
        with self.assertRaises(FleetLeaseError):
            context.acquire_lease(
                TaskKind.EXPEDITION,
                2,
                owner_run_id="run-2",
                purpose="second holder",
                ttl_seconds=60.0,
            )

    def test_cli_tasks_exposes_registration_without_composing_a_runtime(self):
        buffer = io.StringIO()
        with patch.object(cli.importlib, "import_module") as importer:
            with redirect_stdout(buffer):
                self.assertEqual(cli.main(["tasks"]), 0)
        importer.assert_not_called()
        output = buffer.getvalue()
        self.assertIn("Task runtime registration", output)
        self.assertIn("declared", output)
        self.assertIn("click_path_wired=False", output)
        # The original task-scope view must remain available.
        self.assertIn("IMPLEMENTED", output)
        self.assertIn("task_neutral", output)

    def test_assembly_and_queries_have_no_external_side_effects(self):
        with patch("sqlite3.connect") as sqlite_connect, patch(
            "kancollebot.adapters.poi.ui_operation_lock.poi_ui_operation"
        ) as ui_lock:
            context = build_task_runtime_context()
            context.describe()
            context.arbitrate(_request())
            with self.assertRaises(FleetLeaseError):
                context.acquire_lease(TaskKind.EXPEDITION, 2, **_lease_kwargs())
            self.assertEqual(context.require_task_policy(TaskKind.EXPEDITION).task_kind,
                             TaskKind.EXPEDITION)
        sqlite_connect.assert_not_called()
        ui_lock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
