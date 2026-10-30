"""Loopback-only, allowlisted WebSocket transport for CDP mouse input.

Only viewport inspection, one fixed read-only fleet snapshot expression, mouse
input, and screenshot capture are allowed. Arbitrary Runtime.evaluate expressions
and all other CDP methods are rejected.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import urllib.parse
from typing import Any

from .cdp_input import CdpInput, CdpInputError


_ALLOWED_METHODS = {
    # Read-only accessibility and DOM geometry methods for semantic target discovery.
    "Accessibility.getFullAXTree",
    "DOM.pushNodesByBackendIdsToFrontend",
    "DOM.getBoxModel",
    "Page.getLayoutMetrics",
    "Page.captureScreenshot",
    # Mouse input remains the only enabled input method.
    "Input.dispatchMouseEvent",
    # A single exact read-only Redux snapshot expression; arbitrary evaluation remains blocked.
    "Runtime.evaluate",
}
_MAX_FRAME_BYTES = 8 * 1024 * 1024

READ_ONLY_FLEET_SNAPSHOT_EXPRESSION = "JSON.stringify((window.getStore?window.getStore('info.fleets'):[]).map(f=>({id:f.api_id,name:f.api_name,mission:f.api_mission,ships:f.api_ship})))"


class CdpWebSocketTransport:
    """Synchronous local CDP transport implementing the CdpInput protocol."""

    def __init__(self, websocket_url: str, *, timeout: float = 3.0) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        parsed = urllib.parse.urlsplit(websocket_url)
        if (
            parsed.scheme != "ws"
            or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise CdpInputError("CDP WebSocket URL must be credential-free loopback ws://")
        try:
            port = parsed.port or 80
        except ValueError as exc:
            raise CdpInputError("Invalid CDP WebSocket port") from exc
        if not 1 <= port <= 65535:
            raise CdpInputError("Invalid CDP WebSocket port")
        self._socket = socket.create_connection((parsed.hostname, port), timeout=timeout)
        self._socket.settimeout(timeout)
        self._next_id = 1
        self._closed = False
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        host_header = f"{parsed.hostname}:{port}"
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {host_header}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        try:
            self._socket.sendall(request.encode("ascii"))
            headers = self._read_headers()
            expected = base64.b64encode(
                hashlib.sha1(
                    (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
                ).digest()
            ).decode("ascii")
            lines = headers.decode("latin-1").split("\r\n")
            if not lines or " 101 " not in f" {lines[0]} ":
                raise CdpInputError("CDP WebSocket upgrade was rejected")
            parsed_headers = {}
            for line in lines[1:]:
                if ":" in line:
                    name, value = line.split(":", 1)
                    parsed_headers[name.strip().lower()] = value.strip()
            if parsed_headers.get("sec-websocket-accept") != expected:
                raise CdpInputError("CDP WebSocket handshake validation failed")
        except Exception:
            self._socket.close()
            self._closed = True
            raise

    def _read_headers(self) -> bytes:
        data = bytearray()
        while not data.endswith(b"\r\n\r\n"):
            chunk = self._socket.recv(1)
            if not chunk or len(data) > 16_384:
                raise CdpInputError("Incomplete CDP WebSocket handshake")
            data.extend(chunk)
        return bytes(data[:-4])

    def _read_exact(self, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            part = self._socket.recv(size - len(data))
            if not part:
                raise CdpInputError("CDP WebSocket closed unexpectedly")
            data.extend(part)
        return bytes(data)

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        size = len(payload)
        first = bytes([0x80 | opcode])
        if size < 126:
            header = first + bytes([0x80 | size])
        elif size <= 0xFFFF:
            header = first + bytes([0x80 | 126]) + struct.pack("!H", size)
        else:
            header = first + bytes([0x80 | 127]) + struct.pack("!Q", size)
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self._socket.sendall(header + mask + masked)

    def _receive_text(self) -> dict[str, Any]:
        while True:
            header = self._read_exact(2)
            opcode = header[0] & 0x0F
            masked = bool(header[1] & 0x80)
            size = header[1] & 0x7F
            if size == 126:
                size = struct.unpack("!H", self._read_exact(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self._read_exact(8))[0]
            if size > _MAX_FRAME_BYTES:
                raise CdpInputError("CDP WebSocket frame exceeds the size limit")
            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(size)
            if masked:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 8:
                raise CdpInputError("CDP WebSocket closed by peer")
            if opcode == 9:
                self._send_frame(10, payload)
                continue
            if opcode in {0, 10}:
                continue
            if opcode != 1:
                continue
            try:
                result = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CdpInputError("CDP returned invalid JSON") from exc
            if isinstance(result, dict):
                return result

    def send(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send one allowlisted command and return its result; never auto-retry."""
        if self._closed:
            raise CdpInputError("CDP transport is closed")
        if method not in _ALLOWED_METHODS:
            raise CdpInputError(f"CDP method is not allowlisted: {method}")
        if method == "Runtime.evaluate":
            if (
                not isinstance(params, dict)
                or set(params) != {"expression", "returnByValue"}
                or params.get("expression") != READ_ONLY_FLEET_SNAPSHOT_EXPRESSION
                or params.get("returnByValue") is not True
            ):
                raise CdpInputError(
                    "Runtime.evaluate is restricted to the fixed read-only fleet snapshot"
                )
        request_id = self._next_id
        self._next_id += 1
        try:
            self._send_frame(
                1,
                json.dumps(
                    {"id": request_id, "method": method, "params": params},
                    separators=(",", ":"),
                ).encode("utf-8"),
            )
            while True:
                response = self._receive_text()
                if response.get("id") != request_id:
                    continue
                if "error" in response:
                    raise CdpInputError(f"CDP command failed: {method}")
                result = response.get("result", {})
                if not isinstance(result, dict):
                    raise CdpInputError(f"CDP returned an invalid result for {method}")
                return result
        except (OSError, TimeoutError) as exc:
            raise CdpInputError(
                f"CDP command outcome is unknown for {method}; do not blindly retry input"
            ) from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._send_frame(8, b"")
        except OSError:
            pass
        self._socket.close()

    def __enter__(self) -> "CdpWebSocketTransport":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


