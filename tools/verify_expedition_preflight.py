"""Read-only preflight and flagship-exchange suggestion for Fleet 2 / 3."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from kancollebot.adapters.poi.cdp_fleet_state import read_poi_fleet_states  # noqa: E402
from kancollebot.adapters.poi.cdp_websocket import connect_poi_session  # noqa: E402
from kancollebot.expeditions.expedition_preflight import evaluate_fleet_expedition_preflight  # noqa: E402
from kancollebot.expeditions.fleet_composition_planner import plan_flagship_exchange  # noqa: E402
from kancollebot.expeditions.mission_eligibility import FleetCompositionSnapshot, MissionCompositionRequirement  # noqa: E402

ASSIGNMENTS = (
    (2, MissionCompositionRequirement(1, 2, 1)),
    (3, MissionCompositionRequirement(3, 3, 3)),
)


def _composition_snapshot(state):
    member_ids = tuple(ship_id for ship_id in state.ship_ids if ship_id > 0)
    supply = {item.ship_id: item for item in state.supply}
    if (
        len(member_ids) != len(set(member_ids))
        or set(supply) != set(member_ids)
        or len(supply) != len(state.supply)
        or any(
            supply[ship_id].level is None or supply[ship_id].ship_type is None
            for ship_id in member_ids
        )
    ):
        return None
    return FleetCompositionSnapshot(
        member_ids,
        tuple(supply[ship_id].level for ship_id in member_ids),
        tuple(supply[ship_id].ship_type for ship_id in member_ids),
    )


def main() -> int:
    with connect_poi_session(timeout=3.0) as session:
        states = read_poi_fleet_states(session.transport)
    by_id = {state.fleet_id: state for state in states}
    reports = []
    requirements = {fleet_id: requirement for fleet_id, requirement in ASSIGNMENTS}
    for fleet_id, requirement in ASSIGNMENTS:
        state = by_id.get(fleet_id)
        if state is None:
            raise RuntimeError(f"Poi snapshot does not contain Fleet {fleet_id}")
        plan = evaluate_fleet_expedition_preflight(state, requirement)
        reports.append({
            "fleet_id": fleet_id,
            "mission_id": requirement.mission_id,
            "decision": plan.decision.value,
            "reason": plan.reason,
            "ship_ids": [ship_id for ship_id in state.ship_ids if ship_id > 0],
            "mission_state": list(state.mission),
            "composition_decision": plan.composition.decision.value if plan.composition else None,
            "unsupplied_ship_ids": list(plan.unsupplied_ship_ids),
            "supply": [
                {"ship_id": item.ship_id, "level": item.level, "ship_type": item.ship_type,
                 "fuel": item.fuel, "max_fuel": item.max_fuel, "ammo": item.ammo,
                 "max_ammo": item.max_ammo, "fully_supplied": item.is_fully_supplied}
                for item in state.supply
            ],
        })

    donor = _composition_snapshot(by_id[2])
    receiver = _composition_snapshot(by_id[3])
    exchange = None
    if donor is not None and receiver is not None:
        suggestion = plan_flagship_exchange(
            donor, receiver, requirements[2], requirements[3],
        )
        exchange = {
            "decision": suggestion.decision.value,
            "donor_fleet_id": 2,
            "receiver_fleet_id": 3,
            "donor_ship_id": suggestion.donor_ship_id,
            "receiver_flagship_id": suggestion.receiver_flagship_id,
            "donor_after_ship_ids": list(suggestion.donor_after.ship_ids) if suggestion.donor_after else None,
            "donor_after_levels": list(suggestion.donor_after.ship_levels) if suggestion.donor_after else None,
            "receiver_after_ship_ids": list(suggestion.receiver_after.ship_ids) if suggestion.receiver_after else None,
            "receiver_after_levels": list(suggestion.receiver_after.ship_levels) if suggestion.receiver_after else None,
            "reason": suggestion.reason,
        }

    output = {
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "Poi cached Redux snapshot via fixed read-only expression",
        "policy": "all fleet members must have complete supply metadata and be fully supplied",
        "assignments": reports,
        "flagship_exchange_suggestion": exchange,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if all(item["decision"] == "ready" for item in reports) else 2


if __name__ == "__main__":
    raise SystemExit(main())
