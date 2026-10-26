"""Minimal, read-only MCP Streamable HTTP client for the local Poi bridge.

Uses only the Python standard library. It never performs game actions and
rejects non-loopback endpoints by default.
"""

from __future__ import annotations

import json
import ipaddress
import itertools
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never follow redirects from a validated local endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PoiMcpError(RuntimeError):
    """Raised when the local Poi MCP endpoint cannot provide a valid response."""


@dataclass(frozen=True, slots=True)
class McpTool:
    name: str
    description: str = ""


class PoiMcpClient:
    """Synchronous MCP Streamable HTTP client restricted to loopback by default."""

    def __init__(self, endpoint: str = "http://127.0.0.1:12450/mcp", *, timeout: float = 3.0,
                 allow_non_loopback: bool = False) -> None:
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.scheme != "http" or not parsed.hostname or not parsed.path:
            raise ValueError("endpoint must be an absolute HTTP URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("endpoint URL must not contain credentials")
        try:
            address = ipaddress.ip_address(parsed.hostname)
            is_loopback = address.is_loopback
        except ValueError:
            is_loopback = parsed.hostname.lower() == "localhost"
        if not is_loopback and not allow_non_loopback:
            raise ValueError("endpoint must use a loopback host")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.endpoint = endpoint
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirectHandler, urllib.request.ProxyHandler({}))
        self._ids = itertools.count(1)
        self._session_id: str | None = None
        self._protocol_version = "2025-03-26"
        self._initialized = False

    def _post(self, payload: dict[str, Any], *, notification: bool = False) -> Any:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._initialized:
            headers["MCP-Protocol-Version"] = self._protocol_version
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(self.endpoint, data=data, headers=headers, method="POST")
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                status = response.status
                content_type = response.headers.get("Content-Type", "").lower()
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self._session_id = session_id
                body = response.read(2 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            detail = exc.read(4096).decode("utf-8", errors="replace")
            raise PoiMcpError(f"MCP HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PoiMcpError(f"Cannot reach local Poi MCP endpoint: {exc}") from exc

        if len(body) > 2 * 1024 * 1024:
            raise PoiMcpError("MCP response exceeded the 2 MiB safety limit")
        if notification and status in (202, 204):
            return None
        if status not in (200, 201):
            raise PoiMcpError(f"Unexpected MCP HTTP status: {status}")
        if not body.strip():
            if notification:
                return None
            raise PoiMcpError("MCP endpoint returned an empty response")
        text = body.decode("utf-8", errors="strict")
        if "text/event-stream" in content_type:
            messages = []
            data_lines: list[str] = []
            for line in text.splitlines() + [""]:
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                elif not line.strip() and data_lines:
                    joined = "\n".join(data_lines)
                    data_lines.clear()
                    if joined != "[DONE]":
                        try:
                            messages.append(json.loads(joined))
                        except json.JSONDecodeError as exc:
                            raise PoiMcpError("Malformed JSON in MCP event stream") from exc
            if not messages:
                raise PoiMcpError("MCP event stream contained no JSON-RPC message")
            return next((item for item in messages if isinstance(item, dict) and "id" in item), messages[-1])
        try:
            result = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PoiMcpError("MCP endpoint returned malformed JSON") from exc
        if not isinstance(result, dict):
            raise PoiMcpError("MCP response must be a JSON object")
        return result

    def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = next(self._ids)
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        response = self._post(payload)
        if not isinstance(response, dict) or response.get("id") != request_id:
            raise PoiMcpError(f"Invalid response id for MCP method {method}")
        if "error" in response:
            error = response["error"]
            raise PoiMcpError(f"MCP {method} error: {error}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise PoiMcpError(f"MCP {method} returned no result object")
        return result

    def initialize(self) -> dict[str, Any]:
        """Negotiate the MCP session and return server information."""
        result = self._request(
            "initialize",
            {
                "protocolVersion": self._protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "kancollebot-readonly", "version": "0.1.0"},
            },
        )
        server_info = result.get("serverInfo", {})
        negotiated = result.get("protocolVersion")
        if isinstance(negotiated, str):
            self._protocol_version = negotiated
        # The negotiated protocol version applies to the initialized notification too.
        self._initialized = True
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, notification=True)
        return server_info if isinstance(server_info, dict) else {}

    def list_tools(self) -> tuple[McpTool, ...]:
        result = self._request("tools/list")
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise PoiMcpError("MCP tools/list response has no tools array")
        parsed = []
        for item in tools:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                description = item.get("description", "")
                parsed.append(McpTool(item["name"], description if isinstance(description, str) else ""))
        return tuple(parsed)

    def call_read_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call an explicitly allowlisted read tool and return its MCP result."""
        if name not in {"poi_describe", "poi_get", "poi_lookup"}:
            raise ValueError("only poi_describe, poi_get, and poi_lookup are allowed")
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError") is True:
            content = result.get("content", [])
            messages = [item.get("text", "") for item in content if isinstance(item, dict)]
            raise PoiMcpError(f"Poi tool {name} failed: {'; '.join(messages)}")
        return result

    def health(self) -> dict[str, Any]:
        """Read the plugin health endpoint without creating an MCP session."""
        parsed = urllib.parse.urlsplit(self.endpoint)
        url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/health", "", ""))
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = response.read(65537)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PoiMcpError(f"Cannot read Poi MCP health endpoint: {exc}") from exc
        if len(data) > 65536:
            raise PoiMcpError("Health response exceeded the 64 KiB safety limit")
        try:
            result = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PoiMcpError("Health endpoint returned malformed JSON") from exc
        if not isinstance(result, dict):
            raise PoiMcpError("Health endpoint response must be a JSON object")
        return result
