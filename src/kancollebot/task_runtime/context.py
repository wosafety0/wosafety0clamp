"""Assembled offline task-runtime composition root (migration step D-0).

This is the first wiring step for the three DESIGN-ONLY interfaces: the lease
table and the UI arbiter now consume ONE shared ``TaskRegistry`` instead of
three independent truth sources. The assembly is exposed through the read-only
``kancollebot tasks`` command.

It is NOT a production UI gate. No production entry point calls it to authorize
a click; expedition still goes through ``task_scope.require_task_enabled`` and
the existing visual/UI-state gates. Constructing this context opens no Poi
session, holds no cross-process mutex, performs no click and writes no database.

Every task is registered ``DECLARED`` by default. ``ENABLED`` additionally
requires calibrated screen ids and real-client visual-gate evidence; reward
claim calibration (P1-1) and unattended-loop acceptance are still pending, so
granting it here would overstate the evidence. ``registry.require_enabled``
therefore fails closed for all tasks until that evidence exists.

See docs/DESIGN_multi_task_runtime.md for the remaining migration steps.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable

from kancollebot.runtime_config import RUNTIME_CONFIG, RuntimeConfig
from kancollebot.task_scope import (
    FleetPolicy,
    TaskKind,
    declared_task_kinds,
    fleet_policy_for,
    require_task_enabled,
)

from .fleet_lease import FleetLease, FleetLeaseTable, _utcnow
from .registry import RegistrationStatus, TaskRegistration, TaskRegistry
from .ui_arbitration import InMemoryUiArbiter, UiArbitrationResult, UiRequest


_DECLARED_NOTE = (
    "declared; ENABLED requires calibrated screen ids and real-client "
    "visual-gate evidence. Existing expedition paths keep their own gates."
)


@dataclass(frozen=True, slots=True)
class TaskRuntimeContext:
    """One registry shared by the lease table and the UI arbiter."""

    registry: TaskRegistry
    leases: FleetLeaseTable
    arbiter: InMemoryUiArbiter

    def __post_init__(self) -> None:
        # The point of this assembly is a single source of truth. A mismatched
        # registry would silently reintroduce three independent truth sources.
        if getattr(self.leases, "_registry", None) is not self.registry:
            raise ValueError("lease table must share the context registry")
        if getattr(self.arbiter, "_registry", None) is not self.registry:
            raise ValueError("UI arbiter must share the context registry")

    def require_task_policy(self, task_kind: TaskKind | str) -> FleetPolicy:
        """Existing production gate; policy truth stays in task_scope."""
        return require_task_enabled(task_kind)

    def arbitrate(
        self, request: UiRequest, holder: UiRequest | None = None
    ) -> UiArbitrationResult:
        """Registry-backed UI admission decision (no click is performed)."""
        return self.arbiter.arbitrate(request, holder=holder)

    def acquire_lease(
        self,
        task_kind: TaskKind | str,
        fleet_id: int,
        *,
        owner_run_id: str,
        purpose: str,
        ttl_seconds: float,
        unlocked_fleet_ids: Iterable[int] | None = None,
    ) -> FleetLease:
        """Registry-backed fleet lease (memory only, no persistent store)."""
        return self.leases.acquire(
            task_kind,
            fleet_id,
            owner_run_id=owner_run_id,
            purpose=purpose,
            ttl_seconds=ttl_seconds,
            unlocked_fleet_ids=unlocked_fleet_ids,
        )

    def describe(self) -> tuple[str, ...]:
        """Read-only summary for the CLI; never changes runtime state."""
        lines = list(self.registry.describe())
        lines.append(f"active_leases={len(self.leases.active_leases())}")
        lines.append("click_path_wired=False (production gates unchanged)")
        return tuple(lines)


def build_task_runtime_context(
    *,
    config: RuntimeConfig = RUNTIME_CONFIG,
    clock: Callable[[], datetime] = _utcnow,
) -> TaskRuntimeContext:
    """Assemble the three interfaces around one all-DECLARED registry.

    ``clock`` is injectable so lease expiry stays offline-testable. There is
    deliberately no switch that marks a task ENABLED: that state must come from
    real-client calibration evidence, not from a constructor argument.
    """
    registry = TaskRegistry(config=config)
    for kind in declared_task_kinds():
        registry.register(
            TaskRegistration(
                task_kind=kind,
                status=RegistrationStatus.DECLARED,
                fleet_policy=fleet_policy_for(kind, config=config),
                note=_DECLARED_NOTE,
            )
        )
    leases = FleetLeaseTable(config=config, registry=registry, clock=clock)
    arbiter = InMemoryUiArbiter(registry=registry)
    return TaskRuntimeContext(registry=registry, leases=leases, arbiter=arbiter)
