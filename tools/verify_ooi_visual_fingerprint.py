"""Verify the currently displayed Ooi screen against compact visual fingerprints.

This is a one-shot diagnostic. It moves the pointer to a neutral position to
clear hover overlays, but does not click, navigate, or save screenshots.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from kancollebot.adapters.poi.cdp_websocket import connect_game_webview  # noqa: E402
from kancollebot.adapters.poi.visual_fingerprint import (  # noqa: E402
    VisualFingerprintError,
    classify_screenshot_fingerprints,
)


DEFAULT_FINGERPRINTS = ROOT / "src" / "kancollebot" / "adapters" / "poi" / "ooi_1200x720_fingerprints.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", required=True)
    parser.add_argument("--fingerprints", type=Path, default=DEFAULT_FINGERPRINTS)
    parser.add_argument("--minimum-score", type=float, default=0.97)
    parser.add_argument("--minimum-margin", type=float, default=0.04)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        document = json.loads(args.fingerprints.read_text(encoding="utf-8"))
        with connect_game_webview() as session:
            session.transport.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 1195, "y": 715})
            time.sleep(0.15)
            payload = session.transport.send(
                "Page.captureScreenshot",
                {"format": "png", "fromSurface": True, "captureBeyondViewport": False},
            )["data"]
        observed, scores = classify_screenshot_fingerprints(
            payload,
            document,
            minimum_score=args.minimum_score,
            minimum_margin=args.minimum_margin,
        )
    except (OSError, json.JSONDecodeError, KeyError, VisualFingerprintError) as exc:
        print(json.dumps({"status": "FAILED_CLOSED", "error": str(exc)}, ensure_ascii=False))
        return 2
    matched = observed == args.expected
    print(json.dumps({
        "status": "PASS" if matched else "FAIL_CLOSED",
        "expected": args.expected,
        "observed": observed,
        "scores": {name: round(score, 5) for name, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)},
        "screenshot_saved": False,
    }, ensure_ascii=False))
    return 0 if matched else 2


if __name__ == "__main__":
    raise SystemExit(main())