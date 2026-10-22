"""Read-only MCP client for a local Poi plugin.

Uses only the Python standard library. It never sends gameplay commands; the
only MCP methods exposed by this wrapper are initialization, tool discovery,
and explicitly allow-listed read tools.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import urlparse


READ_ONLY_TOOLS = frozenset(
    {
        "poi_status",
        "poi_get_overview",
        "poi_query_ships",
        "poi_query_equipment",
        "poi_query_fleet_assets",
        "poi_get_fleets",
        "poi_get_quests",
        "poi_get_inventory",
        "poi_get_operations",
        "get_fleet_status",
        "poi_get",
    }
)


class PoiMcpError(RuntimeError):
    """Raised when the local Poi MCP endpoint cannot be used safely."""


@dataclass(frozen=True, slots=True)
class PoiMcpConfig:
    base_url: str = "http://127.0.0.1:39271"
    token: str | None = None
    timeout: float = 3.0
    protocol_version: str = "2025-03-26"

    @classmethod
    def from_environment(cls) -> "PoiMcpConfig":
        return cls(
            base_url=os.environ.get("KANCOLLE_POI_MCP_URL", "http://127.0.0.1:39271"),
            token=os.environ.get("KANCOLLE_POI_MCP_TOKEN"),
        )

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Poi MCP must use plain HTTP on loopback only")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Poi MCP base URL must not contain credentials, query, or fragment")
        if not isinstance(self.timeout, (int, float)) or isinstance(self.timeout, bool) or self.timeout <= 0:
            raise ValueError("timeout must be a positive number")
        if not isinstance(self.protocol_version, str) or not self.protocol_version.strip():
            raise ValueError("protocol_version must be a non-empty string")


class PoiMcpClient:
    """Small Streamable-HTTP MCP client restricted to read-only tools."""

    def __init__(
        self,
        config: PoiMcpConfig | None = None,
        *,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config or PoiMcpConfig.from_environment()
        self._opener = opener or urllib.request.urlopen
        self._request_id = 0
        self._initialized = False

    def health(self) -> Mapping[str, Any]:
        """Read the unauthenticated liveness endpoint; does not prove game data is ready."""
        url = self.config.base_url.rstrip("/") + "/health"
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        payload = self._open_json(request)
        if not isinstance(payload, dict):
            raise PoiMcpError("Poi health response must be a JSON object")
        return payload

    def initialize(self) -> Mapping[str, Any]:
        """Perform MCP initialize and mark this client initialized on success."""
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": self.config.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "kancollebot", "version": "0.1.0"},
            },
            allow_uninitialized=True,
        )
        if not isinstance(result, dict):
            raise PoiMcpError("MCP initialize result must be an object")
        self._initialized = True
        return result

    def list_tools(self) -> tuple[Mapping[str, Any], ...]:
        result = self._rpc("tools/list")
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
            raise PoiMcpError("MCP tools/list returned an invalid tool list")
        return tuple(tools)

    def call_read_tool(self, name: str, arguments: Mapping[str, Any] | None = None) -> Any:
        """Call only a known read-only tool; write-capable names are rejected locally."""
        if name not in READ_ONLY_TOOLS:
            raise PoiMcpError(f"Tool is not allow-listed as read-only: {name}")
        result = self._rpc("tools/call", {"name": name, "arguments": dict(arguments or {})})
        if not isinstance(result, dict):
            raise PoiMcpError("MCP tools/call result must be an object")
        if result.get("isError") is True:
            raise PoiMcpError("Poi MCP tool reported an error")
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                    try:
                        return json.loads(item["text"])
                    except json.JSONDecodeError:
                        return item["text"]
        return result

    def _rpc(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        allow_uninitialized: bool = False,
    ) -> Mapping[str, Any]:
        if not self._initialized and not allow_uninitialized and method != "initialize":
            raise PoiMcpError("MCP client is not initialized")
        self._request_id += 1
        body: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
        }
        if params is not None:
            body["params"] = dict(params)
        if not self.config.token:
            raise PoiMcpError("KANCOLLE_POI_MCP_TOKEN is required for MCP requests")
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.config.token,
        }
        request = urllib.request.Request(
            self.config.base_url.rstrip("/") + "/mcp",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        payload = self._open_json(request, expected_id=self._request_id)
        if not isinstance(payload, dict):
            raise PoiMcpError("MCP response must be a JSON object")
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            raise PoiMcpError("MCP request failed: " + (message if isinstance(message, str) else "unknown error"))
        result = payload.get("result")
        if not isinstance(result, dict):
            raise PoiMcpError("MCP response is missing a valid result object")
        return result

    def _open_json(
        self,
        request: urllib.request.Request,
        *,
        expected_id: int | None = None,
    ) -> Any:
        try:
            with self._opener(request, timeout=self.config.timeout) as response:
                raw = response.read()
                content_type = ""
                headers = getattr(response, "headers", None)
                if headers is not None:
                    content_type = headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            # Do not include request headers or tokens in exceptions.
            if exc.code == 401:
                raise PoiMcpError("Poi MCP authentication failed (HTTP 401)") from None
            raise PoiMcpError(f"Poi MCP HTTP request failed (HTTP {exc.code})") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, TimeoutError) or isinstance(exc, TimeoutError):
                raise PoiMcpError("Poi MCP request timed out") from None
            raise PoiMcpError("Poi MCP endpoint is unreachable") from None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise PoiMcpError("Poi MCP returned invalid UTF-8") from None

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            if "text/event-stream" not in content_type.lower() and not text.lstrip().startswith("data:"):
                raise PoiMcpError("Poi MCP returned invalid JSON") from None

        # Streamable HTTP MCP servers may return a Server-Sent Events stream
        # instead of a single JSON document. Select the JSON-RPC response for
        # this request; ignore unrelated notifications and non-data fields.
        for block in reversed(text.replace("\r\n", "\n").split("\n\n")):
            data_lines = [
                line[5:].lstrip()
                for line in block.splitlines()
                if line.startswith("data:")
            ]
            if not data_lines:
                continue
            try:
                event_payload = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                continue
            if not isinstance(event_payload, dict):
                continue
            if expected_id is None or event_payload.get("id") == expected_id:
                return event_payload
        raise PoiMcpError("Poi MCP event stream contained no matching JSON response")
