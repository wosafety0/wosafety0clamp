"""Conservative normalization for Poi fleet snapshots."""

from datetime import datetime
from typing import Any

from kancollebot.core.observed_state import ObservedState
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot


class PoiStateError(ValueError):
    """Raw Poi state does not meet the normalization contract."""


def _int(value: Any, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PoiStateError(f"{label} must be an integer >= {minimum}")
    return value


def _id_map(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not str(k).isdigit() for k in value):
        raise PoiStateError(f"{label} must be an object keyed by numeric IDs")
    return {str(k): v for k, v in value.items()}


def normalize_poi_fleets(
    fleets_payload: Any,
    ships_payload: Any,
    master_ships_payload: Any,
    *,
    observed_at: datetime,
) -> ObservedState[tuple[ExpeditionFleetSnapshot, ...]]:
    """Normalize fleet, roster, and master ship records.

    The API mission timestamp is intentionally not interpreted until its slot
    and units are confirmed against a current authoritative payload.
    """
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise PoiStateError("observed_at must be timezone-aware")
    if not isinstance(fleets_payload, list):
        raise PoiStateError("info.fleets must be an array")
    ships = _id_map(ships_payload, "info.ships")
    masters = _id_map(master_ships_payload, "const.$ships")
    snapshots = []
    seen_fleet_ids: set[int] = set()
    assigned_ship_ids: set[int] = set()
    for index, fleet in enumerate(fleets_payload):
        if not isinstance(fleet, dict):
            raise PoiStateError(f"fleet entry {index} must be an object")
        fleet_id = _int(fleet.get("api_id", index + 1), "fleet api_id", 1)
        if fleet_id in seen_fleet_ids:
            raise PoiStateError(f"duplicate fleet ID {fleet_id}")
        seen_fleet_ids.add(fleet_id)
        raw_ids = fleet.get("api_ship")
        if not isinstance(raw_ids, list) or len(raw_ids) > 6:
            raise PoiStateError(f"fleet {fleet_id} has an invalid api_ship array")
        ship_ids_list = []
        for raw_id in raw_ids:
            if isinstance(raw_id, bool) or not isinstance(raw_id, int):
                raise PoiStateError(f"fleet {fleet_id} has a non-integer ship slot")
            if raw_id == -1:
                continue
            if raw_id < 1:
                raise PoiStateError(f"fleet {fleet_id} has an invalid empty-slot marker")
            ship_ids_list.append(raw_id)
        ship_ids = tuple(ship_ids_list)
        if len(set(ship_ids)) != len(ship_ids):
            raise PoiStateError(f"fleet {fleet_id} has duplicate ship IDs")

        overlap = assigned_ship_ids.intersection(ship_ids)
        if overlap:
            raise PoiStateError(
                f"ship IDs are assigned to multiple fleets: {sorted(overlap)}"
            )
        assigned_ship_ids.update(ship_ids)

        mission_id = None
        raw_mission = fleet.get("api_mission")
        if raw_mission is not None:
            if not isinstance(raw_mission, list) or len(raw_mission) < 4:
                raise PoiStateError(f"fleet {fleet_id} api_mission has an unknown shape")
            status = _int(raw_mission[0], "mission status")
            if status not in (0, 1, 2):
                raise PoiStateError(
                    f"fleet {fleet_id} has unsupported mission status {status}"
                )
            if status > 0:
                mission_id = _int(raw_mission[1], "mission ID", 1)

        damaged = []
        fuel_ready = bool(ship_ids)
        ammo_ready = bool(ship_ids)
        for ship_id in ship_ids:
            ship = ships.get(str(ship_id))
            if not isinstance(ship, dict):
                raise PoiStateError(f"fleet {fleet_id} references missing ship {ship_id}")
            now_hp = ship.get("api_nowhp")
            max_hp = ship.get("api_maxhp")
            if (
                not isinstance(now_hp, int)
                or isinstance(now_hp, bool)
                or not isinstance(max_hp, int)
                or isinstance(max_hp, bool)
                or max_hp < 1
                or now_hp < 0
                or now_hp > max_hp
            ):
                raise PoiStateError(f"ship {ship_id} has invalid or unknown HP fields")
            if now_hp < max_hp:
                damaged.append(ship_id)
            master_id = ship.get("api_ship_id")
            if isinstance(master_id, bool) or not isinstance(master_id, int) or master_id < 1:
                raise PoiStateError(f"ship {ship_id} has invalid master ID")
            master = masters.get(str(master_id))
            if not isinstance(master, dict):
                raise PoiStateError(f"master ship record {master_id} is missing")
            fuel, fuel_cost = ship.get("api_fuel"), master.get("api_fuel")
            ammo, ammo_cost = ship.get("api_bull"), master.get("api_bull")
            if (
                not isinstance(fuel, int)
                or isinstance(fuel, bool)
                or not isinstance(fuel_cost, int)
                or isinstance(fuel_cost, bool)
                or fuel < 0
                or fuel_cost < 0
                or fuel < fuel_cost
            ):
                fuel_ready = False
            if (
                not isinstance(ammo, int)
                or isinstance(ammo, bool)
                or not isinstance(ammo_cost, int)
                or isinstance(ammo_cost, bool)
                or ammo < 0
                or ammo_cost < 0
                or ammo < ammo_cost
            ):
                ammo_ready = False

        snapshots.append(ExpeditionFleetSnapshot(
            fleet_id=fleet_id,
            ship_ids=ship_ids,
            mission_id=mission_id,
            return_at=None,
            fuel_ready=fuel_ready,
            ammo_ready=ammo_ready,
            damaged_ship_ids=tuple(damaged),
        ))
    return ObservedState(tuple(snapshots), observed_at, "poi-redux:info.fleets+info.ships")
