"""Walk the expedition list and record what each mission's panel looks like.

The dispatch path can only aim a click at an expedition it can later *recognise*.
The row position says where to click; the detail panel is the only thing that says
what the click actually selected. This tool builds that second half of the pair:
for every row of every area tab it clicks the row, reads the mission-name band from
the panel, and stores it under the expedition number the position index binds to
that row.

What it clicks, and nothing else:

* 出撃, then 遠征 - the two navigation clicks that open the list;
* area-tab cells, whose receipt is the orange label moving;
* the measured gold chevrons that page the list;
* row name bands (x=350), which open the read-only detail preview;
* at the end, 母港 if ``--return-home`` is given.

It never presses 決定 or 遠征開始, so no expedition can be started, cancelled or
otherwise changed by a walk. Every click is either measured (tab cell, chevron) or
a row whose identity comes from the position index; a row whose position is
unbound is reported and skipped rather than guessed.

The output is per-account state, like the position index: which expeditions are
unlocked decides which rows exist at all. It therefore carries the account label
read from the client header, and the walk is the only thing that can produce it.
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
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from kancollebot.calibration import (  # noqa: E402
    DEFAULT_CATALOG,
    DEFAULT_DETAIL_PATH,
    DEFAULT_POSITION_INDEX_PATH,
    DEFAULT_TAB_AREAS,
    ExpeditionDetailError,
    ExpeditionDetailIndex,
    ExpeditionListError,
    ExpeditionRowError,
    align_pages,
    decode_png,
    encode_png,
    load_detail_index,
    load_position_index,
    plan_scroll_step,
    scan_page,
    stack_vertically,
)

FINGERPRINTS_PATH = (
    ROOT / "src" / "kancollebot" / "adapters" / "poi" / "ooi_1200x720_fingerprints.json"
)
SORTIE_POINT = (280, 425)
EXPEDITION_MODE_POINT = (1000, 360)
HOME_PORT_POINT = (50, 50)
MOUSE_PARK = (1195, 715)


class WalkError(RuntimeError):
    """The walk cannot continue without guessing; stop before any further click."""


def _load_document() -> dict[str, object]:
    return json.loads(FINGERPRINTS_PATH.read_text(encoding="utf-8"))


class Walk:
    def __init__(self, session, *, settle: float, tabs: tuple[int, ...], index, details,
                 out_path: Path, sheet_dir: Path | None, crop_dir: Path | None,
                 label: str | None, max_scrolls: int, return_home: bool) -> None:
        self.session = session
        self.settle = settle
        self.tabs = tabs
        self.index = index
        self.details = details
        self.out_path = out_path
        self.sheet_dir = sheet_dir
        self.crop_dir = crop_dir
        self.label = label
        self.max_scrolls = max_scrolls
        self.return_home = return_home
        self.document = _load_document()
        self.walked: dict[str, str] = {}
        self._scratch = Path(tempfile.mkdtemp(prefix="walk_detail_")) / "frame.png"

    # -- pixels -----------------------------------------------------------
    def capture(self) -> str:
        self.session.transport.send(
            "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": MOUSE_PARK[0], "y": MOUSE_PARK[1]}
        )
        time.sleep(0.28)
        result = self.session.transport.send("Page.captureScreenshot", {"format": "png"})
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, str) or not data:
            raise WalkError("CDP did not return screenshot data")
        return data

    def image(self, payload: str):
        self._scratch.write_bytes(base64.b64decode(payload))
        return decode_png(self._scratch)

    def scan(self):
        return scan_page(self.image(self.capture()))

    def state(self, payload: str) -> tuple[str, dict[str, float]]:
        from kancollebot.adapters.poi.visual_fingerprint import classify_screenshot_fingerprints

        return classify_screenshot_fingerprints(payload, self.document)

    def require_state(self, expected: str, *, phase: str, timeout: float = 45.0,
                      grace: float = 4.0) -> None:
        """Two consecutive matches, retried through the transition.

        A screen change here fades through black frames *and* keeps the old
        screen visible for a moment, so neither a single UNKNOWN nor the previous
        state is a failure on its own. What is a failure is another state still
        showing after the grace period: that means the click went somewhere else,
        and the walk stops before its next one.
        """
        started = time.monotonic()
        deadline = started + timeout
        consecutive = 0
        while time.monotonic() < deadline:
            observed, _scores = self.state(self.capture())
            if observed == expected:
                consecutive += 1
                if consecutive >= 2:
                    return
            else:
                consecutive = 0
                if observed != "UNKNOWN" and time.monotonic() - started > grace:
                    raise WalkError(
                        f"{phase}: still {observed} {grace:.0f}s after the click, "
                        f"expected {expected}; stopping"
                    )
            time.sleep(0.45)
        raise WalkError(f"{phase}: expected {expected}, never observed two consecutive frames")

    # -- input ------------------------------------------------------------
    def click(self, point: tuple[int, int], label: str) -> None:
        from kancollebot.adapters.poi.cdp_input import RecordedPoint

        self.session.input.click(RecordedPoint(point[0], point[1], label))
        time.sleep(self.settle)

    def open_list(self) -> None:
        self.require_state("HOME_PORT", phase="walk start")
        self.click(SORTIE_POINT, "sortie")
        self.require_state("SORTIE_MENU", phase="after 出撃")
        self.click(EXPEDITION_MODE_POINT, "expedition-mode")
        self.require_state("EXPEDITION_LIST", phase="after 遠征")

    # -- list mechanics ---------------------------------------------------
    def select_tab(self, page, tab: int):
        if page.tab == tab:
            return page
        if page.tab is None:
            raise WalkError("the selected area tab is not readable, so it cannot be switched")
        centers = page.tabs.centers
        if not 0 <= tab < len(centers):
            raise WalkError(f"tab {tab + 1} is outside the {len(centers)} tabs on screen")
        self.click(centers[tab], f"expedition-tab-{tab + 1}")
        after = self.scan()
        if after.tab != tab:
            raise WalkError(f"the orange label did not move to tab {tab + 1} (now {after.tab})")
        return after

    def climb(self, page):
        steps = 0
        while page.scroll_up is not None:
            if steps >= self.max_scrolls:
                raise WalkError(f"the list never reached its top in {steps} steps")
            proposal = plan_scroll_step(page, direction="up")
            if proposal.action != "click_measured_control" or proposal.point is None:
                raise WalkError(f"refusing to scroll up without a measured control: {proposal.detail}")
            self.click(proposal.point, "expedition-list-up")
            steps += 1
            page = self.scan()
            if not page.rows:
                raise WalkError("the list went blank while climbing")
        if not page.rows:
            raise WalkError("the list is empty")
        return page

    def page_down(self, page):
        proposal = plan_scroll_step(page, direction="down")
        if proposal.action != "click_measured_control" or proposal.point is None:
            raise WalkError(f"refusing to scroll down without a measured control: {proposal.detail}")
        self.click(proposal.point, "expedition-list-down")
        after = self.scan()
        if after.tab is not None and page.tab is not None and after.tab != page.tab:
            raise WalkError(f"the area tab changed while paging ({page.tab} -> {after.tab})")
        try:
            shift, _support = align_pages(page, after)
        except ExpeditionListError as error:
            raise WalkError(f"cannot place the paged list: {error}") from error
        if shift < 1:
            raise WalkError(f"the list moved by {shift} rows, which cannot be right")
        return after, shift

    # -- the actual walk --------------------------------------------------
    def walk_tab(self, tab: int) -> list[str]:
        page = self.select_tab(self.scan(), tab)
        page = self.climb(page)
        offset = 0
        captured: list[str] = []
        scrolls = 0
        while True:
            # The row geometry is read *before* any row is clicked: opening the
            # detail panel highlights one row, and measuring rows afterwards would
            # carry that highlight into the next page's alignment vote.
            plan: list[tuple[int, str, float]] = []
            for row in page.rows:
                position = offset + row.index - 1
                expedition_id = self.index.resolve(tab, position)
                if expedition_id is None:
                    print(f"  tab {tab + 1} position {position}: unbound, skipped")
                    continue
                if expedition_id in self.walked:
                    continue
                if row.bottom - row.top < 12:
                    print(f"  tab {tab + 1} position {position} ({expedition_id}): clipped, skipped")
                    continue
                plan.append((position, expedition_id, row.center))
            for position, expedition_id, center in plan:
                self.click((350, int(round(center))), f"row-{expedition_id}")
                payload = self.capture()
                record = self.details.bind(expedition_id, payload)
                self.walked[expedition_id] = record.captured_at
                captured.append(expedition_id)
                name = DEFAULT_CATALOG.name_of(expedition_id) or "?"
                print(f"  tab {tab + 1} position {position} -> {expedition_id} {name}")
                if self.crop_dir is not None:
                    self._write_crop(expedition_id, payload)
            if page.scroll_down is None:
                break
            if scrolls >= self.max_scrolls:
                raise WalkError(f"gave up paging tab {tab + 1} after {scrolls} scrolls")
            page, shift = self.page_down(page)
            offset += shift
            scrolls += 1
        self._write_sheet(tab, captured)
        return captured

    def _write_crop(self, expedition_id: str, payload: str) -> None:
        assert self.crop_dir is not None
        self.crop_dir.mkdir(parents=True, exist_ok=True)
        x, y, width, height = 850, 188, 190, 46
        encode_png(
            self.crop_dir / f"{expedition_id}.png",
            self.image(payload).crop(x, y, x + width, y + height),
        )

    def _write_sheet(self, tab: int, numbers: list[str]) -> None:
        if self.sheet_dir is None or not numbers:
            return
        self.sheet_dir.mkdir(parents=True, exist_ok=True)
        images = [
            decode_png(self.crop_dir / f"{number}.png")
            for number in numbers
            if self.crop_dir is not None and (self.crop_dir / f"{number}.png").exists()
        ]
        if images:
            encode_png(self.sheet_dir / f"tab{tab + 1}.png", stack_vertically(images, gap=3))

    def save(self) -> None:
        self.details.label = self.label
        self.details.save(self.out_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=DEFAULT_POSITION_INDEX_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_DETAIL_PATH)
    parser.add_argument("--tabs", default="1-6", help="1-based tabs to walk, e.g. '1,4' or '1-6'")
    parser.add_argument("--settle-seconds", type=float, default=0.5)
    parser.add_argument("--max-scrolls", type=int, default=24)
    parser.add_argument("--label", default=None, help="account label to record with the asset")
    parser.add_argument("--crop-dir", type=Path, default=None, help="write each name band here")
    parser.add_argument("--sheet-dir", type=Path, default=None, help="stack each tab's bands here")
    parser.add_argument("--return-home", action="store_true", help="return to Home Port at the end")
    parser.add_argument("--allow-input", action="store_true", required=True,
                        help="required: the walk clicks rows, tabs and chevrons")
    args = parser.parse_args(argv)

    tabs: list[int] = []
    for part in args.tabs.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, _, end = part.partition("-")
            tabs.extend(range(int(start) - 1, int(end)))
        else:
            tabs.append(int(part) - 1)
    tabs = [tab for tab in tabs if 0 <= tab < len(DEFAULT_TAB_AREAS)]
    if not tabs:
        print("REFUSED: --tabs selected no area tab", file=sys.stderr)
        return 2

    try:
        index = load_position_index(args.index)
    except (ExpeditionListError, OSError, ValueError) as error:
        print(f"INDEX_UNREADABLE: {error}", file=sys.stderr)
        return 1
    if not index.bindings:
        print(f"INDEX_EMPTY: {args.index} holds no position bindings", file=sys.stderr)
        return 1
    try:
        details = load_detail_index(args.out)
    except (ExpeditionDetailError, OSError, ValueError) as error:
        print(f"DETAILS_UNREADABLE: {error}", file=sys.stderr)
        return 1

    from kancollebot.adapters.poi.cdp_websocket import connect_game_webview

    with connect_game_webview(timeout=5.0) as session:
        walk = Walk(
            session,
            settle=args.settle_seconds,
            tabs=tuple(tabs),
            index=index,
            details=details,
            out_path=args.out,
            sheet_dir=args.sheet_dir,
            crop_dir=args.crop_dir,
            label=args.label,
            max_scrolls=args.max_scrolls,
            return_home=args.return_home,
        )
        try:
            walk.open_list()
            for tab in walk.tabs:
                print(f"tab {tab + 1}/7 {DEFAULT_TAB_AREAS[tab]}")
                walk.walk_tab(tab)
                walk.save()
        except (WalkError, ExpeditionRowError, ExpeditionDetailError, ExpeditionListError) as error:
            walk.save()
            print(f"WALK_BLOCKED: {error}", file=sys.stderr)
            print(f"partial asset saved with {len(details)} entries", file=sys.stderr)
            return 2
        finally:
            if args.return_home:
                try:
                    walk.click(HOME_PORT_POINT, "home-port")
                    time.sleep(1.0)
                except Exception as error:  # noqa: BLE001 - recovery is best effort
                    print(f"home port click failed: {type(error).__name__}", file=sys.stderr)

    walk.save()
    print(json.dumps({"entries": len(details), "out": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
