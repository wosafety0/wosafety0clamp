"""Discover Poi renderer targets through loopback-only Chrome DevTools endpoints.

This module performs discovery only. It does not open the returned WebSocket,
send CDP commands, or interact with the game.
"""
from __future__ import annotations

import ipaddress
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DEFAULT_CDP_ENDPOINT = "http://127.0.0.1:9222"


class CdpDiscoveryError(RuntimeError):
    """Raised when the DevTools endpoint cannot be safely inspected."""


@dataclass(frozen=True, slots=True)
class CdpTarget:
    target_id: str
    title: str
    url: str
    websocket_url: str


def _validate_loopback_http_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("CDP endpoint must be an absolute HTTP URL")
    try:
        is_loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        is_loopback = parsed.hostname.lower() == "localhost"
    if not is_loopback:
        raise ValueError("CDP endpoint must use a loopback host")
    if parsed.username or parsed.password:
        raise ValueError("Credentials in CDP endpoint URLs are not allowed")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("CDP endpoint must be a base URL without path, query, or fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("CDP endpoint has an invalid port") from exc
    if port is None or not 1 <= port <= 65535:
        raise ValueError("CDP endpoint must include a valid TCP port")
    # Normalize localhost to an IP literal so the HTTP client cannot resolve
    # the hostname to a non-loopback address through a modified hosts file.
    authority = f"127.0.0.1:{port}" if parsed.hostname.lower() == "localhost" else parsed.netloc
    return f"http://{authority}"


def _validate_websocket_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "ws" or not parsed.hostname:
        raise CdpDiscoveryError("DevTools target has a non-WebSocket debugger URL")
    try:
        is_loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        is_loopback = parsed.hostname.lower() == "localhost"
    if not is_loopback:
        raise CdpDiscoveryError("DevTools target points to a non-loopback WebSocket")
    if parsed.username or parsed.password:
        raise CdpDiscoveryError("Credentials in debugger WebSocket URL are not allowed")
    try:
        port = parsed.port
    except ValueError as exc:
        raise CdpDiscoveryError("Debugger WebSocket URL has an invalid port") from exc
    if port is None or not 1 <= port <= 65535:
        raise CdpDiscoveryError("Debugger WebSocket URL must include a valid port")
    # The probe opens this URL later; avoid a second hostname resolution for
    # localhost by returning a validated loopback IP literal instead.
    if parsed.hostname.lower() == "localhost":
        authority = f"127.0.0.1:{port}"
        return parsed._replace(netloc=authority).geturl()
    return url


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Never follow DevTools redirects outside the validated loopback endpoint."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _get_json(url: str, timeout: float) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    # Do not let environment-configured HTTP proxies intercept this loopback-only
    # request. The endpoint is local DevTools, and proxy routing would violate
    # the loopback boundary even though the URL itself was validated.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RejectRedirects())
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise CdpDiscoveryError(f"DevTools endpoint returned HTTP {response.status}")
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise CdpDiscoveryError(f"DevTools endpoint returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CdpDiscoveryError(f"Cannot reach local DevTools endpoint ({type(exc).__name__})") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise CdpDiscoveryError("DevTools response exceeded 2 MiB")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CdpDiscoveryError("DevTools endpoint did not return valid JSON") from exc


def list_page_targets(endpoint: str = DEFAULT_CDP_ENDPOINT, *, timeout: float = 2.0) -> tuple[CdpTarget, ...]:
    """Return validated page targets without exposing non-loopback endpoints."""
    base = _validate_loopback_http_url(endpoint)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError("timeout must be finite and positive")
    raw = _get_json(base + "/json/list", timeout)
    if not isinstance(raw, list):
        raise CdpDiscoveryError("DevTools target list is not an array")
    targets: list[CdpTarget] = []
    for item in raw:
        if not isinstance(item, dict) or item.get("type") != "page":
            continue
        target_id, title, url, websocket_url = (
            item.get("id"), item.get("title"), item.get("url"), item.get("webSocketDebuggerUrl")
        )
        if not all(isinstance(value, str) and value for value in (target_id, title, url, websocket_url)):
            continue
        try:
            validated_ws = _validate_websocket_url(websocket_url)
        except CdpDiscoveryError:
            # A suspicious page target invalidates discovery rather than being silently trusted.
            raise
        targets.append(CdpTarget(target_id, title, url, validated_ws))
    return tuple(targets)


def select_poi_renderer(
    targets: tuple[CdpTarget, ...],
    *,
    title_hint: str = "poi",
    url_hint: str = "",
) -> CdpTarget:
    """Select exactly one page matching explicit hints; ambiguity is an error."""
    if not title_hint.strip() and not url_hint.strip():
        raise ValueError("at least one target hint is required")
    title_needle = title_hint.casefold().strip()
    url_needle = url_hint.casefold().strip()
    matches = tuple(
        target for target in targets
        if (not title_needle or title_needle in target.title.casefold())
        and (not url_needle or url_needle in target.url.casefold())
    )
    if len(matches) != 1:
        raise CdpDiscoveryError(
            f"Expected exactly one Poi page target, found {len(matches)}; refusing to guess"
        )
    return matches[0]


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=DEFAULT_CDP_ENDPOINT)
    parser.add_argument("--title-hint", default="poi")
    parser.add_argument("--url-hint", default="")
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args()
    try:
        targets = list_page_targets(args.endpoint, timeout=args.timeout)
        selected = select_poi_renderer(
            targets, title_hint=args.title_hint, url_hint=args.url_hint
        )
        print(json.dumps({
            "status": "PASS_DISCOVERY_ONLY",
            "target_count": len(targets),
            "selected": {
                "id": selected.target_id,
                "title": selected.title,
                "url": selected.url,
                "websocket_endpoint": selected.websocket_url,
            },
            "actions_enabled": False,
            "note": "Discovery only; no WebSocket connection or game input was performed.",
        }, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({
            "status": "FAILED",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "actions_enabled": False,
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
