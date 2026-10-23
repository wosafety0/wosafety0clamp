"""Read-only loopback receiver for Poi Forwarder KCSAPI webhook events.

The receiver accepts only the documented webhook route and filters to KCSAPI
or map-resource events. It never sends requests to Poi or issues game actions.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import queue
import threading
from typing import Any
from urllib.parse import urlsplit


DEFAULT_MAX_BODY_BYTES = 2 * 1024 * 1024
DEFAULT_QUEUE_SIZE = 1000
WEBHOOK_PATH = "/webhook/kancolle"
SENSITIVE_KEY_NAMES = frozenset({
    "api_token",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "cookie",
    "set_cookie",
})


def _sanitize_url(value: str) -> str:
    """Remove query strings and fragments that may contain credentials."""
    return value.split("#", 1)[0].split("?", 1)[0]


def _redact_sensitive_fields(value: Any) -> Any:
    """Return a recursively copied payload with credential-like fields redacted."""
    if isinstance(value, dict):
        return {
            key: ("[REDACTED]" if isinstance(key, str) and key.lower() in SENSITIVE_KEY_NAMES
                  else _redact_sensitive_fields(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_fields(item) for item in value]
    if isinstance(value, str):
        return _sanitize_url(value) if value.startswith(("http://", "https://")) else value
    return value


class PoiWebhookError(RuntimeError):
    """Raised when the local Poi webhook receiver cannot be used safely."""


class PoiWebhookReceiver:
    """Bounded, read-only receiver for events posted by Poi's API Forwarder."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 9223,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
        queue_size: int = DEFAULT_QUEUE_SIZE,
    ) -> None:
        if host != "127.0.0.1":
            raise ValueError("Poi webhook receiver must bind to 127.0.0.1 only")
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
            raise ValueError("port must be an integer in the range 0..65535")
        if isinstance(max_body_bytes, bool) or not isinstance(max_body_bytes, int) or max_body_bytes < 1:
            raise ValueError("max_body_bytes must be a positive integer")
        if isinstance(queue_size, bool) or not isinstance(queue_size, int) or queue_size < 1:
            raise ValueError("queue_size must be a positive integer")

        self.host = host
        self.port = port
        self.max_body_bytes = max_body_bytes
        self._events: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=queue_size)
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def bound_port(self) -> int | None:
        """Return the actual bound port, including when port=0 was requested."""
        server = self._server
        if server is None:
            return None
        return int(server.server_address[1])

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> int:
        """Start the HTTP receiver and return its bound port."""
        with self._lock:
            if self._server is not None:
                return int(self._server.server_address[1])

            receiver = self

            class WebhookHandler(BaseHTTPRequestHandler):
                def do_POST(self) -> None:
                    if self.path != WEBHOOK_PATH:
                        self._reply(404, {"status": "NOT FOUND"})
                        return

                    raw_length = self.headers.get("Content-Length")
                    if raw_length is None:
                        self._reply(411, {"status": "CONTENT LENGTH REQUIRED"})
                        return
                    try:
                        length = int(raw_length)
                    except (TypeError, ValueError):
                        self._reply(400, {"status": "INVALID CONTENT LENGTH"})
                        return
                    if length < 1:
                        self._reply(400, {"status": "EMPTY BODY"})
                        return
                    if length > receiver.max_body_bytes:
                        self._reply(413, {"status": "BODY TOO LARGE"})
                        return

                    raw_body = self.rfile.read(length)
                    if len(raw_body) != length:
                        self._reply(400, {"status": "INCOMPLETE BODY"})
                        return
                    try:
                        payload = json.loads(raw_body.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        self._reply(400, {"status": "INVALID JSON"})
                        return
                    if not isinstance(payload, dict):
                        self._reply(400, {"status": "JSON OBJECT REQUIRED"})
                        return

                    raw_path = payload.get("path", "")
                    if not isinstance(raw_path, str) or not raw_path:
                        self._reply(400, {"status": "INVALID EVENT PATH"})
                        return
                    safe_path = _sanitize_url(raw_path)
                    resource_path = safe_path
                    if safe_path.startswith(("http://", "https://")):
                        try:
                            resource_path = urlsplit(safe_path).path
                        except ValueError:
                            self._reply(400, {"status": "INVALID EVENT PATH"})
                            return

                    is_kcsapi = resource_path.startswith("/kcsapi/")
                    is_map_resource = "kcs2/resources/map" in resource_path
                    if not is_kcsapi and not is_map_resource:
                        self._reply(200, {"status": "FILTERED OUT (UNKNOWN RESOURCE)"})
                        return

                    if is_map_resource:
                        event = {
                            "path": (
                                resource_path.lstrip("/")
                                if safe_path.startswith(("http://", "https://"))
                                else safe_path
                            ),
                            "response": {"url": safe_path},
                            "request": None,
                            "stage": payload.get("stage"),
                            "timestamp": payload.get("timestamp"),
                        }
                    else:
                        event = {
                            "path": safe_path,
                            "response": _redact_sensitive_fields(payload.get("response")),
                            "request": _redact_sensitive_fields(payload.get("request")),
                            "stage": payload.get("stage"),
                            "timestamp": payload.get("timestamp"),
                        }

                    try:
                        receiver._events.put_nowait(event)
                    except queue.Full:
                        self._reply(503, {"status": "EVENT QUEUE FULL"})
                        return
                    self._reply(200, {"status": "ACCEPTED & QUEUED"})

                def _reply(self, status: int, payload: dict[str, str]) -> None:
                    body = json.dumps(payload).encode("utf-8")
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(body)
                    self.close_connection = True

                def log_message(self, format: str, *args: Any) -> None:
                    return

            server = HTTPServer((self.host, self.port), WebhookHandler)
            thread = threading.Thread(
                target=server.serve_forever,
                name="PoiWebhookReceiver",
                daemon=True,
            )
            self._server = server
            self._thread = thread
            thread.start()
            return int(server.server_address[1])

    def get_event(self, timeout: float | None = None) -> dict[str, Any] | None:
        """Return the next queued event, or None if no event arrives in time."""
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must not be negative")
        try:
            if timeout is None:
                return self._events.get()
            if timeout == 0:
                return self._events.get_nowait()
            return self._events.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self) -> None:
        """Stop the local server without affecting Poi or other system settings."""
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
