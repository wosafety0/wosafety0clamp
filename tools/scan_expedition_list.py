"""Read-only scan of the expedition list: rows, area tab, boundary, identity.

The expedition list is per-area and grows as expeditions unlock, so a single
screenshot is only ever one page of one tab. This tool scans pages, binds rows
to expedition numbers by pixel signature, and reports what it can prove:

* which rows are on screen and where their centers are,
* which area tab is selected (orange label, not an assumed order),
* whether the list provably ends on this page (a full page proves nothing),
* which rows still need a one-time label before they can be named.

It never clicks, never scrolls, and never dispatches input. The scroll controls
are reported from their measured geometry (gold chevron boxes), so
``--propose-scroll`` can name a real point; actually pressing it is left to an
authorised caller.

Exit codes
    0  scanned, list boundary proven, every visible row identified
    2  scanned but blocked: unlabelled rows, unknown boundary, or pages that
       could not be aligned (fail closed)
    1  the scan itself failed
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
    DEFAULT_CATALOG,
    DEFAULT_PANEL_BOX,
    BoundaryState,
    ExpeditionListError,
    ExpeditionListIndex,
    PageScan,
    check_display_order,
    classify_boundary,
    decode_png,
    encode_png,
    find_button_candidates,
    plan_scroll_step,
    scan_page,
    stack_vertically,
)

INDEX_VERSION = 1


def _parse_box(text: str) -> tuple[int, int, int, int]:
    parts = text.split(",")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("--panel-box must look like 'left,top,right,bottom'")
    try:
        left, top, right, bottom = (int(part) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--panel-box must contain integers") from exc
    if not (left < right and top < bottom):
        raise argparse.ArgumentTypeError("--panel-box must be increasing")
    return left, top, right, bottom


def _parse_label(text: str) -> tuple[int | None, int, str]:
    """Parse ``row=ID`` or ``page:row=ID``."""
    if "=" not in text:
        raise argparse.ArgumentTypeError("--label must look like 'row=ID' or 'page:row=ID'")
    where, expedition_id = text.split("=", 1)
    expedition_id = expedition_id.strip()
    if not expedition_id:
        raise argparse.ArgumentTypeError("--label needs an expedition number")
    page: int | None = None
    row_text = where
    if ":" in where:
        page_text, row_text = where.split(":", 1)
        try:
            page = int(page_text)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("--label page must be an integer") from exc
    try:
        row = int(row_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--label row must be an integer") from exc
    if row < 1:
        raise argparse.ArgumentTypeError("--label row is 1-based")
    return page, row, expedition_id


def _infer_area(numbers: tuple[str, ...]) -> str | None:
    """Pick the catalogue area when every recognised number belongs to one.

    The tab index cannot be mapped to an area name without reading the label,
    but the numbers themselves are unambiguous, so a homogeneous list identifies
    its own area. A mixed or entirely unknown list returns ``None`` and the
    caller must pass ``--area``.
    """
    areas = {
        area
        for area in (DEFAULT_CATALOG.area_of(number) for number in numbers)
        if area is not None
    }
    return next(iter(areas)) if len(areas) == 1 else None


def capture_screenshot(destination: Path) -> Path:
    """Take one read-only screenshot through the allowlisted CDP method."""
    from kancollebot.adapters.poi.cdp_websocket import connect_game_webview

    with connect_game_webview() as session:
        result = session.transport.send("Page.captureScreenshot", {"format": "png"})
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, str) or not data:
        raise ExpeditionListError("CDP did not return screenshot data")
    destination.write_bytes(base64.b64decode(data))
    return destination


def load_index(path: Path) -> ExpeditionListIndex:
    index = ExpeditionListIndex()
    if not path.exists():
        return index
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != INDEX_VERSION:
        raise ExpeditionListError(f"{path} is not a version {INDEX_VERSION} index")
    bindings = payload.get("bindings", {})
    if not isinstance(bindings, dict):
        raise ExpeditionListError("index bindings must be an object")
    for key, expedition_id in bindings.items():
        if not isinstance(key, str) or not isinstance(expedition_id, str):
            raise ExpeditionListError("index entries must be strings")
        tab_text, _, signature_key = key.partition("|")
        tab = None if tab_text == "none" else int(tab_text)
        index.bindings[(tab, signature_key)] = expedition_id
    return index


def save_index(path: Path, index: ExpeditionListIndex) -> None:
    payload = {
        "version": INDEX_VERSION,
        "bindings": {
            f"{'none' if tab is None else tab}|{signature_key}": expedition_id
            for (tab, signature_key), expedition_id in sorted(
                index.bindings.items(), key=lambda item: (str(item[0][0]), item[1])
            )
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def master_records() -> dict[str, dict[str, object]]:
    """Read the mission master data through the local Poi read tools."""
    from kancollebot.adapters.poi.mcp_client import PoiMcpClient

    client = PoiMcpClient()
    result = client.call_read_tool("poi_get", {"path": "const.$missions"})
    content = result.get("content", [])
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            payload = json.loads(item["text"])
            items = payload.get("items", {})
            if not isinstance(items, dict):
                raise ExpeditionListError("mission master data has no items map")
            by_disp: dict[str, dict[str, object]] = {}
            for record in items.values():
                if isinstance(record, dict) and isinstance(record.get("api_disp_no"), str):
                    by_disp[record["api_disp_no"]] = record
            return by_disp
    raise ExpeditionListError("mission master data was not returned")


def _print_page(page: int, scan: PageScan) -> None:
    tab_text = "unknown" if scan.tab is None else f"tab {scan.tab + 1}/{scan.tabs.count}"
    print(f"--- page {page} ({tab_text}) ---")
    print(f"  image          : {scan.image_size[0]}x{scan.image_size[1]}")
    print(f"  rows visible   : {len(scan.rows)} of capacity {scan.capacity}")
    print(f"  tab scores     : {', '.join(str(value) for value in scan.tabs.scores)}")
    print(f"  scroll hints   : {len(scan.scroll_candidates)} candidate(s)")
    for name, control in (("up", scan.scroll_up), ("down", scan.scroll_down)):
        if control is None:
            print(f"  scroll {name:<7}: not measured on this capture")
        else:
            print(
                f"  scroll {name:<7}: box {control.box} center {control.center} "
                f"gold {control.gold_pixels}px"
            )
    for row in scan.rows:
        print(
            f"    row {row.index}: y {row.top:>3}..{row.bottom:<3} "
            f"center {row.center:>6.1f}  signature {row.signature.key}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only scan of the expedition list (no input, no scrolling)."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--image",
        type=Path,
        action="append",
        help="screenshot PNG; repeat in page order (post-scroll pages after the first)",
    )
    source.add_argument(
        "--capture",
        action="store_true",
        help="take one read-only CDP screenshot instead of reading a file",
    )
    parser.add_argument("--index", type=Path, help="JSON file holding signature->number bindings")
    parser.add_argument(
        "--label",
        type=_parse_label,
        action="append",
        default=[],
        metavar="[PAGE:]ROW=ID",
        help="bind a row of a page to an expedition number, e.g. 3=A2 or 1:3=A2",
    )
    parser.add_argument(
        "--label-all",
        default="",
        help="comma separated numbers for every row of the last page, in order",
    )
    parser.add_argument(
        "--names",
        action="store_true",
        help="resolve expedition names from the mission master data (read-only)",
    )
    parser.add_argument(
        "--catalog",
        action="store_true",
        help=(
            "compare the observed rows with the offline wiki catalogue and name them; "
            "the catalogue is an account-independent ceiling, not this account's unlock state"
        ),
    )
    parser.add_argument(
        "--area",
        help="catalogue area to compare against, e.g. 鎮守府海域 (inferred from the numbers when omitted)",
    )
    parser.add_argument(
        "--propose-scroll",
        choices=("up", "down"),
        help="print the next scroll step that would be structurally safe",
    )
    parser.add_argument(
        "--confirm-scroll-point",
        help="human-confirmed scroll control, as 'x,y'",
    )
    parser.add_argument("--panel-box", type=_parse_box, default=DEFAULT_PANEL_BOX)
    parser.add_argument(
        "--probe-band",
        type=_parse_box,
        help=(
            "report small glyph clusters in 'left,top,right,bottom' as scroll-control "
            "candidates; use this once an overfull list exposes the real control"
        ),
    )
    parser.add_argument("--annotate", type=Path, help="write a stacked crop of every visible row")
    parser.add_argument("--json-out", type=Path, help="write the scan result as JSON")
    parser.add_argument("--keep-capture", type=Path, help="write the captured PNG here")
    args = parser.parse_args(argv)

    paths: list[Path] = []
    if args.capture:
        destination = args.keep_capture or (
            Path(tempfile.gettempdir()) / "kancolle_expedition_list_scan.png"
        )
        paths.append(capture_screenshot(destination))
    else:
        paths.extend(args.image or [])

    index = load_index(args.index) if args.index else ExpeditionListIndex()
    scans: list[PageScan] = []
    try:
        for path in paths:
            image = decode_png(path)
            scan = scan_page(image, panel_box=args.panel_box)
            if not scan.rows:
                print(
                    f"SCAN_FAILED: no expedition rows in {path}; is the expedition list open?",
                    file=sys.stderr,
                )
                return 1
            scans.append(scan)
            if scan.tab is None:
                print(
                    "TAB_UNKNOWN: the selected area tab is not identifiable; "
                    "refusing to file these rows under an area",
                    file=sys.stderr,
                )
                return 2
            index.add_page(scan)
    except (ExpeditionListError, ValueError) as exc:
        print(f"SCAN_FAILED: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"SCAN_FAILED: {exc}", file=sys.stderr)
        return 1

    for page, scan in enumerate(scans):
        _print_page(page, scan)

    probed = ()
    if args.probe_band:
        try:
            probed = find_button_candidates(decode_png(paths[-1]), region=args.probe_band)
        except ValueError as exc:
            print(f"PROBE_BAND_INVALID: {exc}", file=sys.stderr)
            return 2
        print()
        print("=== glyph clusters in the probed band (candidates, never clicked) ===")
        if not probed:
            print("  none")
        for candidate in probed:
            print(
                f"  center {candidate.center} size {candidate.width}x{candidate.height} "
                f"pixels {candidate.pixels}"
            )

    # --label-all refers to the last page, because that is the page a human just looked at.
    if args.label_all.strip():
        labels = [part.strip() for part in args.label_all.split(",") if part.strip()]
        last = len(scans) - 1
        if len(labels) != len(scans[last].rows):
            print(
                f"LABEL_COUNT_MISMATCH: {len(labels)} labels for {len(scans[last].rows)} "
                "visible rows; refusing to guess a mapping",
                file=sys.stderr,
            )
            return 2
        for row_index, expedition_id in enumerate(labels, start=1):
            try:
                index.label_row(last, row_index, expedition_id)
            except ExpeditionListError as exc:
                print(f"LABEL_REJECTED: {exc}", file=sys.stderr)
                return 2
    for page, row, expedition_id in args.label:
        target = len(scans) - 1 if page is None else page
        try:
            index.label_row(target, row, expedition_id)
        except ExpeditionListError as exc:
            print(f"LABEL_REJECTED: {exc}", file=sys.stderr)
            return 2

    latest = scans[-1]
    boundary = classify_boundary(latest)
    entries = index.entries(DEFAULT_CATALOG.name_of if args.catalog else None)
    unbound = index.unbound_rows
    order_check = "unverified"
    names: dict[str, str] = {}
    coverage = None
    if args.catalog:
        observed_ids = tuple(entry.expedition_id for entry in entries)
        area = args.area or _infer_area(observed_ids)
        try:
            coverage = DEFAULT_CATALOG.coverage(observed_ids, area=area)
        except ValueError as exc:
            print(f"CATALOG_QUERY_INVALID: {exc}", file=sys.stderr)
            return 2
        for entry in entries:
            resolved = DEFAULT_CATALOG.name_of(entry.expedition_id)
            if resolved:
                names.setdefault(entry.expedition_id, resolved)
    if args.names:
        try:
            records = master_records()
        except Exception as exc:  # noqa: BLE001 - names are metadata only
            print(f"NAMES_UNAVAILABLE: {exc}", file=sys.stderr)
            records = {}
        for entry in entries:
            record = records.get(entry.expedition_id)
            if record is not None and isinstance(record.get("api_name"), str):
                names[entry.expedition_id] = record["api_name"]
        order_check = check_display_order(
            tuple(entry.expedition_id for entry in entries if entry.expedition_id in records)
        )

    print()
    print("=== list state ===")
    print(f"pages scanned    : {len(scans)}")
    print(f"area tab         : {latest.tab}")
    print(f"boundary         : {boundary.state.value} ({boundary.reason})")
    print(f"rows identified  : {sum(1 for entry in entries if entry.visible)}/{len(latest.rows)}")
    print(f"display order    : {order_check}")

    if coverage is not None:
        scope = coverage.area or "all areas (numbers mixed or unrecognised)"
        print()
        print("=== catalogue coverage (wiki ceiling, not this account) ===")
        print(f"  area       : {scope}")
        print(f"  observed   : {len(coverage.observed)}")
        print(f"  missing    : {', '.join(coverage.missing) if coverage.missing else 'none'}")
        print(f"  extra      : {', '.join(coverage.foreign) if coverage.foreign else 'none'}")
        print(
            "  note       : 'missing' means locked here OR never scanned; "
            "pixels cannot tell the two apart"
        )
        print(f"  source     : {DEFAULT_CATALOG.source} ({DEFAULT_CATALOG.retrieved_on})")

    if entries:
        print()
        print("=== identified rows ===")
        header = f"{'id':>4}  {'name':<14} {'page':>4} {'visible':>7}  center"
        print(header)
        print("-" * len(header))
        for entry in entries:
            center = "-" if entry.center is None else f"({entry.center[0]}, {entry.center[1]:.0f})"
            print(
                f"{entry.expedition_id:>4}  {names.get(entry.expedition_id, '-'):<14} "
                f"{entry.scroll_page:>4} {str(entry.visible):>7}  {center}"
            )

    if unbound:
        print()
        print("=== rows that need a one-time label ===")
        for page, row in unbound:
            scan = scans[page]
            print(
                f"  page {page} row {row}: center ({scan.rows[row - 1].click_point[0]}, "
                f"{scan.rows[row - 1].center:.0f}) signature {scan.rows[row - 1].signature.key}"
            )
        print("  bind them with --label-all 01,02,... or --label [PAGE:]ROW=ID")

    proposal = None
    if args.propose_scroll:
        point = None
        if args.confirm_scroll_point:
            try:
                x_text, y_text = args.confirm_scroll_point.split(",")
                point = (int(x_text), int(y_text))
            except ValueError:
                print("SCROLL_POINT_INVALID: expected 'x,y'", file=sys.stderr)
                return 2
        proposal = plan_scroll_step(latest, direction=args.propose_scroll, confirmed_point=point)
        print()
        print("=== next scroll step (not executed) ===")
        print(f"  action : {proposal.action}")
        print(f"  point  : {proposal.point}")
        print(f"  detail : {proposal.detail}")

    if args.annotate:
        image = decode_png(paths[-1])
        crops = [
            image.crop(233, max(0, row.top - 3), min(image.width, 745), min(image.height, row.bottom + 4))
            for row in latest.rows
        ]
        encode_png(args.annotate, stack_vertically(crops, gap=6))
        print(f"\nannotation written to {args.annotate}")

    if args.index:
        save_index(args.index, index)
        print(f"index written to {args.index}")

    payload = {
        "image": str(paths[-1]),
        "pages": len(scans),
        "tab": latest.tab,
        "tab_count": latest.tabs.count,
        "tab_scores": list(latest.tabs.scores),
        "capacity": latest.capacity,
        "boundary": {"state": boundary.state.value, "reason": boundary.reason},
        "display_order_check": order_check,
        "catalog": (
            None
            if coverage is None
            else {
                "area": coverage.area,
                "observed": list(coverage.observed),
                "missing": list(coverage.missing),
                "extra": list(coverage.foreign),
                "complete": coverage.is_complete,
                "size": len(DEFAULT_CATALOG),
                "source": DEFAULT_CATALOG.source,
                "retrieved_on": DEFAULT_CATALOG.retrieved_on,
            }
        ),
        "scroll_candidates": [
            {"center": list(candidate.center), "width": candidate.width, "height": candidate.height}
            for candidate in latest.scroll_candidates
        ],
        "scroll_controls": {
            name: (
                None
                if control is None
                else {
                    "box": list(control.box),
                    "center": list(control.center),
                    "gold_pixels": control.gold_pixels,
                }
            )
            for name, control in (("up", latest.scroll_up), ("down", latest.scroll_down))
        },
        "probe_band": (
            None
            if not args.probe_band
            else {
                "region": list(args.probe_band),
                "candidates": [
                    {
                        "center": list(candidate.center),
                        "width": candidate.width,
                        "height": candidate.height,
                        "pixels": candidate.pixels,
                    }
                    for candidate in probed
                ],
            }
        ),
        "rows": [
            {**entry.to_dict(), "name": names.get(entry.expedition_id)}
            for entry in entries
        ],
        "unbound_rows": [
            {
                "page": page,
                "row": row,
                "center": {
                    "x": scans[page].rows[row - 1].click_point[0],
                    "y": round(scans[page].rows[row - 1].center, 1),
                },
                "signature": scans[page].rows[row - 1].signature.key,
            }
            for page, row in unbound
        ],
        "proposal": (
            None
            if proposal is None
            else {"action": proposal.action, "point": proposal.point, "detail": proposal.detail}
        ),
    }
    if args.json_out:
        args.json_out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"json written to {args.json_out}")

    if unbound:
        return 2
    if boundary.state is not BoundaryState.END_CONFIRMED:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
