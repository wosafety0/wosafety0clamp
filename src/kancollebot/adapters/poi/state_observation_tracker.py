"""Track fresh, validated Poi response events for page-health recovery gating.

Only successful KCSAPI response events from a small state-relevant endpoint
allowlist can advance the observation timestamp. Untrusted, malformed, stale,
future-dated, request-stage, and failed responses are ignored.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from threading import Lock
from typing import Any


_STATE_PATHS = frozenset({
    "/kcsapi/api_port/port",
    "/kcsapi/api_get_member/require_info",
    "/kcsapi/api_get_member/deck",
    "/kcsapi/api_get_member/ship2",
    "/kcsapi/api_get_member/ship3",
    "/kcsapi/api_get_member/slot_item",
    "/kcsapi/api_get_member/material",
})
_MAX_FUTURE_SKEW_SECONDS = 5.0


def _parse_event_timestamp(value: Any) -> datetime | None:
    """Parse only numeric Unix seconds/milliseconds; reject ambiguous strings."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or numeric <= 0:
        return None
    # Poi Date.now() is milliseconds; some forwarders serialize Unix seconds.
    if numeric >= 100_000_000_000:
        numeric /= 1000.0
    try:
        return datetime.fromtimestamp(numeric, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


class PoiStateObservationTracker:
    """Thread-safe timestamp tracker backed only by validated Poi response events."""

    def __init__(self, *, max_age_seconds: float = 30.0) -> None:
        if (
            isinstance(max_age_seconds, bool)
            or not isinstance(max_age_seconds, (int, float))
            or not math.isfinite(float(max_age_seconds))
            or max_age_seconds <= 0
        ):
            raise ValueError("max_age_seconds must be a positive number")
        self.max_age_seconds = float(max_age_seconds)
        self._latest_at: datetime | None = None
        self._lock = Lock()

    @property
    def latest_valid_state_at(self) -> datetime | None:
        """Return the latest accepted source timestamp, never a read-time timestamp."""
        with self._lock:
            return self._latest_at

    def observe_event(self, event: Any, *, now: datetime | None = None) -> bool:
        """Accept a successful state-relevant response event if its source time is plausible."""
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if not isinstance(event, dict):
            return False
        if event.get("stage") not in {"response", "response_received"} or event.get("path") not in _STATE_PATHS:
            return False
        response = event.get("response")
        if not isinstance(response, dict) or type(response.get("api_result")) is not int or response.get("api_result") != 1:
            return False
        timestamp = _parse_event_timestamp(event.get("timestamp"))
        if timestamp is None:
            return False
        try:
            age = (current - timestamp).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return False
        if age < -_MAX_FUTURE_SKEW_SECONDS or age > self.max_age_seconds:
            return False
        with self._lock:
            if self._latest_at is None or timestamp > self._latest_at:
                self._latest_at = timestamp
        return True
