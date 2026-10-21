"""Read-only verifier for supported Poi local data bridges.

No dependencies beyond the Python standard library. This script never sends
game actions and prints structural summaries rather than raw game data.
Run with: PYTHONPATH=src python tools/verify_poi_bridge.py --bridge nanodesu
or:       python tools/verify_poi_bridge.py --bridge poi-plugin-mcp
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DEFAULT_NANODESU_ENDPOINT = "http://127.0.0.1:12450/mcp"
DEFAULT_PLUGIN_MCP_ENDPOINT = "http://127.0.0.1:17777"


def validate_loopback_url(url: str) -> str:
    """Validate an HTTP URL with a literal loopback IP or localhost hostname."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("Only absolute HTTP URLs are supported")
    try:
        loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        loopback = parsed.hostname.lower() == "localhost"
    if not loopback:
        raise ValueError("Only loopback endpoints are allowed")
    if parsed.username or parsed.password:
        raise ValueError("Credentials in endpoint URLs are not allowed")
    return url.rstrip("/")


def summarize(value: Any) -> dict[str, Any]:
    """Describe payload shape without exposing field values."""
    result: dict[str, Any] = {"type": type(value).__name__}
    if isinstance(value, dict):
        result["keys"] = sorted(str(key) for key in value.keys())[:80]
        result["key_count"] = len(value)
        for candidate in ("items", "fleets", "ships", "data", "results"):
            child = value.get(candidate)
            if isinstance(child, (list, dict)):
                result["collection_field"] = candidate
                result["collection_count"] = len(child)
                break
    elif isinstance(value, (list, tuple)):
        result["item_count"] = len(value)
        if value:
            result["first_item_type"] = type(value[0]).__name__
            if isinstance(value[0], dict):
                result["first_item_keys"] = sorted(str(key) for key in value[0].keys())[:40]
    elif isinstance(value, str):
        result["string_length"] = len(value)
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            pass
        else:
            nested = summarize(parsed)
            result["json_payload"] = nested
    return result


def http_get_json(url: str, timeout: float = 3.0) -> Any:
    validate_loopback_url(url)
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            status = response.status
    except urllib.error.HTTPError as exc:
        detail = exc.read(1024).decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Cannot reach {url}: {exc}") from exc
    if status != 200:
        raise RuntimeError(f"Unexpected HTTP status {status} from {url}")
    if len(body) > MAX_RESPONSE_BYTES:
        raise RuntimeError(f"Response from {url} exceeded 2 MiB")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Endpoint did not return valid JSON: {url}") from exc


def resolve_plugin_mcp_endpoint() -> str:
    """Read the documented port file, falling back to port 17777."""
    port = 17777
    profile = os.environ.get("USERPROFILE")
    if profile:
        port_file = Path(profile) / ".poi-mcp" / "port"
        try:
            raw = port_file.read_text(encoding="utf-8").strip()
        except OSError:
            raw = ""
        if raw:
            try:
                candidate = int(raw)
            except ValueError as exc:
                raise ValueError("Poi MCP port file does not contain an integer") from exc
            if not 1 <= candidate <= 65535:
                raise ValueError("Poi MCP port file is outside the valid TCP port range")
            port = candidate
    return f"http://127.0.0.1:{port}"


def summarize_mcp_result(result: dict[str, Any]) -> dict[str, Any]:
    """Summarize MCP result content without printing raw values."""
    summary: dict[str, Any] = {"keys": sorted(result.keys())}
    content = result.get("content")
    if isinstance(content, list):
        summary["content_count"] = len(content)
        summary["content"] = []
        for item in content[:8]:
            if not isinstance(item, dict):
                summary["content"].append({"type": type(item).__name__})
                continue
            entry = {"type": item.get("type", "unknown")}
            text_value = item.get("text")
            if isinstance(text_value, str):
                entry["text"] = summarize(text_value)
            if isinstance(item.get("data"), str):
                entry["data_length"] = len(item["data"])
                entry["mimeType"] = item.get("mimeType")
            summary["content"].append(entry)
    structured = result.get("structuredContent")
    if isinstance(structured, (dict, list)):
        summary["structuredContent"] = summarize(structured)
    return summary


