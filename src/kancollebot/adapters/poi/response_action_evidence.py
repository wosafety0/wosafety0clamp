"""Extract minimal current-session evidence from Poi Redux response actions.

Poi's current response pipeline validates the outer KCSAPI result before it
dispatches a response action, then replaces the action body with api_data when
present. This module retains neither response bodies nor request payloads.
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
    """Return compact evidence from an allowlisted Poi success action.

    In Poi's current data-resolver, only responses with outer api_result == 1
    reach onGameResponse; the action body is then api_data ?? body. Therefore a
    normal successful Redux action usually has no api_result field. If a
    malformed/untransformed body does include api_result, require exact numeric
    success rather than treating it as truthy.
    """
    if not isinstance(action, Mapping):
        return None
    if not isinstance(bridge_instance_id, str) or not bridge_instance_id.strip():
        return None
    action_type = action.get("type")
    if not isinstance(action_type, str) or action_type not in _ALLOWED_ACTIONS:
        return None

    payload = action.get("payload")
    if not isinstance(payload, Mapping):
        return None
    body = payload.get("body")
    if not isinstance(body, Mapping):
        return None

    # Successful responses are filtered before Poi dispatches these actions.
    # If an outer-envelope-shaped body is supplied anyway, reject non-success.
    if "api_result" in body:
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
