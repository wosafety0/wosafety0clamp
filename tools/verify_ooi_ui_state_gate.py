"""Run the production UI checkpoint gate once against the currently displayed Ooi screen.

This diagnostic may move the pointer to a neutral position to clear hover
overlays; it does not navigate, click, or write screenshot files.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from kancollebot.adapters.poi.cdp_websocket import connect_game_webview
from kancollebot.adapters.poi.ui_state_gate import UiState
from one_expedition_ui_loop import _require_ui_state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", required=True, choices=[state.value for state in UiState if state is not UiState.UNKNOWN])
    parser.add_argument(
        "--template-dir",
        type=Path,
        default=Path(os.environ["KANCOLLEBOT_TEMPLATE_DIR"]) if os.environ.get("KANCOLLEBOT_TEMPLATE_DIR") else None,
        help="Directory containing local visual templates for states without Ooi fingerprints",
    )
    args = parser.parse_args(argv)
    if args.template_dir is not None:
        if not args.template_dir.is_dir():
            print(json.dumps({"status": "FAIL_CLOSED", "expected": args.expected,
                              "error": f"template directory does not exist: {args.template_dir}"}, ensure_ascii=False))
            return 2
        os.environ["KANCOLLEBOT_TEMPLATE_DIR"] = str(args.template_dir.expanduser().resolve())
    try:
        expected = UiState(args.expected)
        with connect_game_webview() as session:
            _require_ui_state(session, expected)
    except Exception as exc:
        print(json.dumps({"status": "FAIL_CLOSED", "expected": args.expected, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({
        "status": "PASS",
        "expected": expected.value,
        "evidence": "production_checkpoint_gate",
        "screenshot_saved": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())