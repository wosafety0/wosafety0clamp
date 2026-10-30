"""Run one UI-mediated fleet-2 mission-3 expedition from idle back to idle.

The controller reads the Poi Redux fleet snapshot and sends mouse input only to
the visible game UI. It never constructs or sends game-server requests. This is
an unverified live-action prototype: coordinate mapping and the return-result
screen gate must pass on the actual client before unattended use.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
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


FLEET_ID = 2
MISSION_ID = 3
HOME_PORT = RecordedPoint(50, 50, "home-port")
SORTIE = RecordedPoint(280, 425, "sortie")
EXPEDITION_MODE = RecordedPoint(1105, 179, "expedition-mode")
MISSION_ROW = RecordedPoint(500, 335, "mission-3-security-task")
MISSION_CONFIRM = RecordedPoint(1030, 670, "mission-confirm")
FLEET_2_TAB = RecordedPoint(300, 100, "fleet-2-tab")
EXPEDITION_START = RecordedPoint(920, 670, "expedition-start")
RETURN_CONFIRM = RecordedPoint(1140, 670, "returned-expedition-confirm")


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


def _require_ui_state(game_session, expected: UiState) -> None:
    """Require accessible evidence, then use in-memory templates as a guarded fallback."""
    result = game_session.transport.send("Accessibility.getFullAXTree", {})
    nodes = result.get("nodes") if isinstance(result, dict) else None
    try:
        require_state(nodes, expected)
        _print_event("ui_state_confirmed", ui_state=expected.value, evidence="accessibility")
        return
    except (UiStateGateError, ValueError) as accessible_error:
        template_dir = os.environ.get("KANCOLLEBOT_TEMPLATE_DIR")
        if not template_dir:
            raise accessible_error
        try:
            game_session.transport.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 1100, "y": 500})
            time.sleep(0.35)
            payload = game_session.input.capture_screenshot()
            visual_state, scores = classify_screenshot_png(payload, template_dir)
            if HOME_REFERENCE_BASE64:
                scores["home_reference_similarity"] = home_reference_similarity(payload, HOME_REFERENCE_BASE64)
                visual_state = classify_template_scores(scores)
        except VisualStateError as visual_error:
            raise LoopError(
                f"Accessible state unavailable ({accessible_error}); visual fallback failed ({visual_error})"
            ) from visual_error
        _print_event(
            "ui_visual_state_check",
            expected=expected.value,
            observed=visual_state,
            scores={key: round(value, 4) for key, value in scores.items()},
            screenshot_written_to_disk=False,
        )
        if visual_state != expected.value:
            raise LoopError(
                f"Expected {expected.value}; accessible labels were unavailable and visual state was {visual_state}"
            ) from accessible_error
        _print_event("ui_state_confirmed", ui_state=expected.value, evidence="in_memory_template_match")


def _capture_home_reference(game_session) -> None:
    """Capture a transient Home Port baseline in memory for later visual comparisons."""
    global HOME_REFERENCE_BASE64
    template_dir = os.environ.get("KANCOLLEBOT_TEMPLATE_DIR")
    if not template_dir:
        raise LoopError("KANCOLLEBOT_TEMPLATE_DIR is required for visual state verification")
    game_session.transport.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 1100, "y": 500})
    time.sleep(0.35)
    payload = game_session.input.capture_screenshot()
    _, scores = classify_screenshot_png(payload, template_dir)
    home_nav = scores.get("negative_home_other_menu.png", -1.0)
    competing = max(
        scores.get("home_expedition_menu.png", -1.0),
        scores.get("expedition_list_panel.png", -1.0),
        scores.get("expedition_dispatch_button.png", -1.0),
        scores.get("expedition_result_success.png", -1.0),
        scores.get("expedition_result_failure.png", -1.0),
        scores.get("return_notice.png", -1.0),
        scores.get("bonus_title.png", -1.0),
        scores.get("result_next_button.png", -1.0),
    )
    if home_nav < 0.90 or competing >= 0.85:
        raise LoopError(
            f"Cannot establish a Home Port reference safely: navigation_marker={home_nav:.3f}, competing_marker={competing:.3f}"
        )
    HOME_REFERENCE_BASE64 = payload
    _print_event(
        "home_reference_captured",
        home_navigation_marker=round(home_nav, 4),
        strongest_competing_marker=round(competing, 4),
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
            _print_event("reward_collection_confirmed_idle", state)
            return state
        if state.mission_status != 2 or state.mission_id != MISSION_ID:
            raise LoopError(f"Unexpected fleet 2 state after reward confirmation: {state.mission}")
        time.sleep(poll_seconds)
    raise LoopError("Reward collection was not confirmed; refusing to click again")


def _dispatch(reader_transport, game_input, expected_start_ms: int) -> PoiFleetMissionState:
    before = _fleet(read_poi_fleet_states(reader_transport))
    if not _idle(before):
        raise LoopError(f"Fleet 2 is not idle before dispatch: {before.mission}")
    # Navigate through the visible expedition menus from the idle Home Port state.
    for step, (point, settle) in enumerate((
        (SORTIE, 1.0),
        (EXPEDITION_MODE, 1.0),
        (MISSION_ROW, 0.5),
        (MISSION_CONFIRM, 0.7),
        (FLEET_2_TAB, 0.4),
        (EXPEDITION_START, 1.5),
    ), start=1):
        _print_event("ui_action_precondition", action="dispatch_menu_click",
                     step=step, point=point.label, fleet_idle=True)
        game_input.click(point)
        _print_event("ui_click_sent", point=point.label, step=step)
        time.sleep(settle)
        _print_event("ui_action_wait_completed", point=point.label, step=step,
                     wait_seconds=settle)
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


def run(args: argparse.Namespace) -> int:
    if not args.authorize_ui_actions:
        raise LoopError("Missing explicit --authorize-ui-actions flag")
    if not 1 <= args.poll_seconds <= 30:
        raise LoopError("poll_seconds must be between 1 and 30")
    if not 60 <= args.timeout_seconds <= 3600:
        raise LoopError("timeout_seconds must be between 60 and 3600")
    if not 0 <= args.grace_delay_seconds <= 300:
        raise LoopError("grace_delay_seconds must be between 0 and 300")
    with connect_poi_session(timeout=3.0) as state_session, connect_game_webview(timeout=3.0) as game_session:
        initial_states = read_poi_fleet_states(state_session.transport)
        initial = _fleet(initial_states)
        if not _idle(initial):
            raise LoopError(f"Fleet 2 must be idle before this one-shot run: {initial.mission}")
        _print_event("startup_idle_state_verified", initial)
        _capture_home_reference(game_session)
        _recover_home_port(game_session)
        baseline_ms = int(time.time() * 1000)
        dispatched = _dispatch(state_session.transport, game_session.input, baseline_ms)
        expected_final_time_ms = dispatched.final_time_ms
        _print_event("dispatch_confirmed_waiting_for_timer", dispatched,
                     expected_final_time_ms=expected_final_time_ms)
        _wait_for_return(
            state_session.transport,
            expected_final_time_ms,
            args.poll_seconds,
            args.timeout_seconds,
            args.grace_delay_seconds,
        )

        # Follow the requested UI refresh path: open Sortie, then navigate back
        # through the top-left Home Port control before checking the refreshed state.
        _print_event("ui_action_precondition", action="click_sortie", expected="expedition return refresh")
        game_session.input.click(SORTIE)
        _print_event("ui_click_sent", point=SORTIE.label)
        time.sleep(1.5)
        _print_event("ui_action_wait_completed", point=SORTIE.label, wait_seconds=1.5)
        _recover_home_port(game_session, force_click=True)
        refresh_deadline = time.monotonic() + 120.0
        last_refresh_log = 0.0
        result_state = None
        while time.monotonic() < refresh_deadline:
            observed = _fleet(read_poi_fleet_states(state_session.transport))
            if observed.mission_status == 2 and observed.mission_id == MISSION_ID and observed.final_time_ms == expected_final_time_ms:
                result_state = observed
                _print_event("expedition_completion_confirmed_after_home_port_refresh", observed)
                break
            if observed.mission_status != 1 or observed.mission_id != MISSION_ID or observed.final_time_ms != expected_final_time_ms:
                raise LoopError(f"Unexpected fleet 2 state after Home Port refresh: {observed.mission}")
            now = time.monotonic()
            if now - last_refresh_log >= 5.0:
                _print_event("waiting_for_refreshed_completion_state", observed, retry_interval_seconds=5)
                last_refresh_log = now
            time.sleep(5.0)
        if result_state is None:
            raise LoopError("Completion state was not confirmed within 120 seconds after Home Port refresh")

        _require_ui_state(game_session, UiState.RETURN_RESULT)
        _print_event("ui_action_precondition", action="collect_expedition_reward",
                     fleet_id=FLEET_ID, mission_id=MISSION_ID, expected_state="RETURN_RESULT")
        game_session.input.click(RETURN_CONFIRM)
        _print_event("ui_click_sent", point=RETURN_CONFIRM.label)
        time.sleep(1.5)
        _print_event("ui_action_wait_completed", point=RETURN_CONFIRM.label, wait_seconds=1.5)
        idle = _wait_for_idle(state_session.transport)
        _require_ui_state(game_session, UiState.BONUS_ITEMS)
        _print_event("ui_action_precondition", action="advance_bonus_items", expected_state="BONUS_ITEMS")
        game_session.input.click(RETURN_CONFIRM)
        _print_event("ui_click_sent", point="bonus-next")
        time.sleep(1.0)
        _print_event("ui_action_wait_completed", point="bonus-next", wait_seconds=1.0)
        _recover_home_port(game_session)
        final_state = _fleet(read_poi_fleet_states(state_session.transport))
        if not _idle(final_state):
            raise LoopError(f"Fleet 2 did not return to idle after collection: {final_state.mission}")
        _print_event("ONE_EXPEDITION_LOOP_VERIFIED_READY", final_state,
                     previous_idle_state=idle.mission_status == 0,
                     dispatch_final_time_ms=expected_final_time_ms)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorize-ui-actions", action="store_true", required=True,
                        help="Explicitly authorize this one fixed live UI loop")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--grace-delay-seconds", type=float, default=10.0,
                        help="Wait after the scheduled return time before refreshing through the UI")
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
