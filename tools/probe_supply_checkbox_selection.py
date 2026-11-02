"""One-shot, selection-only probe for the first Fleet 2 ship checkbox.

The tool requires the live Fleet 2 supply-tab gate, locates one checkbox in a
fixed region, and sends exactly one reversible selection click. It never clicks
any supply/repair/dispatch/reward control and never writes screenshot bytes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from kancollebot.adapters.poi.cdp_websocket import connect_game_webview
from kancollebot.adapters.poi.ui_state_gate import UiState
from kancollebot.adapters.poi.visual_fingerprint import (
    compute_grid_fingerprint,
    fingerprint_similarity,
)
from kancollebot.adapters.poi.visual_target import locate_square_checkbox
from one_expedition_ui_loop import LoopError, _require_ui_state


LOG_PATH = ROOT / "logs" / "supply_checkbox_probe.jsonl"
CHECKBOX_CROP = (164, 238, 26, 26)
CHECKBOX_GRID = (13, 13)


def log_event(event: str, **fields: object) -> None:
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    line = json.dumps(payload, ensure_ascii=False)
    print(line, flush=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(line + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--authorize-selection-only",
        action="store_true",
        required=True,
        help="Authorize one reversible click on Fleet 2's first ship checkbox only",
    )
    args = parser.parse_args()

    try:
        with connect_game_webview(timeout=3.0) as session:
            _require_ui_state(session, UiState.SUPPLY_FLEET_2_SELECTED)
            log_event("precondition_confirmed", expected="SUPPLY_FLEET_2_SELECTED")

            before_image = session.input.capture_screenshot()
            before_fingerprint = compute_grid_fingerprint(
                before_image, crop=CHECKBOX_CROP, grid=CHECKBOX_GRID
            )
            target = locate_square_checkbox(before_image)
            log_event(
                "visual_target_located",
                label=target.label,
                bounds=list(target.bounds),
                center=[target.x, target.y],
                region=list(target.region),
                locator=target.locator,
                source_frame_sha256=target.source_frame_sha256,
            )

            session.input.click_visual_target(
                target, screenshot_base64=before_image, button="left"
            )
            log_event("selection_click_sent", target=target.label, click_count=1)
            time.sleep(0.5)

            _require_ui_state(session, UiState.SUPPLY_FLEET_2_SELECTED)
            _require_ui_state(session, UiState.SUPPLY_FLEET_2_SHIP_1_CHECKED)
            log_event("checked_child_gate_confirmed", ui_state=UiState.SUPPLY_FLEET_2_SHIP_1_CHECKED.value)
            try:
                _require_ui_state(session, UiState.SUPPLY_FLEET_2_SHIP_1_UNCHECKED)
            except LoopError:
                log_event("negative_unchecked_expectation_rejected", expected=UiState.SUPPLY_FLEET_2_SHIP_1_UNCHECKED.value)
            else:
                raise RuntimeError("checked checkbox incorrectly passed the unchecked-state gate")

            after_image = session.input.capture_screenshot()
            after_fingerprint = compute_grid_fingerprint(
                after_image, crop=CHECKBOX_CROP, grid=CHECKBOX_GRID
            )
            similarity = fingerprint_similarity(before_fingerprint, after_fingerprint)
            log_event(
                "post_click_observation",
                parent_state="SUPPLY_FLEET_2_SELECTED",
                checkbox_crop_similarity=round(similarity, 6),
                screenshot_saved=False,
                additional_clicks_sent=0,
            )
            if similarity >= 0.995:
                log_event(
                    "STOPPED_FAIL_CLOSED",
                    reason="checkbox visual region did not change enough to prove selection",
                    no_retry=True,
                )
                return 2

            log_event(
                "selection_change_observed",
                checkbox_crop_similarity=round(similarity, 6),
                screenshot_saved=False,
                restore_pending=True,
            )
            return 0
    except Exception as exc:
        log_event(
            "STOPPED_FAIL_CLOSED",
            error_type=type(exc).__name__,
            error=str(exc),
            no_automatic_retry=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
