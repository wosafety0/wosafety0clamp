"""Run one supervised Fleet 3 -> mission 3 UI dispatch transaction.

The production entry composes the fresh Poi monitor, durable scheduler and
dispatch-intent coordinator, ContinuousDispatchController, single-tick
ContinuousExpeditionSupervisor, and a visible CDP UI driver. It exits only after
Poi confirms the new mission tuple and two-frame Home Port evidence. It does not
wait for return, collect rewards, resupply, or run unattended. All game input is
visible UI input; no game-server requests or page-state mutation are performed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from kancollebot.adapters.poi.cdp_fleet_state import (  # noqa: E402
    CdpFleetStateError,
    PoiFleetMissionState,
    read_poi_fleet_states,
)
from kancollebot.adapters.poi.cdp_input import CdpInputError, RecordedPoint  # noqa: E402
from kancollebot.adapters.poi.cdp_websocket import (  # noqa: E402
    connect_game_webview,
    connect_poi_session,
)
from kancollebot.adapters.poi.ui_operation_lock import poi_ui_operation  # noqa: E402
from kancollebot.adapters.poi.ui_state_gate import (  # noqa: E402
    UiState,
    UiStateGateError,
    require_state,
)
from kancollebot.adapters.poi.visual_state_gate import (
    VisualStateError,
    classify_screenshot_png,
    classify_template_scores,
    home_reference_similarity,
)  # noqa: E402
from kancollebot.adapters.poi.visual_fingerprint import (  # noqa: E402
    VisualFingerprintError,
    classify_screenshot_fingerprints,
    classify_supply_checkbox_state,
)


FLEET_ID = 2
MISSION_ID = 3
HOME_PORT = RecordedPoint(50, 50, "home-port")
SORTIE = RecordedPoint(280, 425, "sortie")
EXPEDITION_MODE = RecordedPoint(1000, 360, "expedition-mode")
MISSION_ROW = RecordedPoint(350, 350, "mission-3-security-task")
# State-dependent: this is the mission-confirm control on MISSION_SELECTED, but overlaps the expedition-start control on fleet selection.
# The pre-click MISSION_SELECTED checkpoint is mandatory.
MISSION_CONFIRM = RecordedPoint(1030, 670, "mission-confirm")
FLEET_2_TAB = RecordedPoint(590, 170, "fleet-2-tab")
FLEET_3_TAB = RecordedPoint(630, 170, "fleet-3-tab")
EXPEDITION_START = RecordedPoint(920, 670, "expedition-start")
RETURN_CONFIRM = RecordedPoint(1140, 670, "returned-expedition-confirm")

# Every click needs fresh evidence for its pre- and post-state. Pre-dispatch
# checkpoints use two consecutive in-memory Ooi fingerprints. Return/bonus
# checkpoints remain a separate production-readiness gate.
DISPATCH_STEPS = (
    (SORTIE, 1.0, "HOME_PORT", "SORTIE_MENU"),
    (EXPEDITION_MODE, 1.0, "SORTIE_MENU", "EXPEDITION_LIST"),
    (MISSION_ROW, 0.5, "EXPEDITION_LIST", "MISSION_SELECTED"),
    (MISSION_CONFIRM, 0.7, "MISSION_SELECTED", "FLEET_2_SELECTED"),
    (EXPEDITION_START, 1.5, "FLEET_2_SELECTED", "EXPEDITION_ACTIVE"),
)


class LoopError(RuntimeError):
    """The observed state or UI sequence no longer matches the fixed loop."""


def _fleet(states: tuple[PoiFleetMissionState, ...], fleet_id: int = FLEET_ID) -> PoiFleetMissionState:
    matches = [state for state in states if state.fleet_id == fleet_id]
    if len(matches) != 1:
        raise LoopError(f"Expected exactly one fleet {fleet_id} in the Poi snapshot")
    return matches[0]


def _idle(state: PoiFleetMissionState) -> bool:
    return state.mission == (0, 0, 0, 0)


LOG_PATH: Path | None = None
RUN_ID = "uninitialized"
HOME_REFERENCE_BASE64: str | None = None
_VISUAL_DOCUMENT_CACHE: dict[tuple[str, int, int], object] = {}


def _read_visual_document(path: Path) -> object:
    # Read each unchanged fingerprint document only once per process.
    try:
        metadata = path.stat()
    except OSError:
        return json.loads(path.read_text(encoding="utf-8"))

    resolved_path = str(path.resolve())
    cache_key = (resolved_path, metadata.st_mtime_ns, metadata.st_size)
    if cache_key in _VISUAL_DOCUMENT_CACHE:
        return _VISUAL_DOCUMENT_CACHE[cache_key]

    document = json.loads(path.read_text(encoding="utf-8"))
    for stale_key in tuple(_VISUAL_DOCUMENT_CACHE):
        if stale_key[0] == resolved_path:
            del _VISUAL_DOCUMENT_CACHE[stale_key]
    if len(_VISUAL_DOCUMENT_CACHE) >= 4:
        _VISUAL_DOCUMENT_CACHE.pop(next(iter(_VISUAL_DOCUMENT_CACHE)))
    _VISUAL_DOCUMENT_CACHE[cache_key] = document
    return document


def _print_event(event: str, state: PoiFleetMissionState | None = None, **extra: object) -> None:
    payload: dict[str, object] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": RUN_ID,
        "event": event,
        **extra,
    }
    if state is not None:
        payload["fleet_id"] = state.fleet_id
        payload["mission"] = list(state.mission)
        payload["ship_count"] = len([ship_id for ship_id in state.ship_ids if ship_id > 0])
    line = json.dumps(payload, ensure_ascii=False)
    print(line, flush=True)
    if LOG_PATH is not None:
        with LOG_PATH.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line + "\n")


def _require_ui_state(
    game_session, expected: UiState, *, visual_attempts: int = 3
) -> str:
    """Require accessible labels or two consecutive in-memory visual matches."""
    if (
        isinstance(visual_attempts, bool)
        or not isinstance(visual_attempts, int)
        or not 2 <= visual_attempts <= 12
    ):
        raise ValueError("visual_attempts must be an integer from 2 through 12")
    result = game_session.transport.send("Accessibility.getFullAXTree", {})
    nodes = result.get("nodes") if isinstance(result, dict) else None
    try:
        require_state(nodes, expected)
        evidence = "accessibility"
        _print_event("ui_state_confirmed", ui_state=expected.value, evidence=evidence)
        return evidence
    except (UiStateGateError, ValueError) as accessible_error:
        fingerprint_path = Path(
            os.environ.get(
                "KANCOLLEBOT_FINGERPRINTS_PATH",
                ROOT / "src" / "kancollebot" / "adapters" / "poi" / "ooi_1200x720_fingerprints.json",
            )
        )
        template_dir = os.environ.get("KANCOLLEBOT_TEMPLATE_DIR")
        try:
            document = None
            if fingerprint_path.is_file():
                document = _read_visual_document(fingerprint_path)
            fingerprint_states = document.get("states", {}) if isinstance(document, dict) else {}
            checkbox_states = {
                state for state in UiState
                if state.value.startswith(("SUPPLY_FLEET_2_SHIP_", "SUPPLY_FLEET_3_SHIP_"))
                and state.value.endswith(("_CHECKED", "_UNCHECKED"))
            }
            if expected in checkbox_states:
                if not isinstance(document, dict):
                    raise LoopError("Parent Ooi fingerprint document is unavailable") from accessible_error
                checkbox_path = Path(
                    os.environ.get(
                        "KANCOLLEBOT_SUPPLY_CHECKBOX_FINGERPRINTS_PATH",
                        ROOT / "src" / "kancollebot" / "adapters" / "poi" / "supply_checkbox_fingerprints.json",
                    )
                )
                if not checkbox_path.is_file():
                    raise LoopError(
                        f"Supply checkbox fingerprint file is missing: {checkbox_path}"
                    ) from accessible_error
                checkbox_document = _read_visual_document(checkbox_path)
                checkbox_descriptors = checkbox_document.get("states", {})
                if expected.value not in checkbox_descriptors:
                    raise LoopError(
                        f"Supply checkbox fingerprint is missing for {expected.value}"
                    ) from accessible_error
                state_parts = expected.value.split("_")
                fleet_index = int(state_parts[2])
                ship_index = int(state_parts[4])

                consecutive_matches = 0
                for attempt in range(visual_attempts):
                    game_session.transport.send(
                        "Input.dispatchMouseEvent",
                        {"type": "mouseMoved", "x": 1195, "y": 715},
                    )
                    time.sleep(0.15)
                    payload = game_session.input.capture_screenshot()
                    observed, scores = classify_supply_checkbox_state(
                        payload, document, checkbox_document,
                        fleet_index=fleet_index, ship_index=ship_index
                    )
                    _print_event(
                        "ui_supply_checkbox_state_check",
                        attempt=attempt + 1,
                        expected=expected.value,
                        observed=observed,
                        scores={key: round(value, 4) for key, value in scores.items()},
                        screenshot_written_to_disk=False,
                    )
                    if observed == expected.value:
                        consecutive_matches += 1
                        if consecutive_matches >= 2:
                            evidence = "two_consecutive_in_memory_parent_and_checkbox_fingerprints"
                            _print_event(
                                "ui_state_confirmed",
                                ui_state=expected.value,
                                evidence=evidence,
                            )
                            return evidence
                    elif observed != "UNKNOWN":
                        raise LoopError(
                            f"Expected {expected.value}; visual classifier observed {observed}"
                        ) from accessible_error
                    else:
                        consecutive_matches = 0
                    if attempt < visual_attempts - 1:
                        time.sleep(0.25)
                raise LoopError(
                    f"Expected {expected.value}; no stable parent+checkbox fingerprint match after {visual_attempts} attempts"
                ) from accessible_error

            if expected.value in fingerprint_states:
                consecutive_matches = 0
                for attempt in range(visual_attempts):
                    game_session.transport.send(
                        "Input.dispatchMouseEvent",
                        {"type": "mouseMoved", "x": 1195, "y": 715},
                    )
                    time.sleep(0.15)
                    payload = game_session.input.capture_screenshot()
                    observed, scores = classify_screenshot_fingerprints(payload, document)
                    _print_event(
                        "ui_fingerprint_state_check",
                        attempt=attempt + 1,
                        expected=expected.value,
                        observed=observed,
                        scores={key: round(value, 4) for key, value in scores.items()},
                        screenshot_written_to_disk=False,
                    )
                    if observed == expected.value:
                        consecutive_matches += 1
                        if consecutive_matches >= 2:
                            evidence = "two_consecutive_in_memory_ooi_fingerprints"
                            _print_event(
                                "ui_state_confirmed",
                                ui_state=expected.value,
                                evidence=evidence,
                            )
                            return evidence
                    elif observed != "UNKNOWN":
                        raise LoopError(
                            f"Expected {expected.value}; Ooi fingerprint classified {observed}"
                        ) from accessible_error
                    else:
                        consecutive_matches = 0
                    if attempt < visual_attempts - 1:
                        time.sleep(0.25)
                if not template_dir:
                    raise LoopError(
                        f"Expected {expected.value}; no stable fingerprint match was obtained after {visual_attempts} attempts"
                    ) from accessible_error

            if not template_dir:
                raise LoopError(
                    f"Expected {expected.value}; no fingerprint for this state and no KANCOLLEBOT_TEMPLATE_DIR configured"
                ) from accessible_error
            template_path = Path(template_dir)
            if not template_path.is_dir():
                raise LoopError(f"Visual template directory does not exist: {template_path}") from accessible_error

            consecutive_matches = 0
            for attempt in range(visual_attempts):
                game_session.transport.send(
                    "Input.dispatchMouseEvent",
                    {"type": "mouseMoved", "x": 1195, "y": 715},
                )
                time.sleep(0.15)
                payload = game_session.input.capture_screenshot()
                visual_state, scores = classify_screenshot_png(payload, template_path)
                if HOME_REFERENCE_BASE64:
                    scores["home_reference_similarity"] = home_reference_similarity(
                        payload, HOME_REFERENCE_BASE64
                    )
                    visual_state = classify_template_scores(scores)
                _print_event(
                    "ui_template_state_check",
                    attempt=attempt + 1,
                    expected=expected.value,
                    observed=visual_state,
                    scores={key: round(value, 4) for key, value in scores.items()},
                    screenshot_written_to_disk=False,
                )
                if visual_state == expected.value:
                    consecutive_matches += 1
                    if consecutive_matches >= 2:
                        evidence = "two_consecutive_in_memory_template_matches"
                        _print_event(
                            "ui_state_confirmed",
                            ui_state=expected.value,
                            evidence=evidence,
                        )
                        return evidence
                elif visual_state != "UNKNOWN":
                    raise LoopError(
                        f"Expected {expected.value}; visual template classifier returned {visual_state}"
                    ) from accessible_error
                else:
                    consecutive_matches = 0
                if attempt < visual_attempts - 1:
                    time.sleep(0.25)
            raise LoopError(
                f"Expected {expected.value}; no stable template match was obtained after {visual_attempts} attempts"
            ) from accessible_error
        except (VisualFingerprintError, VisualStateError, OSError, ValueError) as visual_error:
            raise LoopError(
                f"Accessible state unavailable ({accessible_error}); visual fallback failed ({visual_error})"
            ) from visual_error


def _capture_home_reference(game_session) -> None:
    """Capture a transient Home Port baseline only after fingerprint confirmation."""
    global HOME_REFERENCE_BASE64
    _require_ui_state(game_session, UiState.HOME_PORT)
    game_session.transport.send(
        "Input.dispatchMouseEvent",
        {"type": "mouseMoved", "x": 1195, "y": 715},
    )
    time.sleep(0.15)
    HOME_REFERENCE_BASE64 = game_session.input.capture_screenshot()
    _print_event(
        "home_reference_captured",
        evidence="in_memory_ooi_fingerprint",
        screenshot_written_to_disk=False,
    )


def _recover_home_port(game_session, attempts: int = 3, force_click: bool = False) -> None:
    """Use bounded top-left navigation retries, requiring state evidence after each click."""
    if not force_click:
        try:
            _require_ui_state(game_session, UiState.HOME_PORT)
            _print_event("home_port_recovery_confirmed", attempt=0)
            return
        except (UiStateGateError, LoopError) as exc:
            _print_event("home_port_state_not_confirmed", attempt=0, error=str(exc))
    for attempt in range(1, attempts + 1):
        _print_event("ui_action_precondition", action="click_home_port", attempt=attempt,
                     force_click=force_click)
        game_session.input.click(HOME_PORT)
        _print_event("ui_click_sent", point=HOME_PORT.label, attempt=attempt)
        time.sleep(1.0)
        _print_event("ui_action_wait_completed", point=HOME_PORT.label, attempt=attempt,
                     wait_seconds=1.0)
        try:
            _require_ui_state(game_session, UiState.HOME_PORT)
            _print_event("home_port_recovery_confirmed", attempt=attempt)
            return
        except (UiStateGateError, LoopError) as exc:
            _print_event("home_port_recovery_retry", attempt=attempt, error=str(exc))
    raise LoopError(f"Home Port was not confirmed after {attempts} bounded attempts")


def _wait_for_return(
    reader_transport,
    expected_start_ms: int,
    poll_seconds: float,
    timeout_seconds: float,
    grace_delay_seconds: float = 10.0,
) -> PoiFleetMissionState:
    deadline = time.monotonic() + timeout_seconds
    last_log = 0.0
    grace_applied = False
    while time.monotonic() < deadline:
        state = _fleet(read_poi_fleet_states(reader_transport))
        if state.mission_status == 2 and state.mission_id == MISSION_ID and state.final_time_ms == expected_start_ms:
            _print_event("same_mission_returned", state)
            return state
        if state.mission_status != 1 or state.mission_id != MISSION_ID or state.final_time_ms != expected_start_ms:
            raise LoopError(f"Fleet 2 left the expected active mission unexpectedly: {state.mission}")
        if int(time.time() * 1000) >= expected_start_ms:
            if not grace_applied:
                _print_event("completion_grace_delay_started", state, grace_delay_seconds=grace_delay_seconds)
                time.sleep(grace_delay_seconds)
                grace_applied = True
                continue
            _print_event("completion_grace_elapsed_refresh_required", state)
            return state
        now = time.monotonic()
        if now - last_log >= 30:
            _print_event("waiting_for_return", state)
            last_log = now
        time.sleep(poll_seconds)
    raise LoopError("Timed out waiting for the same mission to return; no input was sent after timeout")

def _wait_for_idle(reader_transport, poll_seconds: float = 1.0, timeout_seconds: float = 10.0) -> PoiFleetMissionState:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = _fleet(read_poi_fleet_states(reader_transport))
        if _idle(state):
            _print_event("fleet_idle_confirmed_after_bonus_advance", state)
            return state
        if state.mission_status != 2 or state.mission_id != MISSION_ID:
            raise LoopError(f"Unexpected fleet 2 state after reward confirmation: {state.mission}")
        time.sleep(poll_seconds)
    raise LoopError("Fleet 2 did not become idle after bonus advance; refusing further UI actions")


def _advance_returned_expedition_screens(game_session) -> None:
    """Advance the verified result and bonus screens using bounded UI actions."""
    _print_event("ui_action_precondition", action="collect_expedition_reward",
                 fleet_id=FLEET_ID, mission_id=MISSION_ID, expected_state="RETURN_RESULT")
    game_session.input.click(RETURN_CONFIRM)
    _print_event("ui_click_sent", point=RETURN_CONFIRM.label)
    time.sleep(1.5)
    _print_event("ui_action_wait_completed", point=RETURN_CONFIRM.label, wait_seconds=1.5)

    _require_ui_state(game_session, UiState.BONUS_ITEMS)
    _print_event("ui_action_precondition", action="advance_bonus_items", expected_state="BONUS_ITEMS")
    game_session.input.click(RETURN_CONFIRM)
    _print_event("ui_click_sent", point="bonus-next")
    time.sleep(1.0)
    _print_event("ui_action_wait_completed", point="bonus-next", wait_seconds=1.0)


def _verify_returned_expedition_ready(
    reader_transport, expected_final_time_ms: int
) -> PoiFleetMissionState:
    final_state = _fleet(read_poi_fleet_states(reader_transport))
    if not _idle(final_state):
        raise LoopError(f"Fleet 2 did not return to idle after collection: {final_state.mission}")
    _print_event("ONE_EXPEDITION_LOOP_VERIFIED_READY", final_state,
                 dispatch_final_time_ms=expected_final_time_ms)
    return final_state


def _collect_returned_expedition_reward(
    game_session, reader_transport, expected_final_time_ms: int
) -> PoiFleetMissionState:
    """Compatibility wrapper for the complete reward flow."""
    _advance_returned_expedition_screens(game_session)
    _wait_for_idle(reader_transport)
    _recover_home_port(game_session)
    return _verify_returned_expedition_ready(reader_transport, expected_final_time_ms)


def build_checkpoint_verifier(reader_transport, game_session, expected_start_ms: int):
    """Build the per-step verifier used by the one-shot dispatch sequence."""

    def verify(step: int, phase: str, expected_state: str) -> None:
        if expected_state == "EXPEDITION_ACTIVE":
            state = _fleet(read_poi_fleet_states(reader_transport))
            if (
                state.mission_status != 1
                or state.mission_id != MISSION_ID
                or state.final_time_ms <= expected_start_ms
            ):
                raise LoopError(
                    f"Expected the same newly started expedition; observed {state.mission}"
                )
            _print_event(
                "dispatch_checkpoint_confirmed",
                step=step,
                phase=phase,
                expected_state=expected_state,
                evidence="poi_redux_same_mission_new_final_time",
            )
            return

        try:
            expected = UiState(expected_state)
        except ValueError as exc:
            raise LoopError(f"Unknown UI checkpoint state: {expected_state}") from exc
        if expected is UiState.UNKNOWN:
            raise LoopError("UNKNOWN is not an acceptable dispatch checkpoint")
        evidence = _require_ui_state(game_session, expected)
        _print_event(
            "dispatch_checkpoint_confirmed",
            step=step,
            phase=phase,
            expected_state=expected_state,
            evidence=evidence,
        )

    return verify


def _dispatch(
    reader_transport,
    game_input,
    expected_start_ms: int,
    *,
    checkpoint_verifier: Callable[[int, str, str], None] | None = None,
) -> PoiFleetMissionState:
    """Dispatch only when a verifier can confirm every UI transition.

    The callback receives (step_number, phase, expected_state). A failed check
    raises and prevents every subsequent click. Pre-dispatch Ooi states require
    two consecutive high-confidence in-memory fingerprints. Production remains
    disabled until RETURN_RESULT and BONUS_ITEMS are independently verified.
    """
    if checkpoint_verifier is None:
        _print_event(
            "dispatch_blocked",
            reason="stepwise_ui_checkpoint_verifier_unavailable",
            clicks_sent=0,
        )
        raise LoopError(
            "Dispatch blocked before any click: a verified per-step UI checkpoint "
            "provider is not available for all five dispatch transitions"
        )

    before = _fleet(read_poi_fleet_states(reader_transport))
    if not _idle(before):
        raise LoopError(f"Fleet 2 is not idle before dispatch: {before.mission}")

    for step, (point, settle, expected_before, expected_after) in enumerate(
        DISPATCH_STEPS, start=1
    ):
        checkpoint_verifier(step, "before", expected_before)
        if point == EXPEDITION_START:
            # Recheck the target immediately before the irreversible start click.
            # This Redux read is not freshness proof; the independent visual
            # checkpoint remains mandatory.
            pre_start = _fleet(read_poi_fleet_states(reader_transport))
            if not _idle(pre_start) or pre_start.ship_ids != before.ship_ids:
                raise LoopError("Fleet 2 changed before expedition start; refusing to click")
            expected_supply_ids = {ship_id for ship_id in pre_start.ship_ids if ship_id > 0}
            actual_supply_ids = {item.ship_id for item in pre_start.supply}
            if not expected_supply_ids or actual_supply_ids != expected_supply_ids:
                raise LoopError("Fleet 2 supply snapshot is missing or incomplete; refusing to click")
            unsupplied = [item.ship_id for item in pre_start.supply if not item.is_fully_supplied]
            if unsupplied:
                raise LoopError(
                    f"Fleet 2 has {len(unsupplied)} ship(s) not fully supplied; refusing to click"
                )
            _print_event("fleet_supply_verified", pre_start, fully_supplied_ship_count=len(pre_start.supply))
        _print_event(
            "ui_action_precondition",
            action="dispatch_menu_click",
            step=step,
            point=point.label,
            expected_ui_state=expected_before,
            fleet_idle=True,
        )
        game_input.click(point)
        _print_event("ui_click_sent", point=point.label, step=step)
        time.sleep(settle)
        _print_event(
            "ui_action_wait_completed", point=point.label, step=step,
            wait_seconds=settle,
        )
        if expected_after != "EXPEDITION_ACTIVE":
            checkpoint_verifier(step, "after", expected_after)

    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline:
        state = _fleet(read_poi_fleet_states(reader_transport))
        if state.mission_status == 1 and state.mission_id == MISSION_ID and state.final_time_ms > expected_start_ms:
            _print_event("dispatch_verified", state)
            return state
        if not _idle(state) and not (state.mission_status == 1 and state.mission_id == MISSION_ID):
            raise LoopError(f"Unexpected state after re-dispatch clicks: {state.mission}")
        time.sleep(1.0)
    raise LoopError("Dispatch was not verified from Poi Redux state; do not repeat clicks")


def _return_home_port_after_dispatch(
    game_session, reader_transport, dispatched: PoiFleetMissionState
) -> PoiFleetMissionState:
    """Return from the expedition list to Home Port and revalidate the active mission."""
    _recover_home_port(game_session)
    observed = _fleet(read_poi_fleet_states(reader_transport))
    if (
        observed.mission_status != 1
        or observed.mission_id != dispatched.mission_id
        or observed.final_time_ms != dispatched.final_time_ms
        or observed.ship_ids != dispatched.ship_ids
    ):
        raise LoopError(
            "Fleet mission changed while returning to Home Port; "
            f"expected {dispatched.mission}, observed {observed.mission}"
        )
    _print_event(
        "dispatch_home_port_verified",
        observed,
        evidence="two_consecutive_in_memory_ooi_fingerprints_and_poi_active_tuple",
    )
    return observed


def _run_production_supervisor_once(args: argparse.Namespace) -> int:
    """Compose the durable monitor/controller/supervisor with the visible CDP driver."""
    from datetime import timedelta

    from kancollebot.adapters.poi.continuous_expedition_ui import (
        CdpContinuousExpeditionUiDriver,
    )
    from kancollebot.expeditions.continuous_dispatch_controller import (
        ContinuousDispatchController,
        UiStateEvidence,
    )
    from kancollebot.expeditions.continuous_scheduler import (
        FleetPhase,
        UiOperationArbiter,
    )
    from kancollebot.expeditions.continuous_supervisor import (
        ContinuousExpeditionSupervisor,
    )
    from kancollebot.expeditions.dispatch_recovery import (
        DurableDispatchCoordinator,
        SqliteDispatchIntentStore,
    )
    from run_continuous_expedition_monitor import build_runtime

    runtime = None
    try:
        with connect_game_webview(timeout=3.0) as game_session:
            runtime = build_runtime(
                database_path=args.database,
                observer_script=args.observer_script,
                observer_timeout_seconds=args.observer_timeout_seconds,
                max_state_age_seconds=args.max_state_age_seconds,
                dispatch_fleet_ids=(3,),
                enable_ui_refresh=False,
            )
            _capture_home_reference(game_session)

            def verify_state(expected: str) -> UiStateEvidence:
                state = UiState(expected)
                evidence = _require_ui_state(game_session, state)
                return UiStateEvidence(
                    state=state.value,
                    consecutive_states=(state.value, state.value),
                    evidence=evidence,
                )

            ui_driver = CdpContinuousExpeditionUiDriver(
                game_input=game_session.input,
                verify_state=verify_state,
                event_sink=runtime.monitor.event_sink,
                neutralize_pointer=lambda: game_session.transport.send(
                    "Input.dispatchMouseEvent",
                    {"type": "mouseMoved", "x": 1195, "y": 715},
                ),
            )
            dispatch_coordinator = DurableDispatchCoordinator(
                runtime.scheduler,
                SqliteDispatchIntentStore(args.database),
            )
            dispatch_controller = ContinuousDispatchController(
                scheduler=runtime.scheduler,
                dispatch_coordinator=dispatch_coordinator,
                state_reader=runtime.monitor.state_reader,
                ui_driver=ui_driver,
                ui_arbiter=UiOperationArbiter(),
                mission_requirements=runtime.monitor.mission_requirements,
                event_sink=runtime.monitor.event_sink,
                allow_ui_actions=True,
                max_state_age=timedelta(seconds=args.max_state_age_seconds),
            )
            supervisor = ContinuousExpeditionSupervisor(
                monitor=runtime.monitor,
                dispatch_controller=dispatch_controller,
            )

            _print_event(
                "production_supervisor_started",
                dispatch_fleets=[3],
                fixed_mission_id=3,
                read_only_fleets=[1, 2],
                deferred_fleets=[4],
                game_api_requests=False,
            )
            result = supervisor.tick()
            if result.paused_reason is not None or not result.dispatches:
                _print_event(
                    "PRODUCTION_SUPERVISOR_PAUSED",
                    reason=result.paused_reason or "fleet_3_not_ready",
                    dispatch_count=len(result.dispatches),
                    game_actions_sent=0,
                )
                return 2

            if len(result.dispatches) != 1:
                raise LoopError(
                    f"Expected exactly one Fleet 3 dispatch, observed {len(result.dispatches)}"
                )
            dispatch = result.dispatches[0]
            state = runtime.scheduler.fleet(3)
            if (
                dispatch.fleet_id != 3
                or dispatch.mission_id != 3
                or state.phase is not FleetPhase.EXPEDITION_ACTIVE
                or not state.home_port_verified
            ):
                raise LoopError(
                    "Supervisor returned without the exact Fleet 3 / mission 3 "
                    "active tuple and verified Home Port state"
                )
            _print_event(
                "PRODUCTION_UI_CONTROLLER_INTEGRATION_VERIFIED",
                fleet_id=3,
                mission_id=3,
                final_time_ms=dispatch.mission.final_time_ms,
                ship_ids=list(dispatch.mission.ship_ids),
                home_port_verified=True,
                evidence="fresh_poi_before_after_and_two_frame_home_port",
            )
            return 0
    except Exception as exc:
        try:
            _print_event(
                "STOPPED_FAIL_CLOSED",
                error_type=type(exc).__name__,
                error=str(exc),
                automatic_retry=False,
            )
        except OSError:
            pass
        if isinstance(exc, LoopError):
            raise
        raise LoopError(
            f"Production UI controller stopped fail-closed: {type(exc).__name__}: {exc}"
        ) from exc
    finally:
        if runtime is not None:
            runtime.provider.close(join_timeout_seconds=1.0)


def run(args: argparse.Namespace) -> int:
    if not args.authorize_ui_actions:
        raise LoopError("Missing explicit --authorize-ui-actions flag")
    return _run_production_supervisor_once(args)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--authorize-ui-actions",
        action="store_true",
        required=True,
        help="Explicitly authorize the fixed Fleet 3 -> mission 3 visible-UI dispatch",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=ROOT / "logs" / "one_expedition_production.sqlite3",
        help="Dedicated durable scheduler/dispatch database for this one-fleet entry",
    )
    parser.add_argument(
        "--observer-script",
        type=Path,
        default=ROOT / "tools" / "hand_sandbox" / "poi_port_response_observer_20261029.js",
    )
    parser.add_argument("--observer-timeout-seconds", type=float, default=130.0)
    parser.add_argument("--max-state-age-seconds", type=float, default=30.0)
    parser.add_argument(
        "--log-path",
        type=Path,
        default=ROOT / "logs" / "one_expedition_ui_loop.jsonl",
        help="The sole permitted local output file; defaults to logs/one_expedition_ui_loop.jsonl",
    )
    args = parser.parse_args()
    global LOG_PATH, RUN_ID, HOME_REFERENCE_BASE64
    LOG_PATH = args.log_path.expanduser().resolve()
    HOME_REFERENCE_BASE64 = None
    RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _print_event("run_started", log_path=str(LOG_PATH), output_policy="append-only-jsonl; no screenshots or other runtime files")
        return run(args)
    except (LoopError, CdpInputError, CdpFleetStateError, UiStateGateError, OSError, TimeoutError, ValueError) as exc:
        try:
            _print_event("STOPPED_FAIL_CLOSED", error_type=type(exc).__name__, error=str(exc))
        except OSError as log_error:
            LOG_PATH = None
            print(json.dumps({
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "run_id": RUN_ID,
                "event": "STOPPED_FAIL_CLOSED_LOG_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "log_error_type": type(log_error).__name__,
            }, ensure_ascii=False), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
