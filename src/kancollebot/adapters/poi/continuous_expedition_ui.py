"""Visible-UI driver for the fixed Fleet 3 -> mission 3 expedition.

This adapter owns only CDP mouse input and visual checkpoint verification.
It never invokes game APIs, mutates page state, or retries an ambiguous click.
The mission/fleet mapping is deliberately narrow until additional mission
selection fingerprints have been calibrated and independently verified.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Protocol

from kancollebot.adapters.poi.cdp_input import RecordedPoint
from kancollebot.expeditions.continuous_dispatch_controller import UiStateEvidence


class ContinuousUiDriverError(RuntimeError):
    """A visible expedition UI transition cannot be proven safe."""


class VisibleGameInput(Protocol):
    def click(self, point: RecordedPoint) -> None:
        """Click one coordinate in the validated visible game viewport."""
        ...


HOME_PORT = RecordedPoint(50, 50, "home-port")
SORTIE = RecordedPoint(280, 425, "sortie")
EXPEDITION_MODE = RecordedPoint(1000, 360, "expedition-mode")
MISSION_3_ROW = RecordedPoint(350, 350, "mission-3-row")
MISSION_CONFIRM = RecordedPoint(1030, 670, "mission-confirm")
FLEET_3_TAB = RecordedPoint(630, 170, "fleet-3-tab")
EXPEDITION_START = RecordedPoint(920, 670, "expedition-start")


class CdpContinuousExpeditionUiDriver:
    """Implement the ContinuousUiDriver contract for Fleet 3 / mission 3 only.

    The current verified visual assets identify the mission-3 selected state
    and Fleet-3 tab. Other fleet/mission combinations are rejected before any
    click rather than guessed from a generic selected-row fingerprint.
    """

    def __init__(
        self,
        *,
        game_input: VisibleGameInput,
        verify_state: Callable[[str], UiStateEvidence],
        event_sink: Callable[[str, Mapping[str, object]], None],
        neutralize_pointer: Callable[[], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        settle_seconds: float = 1.0,
    ) -> None:
        if not callable(getattr(game_input, "click", None)):
            raise ValueError("game_input must provide click(point)")
        if not callable(verify_state) or not callable(event_sink) or not callable(sleep):
            raise ValueError("verify_state, event_sink, and sleep must be callable")
        if settle_seconds < 0:
            raise ValueError("settle_seconds must be non-negative")
        self._input = game_input
        self._verify_state = verify_state
        self._event_sink = event_sink
        self._neutralize_pointer = neutralize_pointer or (lambda: None)
        self._sleep = sleep
        self._settle_seconds = settle_seconds

    def _confirm(self, expected: str, *, phase: str) -> UiStateEvidence:
        self._neutralize_pointer()
        evidence = self._verify_state(expected)
        if not isinstance(evidence, UiStateEvidence):
            raise ContinuousUiDriverError(
                f"{phase}: verifier returned no structured UI evidence"
            )
        if (
            evidence.state != expected
            or evidence.consecutive_states != (expected, expected)
        ):
            raise ContinuousUiDriverError(
                f"{phase}: expected two consecutive {expected} frames; "
                f"observed {evidence.consecutive_states!r}"
            )
        self._event_sink("continuous_ui_state_confirmed", {
            "phase": phase,
            "state": expected,
            "evidence": evidence.evidence,
            "consecutive_states": evidence.consecutive_states,
        })
        return evidence

    def _confirm_one_of(
        self, expected_states: tuple[str, ...], *, phase: str
    ) -> UiStateEvidence:
        errors = []
        for expected in expected_states:
            try:
                return self._confirm(expected, phase=phase)
            except Exception as exc:
                errors.append(f"{expected}: {type(exc).__name__}")
        raise ContinuousUiDriverError(
            f"{phase}: no supported UI state matched ({'; '.join(errors)})"
        )

    def _click(self, point: RecordedPoint, *, action: str, step: int) -> None:
        self._event_sink("continuous_ui_click_attempted", {
            "step": step,
            "action": action,
            "target": point.label,
        })
        try:
            self._input.click(point)
        except Exception as exc:
            self._event_sink("continuous_ui_click_ambiguous", {
                "step": step,
                "action": action,
                "target": point.label,
                "error_type": type(exc).__name__,
            })
            raise
        self._event_sink("continuous_ui_click_sent", {
            "step": step,
            "action": action,
            "target": point.label,
        })
        if self._settle_seconds:
            self._sleep(self._settle_seconds)

    def prepare_dispatch(self, *, fleet_id: int, mission_id: int) -> UiStateEvidence:
        """Navigate to the verified fleet-selection state without starting an expedition."""
        if (fleet_id, mission_id) != (3, 3):
            raise ContinuousUiDriverError(
                "No independently verified UI mapping for "
                f"Fleet {fleet_id} / mission {mission_id}; supported target is Fleet 3 / mission 3"
            )

        self._event_sink("continuous_ui_dispatch_preparation_started", {
            "fleet_id": fleet_id,
            "mission_id": mission_id,
            "game_api_requests": False,
            "expedition_start_clicked": False,
        })
        try:
            self._confirm("HOME_PORT", phase="dispatch precondition")
            self._click(SORTIE, action="open_sortie", step=1)
            self._confirm("SORTIE_MENU", phase="after open_sortie")

            self._click(EXPEDITION_MODE, action="open_expedition_list", step=2)
            self._confirm("EXPEDITION_LIST", phase="after open_expedition_list")

            self._click(MISSION_3_ROW, action="select_mission_3", step=3)
            self._confirm("MISSION_SELECTED", phase="after select_mission_3")

            self._click(MISSION_CONFIRM, action="confirm_mission", step=4)
            selected_fleet = self._confirm_one_of(
                ("FLEET_2_SELECTED", "FLEET_3_SELECTED"),
                phase="after confirm_mission",
            )
            if selected_fleet.state != "FLEET_3_SELECTED":
                self._click(FLEET_3_TAB, action="select_fleet_3", step=5)
                self._confirm("FLEET_3_SELECTED", phase="after select_fleet_3")
            else:
                self._event_sink("continuous_ui_fleet_3_already_selected", {
                    "fleet_id": fleet_id,
                })

            evidence = self._confirm(
                "FLEET_3_SELECTED",
                phase="expedition start precondition",
            )
            self._event_sink("continuous_ui_dispatch_preparation_completed", {
                "fleet_id": fleet_id,
                "mission_id": mission_id,
                "final_state": evidence.state,
                "expedition_start_clicked": False,
            })
            return evidence
        except Exception as exc:
            try:
                self._event_sink("continuous_ui_dispatch_preparation_blocked", {
                    "fleet_id": fleet_id,
                    "mission_id": mission_id,
                    "error_type": type(exc).__name__,
                    "reason": "checkpoint failed or UI click outcome is ambiguous; no automatic retry",
                })
            except Exception:
                pass
            raise

    def dispatch_expedition(self, *, fleet_id: int, mission_id: int) -> None:
        """Start the fixed expedition only after the complete visual preparation gate."""
        try:
            self.prepare_dispatch(fleet_id=fleet_id, mission_id=mission_id)
            self._confirm("FLEET_3_SELECTED", phase="immediate expedition start precondition")
            self._click(EXPEDITION_START, action="start_expedition", step=6)
            self._event_sink("continuous_ui_dispatch_input_completed", {
                "fleet_id": fleet_id,
                "mission_id": mission_id,
                "postcondition": "must_be_confirmed_by_newer_poi_observation",
            })
        except Exception as exc:
            try:
                self._event_sink("continuous_ui_dispatch_blocked", {
                    "fleet_id": fleet_id,
                    "mission_id": mission_id,
                    "error_type": type(exc).__name__,
                    "reason": "checkpoint failed or UI click outcome is ambiguous; no automatic retry",
                })
            except Exception:
                pass
            raise

    def return_to_home_port(
        self,
        *,
        fleet_id: int,
        expected_current_state: str = "EXPEDITION_LIST",
    ) -> UiStateEvidence:
        """Return from one explicitly verified page with exactly one navigation click."""
        if fleet_id != 3:
            raise ContinuousUiDriverError(
                f"Home Port recovery is not calibrated for Fleet {fleet_id}"
            )
        if expected_current_state not in (
            "EXPEDITION_LIST",
            "MISSION_SELECTED",
            "FLEET_2_SELECTED",
            "FLEET_3_SELECTED",
        ):
            raise ContinuousUiDriverError(
                f"Unsupported Home Port recovery precondition: {expected_current_state}"
            )
        self._confirm(
            expected_current_state,
            phase="precondition for return_home_port",
        )
        self._click(HOME_PORT, action="return_home_port", step=7)
        try:
            return self._confirm("HOME_PORT", phase="after return_home_port")
        except Exception as first_error:
            self._event_sink("continuous_ui_home_port_verification_pending", {
                "fleet_id": fleet_id,
                "error_type": type(first_error).__name__,
                "retry": "read-only visual verification only; no second click",
            })
            self._sleep(self._settle_seconds)
            return self._confirm("HOME_PORT", phase="after return_home_port observation retry")
