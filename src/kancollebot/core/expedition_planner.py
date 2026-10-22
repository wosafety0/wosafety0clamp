"""Pure expedition planning; this module never executes gameplay actions."""

from dataclasses import dataclass
from typing import Mapping, Sequence

from kancollebot.core.fleet_parser import FleetObservation


@dataclass(frozen=True, slots=True)
class ExpeditionDecision:
    """A recommendation for a caller to review, not an executable command."""

    action: str
    fleet_id: int | None
    mission_id: int | None
    reason: str


def plan_next_expedition(
    fleets: Sequence[FleetObservation],
    *,
    dispatchable_fleet_ids: Sequence[int] = (2, 3, 4),
    mission_by_fleet: Mapping[int, int],
) -> ExpeditionDecision:
    """Recommend at most one action, prioritizing completed expeditions.

    The planner intentionally does not validate ship damage, repair state,
    supply, fleet composition, or live-action authorization. Those checks must
    pass in separate layers before any future action adapter can execute.
    Unknown mission state never qualifies as idle or completed.
    """
    for fleet in fleets:
        if fleet.mission_state_code == 2:
            return ExpeditionDecision(
                action="collect",
                fleet_id=fleet.fleet_id,
                mission_id=fleet.mission_task_id,
                reason="Expedition is complete and awaiting collection.",
            )

    dispatchable = set(dispatchable_fleet_ids)
    for fleet in fleets:
        if fleet.fleet_id not in dispatchable or fleet.mission_state_code != 0:
            continue
        mission_id = mission_by_fleet.get(fleet.fleet_id)
        if mission_id is None:
            continue
        if isinstance(mission_id, bool) or not isinstance(mission_id, int) or mission_id <= 0:
            raise ValueError(f"configured mission id for fleet {fleet.fleet_id} must be a positive integer")
        if not fleet.occupied_ship_ids:
            continue
        return ExpeditionDecision(
            action="dispatch",
            fleet_id=fleet.fleet_id,
            mission_id=mission_id,
            reason="Fleet is observed idle and has a configured expedition.",
        )

    return ExpeditionDecision(
        action="wait",
        fleet_id=None,
        mission_id=None,
        reason="No safe planning candidate was found from the available mission-state data.",
    )
