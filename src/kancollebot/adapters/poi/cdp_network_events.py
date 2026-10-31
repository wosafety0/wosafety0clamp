"""Redaction helpers for passive CDP Network events."""
from __future__ import annotations
import json
import re
import urllib.parse
from typing import Any
_SENSITIVE = re.compile(r"(?:cookie|authorization|password|passwd|secret|token|credential|session|api[_-]?key)", re.IGNORECASE)
_QUERY = re.compile(r"(?i)([?&][^=&#]*(?:token|key|password|passwd|secret|session|auth|credential|cookie)[^=&#]*=)[^&#]*")
_TEXT = re.compile(r"(?i)(\b(?:cookie|set-cookie|authorization|password|passwd|secret|token|api[_-]?key|session)\b\s*[=:]\s*)([^&;,\s\"]+)")

def _clean(key: str, value: Any) -> Any:
    if _SENSITIVE.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _clean(str(k), v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(key, item) for item in value]
    if not isinstance(value, str):
        return value
    if key.lower() == "postdata":
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            pairs = urllib.parse.parse_qsl(value, keep_blank_values=True)
            if not pairs or "=" not in value:
                return "[REDACTED_UNPARSED_BODY]"
            return urllib.parse.urlencode([(k, '[REDACTED]' if _SENSITIVE.search(k) else v) for k, v in pairs])
        return json.dumps(_clean("postData", parsed), separators=(",", ":"))
    return _TEXT.sub(r'\1[REDACTED]', _QUERY.sub(r'\1[REDACTED]', value))

def sanitize_network_event(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    params = message.get("params")
    if not isinstance(method, str) or not method.startswith("Network.") or not isinstance(params, dict):
        return None
    return {"method": method, "params": _clean("params", params)}
