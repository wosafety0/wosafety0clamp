"""Extract minimal current-session evidence from Poi Redux response actions.

This module mirrors the narrow contract a Poi extension reducer must implement.
It deliberately retains no response body or request payload.
"""

from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping


_ALLOWED_ACTIONS = frozenset(
    {
        "@@Response/kcsapi/api_port/port",
        "@@Response/kcsapi/api_get_member/require_info",
    }
)


def evidence_from_poi_response_action(
    action: Mapping[str, Any] | None,
    *,
    bridge_instance_id: str,
) -> dict[str, Any] | None:
    """Return a compact evidence record for an allowlisted successful response.

    Poi's response action uses payload.body and payload.time. The time value is
    treated as Unix epoch milliseconds, matching JavaScript Date.now semantics.
    Unknown shapes, failed responses and untrusted timestamps return None.
    """
    if not isinstance(action, Mapping):
        return None
    if not isinstance(bridge_instance_id, str) or not bridge_instance_id.strip():
        return None
    action_type = action.get("type")
    if action_type not in _ALLOWED_ACTIONS:
        return None

    payload = action.get("payload")
    if not isinstance(payload, Mapping):
        return None
    body = payload.get("body")
    if not isinstance(body, Mapping):
        return None
    # KCSAPI success is explicitly numeric api_result == 1; do not accept True,
    # since bool is a subclass of int in Python.
    result = body.get("api_result")
    if type(result) is not int or result != 1:
        return None

    timestamp = payload.get("time")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        return None
    if not isfinite(timestamp) or timestamp <= 0:
        return None
    try:
        observed = datetime.fromtimestamp(timestamp / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None

    path = action_type.removeprefix("@@Response/kcsapi/")
    return {
        "bridgeInstanceId": bridge_instance_id.strip(),
        "responsePath": path,
        "responseOk": True,
        "observedAt": observed.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "invalidated": False,
    }
