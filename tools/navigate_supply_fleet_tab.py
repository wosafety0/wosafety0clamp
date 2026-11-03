"""Navigate between the supply-page Fleet 2 and Fleet 3 tabs with visual gates."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from kancollebot.adapters.poi.cdp_websocket import connect_game_webview
from kancollebot.expeditions.continuous_scheduler import UiOperationArbiter
from kancollebot.adapters.poi.ui_state_gate import UiState
from kancollebot.adapters.poi.visual_target import locate_supply_fleet_tab
from one_expedition_ui_loop import _require_ui_state

LOG_PATH = ROOT / "logs" / "supply_fleet_tab_navigation.jsonl"


def log(event: str, **fields: object) -> None:
    payload = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    line = json.dumps(payload, ensure_ascii=False)
    print(line, flush=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(line + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-fleet", type=int, choices=(2, 3), required=True)
    parser.add_argument("--to-fleet", type=int, choices=(2, 3), required=True)
    args = parser.parse_args()
    if args.from_fleet == args.to_fleet:
        parser.error("from-fleet and to-fleet must differ")
    before = UiState[f"SUPPLY_FLEET_{args.from_fleet}_SELECTED"]
    after = UiState[f"SUPPLY_FLEET_{args.to_fleet}_SELECTED"]
    try:
        with UiOperationArbiter().operation("supply-fleet-tab-navigation"), connect_game_webview(timeout=3.0) as session:
            _require_ui_state(session, before)
            frame = session.input.capture_screenshot()
            target = locate_supply_fleet_tab(frame, fleet_index=args.to_fleet)
            session.input.click_visual_target(target, screenshot_base64=frame, button="left")
            log("supply_fleet_tab_click_sent", from_fleet=args.from_fleet,
                to_fleet=args.to_fleet, click_count=1, label=target.label,
                bounds=list(target.bounds), source_frame_sha256=target.source_frame_sha256)
            time.sleep(0.5)
            _require_ui_state(session, after)
            log("supply_fleet_tab_transition_verified", from_fleet=args.from_fleet,
                to_fleet=args.to_fleet, evidence="two_consecutive_parent_fingerprint_frames")
            return 0
    except Exception as exc:
        log("STOPPED_FAIL_CLOSED", from_fleet=args.from_fleet, to_fleet=args.to_fleet,
            error_type=type(exc).__name__, error=str(exc), automatic_retry=False)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
