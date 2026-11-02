"""Visible, non-mutating Poi UI navigation used to obtain a natural port witness.

This adapter only navigates HOME_PORT -> SORTIE_MENU -> EXPEDITION_LIST ->
HOME_PORT. It never selects a mission/fleet, dispatches, collects rewards,
resupplies, reloads the page, or creates a game-server request. Every click is
single-shot and requires two-frame state evidence before the next click.
"""
from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from kancollebot.adapters.poi.cdp_input import RecordedPoint
from kancollebot.expeditions.continuous_dispatch_controller import UiStateEvidence
from kancollebot.expeditions.continuous_scheduler import UiOperationArbiter


HOME_PORT = RecordedPoint(50, 50, "home-port")
SORTIE = RecordedPoint(280, 425, "sortie")
EXPEDITION_MODE = RecordedPoint(1000, 360, "expedition-mode")


class VisibleUiRefreshError(RuntimeError):
    """A visible navigation checkpoint failed; no automatic click retry is allowed."""


class VisibleClickInput(Protocol):
    def click(self, point: RecordedPoint) -> None:
        """Send one visible mouse click after validating the current viewport."""
        ...


class FleetStateReader(Protocol):
    def read_fleets(self) -> Any:
        """Return one normalized Poi fleet observation."""
        ...


class VisibleUiPortRefreshDriver:
    """Refresh Poi's current port data through the already-reviewed visible UI path."""

    _STEPS = (
        ("open_sortie", SORTIE, "SORTIE_MENU"),
        ("open_expedition_list", EXPEDITION_MODE, "EXPEDITION_LIST"),
        ("return_home_port", HOME_PORT, "HOME_PORT"),
    )

    def __init__(
        self,
        *,
        game_input: VisibleClickInput,
        verify_state: Callable[[str], UiStateEvidence],
        ui_arbiter: UiOperationArbiter,
        event_sink: Callable[[str, Mapping[str, object]], None],
        sleep: Callable[[float], None] = time.sleep,
        settle_seconds: float = 1.0,
    ) -> None:
        if not callable(getattr(game_input, "click", None)):
            raise ValueError("game_input must provide click(point)")
        if not callable(verify_state) or not isinstance(ui_arbiter, UiOperationArbiter):
            raise ValueError("a state verifier and shared UI arbiter are required")
        if not callable(event_sink) or not callable(sleep):
            raise ValueError("event_sink and sleep must be callable")
        if not math.isfinite(settle_seconds) or settle_seconds < 0:
            raise ValueError("settle_seconds must be finite and non-negative")
        self._input = game_input
        self._verify_state = verify_state
        self._arbiter = ui_arbiter
        self._event_sink = event_sink
        self._sleep = sleep
        self._settle_seconds = settle_seconds

    def _confirm(self, expected: str, *, phase: str) -> UiStateEvidence:
        evidence = self._verify_state(expected)
        if not isinstance(evidence, UiStateEvidence):
            raise VisibleUiRefreshError("UI verifier returned no structured two-frame evidence")
        if (
            evidence.state != expected
            or evidence.consecutive_states != (expected, expected)
        ):
            raise VisibleUiRefreshError(
                f"Expected two consecutive {expected} states during {phase}; "
                f"observed {evidence.consecutive_states!r}"
            )
        return evidence

    def refresh_port_state(self) -> UiStateEvidence:
        """Perform the exact three-click navigation once; never retry an ambiguous click."""
        clicks_attempted = 0
        clicks_completed = 0
        current_state = "UNKNOWN"
        try:
            with self._arbiter.operation("visible-poi-port-refresh"):
                initial = self._confirm("HOME_PORT", phase="refresh precondition")
                current_state = initial.state
                self._event_sink("ui_refresh_started", {
                    "initial_state": current_state,
                    "evidence": initial.evidence,
                    "game_actions_enabled": False,
                })
                final_evidence = initial
                for index, (action, point, expected_after) in enumerate(self._STEPS, start=1):
                    self._event_sink("ui_refresh_action_precondition", {
                        "step": index,
                        "action": action,
                        "target": point.label,
                        "expected_state": current_state,
                        "clicks_attempted": clicks_attempted,
                        "clicks_completed": clicks_completed,
                    })
                    clicks_attempted += 1
                    self._event_sink("ui_refresh_click_attempted", {
                        "step": index,
                        "action": action,
                        "target": point.label,
                        "clicks_attempted": clicks_attempted,
                    })
                    self._input.click(point)
                    clicks_completed += 1
                    self._event_sink("ui_refresh_click_sent", {
                        "step": index,
                        "action": action,
                        "target": point.label,
                        "clicks_sent": clicks_completed,
                    })
                    if self._settle_seconds:
                        self._sleep(self._settle_seconds)
                    self._event_sink("ui_refresh_wait_completed", {
                        "step": index,
                        "action": action,
                        "wait_seconds": self._settle_seconds,
                    })
                    final_evidence = self._confirm(
                        expected_after, phase=f"after {action}"
                    )
                    current_state = final_evidence.state
                    self._event_sink("ui_refresh_state_confirmed", {
                        "step": index,
                        "action": action,
                        "state": current_state,
                        "consecutive_states": final_evidence.consecutive_states,
                        "evidence": final_evidence.evidence,
                    })
                self._event_sink("ui_refresh_completed", {
                    "sequence": ["HOME_PORT", "SORTIE_MENU", "EXPEDITION_LIST", "HOME_PORT"],
                    "clicks_sent": clicks_completed,
                    "final_state": current_state,
                })
                return final_evidence
        except Exception as exc:
            try:
                self._event_sink("ui_refresh_blocked", {
                    "clicks_attempted": clicks_attempted,
                    "clicks_completed": clicks_completed,
                    "outcome_ambiguous": clicks_attempted > clicks_completed,
                    "last_confirmed_state": current_state,
                    "error_type": type(exc).__name__,
                    "reason": "checkpoint failed or UI action outcome is ambiguous; automatic retry forbidden",
                })
            except Exception:
                pass
            if isinstance(exc, VisibleUiRefreshError):
                raise
            raise VisibleUiRefreshError(
                f"Visible Poi refresh stopped after {clicks_attempted} click attempt(s): {type(exc).__name__}"
            ) from exc


