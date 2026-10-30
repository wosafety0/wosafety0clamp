"""Run one explicitly authorized UI-mediated expedition return/collect/re-dispatch loop.

This controller is deliberately pinned to fleet 2 and mission 3. It reads only a
fixed Poi Redux fleet snapshot and uses single-click CDP mouse input against the
visible game UI. It never constructs or sends game-server requests. This script
is a live-action prototype; its return-result confirmation point still requires
validation on the actual return screen before unattended use.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
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

FLEET_ID = 2
MISSION_ID = 3
HOME_PORT = RecordedPoint(65, 85, "home-port")
SORTIE = RecordedPoint(280, 425, "sortie")
EXPEDITION_MODE = RecordedPoint(1020, 450, "expedition-mode")
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


def _print_event(event: str, state: PoiFleetMissionState | None = None, **extra: object) -> None:
    payload: dict[str, object] = {"event": event, **extra}
    if state is not None:
        payload["fleet_id"] = state.fleet_id
        payload["mission"] = list(state.mission)
        payload["ship_count"] = len([ship_id for ship_id in state.ship_ids if ship_id > 0])
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _require_ui_state(game_transport, expected: UiState) -> None:
    """Require explicit accessible text evidence before a sensitive UI click."""
    result = game_transport.send("Accessibility.getFullAXTree", {})
    nodes = result.get("nodes") if isinstance(result, dict) else None
    require_state(nodes, expected)
    _print_event("ui_state_confirmed", state=expected.value)


def _wait_for_return(reader_transport, expected_start_ms: int, poll_seconds: float, timeout_seconds: float) -> PoiFleetMissionState:
    deadline = time.monotonic() + timeout_seconds
    last_log = 0.0
    while time.monotonic() < deadline:
        state = _fleet(read_poi_fleet_states(reader_transport))
        if state.mission_status == 2 and state.mission_id == MISSION_ID and state.final_time_ms == expected_start_ms:
            _print_event("same_mission_returned", state)
            return state
        if state.mission_status != 1 or state.mission_id != MISSION_ID or state.final_time_ms != expected_start_ms:
            raise LoopError(f"Fleet 2 left the expected active mission unexpectedly: {state.mission}")
        # Poi Redux can retain status=1 after the scheduled return timestamp until
        # the visible UI requests/refreshes the expedition result. Do not wait for
        # a status=2 transition that may require opening the result screen first.
        if int(time.time() * 1000) >= expected_start_ms:
            _print_event("return_time_reached_refresh_through_ui", state)
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


def _dispatch_again(reader_transport, game_input, expected_start_ms: int) -> PoiFleetMissionState:
    before = _fleet(read_poi_fleet_states(reader_transport))
    if not _idle(before):
        raise LoopError(f"Fleet 2 is not idle before re-dispatch: {before.mission}")
    # The result/bonus flow returns to the mother-port screen after the
    # verified visible Next controls; Home Port itself is hidden at that point.
    for point, settle in (
        (SORTIE, 1.0),
        (EXPEDITION_MODE, 1.0),
        (MISSION_ROW, 0.5),
        (MISSION_CONFIRM, 0.7),
        (FLEET_2_TAB, 0.4),
        (EXPEDITION_START, 1.5),
    ):
        game_input.click(point)
        _print_event("ui_click_sent", point=point.label)
        time.sleep(settle)
    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline:
        state = _fleet(read_poi_fleet_states(reader_transport))
        if state.mission_status == 1 and state.mission_id == MISSION_ID and state.final_time_ms > expected_start_ms:
            _print_event("redispatch_verified", state)
            return state
        if not _idle(state) and not (state.mission_status == 1 and state.mission_id == MISSION_ID):
            raise LoopError(f"Unexpected state after re-dispatch clicks: {state.mission}")
        time.sleep(1.0)
    raise LoopError("Re-dispatch was not verified from Poi Redux state; do not repeat clicks")


def run(args: argparse.Namespace) -> int:
    if not args.authorize_ui_actions:
        raise LoopError("Missing explicit --authorize-ui-actions flag")
    if args.expected_start_ms <= 0:
        raise LoopError("expected_start_ms must be positive")
    if not 1 <= args.poll_seconds <= 30:
        raise LoopError("poll_seconds must be between 1 and 30")
    if not 60 <= args.timeout_seconds <= 3600:
        raise LoopError("timeout_seconds must be between 60 and 3600")
    with connect_poi_session(timeout=3.0) as state_session, connect_game_webview(timeout=3.0) as game_session:
        state = _fleet(read_poi_fleet_states(state_session.transport))
        if state.mission_status != 1 or state.mission_id != MISSION_ID or state.final_time_ms != args.expected_start_ms:
            raise LoopError(f"Startup state does not match the authorized mission: {state.mission}")
        other_active = [item.fleet_id for item in read_poi_fleet_states(state_session.transport) if item.fleet_id != FLEET_ID and not _idle(item)]
        if other_active:
            raise LoopError(f"Other fleets are not idle: {other_active}")
        _print_event("attached_to_expected_active_mission", state, target=game_session.target.title)
        returned = _wait_for_return(
            state_session.transport,
            args.expected_start_ms,
            args.poll_seconds,
            args.timeout_seconds,
        )
        # The return notification was observed on the home-port screen. The
        # top-left Home Port navigation control is hidden there, so do not click
        # its stale coordinate. Open the returned-expedition result from Sortie.
        still_returned = _fleet(read_poi_fleet_states(state_session.transport))
        if still_returned.mission != returned.mission:
            raise LoopError("Mission state changed before opening its result; stopping")
        _require_ui_state(game_session.transport, UiState.HOME_PORT)
        game_session.input.click(SORTIE)
        _print_event("ui_click_sent", point=SORTIE.label)
        time.sleep(1.5)
        result_deadline = time.monotonic() + 15.0
        while time.monotonic() < result_deadline:
            result_state = _fleet(read_poi_fleet_states(state_session.transport))
            if result_state.mission_status == 2 and result_state.mission_id == MISSION_ID and result_state.final_time_ms == args.expected_start_ms:
                _print_event("return_result_visible_in_state", result_state)
                break
            if result_state.mission_status != 1 or result_state.mission_id != MISSION_ID or result_state.final_time_ms != args.expected_start_ms:
                raise LoopError("Unexpected state while waiting for return result: " + str(result_state.mission))
            time.sleep(1.0)
        else:
            raise LoopError("Sortie UI did not refresh the return result; no confirmation click sent")
        _require_ui_state(game_session.transport, UiState.RETURN_RESULT)
        # Single attempt only. If the point is wrong, idle-state verification fails
        # and the controller will not retry a potentially ambiguous click.
        game_session.input.click(RETURN_CONFIRM)
        _print_event("ui_click_sent", point=RETURN_CONFIRM.label)
        time.sleep(1.5)
        _wait_for_idle(state_session.transport)
        _require_ui_state(game_session.transport, UiState.BONUS_ITEMS)
        # The observed result flow has a second visible Next page (bonus/items)
        # after the fleet state becomes idle. Advance once to restore mother port.
        game_session.input.click(RETURN_CONFIRM)
        _print_event("ui_click_sent", point="bonus-next")
        time.sleep(1.0)
        _require_ui_state(game_session.transport, UiState.HOME_PORT)
        _dispatch_again(state_session.transport, game_session.input, args.expected_start_ms)
        _print_event("ONE_EXPEDITION_RETURN_COLLECT_REDISPATCH_VERIFIED")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorize-ui-actions", action="store_true", required=True,
                        help="Explicitly authorize this one fixed live UI loop")
    parser.add_argument("--expected-start-ms", type=int, required=True,
                        help="Exact current fleet-2 mission-3 timestamp; prevents acting on a different mission")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    args = parser.parse_args()
    try:
        return run(args)
    except (LoopError, CdpInputError, CdpFleetStateError, UiStateGateError, OSError, TimeoutError, ValueError) as exc:
        _print_event("STOPPED_FAIL_CLOSED", error_type=type(exc).__name__, error=str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
