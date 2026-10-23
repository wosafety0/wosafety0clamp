"""Read-only CDP probe for the selected Poi renderer.

Uses only the Python standard library, connects to loopback WebSocket endpoints,
and evaluates read-only document metadata. It never clicks, reloads, or sends
game API requests. This probe does not prove Redux freshness; callers must
supply a separately validated state timestamp to the policy layer.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import math
import os
import socket
import struct
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from kancollebot.adapters.poi.cdp_targets import (
    CdpDiscoveryError,
    CdpTarget,
    list_page_targets,
    select_poi_renderer,
)


class CdpProbeError(RuntimeError):
    """Raised when a read-only CDP probe cannot be completed safely."""


MAX_TIMEOUT_SECONDS = 3600.0


def _normalize_timeout(timeout: Any) -> float:
    """Validate timeout inputs and bound socket/protocol waits to one hour."""
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be finite and positive")
    try:
        normalized = float(timeout)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("timeout must be finite and positive") from exc
    if not math.isfinite(normalized) or normalized <= 0 or normalized > MAX_TIMEOUT_SECONDS:
        raise ValueError("timeout must be finite and positive, and at most 3600 seconds")
    return normalized


def _sanitize_display_url(url: str) -> str:
    """Remove URL credentials, query parameters, and fragments before display."""
    try:
        parsed = urllib.parse.urlsplit(url)
        netloc = parsed.netloc.rsplit("@", 1)[-1]
        return parsed._replace(netloc=netloc, query="", fragment="").geturl()
    except (TypeError, ValueError):
        return "[invalid-url]"


@dataclass(frozen=True, slots=True)
class CdpPageProbe:
    checked_at: datetime
    target_found: bool | None
    title: str
    url: str
    document_ready_state: str | None
    error_text: str
    probe_error: str = ""
    url_identity: str = ""

    def __post_init__(self) -> None:
        # Keep only a one-way identity digest for navigation-change detection.
        # The raw URL can contain query tokens and must not escape this object.
        raw_url = self.url
        if not self.url_identity:
            digest = hashlib.sha256(raw_url.encode("utf-8", errors="replace")).hexdigest()
            object.__setattr__(self, "url_identity", digest)
        object.__setattr__(self, "url", _sanitize_display_url(raw_url))

    @property
    def display_url(self) -> str:
        """Privacy-safe URL for logs and user-facing diagnostics."""
        return self.url


def _connect_websocket(url: str, timeout: float) -> socket.socket:
    timeout = _normalize_timeout(timeout)
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in url):
        raise CdpProbeError("CDP WebSocket URL contains control characters")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "ws" or not parsed.hostname:
        raise CdpProbeError("Only ws:// CDP endpoints are supported")
    try:
        loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        loopback = parsed.hostname.casefold() == "localhost"
    if not loopback or "@" in parsed.netloc or parsed.username is not None or parsed.password is not None:
        raise CdpProbeError("CDP WebSocket must be credential-free and loopback-only")
    if parsed.query:
        raise CdpProbeError("CDP WebSocket query strings are not allowed")
    if parsed.fragment:
        raise CdpProbeError("CDP WebSocket fragments are not allowed")
    try:
        port = parsed.port
    except ValueError as exc:
        raise CdpProbeError("Invalid CDP WebSocket port") from exc
    if port is None or not 1 <= port <= 65535:
        raise CdpProbeError("CDP WebSocket port is required")
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    if not path.startswith("/") or any(ord(char) <= 0x20 or ord(char) == 0x7F for char in path):
        raise CdpProbeError("CDP WebSocket request target contains invalid characters")
    # Resolve the special localhost name to an IP literal ourselves rather
    # than allowing socket.create_connection to consult a potentially modified
    # hosts file or resolver configuration.
    connect_host = "127.0.0.1" if parsed.hostname.casefold() == "localhost" else parsed.hostname
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    sock = socket.create_connection((connect_host, port), timeout=timeout)
    host = f"[{connect_host}]:{port}" if ":" in connect_host else f"{connect_host}:{port}"
    request = (
        f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    try:
        deadline = time.monotonic() + timeout
        sock.settimeout(max(deadline - time.monotonic(), 1e-6))
        sock.sendall(request.encode("ascii"))
        response = bytearray()
        while b"\r\n\r\n" not in response:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CdpProbeError("CDP WebSocket handshake deadline exceeded")
            sock.settimeout(remaining)
            chunk = sock.recv(1)
            if not chunk:
                raise CdpProbeError("CDP WebSocket closed during handshake")
            response.extend(chunk)
            if len(response) > 16384:
                raise CdpProbeError("CDP WebSocket handshake headers too large")
        header, _ = bytes(response).split(b"\r\n\r\n", 1)
        lines = header.decode("iso-8859-1").split("\r\n")
        status_parts = lines[0].split(" ", 2)
        if (
            len(status_parts) != 3
            or status_parts[0] != "HTTP/1.1"
            or status_parts[1] != "101"
        ):
            raise CdpProbeError("CDP WebSocket upgrade was rejected")
        headers = {}
        for line in lines[1:]:
            if not line or ":" not in line:
                raise CdpProbeError("CDP WebSocket handshake contains a malformed header")
            name, value = line.split(":", 1)
            normalized_name = name.strip().casefold()
            token_chars = "!#$%&'*+-.^_`|~"
            if (
                not normalized_name
                or any(not (char.isascii() and (char.isalnum() or char in token_chars)) for char in name)
                or normalized_name in headers
            ):
                raise CdpProbeError("CDP WebSocket handshake contains invalid headers (duplicate or empty headers, or invalid header name)")
            if any((ord(char) < 0x20 and char != "\t") or ord(char) == 0x7F for char in value):
                raise CdpProbeError("CDP WebSocket handshake contains invalid header values")
            headers[normalized_name] = value.strip()
        if "sec-websocket-extensions" in headers or "sec-websocket-protocol" in headers:
            raise CdpProbeError("CDP WebSocket handshake selected an unrequested extension or subprotocol")
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        ).decode("ascii")
        if headers.get("sec-websocket-accept") != expected:
            raise CdpProbeError("CDP WebSocket handshake accept key mismatch")
        upgrade_tokens = {
            token.strip().casefold()
            for token in headers.get("upgrade", "").split(",")
        }
        connection_tokens = {
            token.strip().casefold()
            for token in headers.get("connection", "").split(",")
        }
        if "websocket" not in upgrade_tokens or "upgrade" not in connection_tokens:
            raise CdpProbeError("CDP WebSocket handshake is missing Upgrade headers")
        return sock
    except Exception:
        sock.close()
        raise


def _send_frame(sock: socket.socket, payload: bytes, opcode: int = 1) -> None:
    first = 0x80 | opcode
    length = len(payload)
    mask = os.urandom(4)
    if length < 126:
        header = bytes((first, 0x80 | length))
    elif length < 65536:
        header = bytes((first, 0x80 | 126)) + struct.pack("!H", length)
    else:
        header = bytes((first, 0x80 | 127)) + struct.pack("!Q", length)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    sock.sendall(header + mask + masked)


def _read_exact(sock: socket.socket, size: int, *, deadline: float | None = None) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CdpProbeError("CDP WebSocket response deadline exceeded")
            sock.settimeout(remaining)
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise CdpProbeError("CDP WebSocket closed before response completed")
        chunks.extend(chunk)
    return bytes(chunks)


def _read_text_message(
    sock: socket.socket,
    *,
    max_message_bytes: int = 2 * 1024 * 1024,
    deadline: float | None = None,
) -> str:
    if isinstance(max_message_bytes, bool) or not isinstance(max_message_bytes, int) or max_message_bytes <= 0:
        raise ValueError("max_message_bytes must be a positive integer")
    chunks = bytearray()
    fragmented = False
    while True:
        header = _read_exact(sock, 2, deadline=deadline)
        fin = bool(header[0] & 0x80)
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F
        if header[0] & 0x70:
            raise CdpProbeError("CDP WebSocket reserved frame bits are set")
        if masked:
            raise CdpProbeError("CDP server-to-client frames must not be masked")
        if length == 126:
            length = struct.unpack("!H", _read_exact(sock, 2, deadline=deadline))[0]
            if length < 126:
                raise CdpProbeError("CDP WebSocket frame uses a non-minimal length")
        elif length == 127:
            length = struct.unpack("!Q", _read_exact(sock, 8, deadline=deadline))[0]
            if length < 65536 or length & (1 << 63):
                raise CdpProbeError("CDP WebSocket frame has an invalid extended length")
        is_control = opcode >= 8
        if is_control and (not fin or length > 125):
            raise CdpProbeError("CDP WebSocket control frame is malformed")
        if length > max_message_bytes or len(chunks) + length > max_message_bytes:
            raise CdpProbeError("CDP message exceeded 2 MiB")
        mask = _read_exact(sock, 4, deadline=deadline) if masked else b""
        data = _read_exact(sock, length, deadline=deadline)
        if masked:
            data = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        if opcode == 8:
            raise CdpProbeError("CDP WebSocket sent a close frame")
        if opcode == 9:
            _send_frame(sock, data, opcode=10)
            continue
        if opcode == 10:
            continue
        if opcode == 1:
            if fragmented:
                raise CdpProbeError("CDP WebSocket started a nested data message")
            chunks.extend(data)
            if fin:
                return chunks.decode("utf-8", errors="strict")
            fragmented = True
            continue
        if opcode == 0:
            if not fragmented:
                raise CdpProbeError("CDP WebSocket sent an unexpected continuation frame")
            chunks.extend(data)
            if fin:
                return chunks.decode("utf-8", errors="strict")
            continue
        raise CdpProbeError("Unsupported CDP WebSocket frame opcode")


def _evaluate_read_only(target: CdpTarget, timeout: float) -> dict[str, Any]:
    timeout = _normalize_timeout(timeout)
    sock = _connect_websocket(target.websocket_url, timeout)
    try:
        expression = (
            "JSON.stringify({title:String(document.title||''),"
            "url:String(location.href||''),"
            "readyState:String(document.readyState||''),"
            "bodyText:String((document.body&&document.body.innerText)||'').slice(0,12000)})"
        )
        command = {
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
                "timeout": int(timeout * 1000),
            },
        }
        _send_frame(sock, json.dumps(command, separators=(",", ":")).encode("utf-8"))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CdpProbeError("CDP Runtime.evaluate response deadline exceeded")
            sock.settimeout(remaining)
            message = json.loads(_read_text_message(sock, deadline=deadline))
            if not isinstance(message, dict) or message.get("id") != 1:
                continue
            if "error" in message:
                raise CdpProbeError("CDP Runtime.evaluate returned a protocol error")
            envelope = message.get("result")
            if not isinstance(envelope, dict):
                raise CdpProbeError("CDP Runtime.evaluate response has an invalid result envelope")
            result = envelope.get("result")
            if not isinstance(result, dict):
                raise CdpProbeError("CDP Runtime.evaluate response has an invalid remote object")
            value = result.get("value")
            if not isinstance(value, str):
                raise CdpProbeError("CDP returned no serializable document metadata")
            decoded = json.loads(value)
            if not isinstance(decoded, dict):
                raise CdpProbeError("CDP document metadata has an invalid shape")
            return decoded
    except (OSError, TimeoutError, UnicodeError, json.JSONDecodeError) as exc:
        raise CdpProbeError(f"CDP read-only probe failed ({type(exc).__name__})") from exc
    finally:
        sock.close()


def probe_poi_page(
    *,
    endpoint: str = "http://127.0.0.1:9222",
    title_hint: str = "poi",
    url_hint: str = "",
    timeout: float = 2.0,
) -> CdpPageProbe:
    """Probe exactly one selected Poi page without any game interaction."""
    timeout = _normalize_timeout(timeout)
    checked_at = datetime.now(timezone.utc)
    try:
        targets = list_page_targets(endpoint, timeout=timeout)
        target = select_poi_renderer(targets, title_hint=title_hint, url_hint=url_hint)
        data = _evaluate_read_only(target, timeout)
        actual_title = data.get("title", "")
        actual_url = data.get("url", "")
        ready_state = data.get("readyState", "")
        if not all(isinstance(value, str) for value in (actual_title, actual_url, ready_state)):
            raise CdpProbeError("CDP document metadata contains invalid field types")
        # Revalidate the live document metadata obtained over the selected
        # target's WebSocket; the discovery list may be stale during navigation.
        if actual_url != target.url:
            raise CdpProbeError("Selected Poi page URL changed during the read-only probe")
        try:
            select_poi_renderer(
                (CdpTarget(target.target_id, actual_title, actual_url, target.websocket_url),),
                title_hint=title_hint,
                url_hint=url_hint,
            )
        except CdpDiscoveryError as exc:
            raise CdpProbeError("Live document metadata no longer matches the selected Poi page") from exc
        body_text = data.get("bodyText", "")
        if not isinstance(body_text, str):
            body_text = ""
        return CdpPageProbe(
            checked_at=checked_at,
            target_found=True,
            title=actual_title,
            url=actual_url,
            document_ready_state=ready_state,
            error_text=body_text,
        )
    except (CdpDiscoveryError, CdpProbeError, OSError, TimeoutError, ValueError) as exc:
        return CdpPageProbe(
            checked_at=checked_at,
            target_found=False if isinstance(exc, CdpDiscoveryError) and "found 0" in str(exc) else None,
            title="",
            url="",
            document_ready_state=None,
            error_text="",
            probe_error=type(exc).__name__,
        )
