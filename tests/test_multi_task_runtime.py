"""Offline tests for the DESIGN-ONLY multi-task runtime interfaces.

Nothing here connects to Poi/CDP, performs a click, or writes a production
SQLite database. The last test enforces that explicitly.
See docs/DESIGN_multi_task_runtime.md.
"""
from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from kancollebot.task_runtime import (
    FleetLeaseError,
    FleetLeaseTable,
    InMemoryUiArbiter,
    LeaseConflictError,
    LeaseOwnerError,
    RegistrationStatus,
    TaskRegistration,
    TaskRegistry,
    TaskRegistryError,
    UiArbiter,
    UiDecision,
    UiPriority,
    UiRequest,
    ui_operation_label,
)
from kancollebot.task_scope import TaskKind, fleet_policy_for


def enabled_expedition(
    *, capabilities: tuple[str, ...] = ("poi_ui_mutex", "visual_dependency_gate")
) -> TaskRegistration:
    return TaskRegistration(
        task_kind=TaskKind.EXPEDITION,
        status=RegistrationStatus.ENABLED,
        fleet_policy=fleet_policy_for(TaskKind.EXPEDITION),
        screen_ids=("HOME_PORT", "EXPEDITION_LIST"),
        visual_gates_verified=True,
        required_capabilities=capabilities,
    )


def registry_with_expedition() -> TaskRegistry:
    registry = TaskRegistry()
    registry.register(enabled_expedition())
    return registry


def request(
    kind: TaskKind = TaskKind.EXPEDITION,
    *,
    priority: UiPriority = UiPriority.AUTOMATION,
    run_id: str = "run-a",
    visual_gate_verified: bool = True,
) -> UiRequest:
    return UiRequest(
        task_kind=kind,
        operation="dispatch",
        priority=priority,
        run_id=run_id,
        visual_gate_verified=visual_gate_verified,
    )


