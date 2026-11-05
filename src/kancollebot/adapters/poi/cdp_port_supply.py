"""Strict reader for the fixed, read-only Poi Redux port-supply snapshot.

The single-fleet resupply controller needs a port-shaped payload (fleets, ship
instances, ship master data, material inventory) because a supply cost is the
per-ship fuel/ammo deficit. This module reads exactly that payload through the
second allowlisted ``Runtime.evaluate`` expression and validates its shape.

Two things this module deliberately does *not* do:

* It never decides that the data is fresh. ``observed_at`` is the moment the
  read happened; whether the underlying port data actually came from the live
  game is decided by matching a real port-response witness (see
  ``kancollebot.expeditions.port_supply_provider``). A local timestamp alone is
  not freshness evidence.
* It never fills in missing values. A missing or malformed field raises, so a
  resupply preflight fails closed instead of computing a cost from guessed
  numbers.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from .cdp_websocket import READ_ONLY_PORT_SUPPLY_SNAPSHOT_EXPRESSION

_MAX_SAFE_INT = 9_007_199_254_740_991


class CdpPortSupplyError(RuntimeError):
    """The Poi Redux port-supply snapshot is missing, malformed, or ambiguous."""


class CdpReadTransport(Protocol):
    def send(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(frozen=True, slots=True)
class PoiPortSupplySnapshot:
    """A validated, port-shaped Redux payload plus the time it was read.

    ``fleets``/``ships``/``master_ships``/``resources`` are exactly the shapes
    ``build_fleet_supply_plan`` accepts, so no transformation happens later.
    """

    fleets: list[dict[str, Any]]
    ships: dict[str, dict[str, Any]]
    master_ships: dict[str, dict[str, Any]]
    resources: list[int]
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")


def _safe_int(value: Any, label: str, *, minimum: int = 0) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or abs(value) > _MAX_SAFE_INT
    ):
        raise CdpPortSupplyError(f"{label} is not a safe integer >= {minimum}")
    return value


def _optional_nonnegative_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    return _safe_int(value, label)


def _validate_fleets(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not 3 <= len(raw) <= 4:
        raise CdpPortSupplyError("port supply snapshot must contain three or four fleet records")
    fleets: list[dict[str, Any]] = []
    seen: set[int] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise CdpPortSupplyError(f"Fleet record {index} is not an object")
        fleet_id = _safe_int(item.get("api_id"), f"Fleet {index} ID", minimum=1)
        if fleet_id > 4 or fleet_id in seen:
            raise CdpPortSupplyError("Fleet IDs must be unique integers from 1 through 4")
        seen.add(fleet_id)
        name = item.get("api_name")
        if name is not None and not isinstance(name, str):
            raise CdpPortSupplyError(f"Fleet {fleet_id} name has an invalid type")
        mission = item.get("api_mission")
        if not isinstance(mission, list) or len(mission) != 4:
            raise CdpPortSupplyError(f"Fleet {fleet_id} mission must contain exactly four slots")
        for slot in mission:
            _safe_int(slot, f"Fleet {fleet_id} mission slot")
        slots = item.get("api_ship")
        if not isinstance(slots, list) or len(slots) > 6:
            raise CdpPortSupplyError(f"Fleet {fleet_id} ship roster is invalid")
        validated_slots: list[int] = []
        for slot in slots:
            if isinstance(slot, bool) or not isinstance(slot, int):
                raise CdpPortSupplyError(f"Fleet {fleet_id} has an invalid ship slot")
            if slot != -1:
                _safe_int(slot, f"Fleet {fleet_id} ship slot", minimum=1)
            validated_slots.append(slot)
        fleets.append(
            {
                "api_id": fleet_id,
                "api_name": name,
                "api_mission": list(mission),
                "api_ship": validated_slots,
            }
        )
    if seen != set(range(1, len(raw) + 1)):
        raise CdpPortSupplyError("Fleet IDs are not a contiguous set starting at 1")
    return fleets


def _validate_ships(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict) or not raw:
        raise CdpPortSupplyError("ship instance collection is missing or empty")
    ships: dict[str, dict[str, Any]] = {}
    for key, record in raw.items():
        if not isinstance(key, str) or not key.isdigit() or key in ships:
            raise CdpPortSupplyError("ship instance keys must be unique positive integer strings")
        if not isinstance(record, dict):
            raise CdpPortSupplyError(f"ship {key} is not an object")
        ship_id = _safe_int(record.get("api_id"), f"ship {key} ID", minimum=1)
        if str(ship_id) != key:
            raise CdpPortSupplyError(f"ship {key} ID does not match its key")
        master_id = _safe_int(record.get("api_ship_id"), f"ship {key} master ID", minimum=1)
        nowhp = _safe_int(record.get("api_nowhp"), f"ship {key} current HP")
        maxhp = _safe_int(record.get("api_maxhp"), f"ship {key} maximum HP")
        if maxhp < 1 or nowhp > maxhp:
            raise CdpPortSupplyError(f"ship {key} HP values are out of range")
        fuel = _safe_int(record.get("api_fuel"), f"ship {key} fuel")
        ammo = _safe_int(record.get("api_bull"), f"ship {key} ammo")
        level = _optional_nonnegative_int(record.get("api_lv"), f"ship {key} level")
        condition = _optional_nonnegative_int(record.get("api_cond"), f"ship {key} condition")
        ships[key] = {
            "api_id": ship_id,
            "api_ship_id": master_id,
            "api_nowhp": nowhp,
            "api_maxhp": maxhp,
            "api_fuel": fuel,
            "api_bull": ammo,
            "api_lv": level,
            "api_cond": condition,
        }
    return ships


def _validate_master_ships(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict) or not raw:
        raise CdpPortSupplyError("ship master-data collection is missing or empty")
    masters: dict[str, dict[str, Any]] = {}
    for key, record in raw.items():
        if not isinstance(key, str) or not key.isdigit() or key in masters:
            raise CdpPortSupplyError("master ship keys must be unique positive integer strings")
        if not isinstance(record, dict):
            raise CdpPortSupplyError(f"master ship {key} is not an object")
        master_id = _safe_int(record.get("api_id"), f"master ship {key} ID", minimum=1)
        if str(master_id) != key:
            raise CdpPortSupplyError(f"master ship {key} ID does not match its key")
        masters[key] = {
            "api_id": master_id,
            "api_fuel_max": _safe_int(record.get("api_fuel_max"), f"master ship {key} max fuel"),
            "api_bull_max": _safe_int(record.get("api_bull_max"), f"master ship {key} max ammo"),
            "api_stype": _optional_nonnegative_int(record.get("api_stype"), f"master ship {key} type"),
        }
    return masters


def _validate_resources(raw: Any) -> list[int]:
    if not isinstance(raw, list) or len(raw) < 4:
        raise CdpPortSupplyError("material inventory is incomplete")
    resources: list[int] = []
    for index, value in enumerate(raw):
        resources.append(_safe_int(value, f"material {index + 1}"))
    return resources


def read_poi_port_supply_snapshot(
    transport: CdpReadTransport,
    *,
    clock: Callable[[], datetime] | None = None,
) -> PoiPortSupplySnapshot:
    """Read and validate one port-supply snapshot from Poi's cached Redux state.

    The observation time is taken *after* the payload arrives, so it can never
    predate the data it describes.
    """
    if not hasattr(transport, "send"):
        raise ValueError("transport must provide send(method, params)")
    result = transport.send(
        "Runtime.evaluate",
        {"expression": READ_ONLY_PORT_SUPPLY_SNAPSHOT_EXPRESSION, "returnByValue": True},
    )
    remote = result.get("result") if isinstance(result, dict) else None
    raw = remote.get("value") if isinstance(remote, dict) else None
    if not isinstance(remote, dict) or remote.get("type") != "string" or not isinstance(raw, str):
        raise CdpPortSupplyError("CDP did not return a string port-supply snapshot")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CdpPortSupplyError("Poi port-supply snapshot was not valid JSON") from exc
    if payload is None:
        raise CdpPortSupplyError(
            "Poi Redux has no complete port-supply snapshot (fleets, ships, master ships "
            "or materials are missing)"
        )
    if not isinstance(payload, dict):
        raise CdpPortSupplyError("Poi port-supply snapshot is not an object")
    fleets = _validate_fleets(payload.get("fleets"))
    ships = _validate_ships(payload.get("ships"))
    master_ships = _validate_master_ships(payload.get("master_ships"))
    resources = _validate_resources(payload.get("resources"))
    for fleet in fleets:
        for ship_id in fleet["api_ship"]:
            if ship_id == -1:
                continue
            ship = ships.get(str(ship_id))
            if ship is None:
                raise CdpPortSupplyError(
                    f"Fleet {fleet['api_id']} references ship {ship_id} missing from the snapshot"
                )
            if str(ship["api_ship_id"]) not in master_ships:
                raise CdpPortSupplyError(
                    f"ship {ship_id} master record {ship['api_ship_id']} is missing "
                    "from the snapshot"
                )
    observed_at = (clock or (lambda: datetime.now(timezone.utc)))()
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise CdpPortSupplyError("clock must return a timezone-aware datetime")
    return PoiPortSupplySnapshot(
        fleets=fleets,
        ships=ships,
        master_ships=master_ships,
        resources=resources,
        observed_at=observed_at,
    )
