"""Read-only: plan a fleet's 編成 completion from the dock.

Uses only the Poi read tools (``poi_get`` / ``poi_lookup``). It never clicks,
never dispatches, and never writes game state; applying a plan is a separate,
gated step.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from kancollebot.adapters.poi.dock_reader import read_dock_and_fleets  # noqa: E402
from kancollebot.expeditions.dock_composition_planner import (  # noqa: E402
    DockCompositionDecision,
    plan_dock_composition,
)
from kancollebot.expeditions.mission_eligibility import (  # noqa: E402
    MissionCompositionRequirement,
)

DEFAULT_REQUIREMENTS = {
    2: MissionCompositionRequirement(1, 2, 1),
    3: MissionCompositionRequirement(3, 3, 3),
}


def _emit(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, sort_keys=True))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fleet", type=int, required=True, help="target fleet ID")
    parser.add_argument("--mission-id", type=int, default=None)
    parser.add_argument("--minimum-ships", type=int, default=None)
    parser.add_argument("--flagship-level", type=int, default=None)
    parser.add_argument(
        "--allowed-types",
        type=str,
        default=None,
        help="comma-separated allowed api_stype values; omit for no restriction",
    )
    parser.add_argument("--poi-endpoint", default="http://127.0.0.1:12450/mcp")
    return parser.parse_args(argv)


def _requirement(args: argparse.Namespace) -> MissionCompositionRequirement:
    default = DEFAULT_REQUIREMENTS.get(args.fleet)
    if default is None:
        if args.minimum_ships is None or args.flagship_level is None:
            raise ValueError(
                f"fleet {args.fleet} has no default requirement; pass "
                "--minimum-ships and --flagship-level"
            )
        mission_id, minimum_ships, flagship_level = (
            args.mission_id if args.mission_id is not None else 1,
            args.minimum_ships,
            args.flagship_level,
        )
    else:
        mission_id = args.mission_id if args.mission_id is not None else default.mission_id
        minimum_ships = (
            args.minimum_ships if args.minimum_ships is not None else default.minimum_ships
        )
        flagship_level = (
            args.flagship_level
            if args.flagship_level is not None
            else default.minimum_flagship_level
        )
    allowed = None
    if args.allowed_types:
        allowed = tuple(
            int(value) for value in args.allowed_types.split(",") if value.strip()
        )
    return MissionCompositionRequirement(mission_id, minimum_ships, flagship_level, allowed)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        requirement = _requirement(args)
    except (ValueError, TypeError) as exc:
        _emit("STOPPED_FAIL_CLOSED", error_type=type(exc).__name__, error=str(exc))
        return 2

    try:
        reading = read_dock_and_fleets(poi_endpoint=args.poi_endpoint)
    except Exception as exc:  # noqa: BLE001 - fail closed on any read error
        _emit("STOPPED_FAIL_CLOSED", error_type=type(exc).__name__, error=str(exc))
        return 2

    _emit(
        "DOCK_READ_OK",
        fleet_count=len(reading.fleets),
        ship_count=reading.ship_count,
        dock_ship_count=len(reading.dock),
        read_only=True,
    )
    fleet = next((item for item in reading.fleets if item.fleet_id == args.fleet), None)
    if fleet is None:
        _emit("STOPPED_FAIL_CLOSED", error_type="UnknownFleet", error=f"fleet {args.fleet} not observed")
        return 2

    plan = plan_dock_composition(
        reading.composition(args.fleet), requirement, reading.dock
    )
    _emit(
        "COMPOSITION_PLAN",
        fleet_id=args.fleet,
        mission_id=requirement.mission_id,
        decision=plan.decision.value,
        actionable=plan.actionable,
        reason=plan.reason,
        action_count=len(plan.actions),
    )
    for index, action in enumerate(plan.actions, start=1):
        _emit(
            "COMPOSITION_ACTION",
            index=index,
            kind=action.kind.value,
            ship_id=action.ship_id,
            slot=action.slot,
            master_id=action.master_id,
            reason=action.reason,
        )
    if plan.resulting_composition is not None:
        _emit(
            "COMPOSITION_RESULT",
            ship_ids=list(plan.resulting_composition.ship_ids),
            ship_levels=list(plan.resulting_composition.ship_levels),
            ship_types=list(plan.resulting_composition.ship_types),
        )
    _emit(
        "PLAN_DONE",
        decision=plan.decision.value,
        requires_ui_execution=plan.actionable,
    )
    return 0 if plan.decision in {
        DockCompositionDecision.NO_CHANGE_NEEDED,
        DockCompositionDecision.PLAN_AVAILABLE,
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())
