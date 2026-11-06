"""Pure, fail-closed classification of passive network/recovery signals.

This module does not reconnect, refresh, click, replay requests, or authorize
resuming the expedition supervisor. It only converts observed signals into
a conservative decision for a higher-level recovery coordinator.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class RecoveryCategory(str, Enum):
    HEALTHY_WITNESS = "healthy_witness"
    NO_NATURAL_WITNESS = "no_natural_witness"
    CAT_SCREEN = "cat_screen"
    CDP_UNAVAILABLE = "cdp_unavailable"
    OBSERVER_UNAVAILABLE = "observer_unavailable"
    SERVER_REJECTED = "server_rejected"
    LOGIN_REQUIRED = "login_required"
    MAINTENANCE = "maintenance"
    UNKNOWN = "unknown"
    INACTIVE = "inactive"


@dataclass(frozen=True, slots=True)
class RecoveryClassification:
    """A classification only; it is never permission to resume automation."""

    category: RecoveryCategory
    pause_required: bool
    reconnect_observer: bool
    manual_required: bool
    reason: str

    @property
    def resume_allowed(self) -> bool:
        """Network classification alone can never authorize business resumption."""
        return False


_CDP_UNAVAILABLE_STATUSES = frozenset({
    "target_discovery_failed",
    "cdp_connection_failed",
    "cdp_connection_closed",
})
_OBSERVER_UNAVAILABLE_STATUSES = frozenset({
    "process_timeout",
    "launch_failed",
    "observer_failed",
    "script_missing",
    "cdp_protocol_error",
    "cdp_command_failed",
})
_INVALID_STATUSES = frozenset({
    "output_too_large",
    "invalid_output",
    "invalid_json",
    "invalid_shape",
    "response_rejected",
    "no_capture",
})


def classify_recovery_signals(
    *,
    observer_status: str = "not_started",
    cdp_connected: bool | None = None,
    target_crashed: bool = False,
    visual_state: str = "unknown",
    api_result: Any = None,
    login_required: bool = False,
    maintenance: bool = False,
) -> RecoveryClassification:
    """Classify observed signals without inferring more than the evidence says.

    Priority is intentional: explicit server/session failures outrank transport
    symptoms; a missing natural response is not itself proof of a network fault.
    Accepted visual states are normal, cat and unknown.
    API result is inspected only when supplied by an upstream parser.
    """
    status = observer_status.strip().lower() if isinstance(observer_status, str) else ""
    visual = visual_state.strip().lower() if isinstance(visual_state, str) else "unknown"

    if api_result == 201:
        return RecoveryClassification(
            RecoveryCategory.SERVER_REJECTED, True, False, True,
            "api_result_201_circuit_open",
        )
    if login_required or visual == "login":
        return RecoveryClassification(
            RecoveryCategory.LOGIN_REQUIRED, True, False, True, "login_required",
        )
    if maintenance or visual == "maintenance":
        return RecoveryClassification(
            RecoveryCategory.MAINTENANCE, True, False, True, "maintenance_detected",
        )
    if api_result is not None and api_result != 1:
        return RecoveryClassification(
            RecoveryCategory.SERVER_REJECTED, True, False, True,
            "non_success_api_result",
        )
    if target_crashed:
        return RecoveryClassification(
            RecoveryCategory.CDP_UNAVAILABLE, True, True, True, "cdp_target_crashed",
        )
    if cdp_connected is False or status in _CDP_UNAVAILABLE_STATUSES:
        return RecoveryClassification(
            RecoveryCategory.CDP_UNAVAILABLE, True, True, False,
            "cdp_transport_unavailable",
        )
    if visual == "cat":
        return RecoveryClassification(
            RecoveryCategory.CAT_SCREEN, True, False, False,
            "cat_screen_requires_observation",
        )
    if status in _OBSERVER_UNAVAILABLE_STATUSES:
        return RecoveryClassification(
            RecoveryCategory.OBSERVER_UNAVAILABLE, True, True, False,
            f"observer_unavailable:{status}",
        )
    if status == "captured":
        return RecoveryClassification(
            RecoveryCategory.HEALTHY_WITNESS, False, False, False,
            "natural_port_witness_captured",
        )
    if status == "natural_response_timeout":
        return RecoveryClassification(
            RecoveryCategory.NO_NATURAL_WITNESS, False, False, False,
            "no_natural_request_observed_not_proof_of_network_failure",
        )
    if status in {"not_started", "cancelled"}:
        return RecoveryClassification(
            RecoveryCategory.INACTIVE, False, False, False, f"observer_{status}",
        )
    if status in _INVALID_STATUSES or not status:
        return RecoveryClassification(
            RecoveryCategory.UNKNOWN, True, False, True,
            f"untrusted_observer_signal:{status or \"missing\"}",
        )
    return RecoveryClassification(
        RecoveryCategory.UNKNOWN, True, False, True,
        f"unrecognized_observer_status:{status}",
    )