def run_nanodesu(endpoint: str, timeout: float) -> dict[str, Any]:
    from kancollebot.adapters.poi.mcp_client import PoiMcpClient

    client = PoiMcpClient(endpoint, timeout=timeout)
    report: dict[str, Any] = {
        "bridge": "nanodesu-mcp",
        "endpoint": endpoint,
        "checks": {},
        "notes": [
            "A healthy bridge does not prove an active game session.",
            "Payload summaries omit raw fleet and ship values.",
        ],
    }
    health = client.health()
    report["checks"]["health"] = {"status": "PASS", "summary": summarize(health)}
    server_info = client.initialize()
    report["checks"]["initialize"] = {"status": "PASS", "server": server_info}
    tools = client.list_tools()
    names = sorted(tool.name for tool in tools)
    report["checks"]["tools"] = {
        "status": "PASS" if {"poi_describe", "poi_get"}.issubset(names) else "FAIL",
        "count": len(names),
        "names": names,
    }
    if not {"poi_describe", "poi_get"}.issubset(names):
        raise RuntimeError("Required read tools poi_describe and poi_get are missing")
    describe = client.call_read_tool("poi_describe", {})
    report["checks"]["describe"] = {"status": "PASS", "summary": summarize_mcp_result(describe)}
    for label, path in (("fleets", "info.fleets"), ("ships", "info.ships")):
        value = client.call_read_tool("poi_get", {"path": path})
        report["checks"][label] = {"status": "PASS", "summary": summarize_mcp_result(value)}
    report["checks"]["session_validity"] = {
        "status": "NOT_VERIFIED",
        "reason": "Login/home-port evidence and payload freshness require local runtime inspection.",
    }
    return report


def run_plugin_mcp(base_url: str, timeout: float) -> dict[str, Any]:
    base_url = validate_loopback_url(base_url)
    report: dict[str, Any] = {
        "bridge": "poi-plugin-mcp",
        "endpoint": base_url,
        "checks": {},
        "notes": [
            "Only documented read-only GET endpoints are queried.",
            "WebView input and debug evaluation are never invoked.",
            "A healthy bridge does not prove an active game session.",
        ],
    }
    health = http_get_json(f"{base_url}/health", timeout)
    report["checks"]["health"] = {
        "status": "PASS" if isinstance(health, dict) else "FAIL",
        "summary": summarize(health),
    }
    for label, path in (
        ("fleets", "/fleets"),
        ("ships", "/ships"),
        ("resources", "/resources"),
        ("quests", "/quests"),
    ):
        payload = http_get_json(f"{base_url}{path}", timeout)
        report["checks"][label] = {"status": "PASS", "summary": summarize(payload)}
    report["checks"]["session_validity"] = {
        "status": "NOT_VERIFIED",
        "reason": "Login/home-port evidence and payload freshness require local runtime inspection.",
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge", choices=("nanodesu", "poi-plugin-mcp"), default="nanodesu")
    parser.add_argument("--endpoint", help="Override endpoint; loopback only")
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        if args.bridge == "nanodesu":
            endpoint = validate_loopback_url(args.endpoint or DEFAULT_NANODESU_ENDPOINT)
            report = run_nanodesu(endpoint, args.timeout)
        else:
            endpoint = args.endpoint or resolve_plugin_mcp_endpoint()
            report = run_plugin_mcp(validate_loopback_url(endpoint), args.timeout)
        failed = any(
            isinstance(check, dict) and check.get("status") == "FAIL"
            for check in report.get("checks", {}).values()
        )
        report["overall"] = "FAIL" if failed else "PASS_WITH_SESSION_UNVERIFIED"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if failed else 0
    except Exception as exc:
        print(json.dumps({
            "overall": "FAILED",
            "bridge": args.bridge,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "live_actions_enabled": False,
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
