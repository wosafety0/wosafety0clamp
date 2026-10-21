"""Deterministic fleet rotation selection built on the offline readiness planner."""

from dataclasses import dataclass

from .planner import (
    ExpeditionDecision,
    ExpeditionFleetSnapshot,
    ExpeditionPlan,
    evaluate_expedition_fleet,
)


@dataclass(frozen=True, slots=True)
class FleetRotationPlan:
    """Read-only recommendation for which observed fleet to use next."""

    selected_fleet_id: int | None
    evaluated_fleets: tuple[ExpeditionPlan, ...]
    reason: str


def select_next_expedition_fleet(
    snapshots: tuple[ExpeditionFleetSnapshot, ...],
    *,
    now,
    preferred_fleet_ids: tuple[int, ...] = (),
) -> FleetRotationPlan:
    """Select a ready fleet without dispatching it or changing any state.

    Preferred IDs only break ties between ready fleets. If none are supplied,
    the lowest fleet ID wins. Duplicate fleet IDs and duplicate preferences are
    rejected so that selection remains explicit and deterministic.
    """
    fleet_ids = tuple(snapshot.fleet_id for snapshot in snapshots)
    if len(set(fleet_ids)) != len(fleet_ids):
        raise ValueError("fleet snapshot IDs must be unique")
    if len(set(preferred_fleet_ids)) != len(preferred_fleet_ids):
        raise ValueError("preferred fleet IDs must be unique")
    if any(fleet_id < 1 for fleet_id in preferred_fleet_ids):
        raise ValueError("preferred fleet IDs must be positive")

    evaluated = tuple(
        evaluate_expedition_fleet(snapshot, now=now)
        for snapshot in snapshots
    )
    ready_ids = {
        plan.fleet_id
        for plan in evaluated
        if plan.decision is ExpeditionDecision.READY_TO_DISPATCH
    }
    if not ready_ids:
        return FleetRotationPlan(
            selected_fleet_id=None,
            evaluated_fleets=evaluated,
            reason="No observed fleet is ready; no dispatch recommendation made.",
        )

    preferred_rank = {
        fleet_id: index for index, fleet_id in enumerate(preferred_fleet_ids)
    }
    selected = min(
        ready_ids,
        key=lambda fleet_id: (
            preferred_rank.get(fleet_id, len(preferred_rank)),
            fleet_id,
        ),
    )
    return FleetRotationPlan(
        selected_fleet_id=selected,
        evaluated_fleets=evaluated,
        reason=(
            "Selected the highest-priority ready fleet. This is a recommendation "
            "only; refresh state and apply the live-action safety policy before use."
        ),
    )
