"""Capture a manually confirmed Ooi screen as a compact in-memory fingerprint.

The tool never writes screenshot bytes. It stores only low-resolution RGB cell
averages so visual checkpoints can be compared without local screenshot files.
Each invocation captures the screen currently displayed; it moves the pointer
to a neutral position to clear hover overlays, but does not navigate or click.
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
from kancollebot.adapters.poi.ui_operation_lock import poi_ui_operation  # noqa: E402
from kancollebot.adapters.poi.visual_fingerprint import (  # noqa: E402
    VisualFingerprintError,
    average_fingerprints,
    compute_grid_fingerprint,
)


DEFAULT_OUTPUT = ROOT / "src" / "kancollebot" / "adapters" / "poi" / "ooi_1200x720_fingerprints.json"
SUPPORTED_STATES = (
    "HOME_PORT",
    "SORTIE_MENU",
    "EXPEDITION_LIST",
    "SUPPLY_MENU",
    "SUPPLY_FLEET_1_SELECTED",
    "SUPPLY_FLEET_2_SELECTED",
    "SUPPLY_FLEET_3_SELECTED",
    "SUPPLY_FLEET_4_SELECTED",
    "SUPPLY_FLEET_2_SHIP_1_CHECKED",
    "SUPPLY_FLEET_2_SHIP_1_UNCHECKED",
    "MISSION_SELECTED",
    "FLEET_3_SELECTED",
    "FLEET_2_SELECTED",
    "RETURN_RESULT",
    "BONUS_ITEMS",
)


def _parse_tuple(value: str, *, label: str) -> tuple[int, ...]:
    try:
        parts = tuple(int(part.strip()) for part in value.split(","))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"{label} must be comma-separated integers") from exc
    if any(part < 0 for part in parts):
        raise argparse.ArgumentTypeError(f"{label} values must be non-negative")
    return parts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, choices=SUPPORTED_STATES)
    parser.add_argument("--crop", required=True, type=lambda value: _parse_tuple(value, label="crop"))
    parser.add_argument("--grid", default="24,16", type=lambda value: _parse_tuple(value, label="grid"))
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--replace", action="store_true")
    return parser


def capture_descriptor(state: str, crop: tuple[int, int, int, int], grid: tuple[int, int], samples: int) -> dict:
    if state not in SUPPORTED_STATES:
        raise VisualFingerprintError("state label is not supported")
    if len(crop) != 4 or len(grid) != 2:
        raise VisualFingerprintError("crop must have four values and grid must have two")
    if not 1 <= samples <= 5:
        raise VisualFingerprintError("samples must be between 1 and 5")

    fingerprints = []
    with poi_ui_operation("ooi-visual-fingerprint-capture"), connect_game_webview() as session:
        metrics = session.transport.send("Page.getLayoutMetrics", {})
        viewport = metrics.get("cssVisualViewport") or metrics.get("visualViewport") or {}
        width = int(viewport.get("clientWidth", viewport.get("width", 0)))
        height = int(viewport.get("clientHeight", viewport.get("height", 0)))
        if (width, height) != (1200, 720):
            raise VisualFingerprintError(f"expected 1200x720 Ooi viewport, observed {width}x{height}")
        session.transport.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 1195, "y": 715})
        time.sleep(0.15)
        for index in range(samples):
            payload = session.transport.send(
                "Page.captureScreenshot",
                {"format": "png", "fromSurface": True, "captureBeyondViewport": False},
            ).get("data")
            fingerprints.append(compute_grid_fingerprint(payload, crop=crop, grid=grid))
            if index + 1 < samples:
                time.sleep(0.25)

    return {
        "crop": list(crop),
        "grid": list(grid),
        "samples": samples,
        "fingerprint": list(average_fingerprints(fingerprints)),
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "screenshot_bytes_saved": False,
        "label_source": "manually_confirmed_current_screen",
    }


def update_output(path: Path, state: str, descriptor: dict, *, replace: bool = False) -> None:
    document = {"version": 1, "viewport": [1200, 720], "states": {}}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VisualFingerprintError("existing fingerprint file is unreadable") from exc
        if existing.get("version") != 1 or existing.get("viewport") != [1200, 720]:
            raise VisualFingerprintError("existing fingerprint file has an unsupported schema or viewport")
        states = existing.get("states")
        if not isinstance(states, dict):
            raise VisualFingerprintError("existing fingerprint states must be an object")
        document = existing
    if state in document["states"] and not replace:
        raise VisualFingerprintError(f"{state} already exists; pass --replace to update it")
    document["states"][state] = descriptor
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if len(args.crop) != 4 or len(args.grid) != 2:
            raise VisualFingerprintError("--crop requires x,y,width,height and --grid requires columns,rows")
        descriptor = capture_descriptor(args.state, args.crop, args.grid, args.samples)
        update_output(args.output, args.state, descriptor, replace=args.replace)
    except (VisualFingerprintError, OSError) as exc:
        print(json.dumps({"status": "FAILED_CLOSED", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({
        "status": "FINGERPRINT_SAVED",
        "state": args.state,
        "output": str(args.output),
        "descriptor_values": len(descriptor["fingerprint"]),
        "samples": descriptor["samples"],
        "screenshot_bytes_saved": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
