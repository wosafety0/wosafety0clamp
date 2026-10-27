"""Read-only preflight for the local Poi CDP target and viewport.

This command opens a loopback CDP WebSocket and reads layout metrics only.
It never sends mouse/keyboard input or evaluates JavaScript.
"""
from __future__ import annotations

import argparse
import json

from kancollebot.adapters.poi.cdp_websocket import connect_poi_session


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:9222")
    parser.add_argument("--title-hint", default="poi")
    parser.add_argument("--url-hint", default="")
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args()

    try:
        with connect_poi_session(
            args.endpoint,
            title_hint=args.title_hint,
            url_hint=args.url_hint,
            timeout=args.timeout,
        ) as session:
            metrics = session.transport.send("Page.getLayoutMetrics", {})
            viewport = metrics.get("cssVisualViewport") or metrics.get("visualViewport")
            if not isinstance(viewport, dict):
                raise RuntimeError("CDP did not provide viewport dimensions")
            width, height = viewport.get("clientWidth"), viewport.get("clientHeight")
            if (
                isinstance(width, bool) or isinstance(height, bool)
                or not isinstance(width, (int, float)) or not isinstance(height, (int, float))
                or width <= 0 or height <= 0
            ):
                raise RuntimeError("CDP viewport dimensions are invalid")
            report = {
                "status": "PASS_READ_ONLY_CDP_PREFLIGHT",
                "target": {
                    "id": session.target.target_id,
                    "title": session.target.title,
                    "url": session.target.url,
                },
                "viewport_css_pixels": {"width": width, "height": height},
                "mouse_input_sent": False,
                "javascript_evaluated": False,
                "gameplay_action_sent": False,
                "mvp_accepted": False,
            }
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
    except Exception as exc:
        print(json.dumps({
            "status": "FAILED",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "mouse_input_sent": False,
            "javascript_evaluated": False,
            "gameplay_action_sent": False,
            "mvp_accepted": False,
        }, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
