"""Non-blocking, reusable source for live Poi port-response witnesses.

The provider continuously arms the reviewed passive observer in a daemon
thread. Reads return the latest in-memory witness immediately; they never
wait for a network response. Freshness and exact Redux-state matching remain
the responsibility of PoiExpeditionReader / port_snapshot_matches_current_state.
"""
from __future__ import annotations

import copy
import math
import threading
import time
from datetime import datetime
from typing import Any

from kancollebot.adapters.poi.port_response_observer import PassivePortResponseObserver


class BackgroundPortSnapshotProvider:
    """Continuously collect natural port responses without blocking readers."""

    def __init__(
        self,
        observer: PassivePortResponseObserver,
        *,
        retry_delay_seconds: float = 0.25,
    ) -> None:
        if not math.isfinite(retry_delay_seconds) or retry_delay_seconds <= 0:
            raise ValueError("retry_delay_seconds must be finite and positive")
        self._observer = observer
        self._retry_delay_seconds = retry_delay_seconds
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot: dict[str, Any] | None = None
        self._last_status = "not_started"

    def __call__(self) -> dict[str, Any] | None:
        """Return the latest witness immediately and start collection lazily."""
        with self._condition:
            if self._stop_event.is_set():
                return None
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run,
                    name="poi-port-snapshot-observer",
                    daemon=True,
                )
                self._thread.start()
            return copy.deepcopy(self._snapshot)

    @property
    def latest_snapshot(self) -> dict[str, Any] | None:
        with self._condition:
            return copy.deepcopy(self._snapshot)

    @property
    def last_status(self) -> str:
        with self._condition:
            return self._last_status

    @property
    def is_running(self) -> bool:
        with self._condition:
            return self._thread is not None and self._thread.is_alive()

    def close(self, *, join_timeout_seconds: float = 1.0) -> None:
        """Stop future cycles and cancel an in-flight observer when supported.

        PassivePortResponseObserver implements cancellable capture. A generic
        observer exposing only capture() may continue until its own call returns.
        """
        if not math.isfinite(join_timeout_seconds) or join_timeout_seconds < 0:
            raise ValueError("join_timeout_seconds must be finite and non-negative")
        self._stop_event.set()
        with self._condition:
            thread = self._thread
            self._condition.notify_all()
        if thread is not None and thread is not threading.current_thread():
            thread.join(join_timeout_seconds)

    def wait_for_snapshot_after(
        self,
        *,
        after: datetime,
        timeout_seconds: float,
    ) -> dict[str, Any] | None:
        """Wait for a naturally captured witness whose receive and body times are newer."""
        if not isinstance(after, datetime) or after.tzinfo is None or after.utcoffset() is None:
            raise ValueError("after must be a timezone-aware datetime")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds < 0
        ):
            raise ValueError("timeout_seconds must be finite and non-negative")
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            while not self._stop_event.is_set():
                snapshot = self._snapshot
                if isinstance(snapshot, dict):
                    source = snapshot.get("source")
                    received = self._parse_witness_time(source, "responseReceivedAt")
                    captured = self._parse_witness_time(source, "bodyCapturedAt")
                    if received is not None and captured is not None and received > after and captured > after:
                        return copy.deepcopy(snapshot)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
            return None

    @staticmethod
    def _parse_witness_time(source: Any, field: str) -> datetime | None:
        if not isinstance(source, dict):
            return None
        value = source.get(field)
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                cancellable_capture = getattr(self._observer, "capture_cancellable", None)
                if callable(cancellable_capture):
                    snapshot = cancellable_capture(self._stop_event)
                else:
                    snapshot = self._observer.capture()
                status = getattr(self._observer, "last_status", "unknown")
            except Exception as exc:  # Keep the reader fail-closed and retryable.
                snapshot = None
                status = f"observer_exception:{type(exc).__name__}"

            with self._condition:
                if snapshot is not None:
                    self._snapshot = copy.deepcopy(snapshot)
                    status = "captured"
                self._last_status = str(status)
                self._condition.notify_all()

            if self._stop_event.wait(self._retry_delay_seconds):
                break
