#!/usr/bin/env python3
"""Calibrate the expedition countdown reader from a real client frame.

The early-completion mechanic is decided by the countdown the *client* displays,
so the reader needs glyph descriptors taken from that same font. This tool is
the only sanctioned way to produce them; nothing here is synthetic and nothing
here clicks.

Two steps, both read-only:

1. ``capture`` - save one frame so the operator can measure the countdown box:

       PYTHONPATH=src python tools/capture_expedition_countdown.py capture \\
           --out logs/countdown_frame.png

2. ``record`` - given that frame, the region, and the text that was on screen,
   write ``expedition_countdown_glyphs.json``:

       PYTHONPATH=src python tools/capture_expedition_countdown.py record \\
           --frame logs/countdown_frame.png --roi 1010,120,96,24 \\
           --cell 19x24 --cells 5 --text 14:59 \\
           --out src/kancollebot/adapters/poi/expedition_countdown_glyphs.json

   The client renders ``HH:MM:SS`` in vertically layered cells, not one
   horizontal row. Pass the real layout with ``--cells 8 --cell-rows 8`` (a
   single vertical column) or any ``--cell-rows`` that divides ``--cells``
   exactly; the boxes are then tiled into that ``rows x cols`` grid.

The text must be read off the screen by a human the first time: that is the
ground truth the glyphs are bound to. Recording it from a timer would defeat the
whole point of reading the display.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from kancollebot.adapters.poi.visual_fingerprint import compute_grid_fingerprint  # noqa: E402


def _capture_once() -> str:
    from kancollebot.adapters.poi.cdp_websocket import connect_game_webview

    with connect_game_webview(timeout=5.0) as session:
        return session.input.capture_screenshot()


def cmd_capture(args: argparse.Namespace) -> int:
    payload = _capture_once()
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(base64.b64decode(payload))
    print(json.dumps({
        "event": "countdown_frame_captured",
        "path": str(target),
        "bytes": target.stat().st_size,
        "next_step": "measure the countdown box, then run `record` with --text",
    }, ensure_ascii=False))
    return 0


def _parse_roi(value: str) -> tuple[int, int, int, int]:
    parts = [int(item) for item in value.replace(" ", "").split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("--roi needs x,y,width,height")
    return parts[0], parts[1], parts[2], parts[3]


def _parse_cell(value: str) -> tuple[int, int]:
    parts = [int(item) for item in value.lower().replace(" ", "").split("x")]
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("--cell needs WIDTHxHEIGHT")
    return parts[0], parts[1]


def cmd_record(args: argparse.Namespace) -> int:
    frame = Path(args.frame)
    if not frame.is_file():
        raise SystemExit(f"frame not found: {frame}")
    payload = base64.b64encode(frame.read_bytes()).decode("ascii")
    text = args.text.strip()
    x, y, width, height = args.roi
    cell_width, cell_height = args.cell
    cell_rows = args.cell_rows
    if len(text) != args.cells:
        raise SystemExit(
            f"--text has {len(text)} characters but --cells is {args.cells}"
        )
    if cell_rows <= 0 or args.cells % cell_rows != 0:
        raise SystemExit("--cell-rows must divide --cells exactly")
    cols = args.cells // cell_rows
    if cell_width * cols > width:
        raise SystemExit("the cells do not fit horizontally inside --roi")
    if cell_height * cell_rows > height:
        raise SystemExit("the cells do not fit vertically inside --roi")

    glyphs: dict[str, list[int]] = {}
    conflicts: list[str] = []
    for index, character in enumerate(text):
        row = index // cols
        column = index % cols
        box = (
            x + column * cell_width,
            y + row * cell_height,
            cell_width,
            cell_height,
        )
        descriptor = list(compute_grid_fingerprint(payload, crop=box, grid=args.grid))
        existing = glyphs.get(character)
        if existing is None:
            glyphs[character] = descriptor
        elif existing != descriptor:
            # Same character rendering differently in one frame means the cell
            # grid is misaligned. Recording it would make every later read
            # ambiguous, so refuse instead.
            conflicts.append(character)
    if conflicts:
        raise SystemExit(
            "the same glyph produced different descriptors "
            f"({sorted(set(conflicts))}); adjust --roi/--cell until the cells "
            "line up, then record again"
        )

    document = {
        "version": 1,
        "source": str(frame),
        "recorded_text": text,
        "roi": [x, y, width, height],
        "cell_width": cell_width,
        "cell_height": cell_height,
        "cells": args.cells,
        "cell_rows": cell_rows,
        "minimum_score": args.minimum_score,
        "grid": list(args.grid),
        "glyphs": glyphs,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "event": "countdown_glyphs_recorded",
        "path": str(out),
        "glyphs": sorted(glyphs),
        "note": (
            "one frame only binds the glyphs it showed; record more frames with "
            "different digits until all of 0-9 and ':' are covered"
        ),
    }, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    capture = sub.add_parser("capture", help="save one frame for measuring")
    capture.add_argument("--out", default="logs/countdown_frame.png")
    capture.set_defaults(func=cmd_capture)

    record = sub.add_parser("record", help="bind glyph descriptors from a frame")
    record.add_argument("--frame", required=True)
    record.add_argument("--roi", required=True, type=_parse_roi)
    record.add_argument("--cell", required=True, type=_parse_cell)
    record.add_argument("--cells", type=int, default=5)
    record.add_argument(
        "--cell-rows",
        type=int,
        default=1,
        help="rows the cells are stacked into (1 = horizontal row; for the "
        "client's vertically layered HH:MM:SS use --cells 8 --cell-rows 8)",
    )
    record.add_argument("--text", required=True)
    record.add_argument("--minimum-score", type=float, default=0.93)
    record.add_argument("--grid", type=int, nargs=2, default=(8, 12))
    record.add_argument(
        "--out",
        default=str(
            ROOT / "src" / "kancollebot" / "adapters" / "poi"
            / "expedition_countdown_glyphs.json"
        ),
    )
    record.set_defaults(func=cmd_record)

    args = parser.parse_args(argv)
    args.grid = tuple(args.grid)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
