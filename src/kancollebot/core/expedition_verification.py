"""Verify expedition actions from independent, fresh POI observations.

This module never performs gameplay actions and never retries them. Any result
other than a confirmed state transition requires the caller to stop and obtain
fresh observations or human review.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from kancollebot.core.fleet_parser import FleetObservation
from kancollebot.core.observed_state import ObservedState

VerificationStatus = Literal["confirmed", "not_confirmed", "unknown"]


@dataclass(frozen=True, slots=True)
class ExpeditionActionVerification:
    """Outcome assessment; this is not permission to issue another action."""

    status: VerificationStatus
    stop_required: bool
    reason: str


def _unknown(reason: str) -> ExpeditionActionVerification:
    return ExpeditionActionVerification("unknown", True, reason)


def _not_confirmed(reason: str) -> ExpeditionActionVerification:
    return ExpeditionActionVerification("not_confirmed", True, reason)


def _confirmed(reason: str) -> ExpeditionActionVerification:
    return ExpeditionActionVerification("confirmed", False, reason)


def verify_expedition_action(
    action: str,
    *,
    fleet_id: int,
    mission_id: int | None,
    before: ObservedState[Sequence[FleetObservation]],
    after: ObservedState[Sequence[FleetObservation]],
    now: datetime,
    max_age: timedelta,
) -> ExpeditionActionVerification:
    """Verify collect/dispatch using two independent, fresh observations.

    A collection is confirmed only by completed -> idle. A dispatch is
    confirmed only by idle -> underway for the requested mission. A valid
    unchanged state is not confirmed, not a reason to retry. Stale snapshots,
    mismatched sources, duplicate/missing fleets, invalid preconditions, or
    unsupported states are unknown and require stopping.
    """
    if action not in {"collect", "dispatch"}:
        return _unknown("Unsupported expedition action; stop for review.")
    if isinstance(fleet_id, bool) or not isinstance(fleet_id, int) or fleet_id <= 0:
        return _unknown("Invalid fleet ID; stop for review.")
    if action == "dispatch" and (
        isinstance(mission_id, bool)
        or not isinstance(mission_id, int)
        or mission_id <= 0
    ):
        return _unknown("Dispatch mission ID is missing or invalid; stop for review.")
    if before.source != after.source:
        return _unknown("Pre-action and post-action observations have different sources.")
    try:
        before.require_fresh(now=now, max_age=max_age)
        after.require_fresh(now=now, max_age=max_age)
    except (TypeError, ValueError):
        return _unknown("Pre-action or post-action observation is stale or has invalid timestamps.")
    if after.observed_at <= before.observed_at:
        return _unknown("Post-action observation is not newer than the pre-action observation.")

    before_matches = [item for item in before.payload if item.fleet_id == fleet_id]
    after_matches = [item for item in after.payload if item.fleet_id == fleet_id]
    if len(before_matches) != 1 or len(after_matches) != 1:
        return _unknown("Target fleet is missing or duplicated in an observation.")

    old = before_matches[0]
    new = after_matches[0]
    if action == "collect":
        if old.mission_state_code != 2:
            return _unknown("Collection precondition was not observed as completed.")
        if new.mission_state_code == 0:
            return _confirmed("Collection confirmed by completed -> idle state transition.")
        if new.mission_state_code == 2:
            return _not_confirmed("Collection is not reflected in the latest observation; do not retry automatically.")
        return _unknown("Post-collection fleet state is unexpected or unknown; stop for review.")

    if old.mission_state_code != 0:
        return _unknown("Dispatch precondition was not observed as idle.")
    if new.mission_state_code == 1 and new.mission_task_id == mission_id:
        return _confirmed("Dispatch confirmed by idle -> underway state transition for the requested mission.")
    if new.mission_state_code == 0:
        return _not_confirmed("Dispatch is not reflected in the latest observation; do not retry automatically.")
    if new.mission_state_code == 1 and new.mission_task_id != mission_id:
        return _unknown("Fleet is underway on a different or unverified mission; stop for review.")
    return _unknown("Post-dispatch fleet state is unexpected or unknown; stop for review.")
