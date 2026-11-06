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
from typing import Any, Callable

from kancollebot.adapters.poi.port_response_observer import PassivePortResponseObserver


class BackgroundPortSnapshotProvider:
    """Continuously collect natural port responses without blocking readers."""

    def __init__(
        self,
        observer: PassivePortResponseObserver,
        *,
        retry_delay_seconds: float = 0.25,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        if not math.isfinite(retry_delay_seconds) or retry_delay_seconds <= 0:
            raise ValueError("retry_delay_seconds must be finite and positive")
        if on_status is not None and not callable(on_status):
            raise ValueError("on_status must be callable or None")
        self._observer = observer
        self._on_status = on_status
        self._retry_delay_seconds = retry_delay_seconds
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot: dict[str, Any] | None = None
        self._last_status = "not_started"

    def __call__(self) -> dict[str, Any] | None:
        """Return the latest witness immediately and start collection lazily."""
        with self._lock:
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
        with self._lock:
            return copy.deepcopy(self._snapshot)

    @property
    def last_status(self) -> str:
        with self._lock:
            return self._last_status

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def close(self, *, join_timeout_seconds: float = 1.0) -> None:
        """Stop future cycles and cancel an in-flight observer when supported.

        PassivePortResponseObserver implements cancellable capture. A generic
        observer exposing only capture() may continue until its own call returns.
        """
        if not math.isfinite(join_timeout_seconds) or join_timeout_seconds < 0:
            raise ValueError("join_timeout_seconds must be finite and non-negative")
        self._stop_event.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(join_timeout_seconds)

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

            callback = None
            status = str(status)
            with self._lock:
                if snapshot is not None:
                    self._snapshot = copy.deepcopy(snapshot)
                    status = "captured"
                if status != self._last_status:
                    callback = self._on_status
                self._last_status = status

            # The callback is deliberately limited to thread-safe in-memory
            # state (the recovery gate); background observer threads must not
            # share the event sink or inherit a foreground correlation scope.
            if callback is not None:
                callback(status)

            if self._stop_event.wait(self._retry_delay_seconds):
                break
