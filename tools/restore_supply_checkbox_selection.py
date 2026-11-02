"""Restore the first Fleet 2 ship checkbox after a selection-only probe.

This tool sends at most one click, and only when the live checkbox contains a
visible check mark. It never interacts with supply, repair, dispatch, or reward
controls and never writes screenshot bytes.
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
from kancollebot.adapters.poi.visual_target import (
    checkbox_mark_pixel_count,
    locate_square_checkbox,
)
from one_expedition_ui_loop import _require_ui_state


LOG_PATH = ROOT / "logs" / "supply_checkbox_probe.jsonl"
CHECKBOX_CROP = (164, 238, 26, 26)
CHECKBOX_GRID = (13, 13)
EXPECTED_BOUNDS = (166, 240, 22, 23)
MIN_CHECK_MARK_PIXELS = 20


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
        "--authorize-restore",
        action="store_true",
        required=True,
        help="Authorize one reversible click to restore the first ship checkbox to unchecked",
    )
    parser.parse_args()

    try:
        with connect_game_webview(timeout=3.0) as session:
            _require_ui_state(session, UiState.SUPPLY_FLEET_2_SELECTED)
            _require_ui_state(session, UiState.SUPPLY_FLEET_2_SHIP_1_CHECKED)
            before_image = session.input.capture_screenshot()
            target = locate_square_checkbox(before_image)
            if target.bounds != EXPECTED_BOUNDS:
                raise RuntimeError(f"checkbox bounds changed: {target.bounds}")
            before_mark_pixels = checkbox_mark_pixel_count(before_image, target)
            if before_mark_pixels < MIN_CHECK_MARK_PIXELS:
                raise RuntimeError(
                    f"checkbox is not positively checked (bright mark pixels={before_mark_pixels}); refusing click"
                )
            before_fingerprint = compute_grid_fingerprint(
                before_image, crop=CHECKBOX_CROP, grid=CHECKBOX_GRID
            )
            log_event(
                "restore_precondition_confirmed",
                parent_state="SUPPLY_FLEET_2_SELECTED",
                bounds=list(target.bounds),
                check_mark_pixels=before_mark_pixels,
            )

            session.input.click_visual_target(
                target, screenshot_base64=before_image, button="left"
            )
            log_event("restore_click_sent", target=target.label, click_count=1)
            time.sleep(0.5)

            _require_ui_state(session, UiState.SUPPLY_FLEET_2_SHIP_1_UNCHECKED)
            after_image = session.input.capture_screenshot()
            after_target = locate_square_checkbox(after_image)
            after_mark_pixels = checkbox_mark_pixel_count(after_image, after_target)
            after_fingerprint = compute_grid_fingerprint(
                after_image, crop=CHECKBOX_CROP, grid=CHECKBOX_GRID
            )
            similarity = fingerprint_similarity(before_fingerprint, after_fingerprint)
            if after_mark_pixels >= MIN_CHECK_MARK_PIXELS or similarity >= 0.995:
                log_event(
                    "STOPPED_FAIL_CLOSED",
                    reason="checkbox did not return to a positively unchecked visual state",
                    after_mark_pixels=after_mark_pixels,
                    before_after_similarity=round(similarity, 6),
                    no_automatic_retry=True,
                )
                return 2
            log_event(
                "restore_verified",
                after_mark_pixels=after_mark_pixels,
                before_after_similarity=round(similarity, 6),
                screenshot_saved=False,
                additional_clicks_sent=0,
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
