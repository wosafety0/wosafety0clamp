"""Page through one area tab of the expedition list and read every row.

``scan_expedition_list.py`` never clicks. This tool does, so it is deliberately
separate and gated behind ``--allow-scroll-clicks``. It is willing to press
exactly two kinds of thing, both of them measured rather than guessed:

* a scroll chevron - ``plan_scroll_step`` must return ``click_measured_control``,
  otherwise the walk stops instead of inventing a coordinate;
* one area tab, and only when ``--switch-to`` asked for it. The point comes from
  ``detect_area_tabs(...).centers``, and the click is only accepted if the
  orange label moves to that tab (see ``Walker.select_tab``); a click that
  changes nothing, or leaves the selection unreadable, blocks the walk.

Nothing else on the screen is ever pressed - not a row, not a dispatch button.

Why a walk is needed at all: the eight-row viewport hides rows, so a single
screenshot cannot distinguish "this account has not unlocked the rest" from
"the rest is on the next page". Only reaching both ends settles it.

Order matters, and the first version of this tool got it wrong. The list is not
necessarily showing its first row when the walk starts - a previous scroll
leaves it part-way down - so walking straight *down* silently skipped the rows
above. The walk therefore climbs to the true top first (no ``PREV`` chevron),
indexes from there, and only then descends. Finally it returns to the page it
started on: up to the top, then down the same number of steps it had climbed,
verified by pixel signature rather than assumed.

Names come from the offline catalogue, so no OCR is involved, but identity still
has to be bound once. Identity is the row's **list position**, not its pixels:
``西方海域`` rows 29 and 30 render identically, so a fingerprint cannot tell them
apart while their positions differ by one. ``--label-first 01,02,...`` binds the
top page's positions; every later page then resolves through its offset, and
``--index`` keeps those bindings so the next walk needs no labelling at all.

Exit codes
    0  walked both ends, every row named, starting page restored
    2  walked but blocked: no measured control, no movement, pages that would
       not align, an unnamed row, or a tab change mid-walk (fail closed)
    1  the walk itself failed
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "src", ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from kancollebot.calibration import (  # noqa: E402
    DEFAULT_CATALOG,
    DEFAULT_PANEL_BOX,
    DEFAULT_TAB_AREAS,
    BoundaryState,
    ExpeditionListError,
    ExpeditionListIndex,
    PageScan,
    align_pages,
    check_display_order,
    classify_boundary,
    decode_png,
    plan_scroll_step,
    same_view,
    scan_page,
)
from scan_expedition_list import load_index, save_index  # noqa: E402

DEFAULT_SETTLE_SECONDS = 0.9
DEFAULT_MAX_STEPS = 24


def _capture(session, destination: Path) -> PageScan:
    result = session.transport.send("Page.captureScreenshot", {"format": "png"})
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, str) or not data:
        raise ExpeditionListError("CDP did not return screenshot data")
    destination.write_bytes(base64.b64decode(data))
    return scan_page(decode_png(destination))


def _click_measured(session, point: tuple[int, int], label: str) -> None:
    from kancollebot.adapters.poi.cdp_input import RecordedPoint

    session.input.click(RecordedPoint(point[0], point[1], label))


class Walker:
    """One CDP session, one settle delay, one place to log every click."""

    def __init__(self, session, *, settle_seconds: float, frames: Path) -> None:
        self.session = session
        self.settle_seconds = settle_seconds
        self.frames = frames
        self.clicks = 0
        self.events: list[dict[str, object]] = []

    def capture(self, tag: str) -> PageScan:
        path = self.frames / f"{tag}.png"
        return _capture(self.session, path)

    def step(
        self, current: PageScan, *, direction: str, expected_tab: int | None, tag: str
    ) -> tuple[str, PageScan | None]:
        """Take one measured scroll step. Return ``(outcome, new_scan)``."""
        proposal = plan_scroll_step(current, direction=direction)
        if proposal.action != "click_measured_control" or proposal.point is None:
            return proposal.action, None
        print(
            f"  {direction:<4} click {proposal.point} ({proposal.detail})",
            flush=True,
        )
        _click_measured(self.session, proposal.point, f"expedition-scroll-{direction}")
        self.clicks += 1
        time.sleep(self.settle_seconds)
        moved = self.capture(tag)
        if not moved.rows:
            return "rows_disappeared", None
        if moved.tab != expected_tab:
            return "tab_changed", None
        if same_view(current, moved):
            return "no_movement", None
        return "moved", moved

    def select_tab(self, current: PageScan, target: int) -> tuple[str, PageScan | None]:
        """Press one area tab and prove the strip really moved there.

        The selected tab is the only one painting its label orange, so the tab's
        own detection doubles as the receipt for the click: either the label is
        on ``target`` afterwards or the click is treated as not delivered. That
        is why no coordinate can be "probably right" - a miss costs nothing and
        is reported as a miss.

        A tab that is already selected gets no click at all: pressing it again
        would be indistinguishable from a failed press, and the caller asked for
        a state, not for an event.
        """
        centers = current.tabs.centers
        if not 0 <= target < len(centers):
            return "target_out_of_range", None
        if current.tab == target:
            print(f"  tab {target + 1}: already selected, no click", flush=True)
            return "already_selected", current
        point = centers[target]
        print(f"  tab {target + 1}: click {point}", flush=True)
        _click_measured(self.session, point, f"expedition-tab-{target + 1}")
        self.clicks += 1
        time.sleep(self.settle_seconds)
        after = self.capture(f"tab_{target + 1:02d}_selected")
        if after.tab is None:
            return "tab_unreadable", None
        if after.tab != target:
            return "tab_not_selected", None
        return "selected", after


def _parse_labels(text: str) -> list[str]:
    labels = [part.strip() for part in text.split(",") if part.strip()]
    if not labels:
        raise argparse.ArgumentTypeError("--label-first needs at least one number")
    return labels


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Walk one expedition-list tab from the true top to the bottom, name every "
            "row, then return to the starting page. Clicks measured scroll chevrons only."
        )
    )
    parser.add_argument(
        "--allow-scroll-clicks",
        action="store_true",
        help="required: without it the tool refuses to touch the input at all",
    )
    parser.add_argument("--area", help="area name for the catalogue comparison (inferred when omitted)")
    parser.add_argument(
        "--switch-to",
        type=int,
        metavar="TAB",
        help=(
            "1-based position on the tab strip to select before walking, e.g. 4 for "
            "南西海域; switching resets the list to its top page"
        ),
    )
    parser.add_argument("--panel-box", default=None, help="override the list panel box 'l,t,r,b'")
    parser.add_argument(
        "--label-first",
        type=_parse_labels,
        help="numbers for every row of the top page, in order, e.g. 01,02,...,08",
    )
    parser.add_argument(
        "--label-unbound",
        type=_parse_labels,
        help=(
            "numbers for the still-unbound list positions, bottom-most last, in screen "
            "order; a freshly walked tab needs this once for rows no binding covers"
        ),
    )
    parser.add_argument("--index", type=Path, help="JSON file of signature->number bindings to reuse and update")
    parser.add_argument(
        "--max-steps", type=int, default=DEFAULT_MAX_STEPS, help="hard stop on steps in each phase"
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=DEFAULT_SETTLE_SECONDS,
        help="wait after each click before capturing",
    )
    parser.add_argument("--json-out", type=Path, help="write the walk result as JSON")
    parser.add_argument(
        "--keep-frames",
        type=Path,
        help="directory to keep every captured frame in (default: temp)",
    )
    parser.add_argument(
        "--stay",
        action="store_true",
        help="do not return to the starting page (leaves the list at the bottom)",
    )
    args = parser.parse_args(argv)

    if not args.allow_scroll_clicks:
        print(
            "REFUSED: this tool sends input. Re-run with --allow-scroll-clicks once you "
            "accept one reversible scroll click per step.",
            file=sys.stderr,
        )
        return 2
    if args.max_steps < 1:
        print("REFUSED: --max-steps must be at least 1", file=sys.stderr)
        return 2

    panel = DEFAULT_PANEL_BOX
    if args.panel_box:
        parts = args.panel_box.split(",")
        if len(parts) != 4:
            print("REFUSED: --panel-box must look like 'left,top,right,bottom'", file=sys.stderr)
            return 2
        try:
            panel = tuple(int(part) for part in parts)  # type: ignore[assignment]
        except ValueError:
            print("REFUSED: --panel-box must contain integers", file=sys.stderr)
            return 2

    frames = args.keep_frames or Path(tempfile.mkdtemp(prefix="expedition_walk_"))
    frames.mkdir(parents=True, exist_ok=True)

    from kancollebot.adapters.poi.cdp_websocket import connect_game_webview

    index = load_index(args.index) if args.index else ExpeditionListIndex()
    blocked: str | None = None
    end_evidence = "not_established"
    down_steps = 0
    said_top = 0
    restored: bool | None = None
    area = args.area
    coverage = None
    entries: tuple = ()

    try:
        with connect_game_webview() as session:
            walker = Walker(session, settle_seconds=args.settle_seconds, frames=frames)
            original = walker.capture("00_original")
            if args.switch_to is not None:
                target = args.switch_to - 1
                outcome, selected = walker.select_tab(original, target)
                walker.events.append(
                    {"phase": "select_tab", "target": target, "outcome": outcome}
                )
                if outcome not in {"selected", "already_selected"} or selected is None:
                    wanted = (
                        DEFAULT_TAB_AREAS[target]
                        if 0 <= target < len(DEFAULT_TAB_AREAS)
                        else "not a known area"
                    )
                    print(
                        f"WALK_BLOCKED: tab {args.switch_to} ({wanted}) was not "
                        f"selected: {outcome}",
                        file=sys.stderr,
                    )
                    return 2
                original = selected
            if not original.rows:
                if original.tab is None:
                    print(
                        "WALK_FAILED: no rows and no identifiable tab; the expedition "
                        "list is probably not open",
                        file=sys.stderr,
                    )
                    return 1
                area_name = (
                    DEFAULT_TAB_AREAS[original.tab]
                    if original.tab < len(DEFAULT_TAB_AREAS)
                    else "unknown area"
                )
                print(
                    f"empty: tab {original.tab + 1}/{original.tabs.count} ({area_name}) "
                    "shows no expeditions; this account has unlocked nothing here",
                    flush=True,
                )
                return 0
            if original.tab is None:
                print(
                    "WALK_FAILED: the selected area tab is not identifiable; "
                    "refusing to file rows under an area",
                    file=sys.stderr,
                )
                return 1
            tab = original.tab
            tab_label = (
                DEFAULT_TAB_AREAS[tab] if 0 <= tab < len(DEFAULT_TAB_AREAS) else "unknown"
            )
            print(
                f"start: tab {tab + 1}/{original.tabs.count} ({tab_label}), "
                f"{len(original.rows)} rows, "
                f"PREV {'present' if original.scroll_up else 'absent'}",
                flush=True,
            )

            # Phase 0 - climb to the true top. The list may already be scrolled,
            # and walking down from a mid-list page would skip the rows above it.
            current = original
            while said_top < args.max_steps:
                outcome, moved = walker.step(
                    current, direction="up", expected_tab=tab, tag=f"top_{said_top:02d}"
                )
                walker.events.append({"phase": "to_top", "outcome": outcome})
                if outcome == "blocked_at_top":
                    break
                if outcome != "moved" or moved is None:
                    print(f"  to-top stop: {outcome}", flush=True)
                    if outcome not in {"blocked_at_top", "no_movement"}:
                        blocked = f"to_top_{outcome}"
                    break
                current = moved
                said_top += 1
            top = current
            print(
                f"top reached after {said_top} step(s), "
                f"{len(top.rows)} rows, NEXT {'present' if top.scroll_down else 'absent'}",
                flush=True,
            )
            if blocked is not None:
                print(f"WALK_BLOCKED: {blocked}", file=sys.stderr)
                return 2

            index.add_page(top)
            if args.label_first:
                if len(args.label_first) != len(top.rows):
                    print(
                        f"LABEL_COUNT_MISMATCH: {len(args.label_first)} labels for "
                        f"{len(top.rows)} visible rows on the top page",
                        file=sys.stderr,
                    )
                    return 2
                for row_index, expedition_id in enumerate(args.label_first, start=1):
                    index.label_row(0, row_index, expedition_id)

            # Phase 1 - descend, indexing every page.
            current = top
            for step in range(1, args.max_steps + 1):
                outcome, moved = walker.step(
                    current, direction="down", expected_tab=tab, tag=f"down_{step:02d}"
                )
                walker.events.append({"phase": "down", "outcome": outcome})
                if outcome != "moved" or moved is None:
                    print(f"  down stop: {outcome}", flush=True)
                    if outcome not in {"blocked_no_measured_control", "blocked_at_end"}:
                        blocked = f"down_{outcome}"
                    end_evidence = (
                        "next_chevron_gone_after_descent"
                        if outcome == "blocked_no_measured_control"
                        else "explicit"
                    )
                    break
                try:
                    shift, support = align_pages(current, moved)
                except ExpeditionListError as error:
                    blocked = f"align_failed: {error}"
                    print(f"  down stop: {blocked}", flush=True)
                    break
                print(
                    f"  down ok: shift {shift}, support {support}, rows {len(moved.rows)}",
                    flush=True,
                )
                index.add_page(moved)
                current = moved
                down_steps += 1
            else:
                blocked = f"max_steps_reached({args.max_steps})"
                print(f"  {blocked}", flush=True)

            boundary = classify_boundary(current)
            print(f"bottom: boundary {boundary.state.value} ({boundary.reason})", flush=True)

            # Phase 2 - return: up to the top, then down what phase 0 climbed,
            # which is exactly the page the session started on.
            if not args.stay:
                for step in range(1, down_steps + 1):
                    outcome, moved = walker.step(
                        current, direction="up", expected_tab=tab, tag=f"back_up_{step:02d}"
                    )
                    walker.events.append({"phase": "back_to_top", "outcome": outcome})
                    if outcome != "moved" or moved is None:
                        print(f"  back-to-top stop: {outcome}", flush=True)
                        if blocked is None:
                            blocked = f"climb_stopped_early: {outcome}"
                        break
                    current = moved
                for step in range(1, said_top + 1):
                    outcome, moved = walker.step(
                        current, direction="down", expected_tab=tab, tag=f"back_down_{step:02d}"
                    )
                    walker.events.append({"phase": "back_to_start", "outcome": outcome})
                    if outcome != "moved" or moved is None:
                        print(f"  back-to-start stop: {outcome}", flush=True)
                        if blocked is None:
                            blocked = f"return_stopped_early: {outcome}"
                        break
                    current = moved
                restored = same_view(original, current)
                print(f"restored: {restored}", flush=True)
                if not restored and blocked is None:
                    blocked = "not_restored"
    except (ExpeditionListError, ValueError, OSError) as error:
        print(f"WALK_FAILED: {error}", file=sys.stderr)
        return 1

    if args.label_unbound:
        try:
            index.label_positions(tuple(args.label_unbound))
        except ExpeditionListError as error:
            print(f"LABEL_REJECTED: {error}", file=sys.stderr)
            return 2

    entries = index.entries(DEFAULT_CATALOG.name_of)
    observed = tuple(entry.expedition_id for entry in entries)
    from_tab = DEFAULT_TAB_AREAS[tab] if 0 <= tab < len(DEFAULT_TAB_AREAS) else None
    from_numbers = DEFAULT_CATALOG.area_of_numbers(observed)
    if from_tab and from_numbers and from_tab != from_numbers:
        print(
            f"AREA_DISAGREEMENT: the tab index says {from_tab} but the numbers say "
            f"{from_numbers}; one of the two is wrong, so coverage is not reported",
            file=sys.stderr,
        )
        from_numbers = None
        if area is None:
            area = None
    if area is None:
        area = from_tab or from_numbers
    if area is not None and area not in DEFAULT_CATALOG.areas():
        print(
            f"COVERAGE_SKIPPED: {area} is not in the reference catalogue "
            f"({DEFAULT_CATALOG.source}), so there is nothing to compare against",
            file=sys.stderr,
        )
        area = None
    if area is not None:
        try:
            coverage = DEFAULT_CATALOG.coverage(observed, area=area)
        except ValueError as error:
            print(f"CATALOG_QUERY_INVALID: {error}", file=sys.stderr)

    print()
    print("=== every row of this tab ===")
    header = f"{'id':>4}  {'name':<16} {'page':>4} {'visible':>7}  center"
    print(header)
    print("-" * len(header))
    for entry in entries:
        center = "-" if entry.center is None else f"({entry.center[0]}, {entry.center[1]:.0f})"
        print(
            f"{entry.expedition_id:>4}  {entry.name or '-':<16} "
            f"{entry.scroll_page:>4} {str(entry.visible):>7}  {center}"
        )

    unbound_positions = index.unbound_positions()
    clipped = index.unbound_rows
    if unbound_positions:
        print()
        print("=== positions that still need a label ===")
        for tab_index, position in unbound_positions:
            where = "unknown tab" if tab_index is None else f"tab {tab_index + 1}"
            print(f"  {where} position {position} (0-based, {position + 1} on screen)")
        print("  supply them with --label-unbound, bottom-most last")
    elif clipped:
        print()
        print(
            f"note: {len(clipped)} row(s) stayed unresolved because the viewport clipped "
            "them at a page edge; every position is resolved from another page"
        )

    print()
    print("=== walk summary ===")
    print(f"clicks sent    : {walker.clicks}")
    print(f"pages indexed  : {len(index.pages)}")
    print(f"rows named     : {len(entries)}")
    print(f"down steps     : {down_steps}")
    print(f"bottom proof   : {boundary.state.value} ({boundary.reason})")
    print(f"end evidence   : {end_evidence}")
    print(f"restored       : {restored}")
    print(f"blocked        : {blocked or 'no'}")

    if coverage is not None:
        bottom_proven = boundary.state is BoundaryState.END_CONFIRMED
        print()
        print("=== catalogue coverage (wiki ceiling, not this account) ===")
        print(f"  area     : {coverage.area}")
        print(f"  observed : {len(coverage.observed)}")
        print(f"  missing  : {', '.join(coverage.missing) if coverage.missing else 'none'}")
        print(f"  extra    : {', '.join(coverage.foreign) if coverage.foreign else 'none'}")
        if bottom_proven and coverage.missing:
            print(
                "  note     : the bottom was proven, so 'missing' here means NOT "
                "UNLOCKED by this account - not 'not scanned'"
            )
        elif bottom_proven:
            print("  note     : this account has every expedition the area defines")
        else:
            # Claiming the stronger reading here would be a lie: a full last page
            # without a measured NEXT is exactly the case where "locked" and
            # "never reached" look identical.
            print(
                f"  note     : the bottom was NOT proven ({boundary.reason}), so "
                "'missing' is 'not unlocked OR not reached' - pixels cannot tell "
                "the two apart"
            )
        print(f"  source   : {DEFAULT_CATALOG.source} ({DEFAULT_CATALOG.retrieved_on})")

    payload = {
        "tab": tab,
        "tab_label": from_tab,
        "tab_count": original.tabs.count,
        "tab_scores": list(original.tabs.scores),
        "tab_switch": args.switch_to,
        "steps_to_top": said_top,
        "pages": len(index.pages),
        "down_steps": down_steps,
        "clicks": walker.clicks,
        "boundary": {"state": boundary.state.value, "reason": boundary.reason},
        "bottom_proven": boundary.state is BoundaryState.END_CONFIRMED,
        "end_evidence": end_evidence,
        "restored": restored,
        "blocked": blocked,
        "display_order_check": check_display_order(observed),
        "events": walker.events,
        "rows": [entry.to_dict() for entry in entries],
        "unbound_positions": list(unbound_positions),
        "clipped_rows": [{"page": page, "row": row} for page, row in clipped],
        "catalog": (
            None
            if coverage is None
            else {
                "area": coverage.area,
                "observed": list(coverage.observed),
                "missing": list(coverage.missing),
                "extra": list(coverage.foreign),
                "complete": coverage.is_complete,
                "source": DEFAULT_CATALOG.source,
                "retrieved_on": DEFAULT_CATALOG.retrieved_on,
            }
        ),
        "frames": str(frames),
    }
    if args.index:
        save_index(args.index, index)
        print(f"\nindex written to {args.index}")
    if args.json_out:
        args.json_out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"json written to {args.json_out}")

    if blocked is not None:
        return 2
    if unbound_positions:
        return 2
    if not entries:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
