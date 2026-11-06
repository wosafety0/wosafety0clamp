"""Fail-closed classification and recovery gate for passive Poi observation.

This module is deliberately side-effect free: it never opens CDP, sends game
requests, refreshes the page, or performs UI input. Runtime wiring must feed it
a monotonically increasing observer-attempt revision and may resume automation
only after a fresh witness has been reconciled with durable expedition state.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ObserverFaultClass(str, Enum):
    HEALTHY = "healthy"
    SUSPECT = "suspect"
    DISCONNECTED = "disconnected"
    OBSERVER_UNAVAILABLE = "observer_unavailable"
    SERVER_REJECTED = "server_rejected"
    UNKNOWN = "unknown"


class RecoveryState(str, Enum):
    RUNNING = "running"
    SUSPECT = "suspect"
    PAUSED = "paused"
    RECONCILE_REQUIRED = "reconcile_required"
    MANUAL_REQUIRED = "manual_required"


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    state: RecoveryState
    fault_class: ObserverFaultClass
    observer_status: str
    revision: int
    changed: bool
    automation_allowed: bool
    reason: str


# A timeout only proves that no qualifying natural response was observed in
# the window. It does not prove the game server or local network is offline.
_SUSPECT_STATUSES = frozenset({
    "natural_response_timeout",
    "process_timeout",
    "no_capture",
})

_DISCONNECTED_STATUSES = frozenset({
    "target_discovery_failed",
    "cdp_connection_failed",
    "cdp_connection_closed",
})

_UNAVAILABLE_STATUSES = frozenset({
    "launch_failed",
    "script_missing",
    "observer_failed",
    "cdp_protocol_error",
    "cdp_command_failed",
    "output_too_large",
    "invalid_output",
    "invalid_json",
    "invalid_shape",
    "response_rejected",
})

_SERVER_REJECTED_STATUSES = frozenset({
    "server_rejected",
    "api_result_201",
    "login_expired",
    "maintenance",
})


def classify_observer_status(status: str) -> ObserverFaultClass:
    """Classify one observer status without overclaiming network failure."""
    if not isinstance(status, str) or not status.strip():
        return ObserverFaultClass.UNKNOWN
    normalized = status.strip().lower()
    if normalized == "captured":
        return ObserverFaultClass.HEALTHY
    if normalized in _SUSPECT_STATUSES:
        return ObserverFaultClass.SUSPECT
    if normalized in _DISCONNECTED_STATUSES:
        return ObserverFaultClass.DISCONNECTED
    if normalized in _SERVER_REJECTED_STATUSES:
        return ObserverFaultClass.SERVER_REJECTED
    if normalized in _UNAVAILABLE_STATUSES or normalized.startswith("observer_exception:"):
        return ObserverFaultClass.OBSERVER_UNAVAILABLE
    # Startup, cancellation, and future statuses are not evidence of a healthy
    # connection. They also are not sufficient to diagnose a network failure.
    return ObserverFaultClass.UNKNOWN


class NetworkRecoveryGate:
    """Stateful, revision-aware gate; repeated reads never amplify one signal.

    A captured witness after a fault does not reopen automation. It only moves
    the gate to RECONCILE_REQUIRED. The caller must complete the existing
    Action Journal / Dispatch Registry / Poi-state reconciliation and explicitly
    call mark_reconciled(True). Any ambiguity must remain manual.
    """

    def __init__(self) -> None:
        self._state = RecoveryState.RUNNING
        self._fault_class = ObserverFaultClass.UNKNOWN
        self._observer_status = "not_started"
        self._revision = -1
        self._reason = "no observer evidence has been received"

    @property
    def state(self) -> RecoveryState:
        return self._state

    @property
    def automation_allowed(self) -> bool:
        return self._state is RecoveryState.RUNNING

    @property
    def last_decision(self) -> RecoveryDecision:
        return RecoveryDecision(
            state=self._state,
            fault_class=self._fault_class,
            observer_status=self._observer_status,
            revision=self._revision,
            changed=False,
            automation_allowed=self.automation_allowed,
            reason=self._reason,
        )

    def observe(self, status: str, *, revision: int) -> RecoveryDecision:
        """Consume one new observer attempt; stale/duplicate revisions are inert."""
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ValueError("revision must be a non-negative integer")
        if not isinstance(status, str) or not status.strip():
            raise ValueError("status must be a non-empty string")
        if revision <= self._revision:
            return self.last_decision

        previous = self._state
        fault_class = classify_observer_status(status)
        self._revision = revision
        self._observer_status = status.strip().lower()
        self._fault_class = fault_class

        if fault_class is ObserverFaultClass.HEALTHY:
            if previous is RecoveryState.RUNNING:
                self._reason = "fresh natural port witness observed"
            elif previous is RecoveryState.MANUAL_REQUIRED:
                self._reason = "manual-required state is latched; witness cannot clear it"
            else:
                self._state = RecoveryState.RECONCILE_REQUIRED
                self._reason = "observer recovered; business-state reconciliation is required"
        elif fault_class is ObserverFaultClass.SUSPECT:
            if previous not in (RecoveryState.MANUAL_REQUIRED, RecoveryState.RECONCILE_REQUIRED):
                self._state = RecoveryState.SUSPECT
            self._reason = (
                "natural response was not observed; this is ambiguous and must not "
                "be treated as proof of network disconnection"
            )
        elif fault_class in (
            ObserverFaultClass.DISCONNECTED,
            ObserverFaultClass.OBSERVER_UNAVAILABLE,
        ):
            if previous is not RecoveryState.MANUAL_REQUIRED:
                self._state = RecoveryState.PAUSED
            self._reason = f"observer fault requires pause: {fault_class.value}"
        elif fault_class is ObserverFaultClass.SERVER_REJECTED:
            self._state = RecoveryState.MANUAL_REQUIRED
            self._reason = "server/session rejection requires manual handling; no retry or refresh"
        else:
            # Unknown is not proof of recovery. Preserve any existing pause.
            self._reason = "observer status is unknown; existing gate state is preserved"

        return RecoveryDecision(
            state=self._state,
            fault_class=fault_class,
            observer_status=self._observer_status,
            revision=self._revision,
            changed=self._state is not previous,
            automation_allowed=self.automation_allowed,
            reason=self._reason,
        )

    def mark_reconciled(self, *, safe: bool, reason: str = "") -> RecoveryDecision:
        """Release only after the caller proves the durable business state is safe."""
        if not isinstance(safe, bool):
            raise ValueError("safe must be a bool")
        if self._state is not RecoveryState.RECONCILE_REQUIRED:
            raise RuntimeError("business reconciliation is only valid after observer recovery")
        if safe:
            self._state = RecoveryState.RUNNING
            self._reason = reason.strip() or "business state reconciled; automation may resume"
        else:
            self._state = RecoveryState.MANUAL_REQUIRED
            self._reason = reason.strip() or "business-state reconciliation was inconclusive"
        return self.last_decision

    def require_manual(self, reason: str) -> RecoveryDecision:
        """Latch the gate closed until an operator creates a new run."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be a non-empty string")
        self._state = RecoveryState.MANUAL_REQUIRED
        self._reason = reason.strip()
        return self.last_decision
