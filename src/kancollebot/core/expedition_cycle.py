"""Fail-closed expedition cycle transitions without gameplay I/O.

Each staged intent is bound to the exact pre-action observation that produced
it. No method here can issue clicks, send game requests, or retry uncertainty.
"""
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from kancollebot.core.expedition_planner import ExpeditionDecision
from kancollebot.core.expedition_verification import (
    ExpeditionActionVerification,
    verify_expedition_action,
)
from kancollebot.core.observed_state import ObservedState
from kancollebot.core.fleet_parser import FleetObservation

FleetSnapshot = ObservedState[Sequence[FleetObservation]]
CycleStatus = Literal["ready", "awaiting_observation", "stopped"]


@dataclass(frozen=True, slots=True)
class ExpeditionCycle:
    status: CycleStatus
    pending: ExpeditionDecision | None
    reason: str
    baseline: FleetSnapshot | None = None


def stage_expedition_decision(
    decision: ExpeditionDecision,
    *,
    before: FleetSnapshot | None = None,
    previous: ExpeditionCycle | None = None,
) -> ExpeditionCycle:
    """Stage one intent bound to its planning snapshot; never execute it."""
    if previous is not None and previous.status != "ready":
        return ExpeditionCycle("stopped", None, "Previous cycle is unresolved; manual review required.")
    if decision.action == "wait":
        return ExpeditionCycle("ready", None, decision.reason)
    if decision.action not in {"collect", "dispatch"}:
        return ExpeditionCycle("stopped", None, "Unsupported expedition decision.")
    if isinstance(decision.fleet_id, bool) or not isinstance(decision.fleet_id, int) or decision.fleet_id <= 0:
        return ExpeditionCycle("stopped", None, "Invalid target fleet.")
    if decision.action == "dispatch" and (
        isinstance(decision.mission_id, bool)
        or not isinstance(decision.mission_id, int)
        or decision.mission_id <= 0
    ):
        return ExpeditionCycle("stopped", None, "Invalid dispatch mission.")
    if before is None:
        return ExpeditionCycle("stopped", None, "Planning observation missing; intent not staged.")
    if not isinstance(before, ObservedState):
        return ExpeditionCycle("stopped", None, "Planning observation is invalid; intent not staged.")
    if before.observed_at.tzinfo is None or before.observed_at.utcoffset() is None or not before.source.strip():
        return ExpeditionCycle("stopped", None, "Planning observation metadata is invalid; intent not staged.")
    matches = [item for item in before.payload if isinstance(item, FleetObservation) and item.fleet_id == decision.fleet_id]
    if len(matches) != 1:
        return ExpeditionCycle("stopped", None, "Target fleet missing or duplicated in planning observation.")
    expected_state = 2 if decision.action == "collect" else 0
    if matches[0].mission_state_code != expected_state:
        return ExpeditionCycle("stopped", None, "Planning observation does not satisfy the action precondition.")
    return ExpeditionCycle(
        "awaiting_observation", decision,
        "Action intent staged against its planning observation; no action executed.",
        before,
    )


def resolve_expedition_cycle(
    cycle: ExpeditionCycle,
    *,
    after: FleetSnapshot | None,
    now: datetime,
    max_age: timedelta,
    action_error: str | None = None,
) -> tuple[ExpeditionCycle, ExpeditionActionVerification | None]:
    """Resolve against the exact staged baseline; failure locks the cycle."""
    if cycle.status != "awaiting_observation" or cycle.pending is None or cycle.baseline is None:
        return ExpeditionCycle("stopped", None, "No valid pending action and baseline to resolve."), None
    if action_error is not None:
        return ExpeditionCycle("stopped", None, "Action error reported; no automatic retry."), None
    if after is None:
        return ExpeditionCycle("stopped", None, "Post-action observation unavailable; no automatic retry."), None
    pending = cycle.pending
    outcome = verify_expedition_action(
        pending.action,
        fleet_id=pending.fleet_id,
        mission_id=pending.mission_id,
        before=cycle.baseline,
        after=after,
        now=now,
        max_age=max_age,
    )
    if outcome.status != "confirmed":
        return ExpeditionCycle("stopped", None, outcome.reason), outcome
    return ExpeditionCycle("ready", None, outcome.reason), outcome
