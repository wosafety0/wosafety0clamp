"""Read-only preflight for the current Fleet 2 / mission 1 and Fleet 3 / mission 3 plan."""
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
from kancollebot.expeditions.mission_eligibility import MissionCompositionRequirement  # noqa: E402

ASSIGNMENTS = (
    (2, MissionCompositionRequirement(1, 2, 1)),
    (3, MissionCompositionRequirement(3, 3, 3)),
)


def main() -> int:
    with connect_poi_session(timeout=3.0) as session:
        states = read_poi_fleet_states(session.transport)
    by_id = {state.fleet_id: state for state in states}
    reports = []
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
    output = {
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "Poi cached Redux snapshot via fixed read-only expression",
        "policy": "all fleet members must have complete supply metadata and be fully supplied",
        "assignments": reports,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if all(item["decision"] == "ready" for item in reports) else 2


if __name__ == "__main__":
    raise SystemExit(main())
