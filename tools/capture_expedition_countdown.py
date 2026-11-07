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

from kancollebot.adapters.poi.expedition_countdown_vision import (  # noqa: E402
    CountdownTemplates,
    CountdownTemplateError,
    _cell_ink,
    _ink_similarity,
)
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


def _parse_boxes(value: str) -> list[tuple[int, int, int, int]]:
    boxes: list[tuple[int, int, int, int]] = []
    for part in value.split(";"):
        part = part.strip()
        if not part:
            continue
        nums = [int(item) for item in part.replace(" ", "").split(",")]
        if len(nums) != 4:
            raise argparse.ArgumentTypeError(
                "each --boxes entry needs x,y,width,height"
            )
        boxes.append((nums[0], nums[1], nums[2], nums[3]))
    if not boxes:
        raise argparse.ArgumentTypeError("--boxes needs at least one entry")
    return boxes


def _parse_groups(value: str) -> tuple[int, ...]:
    parts = [int(item) for item in value.replace(" ", "").split(",") if item]
    if not parts or any(item <= 0 for item in parts):
        raise argparse.ArgumentTypeError("--groups needs positive integers")
    return tuple(parts)


def cmd_record(args: argparse.Namespace) -> int:
    frame = Path(args.frame)
    if not frame.is_file():
        raise SystemExit(f"frame not found: {frame}")
    payload = base64.b64encode(frame.read_bytes()).decode("ascii")
    text = args.text.strip()
    grid = tuple(args.grid)

    explicit_boxes = getattr(args, "boxes", None)
    groups = getattr(args, "groups", None)
    if explicit_boxes:
        cell_box_list = [tuple(box) for box in explicit_boxes]
        cells = len(cell_box_list)
        for (bx, by, bw, bh) in cell_box_list:
            if bx < 0 or by < 0 or bw <= 0 or bh <= 0:
                raise SystemExit("boxes need non-negative x/y and positive width/height")
        if len(text) != cells:
            raise SystemExit(
                f"--text has {len(text)} characters but --boxes has {cells} entries"
            )
        if groups is not None and sum(groups) != cells:
            raise SystemExit("--groups must sum to the number of --boxes")
        min_x = min(box[0] for box in cell_box_list)
        min_y = min(box[1] for box in cell_box_list)
        max_x = max(box[0] + box[2] for box in cell_box_list)
        max_y = max(box[1] + box[3] for box in cell_box_list)
        roi = (min_x, min_y, max_x - min_x, max_y - min_y)
        cell_width = max(box[2] for box in cell_box_list)
        cell_height = max(box[3] for box in cell_box_list)
        cell_rows = 1
    else:
        if args.roi is None or args.cell is None:
            raise SystemExit("--roi and --cell are required unless --boxes is given")
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
        cells = args.cells
        roi = (x, y, width, height)
        cell_box_list = []
        for index in range(cells):
            row = index // cols
            column = index % cols
            cell_box_list.append(
                (
                    x + column * cell_width,
                    y + row * cell_height,
                    cell_width,
                    cell_height,
                )
            )

    expected_boxes = tuple(cell_box_list) if explicit_boxes else None
    expected_groups = groups if explicit_boxes else None
    descriptor_kind = getattr(args, "descriptor", "grid")
    if descriptor_kind not in ("grid", "ink"):
        raise SystemExit("--descriptor must be 'grid' or 'ink'")

    glyphs: dict[str, list[int]] = {}
    conflicts: list[str] = []
    for character, box in zip(text, cell_box_list):
        if descriptor_kind == "ink":
            descriptor = list(_cell_ink(payload, box, grid))
        else:
            descriptor = list(compute_grid_fingerprint(payload, crop=box, grid=grid))
        existing = glyphs.get(character)
        if existing is None:
            glyphs[character] = descriptor
        elif descriptor_kind == "ink":
            # The same digit at the larger HH:MM size and the smaller SS size is
            # never byte-identical; accept it when it is clearly the same glyph.
            if _ink_similarity(tuple(existing), tuple(descriptor)) < 0.85:
                conflicts.append(character)
        elif existing != descriptor:
            # Same character rendering differently in one frame means the cell
            # grid is misaligned. Recording it would make every later read
            # ambiguous, so refuse instead.
            conflicts.append(character)
    if conflicts:
        raise SystemExit(
            "the same glyph produced different descriptors "
            f"({sorted(set(conflicts))}); adjust the boxes/cells until they line "
            "up, then record again"
        )

    out = Path(args.out)
    if args.merge and out.is_file():
        try:
            prior = json.loads(out.read_text(encoding="utf-8"))
            existing = CountdownTemplates.from_document(prior)
        except (OSError, ValueError, CountdownTemplateError) as exc:
            raise SystemExit(f"--merge target is not a usable template: {exc}")
        if (
            existing.roi != roi
            or existing.cell_width != cell_width
            or existing.cell_height != cell_height
            or existing.cells != cells
            or existing.cell_rows != cell_rows
            or existing.grid != grid
            or existing.boxes != expected_boxes
            or existing.groups != expected_groups
            or existing.descriptor != descriptor_kind
        ):
            raise SystemExit(
                "--merge target has a different layout; re-record it from "
                "scratch without --merge"
            )
        merged: dict[str, list[int]] = {name: list(value) for name, value in existing.glyphs.items()}
        for character, descriptor in glyphs.items():
            if character in merged:
                if descriptor_kind == "ink":
                    if _ink_similarity(tuple(merged[character]), tuple(descriptor)) < 0.85:
                        conflicts.append(character)
                elif merged[character] != list(descriptor):
                    # This character already had a descriptor from an earlier
                    # frame and now renders differently: the grid has drifted
                    # between captures, so refuse rather than overwrite.
                    conflicts.append(character)
            else:
                # A glyph this frame shows for the first time: add it, do not
                # overwrite the others already accumulated in the template.
                merged[character] = list(descriptor)
        if conflicts:
            raise SystemExit(
                "a merged glyph disagrees with the earlier recording "
                f"({sorted(set(conflicts))}); the cell grid is not stable, fix "
                "the boxes before merging"
            )
        glyphs = merged

    document = {
        "version": 1,
        "source": str(frame),
        "recorded_text": text,
        "roi": list(roi),
        "cell_width": cell_width,
        "cell_height": cell_height,
        "cells": cells,
        "cell_rows": cell_rows,
        "minimum_score": args.minimum_score,
        "grid": list(grid),
        "descriptor": descriptor_kind,
        "glyphs": glyphs,
    }
    if expected_boxes is not None:
        document["boxes"] = [list(box) for box in expected_boxes]
    if expected_groups is not None:
        document["groups"] = list(expected_groups)
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
    record.add_argument("--roi", default=None, type=_parse_roi)
    record.add_argument("--cell", default=None, type=_parse_cell)
    record.add_argument(
        "--boxes",
        default=None,
        type=_parse_boxes,
        help="explicit per-cell boxes 'x,y,w,h;x,y,w,h;...' for non-uniform "
        "layouts (e.g. the client's HH:MM / SS); replaces --roi/--cell/--cells",
    )
    record.add_argument(
        "--groups",
        default=None,
        type=_parse_groups,
        help="how to join the read glyphs, e.g. '2,2,2' -> HH:MM:SS",
    )
    record.add_argument("--cells", type=int, default=5)
    record.add_argument(
        "--cell-rows",
        type=int,
        default=1,
        help="rows the cells are stacked into (1 = horizontal row; for the "
        "client's vertically layered HH:MM:SS use --cells 8 --cell-rows 8)",
    )
    record.add_argument("--text", required=True)
    record.add_argument(
        "--merge",
        action="store_true",
        help="update an existing template with only the glyphs this frame "
        "shows, keeping the rest; refuses if the new frame disagrees with a "
        "previously recorded glyph (cell grid not stable)",
    )
    record.add_argument("--minimum-score", type=float, default=0.93)
    record.add_argument(
        "--descriptor",
        choices=("grid", "ink"),
        default="grid",
        help="glyph descriptor: 'grid' (full-crop fingerprint) or 'ink' "
        "(binarised, size-normalised; needed for the two-row HH:MM/SS box)",
    )
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
