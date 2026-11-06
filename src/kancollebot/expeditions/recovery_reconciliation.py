"""Read-only proof that a paused expedition run has a fresh, reconciled state.

This module never mutates the scheduler or ledgers, never clicks, and never
releases RecoveryPauseGate. A successful report means the observed business
state is internally consistent; a separate, later phase must verify the
visible UI state and explicitly decide whether the gate may be released.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from kancollebot.expeditions.continuous_scheduler import FleetPhase, MissionTuple
from kancollebot.expeditions.recovery_classification import RecoveryPauseGate


@dataclass(frozen=True, slots=True)
class RecoveryFleetReconciliation:
    fleet_id: int
    scheduler_phase: str
    outcome: str
    dispatch_id: str | None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class RecoveryReconciliationReport:
    status: str
    generation: int
    paused_at_utc: datetime | None
    witness_received_at: datetime | None
    observed_at: datetime | None
    business_reconciled: bool
    manual_required: bool
    reason_codes: tuple[str, ...]
    fleets: tuple[RecoveryFleetReconciliation, ...]

    @property
    def resume_allowed(self) -> bool:
        """This report is evidence only; it can never release the pause gate."""
        return False


def _parse_witness_time(snapshot: Any) -> datetime | None:
    if not isinstance(snapshot, dict):
        return None
    source = snapshot.get("source")
    if not isinstance(source, dict):
        return None
    value = source.get("responseReceivedAt")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


class RecoveryReconciliationCoordinator:
    """Read current Poi state and durable dispatch evidence after a pause."""

    def __init__(
        self,
        *,
        recovery_gate: RecoveryPauseGate,
        snapshot_provider: Any,
        state_reader: Any,
        scheduler: Any,
        dispatch_registry: Any,
        intent_store: Any,
        action_journal: Any = None,
        max_state_age: timedelta = timedelta(seconds=30),
        clock: Any = None,
    ) -> None:
        if not isinstance(recovery_gate, RecoveryPauseGate):
            raise ValueError("recovery_gate must be a RecoveryPauseGate")
        if max_state_age < timedelta(0):
            raise ValueError("max_state_age must not be negative")
        for label, value, method in (
            ("snapshot_provider", snapshot_provider, "latest_snapshot"),
            ("state_reader", state_reader, "read_fleets"),
            ("scheduler", scheduler, "states"),
            ("dispatch_registry", dispatch_registry, "load_open"),
            ("intent_store", intent_store, "load_all"),
        ):
            if value is None or not hasattr(value, method):
                raise ValueError(f"{label} does not expose {method}")
        if action_journal is not None and not callable(
            getattr(action_journal, "load_unresolved", None)
        ):
            raise ValueError("action_journal must expose load_unresolved()")
        self.recovery_gate = recovery_gate
        self.snapshot_provider = snapshot_provider
        self.state_reader = state_reader
        self.scheduler = scheduler
        self.dispatch_registry = dispatch_registry
        self.intent_store = intent_store
        self.action_journal = action_journal
        self.max_state_age = max_state_age
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _report(
        self,
        *,
        status: str,
        paused_at: datetime | None,
        witness_at: datetime | None = None,
        observed_at: datetime | None = None,
        reasons: tuple[str, ...] = (),
        fleets: tuple[RecoveryFleetReconciliation, ...] = (),
    ) -> RecoveryReconciliationReport:
        manual = status == "MANUAL_REQUIRED"
        return RecoveryReconciliationReport(
            status=status,
            generation=self.recovery_gate.generation,
            paused_at_utc=paused_at,
            witness_received_at=witness_at,
            observed_at=observed_at,
            business_reconciled=(status == "RECONCILED"),
            manual_required=manual,
            reason_codes=reasons,
            fleets=fleets,
        )

    def evaluate(self) -> RecoveryReconciliationReport:
        """Evaluate without writing any ledger, scheduler state, or UI input."""
        if not self.recovery_gate.is_paused:
            return self._report(
                status="MANUAL_REQUIRED",
                paused_at=None,
                reasons=("recovery_gate_not_paused",),
            )
        paused_at = self.recovery_gate.paused_at_utc
        if paused_at is None:
            return self._report(
                status="MANUAL_REQUIRED",
                paused_at=None,
                reasons=("pause_timestamp_missing",),
            )

        try:
            snapshot = self.snapshot_provider.latest_snapshot
        except Exception:
            return self._report(
                status="MANUAL_REQUIRED",
                paused_at=paused_at,
                reasons=("snapshot_provider_unavailable",),
            )
        witness_at = _parse_witness_time(snapshot)
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            return self._report(
                status="MANUAL_REQUIRED",
                paused_at=paused_at,
                witness_at=witness_at,
                reasons=("reconciliation_clock_invalid",),
            )
        now = now.astimezone(timezone.utc)
        if witness_at is None or witness_at <= paused_at:
            return self._report(
                status="WAITING_FOR_WITNESS",
                paused_at=paused_at,
                witness_at=witness_at,
                reasons=("awaiting_natural_witness_after_pause",),
            )
        if witness_at > now:
            return self._report(
                status="MANUAL_REQUIRED",
                paused_at=paused_at,
                witness_at=witness_at,
                reasons=("witness_timestamp_is_future_dated",),
            )

        try:
            observation = self.state_reader.read_fleets()
            observation.require_fresh(now=now, max_age=self.max_state_age)
        except Exception as exc:
            return self._report(
                status="MANUAL_REQUIRED",
                paused_at=paused_at,
                witness_at=witness_at,
                reasons=(f"poi_observation_untrusted:{type(exc).__name__}",),
            )
        observed_at = getattr(observation, "observed_at", None)
        if (
            not getattr(observation, "source_freshness_verified", False)
            or not isinstance(observed_at, datetime)
            or observed_at <= paused_at
            or not str(getattr(observation, "source", "")).startswith("poi-redux:")
        ):
            return self._report(
                status="MANUAL_REQUIRED",
                paused_at=paused_at,
                witness_at=witness_at,
                observed_at=observed_at if isinstance(observed_at, datetime) else None,
                reasons=("poi_state_not_freshly_witnessed_after_pause",),
            )

        try:
            scheduler_states = tuple(self.scheduler.states())
            open_dispatches = tuple(self.dispatch_registry.load_open())
            pending_intents = tuple(self.intent_store.load_all())
            unresolved_actions = (
                tuple(self.action_journal.load_unresolved())
                if self.action_journal is not None else ()
            )
        except Exception as exc:
            return self._report(
                status="MANUAL_REQUIRED",
                paused_at=paused_at,
                witness_at=witness_at,
                observed_at=observed_at,
                reasons=(f"durable_ledger_read_failed:{type(exc).__name__}",),
            )

        reasons: list[str] = []
        if pending_intents:
            reasons.append("pending_dispatch_intents")
        if unresolved_actions:
            reasons.append("unresolved_action_journal")

        observed_by_id = {}
        for item in getattr(observation, "payload", ()):
            fleet_id = getattr(item, "fleet_id", None)
            if isinstance(fleet_id, bool) or not isinstance(fleet_id, int) or fleet_id in observed_by_id:
                reasons.append("poi_fleet_identity_missing_or_duplicated")
                continue
            observed_by_id[fleet_id] = item

        dispatches_by_fleet: dict[int, list[Any]] = {}
        for record in open_dispatches:
            fleet_id = getattr(record, "fleet_id", None)
            if isinstance(fleet_id, bool) or not isinstance(fleet_id, int):
                reasons.append("dispatch_registry_fleet_id_invalid")
                continue
            dispatches_by_fleet.setdefault(fleet_id, []).append(record)

        scheduler_ids = {getattr(state, "fleet_id", None) for state in scheduler_states}
        if any(fleet_id not in scheduler_ids for fleet_id in dispatches_by_fleet):
            reasons.append("open_dispatch_outside_scheduler_scope")

        fleet_reports: list[RecoveryFleetReconciliation] = []
        for state in scheduler_states:
            fleet_id = state.fleet_id
            phase = state.phase
            phase_name = phase.value if isinstance(phase, FleetPhase) else str(phase)
            observed = observed_by_id.get(fleet_id)
            records = dispatches_by_fleet.get(fleet_id, [])
            record = records[0] if len(records) == 1 else None
            fleet_reason: str | None = None
            outcome = "UNKNOWN"

            if observed is None:
                fleet_reason = "poi_fleet_missing"
            elif len(records) > 1:
                fleet_reason = "multiple_open_dispatch_instances"
            elif phase in (FleetPhase.BLOCKED, FleetPhase.DISPATCHING):
                fleet_reason = f"scheduler_phase_requires_manual:{phase_name}"
            else:
                mission_status = getattr(observed, "mission_status", None)
                mission_id = getattr(observed, "mission_id", None)
                return_at = getattr(observed, "return_at", None)
                ship_ids = getattr(observed, "ship_ids", None)
                reserved = getattr(observed, "reserved", None)
                idle = (
                    mission_status == 0
                    and mission_id is None
                    and return_at is None
                    and reserved == 0
                )

                if phase in (FleetPhase.IDLE, FleetPhase.READY, FleetPhase.RESUPPLY_REQUIRED):
                    if records:
                        fleet_reason = "open_dispatch_conflicts_with_idle_scheduler_phase"
                    elif not idle:
                        fleet_reason = "poi_mission_active_without_scheduler_identity"
                    elif phase is FleetPhase.READY and not (
                        getattr(observed, "fuel_ready", False)
                        and getattr(observed, "ammo_ready", False)
                    ):
                        fleet_reason = "scheduler_ready_but_fleet_not_fully_supplied"
                    else:
                        outcome = (
                            "READY" if phase is FleetPhase.READY
                            else "RESUPPLY_REQUIRED" if phase is FleetPhase.RESUPPLY_REQUIRED
                            else "IDLE"
                        )
                elif phase in (
                    FleetPhase.EXPEDITION_ACTIVE,
                    FleetPhase.RETURNED_PENDING_UI,
                    FleetPhase.RETURN_RESULT,
                    FleetPhase.BONUS_ITEMS,
                ):
                    expected_status = (
                        1 if phase is FleetPhase.EXPEDITION_ACTIVE else 2
                    )
                    mission = state.mission
                    if (
                        record is None
                        or record.status != "confirmed"
                        or mission is None
                        or mission.mission_status != expected_status
                        or mission_status != expected_status
                        or mission_id != mission.mission_id
                        or tuple(ship_ids or ()) != mission.ship_ids
                        or reserved != mission.reserved
                        or return_at is None
                        or int(return_at.timestamp() * 1000) != mission.final_time_ms
                        or record.mission_id != mission.mission_id
                        or record.ship_ids != mission.ship_ids
                        or record.final_time_ms != mission.final_time_ms
                    ):
                        fleet_reason = "scheduler_poi_and_dispatch_identity_conflict"
                    elif phase is FleetPhase.EXPEDITION_ACTIVE and not state.home_port_verified:
                        fleet_reason = "active_fleet_missing_home_port_verification"
                    else:
                        outcome = (
                            "EXPEDITION_ACTIVE" if expected_status == 1
                            else "SETTLEMENT_PENDING"
                        )
                else:
                    fleet_reason = f"unsupported_scheduler_phase:{phase_name}"

            if fleet_reason is not None:
                reasons.append(f"fleet_{fleet_id}:{fleet_reason}")
            fleet_reports.append(
                RecoveryFleetReconciliation(
                    fleet_id=fleet_id,
                    scheduler_phase=phase_name,
                    outcome=outcome,
                    dispatch_id=getattr(record, "dispatch_id", None),
                    reason=fleet_reason,
                )
            )

        if reasons:
            return self._report(
                status="MANUAL_REQUIRED",
                paused_at=paused_at,
                witness_at=witness_at,
                observed_at=observed_at,
                reasons=tuple(dict.fromkeys(reasons)),
                fleets=tuple(fleet_reports),
            )
        return self._report(
            status="RECONCILED",
            paused_at=paused_at,
            witness_at=witness_at,
            observed_at=observed_at,
            fleets=tuple(fleet_reports),
        )
