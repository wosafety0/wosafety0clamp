"""Fail-closed live dispatch of any expedition this account has unlocked.

This is the honest live path for the generalized expedition UI driver. It
drives the real game through the CDP webview and proves every screen
transition from on-screen pixels; it does NOT depend on the Poi MCP bridge,
which truncates large accounts' info.ships and therefore can never satisfy
the supervisor's full-read gate for this account.

The tool reads live fleet state over CDP (window.getStore) rather than the
bridge, so it works regardless of the bridge's truncation.

Default: run prepare_dispatch only -- navigate to the dispatch screen and
prove every checkpoint up to, but not including, the irreversible start
click. Pass --commit to also click EXPEDITION_START and then re-read the
live fleet state over CDP to confirm the expedition actually started.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

# Reuse the exact production state-verification logic.
from one_expedition_ui_loop import (  # noqa: E402
    HOME_REFERENCE_SETTLE_SECONDS,
    UiState,
    _recover_home_port,
    _require_ui_state,
)

from kancollebot.adapters.poi.cdp_fleet_state import (  # noqa: E402
    read_poi_fleet_states,
)
from kancollebot.adapters.poi.cdp_websocket import (  # noqa: E402
    connect_game_webview,
    connect_poi_session,
)
from kancollebot.adapters.poi.cdp_input import RecordedPoint  # noqa: E402
from kancollebot.adapters.poi.continuous_expedition_ui import (  # noqa: E402
    CdpContinuousExpeditionUiDriver,
)
from kancollebot.calibration import (  # noqa: E402
    DEFAULT_CATALOG,
    DEFAULT_DETAIL_PATH,
    DEFAULT_MARKS_PATH,
    DEFAULT_POSITION_INDEX_PATH,
    ExpeditionListSession,
    decode_png,
    load_detail_index,
    load_marks,
    load_position_index,
    scan_page,
)
from kancollebot.expeditions.continuous_dispatch_controller import (  # noqa: E402
    UiStateEvidence,
)
from kancollebot.expeditions.dispatch_registry import (  # noqa: E402
    DispatchIdentity,
    DispatchRegistryError,
    SqliteDispatchRegistry,
    new_dispatch_id,
)
from kancollebot.observability.sink import (  # noqa: E402
    correlation_capability,
)
from kancollebot.runtime_config import RUNTIME_CONFIG  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fleet-id", type=int, required=True,
                        help="Fleet to dispatch (must be idle and actually unlocked on this account)")
    parser.add_argument("--mission-id", type=int, required=True,
                        help="Master-data mission id (e.g. 1 for 01 練習航海, 131 for D1)")
    parser.add_argument("--commit", action="store_true",
                        help="Also click EXPEDITION_START (real dispatch)")
    parser.add_argument("--authorize-ui-actions", action="store_true", required=True,
                        help="Explicit confirmation that UI clicks are permitted")
    parser.add_argument("--log-path", type=Path,
                        default=Path(tempfile.gettempdir()) / "live_dispatch.jsonl",
                        help="Where to append the per-event JSONL log")
    parser.add_argument("--database", type=Path,
                        default=RUNTIME_CONFIG.dispatch_database,
                        help="Durable dispatch database (the same file the production "
                             "supervisor uses); the dispatch identity of this attempt is "
                             "recorded here so the settlement half can attribute the result")
    return parser


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    log_path = args.log_path.expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def event_sink(event: str, payload: dict) -> None:
        line = json.dumps({"event": event, **payload}, ensure_ascii=False)
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line + "\n")

    # This tool logs to a plain append-only function, which cannot bind a
    # correlation scope at all. Recording that once, as a fact, is what makes
    # every ``dispatch_id: null`` in this file explainable: without it a reader
    # cannot tell "no dispatch" from "this log can never carry one".
    event_sink("run_started", {
        "fleet_id": args.fleet_id, "mission_id": args.mission_id,
        "commit": args.commit,
        **correlation_capability(event_sink).as_fields(),
    })

    # The game webview drives input/screenshots/visual checks; the Poi session
    # reads the live fleet state over CDP (the MCP bridge truncates info.ships).
    with connect_game_webview(timeout=5.0) as game_session, \
            connect_poi_session(timeout=5.0) as poi_session:
        # Pre-flight: read the live fleet state over CDP (not the MCP bridge).
        states = tuple(read_poi_fleet_states(poi_session.transport))
        target = next((s for s in states if s.fleet_id == args.fleet_id), None)
        if target is None:
            event_sink("PREFLIGHT_FAIL", {"reason": f"fleet {args.fleet_id} not in snapshot"})
            return 2
        if target.mission != (0, 0, 0, 0):
            event_sink("PREFLIGHT_FAIL", {
                "reason": f"fleet {args.fleet_id} is not idle",
                "mission": list(target.mission),
            })
            return 2
        ships = [x for x in target.ship_ids if x > 0]
        event_sink("PREFLIGHT_OK", {
            "fleet_id": args.fleet_id, "ship_count": len(ships),
            "ship_ids": ships,
        })

        # P0-2: this entry sends a real dispatch, so it must leave a durable
        # record behind - otherwise the production settlement controller can
        # never attribute the result (the intent row is deleted the moment Poi
        # confirms). The write happens at the same boundary the supervisor
        # uses: pending before the irreversible click, confirmed when Poi
        # reports the mission active.
        try:
            registry = SqliteDispatchRegistry(
                args.database, task_kind=RUNTIME_CONFIG.dispatch_policy_task_kind
            )
            open_ids = registry.unsettled_dispatch_ids(args.fleet_id)
        except DispatchRegistryError as exc:
            event_sink("DISPATCH_BLOCKED_REGISTRY_UNREADABLE", {
                "database": str(args.database),
                "error_type": type(exc).__name__,
                "reason": "a dispatch whose identity cannot be recorded must not be sent",
                "ui_clicks_sent": 0,
            })
            return 3
        if open_ids:
            # Not a style preference: with two unsettled instances of the same
            # fleet, a returned result could not be attributed to either.
            event_sink("DISPATCH_BLOCKED_PRIOR_UNSETTLED", {
                "fleet_id": args.fleet_id,
                "unsettled_dispatch_ids": list(open_ids),
                "reason": "an earlier dispatch of this fleet is not settled; a second "
                          "attempt could not be told apart from it afterwards",
                "ui_clicks_sent": 0,
            })
            return 3
        dispatch_id = new_dispatch_id()

        def verify_state(expected: str, *, visual_attempts: int = 6) -> UiStateEvidence:
            # The driver passes its own sample count for the reward screens; the
            # dispatch checkpoints keep this tool's six-sample default.
            state = UiState(expected)
            evidence = _require_ui_state(
                game_session, state, visual_attempts=visual_attempts
            )
            return UiStateEvidence(
                state=state.value,
                consecutive_states=(state.value, state.value),
                evidence=evidence,
            )

        def capture_frame() -> str:
            game_session.transport.send(
                "Input.dispatchMouseEvent",
                {"type": "mouseMoved", "x": 1195, "y": 715},
            )
            time.sleep(HOME_REFERENCE_SETTLE_SECONDS)
            return game_session.input.capture_screenshot()

        scratch = Path(tempfile.gettempdir()) / "live_dispatch_scan.png"

        def scan_list():
            payload = capture_frame()
            scratch.write_bytes(base64.b64decode(payload))
            return scan_page(decode_png(scratch))

        list_session = ExpeditionListSession(
            scan=scan_list,
            click=lambda point, label: game_session.input.click(
                RecordedPoint(point[0], point[1], label)
            ),
            index=load_position_index(DEFAULT_POSITION_INDEX_PATH),
            settle_seconds=0.55,
        )

        driver = CdpContinuousExpeditionUiDriver(
            game_input=game_session.input,
            verify_state=verify_state,
            event_sink=event_sink,
            details=load_detail_index(DEFAULT_DETAIL_PATH),
            marks=load_marks(DEFAULT_MARKS_PATH),
            capture=capture_frame,
            locate_row=list_session.locate_any_tab,
            supported_fleets=(2, 3, 4),
            neutralize_pointer=lambda: game_session.transport.send(
                "Input.dispatchMouseEvent",
                {"type": "mouseMoved", "x": 1195, "y": 715},
            ),
        )

        try:
            expedition_id = DEFAULT_CATALOG.number_of_mission_id(args.mission_id)
            # Always begin from a verified Home Port, whatever screen the game
            # is currently on (a prior run may have left it at the
            # fleet-selection screen). dispatch_expedition re-navigates from
            # HOME_PORT internally, so the entry screen must be HOME_PORT.
            _recover_home_port(game_session)
            event_sink("PREPARE_START", {"expedition_id": expedition_id})
            if args.commit:
                # dispatch_expedition runs the full prepare + the irreversible
                # start click; do NOT call prepare_dispatch separately first.
                # The identity row goes down before that click.
                registry.record_intent(DispatchIdentity(
                    dispatch_id=dispatch_id,
                    fleet_id=args.fleet_id,
                    mission_id=args.mission_id,
                    ship_ids=tuple(ships),
                    intent_persisted_at=datetime.now(timezone.utc),
                    status="pending",
                    task_kind=RUNTIME_CONFIG.dispatch_policy_task_kind,
                    source="live_dispatch_tool",
                    detail="prepare checkpoints passed; start click about to be sent",
                ))
                event_sink("DISPATCH_INTENT_PERSISTED", {
                    "fleet_id": args.fleet_id, "mission_id": args.mission_id,
                    "dispatch_id": dispatch_id,
                })
                driver.dispatch_expedition(fleet_id=args.fleet_id, mission_id=args.mission_id)
                event_sink("DISPATCH_INPUT_SENT", {
                    "fleet_id": args.fleet_id, "mission_id": args.mission_id,
                    "dispatch_id": dispatch_id,
                })
            else:
                driver.prepare_dispatch(fleet_id=args.fleet_id, mission_id=args.mission_id)
                event_sink("PREPARE_OK", {
                    "fleet_id": args.fleet_id, "mission_id": args.mission_id,
                    "expedition_id": expedition_id,
                })
                driver.return_to_home_port(
                    fleet_id=args.fleet_id,
                    expected_current_state=f"FLEET_{args.fleet_id}_SELECTED",
                )
                event_sink("PREPARE_ONLY", {
                    "note": "all checkpoints passed; --commit not set, no start click; returned home",
                })
                return 0
        except Exception as exc:
            event_sink("DRIVER_BLOCKED", {
                "error_type": type(exc).__name__, "error": str(exc),
                "dispatch_id": dispatch_id if args.commit else None,
            })
            if args.commit:
                # The start click may or may not have landed. Recording it as
                # anything other than unknown would be a guess.
                try:
                    registry.record_unknown(
                        dispatch_id,
                        reason=f"dispatch driver failed with {type(exc).__name__}; outcome unproven",
                    )
                    event_sink("DISPATCH_OUTCOME_UNKNOWN", {"dispatch_id": dispatch_id})
                except DispatchRegistryError:
                    pass
            # Best-effort: leave the game at a verified Home Port rather than
            # stranded on a mid-dispatch screen. Failure here is non-fatal.
            try:
                _recover_home_port(game_session)
            except Exception:
                pass
            return 2

        # Post-flight: confirm the live fleet state over CDP.
        deadline = time.monotonic() + 12.0
        confirmed = False
        while time.monotonic() < deadline:
            states = tuple(read_poi_fleet_states(poi_session.transport))
            now = next((s for s in states if s.fleet_id == args.fleet_id), None)
            if now is not None and now.mission_status == 1 and now.mission_id == args.mission_id:
                # This is the one boundary where the dispatch becomes a durable
                # fact: the server is running this mission for this fleet.
                registry.record_confirmed(
                    dispatch_id,
                    confirmed_at=datetime.now(timezone.utc),
                    final_time_ms=now.final_time_ms,
                )
                event_sink("DISPATCH_CONFIRMED", {
                    "fleet_id": args.fleet_id,
                    "dispatch_id": dispatch_id,
                    "mission": list(now.mission),
                    "final_time_ms": now.final_time_ms,
                    "ship_ids": [x for x in now.ship_ids if x > 0],
                })
                confirmed = True
                break
            time.sleep(1.0)
        if not confirmed:
            try:
                registry.record_unknown(
                    dispatch_id,
                    reason="start click sent but CDP state never showed the mission active",
                )
            except DispatchRegistryError:
                pass
            event_sink("DISPATCH_UNCONFIRMED", {
                "dispatch_id": dispatch_id,
                "note": "start click sent but CDP state did not show the expedition active; "
                        "the game refused it (e.g. supply) or it is still settling",
            })
            return 2
        # Leave the game at a verified Home Port.
        try:
            _recover_home_port(game_session)
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
