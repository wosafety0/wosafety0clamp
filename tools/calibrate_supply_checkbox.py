"""Calibrate one Fleet 2 ship checkbox using only its reversible selection UI.

Actions: capture-unchecked, probe, capture-checked, restore.
No supply, repair, dispatch, reward, or game API action is exposed.
"""
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
from kancollebot.adapters.poi.ui_state_gate import UiState
from kancollebot.adapters.poi.visual_fingerprint import (
    average_fingerprints,
    compute_grid_fingerprint,
    fingerprint_similarity,
)
from kancollebot.adapters.poi.visual_target import (
    checkbox_mark_pixel_count,
    locate_square_checkbox,
)
from one_expedition_ui_loop import _require_ui_state

TARGETS = {
    1: ((150, 225, 55, 50), (166, 240, 22, 23)),
    2: ((150, 302, 55, 50), (166, 317, 22, 23)),
    3: ((150, 379, 55, 50), (166, 394, 22, 23)),
    4: ((150, 456, 55, 50), (166, 471, 22, 23)),
}
GRID = (13, 13)
MARK_THRESHOLD = 20
FINGERPRINT_PATH = ROOT / "src" / "kancollebot" / "adapters" / "poi" / "supply_checkbox_fingerprints.json"
LOG_PATH = ROOT / "logs" / "supply_checkbox_calibration.jsonl"


def log(event: str, **fields: object) -> None:
    payload = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    line = json.dumps(payload, ensure_ascii=False)
    print(line, flush=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(line + "\n")


def state_for(ship_index: int, checked: bool) -> UiState:
    suffix = "CHECKED" if checked else "UNCHECKED"
    return UiState[f"SUPPLY_FLEET_2_SHIP_{ship_index}_{suffix}"]


def locate(frame: str, ship_index: int):
    region, expected_bounds = TARGETS[ship_index]
    target = locate_square_checkbox(
        frame, region=region, label=f"fleet2-ship{ship_index}-checkbox"
    )
    if target.bounds != expected_bounds:
        raise RuntimeError(
            f"ship {ship_index} checkbox bounds drifted: {target.bounds}; expected {expected_bounds}"
        )
    return target


def capture_samples(session, ship_index: int, checked: bool) -> None:
    state_name = state_for(ship_index, checked).value
    document = json.loads(FINGERPRINT_PATH.read_text(encoding="utf-8"))
    states = document.setdefault("states", {})
    if state_name in states:
        raise RuntimeError(f"refusing to overwrite existing fingerprint state {state_name}")
    fingerprints = []
    crop = None
    for sample_index in range(3):
        _require_ui_state(session, UiState.SUPPLY_FLEET_2_SELECTED)
        frame = session.input.capture_screenshot()
        target = locate(frame, ship_index)
        marks = checkbox_mark_pixel_count(frame, target)
        if checked and marks < MARK_THRESHOLD:
            raise RuntimeError(f"ship {ship_index} checkbox is not visibly checked ({marks} mark pixels)")
        if not checked and marks >= MARK_THRESHOLD:
            raise RuntimeError(f"ship {ship_index} checkbox is not visibly unchecked ({marks} mark pixels)")
        x, y, _, _ = target.bounds
        crop = (x - 2, y - 2, 26, 26)
        fingerprints.append(compute_grid_fingerprint(frame, crop=crop, grid=GRID))
        log("fingerprint_sample_captured", ship_index=ship_index, state=state_name,
            sample=sample_index + 1, mark_pixels=marks, crop=list(crop),
            screenshot_saved=False)
        if sample_index < 2:
            time.sleep(0.35)
    states[state_name] = {
        "crop": list(crop),
        "grid": list(GRID),
        "samples": len(fingerprints),
        "fingerprint": list(average_fingerprints(fingerprints)),
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "screenshot_bytes_saved": False,
        "label_source": "live_checkbox_mark_gate",
    }
    temporary = FINGERPRINT_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(FINGERPRINT_PATH)
    log("fingerprint_state_saved", ship_index=ship_index, state=state_name,
        samples=len(fingerprints), crop=list(crop), screenshot_saved=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ship-index", type=int, choices=(1, 2, 3, 4), required=True)
    parser.add_argument("--action", choices=("capture-unchecked", "probe", "capture-checked", "restore"), required=True)
    args = parser.parse_args()
    ship = args.ship_index
    try:
        with connect_game_webview(timeout=3.0) as session:
            _require_ui_state(session, UiState.SUPPLY_FLEET_2_SELECTED)
            if args.action == "capture-unchecked":
                capture_samples(session, ship, checked=False)
                return 0
            if args.action == "capture-checked":
                capture_samples(session, ship, checked=True)
                return 0

            if args.action == "probe":
                frame = session.input.capture_screenshot()
                target = locate(frame, ship)
                before_marks = checkbox_mark_pixel_count(frame, target)
                if before_marks >= MARK_THRESHOLD:
                    raise RuntimeError("checkbox already appears checked; probe refused")
                before_fp = compute_grid_fingerprint(
                    frame, crop=(target.bounds[0] - 2, target.bounds[1] - 2, 26, 26), grid=GRID
                )
                session.input.click_visual_target(target, screenshot_base64=frame, button="left")
                log("selection_click_sent", ship_index=ship, click_count=1,
                    target=target.label, bounds=list(target.bounds))
                time.sleep(0.5)
                _require_ui_state(session, UiState.SUPPLY_FLEET_2_SELECTED)
                after = session.input.capture_screenshot()
                after_target = locate(after, ship)
                after_marks = checkbox_mark_pixel_count(after, after_target)
                after_fp = compute_grid_fingerprint(
                    after, crop=(after_target.bounds[0] - 2, after_target.bounds[1] - 2, 26, 26), grid=GRID
                )
                similarity = fingerprint_similarity(before_fp, after_fp)
                if after_marks < MARK_THRESHOLD or similarity >= 0.995:
                    raise RuntimeError(
                        f"selection not proven (mark_pixels={after_marks}, similarity={similarity:.6f}); no retry"
                    )
                log("selection_change_verified_by_mark", ship_index=ship,
                    before_mark_pixels=before_marks, after_mark_pixels=after_marks,
                    similarity=round(similarity, 6), restore_pending=True,
                    screenshot_saved=False, additional_clicks_sent=0)
                return 0

            checked_state = state_for(ship, True)
            unchecked_state = state_for(ship, False)
            _require_ui_state(session, checked_state)
            frame = session.input.capture_screenshot()
            target = locate(frame, ship)
            marks = checkbox_mark_pixel_count(frame, target)
            if marks < MARK_THRESHOLD:
                raise RuntimeError(f"restore refused: checkbox mark not visible ({marks} pixels)")
            session.input.click_visual_target(target, screenshot_base64=frame, button="left")
            log("restore_click_sent", ship_index=ship, click_count=1,
                target=target.label, mark_pixels_before=marks)
            time.sleep(0.5)
            _require_ui_state(session, unchecked_state)
            after = session.input.capture_screenshot()
            after_target = locate(after, ship)
            after_marks = checkbox_mark_pixel_count(after, after_target)
            if after_marks >= MARK_THRESHOLD:
                raise RuntimeError(f"restore verification failed ({after_marks} mark pixels)")
            log("restore_verified", ship_index=ship, mark_pixels_after=after_marks,
                screenshot_saved=False, additional_clicks_sent=0)
            return 0
    except Exception as exc:
        log("STOPPED_FAIL_CLOSED", ship_index=ship, action=args.action,
            error_type=type(exc).__name__, error=str(exc), no_automatic_retry=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
