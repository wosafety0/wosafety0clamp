"""Read-only verdict on whether Poi's game surface can be driven right now.

This tool never clicks, never reloads, and never sends input. It opens one
loopback CDP connection to the game target, sends a single read-only layout
probe, and reports whether the renderer answered.

Exit codes: 0 the game surface answered; 2 it did not (a human is needed);
1 the probe itself could not run.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:  # allow `python tools/check_poi_renderer.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kancollebot.adapters.poi.renderer_block import (  # noqa: E402
    DEFAULT_TIMEOUT_SECONDS,
    MANUAL_ACTION,
    BlockProbe,
    Surface,
    live_probe,
)


DIALOG_HEADLINE = "エラーが発生したため、ページ更新します。"


def _report(probe: BlockProbe) -> None:
    print(f"game renderer : {probe.game.value}")
    print(f"poi shell     : {probe.shell.value}")
    print(f"verdict       : {probe.reason.value}")
    print(f"input allowed : {'yes' if probe.input_allowed else 'NO'}")
    if probe.latency_seconds is not None:
        print(f"probe latency : {probe.latency_seconds:.2f}s")
    print(f"detail        : {probe.detail}")

    if probe.dialog_suspected:
        print()
        print("A blocking refresh confirmation is almost certainly on screen:")
        print(f"    {DIALOG_HEADLINE}")
        print(f"    [ 確定 ] [ 取消 ]")
        print()
        print(f"手动处理：{MANUAL_ACTION}")
        print(
            "检测可以自动化，取消不能：这个框是 Electron 把页面 confirm() 变成的"
            "主进程原生模态框，CDP 的对话框管理器根本看不到它"
            "（Page.handleJavaScriptDialog 会直接报错），它同时把游戏渲染进程整条"
            "主线程挂住。"
        )
    elif probe.game is Surface.RESPONSIVE:
        print()
        print("The renderer answered; the surface is safe to drive.")
    else:
        print()
        print("The game surface is not usable. Do not send input; check Poi by hand.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="seconds to wait for the renderer before calling it blocked",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the probe as one JSON object instead of prose",
    )
    args = parser.parse_args(argv)

    if not args.timeout > 0:
        parser.error("--timeout must be positive")

    try:
        probe = live_probe(timeout=args.timeout)
    except Exception as exc:  # noqa: BLE001 - the probe itself failed
        print(f"probe failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        import json

        print(json.dumps(probe.as_event(), ensure_ascii=False, indent=2))
    else:
        _report(probe)

    return 0 if probe.input_allowed else 2


if __name__ == "__main__":
    raise SystemExit(main())
