"""Offline expedition readiness planning; this module never performs game actions."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

# P0 R1: the observed fleet snapshot is a task-neutral DATA SHAPE. Its
# canonical definition now lives in ``core.observation_contracts``; the legacy
# name is an alias of the very same class, so existing imports, isinstance
# checks and dataclass equality keep working byte-for-byte.
from kancollebot.core.observation_contracts import (
    ObservedFleetSnapshot as ExpeditionFleetSnapshot,
)


class ExpeditionDecision(str, Enum):
    """A recommendation for a fleet, not an instruction to execute an action."""

    WAITING_FOR_RETURN = "waiting_for_return"
    RETURNED_AWAITING_REFRESH = "returned_awaiting_refresh"
    BLOCKED_UNKNOWN_RETURN_TIME = "blocked_unknown_return_time"
    COMPLETED_AWAITING_REWARD = "completed_awaiting_reward"
    BLOCKED_EMPTY_FLEET = "blocked_empty_fleet"
    BLOCKED_DAMAGED_SHIPS = "blocked_damaged_ships"
    BLOCKED_CRITICAL_DAMAGE = "blocked_critical_damage"
    NEEDS_RESUPPLY = "needs_resupply"
    READY_TO_DISPATCH = "ready_to_dispatch"


# ``ExpeditionFleetSnapshot`` is now an alias of
# ``core.observation_contracts.ObservedFleetSnapshot`` (imported above). The
# validation contract is unchanged; it lives with the neutral data shape.


@dataclass(frozen=True, slots=True)
class ExpeditionPlan:
    """A deterministic, side-effect-free result for one observed fleet."""

    fleet_id: int
    decision: ExpeditionDecision
    reason: str


def evaluate_expedition_fleet(
    snapshot: ExpeditionFleetSnapshot,
    *,
    now: datetime,
) -> ExpeditionPlan:
    """Evaluate readiness using supplied facts and a timezone-aware clock.

    Safety behavior:
    - An active mission is never considered dispatchable.
    - A reached return time does not prove the game state has refreshed.
    - Critical damage (大破) blocks dispatch; minor or moderate damage does not.
    - Missing fuel or ammunition requires resupply.
    - READY_TO_DISPATCH is only a planning result. No POI or network calls occur.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    if snapshot.mission_id is not None:
        if snapshot.mission_status == 2:
            return ExpeditionPlan(
                snapshot.fleet_id,
                ExpeditionDecision.COMPLETED_AWAITING_REWARD,
                "Expedition completed; reward settlement remains pending.",
            )
        if snapshot.return_at is None:
            return ExpeditionPlan(
                snapshot.fleet_id,
                ExpeditionDecision.BLOCKED_UNKNOWN_RETURN_TIME,
                "Active mission has no trusted return time.",
            )
        if now < snapshot.return_at:
            return ExpeditionPlan(
                snapshot.fleet_id,
                ExpeditionDecision.WAITING_FOR_RETURN,
                "The observed expedition return time is still in the future.",
            )
        return ExpeditionPlan(
            snapshot.fleet_id,
            ExpeditionDecision.RETURNED_AWAITING_REFRESH,
            "Return time elapsed; refresh observed game state before planning again.",
        )

    if not snapshot.ship_ids:
        return ExpeditionPlan(
            snapshot.fleet_id,
            ExpeditionDecision.BLOCKED_EMPTY_FLEET,
            "Fleet has no observed ships.",
        )

    if snapshot.heavy_damage_ship_ids:
        return ExpeditionPlan(
            snapshot.fleet_id,
            ExpeditionDecision.BLOCKED_CRITICAL_DAMAGE,
            "At least one ship is critically damaged (大破); dispatch blocked until repaired.",
        )

    if not snapshot.fuel_ready or not snapshot.ammo_ready:
        missing = []
        if not snapshot.fuel_ready:
            missing.append("fuel")
        if not snapshot.ammo_ready:
            missing.append("ammunition")
        return ExpeditionPlan(
            snapshot.fleet_id,
            ExpeditionDecision.NEEDS_RESUPPLY,
            "Insufficient readiness: " + " and ".join(missing) + ".",
        )

    return ExpeditionPlan(
        snapshot.fleet_id,
        ExpeditionDecision.READY_TO_DISPATCH,
        "Observed fleet is non-empty, free of critical damage, supplied, and not on a mission.",
    )
