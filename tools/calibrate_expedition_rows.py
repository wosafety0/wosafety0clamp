"""Read-only calibration of the expedition-list row grid from a screenshot.

The expedition list is canvas-rendered, so per-row geometry must be measured
from pixels (the accessibility tree exposes the game surface as a single
childless ``application`` node). This tool:

* decodes a PNG with the standard library (no numpy, no OpenCV),
* projects dark pixels across a horizontal band to find the text rows,
* fits a straight line to the row centers,
* reports the first/last center, mean pitch, worst pitch error and residuals,
* optionally writes a stacked crop of every detected row for eyeball checking.

It never clicks, never dispatches, and never writes game state. ``--capture``
only takes a screenshot through the allowlisted read-only CDP method.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from kancollebot.calibration import (  # noqa: E402
    DEFAULT_DARK_THRESHOLD,
    DEFAULT_MERGE_GAP,
    DEFAULT_MIN_ROW_HEIGHT,
    DEFAULT_MIN_ROW_PIXELS,
    DEFAULT_PROJECTION_X,
    DEFAULT_SEARCH_Y,
    RowCalibrationError,
    RowGrid,
    calibrate_rows,
    decode_png,
    encode_png,
    stack_vertically,
)

DEFAULT_CLICK_X = 350
DEFAULT_ANCHOR_Y = 350


def _parse_pair(text: str, *, label: str) -> tuple[int, int]:
    parts = text.split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"{label} must look like 'left,right'")
    try:
        left, right = (int(part) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{label} must contain integers") from exc
    if left >= right:
        raise argparse.ArgumentTypeError(f"{label} must be increasing")
    return left, right


def capture_screenshot(destination: Path) -> Path:
    """Take one read-only screenshot through the allowlisted CDP method."""
    from kancollebot.adapters.poi.cdp_websocket import connect_game_webview

    with connect_game_webview() as session:
        result = session.transport.send("Page.captureScreenshot", {"format": "png"})
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, str) or not data:
        raise RowCalibrationError("CDP did not return screenshot data")
    destination.write_bytes(base64.b64decode(data))
    return destination


def _print_report(
    grid: RowGrid,
    *,
    image_size: tuple[int, int],
    labels: list[str],
    click_x: int,
    anchor_y: int,
) -> dict[str, object]:
    print("=== expedition row calibration (read-only) ===")
    print(f"image            : {image_size[0]}x{image_size[1]}")
    print(f"rows detected    : {grid.count}")
    print(f"first center y   : {grid.first_center:.1f}")
    print(f"last center y    : {grid.last_center:.1f}")
    print(f"mean pitch       : {grid.pitch:.2f}")
    if grid.count > 1:
        print(f"pitch range      : {min(g + grid.pitch for g in grid.pitch_errors):.1f}"
              f" .. {max(g + grid.pitch for g in grid.pitch_errors):.1f}")
        print(f"max pitch error  : {grid.max_pitch_error:.2f}")
    print(f"fit              : center = {grid.intercept:.2f} + {grid.pitch:.2f} * index")
    print(f"max residual     : {grid.max_residual:.2f}")
    print(f"first residual   : {grid.residuals[0]:+.2f}")
    print(f"last residual    : {grid.residuals[-1]:+.2f}")

    anchor_row, anchor_delta = grid.nearest_row(anchor_y)
    print(
        f"anchor y={anchor_y:>3d}      : nearest row {anchor_row.index} "
        f"(center {anchor_row.center:.1f}, delta {anchor_delta:+.1f})"
    )

    print()
    header = f"{'row':>3}  {'label':<6} {'top':>4} {'bottom':>6} {'center':>7} {'gap':>6} {'resid':>6}  click"
    print(header)
    print("-" * len(header))
    mapping: dict[str, object] = {}
    for position, row in enumerate(grid.rows):
        gap = "-" if position == 0 else f"{row.center - grid.rows[position - 1].center:.1f}"
        label = labels[position]
        print(
            f"{row.index:>3}  {label:<6} {row.top:>4} {row.bottom:>6} "
            f"{row.center:>7.1f} {gap:>6} {grid.residuals[position]:>+6.2f}  ({click_x}, {row.center:.0f})"
        )
        mapping[label] = {
            "row": row.index,
            "x": click_x,
            "y": round(row.center, 1),
            "y_top": row.top,
            "y_bottom": row.bottom,
            "residual": round(grid.residuals[position], 2),
        }
    return mapping


def _write_annotation(path: Path, image, grid: RowGrid, x_range: tuple[int, int]) -> None:
    left, right = x_range
    crops = []
    for row in grid.rows:
        top = max(0, row.top - 4)
        bottom = min(image.height, row.bottom + 5)
        crops.append(image.crop(left, top, right, bottom))
    encode_png(path, stack_vertically(crops, gap=6))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only expedition-list row calibration from a screenshot."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--image", type=Path, help="existing PNG screenshot")
    source.add_argument(
        "--capture",
        action="store_true",
        help="take one read-only CDP screenshot of the game webview",
    )
    parser.add_argument("--x-range", type=lambda text: _parse_pair(text, label="--x-range"),
                        default=DEFAULT_PROJECTION_X, help="projection columns, default 155,745")
    parser.add_argument("--y-range", type=lambda text: _parse_pair(text, label="--y-range"),
                        default=DEFAULT_SEARCH_Y, help="search rows, default 240,600")
    parser.add_argument("--dark-threshold", type=int, default=DEFAULT_DARK_THRESHOLD)
    parser.add_argument("--min-pixels", type=int, default=DEFAULT_MIN_ROW_PIXELS)
    parser.add_argument("--min-height", type=int, default=DEFAULT_MIN_ROW_HEIGHT)
    parser.add_argument("--merge-gap", type=int, default=DEFAULT_MERGE_GAP)
    parser.add_argument("--click-x", type=int, default=DEFAULT_CLICK_X,
                        help="x used in the emitted click mapping")
    parser.add_argument("--anchor-y", type=int, default=DEFAULT_ANCHOR_Y,
                        help="y to check against the detected grid (recorded point)")
    parser.add_argument("--labels", default="",
                        help="comma separated display labels, e.g. 01,02,...,A1")
    parser.add_argument("--annotate", type=Path, help="write a stacked crop of every row")
    parser.add_argument("--json-out", type=Path, help="write the mapping as JSON")
    args = parser.parse_args(argv)

    if not args.image and not args.capture:
        parser.error("one of --image or --capture is required")

    image_path = args.image
    if args.capture:
        image_path = capture_screenshot(
            Path(tempfile.gettempdir()) / "kancolle_expedition_calibration.png"
        )

    image = decode_png(image_path)
    try:
        grid = calibrate_rows(
            image,
            x_range=args.x_range,
            y_range=args.y_range,
            dark_threshold=args.dark_threshold,
            min_pixels=args.min_pixels,
            min_height=args.min_height,
            merge_gap=args.merge_gap,
        )
    except RowCalibrationError as exc:
        print(f"CALIBRATION_FAILED: {exc}", file=sys.stderr)
        return 1

    labels = [part.strip() for part in args.labels.split(",") if part.strip()]
    if labels and len(labels) != grid.count:
        print(
            f"LABEL_COUNT_MISMATCH: {len(labels)} labels for {grid.count} rows; "
            "refusing to guess a mapping",
            file=sys.stderr,
        )
        return 2
    if not labels:
        labels = [str(row.index) for row in grid.rows]

    mapping = _print_report(
        grid,
        image_size=(image.width, image.height),
        labels=labels,
        click_x=args.click_x,
        anchor_y=args.anchor_y,
    )

    print()
    print("=== display label -> click point ===")
    for label, entry in mapping.items():
        print(f"  {label:>4}: ({entry['x']}, {entry['y']})")

    if args.annotate:
        _write_annotation(args.annotate, image, grid, args.x_range)
        print(f"annotation written to {args.annotate}")
    if args.json_out:
        payload = {
            "image": str(image_path),
            "rows": grid.count,
            "pitch": round(grid.pitch, 3),
            "first_center": round(grid.first_center, 2),
            "last_center": round(grid.last_center, 2),
            "max_residual": round(grid.max_residual, 3),
            "mapping": mapping,
        }
        args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"json written to {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
