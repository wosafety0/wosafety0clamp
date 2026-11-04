"""Read-only, source-fresh fleet reconciliation for the continuous scheduler."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from kancollebot.core.observed_state import ObservedState
from kancollebot.expeditions.continuous_scheduler import (
    ContinuousExpeditionScheduler,
    FleetPhase,
    FleetRuntime,
    MissionTuple,
)
from kancollebot.expeditions.mission_eligibility import (
    FleetCompositionSnapshot,
    MissionCompositionRequirement,
    MissionEligibilityDecision,
    evaluate_mission_composition,
)
from kancollebot.expeditions.planner import (
    ExpeditionDecision,
    ExpeditionFleetSnapshot,
    evaluate_expedition_fleet,
)


class ContinuousMonitorError(RuntimeError):
    """A fleet observation cannot be safely reconciled."""


class PoiFleetStateReader(Protocol):
    def read_fleets(self) -> ObservedState[tuple[ExpeditionFleetSnapshot, ...]]:
        ...


@dataclass(frozen=True, slots=True)
class FleetMonitorResult:
    fleet_id: int
    previous_phase: FleetPhase
    current_phase: FleetPhase
    action: str
    reason: str


@dataclass(frozen=True, slots=True)
class ContinuousMonitorReport:
    observed_at: datetime
    source: str
    fleets: tuple[FleetMonitorResult, ...]
    source_freshness_verified: bool = False


class ContinuousExpeditionMonitor:
    """Reconcile all dispatchable fleets from one fresh Poi Redux observation.

    This monitor performs no UI input and never treats a timer expiry as proof
    of return. A status-2 mission is accepted only when its identity matches a
    previously confirmed dispatch and the scheduler had already verified
    Home Port after that dispatch.
    """

    def __init__(
        self,
        *,
        scheduler: ContinuousExpeditionScheduler,
        state_reader: PoiFleetStateReader,
        mission_requirements: Mapping[int, MissionCompositionRequirement],
        event_sink: Callable[[str, Mapping[str, object]], None],
        max_state_age: timedelta = timedelta(seconds=30),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if set(mission_requirements) != set(scheduler.dispatch_fleet_ids):
            raise ValueError("mission_requirements must configure every and only dispatchable fleet")
        if any(not isinstance(item, MissionCompositionRequirement) for item in mission_requirements.values()):
            raise ValueError("each dispatch fleet requires a valid mission composition requirement")
        if not callable(event_sink):
            raise ValueError("a durable event_sink is required")
        if max_state_age < timedelta(0):
            raise ValueError("max_state_age must not be negative")
        self.scheduler = scheduler
        self.state_reader = state_reader
        self.mission_requirements = dict(mission_requirements)
        self.event_sink = event_sink
        self.max_state_age = max_state_age
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def tick(self) -> ContinuousMonitorReport:
        """Read once, validate the whole fleet set, then reconcile each fleet."""
        observation = self.state_reader.read_fleets()
        now = self.clock()
        try:
            observation.require_fresh(now=now, max_age=self.max_state_age)
        except ValueError as exc:
            raise ContinuousMonitorError(f"Poi observation is not trusted: {exc}") from exc
        if not observation.source.startswith("poi-redux:"):
            raise ContinuousMonitorError("monitor requires a Poi Redux source")

        by_fleet: dict[int, ExpeditionFleetSnapshot] = {}
        for snapshot in observation.payload:
            if snapshot.fleet_id in by_fleet:
                raise ContinuousMonitorError(f"duplicate Fleet {snapshot.fleet_id} in Poi observation")
            by_fleet[snapshot.fleet_id] = snapshot
        missing = set(self.scheduler.dispatch_fleet_ids) - set(by_fleet)
        if missing:
            raise ContinuousMonitorError(
                f"Poi observation omitted dispatchable fleets: {sorted(missing)} "
                f"(observed unlocked fleets: {sorted(by_fleet)}). A fleet that is "
                "not unlocked on this account must not be in the dispatch set; "
                "refusing to guess."
            )

        results = tuple(
            self._reconcile_fleet(fleet_id, by_fleet[fleet_id], now=now)
            for fleet_id in self.scheduler.dispatch_fleet_ids
        )
        return ContinuousMonitorReport(
            observed_at=observation.observed_at,
            source=observation.source,
            fleets=results,
            source_freshness_verified=observation.source_freshness_verified,
        )

    def _reconcile_fleet(
        self, fleet_id: int, snapshot: ExpeditionFleetSnapshot, *, now: datetime
    ) -> FleetMonitorResult:
        current = self.scheduler.fleet(fleet_id)
        previous = current.phase

        if current.phase is FleetPhase.BLOCKED:
            return self._result(fleet_id, previous, current, "blocked_hold", current.blocked_reason or "")
        if current.phase is FleetPhase.DISPATCHING:
            return self._result(
                fleet_id, previous, current, "dispatch_reconciliation_required",
                "pending dispatch intent must be reconciled by DurableDispatchCoordinator",
            )

        if current.phase is FleetPhase.EXPEDITION_ACTIVE:
            if current.mission is None:
                return self._block(fleet_id, current, "active fleet has no persisted mission")
            observed = self._mission_from_snapshot(snapshot, fleet_id)
            if observed is not None and observed == current.mission:
                action = "active_confirmed" if current.home_port_verified else "home_port_verification_required"
                return self._result(fleet_id, previous, current, action, "active mission tuple unchanged")
            if observed is not None and observed.mission_status == 2 and observed.same_expedition(current.mission):
                if not current.home_port_verified:
                    return self._block(
                        fleet_id, current,
                        "returned mission observed before post-dispatch Home Port verification",
                    )
                updated = self.scheduler.confirm_returned(fleet_id, observed)
                return self._result(
                    fleet_id, previous, updated, "return_detected",
                    "Poi mission status 2 matches the confirmed dispatch identity",
                )
            return self._block(fleet_id, current, "active mission tuple is missing or mismatched")

        if current.phase in (
            FleetPhase.RETURNED_PENDING_UI,
            FleetPhase.RETURN_RESULT,
            FleetPhase.BONUS_ITEMS,
        ):
            observed = self._mission_from_snapshot(snapshot, fleet_id)
            if (
                observed is not None
                and observed.mission_status == 2
                and current.mission is not None
                and observed.same_expedition(current.mission)
            ):
                return self._result(
                    fleet_id, previous, current, "settlement_pending",
                    "returned mission identity remains unchanged",
                )
            return self._block(fleet_id, current, "settlement phase no longer matches returned mission")

        if current.phase in (FleetPhase.IDLE, FleetPhase.READY, FleetPhase.RESUPPLY_REQUIRED):
            if not self._is_idle_snapshot(snapshot):
                return self._block(fleet_id, current, "scheduler expects idle fleet but Poi reports a mission")
            plan = evaluate_expedition_fleet(snapshot, now=now)
            if plan.decision is not ExpeditionDecision.READY_TO_DISPATCH:
                if current.phase is FleetPhase.READY:
                    return self._block(fleet_id, current, f"READY fleet lost supply/readiness: {plan.reason}")
                return self._result(fleet_id, previous, current, "not_ready", plan.reason)
            if not self._composition_is_eligible(snapshot, self.mission_requirements[fleet_id]):
                reason = "mission composition is missing or ineligible"
                if current.phase is FleetPhase.READY:
                    return self._block(fleet_id, current, reason)
                return self._result(fleet_id, previous, current, "composition_not_ready", reason)
            if current.phase is FleetPhase.IDLE:
                updated = self.scheduler.mark_ready(fleet_id)
                return self._result(fleet_id, previous, updated, "ready", "supply and mission composition verified")
            if current.phase is FleetPhase.RESUPPLY_REQUIRED:
                updated = self.scheduler.confirm_resupply(fleet_id, fully_supplied=True)
                return self._result(fleet_id, previous, updated, "resupply_confirmed", "Poi confirms full supply")
            return self._result(fleet_id, previous, current, "ready_hold", "fleet remains ready")

        return self._block(fleet_id, current, f"unsupported scheduler phase: {current.phase.value}")

    @staticmethod
    def _is_idle_snapshot(snapshot: ExpeditionFleetSnapshot) -> bool:
        return (
            snapshot.mission_status == 0
            and snapshot.mission_id is None
            and snapshot.return_at is None
            and snapshot.reserved == 0
        )

    @staticmethod
    def _composition_is_eligible(
        snapshot: ExpeditionFleetSnapshot, requirement: MissionCompositionRequirement
    ) -> bool:
        if (
            len(snapshot.ship_levels) != len(snapshot.ship_ids)
            or len(snapshot.ship_types) != len(snapshot.ship_ids)
        ):
            return False
        composition = FleetCompositionSnapshot(
            snapshot.ship_ids, snapshot.ship_levels, snapshot.ship_types
        )
        return evaluate_mission_composition(
            composition, requirement
        ).decision is MissionEligibilityDecision.ELIGIBLE

    @staticmethod
    def _mission_from_snapshot(
        snapshot: ExpeditionFleetSnapshot, fleet_id: int
    ) -> MissionTuple | None:
        if (
            snapshot.fleet_id != fleet_id
            or snapshot.mission_status not in (1, 2)
            or snapshot.mission_id is None
            or snapshot.return_at is None
            or not snapshot.ship_ids
        ):
            return None
        return MissionTuple(
            mission_status=snapshot.mission_status,
            mission_id=snapshot.mission_id,
            final_time_ms=int(snapshot.return_at.timestamp() * 1000),
            reserved=snapshot.reserved,
            ship_ids=snapshot.ship_ids,
        )

    def _block(
        self, fleet_id: int, current: FleetRuntime, reason: str
    ) -> FleetMonitorResult:
        updated = self.scheduler.block(fleet_id, reason)
        return self._result(fleet_id, current.phase, updated, "blocked", reason)

    def _result(
        self,
        fleet_id: int,
        previous: FleetPhase,
        current: FleetRuntime,
        action: str,
        reason: str,
    ) -> FleetMonitorResult:
        if previous is not current.phase:
            self.event_sink("fleet_monitor_transition", {
                "fleet_id": fleet_id,
                "previous_phase": previous.value,
                "current_phase": current.phase.value,
                "action": action,
                "reason": reason,
            })
        return FleetMonitorResult(
            fleet_id=fleet_id,
            previous_phase=previous,
            current_phase=current.phase,
            action=action,
            reason=reason,
        )