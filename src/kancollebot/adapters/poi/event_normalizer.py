"""Conservative, side-effect-free normalization for sanitized Poi webhook events.

This module does not infer state from request/error events or response bodies
whose success semantics are not established.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Mapping
from urllib.parse import urlsplit


@dataclass(frozen=True)
class NormalizedForwarderEvent:
    """A validated event envelope; not a complete game-state snapshot."""

    path: str
    stage: str
    observed_at: datetime | None
    response: Mapping[str, Any] | None
    state_eligible: bool
    reason: str


def _normalize_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if candidate.startswith(("http://", "https://")):
        try:
            candidate = urlsplit(candidate).path
        except ValueError:
            return None
    else:
        candidate = candidate.split("#", 1)[0].split("?", 1)[0]
    if not candidate or not candidate.startswith(("/", "kcs2/")):
        return None
    return candidate


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            numeric = float(value)
        except (OverflowError, TypeError, ValueError):
            return None
        if not math.isfinite(numeric) or numeric <= 0:
            return None
        # Poi Forwarder may use Date.now() milliseconds or Unix seconds.
        if numeric >= 100_000_000_000:
            numeric /= 1000.0
        try:
            return datetime.fromtimestamp(numeric, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        try:
            return parsed.astimezone(timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def normalize_forwarder_event(event: Mapping[str, Any]) -> NormalizedForwarderEvent:
    """Validate an event envelope without IO or state mutation.

    Only an explicitly labelled response with a mapping body and api_result=1
    is eligible for downstream schema-specific parsing. This does not make
    the event a complete snapshot or authorize an action.
    """
    if not isinstance(event, Mapping):
        raise TypeError("event must be a mapping")

    path = _normalize_path(event.get("path"))
    if path is None:
        return NormalizedForwarderEvent("", "unknown", None, None, False, "invalid_path")
    if not path.startswith("/kcsapi/"):
        return NormalizedForwarderEvent(path, "unknown", None, None, False, "unsupported_resource_path")

    stage_value = event.get("stage")
    stage = stage_value.strip().lower() if isinstance(stage_value, str) else "unknown"
    observed_at = _parse_timestamp(event.get("timestamp"))

    # The inspected Poi Forwarder emits response_received. Accept the older
    # response label only for compatibility with existing fixtures/versions.
    if stage not in {"response", "response_received"}:
        return NormalizedForwarderEvent(path, stage, observed_at, None, False, "not_a_response_stage")

    response = event.get("response")
    if not isinstance(response, Mapping):
        return NormalizedForwarderEvent(path, stage, observed_at, None, False, "missing_or_invalid_response")

    if type(response.get("api_result")) is not int or response.get("api_result") != 1:
        return NormalizedForwarderEvent(path, stage, observed_at, response, False, "api_success_not_confirmed")

    if observed_at is None:
        return NormalizedForwarderEvent(path, stage, None, response, False, "invalid_or_missing_timestamp")

    return NormalizedForwarderEvent(path, stage, observed_at, response, True, "validated_response_envelope")
