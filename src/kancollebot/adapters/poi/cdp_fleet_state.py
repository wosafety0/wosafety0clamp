"""Narrow, read-only fleet-state access through the fixed Poi Redux snapshot expression."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from .cdp_websocket import READ_ONLY_FLEET_SNAPSHOT_EXPRESSION


class CdpFleetStateError(RuntimeError):
    """The Poi Redux fleet snapshot is missing, malformed, or ambiguous."""


class CdpReadTransport(Protocol):
    def send(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(frozen=True, slots=True)
class PoiShipSupplyState:
    ship_id: int
    fuel: int | None
    ammo: int | None
    max_fuel: int | None
    max_ammo: int | None
    level: int | None = None
    ship_type: int | None = None

    @property
    def is_fully_supplied(self) -> bool:
        return (
            self.fuel is not None
            and self.ammo is not None
            and self.max_fuel is not None
            and self.max_ammo is not None
            and self.max_fuel > 0
            and self.max_ammo > 0
            and self.fuel == self.max_fuel
            and self.ammo == self.max_ammo
        )


@dataclass(frozen=True, slots=True)
class PoiFleetMissionState:
    fleet_id: int
    name: str | None
    mission: tuple[int, int, int, int]
    ship_ids: tuple[int, ...]
    supply: tuple[PoiShipSupplyState, ...] = ()

    @property
    def mission_status(self) -> int:
        return self.mission[0]

    @property
    def mission_id(self) -> int:
        return self.mission[1]

    @property
    def final_time_ms(self) -> int:
        return self.mission[2]


def _safe_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or abs(value) > 9_007_199_254_740_991:
        raise CdpFleetStateError(f"{label} is not a safe integer")
    return value


def _optional_nonnegative_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    result = _safe_int(value, label)
    if result < 0:
        raise CdpFleetStateError(f"{label} must be non-negative")
    return result


def read_poi_fleet_states(transport: CdpReadTransport) -> tuple[PoiFleetMissionState, ...]:
    """Read fleet mission, ship membership, and Fleet 2/3 supply from Poi's cached Redux state."""
    from kancollebot.observability.probe_counter import mark_probe
    mark_probe("fleet_state")
    result = transport.send(
        "Runtime.evaluate",
        {"expression": READ_ONLY_FLEET_SNAPSHOT_EXPRESSION, "returnByValue": True},
    )
    remote = result.get("result") if isinstance(result, dict) else None
    raw = remote.get("value") if isinstance(remote, dict) else None
    if not isinstance(remote, dict) or remote.get("type") != "string" or not isinstance(raw, str):
        raise CdpFleetStateError("CDP did not return a string fleet snapshot")
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CdpFleetStateError("Poi fleet snapshot was not valid JSON") from exc
    if not isinstance(items, list) or not 3 <= len(items) <= 4:
        raise CdpFleetStateError("Poi fleet snapshot must contain three or four fleet records")
    states: list[PoiFleetMissionState] = []
    seen: set[int] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise CdpFleetStateError(f"Fleet record {index} is not an object")
        fleet_id = _safe_int(item.get("id"), f"Fleet {index} ID")
        if not 1 <= fleet_id <= 4 or fleet_id in seen:
            raise CdpFleetStateError("Fleet IDs must be unique integers from 1 through 4")
        seen.add(fleet_id)
        name = item.get("name")
        if name is not None and not isinstance(name, str):
            raise CdpFleetStateError(f"Fleet {fleet_id} name has an invalid type")
        mission = item.get("mission")
        if not isinstance(mission, list) or len(mission) != 4:
            raise CdpFleetStateError(f"Fleet {fleet_id} mission must contain exactly four slots")
        mission_tuple = tuple(_safe_int(value, f"Fleet {fleet_id} mission slot") for value in mission)
        ships = item.get("ships")
        if not isinstance(ships, list) or len(ships) > 6:
            raise CdpFleetStateError(f"Fleet {fleet_id} ship membership is invalid")
        ship_tuple = tuple(_safe_int(value, f"Fleet {fleet_id} ship ID") for value in ships)
        expected_supply_ids = {ship_id for ship_id in ship_tuple if ship_id > 0}
        supply_raw = item.get("supply", [])
        if not isinstance(supply_raw, list) or len(supply_raw) > 6:
            raise CdpFleetStateError(f"Fleet {fleet_id} supply snapshot is invalid")
        supply_states: list[PoiShipSupplyState] = []
        seen_supply_ids: set[int] = set()
        for supply_index, supply_item in enumerate(supply_raw):
            if not isinstance(supply_item, dict):
                raise CdpFleetStateError(f"Fleet {fleet_id} supply record {supply_index} is invalid")
            supply_ship_id = _safe_int(supply_item.get("id"), f"Fleet {fleet_id} supply ship ID")
            if supply_ship_id not in expected_supply_ids or supply_ship_id in seen_supply_ids:
                raise CdpFleetStateError(f"Fleet {fleet_id} supply ship ID is not a unique fleet member")
            seen_supply_ids.add(supply_ship_id)
            fuel = _optional_nonnegative_int(supply_item.get("fuel"), f"Fleet {fleet_id} ship fuel")
            ammo = _optional_nonnegative_int(supply_item.get("ammo"), f"Fleet {fleet_id} ship ammo")
            max_fuel = _optional_nonnegative_int(supply_item.get("max_fuel"), f"Fleet {fleet_id} ship max fuel")
            max_ammo = _optional_nonnegative_int(supply_item.get("max_ammo"), f"Fleet {fleet_id} ship max ammo")
            level = _optional_nonnegative_int(supply_item.get("level"), f"Fleet {fleet_id} ship level")
            ship_type = _optional_nonnegative_int(supply_item.get("ship_type"), f"Fleet {fleet_id} ship type")
            if level == 0 or ship_type == 0:
                raise CdpFleetStateError(f"Fleet {fleet_id} ship level and type must be positive")
            if fuel is not None and max_fuel is not None and fuel > max_fuel:
                raise CdpFleetStateError(f"Fleet {fleet_id} ship fuel exceeds its maximum")
            if ammo is not None and max_ammo is not None and ammo > max_ammo:
                raise CdpFleetStateError(f"Fleet {fleet_id} ship ammo exceeds its maximum")
            supply_states.append(PoiShipSupplyState(supply_ship_id, fuel, ammo, max_fuel, max_ammo, level, ship_type))
        if supply_states and seen_supply_ids != expected_supply_ids:
            raise CdpFleetStateError(f"Fleet {fleet_id} supply snapshot is incomplete")
        states.append(PoiFleetMissionState(fleet_id, name, mission_tuple, ship_tuple, tuple(supply_states)))
    if seen != set(range(1, len(items) + 1)):
        raise CdpFleetStateError("Fleet IDs are not a contiguous set starting at 1")
    return tuple(sorted(states, key=lambda state: state.fleet_id))
