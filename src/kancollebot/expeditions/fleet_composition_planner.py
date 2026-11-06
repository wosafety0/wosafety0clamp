"""Pure planner for exchanging a non-flagship ship to meet another fleet's flagship level."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .mission_eligibility import (
    FleetCompositionSnapshot,
    MissionCompositionRequirement,
    MissionEligibilityDecision,
    evaluate_mission_composition,
)


class FlagshipExchangeDecision(str, Enum):
    EXCHANGE_AVAILABLE = "exchange_available"
    NO_EXCHANGE_NEEDED = "no_exchange_needed"
    NO_QUALIFYING_DONOR = "no_qualifying_donor"
    BLOCKED_DONOR_COMPOSITION = "blocked_donor_composition"
    BLOCKED_RECEIVER_COMPOSITION = "blocked_receiver_composition"
    BLOCKED_OVERLAPPING_FLEETS = "blocked_overlapping_fleets"


@dataclass(frozen=True, slots=True)
class FlagshipExchangePlan:
    decision: FlagshipExchangeDecision
    donor_ship_id: int | None
    receiver_flagship_id: int | None
    donor_after: FleetCompositionSnapshot | None
    receiver_after: FleetCompositionSnapshot | None
    reason: str


def plan_flagship_exchange(
    donor: FleetCompositionSnapshot,
    receiver: FleetCompositionSnapshot,
    donor_requirement: MissionCompositionRequirement,
    receiver_requirement: MissionCompositionRequirement,
) -> FlagshipExchangePlan:
    """Find the lowest-level qualifying non-flagship donor while preserving both missions.

    This only returns a proposal. It does not modify a fleet or interact with Poi.
    """
    if set(donor.ship_ids) & set(receiver.ship_ids):
        return FlagshipExchangePlan(
            FlagshipExchangeDecision.BLOCKED_OVERLAPPING_FLEETS,
            None, None, None, None,
            "Donor and receiver snapshots share ship IDs; refusing to plan an exchange.",
        )

    donor_current = evaluate_mission_composition(donor, donor_requirement)
    if donor_current.decision is not MissionEligibilityDecision.ELIGIBLE:
        return FlagshipExchangePlan(
            FlagshipExchangeDecision.BLOCKED_DONOR_COMPOSITION,
            None, None, None, None,
            f"Donor fleet is not currently eligible: {donor_current.reason}",
        )

    receiver_current = evaluate_mission_composition(receiver, receiver_requirement)
    if receiver_current.decision is MissionEligibilityDecision.ELIGIBLE:
        return FlagshipExchangePlan(
            FlagshipExchangeDecision.NO_EXCHANGE_NEEDED,
            None, None, donor, receiver,
            "Receiver fleet already satisfies its mission composition requirement.",
        )
    if receiver_current.decision is not MissionEligibilityDecision.BLOCKED_FLAGSHIP_LEVEL:
        return FlagshipExchangePlan(
            FlagshipExchangeDecision.BLOCKED_RECEIVER_COMPOSITION,
            None, None, None, None,
            f"Receiver has a non-flagship composition blocker: {receiver_current.reason}",
        )

    if not receiver.ship_ids:
        return FlagshipExchangePlan(
            FlagshipExchangeDecision.BLOCKED_RECEIVER_COMPOSITION,
            None, None, None, None,
            "Receiver fleet has no flagship to exchange.",
        )

    candidates = []
    for index in range(1, len(donor.ship_ids)):
        if donor.ship_levels[index] < receiver_requirement.minimum_flagship_level:
            continue
        donor_ids = list(donor.ship_ids)
        donor_levels = list(donor.ship_levels)
        donor_types = list(donor.ship_types)
        receiver_ids = list(receiver.ship_ids)
        receiver_levels = list(receiver.ship_levels)
        receiver_types = list(receiver.ship_types)

        donor_ship_id = donor_ids[index]
        receiver_flagship_id = receiver_ids[0]
        donor_ids[index], receiver_ids[0] = receiver_flagship_id, donor_ship_id
        donor_levels[index], receiver_levels[0] = receiver_levels[0], donor_levels[index]
        donor_types[index], receiver_types[0] = receiver_types[0], donor_types[index]

        donor_after = FleetCompositionSnapshot(tuple(donor_ids), tuple(donor_levels), tuple(donor_types))
        receiver_after = FleetCompositionSnapshot(tuple(receiver_ids), tuple(receiver_levels), tuple(receiver_types))
        donor_plan = evaluate_mission_composition(donor_after, donor_requirement)
        receiver_plan = evaluate_mission_composition(receiver_after, receiver_requirement)
        if (
            donor_plan.decision is MissionEligibilityDecision.ELIGIBLE
            and receiver_plan.decision is MissionEligibilityDecision.ELIGIBLE
        ):
            candidates.append((
                donor.ship_levels[index],
                donor.ship_ids[index],
                donor_after,
                receiver_after,
                receiver_flagship_id,
            ))

    if not candidates:
        return FlagshipExchangePlan(
            FlagshipExchangeDecision.NO_QUALIFYING_DONOR,
            None, None, None, None,
            "No non-flagship donor ship can be exchanged while keeping both fleets eligible.",
        )

    _, donor_ship_id, donor_after, receiver_after, receiver_flagship_id = min(
        candidates, key=lambda item: (item[0], item[1])
    )
    return FlagshipExchangePlan(
        FlagshipExchangeDecision.EXCHANGE_AVAILABLE,
        donor_ship_id,
        receiver_flagship_id,
        donor_after,
        receiver_after,
        f"Exchange donor ship {donor_ship_id} with receiver flagship {receiver_flagship_id}; both resulting formations satisfy their supplied requirements.",
    )
