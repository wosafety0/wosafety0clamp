"""Walk to the expedition list and capture what it looks like while a fleet is out.

Why this exists: on 2026-11-07 a dispatch ended with 66 seconds (34 probe
frames) where *every* named state scored UNKNOWN, and the run only recovered
because the observation loop clicked the 母港 badge. No frame from that window
was kept (production probes classify in memory), so the page could not be
identified afterwards - and a template cannot be recorded from a page nobody
has a picture of.

This tool does exactly the two navigation clicks that open the list, takes a
frame at each step, and ranks every frame against the stored templates. It
never dispatches a fleet, never settles a reward and never guesses a point:
the two navigation coordinates are the same recorded ones the refresh driver
uses, and the 母港 click goes through the visual locator.

Everything it writes is a diagnostic PNG plus a JSON summary.
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

from kancollebot.adapters.poi.cdp_websocket import connect_game_webview  # noqa: E402
from kancollebot.adapters.poi.continuous_expedition_ui import (  # noqa: E402
    EXPEDITION_MODE,
    SORTIE,
)
from kancollebot.adapters.poi.home_port_button_vision import (  # noqa: E402
    HomePortButtonLocator,
    HomePortButtonRefused,
    HomePortVisionThresholds,
)
from kancollebot.adapters.poi.ui_operation_lock import poi_ui_operation  # noqa: E402
from kancollebot.adapters.poi.visual_fingerprint import (  # noqa: E402
    rank_screenshot_fingerprints,
)

DEFAULT_DOCUMENT = (
    ROOT / "src" / "kancollebot" / "adapters" / "poi" / "ooi_1200x720_fingerprints.json"
)
NEUTRAL_POINTER = (1195, 715)


def _ranking(payload: str, document: dict) -> tuple[str, list[tuple[str, float]]]:
    ranking = rank_screenshot_fingerprints(payload, document)
    ordered = sorted(ranking.scores.items(), key=lambda item: item[1], reverse=True)
    return ranking.state, ordered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "logs")
    parser.add_argument("--document", type=Path, default=DEFAULT_DOCUMENT)
    parser.add_argument("--settle-seconds", type=float, default=3.0)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="capture the current screen only; send no clicks",
    )
    args = parser.parse_args(argv)

    document = json.loads(args.document.read_text(encoding="utf-8"))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, object] = {"stamp": stamp, "steps": []}

    def capture(session, label: str, *, from_surface: bool = True) -> str:
        session.transport.send(
            "Input.dispatchMouseEvent",
            {"type": "mouseMoved", "x": NEUTRAL_POINTER[0], "y": NEUTRAL_POINTER[1]},
        )
        time.sleep(0.2)
        payload = session.input.capture_screenshot(from_surface=from_surface)
        layer = "default" if from_surface else "composited"
        state, ordered = _ranking(payload, document)
        path = args.out_dir / f"page_{stamp}_{label}_{layer}.png"
        import base64

        path.write_bytes(base64.b64decode(payload))
        record = {
            "label": label,
            "layer": layer,
            "observed": state,
            "top": [[name, round(score, 4)] for name, score in ordered[:4]],
            "file": str(path),
        }
        summary["steps"].append(record)  # type: ignore[union-attr]
        top = ", ".join(f"{n}/{s:.4f}" for n, s in ordered[:4])
        print(f"  [{label}/{layer}] observed={state}  top: {top}")
        return path

    with poi_ui_operation("walk-expedition-in-progress-page"):
        with connect_game_webview() as session:
            print("=== 1. 当前画面 ===")
            capture(session, "00_before")
            if args.dry_run:
                print("--dry-run: 未发送任何点击")
            else:
                print("=== 2. 点击 出撃 ===")
                session.input.click(SORTIE)
                time.sleep(args.settle_seconds)
                capture(session, "01_sortie_menu")

                print("=== 3. 点击 遠征（关键时刻）===")
                session.input.click(EXPEDITION_MODE)
                time.sleep(args.settle_seconds)
                capture(session, "02_expedition_list_in_progress")

                print("=== 4. 视觉定位 母港 并返回 ===")
                locator = HomePortButtonLocator(
                    capture=lambda: session.input.capture_screenshot(from_surface=True),
                    thresholds=HomePortVisionThresholds(),
                )
                try:
                    detection = locator.locate()
                except HomePortButtonRefused as exc:
                    print(f"  母港按钮拒绝识别: {exc.reason} —— 不再点击")
                    summary["home_port_refusal"] = exc.reason
                else:
                    print(f"  母港按钮 center={detection.point.x:.1f},{detection.point.y:.1f} "
                          f"confidence={detection.confidence:.4f}")
                    session.input.click(detection.point)
                    time.sleep(args.settle_seconds)
                    capture(session, "03_home_port")

    report = args.out_dir / f"page_walk_{stamp}.json"
    report.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n证据: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
