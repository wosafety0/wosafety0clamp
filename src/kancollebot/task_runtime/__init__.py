"""Offline design reference for the future multi-task runtime.

NOT wired into any production entry point. Importing this package does not
connect to Poi, does not perform clicks and does not change existing defaults.
See docs/DESIGN_multi_task_runtime.md.
"""
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
