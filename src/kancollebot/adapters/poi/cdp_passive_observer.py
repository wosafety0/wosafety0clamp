"""Zero-input, path-filtered facade for passive CDP Network observation.

This module exposes only observation lifecycle and bounded event collection.
It deliberately does not expose browser input, reload, evaluation, interception,
or request replay methods.
"""
from __future__ import annotations

import time
from typing import Any, Protocol
from urllib.parse import urlsplit


class PassiveObserverError(RuntimeError):
    """The passive observer cannot safely start or continue."""


class PassiveNetworkTransport(Protocol):
    def send(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send an allowlisted CDP command."""
        ...

    def drain_events(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Drain sanitized, bounded CDP events."""
        ...


class CdpPassiveNetworkObserver:
    """Observe sanitized Network events for explicitly allowed URL paths."""

    def __init__(
        self,
        transport: PassiveNetworkTransport,
        *,
        allowed_paths: set[str] | frozenset[str],
        max_batch_size: int = 100,
    ) -> None:
        if not allowed_paths or any(
            not isinstance(path, str)
            or not path.startswith("/")
            or "?" in path
            or "#" in path
            for path in allowed_paths
        ):
            raise ValueError("allowed_paths must contain exact absolute URL paths")
        if isinstance(max_batch_size, bool) or not isinstance(max_batch_size, int) or not 1 <= max_batch_size <= 100:
            raise ValueError("max_batch_size must be between 1 and 100")
        self._transport = transport
        self._allowed_paths = frozenset(allowed_paths)
        self._max_batch_size = max_batch_size
        self._enabled = False
        self._closed = False

    def start(self) -> None:
        """Enable passive Network events once; no game input is sent."""
        if self._closed:
            raise PassiveObserverError("observer is closed")
        if self._enabled:
            raise PassiveObserverError("observer is already started")
        self._transport.send("Network.enable", {})
        self._enabled = True

    def _matches(self, event: dict[str, Any]) -> bool:
        method = event.get("method")
        params = event.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            return False
        if method == "Network.requestWillBeSent":
            request = params.get("request")
            url = request.get("url") if isinstance(request, dict) else None
        elif method == "Network.responseReceived":
            response = params.get("response")
            url = response.get("url") if isinstance(response, dict) else None
        else:
            return False
        if not isinstance(url, str) or len(url) > 4096:
            return False
        try:
            parsed = urlsplit(url)
        except ValueError:
            return False
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and parsed.path in self._allowed_paths

    def collect(self, *, timeout: float = 1.0, max_events: int = 20) -> list[dict[str, Any]]:
        """Collect matching summaries for a bounded interval; never performs input."""
        if self._closed or not self._enabled:
            raise PassiveObserverError("observer must be started and not closed")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 30:
            raise ValueError("timeout must be greater than 0 and at most 30 seconds")
        if isinstance(max_events, bool) or not isinstance(max_events, int) or not 1 <= max_events <= self._max_batch_size:
            raise ValueError(f"max_events must be between 1 and {self._max_batch_size}")
        deadline = time.monotonic() + timeout
        collected: list[dict[str, Any]] = []
        while len(collected) < max_events:
            batch = self._transport.drain_events(limit=self._max_batch_size)
            for event in batch:
                if self._matches(event):
                    collected.append(event)
                    if len(collected) >= max_events:
                        break
            if len(collected) >= max_events or time.monotonic() >= deadline:
                break
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        return collected

    def close(self) -> None:
        """Disable Network observation if this instance enabled it; never retry."""
        if self._closed:
            return
        self._closed = True
        if self._enabled:
            self._enabled = False
            try:
                self._transport.send("Network.disable", {})
            except Exception:
                # The observer is closed regardless; caller must not retry blindly.
                pass

    def __enter__(self) -> "CdpPassiveNetworkObserver":
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
