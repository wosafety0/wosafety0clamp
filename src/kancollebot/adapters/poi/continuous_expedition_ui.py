"""Visible-UI driver for dispatching an expedition the account actually has.

The driver owns CDP mouse input and visual checkpoint verification. It never
invokes game APIs, mutates page state, or retries an ambiguous click.

Three things had to become general before "dispatch any expedition" meant
anything, and each one is the reason for a piece of this module:

* **Which row.** ``locate_row`` resolves a display number to the row that is on
  screen right now, using the measured position index. The old constant
  ``(350, 350)`` is the third row only while the list sits at its top on tab 1.
* **Which mission.** After the row click the panel is compared against every
  known expedition's name band; the winner must be the intended one. The old
  ``MISSION_SELECTED`` checkpoint was a fingerprint of the whole panel and
  accepted mission 04 for mission 3 at 0.9871, so it proved layout, not identity.
* **Which fleet.** The fleet-selection screen is read through its tab strip, and
  the requested fleet must be the one the game paints as selected before the
  irreversible start click.

Every one of those is *evidence about what is on screen*, not a guess about what a
click should have done: a failure raises before the next click, and nothing is
ever retried.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Protocol

from kancollebot.adapters.poi.cdp_input import RecordedPoint
from kancollebot.calibration import (
    DECISION_POINT,
    DEFAULT_CATALOG,
    FLEET_TAB_POINTS,
    DetailMatch,
    MarkMatch,
    RowLocation,
)
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
MISSION_CONFIRM = RecordedPoint(DECISION_POINT[0], DECISION_POINT[1], "mission-confirm")
EXPEDITION_START = RecordedPoint(920, 670, "expedition-start")

# Fleets the game lets a player send on an expedition. Fleet 1 is drawn on the
# tab strip but cannot be dispatched, so it is not offered.
DEFAULT_SUPPORTED_FLEETS = (2, 3, 4)


class CdpContinuousExpeditionUiDriver:
    """Drive one expedition dispatch through measured pixels and click receipts.

    ``capture`` must return a fresh base64 PNG with the pointer parked off the
    UI. It is only called after a state checkpoint has proven which screen is
    showing, so a frame is never read while a transition is in flight.
    """

    def __init__(
        self,
        *,
        game_input: VisibleGameInput,
        verify_state: Callable[[str], UiStateEvidence],
        event_sink: Callable[[str, Mapping[str, object]], None],
        details: "DetailSourceLike",
        marks: "DispatchMarksLike",
        capture: Callable[[], str],
        neutralize_pointer: Callable[[], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        settle_seconds: float = 1.0,
        locate_row: Callable[[str, int | None], RowLocation] | None = None,
        supported_fleets: tuple[int, ...] = DEFAULT_SUPPORTED_FLEETS,
    ) -> None:
        if not callable(getattr(game_input, "click", None)):
            raise ValueError("game_input must provide click(point)")
        for name, value in (
            ("verify_state", verify_state),
            ("event_sink", event_sink),
            ("capture", capture),
            ("sleep", sleep),
        ):
            if not callable(value):
                raise ValueError(f"{name} must be callable")
        for method in ("reference", "require"):
            if not callable(getattr(details, method, None)):
                raise ValueError(f"details must provide {method}()")
        for method in ("require_decision", "require_fleet", "fleets"):
            if not callable(getattr(marks, method, None)):
                raise ValueError(f"marks must provide {method}()")
        if settle_seconds < 0:
            raise ValueError("settle_seconds must be non-negative")
        if locate_row is not None and not callable(locate_row):
            raise ValueError("locate_row must be callable or None")
        if not supported_fleets:
            raise ValueError("supported_fleets must not be empty")
        self._input = game_input
        self._verify_state = verify_state
        self._event_sink = event_sink
        self._details = details
        self._marks = marks
        self._capture = capture
        self._neutralize_pointer = neutralize_pointer or (lambda: None)
        self._sleep = sleep
        self._settle_seconds = settle_seconds
        self._locate_row = locate_row
        self._supported_fleets = tuple(supported_fleets)

    # -- evidence ---------------------------------------------------------
    def _frame(self) -> str:
        self._neutralize_pointer()
        return self._capture()

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

    def _mission_row_point(self, expedition_id: str) -> RecordedPoint:
        """Where that expedition's row is *now*, proven by the list session."""
        if self._locate_row is None:
            raise ContinuousUiDriverError(
                f"cannot place {expedition_id}'s row without a list session; "
                "refusing to click a coordinate that was never measured"
            )
        try:
            location = self._locate_row(expedition_id, None)
        except Exception as exc:
            raise ContinuousUiDriverError(
                f"cannot place {expedition_id}'s row: {type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(location, RowLocation):
            raise ContinuousUiDriverError("the list session returned no structured row location")
        if location.expedition_id != expedition_id:
            raise ContinuousUiDriverError(
                f"the list session placed {location.expedition_id}, not {expedition_id}"
            )
        self._event_sink("continuous_ui_row_located", {
            "expedition_id": location.expedition_id,
            "tab": location.tab,
            "position": location.position,
            "offset": location.offset,
            "row_index": location.row_index,
            "scroll_clicks": location.scroll_clicks,
            "tab_clicks": location.tab_clicks,
            "detail": location.detail,
        })
        return RecordedPoint(
            location.point[0], location.point[1],
            f"expedition-row-{location.expedition_id}-pos{location.position}",
        )

    def _require_detail(self, expedition_id: str, *, phase: str) -> DetailMatch:
        frame = self._frame()
        try:
            match = self._details.require(expedition_id, frame)
        except Exception as exc:
            raise ContinuousUiDriverError(
                f"{phase}: the selected panel is not {expedition_id}: {exc}"
            ) from exc
        self._event_sink("continuous_ui_detail_confirmed", {
            "phase": phase,
            "expected": expedition_id,
            "observed": match.expedition_id,
            **match.to_dict(),
        })
        return match

    def _require_decision(self, *, phase: str) -> MarkMatch:
        frame = self._frame()
        try:
            match = self._marks.require_decision(frame)
        except Exception as exc:
            raise ContinuousUiDriverError(f"{phase}: {exc}") from exc
        self._event_sink("continuous_ui_decision_confirmed", {
            "phase": phase,
            **match.to_dict(),
        })
        return match

    def _selected_fleet(self) -> int | None:
        """Which fleet tab the fleet-selection screen paints as selected."""
        frame = self._frame()
        best: tuple[float, int] | None = None
        for fleet_id in getattr(self._marks, "fleets", lambda: ())():
            try:
                match = self._marks.require_fleet(fleet_id, frame)
            except Exception:
                continue
            if best is None or match.score > best[0]:
                best = (match.score, fleet_id)
        return None if best is None else best[1]

    def _require_fleet(self, fleet_id: int, *, phase: str) -> MarkMatch:
        frame = self._frame()
        try:
            match = self._marks.require_fleet(fleet_id, frame)
        except Exception as exc:
            raise ContinuousUiDriverError(f"{phase}: {exc}") from exc
        self._event_sink("continuous_ui_fleet_confirmed", {
            "phase": phase,
            "fleet_id": fleet_id,
            **match.to_dict(),
        })
        return match

    # -- the transaction --------------------------------------------------
    def prepare_dispatch(self, *, fleet_id: int, mission_id: int) -> UiStateEvidence:
        """Navigate to the verified start-precondition state without starting."""
        expedition_id = DEFAULT_CATALOG.number_of_mission_id(mission_id)
        if expedition_id is None:
            raise ContinuousUiDriverError(
                f"mission id {mission_id} is not in the master-data table, so its row "
                "cannot be named; refusing to guess"
            )
        if fleet_id not in self._supported_fleets:
            raise ContinuousUiDriverError(
                f"fleet {fleet_id} cannot be dispatched; supported fleets are "
                f"{list(self._supported_fleets)}"
            )
        if self._details.reference(expedition_id) is None:
            raise ContinuousUiDriverError(
                f"no detail reference for {expedition_id}; walk that list before dispatching it"
            )
        available = tuple(getattr(self._marks, "fleets", lambda: ())())
        if fleet_id not in available:
            raise ContinuousUiDriverError(
                f"no fleet tab reference for fleet {fleet_id}; capture the dispatch marks first"
            )

        self._event_sink("continuous_ui_dispatch_preparation_started", {
            "fleet_id": fleet_id,
            "mission_id": mission_id,
            "expedition_id": expedition_id,
            "game_api_requests": False,
            "expedition_start_clicked": False,
        })
        try:
            self._confirm("HOME_PORT", phase="dispatch precondition")
            self._click(SORTIE, action="open_sortie", step=1)
            self._confirm("SORTIE_MENU", phase="after open_sortie")

            self._click(EXPEDITION_MODE, action="open_expedition_list", step=2)
            self._confirm("EXPEDITION_LIST", phase="after open_expedition_list")

            row_point = self._mission_row_point(expedition_id)
            # Locating the row scrolls the list and may switch the area tab, so
            # the checkpoint is re-established on the frame the click lands on.
            self._confirm("EXPEDITION_LIST", phase="dispatch row precondition")
            self._click(row_point, action=f"select_mission_{expedition_id}", step=3)

            self._require_detail(expedition_id, phase="after selecting the row")
            self._require_decision(phase="before confirming the mission")

            self._click(MISSION_CONFIRM, action="confirm_mission", step=4)

            selected = self._selected_fleet()
            if selected is None:
                raise ContinuousUiDriverError(
                    "after confirming the mission the fleet-selection screen did not "
                    "show any recognised fleet tab; refusing to continue"
                )
            if selected != fleet_id:
                self._event_sink("continuous_ui_fleet_switch_required", {
                    "selected": selected,
                    "requested": fleet_id,
                })
                tab = FLEET_TAB_POINTS[fleet_id]
                point = RecordedPoint(tab[0], tab[1], f"fleet-{fleet_id}-tab")
                self._click(point, action=f"select_fleet_{fleet_id}", step=5)
            else:
                self._event_sink("continuous_ui_fleet_already_selected", {
                    "fleet_id": fleet_id,
                })

            match = self._require_fleet(fleet_id, phase="expedition start precondition")
            state = f"FLEET_{fleet_id}_SELECTED"
            evidence = UiStateEvidence(
                state=state,
                consecutive_states=(state, state),
                evidence=f"fleet_tab_fingerprint:{match.score:.4f}",
            )
            self._event_sink("continuous_ui_dispatch_preparation_completed", {
                "fleet_id": fleet_id,
                "mission_id": mission_id,
                "expedition_id": expedition_id,
                "final_state": state,
                "expedition_start_clicked": False,
            })
            return evidence
        except Exception as exc:
            try:
                self._event_sink("continuous_ui_dispatch_preparation_blocked", {
                    "fleet_id": fleet_id,
                    "mission_id": mission_id,
                    "expedition_id": expedition_id,
                    "error_type": type(exc).__name__,
                    "reason": "checkpoint failed or UI click outcome is ambiguous; no automatic retry",
                })
            except Exception:
                pass
            raise

    def dispatch_expedition(self, *, fleet_id: int, mission_id: int) -> None:
        """Start the expedition only after the complete visual preparation gate."""
        try:
            self.prepare_dispatch(fleet_id=fleet_id, mission_id=mission_id)
            # Re-read the fleet tab on a fresh frame immediately before the one
            # click that cannot be undone; the preparation's own frames are older.
            self._require_fleet(fleet_id, phase="immediate expedition start precondition")
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
        if fleet_id not in self._supported_fleets:
            raise ContinuousUiDriverError(
                f"Home Port recovery is not calibrated for Fleet {fleet_id}"
            )
        allowed = ["EXPEDITION_LIST", "MISSION_SELECTED"]
        allowed.extend(f"FLEET_{candidate}_SELECTED" for candidate in self._supported_fleets)
        if expected_current_state not in allowed:
            raise ContinuousUiDriverError(
                f"Unsupported Home Port recovery precondition: {expected_current_state}"
            )
        self._confirm(expected_current_state, phase="precondition for return_home_port")
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


class DetailSourceLike(Protocol):
    """The identity half of the evidence, as far as this driver needs it."""

    def reference(self, expedition_id: str) -> object | None: ...

    def require(self, expedition_id: str, screenshot_base64: str) -> DetailMatch: ...


class DispatchMarksLike(Protocol):
    """The two dispatch controls, as far as this driver needs them."""

    def require_decision(self, screenshot_base64: str) -> MarkMatch: ...

    def require_fleet(self, fleet_id: int, screenshot_base64: str) -> MarkMatch: ...

    def fleets(self) -> tuple[int, ...]: ...
