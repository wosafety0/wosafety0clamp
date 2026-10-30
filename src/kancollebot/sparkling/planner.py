"""Side-effect-free morale planning; this module never performs game actions.

Morale gains vary with battle outcomes and ship role. The planner therefore
requires an explicit estimated gain per sortie and labels sortie counts as
estimates rather than guarantees.
"""

from dataclasses import dataclass
from enum import Enum
from math import ceil


class SparkleDecision(str, Enum):
    """Planning status for one ship."""

    NEEDS_SORTIES = "needs_sorties"
    TARGET_REACHED = "target_reached"
    BLOCKED_UNKNOWN_MORALE = "blocked_unknown_morale"
    BLOCKED_INELIGIBLE = "blocked_ineligible"


@dataclass(frozen=True, slots=True)
class SparkleShipSnapshot:
    """Observed inputs for one ship; None morale means unknown, not zero."""

    ship_id: int
    morale: int | None
    eligible: bool = True

    def __post_init__(self) -> None:
        if type(self.ship_id) is not int or self.ship_id < 1:
            raise ValueError("ship_id must be a positive integer")
        if self.morale is not None and (
            type(self.morale) is not int or not 0 <= self.morale <= 100
        ):
            raise ValueError("morale must be an integer between 0 and 100")
        if type(self.eligible) is not bool:
            raise ValueError("eligible must be a boolean")


@dataclass(frozen=True, slots=True)
class SparkleShipPlan:
    """Recommendation for one ship, with no side effects."""

    ship_id: int
    decision: SparkleDecision
    current_morale: int | None
    target_morale: int
    estimated_sorties: int | None
    reason: str


@dataclass(frozen=True, slots=True)
class SparklePlan:
    """Stable ordered plan for the supplied ship snapshots."""

    target_morale: int
    estimated_gain_per_sortie: int
    ships: tuple[SparkleShipPlan, ...]

    @property
    def pending_ship_ids(self) -> tuple[int, ...]:
        """Eligible ships below target, ordered by lowest observed morale."""
        return tuple(
            item.ship_id
            for item in self.ships
            if item.decision is SparkleDecision.NEEDS_SORTIES
        )


def build_sparkle_plan(
    snapshots: tuple[SparkleShipSnapshot, ...],
    *,
    target_morale: int = 50,
    estimated_gain_per_sortie: int = 10,
    max_sorties_per_ship: int = 3,
) -> SparklePlan:
    """Estimate a morale-farming queue from observed values.

    The caller supplies a conservative gain estimate because actual morale
    changes depend on the sortie and battle result. Unknown morale and
    ineligible ships fail closed. This function does not select a map, start a
    sortie, choose battle commands, or interact with POI.
    """
    if type(target_morale) is not int or not 1 <= target_morale <= 100:
        raise ValueError("target_morale must be an integer between 1 and 100")
    if (
        type(estimated_gain_per_sortie) is not int
        or not 1 <= estimated_gain_per_sortie <= 100
    ):
        raise ValueError("estimated_gain_per_sortie must be an integer between 1 and 100")
    if type(max_sorties_per_ship) is not int or max_sorties_per_ship < 1:
        raise ValueError("max_sorties_per_ship must be a positive integer")

    ship_ids = tuple(item.ship_id for item in snapshots)
    if len(set(ship_ids)) != len(ship_ids):
        raise ValueError("ship snapshot IDs must be unique")

    plans: list[SparkleShipPlan] = []
    for snapshot in snapshots:
        if not snapshot.eligible:
            plans.append(SparkleShipPlan(
                snapshot.ship_id, SparkleDecision.BLOCKED_INELIGIBLE,
                snapshot.morale, target_morale, None,
                "Ship is marked ineligible by the caller's policy.",
            ))
            continue
        if snapshot.morale is None:
            plans.append(SparkleShipPlan(
                snapshot.ship_id, SparkleDecision.BLOCKED_UNKNOWN_MORALE,
                None, target_morale, None,
                "Morale is unknown; refresh observed state before planning sorties.",
            ))
            continue
        if snapshot.morale >= target_morale:
            plans.append(SparkleShipPlan(
                snapshot.ship_id, SparkleDecision.TARGET_REACHED,
                snapshot.morale, target_morale, 0,
                "Observed morale meets or exceeds the target.",
            ))
            continue

        estimated = ceil(
            (target_morale - snapshot.morale) / estimated_gain_per_sortie
        )
        if estimated > max_sorties_per_ship:
            reason = (
                f"Estimated {estimated} sorties exceed the configured per-ship "
                f"limit of {max_sorties_per_ship}; refresh morale after a bounded batch."
            )
            estimated_for_batch: int | None = max_sorties_per_ship
        else:
            reason = (
                "Estimated count only; morale gain must be re-read after each "
                "bounded batch and is not guaranteed."
            )
            estimated_for_batch = estimated
        plans.append(SparkleShipPlan(
            snapshot.ship_id, SparkleDecision.NEEDS_SORTIES,
            snapshot.morale, target_morale, estimated_for_batch, reason,
        ))

    ordered = tuple(sorted(
        plans,
        key=lambda item: (
            0 if item.decision is SparkleDecision.NEEDS_SORTIES else 1,
            item.current_morale if item.current_morale is not None else 101,
            item.ship_id,
        ),
    ))
    return SparklePlan(target_morale, estimated_gain_per_sortie, ordered)
