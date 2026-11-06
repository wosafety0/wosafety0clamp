"""Read-only composition and supply preflight for one fleet expedition assignment."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from kancollebot.adapters.poi.cdp_fleet_state import PoiFleetMissionState
from .mission_eligibility import (
    FleetCompositionSnapshot,
    MissionCompositionPlan,
    MissionCompositionRequirement,
    MissionEligibilityDecision,
    evaluate_mission_composition,
)


class FleetPreflightDecision(str, Enum):
    READY = "ready"
    BLOCKED_BUSY = "blocked_busy"
    BLOCKED_SUPPLY_SNAPSHOT = "blocked_supply_snapshot"
    BLOCKED_COMPOSITION = "blocked_composition"
    BLOCKED_UNSUPPLIED = "blocked_unsupplied"


@dataclass(frozen=True, slots=True)
class FleetExpeditionPreflight:
    fleet_id: int
    mission_id: int
    decision: FleetPreflightDecision
    reason: str
    composition: MissionCompositionPlan | None = None
    unsupplied_ship_ids: tuple[int, ...] = ()


def evaluate_fleet_expedition_preflight(
    fleet: PoiFleetMissionState,
    requirement: MissionCompositionRequirement,
    *,
    require_full_supply: bool = True,
) -> FleetExpeditionPreflight:
    """Evaluate cached fleet state only; never performs UI or game actions.

    The default policy requires every member's fuel and ammunition to be at
    maximum. This is deliberately conservative and is a project preflight
    policy, not a claim that every expedition's game rule requires full supply.
    """
    if not isinstance(require_full_supply, bool):
        raise ValueError("require_full_supply must be a boolean")

    def result(
        decision: FleetPreflightDecision,
        reason: str,
        *,
        composition: MissionCompositionPlan | None = None,
        unsupplied_ship_ids: tuple[int, ...] = (),
    ) -> FleetExpeditionPreflight:
        return FleetExpeditionPreflight(
            fleet.fleet_id,
            requirement.mission_id,
            decision,
            reason,
            composition,
            unsupplied_ship_ids,
        )

    if fleet.mission != (0, 0, 0, 0):
        return result(FleetPreflightDecision.BLOCKED_BUSY, f"Fleet mission state is not idle: {fleet.mission}.")

    member_ids = tuple(ship_id for ship_id in fleet.ship_ids if ship_id > 0)
    supply_by_id = {item.ship_id: item for item in fleet.supply}
    if (
        not member_ids
        or len(member_ids) != len(set(member_ids))
        or set(supply_by_id) != set(member_ids)
        or len(supply_by_id) != len(fleet.supply)
    ):
        return result(
            FleetPreflightDecision.BLOCKED_SUPPLY_SNAPSHOT,
            "Poi supply snapshot is missing, duplicated, or does not cover every fleet member.",
        )

    members = tuple(supply_by_id[ship_id] for ship_id in member_ids)
    if any(item.level is None or item.ship_type is None for item in members):
        return result(
            FleetPreflightDecision.BLOCKED_COMPOSITION,
            "Poi composition metadata is incomplete for one or more fleet members.",
        )

    unsupplied = tuple(item.ship_id for item in members if not item.is_fully_supplied)
    composition_snapshot = FleetCompositionSnapshot(
        member_ids,
        tuple(item.level for item in members),
        tuple(item.ship_type for item in members),
    )
    composition = evaluate_mission_composition(composition_snapshot, requirement)
    if composition.decision is not MissionEligibilityDecision.ELIGIBLE:
        reason = composition.reason
        if require_full_supply and unsupplied:
            reason += f" Full-supply policy also not met by ship IDs: {unsupplied}."
        return result(
            FleetPreflightDecision.BLOCKED_COMPOSITION,
            reason,
            composition=composition,
            unsupplied_ship_ids=unsupplied if require_full_supply else (),
        )

    if require_full_supply and unsupplied:
        return result(
            FleetPreflightDecision.BLOCKED_UNSUPPLIED,
            f"Full-supply policy not met by ship IDs: {unsupplied}.",
            composition=composition,
            unsupplied_ship_ids=unsupplied,
        )

    return result(
        FleetPreflightDecision.READY,
        "Fleet is idle, composition-eligible, and satisfies the configured supply policy.",
        composition=composition,
    )
