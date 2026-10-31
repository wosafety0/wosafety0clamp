"""Capture bounded, sanitized CDP Network summaries without sending game input.

Run this locally while Poi is open. The script only enables passive Network
observation; any game request observed must be generated naturally by the game.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from typing import Any
from urllib.parse import urlsplit

from kancollebot.adapters.poi.cdp_passive_observer import CdpPassiveNetworkObserver
from kancollebot.adapters.poi.cdp_websocket import connect_game_webview


DEFAULT_PATH = "/kcsapi/api_port/port"


def project_event(event: Any) -> dict[str, Any] | None:
    """Project one already-sanitized CDP event to minimal non-secret fields."""
    if not isinstance(event, dict):
        return None
    event_name = event.get("method")
    params = event.get("params")
    if not isinstance(params, dict):
        return None
    result: dict[str, Any] = {}
    if event_name == "Network.requestWillBeSent":
        request = params.get("request")
        if not isinstance(request, dict):
            return None
        url = request.get("url")
        if not isinstance(url, str) or len(url) > 4096:
            return None
        try:
            parsed = urlsplit(url)
        except ValueError:
            return None
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.path.startswith("/"):
            return None
        result["event"] = "request"
        result["path"] = parsed.path
        method = request.get("method")
        if isinstance(method, str):
            result["request_method"] = method[:16]
        body = request.get("postData")
        if isinstance(body, dict) and body.get("present") is True:
            length = body.get("length")
            if isinstance(length, int) and not isinstance(length, bool) and length >= 0:
                result["body_present"] = True
                result["body_length"] = length
    elif event_name == "Network.responseReceived":
        response = params.get("response")
        if not isinstance(response, dict):
            return None
        url = response.get("url")
        if not isinstance(url, str) or len(url) > 4096:
            return None
        try:
            parsed = urlsplit(url)
        except ValueError:
            return None
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.path.startswith("/"):
            return None
        result["event"] = "response"
        result["path"] = parsed.path
        status = response.get("status")
        if isinstance(status, (int, float)) and not isinstance(status, bool) and math.isfinite(status):
            result["status"] = status
        mime = response.get("mimeType")
        if isinstance(mime, str):
            result["mime_type"] = mime[:128]
    else:
        return None
    request_id = params.get("requestId")
    if isinstance(request_id, (str, int)) and not isinstance(request_id, bool):
        result["request_id"] = str(request_id)[:128]
    timestamp = params.get("timestamp")
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool) and math.isfinite(timestamp):
        result["timestamp"] = timestamp
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:9222")
    parser.add_argument("--url-hint", default="kcs2/index.php", help="Unique substring for the game WebView URL")
    parser.add_argument("--duration-seconds", type=float, default=60.0)
    parser.add_argument("--max-events", type=int, default=20)
    parser.add_argument("--path", action="append", dest="paths")
    args = parser.parse_args()

    if not math.isfinite(args.duration_seconds) or not 5 <= args.duration_seconds <= 300:
        parser.error("--duration-seconds must be finite and between 5 and 300")
    if not 1 <= args.max_events <= 100:
        parser.error("--max-events must be between 1 and 100")
    paths = set(args.paths or [DEFAULT_PATH])
    if any(not path.startswith("/") or "?" in path or "#" in path for path in paths):
        parser.error("--path must be an exact absolute URL path without query or fragment")

    records: list[dict[str, Any]] = []
    try:
        with connect_game_webview(args.endpoint, url_hint=args.url_hint) as session:
            observer = CdpPassiveNetworkObserver(
                session.transport, allowed_paths=paths, max_batch_size=100
            )
            try:
                observer.start()
                deadline = time.monotonic() + args.duration_seconds
                while len(records) < args.max_events:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    batch = observer.collect(
                        timeout=min(1.0, remaining),
                        max_events=min(100, args.max_events - len(records)),
                    )
                    for event in batch:
                        projected = project_event(event)
                        if projected is not None:
                            records.append(projected)
                            if len(records) >= args.max_events:
                                break
            finally:
                observer.close()
        report = {
            "status": "PASS_READ_ONLY_OBSERVATION" if records else "NO_MATCHING_EVENTS",
            "event_count": len(records),
            "events": records,
            "allowed_paths": sorted(paths),
            "input_sent": False,
            "game_action_sent": False,
            "request_interception_enabled": False,
            "request_replay_sent": False,
            "raw_headers_or_bodies_saved": False,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if records else 1
    except Exception as exc:
        print(json.dumps({
            "status": "FAILED",
            "error_type": type(exc).__name__,
            "input_sent": False,
            "game_action_sent": False,
            "request_interception_enabled": False,
            "request_replay_sent": False,
            "raw_headers_or_bodies_saved": False,
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
