"""Production ``ContinuousUiDriver`` over CDP visible-UI input.

Extracted from the reviewed ``tools/one_expedition_ui_loop.py`` flow into a
reusable, protocol-conformant driver. Safety properties carried over:

- The driver executes only a reviewed, explicitly declared dispatch plan.
  Coordinates come from reviewed captures; unknown fleet/mission combinations
  fail closed instead of inventing targets.
- Every navigation transition requires a verified pre and post UI checkpoint
  (accessibility evidence first, then two-consecutive in-memory fingerprints,
  then template matching). A failed checkpoint prevents every later click.
- The irreversible expedition-start click is preceded by a fresh Poi Redux
  supply and composition re-check and is never retried.
- No game API/server request is ever constructed; input is CDP mouse input to
  the visible game UI only.

Clock, sleep, and event-sink dependencies are injectable so the full flow is
testable offline without a live Poi.
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from kancollebot.adapters.poi.cdp_fleet_state import PoiFleetMissionState
from kancollebot.adapters.poi.cdp_input import RecordedPoint, home_port_candidates, home_port_point
from kancollebot.adapters.poi.ui_state_gate import UiState, UiStateGateError, require_state
from kancollebot.expeditions.continuous_dispatch_controller import UiStateEvidence

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_FINGERPRINTS_PATH = PACKAGE_DIR / "ooi_1200x720_fingerprints.json"

HOME_PORT_POINT = home_port_point()
_MOUSE_PARK_X = 1195
_MOUSE_PARK_Y = 715


class ContinuousUiDriverError(RuntimeError):
    """The observed state or UI sequence cannot safely continue."""


class StateGate(Protocol):
    """Fail-closed UI state evidence provider used before any input."""

    def require(self, expected: UiState) -> str:
        """Return an evidence label or raise ``ContinuousUiDriverError``/``UiStateGateError``."""
        ...


class CdpWriteTransport(Protocol):
    def send(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send one allowlisted CDP command."""
        ...


class ScreenshotInput(Protocol):
    def capture_screenshot(self) -> str:
        """Return a base64 PNG payload of the current renderer surface."""
        ...


class ClickInput(Protocol):
    def click(self, point: RecordedPoint) -> None:
        """Send one validated click without retry."""
        ...


@dataclass(frozen=True, slots=True)
class DispatchStep:
    """One reviewed click with its mandatory pre/post UI checkpoint states."""

    point: RecordedPoint
    settle_seconds: float
    state_before: str
    state_after: str

    def __post_init__(self) -> None:
        if not isinstance(self.point, RecordedPoint):
            raise ValueError("point must be a RecordedPoint")
        if (
            isinstance(self.settle_seconds, bool)
            or not isinstance(self.settle_seconds, (int, float))
            or not math.isfinite(self.settle_seconds)
            or self.settle_seconds < 0
        ):
            raise ValueError("settle_seconds must be a finite non-negative number")
        for name in (self.state_before, self.state_after):
            if not isinstance(name, str) or not name.strip():
                raise ValueError("checkpoint state names must be non-empty strings")
        if UiState.UNKNOWN.value in (self.state_before, self.state_after):
            raise ValueError("UNKNOWN is not an acceptable checkpoint state")


@dataclass(frozen=True, slots=True)
class DispatchPlan:
    """A reviewed click sequence bound to exactly one fleet/mission pair."""

    fleet_id: int
    mission_id: int
    steps: tuple[DispatchStep, ...]

    def __post_init__(self) -> None:
        if isinstance(self.fleet_id, bool) or not isinstance(self.fleet_id, int) or self.fleet_id < 1:
            raise ValueError("fleet_id must be a positive integer")
        if isinstance(self.mission_id, bool) or not isinstance(self.mission_id, int) or self.mission_id < 1:
            raise ValueError("mission_id must be a positive integer")
        if not isinstance(self.steps, tuple) or not self.steps:
            raise ValueError("steps must be a non-empty tuple")
        first = self.steps[0]
        if first.state_before != UiState.HOME_PORT.value:
            raise ValueError("the first step must depart from HOME_PORT")
        final = self.steps[-1]
        if final.state_after != "EXPEDITION_ACTIVE":
            raise ValueError("the final step must activate the expedition")
        if any(step.state_after == "EXPEDITION_ACTIVE" for step in self.steps[:-1]):
            raise ValueError("EXPEDITION_ACTIVE is only permitted as the final step")
        for current, following in zip(self.steps, self.steps[1:]):
            if current.state_after != following.state_before:
                raise ValueError(
                    f"dispatch steps must chain: {current.state_after} != {following.state_before}"
                )