class TaskRegistryTests(unittest.TestCase):
    def test_empty_registry_refuses_every_task(self):
        registry = TaskRegistry()
        self.assertEqual(len(registry), 0)
        with self.assertRaisesRegex(TaskRegistryError, "not registered"):
            registry.require_enabled(TaskKind.EXPEDITION)

    def test_expedition_registers_as_enabled_with_neutral_capabilities(self):
        registry = registry_with_expedition()
        registration = registry.require_enabled("expedition")
        self.assertIs(registration.status, RegistrationStatus.ENABLED)
        self.assertEqual(registry.describe()[0].split()[0], "expedition")

    def test_forged_policy_from_another_task_is_rejected(self):
        """A combat-labelled policy with EXPEDITION content must still be refused.

        `dataclasses.replace` can change only the label while keeping Fleet 1's
        reservation and the expedition ceiling; checking `task_kind` alone is
        not enough, so register() demands the exact task_scope-derived policy.
        """
        forged_policy = replace(
            fleet_policy_for(TaskKind.EXPEDITION), task_kind=TaskKind.COMBAT
        )
        forged = TaskRegistration(
            task_kind=TaskKind.COMBAT,
            status=RegistrationStatus.DECLARED,
            fleet_policy=forged_policy,
        )
        self.assertIs(forged.fleet_policy.task_kind, TaskKind.COMBAT)
        self.assertEqual(forged.fleet_policy.dispatchable_fleet_ids, (2, 3, 4))
        with self.assertRaisesRegex(TaskRegistryError, "must not borrow"):
            TaskRegistry().register(forged)
        # And the reverse: expedition must not be given a combat policy.
        with self.assertRaisesRegex(TaskRegistryError, "must not borrow"):
            TaskRegistration(
                task_kind=TaskKind.EXPEDITION,
                status=RegistrationStatus.DECLARED,
                fleet_policy=fleet_policy_for(TaskKind.COMBAT),
            )

    def test_unimplemented_task_cannot_be_marked_enabled(self):
        with self.assertRaisesRegex(TaskRegistryError, "not implemented"):
            TaskRegistration(
                task_kind=TaskKind.COMBAT,
                status=RegistrationStatus.ENABLED,
                fleet_policy=fleet_policy_for(TaskKind.COMBAT),
                screen_ids=("SORTIE_MENU",),
                visual_gates_verified=True,
            )

    def test_declared_roadmap_task_can_be_registered_but_never_enabled(self):
        registry = TaskRegistry()
        registration = TaskRegistration(
            task_kind=TaskKind.COMBAT,
            status=RegistrationStatus.DECLARED,
            fleet_policy=fleet_policy_for(TaskKind.COMBAT),
            required_capabilities=("fleet_snapshot", "resupply_preflight"),
        )
        registry.register(registration)
        self.assertIs(registry.get(TaskKind.COMBAT), registration)
        with self.assertRaisesRegex(TaskRegistryError, "not enabled"):
            registry.require_enabled(TaskKind.COMBAT)

    def test_enabled_task_cannot_depend_on_expedition_bound_capability(self):
        with self.assertRaisesRegex(TaskRegistryError, "non-task-neutral"):
            enabled_expedition(capabilities=("poi_ui_mutex", "fleet_snapshot"))

    def test_calibrated_requires_real_visual_evidence(self):
        with self.assertRaisesRegex(TaskRegistryError, "ENABLED requires"):
            TaskRegistration(
                task_kind=TaskKind.EXPEDITION,
                status=RegistrationStatus.ENABLED,
                fleet_policy=fleet_policy_for(TaskKind.EXPEDITION),
                required_capabilities=(),
            )
        with self.assertRaisesRegex(TaskRegistryError, "CALIBRATED requires"):
            TaskRegistration(
                task_kind=TaskKind.COMBAT,
                status=RegistrationStatus.CALIBRATED,
                fleet_policy=fleet_policy_for(TaskKind.COMBAT),
            )

    def test_authorization_cannot_be_disabled_and_unknown_capabilities_rejected(self):
        with self.assertRaisesRegex(TaskRegistryError, "authorization_required"):
            replace(enabled_expedition(), authorization_required=False)
        with self.assertRaisesRegex(TaskRegistryError, "unknown required capabilities"):
            replace(enabled_expedition(), required_capabilities=("typo_capability",))

    def test_duplicate_registration_is_idempotent_or_rejected(self):
        registry = registry_with_expedition()
        self.assertIs(registry.register(enabled_expedition()), registry.get(TaskKind.EXPEDITION))
        with self.assertRaisesRegex(TaskRegistryError, "already registered"):
            registry.register(replace(enabled_expedition(), note="different"))

    def test_registering_expedition_does_not_grant_roadmap_tasks_a_policy(self):
        registry = registry_with_expedition()
        for kind in (TaskKind.COMBAT, TaskKind.SPARKLE, TaskKind.COMPOSITION, TaskKind.QUESTS):
            with self.subTest(kind=kind):
                policy = fleet_policy_for(kind)
                self.assertEqual(policy.dispatchable_fleet_ids, ())
                self.assertIsNone(registry.get(kind))


class FleetLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
        self.now = self.base
        # A permissive registry STAND-IN: the lease layer must be tested on its
        # own, without the (correct) task_scope gate blocking roadmap tasks.
        self.registry = Mock()
        self.table = FleetLeaseTable(
            registry=self.registry, clock=lambda: self.now
        )

    def _acquire(self, kind: TaskKind, fleet_id: int, run_id: str):
        return self.table.acquire(
            kind, fleet_id, owner_run_id=run_id, purpose=f"{kind.value}-work",
            ttl_seconds=60,
        )

    def test_two_tasks_cannot_own_the_same_fleet(self):
        self._acquire(TaskKind.EXPEDITION, 2, "expedition-run")
        with self.assertRaisesRegex(LeaseConflictError, "cannot belong to two tasks"):
            self._acquire(TaskKind.COMBAT, 2, "combat-run")
        kinds = [call.args[0] for call in self.registry.require_enabled.call_args_list]
        self.assertIn(TaskKind.COMBAT, kinds)

    def test_fleet_1_is_not_forbidden_by_the_lease_layer(self):
        """Strategy isolation: Fleet 1 is reserved FOR EXPEDITIONS, not globally."""
        lease = self._acquire(TaskKind.COMBAT, 1, "combat-run")
        self.assertEqual(lease.fleet_id, 1)
        self.assertIs(lease.task_kind, TaskKind.COMBAT)

    def test_different_fleets_can_be_leased_concurrently(self):
        self._acquire(TaskKind.EXPEDITION, 2, "expedition-run")
        self._acquire(TaskKind.COMBAT, 1, "combat-run")
        self._acquire(TaskKind.COMPOSITION, 3, "composition-run")
        self.assertEqual(len(self.table.active_leases()), 3)

    def test_expired_lease_still_blocks_until_explicit_reclaim(self):
        lease = self._acquire(TaskKind.EXPEDITION, 2, "expedition-run")
        self.now = self.base + timedelta(seconds=61)
        self.assertTrue(lease.is_expired(self.now))
        with self.assertRaisesRegex(LeaseConflictError, "reclaim_expired"):
            self._acquire(TaskKind.COMBAT, 2, "combat-run")
        self.assertEqual(self.table.reclaim_expired(), (lease.lease_id,))
        self.assertEqual(self._acquire(TaskKind.COMBAT, 2, "combat-run").fleet_id, 2)

    def test_renew_and_release_require_the_owner(self):
        lease = self._acquire(TaskKind.EXPEDITION, 2, "expedition-run")
        with self.assertRaisesRegex(LeaseOwnerError, "owned by"):
            self.table.renew(lease.lease_id, "someone-else", 60)
        with self.assertRaisesRegex(LeaseOwnerError, "owned by"):
            self.table.release(lease.lease_id, "someone-else")
        renewed = self.table.renew(lease.lease_id, "expedition-run", 120)
        self.assertEqual(renewed.ttl_seconds, 120)
        self.assertEqual(
            self.table.release(lease.lease_id, "expedition-run").lease_id,
            lease.lease_id,
        )
        self.assertIsNone(self.table.holder(2))

    def test_unobserved_fleet_is_refused_for_a_three_fleet_account(self):
        table = FleetLeaseTable(
            registry=self.registry, unlocked_fleet_ids=(1, 2, 3),
            clock=lambda: self.now,
        )
        with self.assertRaisesRegex(FleetLeaseError, "not unlocked"):
            table.acquire(
                TaskKind.EXPEDITION, 4, owner_run_id="run", purpose="expedition",
                ttl_seconds=60,
            )

    def test_invalid_ttl_owner_and_clock_are_refused(self):
        for bad_ttl in (0, -1, float("inf"), float("nan"), True, "60"):
            with self.subTest(bad_ttl=bad_ttl):
                with self.assertRaisesRegex(FleetLeaseError, "ttl_seconds"):
                    self.table.acquire(
                        TaskKind.EXPEDITION, 2, owner_run_id="run",
                        purpose="work", ttl_seconds=bad_ttl,
                    )
        naive = FleetLeaseTable(registry=self.registry, clock=lambda: datetime(2026, 1, 1))
        with self.assertRaisesRegex(FleetLeaseError, "timezone-aware"):
            naive.acquire(
                TaskKind.EXPEDITION, 2, owner_run_id="run", purpose="work",
                ttl_seconds=60,
            )

    def test_default_gate_still_blocks_unimplemented_tasks(self):
        table = FleetLeaseTable(clock=lambda: self.now)
        with self.assertRaisesRegex(FleetLeaseError, "not enabled"):
            table.acquire(
                TaskKind.COMBAT, 1, owner_run_id="run", purpose="combat",
                ttl_seconds=60,
            )


class UiArbitrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = registry_with_expedition()
        self.arbiter = InMemoryUiArbiter(registry=self.registry)

    def test_protocol_conformance(self):
        self.assertIsInstance(self.arbiter, UiArbiter)

    def test_unregistered_or_declared_task_is_refused(self):
        result = self.arbiter.arbitrate(request(TaskKind.COMBAT))
        self.assertIs(result.decision, UiDecision.REJECTED_TASK_DISABLED)
        self.assertFalse(result.may_interrupt_in_flight_click)

    def test_missing_visual_gate_is_refused(self):
        result = self.arbiter.arbitrate(request(visual_gate_verified=False))
        self.assertIs(result.decision, UiDecision.REJECTED_NO_VISUAL_GATE)

    def test_no_holder_is_granted(self):
        result = self.arbiter.arbitrate(request())
        self.assertIs(result.decision, UiDecision.GRANTED)
        self.assertIsNone(result.holder_run_id)

    def test_equal_or_lower_priority_is_rejected_busy(self):
        holder = request(run_id="holder", priority=UiPriority.AUTOMATION)
        for priority in (UiPriority.AUTOMATION, UiPriority.OPERATOR):
            with self.subTest(priority=priority):
                result = self.arbiter.arbitrate(
                    request(run_id="newcomer", priority=priority), holder
                )
                # OPERATOR ranks above AUTOMATION, but only SAFETY may request a
                # handover; everything else is refused (there is no queue yet).
                self.assertIs(result.decision, UiDecision.REJECTED_BUSY)

    def test_safety_can_request_handover_but_never_interrupt_a_click(self):
        holder = request(run_id="holder", priority=UiPriority.AUTOMATION)
        result = self.arbiter.arbitrate(
            request(run_id="safety", priority=UiPriority.SAFETY), holder
        )
        self.assertIs(result.decision, UiDecision.PREEMPT_REQUESTED)
        self.assertFalse(result.may_interrupt_in_flight_click)
        self.assertEqual(result.holder_run_id, "holder")
        self.assertIn("AFTER the current operation", result.reason)

    def test_label_carries_task_attribution(self):
        self.assertEqual(
            ui_operation_label(request(run_id="run-42")),
            "expedition:dispatch:run-42",
        )

    def test_invalid_requests_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "operation"):
            replace(request(), operation="")
        with self.assertRaisesRegex(ValueError, "run_id"):
            replace(request(), run_id="")
        with self.assertRaisesRegex(ValueError, "timeout_seconds"):
            replace(request(), timeout_seconds=float("inf"))
        with self.assertRaisesRegex(ValueError, "priority"):
            replace(request(), priority=0)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            self.arbiter.arbitrate("not-a-request")  # type: ignore[arg-type]


class OfflineIsolationTests(unittest.TestCase):
    """The whole design reference must stay free of live side effects."""

    def test_registry_lease_and_arbitration_never_touch_poi_or_sqlite(self):
        with (
            patch("sqlite3.connect") as sqlite_connect,
            patch("kancollebot.adapters.poi.ui_operation_lock.poi_ui_operation") as mutex,
        ):
            registry = registry_with_expedition()
            table = FleetLeaseTable(registry=registry)
            lease = table.acquire(
                TaskKind.EXPEDITION, 2, owner_run_id="run-a",
                purpose="offline-check", ttl_seconds=60,
            )
            arbiter = InMemoryUiArbiter(registry=registry)
            result = arbiter.arbitrate(request(run_id="run-a"))
            self.assertTrue(lease.is_active())
            self.assertIs(result.decision, UiDecision.GRANTED)
            self.assertEqual(len(table.snapshot()), 1)
        sqlite_connect.assert_not_called()
        mutex.assert_not_called()


if __name__ == "__main__":
    unittest.main()
