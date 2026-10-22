"""Strict validation contract for future Poi current-session evidence.

This module performs no I/O. It does not infer session validity from Redux state;
the bridge must explicitly provide a recent, successful response observation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


_ALLOWED_RESPONSE_PATHS = frozenset({
    "api_port/port",
    "api_get_member/require_info",
})


class PoiSessionEvidenceError(ValueError):
    """Raised when session evidence is absent, stale, or contradictory."""


def validate_session_evidence(
    value: Mapping[str, Any] | None,
    *,
    now: datetime,
    max_age: timedelta = timedelta(seconds=120),
) -> datetime:
    """Validate bridge-provided evidence and return its UTC observation time.

    Required contract fields:
    - bridgeInstanceId: non-empty opaque ID, changed on every bridge restart
    - responsePath: an explicitly allowlisted successful response
    - responseOk: exactly True
    - observedAt: timezone-aware ISO-8601 timestamp
    - invalidated: exactly False

    This validates the contract only; it cannot prove that a bridge truthfully
    observed the underlying Poi response or that a target runtime is compatible.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise PoiSessionEvidenceError("validation clock must be timezone-aware")
    if max_age < timedelta(0):
        raise PoiSessionEvidenceError("max_age must not be negative")
    if not isinstance(value, Mapping):
        raise PoiSessionEvidenceError("session evidence is missing")

    instance_id = value.get("bridgeInstanceId")
    if not isinstance(instance_id, str) or not instance_id.strip():
        raise PoiSessionEvidenceError("bridge instance identity is missing")
    if value.get("responsePath") not in _ALLOWED_RESPONSE_PATHS:
        raise PoiSessionEvidenceError("response path is not an approved session signal")
    if value.get("responseOk") is not True:
        raise PoiSessionEvidenceError("response was not explicitly successful")
    if value.get("invalidated") is not False:
        raise PoiSessionEvidenceError("session evidence is invalidated or ambiguous")

    raw_time = value.get("observedAt")
    if not isinstance(raw_time, str) or not raw_time.strip():
        raise PoiSessionEvidenceError("response observation time is missing")
    try:
        observed_at = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PoiSessionEvidenceError("response observation time is malformed") from exc
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise PoiSessionEvidenceError("response observation time must include a timezone")

    observed_at = observed_at.astimezone(timezone.utc)
    current = now.astimezone(timezone.utc)
    age = current - observed_at
    if age < timedelta(0):
        raise PoiSessionEvidenceError("response observation time is in the future")
    if age > max_age:
        raise PoiSessionEvidenceError("session evidence is stale")
    return observed_at
