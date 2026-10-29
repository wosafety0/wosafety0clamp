"""Read-only composition of observed expedition state and offline plans.

This module has no Poi client, adapter, runner, journal, or dispatch interface.
It suppresses planning output unless both local age and source freshness gates pass.
Passing those gates still does not authorize a gameplay action.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from kancollebot.core.observed_state import ObservedState
from kancollebot.expeditions.planner import (
    ExpeditionDecision,
    ExpeditionFleetSnapshot,
    evaluate_expedition_fleet,
)


@dataclass(frozen=True, slots=True)
class ReadOnlyFleetAssessment:
    """Display-safe planning result; never an executable action."""

    fleet_id: int
    decision: ExpeditionDecision | None
    reason: str
    observation_accepted: bool
    dispatch_eligible: bool = False


def assess_observed_fleets(
    state: ObservedState[tuple[ExpeditionFleetSnapshot, ...]],
    *,
    now: datetime,
    max_state_age: timedelta,
) -> tuple[ReadOnlyFleetAssessment, ...]:
    """Produce plans only from recent observations with verified source freshness.

    An unverified or stale observation yields a suppressed assessment for each
    fleet. Even accepted observations produce planning-only results with
    dispatch_eligible=False; this function never calls an adapter or runner.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if max_state_age < timedelta(0):
        raise ValueError("max_state_age must not be negative")

    accepted = (
        state.source_freshness_verified
        and state.is_fresh(now=now, max_age=max_state_age)
    )
    if not accepted:
        reason = (
            "Planning suppressed: observation is stale or source freshness "
            "has not been verified."
        )
        return tuple(
            ReadOnlyFleetAssessment(
                fleet_id=fleet.fleet_id,
                decision=None,
                reason=reason,
                observation_accepted=False,
            )
            for fleet in state.payload
        )

    return tuple(
        ReadOnlyFleetAssessment(
            fleet_id=fleet.fleet_id,
            decision=(plan := evaluate_expedition_fleet(fleet, now=now)).decision,
            reason=plan.reason,
            observation_accepted=True,
        )
        for fleet in state.payload
    )