class CdpPoiSession:
    """A selected Poi renderer plus its allowlisted input transport."""

    def __init__(self, target, transport: CdpWebSocketTransport) -> None:
        self.target = target
        self.transport = transport

    @property
    def input(self) -> CdpInput:
        """Return the input facade backed by this session's transport."""
        return CdpInput(self.transport)

    def close(self) -> None:
        self.transport.close()

    def __enter__(self) -> "CdpPoiSession":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def connect_game_webview(
    endpoint: str = "http://127.0.0.1:9222",
    *,
    timeout: float = 3.0,
) -> CdpPoiSession:
    """Connect directly to the exact KanColle WebView target using its own WS URL.

    Poi's outer shell is a ``page`` target; the embedded DMM game renderer is a
    separate ``webview`` target. This function selects only the expected game
    URL and opens that target's loopback WebSocket directly. It does not need
    browser-level Target.attachToTarget and does not perform game input.
    """
    from .cdp_targets import list_webview_targets, select_poi_renderer

    targets = list_webview_targets(endpoint, timeout=min(timeout, 2.0))
    target = select_poi_renderer(
        targets,
        title_hint="",
        url_hint="https://play.games.dmm.com/game/kancolle",
    )
    transport = CdpWebSocketTransport(target.websocket_url, timeout=timeout)
    return CdpPoiSession(target, transport)


def connect_poi_session(
    endpoint: str = "http://127.0.0.1:9222",
    *,
    title_hint: str = "poi",
    url_hint: str = "",
    timeout: float = 3.0,
) -> CdpPoiSession:
    """Select exactly one matching Poi page and open its loopback CDP channel.

    This establishes a transport only. It does not send a click or enable a
    gameplay loop. Callers must still verify fresh game state before each step.
    """
    from .cdp_targets import list_page_targets, select_poi_renderer

    targets = list_page_targets(endpoint, timeout=min(timeout, 2.0))
    target = select_poi_renderer(targets, title_hint=title_hint, url_hint=url_hint)
    transport = CdpWebSocketTransport(target.websocket_url, timeout=timeout)
    return CdpPoiSession(target, transport)
