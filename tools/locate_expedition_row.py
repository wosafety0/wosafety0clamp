"""Report where one expedition's row is, without clicking it.

This is the read-only half of the dispatch integration: it exercises the whole
offset chain - select the tab, climb to the proven top, scroll with measured
controls, align each page - and then prints the point a dispatch click *would*
land on. The row itself is never pressed, so the tool cannot dispatch anything.

It is the only way to see the chain work against the live client without
authorising a dispatch, and it is how the row crops in this repository were
produced.

``--allow-scroll-clicks`` is required because locating a row legitimately needs
scroll chevrons and possibly one area-tab cell. Both are measured controls with
receipts (the tab click is only accepted if the orange label moves). Nothing
else on the screen is ever pressed.

Exit codes
    0  the row was located; its point and crop are reported
    2  refused or blocked: no measured control, unprovable top, unbound position,
       or a tab change mid-pass (fail closed, nothing clicked on the row)
    1  the capture or the session itself failed
"""
from __future__ import annotations

import argparse
import base64
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "src", ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from kancollebot.calibration import (  # noqa: E402
    DEFAULT_CATALOG,
    DEFAULT_POSITION_INDEX_PATH,
    DEFAULT_TAB_AREAS,
    ExpeditionListError,
    ExpeditionListSession,
    ExpeditionRowError,
    decode_png,
    encode_png,
    load_position_index,
    scan_page,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("expedition_id", help="expedition number, e.g. 03 or D1")
    parser.add_argument(
        "--tab",
        type=int,
        help="1-based area tab to select first (omit to use the tab already open)",
    )
    parser.add_argument(
        "--allow-scroll-clicks",
        action="store_true",
        help="required: locating a row sends scroll clicks and may switch one tab",
    )
    parser.add_argument(
        "--index",
        type=Path,
        default=DEFAULT_POSITION_INDEX_PATH,
        help="position index to read (defaults to the shipped one)",
    )
    parser.add_argument("--settle-seconds", type=float, default=0.55)
    parser.add_argument("--max-steps", type=int, default=16)
    parser.add_argument("--keep-frames", type=Path, help="keep every captured frame here")
    parser.add_argument("--crop-out", type=Path, help="write the located row as a PNG")
    args = parser.parse_args(argv)

    if not args.allow_scroll_clicks:
        print(
            "REFUSED: locating a row needs scroll clicks. Re-run with "
            "--allow-scroll-clicks once you accept reversible scroll clicks and at "
            "most one area-tab click.",
            file=sys.stderr,
        )
        return 2

    try:
        index = load_position_index(args.index)
    except (ExpeditionListError, OSError, ValueError) as error:
        print(f"INDEX_UNREADABLE: {error}", file=sys.stderr)
        return 1
    if not index.bindings:
        print(f"INDEX_EMPTY: {args.index} holds no position bindings", file=sys.stderr)
        return 1

    frames = args.keep_frames or Path(tempfile.mkdtemp(prefix="locate_row_"))
    frames.mkdir(parents=True, exist_ok=True)
    scratch = frames / "frame.png"
    counter = {"n": 0}
    last = {"image": None, "scan": None}

    from kancollebot.adapters.poi.cdp_input import RecordedPoint
    from kancollebot.adapters.poi.cdp_websocket import connect_game_webview

    def scan():
        counter["n"] += 1
        result = session.transport.send("Page.captureScreenshot", {"format": "png"})
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, str) or not data:
            raise ExpeditionListError("CDP did not return screenshot data")
        scratch.write_bytes(base64.b64decode(data))
        image = decode_png(scratch)
        page = scan_page(image)
        last["image"] = image
        last["scan"] = page
        return page

    def click(point, label):
        session.input.click(RecordedPoint(point[0], point[1], label))

    tab = None if args.tab is None else args.tab - 1
    if tab is not None and not 0 <= tab < len(DEFAULT_TAB_AREAS):
        print(f"REFUSED: --tab must be 1..{len(DEFAULT_TAB_AREAS)}", file=sys.stderr)
        return 2

    try:
        with connect_game_webview() as session:
            locator = ExpeditionListSession(
                scan=scan,
                click=click,
                index=index,
                settle_seconds=args.settle_seconds,
                max_scroll_steps=args.max_steps,
            )
            location = locator.locate(args.expedition_id, tab=tab)
            page = last["scan"]
            image = last["image"]
    except ExpeditionRowError as error:
        print(f"LOCATE_BLOCKED: {error}", file=sys.stderr)
        return 2
    except (ExpeditionListError, OSError, ValueError) as error:
        print(f"LOCATE_FAILED: {error}", file=sys.stderr)
        return 1

    name = DEFAULT_CATALOG.name_of(location.expedition_id)
    area = DEFAULT_TAB_AREAS[location.tab] if 0 <= location.tab < len(DEFAULT_TAB_AREAS) else "?"
    print(f"expedition   : {location.expedition_id}  {name or '-'}")
    print(f"area tab     : {location.tab + 1}/7 ({area})")
    print(f"position     : {location.position} (0-based, row {location.position + 1} of the tab)")
    print(f"page offset  : {location.offset}")
    print(f"row on page  : {location.row_index} of {len(page.rows) if page else 0}")
    print(f"point        : ({location.point[0]}, {location.point[1]:.1f})")
    print(f"scroll clicks: {location.scroll_clicks}")
    print(f"tab clicks   : {location.tab_clicks}")
    print(f"proof        : {location.detail}")

    if args.crop_out and page is not None and image is not None:
        row = page.rows[location.row_index - 1]
        top = max(0, row.top - 4)
        bottom = min(image.height, row.bottom + 4)
        crop = image.crop(150, top, 830, bottom)
        encode_png(args.crop_out, crop)
        print(f"row crop     : {args.crop_out}")
    print()
    print("nothing was clicked on the row; this report is the point a dispatch would use")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
