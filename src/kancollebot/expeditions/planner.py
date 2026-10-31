"""Offline expedition readiness planning; this module never performs game actions."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ExpeditionDecision(str, Enum):
    """A recommendation for a fleet, not an instruction to execute an action."""

    WAITING_FOR_RETURN = "waiting_for_return"
    RETURNED_AWAITING_REFRESH = "returned_awaiting_refresh"
    BLOCKED_UNKNOWN_RETURN_TIME = "blocked_unknown_return_time"
    COMPLETED_AWAITING_REWARD = "completed_awaiting_reward"
    BLOCKED_EMPTY_FLEET = "blocked_empty_fleet"
    BLOCKED_DAMAGED_SHIPS = "blocked_damaged_ships"
    NEEDS_RESUPPLY = "needs_resupply"
    READY_TO_DISPATCH = "ready_to_dispatch"


@dataclass(frozen=True, slots=True)
class ExpeditionFleetSnapshot:
    """Minimal observed facts needed for an offline expedition readiness check."""

    fleet_id: int
    ship_ids: tuple[int, ...]
    mission_id: int | None = None
    return_at: datetime | None = None
    fuel_ready: bool = False
    ammo_ready: bool = False
    damaged_ship_ids: tuple[int, ...] = ()
    mission_status: int | None = None

    def __post_init__(self) -> None:
        if self.fleet_id < 1:
            raise ValueError("fleet_id must be positive")
        if len(self.ship_ids) > 6:
            raise ValueError("a fleet cannot contain more than six ships")
        if any(ship_id < 1 for ship_id in self.ship_ids):
            raise ValueError("ship_ids must be positive")
        if len(set(self.ship_ids)) != len(self.ship_ids):
            raise ValueError("ship_ids must be unique")
        if len(set(self.damaged_ship_ids)) != len(self.damaged_ship_ids):
            raise ValueError("damaged_ship_ids must be unique")
        if not set(self.damaged_ship_ids).issubset(self.ship_ids):
            raise ValueError("damaged_ship_ids must belong to this fleet")
        if self.mission_status not in (None, 0, 1, 2):
            raise ValueError("mission_status must be 0, 1, 2, or unknown")
        if self.mission_status == 0 and self.mission_id is not None:
            raise ValueError("idle mission_status cannot have an active mission")
        if self.mission_status in (1, 2) and self.mission_id is None:
            raise ValueError("active mission_status requires a mission ID")
        if self.mission_id is not None and self.mission_id < 1:
            raise ValueError("mission_id must be positive")
        if self.return_at is not None and (
            self.return_at.tzinfo is None or self.return_at.utcoffset() is None
        ):
            raise ValueError("return_at must be timezone-aware")
        if self.mission_id is None and self.return_at is not None:
            raise ValueError("return_at requires an active mission")


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
    - Damage blocks dispatch; missing fuel or ammunition requires resupply.
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

    if snapshot.damaged_ship_ids:
        return ExpeditionPlan(
            snapshot.fleet_id,
            ExpeditionDecision.BLOCKED_DAMAGED_SHIPS,
            "At least one ship is marked damaged; repair policy is not inferred here.",
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
        "Observed fleet is non-empty, undamaged, supplied, and not on a mission.",
    )
