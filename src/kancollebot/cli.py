"""Unified local runtime CLI for KancolleBot."""
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import Sequence

from kancollebot.runtime_config import REPOSITORY_ROOT


_TOOLS_DIR = REPOSITORY_ROOT / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

_COMMAND_MODULES = {
    "monitor": "run_continuous_expedition_monitor",
    "dispatch-once": "one_expedition_ui_loop",
}


def _list_tasks() -> int:
    """Read-only view of declared automation tasks and their calibration state."""
    from kancollebot.task_scope import (
        declared_task_kinds,
        describe_shared_capabilities,
        fleet_policy_for,
    )

    for kind in declared_task_kinds():
        policy = fleet_policy_for(kind)
        if policy.implemented:
            status = "IMPLEMENTED"
            fleets = str(list(policy.dispatchable_fleet_ids))
        else:
            status = "DECLARED / NOT IMPLEMENTED (fail-closed)"
            fleets = "uncalibrated - expedition policy is NOT reused"
        print(f"{kind.value:<12} {status:<42} fleets={fleets}")
    print("\nShared infrastructure (must stay task-neutral):")
    for line in describe_shared_capabilities():
        print(f"  - {line}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kancollebot",
        description="Unified entry point for KancolleBot local runtime commands.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("monitor", add_help=False, help="Run the read-only Poi expedition monitor")
    commands.add_parser(
        "dispatch-once",
        add_help=False,
        help="Run one safety-gated Fleet 3 -> mission 3 UI dispatch transaction",
    )
    commands.add_parser(
        "tasks",
        add_help=False,
        help="List declared automation tasks, their status, and shared capabilities",
    )
    args, command_args = parser.parse_known_args(argv)

    if args.command == "tasks":
        return _list_tasks()

    module = importlib.import_module(_COMMAND_MODULES[args.command])
    implementation = getattr(module, "_main_impl", None)
    if not callable(implementation):
        parser.error(f"Runtime command implementation is unavailable: {_COMMAND_MODULES[args.command]}")
    return int(implementation(command_args))


if __name__ == "__main__":
    raise SystemExit(main())
