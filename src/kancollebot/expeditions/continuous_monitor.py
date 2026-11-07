"""Read-only, source-fresh fleet reconciliation for the continuous scheduler."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

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


# Phases that can still change what the scheduler does next, and therefore
# justify asking the game for a fresh observation. EXPEDITION_ACTIVE is handled
# separately: it only qualifies once the return is close, or once the fleet
# still owes a post-dispatch Home Port verification.
_WORK_PENDING_PHASES = frozenset({
    FleetPhase.IDLE,
    FleetPhase.DISPATCHING,
    FleetPhase.RETURNED_PENDING_UI,
    FleetPhase.RETURN_RESULT,
    FleetPhase.BONUS_ITEMS,
    FleetPhase.RESUPPLY_REQUIRED,
    FleetPhase.READY,
})


def _read_observation(state_reader: Any) -> Any:
    """Read once without asking for anything.

    Reading is pure observation since R3-D: a reader that can refresh does not
    refresh here. Callers that need a fresh state name themselves through
    :meth:`ContinuousExpeditionMonitor._request_refresh`.
    """
    return state_reader.read_fleets()


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
        # How close a fleet's scheduled return has to be before a stale
        # observation is worth a navigation. Without a lead time an idle
        # monitoring tick would either navigate every 35s for nothing or never
        # notice a return at all; the default sits well above one refresh cycle
        # (cooldown plus witness wait) so a single refresh still lands ahead of
        # the return.
        refresh_lead_time: timedelta = timedelta(seconds=60),
        # Opt-in: adopt a mission the scheduler did not dispatch when it matches
        # the configured mission for that fleet. Off by default because the
        # conservative answer to "I expected an idle fleet" is BLOCKED; a caller
        # that has just confirmed Home Port may prefer to resume instead.
        adopt_unplanned_missions: bool = False,
    ) -> None:
        if set(mission_requirements) != set(scheduler.dispatch_fleet_ids):
            raise ValueError("mission_requirements must configure every and only dispatchable fleet")
        if any(not isinstance(item, MissionCompositionRequirement) for item in mission_requirements.values()):
            raise ValueError("each dispatch fleet requires a valid mission composition requirement")
        if not callable(event_sink):
            raise ValueError("a durable event_sink is required")
        if max_state_age < timedelta(0):
            raise ValueError("max_state_age must not be negative")
        if not isinstance(refresh_lead_time, timedelta) or refresh_lead_time < timedelta(0):
            raise ValueError("refresh_lead_time must be a non-negative timedelta")
        if not isinstance(adopt_unplanned_missions, bool):
            raise ValueError("adopt_unplanned_missions must be a bool")
        self.scheduler = scheduler
        self.state_reader = state_reader
        self.mission_requirements = dict(mission_requirements)
        self.event_sink = event_sink
        self.max_state_age = max_state_age
        self.refresh_lead_time = refresh_lead_time
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.adopt_unplanned_missions = adopt_unplanned_missions

    def tick(self) -> ContinuousMonitorReport:
        """Read once, refresh only if pending work needs a witness, then reconcile.

        The read is pure observation. A navigation happens only when the
        observation is not usable *and* the scheduler still has work that a
        fresh observation could change: a fleet waiting on settlement,
        resupply, dispatch, Home Port verification, or a return that is close
        enough to matter. When nothing is pending the monitor fails closed
        instead of clicking - live 2026-11-05, eleven of twenty-seven
        navigations happened after the last real action, purely because every
        tick read through a self-refreshing reader.
        """
        observation = _read_observation(self.state_reader)
        now = self.clock()
        if not self._is_usable(observation, now):
            reason = self._pending_work_reason(now)
            if reason is None:
                raise ContinuousMonitorError(
                    "Poi observation is not trusted: no fleet work is pending, so "
                    "the monitor will not navigate for a fresh one "
                    f"(observed_at={getattr(observation, 'observed_at', None)})"
                )
            observation = self._request_refresh(observation, origin="monitor_tick", reason=reason)
            # The refresh blocks for a whole navigation; a timestamp taken
            # before it would judge the new observation against the old clock.
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

    def _is_usable(self, observation: Any, now: datetime) -> bool:
        try:
            observation.require_fresh(now=now, max_age=self.max_state_age)
        except ValueError:
            return False
        return True

    def _pending_work_reason(self, now: datetime) -> str | None:
        """Name the pending work that a fresh observation could change.

        Returns ``None`` when every dispatchable fleet is parked in a state that
        no observation can advance - an expedition still running with its return
        far away, or a fleet already blocked for an operator. In that case a
        navigation buys nothing, so the monitor declines to send one.
        """
        for fleet_id in self.scheduler.dispatch_fleet_ids:
            runtime = self.scheduler.fleet(fleet_id)
            if runtime.phase in _WORK_PENDING_PHASES:
                return f"fleet {fleet_id} is {runtime.phase.value}"
            if runtime.phase is not FleetPhase.EXPEDITION_ACTIVE:
                # BLOCKED holds until an operator intervenes; the same read
                # would return the same verdict.
                continue
            if not runtime.home_port_verified:
                return f"fleet {fleet_id} still owes post-dispatch Home Port verification"
            if runtime.mission is None:
                continue
            due_at = datetime.fromtimestamp(
                runtime.mission.final_time_ms / 1000, tz=timezone.utc
            )
            if due_at - now <= self.refresh_lead_time:
                return f"fleet {fleet_id} is due to return at {due_at.isoformat()}"
        return None

    def _request_refresh(self, observation: Any, *, origin: str, reason: str) -> Any:
        ensure_fresh = getattr(self.state_reader, "ensure_fresh", None)
        if not callable(ensure_fresh):
            # A reader that cannot refresh is not read twice: the second read
            # would return the same unverified state and only cost time.
            return observation
        self.event_sink("monitor_refresh_requested", {
            "origin": origin,
            "reason": reason,
        })
        return ensure_fresh(origin=origin)

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
            if self._is_idle_snapshot(snapshot) and current.mission is not None:
                # Accepting the expedition result clears api_mission, so a
                # half-finished reward chain shows up as an idle fleet (live
                # 2026-11-05). The returned identity is in the scheduler and the
                # remaining work is pure UI, so this keeps the settlement alive
                # instead of blocking a fleet the game has already settled.
                return self._result(
                    fleet_id, previous, current, "settlement_continuing",
                    "Poi reports idle after the result was accepted; reward screens pending",
                )
            return self._block(fleet_id, current, "settlement phase no longer matches returned mission")

        if current.phase in (FleetPhase.IDLE, FleetPhase.READY, FleetPhase.RESUPPLY_REQUIRED):
            if not self._is_idle_snapshot(snapshot):
                adopted = self._adopt_unplanned_mission(fleet_id, current, snapshot)
                if adopted is not None:
                    return adopted
                return self._block(fleet_id, current, "scheduler expects idle fleet but Poi reports a mission")
            plan = evaluate_expedition_fleet(snapshot, now=now)
            if plan.decision is not ExpeditionDecision.READY_TO_DISPATCH:
                if current.phase is FleetPhase.READY:
                    return self._block(fleet_id, current, f"READY fleet lost supply/readiness: {plan.reason}")
                if (
                    plan.decision is ExpeditionDecision.NEEDS_RESUPPLY
                    and current.phase
                    in (FleetPhase.IDLE, FleetPhase.RESUPPLY_REQUIRED)
                ):
                    # Step 1 of the core logic: an idle but under-supplied fleet
                    # gets the normal in-game resupply, then a fresh read that
                    # proves full supply. Reporting ``not_ready`` here would be
                    # a dead end -- nothing else in the loop supplies an IDLE
                    # fleet, so it could never become dispatchable. A fleet that
                    # is already RESUPPLY_REQUIRED (e.g. restored from the
                    # scheduler store) keeps that phase; the label stays honest.
                    updated = (
                        self.scheduler.require_resupply(fleet_id)
                        if current.phase is FleetPhase.IDLE
                        else current
                    )
                    return self._result(
                        fleet_id, previous, updated, "resupply_required", plan.reason
                    )
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

    def _adopt_unplanned_mission(
        self,
        fleet_id: int,
        current: FleetRuntime,
        snapshot: ExpeditionFleetSnapshot,
    ) -> FleetMonitorResult | None:
        """Resume a fleet whose mission the durable state does not know about.

        Returns None - which leaves the caller free to block - unless every
        adoptable fact lines up: the option is on, the mission identity equals
        the configured mission for this fleet, and the observed composition
        still satisfies that mission's requirement. The mission is *not* trusted
        on the strength of its arrival time; only the identity and the ships are
        used, so a different expedition on the same fleet still blocks.
        """
        if not self.adopt_unplanned_missions:
            return None
        observed = self._mission_from_snapshot(snapshot, fleet_id)
        if observed is None:
            self._reject_adoption(fleet_id, "mission tuple cannot be formed from the observation")
            return None
        requirement = self.mission_requirements[fleet_id]
        if observed.mission_id != requirement.mission_id:
            self._reject_adoption(
                fleet_id,
                f"observed mission {observed.mission_id} is not the configured "
                f"mission {requirement.mission_id}",
            )
            return None
        if not self._composition_is_eligible(snapshot, requirement):
            self._reject_adoption(fleet_id, "observed composition is not eligible for the configured mission")
            return None
        updated = self.scheduler.adopt_observed_mission(
            fleet_id, observed, home_port_verified=observed.mission_status == 1
        )
        self.event_sink("fleet_mission_adopted", {
            "fleet_id": fleet_id,
            "mission_id": observed.mission_id,
            "mission_status": observed.mission_status,
            "final_time_ms": observed.final_time_ms,
            "ship_ids": list(observed.ship_ids),
            "previous_phase": current.phase.value,
            "current_phase": updated.phase.value,
        })
        reason = (
            "observed mission matches the configured mission; Home Port was confirmed "
            "by the caller, so the fleet resumes as active"
            if observed.mission_status == 1
            else "observed mission already returned; waiting for the reward screens"
        )
        return self._result(fleet_id, current.phase, updated, "adopted_observed_mission", reason)

    def _reject_adoption(self, fleet_id: int, reason: str) -> None:
        self.event_sink("fleet_mission_adoption_rejected", {
            "fleet_id": fleet_id,
            "reason": reason,
        })

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