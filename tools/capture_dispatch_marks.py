"""Capture the two controls the dispatch path must recognise before it clicks.

``dispatch_marks.json`` holds references for:

* the 決定 control in the detail panel - its presence is what separates "a panel
  opened" from "this panel can be confirmed", because an expedition that is
  already running replaces 決定 with 遠征中止/帰還命令;
* each fleet tab on the fleet-selection screen - the only pixel evidence of
  which fleet the game thinks is selected before the irreversible start click.

The bootstrap run cannot verify what it stores, because verification *is* what it
is storing; it therefore prints every capture and relies on the operator having
the screen in front of them. Every later run verifies against the stored marks
instead and refuses when they do not match, which is also how a recapture after a
client update is proven good or bad.

It stops one click short of the irreversible action: no 遠征開始 is ever pressed,
and each fleet tab is selected only to photograph it.
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
    DECISION_POINT,
    DEFAULT_MARKS_PATH,
    DEFAULT_POSITION_INDEX_PATH,
    FLEET_TAB_POINTS,
    ExpeditionDetailError,
    ExpeditionDetailIndex,
    ExpeditionListError,
    ExpeditionRowError,
    ExpeditionListSession,
    decode_png,
    load_detail_index,
    load_marks,
    load_position_index,
    scan_page,
)

FINGERPRINTS_PATH = (
    ROOT / "src" / "kancollebot" / "adapters" / "poi" / "ooi_1200x720_fingerprints.json"
)
SORTIE_POINT = (280, 425)
EXPEDITION_MODE_POINT = (1000, 360)
HOME_PORT_POINT = (50, 50)
MOUSE_PARK = (1195, 715)


class MarksError(RuntimeError):
    """The capture sequence cannot continue without guessing."""


class Session:
    def __init__(self, cdp, *, settle: float) -> None:
        self.cdp = cdp
        self.settle = settle
        self.document = json.loads(FINGERPRINTS_PATH.read_text(encoding="utf-8"))
        self._scratch = Path(tempfile.mkdtemp(prefix="marks_")) / "frame.png"

    def capture(self) -> str:
        self.cdp.transport.send(
            "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": MOUSE_PARK[0], "y": MOUSE_PARK[1]}
        )
        time.sleep(0.28)
        result = self.cdp.transport.send("Page.captureScreenshot", {"format": "png"})
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, str) or not data:
            raise MarksError("CDP did not return screenshot data")
        return data

    def scan(self):
        self._scratch.write_bytes(base64.b64decode(self.capture()))
        return scan_page(decode_png(self._scratch))

    def click(self, point: tuple[int, int], label: str) -> None:
        from kancollebot.adapters.poi.cdp_input import RecordedPoint

        self.cdp.input.click(RecordedPoint(point[0], point[1], label))
        time.sleep(self.settle)

    def classify(self, payload: str) -> str:
        from kancollebot.adapters.poi.visual_fingerprint import classify_screenshot_fingerprints

        return classify_screenshot_fingerprints(payload, self.document)[0]

    def require_state(self, expected: str, *, phase: str, timeout: float = 45.0,
                      grace: float = 4.0) -> None:
        started = time.monotonic()
        consecutive = 0
        while time.monotonic() < started + timeout:
            observed = self.classify(self.capture())
            if observed == expected:
                consecutive += 1
                if consecutive >= 2:
                    return
            else:
                consecutive = 0
                if observed != "UNKNOWN" and time.monotonic() - started > grace:
                    raise MarksError(f"{phase}: still {observed}, expected {expected}")
            time.sleep(0.45)
        raise MarksError(f"{phase}: expected {expected}, never observed twice")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", default="04",
                        help="expedition number whose panel is used; must not be running")
    parser.add_argument("--index", type=Path, default=DEFAULT_POSITION_INDEX_PATH)
    parser.add_argument("--details", type=Path, default=None,
                        help="detail index used to confirm the panel (defaults to the shipped one)")
    parser.add_argument("--out", type=Path, default=DEFAULT_MARKS_PATH)
    parser.add_argument("--label", default=None)
    parser.add_argument("--settle-seconds", type=float, default=0.6)
    parser.add_argument("--bootstrap", action="store_true",
                        help="store references without verifying them (first run only)")
    parser.add_argument("--allow-input", action="store_true", required=True)
    args = parser.parse_args(argv)

    index = load_position_index(args.index)
    if not index.bindings:
        print(f"INDEX_EMPTY: {args.index}", file=sys.stderr)
        return 1
    details_path = args.details
    if details_path is None:
        from kancollebot.calibration import DEFAULT_DETAIL_PATH

        details_path = DEFAULT_DETAIL_PATH
    try:
        details = load_detail_index(details_path)
    except (ExpeditionDetailError, OSError, ValueError) as error:
        print(f"DETAILS_UNREADABLE: {error}", file=sys.stderr)
        return 1
    if details.reference(args.mission) is None:
        print(f"DETAIL_MISSING: no reference for {args.mission} in {details_path}", file=sys.stderr)
        return 1
    try:
        marks = load_marks(args.out)
    except (ExpeditionDetailError, OSError, ValueError) as error:
        print(f"MARKS_UNREADABLE: {error}", file=sys.stderr)
        return 1
    if args.bootstrap and (marks.decision is not None or marks.fleet_tabs):
        print("REFUSED: --bootstrap would overwrite existing references; delete the file first",
              file=sys.stderr)
        return 2

    from kancollebot.adapters.poi.cdp_websocket import connect_game_webview

    with connect_game_webview(timeout=5.0) as cdp:
        session = Session(cdp, settle=args.settle_seconds)
        locator = ExpeditionListSession(
            scan=session.scan,
            click=lambda point, label: session.click(point, label),
            index=index,
            settle_seconds=args.settle_seconds,
        )
        try:
            session.require_state("HOME_PORT", phase="marks start")
            session.click(SORTIE_POINT, "sortie")
            session.require_state("SORTIE_MENU", phase="after 出撃")
            session.click(EXPEDITION_MODE_POINT, "expedition-mode")
            session.require_state("EXPEDITION_LIST", phase="after 遠征")

            location = locator.locate(args.mission)
            print(f"row {args.mission} at ({location.point[0]}, {location.point[1]:.0f}) "
                  f"tab {location.tab + 1} position {location.position} offset {location.offset}")
            session.click((location.point[0], int(round(location.point[1]))), f"row-{args.mission}")
            time.sleep(0.4)
            panel = session.capture()
            match = details.require(args.mission, panel)
            print(f"panel confirmed as {match.expedition_id} at {match.score:.4f} "
                  f"(runner-up {match.runner_up} {match.runner_up_score:.4f})")

            if args.bootstrap:
                marks.capture_decision(panel)
                print(f"decision reference captured from the panel of {args.mission}")
            else:
                decision = marks.require_decision(panel)
                print(f"decision control recognised at {decision.score:.4f}")

            session.click(DECISION_POINT, "mission-confirm")
            time.sleep(1.2)

            for fleet_id in sorted(FLEET_TAB_POINTS):
                point = FLEET_TAB_POINTS[fleet_id]
                session.click(point, f"fleet-{fleet_id}-tab")
                time.sleep(0.7)
                frame = session.capture()
                if args.bootstrap:
                    marks.add_fleet_tab(fleet_id, frame)
                    print(f"fleet {fleet_id} tab reference captured")
                elif not marks.fleet_tabs.get(fleet_id):
                    marks.add_fleet_tab(fleet_id, frame)
                    print(f"fleet {fleet_id}: no reference yet, captured one")
                else:
                    check = marks.require_fleet(fleet_id, frame)
                    print(f"fleet {fleet_id} tab recognised at {check.score:.4f}")

            marks.label = args.label
            marks.save(args.out)
            print(f"saved {args.out}")
        except (MarksError, ExpeditionRowError, ExpeditionDetailError, ExpeditionListError) as error:
            print(f"MARKS_BLOCKED: {error}", file=sys.stderr)
            return 2
        finally:
            try:
                session.click(HOME_PORT_POINT, "home-port")
                time.sleep(1.0)
            except Exception as error:  # noqa: BLE001 - recovery is best effort
                print(f"home port click failed: {type(error).__name__}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