class FreshnessRefreshingStateReader:
    """Obtain a fresh witness through visible navigation when passive observation stalls.

    A single read may trigger at most one navigation sequence. Subsequent reads
    respect the cooldown. If no matching natural response arrives before the
    bounded wait expires, the last unverified observation is returned unchanged
    so the continuous monitor remains fail-closed.
    """

    def __init__(
        self,
        reader: FleetStateReader,
        refresh_driver: VisibleUiPortRefreshDriver,
        *,
        max_wait_seconds: float = 45.0,
        poll_interval_seconds: float = 0.5,
        refresh_cooldown_seconds: float = 30.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        values = (max_wait_seconds, poll_interval_seconds, refresh_cooldown_seconds)
        if any(not math.isfinite(value) for value in values):
            raise ValueError("refresh timing values must be finite")
        if max_wait_seconds <= 0 or poll_interval_seconds <= 0 or refresh_cooldown_seconds < 0:
            raise ValueError("refresh wait/poll must be positive and cooldown non-negative")
        if not callable(getattr(reader, "read_fleets", None)):
            raise ValueError("reader must provide read_fleets()")
        if not callable(getattr(refresh_driver, "refresh_port_state", None)):
            raise ValueError("refresh_driver must provide refresh_port_state()")
        if not callable(monotonic) or not callable(sleep):
            raise ValueError("monotonic and sleep must be callable")
        self._reader = reader
        self._refresh_driver = refresh_driver
        self._max_wait_seconds = max_wait_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._refresh_cooldown_seconds = refresh_cooldown_seconds
        self._monotonic = monotonic
        self._sleep = sleep
        self._refresh_lock = threading.Lock()
        self._last_refresh_at: float | None = None

    def read_fleets(self) -> Any:
        with self._refresh_lock:
            observation = self._reader.read_fleets()
            if bool(getattr(observation, "source_freshness_verified", False)):
                return observation

            now = self._monotonic()
            if (
                self._last_refresh_at is not None
                and now - self._last_refresh_at < self._refresh_cooldown_seconds
            ):
                return observation

            # Set the cooldown before touching the UI. Even an ambiguous click
            # must not be replayed automatically by the next monitor tick.
            self._last_refresh_at = now
            try:
                self._refresh_driver.refresh_port_state()

                deadline = self._monotonic() + self._max_wait_seconds
                while self._monotonic() < deadline:
                    observation = self._reader.read_fleets()
                    if bool(getattr(observation, "source_freshness_verified", False)):
                        return observation
                    remaining = deadline - self._monotonic()
                    if remaining <= 0:
                        break
                    self._sleep(min(self._poll_interval_seconds, remaining))
                return observation
            finally:
                # Cooldown begins after the full navigation/witness wait, not
                # before it; otherwise a long timeout could expire the guard.
                self._last_refresh_at = self._monotonic()
