"""Apply fail-closed readiness checks to expedition planner recommendations.

This is a pure decision layer. It does not authorize live actions or perform I/O.
Callers must still apply the live-action policy before any future action adapter.
"""

from collections.abc import Mapping, Sequence

from kancollebot.core.expedition_planner import ExpeditionDecision
from kancollebot.core.fleet_parser import FleetObservation
from kancollebot.core.models import ShipState
from kancollebot.safety.dispatch import evaluate_dispatch_readiness


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
    recommendation is retained only if its fleet exists in the observations
    and every readiness check passes. This function is not live-action
    authorization; execution must remain separately disabled by default.
    """
    if decision.action != "dispatch":
        return decision

    fleet = next((item for item in fleets if item.fleet_id == decision.fleet_id), None)
    if fleet is None:
        return ExpeditionDecision(
            action="wait",
            fleet_id=None,
            mission_id=None,
            reason="Dispatch blocked: planned fleet is absent from current observations.",
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
        return ExpeditionDecision(
            action="wait",
            fleet_id=None,
            mission_id=None,
            reason=f"Dispatch blocked by readiness gate: {details}.",
        )
    return decision
