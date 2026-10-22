"""Apply fail-closed readiness checks to expedition planner recommendations.

This is a pure decision layer. It does not authorize live actions or perform I/O.
Callers must still apply the live-action policy before any future action adapter.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

from kancollebot.core.expedition_planner import (
    ExpeditionDecision,
    plan_next_expedition,
)
from kancollebot.core.fleet_parser import FleetObservation
from kancollebot.core.models import ShipState
from kancollebot.core.observed_state import ObservedState
from kancollebot.safety.dispatch import evaluate_dispatch_readiness


def _wait(reason: str) -> ExpeditionDecision:
    return ExpeditionDecision(
        action="wait",
        fleet_id=None,
        mission_id=None,
        reason=reason,
    )


def gate_expedition_decision(
    decision: ExpeditionDecision,
    fleets: Sequence[FleetObservation],
    ships_by_id: Mapping[int, ShipState],
    *,
    supply_verified: bool = False,
    repair_status_verified: bool = False,
    mission_composition_verified: bool = False,
    rotation_verified: bool = False,
    critical_hp_ratio: float = 0.25,
) -> ExpeditionDecision:
    """Convert an unsafe or unverifiable dispatch recommendation to wait.

    Collection and wait recommendations are returned unchanged. A dispatch
    recommendation is retained only if it identifies a valid mission, its
    fleet exists and is still observed idle, and every readiness check passes.
    This function is not live-action authorization; execution must remain
    separately disabled by default.
    """
    if decision.action != "dispatch":
        return decision

    if (
        isinstance(decision.mission_id, bool)
        or not isinstance(decision.mission_id, int)
        or decision.mission_id <= 0
    ):
        return _wait("Dispatch blocked: planned mission ID is missing or invalid.")

    fleet = next((item for item in fleets if item.fleet_id == decision.fleet_id), None)
    if fleet is None:
        return _wait("Dispatch blocked: planned fleet is absent from current observations.")

    if fleet.mission_state_code != 0:
        return _wait(
            "Dispatch blocked: planned fleet is not observed idle "
            "(mission state is unknown or non-idle)."
        )

    readiness = evaluate_dispatch_readiness(
        fleet,
        ships_by_id,
        supply_verified=supply_verified,
        repair_status_verified=repair_status_verified,
        mission_composition_verified=mission_composition_verified,
        rotation_verified=rotation_verified,
        critical_hp_ratio=critical_hp_ratio,
    )
    if not readiness.ready:
        details = "; ".join(readiness.reasons)
        return _wait(f"Dispatch blocked by readiness gate: {details}.")
    return decision


def plan_safe_expedition(
    fleets: Sequence[FleetObservation],
    ships_by_id: Mapping[int, ShipState],
    *,
    mission_by_fleet: Mapping[int, int],
    dispatchable_fleet_ids: Sequence[int] = (2, 3, 4),
    supply_verified: bool = False,
    repair_status_verified: bool = False,
    mission_composition_verified: bool = False,
    rotation_verified: bool = False,
    critical_hp_ratio: float = 0.25,
) -> ExpeditionDecision:
    """Plan one expedition decision and apply the fail-closed dispatch gate.

    This is the preferred orchestration entry point for callers that need a
    planning recommendation. It does not authorize or execute live actions.
    Completed expeditions retain collection priority; any dispatch candidate
    is changed to wait unless all readiness gates explicitly pass.
    """
    decision = plan_next_expedition(
        fleets,
        dispatchable_fleet_ids=dispatchable_fleet_ids,
        mission_by_fleet=mission_by_fleet,
    )
    return gate_expedition_decision(
        decision,
        fleets,
        ships_by_id,
        supply_verified=supply_verified,
        repair_status_verified=repair_status_verified,
        mission_composition_verified=mission_composition_verified,
        rotation_verified=rotation_verified,
        critical_hp_ratio=critical_hp_ratio,
    )


def plan_fresh_safe_expedition(
    fleets_state: ObservedState[Sequence[FleetObservation]],
    ships_state: ObservedState[Mapping[int, ShipState]],
    *,
    now: datetime,
    max_age: timedelta,
    mission_by_fleet: Mapping[int, int],
    dispatchable_fleet_ids: Sequence[int] = (2, 3, 4),
    supply_verified: bool = False,
    repair_status_verified: bool = False,
    mission_composition_verified: bool = False,
    rotation_verified: bool = False,
    critical_hp_ratio: float = 0.25,
) -> ExpeditionDecision:
    """Plan only from fresh, timezone-aware fleet and ship observations.

    Both snapshots must be fresh and have the same source label. Stale,
    future-dated, or mismatched-source snapshots return wait without attempting
    to plan collection or dispatch from potentially inconsistent state.
    """
    if fleets_state.source != ships_state.source:
        return _wait("Planning blocked: fleet and ship observations have different sources.")

    try:
        fleets_state.require_fresh(now=now, max_age=max_age)
        ships_state.require_fresh(now=now, max_age=max_age)
    except ValueError:
        return _wait("Planning blocked: fleet or ship observations are stale or have invalid timestamps.")

    return plan_safe_expedition(
        fleets_state.payload,
        ships_state.payload,
        mission_by_fleet=mission_by_fleet,
        dispatchable_fleet_ids=dispatchable_fleet_ids,
        supply_verified=supply_verified,
        repair_status_verified=repair_status_verified,
        mission_composition_verified=mission_composition_verified,
        rotation_verified=rotation_verified,
        critical_hp_ratio=critical_hp_ratio,
    )
