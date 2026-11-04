"""Offline design reference and assembly layer for the future multi-task runtime.

``context.py`` assembles the registry, fleet leases and UI arbiter around one
shared truth source and is exposed through the read-only ``kancollebot tasks``
command. NO production UI/click entry point consumes it yet: existing expedition
paths keep their own gates, and importing this package does not connect to Poi,
perform clicks or change existing defaults. See docs/DESIGN_multi_task_runtime.md.
"""
from .context import TaskRuntimeContext, build_task_runtime_context
from .fleet_lease import (
    FleetLease,
    FleetLeaseError,
    FleetLeaseTable,
    LeaseConflictError,
    LeaseOwnerError,
)
from .registry import (
    RegistrationStatus,
    TaskRegistration,
    TaskRegistry,
    TaskRegistryError,
)
from .ui_arbitration import (
    InMemoryUiArbiter,
    UiArbiter,
    UiArbitrationResult,
    UiDecision,
    UiPriority,
    UiRequest,
    ui_operation_label,
)

__all__ = [
    "TaskRuntimeContext",
    "build_task_runtime_context",
    "FleetLease",
    "FleetLeaseError",
    "FleetLeaseTable",
    "LeaseConflictError",
    "LeaseOwnerError",
    "RegistrationStatus",
    "TaskRegistration",
    "TaskRegistry",
    "TaskRegistryError",
    "InMemoryUiArbiter",
    "UiArbiter",
    "UiArbitrationResult",
    "UiDecision",
    "UiPriority",
    "UiRequest",
    "ui_operation_label",
]
