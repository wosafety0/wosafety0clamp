"""Preflight checks for future action adapters; no actions are executed here."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Generic, TypeVar

from kancollebot.core.observed_state import ObservedState
from kancollebot.safety.policy import SafetyPolicy

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ActionPreflight:
    """Evidence that a proposed action passed local preflight checks."""

    action_name: str
    state_source: str
    state_observed_at: datetime
    checked_at: datetime


def authorize_planned_action(
    action_name: str,
    state: ObservedState[T],
    *,
    now: datetime,
    max_state_age: timedelta,
    policy: SafetyPolicy,
    confirmed: bool = False,
) -> ActionPreflight:
    """Validate a proposed action without sending it to a game client.

    This function checks freshness before the explicit safety policy. It is
    deliberately not an executor and performs no I/O or network operations.
    """
    if not action_name.strip():
        raise ValueError("action_name must not be empty")
    state.require_fresh(now=now, max_age=max_state_age)
    policy.authorize_live_action(confirmed=confirmed)
    return ActionPreflight(
        action_name=action_name,
        state_source=state.source,
        state_observed_at=state.observed_at,
        checked_at=now,
    )
