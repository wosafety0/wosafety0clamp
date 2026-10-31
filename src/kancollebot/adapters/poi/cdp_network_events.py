"""Bounded, allowlisted projection of passive CDP Network events."""
from __future__ import annotations
import re
from typing import Any
_SENSITIVE = re.compile(r"(?:cookie|authorization|password|passwd|secret|token|credential|session|api[_-]?key)", re.IGNORECASE)
_QUERY = re.compile(r"(?i)([?&][^=&#]*(?:token|key|password|passwd|secret|session|auth|credential|cookie)[^=&#]*=)[^&#]*")
_TEXT = re.compile(r'(?i)(\b(?:cookie|set-cookie|authorization|password|passwd|secret|token|api[_-]?key|session|credential|access[_-]?token|refresh[_-]?token|client[_-]?secret)\b\s*[=:]\s*)([^&;,\s"\\]+)')
_MAX_URL_LENGTH = 4096
_MAX_TEXT_LENGTH = 256

def _safe_text(value: Any, *, limit: int = _MAX_TEXT_LENGTH) -> str:
    text = str(value)[:limit]
    return _TEXT.sub(r'\1[REDACTED]', _QUERY.sub(r'\1[REDACTED]', text))

def _request_summary(request: Any) -> dict[str, Any]:
    if not isinstance(request, dict):
        return {}
    result: dict[str, Any] = {}
    if isinstance(request.get("url"), str):
        result["url"] = _safe_text(request["url"], limit=_MAX_URL_LENGTH)
    if isinstance(request.get("method"), str):
        result["method"] = _safe_text(request["method"], limit=16)
    body = request.get("postData")
    if isinstance(body, str):
        result["postData"] = {"present": True, "length": len(body), "content": "[OMITTED_FOR_SAFETY]"}
    return result

def sanitize_network_event(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    params = message.get("params")
    if not isinstance(method, str) or not method.startswith("Network.") or not isinstance(params, dict):
        return None
    result: dict[str, Any] = {"method": method, "params": {}}
    out = result["params"]
    if isinstance(params.get("requestId"), (str, int)):
        out["requestId"] = _safe_text(params["requestId"], limit=128)
    if isinstance(params.get("timestamp"), (int, float)):
        out["timestamp"] = params["timestamp"]
    if isinstance(params.get("type"), str):
        out["type"] = _safe_text(params["type"], limit=32)
    if method == "Network.requestWillBeSent":
        out["request"] = _request_summary(params.get("request"))
    elif method == "Network.responseReceived":
        response = params.get("response")
        if isinstance(response, dict):
            summary: dict[str, Any] = {}
            if isinstance(response.get("url"), str):
                summary["url"] = _safe_text(response["url"], limit=_MAX_URL_LENGTH)
            if isinstance(response.get("status"), (int, float)):
                summary["status"] = response["status"]
            if isinstance(response.get("mimeType"), str):
                summary["mimeType"] = _safe_text(response["mimeType"], limit=128)
            out["response"] = summary
    elif method == "Network.loadingFinished":
        if isinstance(params.get("encodedDataLength"), (int, float)):
            out["encodedDataLength"] = params["encodedDataLength"]
    elif method == "Network.loadingFailed":
        for key in ("errorText", "blockedReason", "corsErrorStatus"):
            if key in params and isinstance(params[key], (str, int, float, bool)):
                out[key] = _safe_text(params[key], limit=128)
    elif method == "Network.responseReceivedExtraInfo":
        if isinstance(params.get("statusCode"), (int, float)):
            out["statusCode"] = params["statusCode"]
    # Headers, cookies, certificate details, and arbitrary event fields are never queued.
    return result