DEFAULT_FLEET2_MISSION3_PLAN = DispatchPlan(
    fleet_id=2,
    mission_id=3,
    steps=(
        DispatchStep(RecordedPoint(280, 425, "sortie"), 1.0, "HOME_PORT", "SORTIE_MENU"),
        DispatchStep(RecordedPoint(1000, 360, "expedition-mode"), 1.0, "SORTIE_MENU", "EXPEDITION_LIST"),
        # State-dependent: this is the mission-confirm control on MISSION_SELECTED,
        # but overlaps the expedition-start control on fleet selection. The
        # pre-click MISSION_SELECTED checkpoint is therefore mandatory.
        DispatchStep(RecordedPoint(350, 350, "mission-3-security-task"), 0.5, "EXPEDITION_LIST", "MISSION_SELECTED"),
        DispatchStep(RecordedPoint(1030, 670, "mission-confirm"), 0.7, "MISSION_SELECTED", "FLEET_2_SELECTED"),
        DispatchStep(RecordedPoint(920, 670, "expedition-start"), 1.5, "FLEET_2_SELECTED", "EXPEDITION_ACTIVE"),
    ),
)


class ProductionStateGate:
    """Three-tier fail-closed UI state gate.

    Tier 1 uses the renderer accessibility tree. Tier 2 requires two
    consecutive in-memory Ooi fingerprint matches. Tier 3 requires two
    consecutive template matches. Supply checkbox states are out of scope for
    this module and fail closed until the production supply module provides
    their evidence.
    """

    def __init__(
        self,
        *,
        game_transport: CdpWriteTransport,
        screenshot_input: ScreenshotInput,
        sleep_fn: Callable[[float], None] = time.sleep,
        attempts: int = 3,
        fingerprint_document: Mapping[str, Any] | None = None,
        template_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
            raise ValueError("attempts must be a positive integer")
        self._game_transport = game_transport
        self._screenshot_input = screenshot_input
        self._sleep = sleep_fn
        self._attempts = attempts
        self._provided_document = dict(fingerprint_document) if fingerprint_document is not None else None
        self._provided_template_dir = template_dir
        self._document_loaded = False
        self._document: dict[str, Any] | None = None
        self.home_reference_base64: str | None = None

    def require(self, expected: UiState) -> str:
        try:
            result = self._game_transport.send("Accessibility.getFullAXTree", {})
            nodes = result.get("nodes") if isinstance(result, dict) else None
            require_state(nodes, expected)
            return "accessibility"
        except (UiStateGateError, ValueError) as accessible_error:
            if expected.value.startswith(("SUPPLY_FLEET_2_SHIP_", "SUPPLY_FLEET_3_SHIP_")) and expected.value.endswith(
                ("_CHECKED", "_UNCHECKED")
            ):
                raise ContinuousUiDriverError(
                    "supply checkbox evidence requires the production supply module; refusing input"
                ) from accessible_error
            document = self._load_fingerprint_document()
            if document is not None and expected.value in document.get("states", {}):
                return self._fingerprint_evidence(expected, document, accessible_error)
            template_dir = self._resolve_template_dir()
            if template_dir is None:
                raise ContinuousUiDriverError(
                    f"Expected {expected.value}; no fingerprint for this state and no "
                    "KANCOLLEBOT_TEMPLATE_DIR configured"
                ) from accessible_error
            return self._template_evidence(expected, template_dir, accessible_error)

    def capture_home_reference(self) -> None:
        """Capture a transient Home Port baseline screenshot for tier-3 gating."""
        self.home_reference_base64 = self._capture_prepared_screenshot()

    def _capture_prepared_screenshot(self) -> str:
        self._game_transport.send(
            "Input.dispatchMouseEvent",
            {"type": "mouseMoved", "x": _MOUSE_PARK_X, "y": _MOUSE_PARK_Y},
        )
        self._sleep(0.15)
        return self._screenshot_input.capture_screenshot()

    def _load_fingerprint_document(self) -> dict[str, Any] | None:
        if not self._document_loaded:
            self._document_loaded = True
            if self._provided_document is not None:
                self._document = dict(self._provided_document)
            else:
                configured = os.environ.get("KANCOLLEBOT_FINGERPRINTS_PATH")
                path = Path(configured) if configured else DEFAULT_FINGERPRINTS_PATH
                try:
                    document = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    self._document = None
                else:
                    self._document = document if isinstance(document, dict) else None
        return self._document

    def _resolve_template_dir(self) -> Path | None:
        if self._provided_template_dir is not None:
            return Path(self._provided_template_dir)
        configured = os.environ.get("KANCOLLEBOT_TEMPLATE_DIR")
        return Path(configured) if configured else None

    def _fingerprint_evidence(
        self, expected: UiState, document: Mapping[str, Any], accessible_error: Exception
    ) -> str:
        from kancollebot.adapters.poi.visual_fingerprint import (
            VisualFingerprintError,
            classify_screenshot_fingerprints,
        )

        consecutive = 0
        try:
            for attempt in range(self._attempts):
                payload = self._capture_prepared_screenshot()
                observed, scores = classify_screenshot_fingerprints(payload, document)
                if observed == expected.value:
                    consecutive += 1
                    if consecutive >= 2:
                        return "two_consecutive_in_memory_ooi_fingerprints"
                elif observed != "UNKNOWN":
                    raise ContinuousUiDriverError(
                        f"Expected {expected.value}; visual classifier observed {observed}"
                    ) from accessible_error
                else:
                    consecutive = 0
                if attempt < self._attempts - 1:
                    self._sleep(0.25)
        except (VisualFingerprintError, OSError, ValueError) as visual_error:
            raise ContinuousUiDriverError(
                f"Visual fingerprint evidence failed for {expected.value}: {visual_error}"
            ) from visual_error
        raise ContinuousUiDriverError(
            f"Expected {expected.value}; no stable fingerprint match was obtained "
            f"after {self._attempts} attempts"
        ) from accessible_error

    def _template_evidence(
        self, expected: UiState, template_dir: Path, accessible_error: Exception
    ) -> str:
        from kancollebot.adapters.poi.visual_state_gate import (
            VisualStateError,
            classify_screenshot_png,
            classify_template_scores,
            home_reference_similarity,
        )

        if not template_dir.is_dir():
            raise ContinuousUiDriverError(
                f"Visual template directory does not exist: {template_dir}"
            ) from accessible_error
        consecutive = 0
        try:
            for attempt in range(self._attempts):
                payload = self._capture_prepared_screenshot()
                visual_state, scores = classify_screenshot_png(payload, template_dir)
                if self.home_reference_base64:
                    scores["home_reference_similarity"] = home_reference_similarity(
                        payload, self.home_reference_base64
                    )
                    visual_state = classify_template_scores(scores)
                if visual_state == expected.value:
                    consecutive += 1
                    if consecutive >= 2:
                        return "two_consecutive_in_memory_template_matches"
                elif visual_state != "UNKNOWN":
                    raise ContinuousUiDriverError(
                        f"Expected {expected.value}; visual template classifier returned {visual_state}"
                    ) from accessible_error
                else:
                    consecutive = 0
                if attempt < self._attempts - 1:
                    self._sleep(0.25)
        except (VisualStateError, OSError, ValueError) as visual_error:
            raise ContinuousUiDriverError(
                f"Visual template evidence failed for {expected.value}: {visual_error}"
            ) from visual_error
        raise ContinuousUiDriverError(
            f"Expected {expected.value}; no stable template match was obtained "
            f"after {self._attempts} attempts"
        ) from accessible_error


class PoiCdpContinuousUiDriver:
    """Protocol-conformant driver executing the reviewed dispatch transaction.

    ``fleet_states_fn`` must return the current read-only Poi fleet snapshot
    (for example via ``read_poi_fleet_states`` over the state CDP transport).
    The driver itself never sends game API requests: reads are delegated to
    ``fleet_states_fn`` and input goes through ``game_input`` only.
    """

    def __init__(
        self,
        *,
        plan: DispatchPlan,
        fleet_states_fn: Callable[[], tuple[PoiFleetMissionState, ...]],
        game_input: ClickInput,
        gate: StateGate,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
        time_ms_fn: Callable[[], int] = lambda: int(time.time() * 1000),
        event_sink: Callable[[str, Mapping[str, object]], None] | None = None,
        recovery_attempts: int = 3,
        dispatch_verify_timeout_seconds: float = 12.0,
        home_port_recovery_settle_seconds: float = 2.0,
    ) -> None:
        if not isinstance(plan, DispatchPlan):
            raise ValueError("plan must be a DispatchPlan")
        if not callable(fleet_states_fn):
            raise ValueError("fleet_states_fn must be callable")
        if isinstance(recovery_attempts, bool) or not isinstance(recovery_attempts, int) or recovery_attempts < 1:
            raise ValueError("recovery_attempts must be a positive integer")
        if (
            isinstance(dispatch_verify_timeout_seconds, bool)
            or not isinstance(dispatch_verify_timeout_seconds, (int, float))
            or not math.isfinite(dispatch_verify_timeout_seconds)
            or dispatch_verify_timeout_seconds <= 0
        ):
            raise ValueError("dispatch_verify_timeout_seconds must be a positive finite number")
        if (
            isinstance(home_port_recovery_settle_seconds, bool)
            or not isinstance(home_port_recovery_settle_seconds, (int, float))
            or not math.isfinite(home_port_recovery_settle_seconds)
            or home_port_recovery_settle_seconds < 0
        ):
            raise ValueError("home_port_recovery_settle_seconds must be a finite non-negative number")
        self._plan = plan
        self._fleet_states_fn = fleet_states_fn
        self._game_input = game_input
        self._gate = gate
        self._sleep = sleep_fn
        self._monotonic = monotonic_fn
        self._time_ms = time_ms_fn
        self._recovery_attempts = recovery_attempts
        self._dispatch_verify_timeout_seconds = float(dispatch_verify_timeout_seconds)
        self._home_port_recovery_settle_seconds = float(home_port_recovery_settle_seconds)
        self.events: list[tuple[str, dict[str, object]]] = []
        self._event_sink = event_sink

    def dispatch_expedition(self, *, fleet_id: int, mission_id: int) -> None:
        """Execute the reviewed dispatch transaction or fail closed before clicking."""
        if fleet_id != self._plan.fleet_id or mission_id != self._plan.mission_id:
            raise ContinuousUiDriverError(
                f"No reviewed dispatch plan for fleet {fleet_id} mission {mission_id}; "
                f"this driver only executes fleet {self._plan.fleet_id} mission {self._plan.mission_id}"
            )
        before = self._require_fleet(fleet_id)
        if not _is_idle(before):
            raise ContinuousUiDriverError(f"Fleet {fleet_id} is not idle before dispatch: {before.mission}")
        baseline_ms = self._time_ms()
        self._emit(
            "dispatch_baseline_verified",
            fleet_id=fleet_id,
            mission_id=mission_id,
            baseline_ms=baseline_ms,
            ship_ids=[ship_id for ship_id in before.ship_ids if ship_id > 0],
        )
        for step_number, step in enumerate(self._plan.steps, start=1):
            self._verify_checkpoint(step_number, "before", step.state_before)
            if step.state_after == "EXPEDITION_ACTIVE":
                # Recheck composition and supply immediately before the
                # irreversible start click. This Redux read is not freshness
                # proof; the independent visual checkpoint remains mandatory.
                self._verify_supply_precondition(fleet_id, before)
            self._emit(
                "ui_action_precondition",
                action="dispatch_menu_click",
                step=step_number,
                point=step.point.label,
                expected_ui_state=step.state_before,
            )
            self._game_input.click(step.point)
            self._emit("ui_click_sent", point=step.point.label, step=step_number)
            self._sleep(step.settle_seconds)
            self._emit(
                "ui_action_wait_completed",
                point=step.point.label,
                step=step_number,
                wait_seconds=step.settle_seconds,
            )
            if step.state_after != "EXPEDITION_ACTIVE":
                self._verify_checkpoint(step_number, "after", step.state_after)
        deadline = self._monotonic() + self._dispatch_verify_timeout_seconds
        while self._monotonic() < deadline:
            state = self._require_fleet(fleet_id)
            if (
                state.mission_status == 1
                and state.mission_id == mission_id
                and state.final_time_ms > baseline_ms
            ):
                self._emit(
                    "dispatch_verified",
                    fleet_id=fleet_id,
                    mission_id=mission_id,
                    final_time_ms=state.final_time_ms,
                )
                return
            if not _is_idle(state) and not (state.mission_status == 1 and state.mission_id == mission_id):
                raise ContinuousUiDriverError(f"Unexpected state after dispatch clicks: {state.mission}")
            self._sleep(1.0)
        raise ContinuousUiDriverError(
            "Dispatch was not verified from Poi Redux state; do not repeat clicks"
        )

    def return_to_home_port(self, *, fleet_id: int) -> UiStateEvidence:
        """Confirm Home Port with bounded click recovery and two-frame evidence.

        The recovery clicks a bounded set of interior points within the large
        top-left 母港 button, then verifies the result with the UI state gate.
        An incorrect layout fails closed after the bounded set is exhausted.
        """
        if isinstance(fleet_id, bool) or not isinstance(fleet_id, int) or fleet_id < 1:
            raise ContinuousUiDriverError("fleet_id must be a positive integer")
        try:
            evidence = self._gate.require(UiState.HOME_PORT)
            self._emit("home_port_confirmed", fleet_id=fleet_id, attempt=0, evidence=evidence)
            return self._home_port_evidence(evidence)
        except (UiStateGateError, ContinuousUiDriverError) as exc:
            self._emit("home_port_state_not_confirmed", fleet_id=fleet_id, attempt=0, error=str(exc))
        candidates = home_port_candidates(HOME_PORT_POINT)
        for attempt in range(1, self._recovery_attempts + 1):
            point = candidates[(attempt - 1) % len(candidates)]
            self._emit(
                "ui_action_precondition",
                action="click_home_port",
                fleet_id=fleet_id,
                attempt=attempt,
                point=point.label,
            )
            self._game_input.click(point)
            self._emit("ui_click_sent", point=point.label, attempt=attempt)
            self._sleep(self._home_port_recovery_settle_seconds)
            try:
                evidence = self._gate.require(UiState.HOME_PORT)
                self._emit("home_port_recovery_confirmed", fleet_id=fleet_id, attempt=attempt)
                return self._home_port_evidence(evidence)
            except (UiStateGateError, ContinuousUiDriverError) as exc:
                self._emit("home_port_recovery_retry", fleet_id=fleet_id, attempt=attempt, error=str(exc))
        raise ContinuousUiDriverError(
            f"Home Port was not confirmed after {self._recovery_attempts} bounded attempts"
        )

    def capture_home_reference(self) -> None:
        """Capture the Home Port baseline only after state confirmation."""
        self._gate.require(UiState.HOME_PORT)
        capture = getattr(self._gate, "capture_home_reference", None)
        if callable(capture):
            capture()

    def _require_fleet(self, fleet_id: int) -> PoiFleetMissionState:
        matches = [state for state in self._fleet_states_fn() if state.fleet_id == fleet_id]
        if len(matches) != 1:
            raise ContinuousUiDriverError(f"Expected exactly one fleet {fleet_id} in the Poi snapshot")
        return matches[0]

    def _verify_checkpoint(self, step_number: int, phase: str, state_name: str) -> None:
        if state_name == "EXPEDITION_ACTIVE":
            raise ContinuousUiDriverError(
                "EXPEDITION_ACTIVE is verified from Poi Redux state, not the UI gate"
            )
        try:
            expected = UiState(state_name)
        except ValueError as exc:
            raise ContinuousUiDriverError(f"Unknown UI checkpoint state: {state_name}") from exc
        evidence = self._gate.require(expected)
        self._emit(
            "dispatch_checkpoint_confirmed",
            step=step_number,
            phase=phase,
            expected_state=state_name,
            evidence=evidence,
        )

    def _verify_supply_precondition(self, fleet_id: int, before: PoiFleetMissionState) -> None:
        pre_start = self._require_fleet(fleet_id)
        if not _is_idle(pre_start) or pre_start.ship_ids != before.ship_ids:
            raise ContinuousUiDriverError("Fleet changed before expedition start; refusing to click")
        expected_supply_ids = {ship_id for ship_id in pre_start.ship_ids if ship_id > 0}
        actual_supply_ids = {item.ship_id for item in pre_start.supply}
        if not expected_supply_ids or actual_supply_ids != expected_supply_ids:
            raise ContinuousUiDriverError("Fleet supply snapshot is missing or incomplete; refusing to click")
        unsupplied = [item.ship_id for item in pre_start.supply if not item.is_fully_supplied]
        if unsupplied:
            raise ContinuousUiDriverError(
                f"Fleet {fleet_id} has {len(unsupplied)} ship(s) not fully supplied; refusing to click"
            )
        self._emit(
            "fleet_supply_verified",
            fleet_id=fleet_id,
            fully_supplied_ship_count=len(pre_start.supply),
        )

    def _home_port_evidence(self, evidence: str) -> UiStateEvidence:
        return UiStateEvidence(
            state=UiState.HOME_PORT.value,
            consecutive_states=(UiState.HOME_PORT.value, UiState.HOME_PORT.value),
            evidence=evidence,
        )

    def _emit(self, event: str, **fields: object) -> None:
        if self._event_sink is not None:
            self._event_sink(event, fields)
        else:
            self.events.append((event, dict(fields)))


def _is_idle(state: PoiFleetMissionState) -> bool:
    return state.mission == (0, 0, 0, 0)
