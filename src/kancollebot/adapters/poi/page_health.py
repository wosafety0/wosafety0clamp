"""Fail-closed policy for POI renderer health and recovery freshness.

This is a pure policy module. It performs no CDP I/O and never triggers gameplay.
A future probe must supply evidence from the selected renderer and the state
adapter. Discovery or document readiness alone is not proof that game data is fresh.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum


class PageHealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class PageHealthObservation:
    checked_at: datetime
    target_found: bool | None
    document_ready_state: str | None
    title: str = ""
    url: str = ""
    error_text: str = ""
    latest_valid_state_at: datetime | None = None

    def __post_init__(self) -> None:
        if not _aware(self.checked_at):
            raise ValueError("checked_at must be timezone-aware")
        if self.latest_valid_state_at is not None and not _aware(self.latest_valid_state_at):
            raise ValueError("latest_valid_state_at must be timezone-aware")
        if self.target_found not in (True, False, None):
            raise ValueError("target_found must be True, False, or None")
        if self.document_ready_state is not None and not isinstance(self.document_ready_state, str):
            raise ValueError("document_ready_state must be a string or None")
        if not all(isinstance(v, str) for v in (self.title, self.url, self.error_text)):
            raise ValueError("title, url, and error_text must be strings")


@dataclass(frozen=True, slots=True)
class PageHealthDecision:
    status: PageHealthStatus
    reasons: tuple[str, ...]


def _aware(value: datetime) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


_ERROR_MARKERS = (
    "err_connection_closed",
    "err_connection_reset",
    "err_connection_refused",
    "this site can’t be reached",
    "this site can't be reached",
    "webpage not available",
)


def evaluate_page_health(
    observation: PageHealthObservation | None,
    *,
    now: datetime,
    max_probe_age: timedelta = timedelta(seconds=5),
    max_state_age: timedelta = timedelta(seconds=15),
    connection_failure_at: datetime | None = None,
) -> PageHealthDecision:
    """Evaluate renderer evidence; any missing/contradictory evidence fails closed."""
    if not _aware(now):
        raise ValueError("now must be timezone-aware")
    for name, value in (("max_probe_age", max_probe_age), ("max_state_age", max_state_age)):
        if not isinstance(value, timedelta) or value < timedelta(0):
            raise ValueError(f"{name} must be a non-negative timedelta")
    if connection_failure_at is not None and not _aware(connection_failure_at):
        raise ValueError("connection_failure_at must be timezone-aware")

    if observation is None:
        return PageHealthDecision(PageHealthStatus.UNKNOWN, ("page_probe_missing",))
    try:
        probe_age = now - observation.checked_at
    except (TypeError, ValueError, OverflowError):
        return PageHealthDecision(PageHealthStatus.UNKNOWN, ("incompatible_probe_timestamp",))
    if probe_age < timedelta(0) or probe_age > max_probe_age:
        return PageHealthDecision(PageHealthStatus.UNKNOWN, ("page_probe_stale_or_future_dated",))
    if observation.target_found is False:
        return PageHealthDecision(PageHealthStatus.UNHEALTHY, ("poi_page_target_missing",))
    if observation.target_found is not True:
        return PageHealthDecision(PageHealthStatus.UNKNOWN, ("poi_page_target_unverified",))

    combined_error = (observation.title + " " + observation.url + " " + observation.error_text).casefold()
    if any(marker in combined_error for marker in _ERROR_MARKERS):
        return PageHealthDecision(PageHealthStatus.UNHEALTHY, ("browser_connection_error_detected",))
    if observation.document_ready_state != "complete":
        return PageHealthDecision(PageHealthStatus.UNKNOWN, ("document_not_confirmed_complete",))

    state_at = observation.latest_valid_state_at
    if state_at is None:
        return PageHealthDecision(PageHealthStatus.UNKNOWN, ("valid_game_state_observation_missing",))
    try:
        state_age = now - state_at
    except (TypeError, ValueError, OverflowError):
        return PageHealthDecision(PageHealthStatus.UNKNOWN, ("incompatible_state_timestamp",))
    if state_age < timedelta(0) or state_age > max_state_age:
        return PageHealthDecision(PageHealthStatus.UNKNOWN, ("game_state_stale_or_future_dated",))
    if connection_failure_at is not None:
        try:
            recovered_after_failure = state_at > connection_failure_at
        except (TypeError, ValueError, OverflowError):
            recovered_after_failure = False
        if not recovered_after_failure:
            return PageHealthDecision(
                PageHealthStatus.UNHEALTHY,
                ("fresh_state_after_connection_failure_not_confirmed",),
            )
    return PageHealthDecision(PageHealthStatus.HEALTHY, ())


def require_healthy_page(decision: PageHealthDecision) -> None:
    """Raise unless health is explicitly HEALTHY; UNKNOWN is not permission."""
    if not isinstance(decision, PageHealthDecision):
        raise TypeError("decision must be a PageHealthDecision")
    if decision.status is not PageHealthStatus.HEALTHY:
        detail = ", ".join(decision.reasons) or "unspecified_health_failure"
        raise RuntimeError("POI page health gate blocked: " + detail)
