"""Fail-closed expedition cycle transitions without gameplay I/O.

An action is represented as a pending intent only. No method here can issue
clicks, send game requests, or automatically retry an uncertain outcome.
"""
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
from collections.abc import Sequence

CycleStatus = Literal["ready", "awaiting_observation", "stopped"]


@dataclass(frozen=True, slots=True)
class ExpeditionCycle:
    status: CycleStatus
    pending: ExpeditionDecision | None
    reason: str


def stage_expedition_decision(
    decision: ExpeditionDecision, *, previous: ExpeditionCycle | None = None
) -> ExpeditionCycle:
    """Stage at most one externally reviewed intent; never execute it."""
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
    return ExpeditionCycle("awaiting_observation", decision, "Action intent staged; no action executed.")


def resolve_expedition_cycle(
    cycle: ExpeditionCycle,
    *,
    before: ObservedState[Sequence[FleetObservation]],
    after: ObservedState[Sequence[FleetObservation]] | None,
    now: datetime,
    max_age: timedelta,
    action_error: str | None = None,
) -> tuple[ExpeditionCycle, ExpeditionActionVerification | None]:
    """Resolve once from new observations; failure/ambiguity locks the cycle."""
    if cycle.status != "awaiting_observation" or cycle.pending is None:
        return ExpeditionCycle("stopped", None, "No pending action to resolve."), None
    if action_error is not None:
        return ExpeditionCycle("stopped", None, "Action error reported; no automatic retry."), None
    if after is None:
        return ExpeditionCycle("stopped", None, "Post-action observation unavailable; no automatic retry."), None
    pending = cycle.pending
    outcome = verify_expedition_action(
        pending.action,
        fleet_id=pending.fleet_id,
        mission_id=pending.mission_id,
        before=before,
        after=after,
        now=now,
        max_age=max_age,
    )
    if outcome.status != "confirmed":
        return ExpeditionCycle("stopped", None, outcome.reason), outcome
    return ExpeditionCycle("ready", None, outcome.reason), outcome
