"""Conservative normalization of raw Poi fleet and ship state.

This module does not perform actions. Unknown or incomplete fields remain
unknown and are rejected rather than guessed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from kancollebot.core.observed_state import ObservedState
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot


class PoiStateError(ValueError):
    """Raised when raw Poi state cannot be safely normalized."""


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PoiStateError(f"{label} must be an object keyed by numeric IDs")
    result: dict[str, Any] = {}
    for key, item in value.items():
        if not str(key).isdigit():
            raise PoiStateError(f"{label} contains a non-numeric key")
        result[str(key)] = item
    return result


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PoiStateError(f"{label} must be an integer >= {minimum}")
    return value


def normalize_poi_fleets(
    fleets_payload: Any,
    ships_payload: Any,
    *,
    observed_at: datetime,
) -> ObservedState[tuple[ExpeditionFleetSnapshot, ...]]:
    """Normalize Poi's info.fleets and info.ships into planner snapshots.

    Poi stores fleets as an array with zero-based array positions and one-based
    API IDs. Ship records are keyed by roster ID. This normalizer intentionally
    does not infer full resupply from current fuel/ammo alone: the planner's
    readiness booleans are true only when the payload explicitly exposes
    matching maximum fields. Actual Poi 12.0.1 payload compatibility remains a
    local runtime check.
    """
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise PoiStateError("observed_at must be timezone-aware")
    if not isinstance(fleets_payload, list):
        raise PoiStateError("info.fleets must be an array")
    ships = _mapping(ships_payload, "info.ships")
    snapshots: list[ExpeditionFleetSnapshot] = []

    for index, fleet in enumerate(fleets_payload):
        if not isinstance(fleet, dict):
            raise PoiStateError(f"fleet entry {index} must be an object")
        fleet_id = _integer(fleet.get("api_id", index + 1), f"fleet[{index}].api_id", minimum=1)
        raw_ship_ids = fleet.get("api_ship")
        if not isinstance(raw_ship_ids, list):
            raise PoiStateError(f"fleet {fleet_id} is missing api_ship array")
        ship_ids = tuple(
            _integer(ship_id, f"fleet {fleet_id} ship ID", minimum=1)
            for ship_id in raw_ship_ids
            if isinstance(ship_id, int) and not isinstance(ship_id, bool) and ship_id > 0
        )
        if len(ship_ids) > 6:
            raise PoiStateError(f"fleet {fleet_id} has more than six occupied slots")
        if len(set(ship_ids)) != len(ship_ids):
            raise PoiStateError(f"fleet {fleet_id} contains duplicate ship IDs")

        mission_id = None
        return_at = None
        raw_mission = fleet.get("api_mission")
        if raw_mission is not None:
            if not isinstance(raw_mission, list) or len(raw_mission) < 4:
                raise PoiStateError(f"fleet {fleet_id} api_mission has an unknown shape")
            status = _integer(raw_mission[0], f"fleet {fleet_id} mission status")
            if status > 0:
                mission_id = _integer(raw_mission[2], f"fleet {fleet_id} mission ID", minimum=1)
                raw_return = raw_mission[3]
                if isinstance(raw_return, int) and not isinstance(raw_return, bool) and raw_return > 0:
                    return_at = datetime.fromtimestamp(raw_return, tz=timezone.utc)
                # A mission without a usable return timestamp is blocked by planner.

        damaged: list[int] = []
        all_fuel_ready = bool(ship_ids)
        all_ammo_ready = bool(ship_ids)
        for ship_id in ship_ids:
            ship = ships.get(str(ship_id))
            if not isinstance(ship, dict):
                raise PoiStateError(f"fleet {fleet_id} references missing ship {ship_id}")
            now_hp = ship.get("api_nowhp")
            max_hp = ship.get("api_maxhp")
            if not isinstance(now_hp, int) or not isinstance(max_hp, int) or max_hp < 1:
                raise PoiStateError(f"ship {ship_id} has unknown HP fields")
            if now_hp < max_hp:
                damaged.append(ship_id)

            # Do not infer supply from current values alone. Only accept an
            # explicit current/max pair if a Poi fork exposes such fields.
            fuel = ship.get("api_fuel")
            fuel_max = ship.get("api_fuel_max")
            ammo = ship.get("api_bull")
            ammo_max = ship.get("api_bull_max")
            if not isinstance(fuel, int) or not isinstance(fuel_max, int):
                all_fuel_ready = False
            elif fuel < fuel_max:
                all_fuel_ready = False
            if not isinstance(ammo, int) or not isinstance(ammo_max, int):
                all_ammo_ready = False
            elif ammo < ammo_max:
                all_ammo_ready = False

        snapshots.append(ExpeditionFleetSnapshot(
            fleet_id=fleet_id,
            ship_ids=ship_ids,
            mission_id=mission_id,
            return_at=return_at,
            fuel_ready=all_fuel_ready,
            ammo_ready=all_ammo_ready,
            damaged_ship_ids=tuple(damaged),
        ))

    return ObservedState(tuple(snapshots), observed_at, "poi-redux:info.fleets+info.ships")
