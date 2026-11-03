"""Read-only dock inventory adapter for Poi.

Reads ``info.fleets``, ``info.ships``, and master ship types through the
allowlisted MCP read tools. It has no dispatch, click, or write capability.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kancollebot.adapters.poi.expedition_reader import _tool_value
from kancollebot.adapters.poi.mcp_client import PoiMcpClient, PoiMcpError
from kancollebot.expeditions.dock_composition_planner import (
    DockInventoryError,
    DockShip,
    dock_ships_from_poi_payloads,
)
from kancollebot.expeditions.mission_eligibility import FleetCompositionSnapshot


class PoiDockReadError(RuntimeError):
    """The read-only dock observation does not meet the adapter contract."""


@dataclass(frozen=True, slots=True)
class ObservedFleetComposition:
    fleet_id: int
    composition: FleetCompositionSnapshot


@dataclass(frozen=True, slots=True)
class DockReading:
    fleets: tuple[ObservedFleetComposition, ...]
    dock: tuple[DockShip, ...]
    ship_count: int

    def composition(self, fleet_id: int) -> FleetCompositionSnapshot:
        for item in self.fleets:
            if item.fleet_id == fleet_id:
                return item.composition
        raise PoiDockReadError(f"fleet {fleet_id} was not observed")


def _collection(value: Any, path: str, *, kind: str) -> Any:
    """Mirror ``PoiExpeditionReader``'s envelope checks for a complete response."""
    if not isinstance(value, dict) or value.get("kind") != kind:
        raise PoiDockReadError(f"Poi {path} did not return {kind} data")
    if value.get("truncated") is not False:
        raise PoiDockReadError(f"Poi {path} response was truncated or unmarked")
    items = value.get("items")
    expected = list if kind == "array" else dict
    if not isinstance(items, expected):
        raise PoiDockReadError(f"Poi {path} result has an invalid items field")
    total, returned = value.get("total"), value.get("returned")
    for label, count in (("total", total), ("returned", returned)):
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise PoiDockReadError(f"Poi {path} result has an invalid {label} count")
    if returned != len(items) or total != returned:
        raise PoiDockReadError(f"Poi {path} result counts do not match its items")
    return items


def _master_ids(ships: dict[str, Any]) -> list[int]:
    ids: set[int] = set()
    for key, ship in ships.items():
        if not isinstance(ship, dict):
            raise PoiDockReadError(f"ship instance {key} is not an object")
        master_id = ship.get("api_ship_id")
        if isinstance(master_id, bool) or not isinstance(master_id, int) or master_id < 1:
            raise PoiDockReadError(f"ship instance {key} has an invalid api_ship_id")
        ids.add(master_id)
    return sorted(ids)


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PoiDockReadError(f"{label} must be a positive integer")
    return value


def read_dock_and_fleets(
    *,
    poi_endpoint: str = "http://127.0.0.1:12450/mcp",
    client: PoiMcpClient | None = None,
) -> DockReading:
    """Collect fleet compositions and the unassigned dock inventory."""
    reader = client or PoiMcpClient(poi_endpoint)
    try:
        health = reader.health()
        if health.get("name") != "poi-nanodesu-mcp":
            raise PoiDockReadError("Poi bridge identity is not poi-nanodesu-mcp")
        if health.get("status") != "ok" or health.get("storeReady") is not True:
            raise PoiDockReadError("Poi bridge is unhealthy or its store is not ready")
        reader.initialize()
        tools = {tool.name for tool in reader.list_tools()}
        if not {"poi_get", "poi_lookup"}.issubset(tools):
            raise PoiDockReadError("Poi bridge is missing required read-only tools")

        fleets = _collection(
            _tool_value(reader.call_read_tool("poi_get", {"path": "info.fleets"}), "poi_get"),
            "info.fleets",
            kind="array",
        )
        ships = _collection(
            _tool_value(reader.call_read_tool("poi_get", {"path": "info.ships"}), "poi_get"),
            "info.ships",
            kind="object-map",
        )
        requested = _master_ids(ships)
        master_result = _tool_value(
            reader.call_read_tool(
                "poi_lookup",
                {"kind": "ships", "ids": requested, "select": ["api_stype"]},
            ),
            "poi_lookup",
        )
        if not isinstance(master_result, dict) or any(
            not str(key).isdigit() for key in master_result
        ):
            raise PoiDockReadError("poi_lookup returned an invalid master-record map")
        missing = [master_id for master_id in requested if not isinstance(master_result.get(str(master_id)), dict)]
        if missing:
            raise PoiDockReadError(f"poi_lookup omitted master ship records {missing}")
    except PoiMcpError as exc:
        raise PoiDockReadError(str(exc)) from exc

    try:
        dock = dock_ships_from_poi_payloads(fleets, ships, master_result)
    except DockInventoryError as exc:
        raise PoiDockReadError(f"dock inventory rejected: {exc}") from exc

    compositions: list[ObservedFleetComposition] = []
    for index, fleet in enumerate(fleets):
        if not isinstance(fleet, dict):
            raise PoiDockReadError(f"fleet entry {index} is not an object")
        fleet_id = _positive_int(fleet.get("api_id"), f"fleet entry {index} api_id")
        raw_slots = fleet.get("api_ship")
        if not isinstance(raw_slots, list) or len(raw_slots) > 6:
            raise PoiDockReadError(f"fleet {fleet_id} has an invalid api_ship array")
        ship_ids: list[int] = []
        for raw_id in raw_slots:
            if isinstance(raw_id, bool) or not isinstance(raw_id, int):
                raise PoiDockReadError(f"fleet {fleet_id} has a non-integer ship slot")
            if raw_id == -1:
                continue
            if raw_id < 1:
                raise PoiDockReadError(f"fleet {fleet_id} has an invalid empty-slot marker")
            ship_ids.append(raw_id)
        levels: list[int] = []
        types: list[int] = []
        for ship_id in ship_ids:
            ship = ships.get(str(ship_id))
            if not isinstance(ship, dict):
                raise PoiDockReadError(f"fleet {fleet_id} references missing ship {ship_id}")
            master_id = _positive_int(ship.get("api_ship_id"), f"ship {ship_id} api_ship_id")
            master = master_result.get(str(master_id))
            if not isinstance(master, dict):
                raise PoiDockReadError(f"master ship record {master_id} is missing")
            levels.append(_positive_int(ship.get("api_lv"), f"ship {ship_id} api_lv"))
            types.append(_positive_int(master.get("api_stype"), f"master {master_id} api_stype"))
        compositions.append(
            ObservedFleetComposition(
                fleet_id,
                FleetCompositionSnapshot(tuple(ship_ids), tuple(levels), tuple(types)),
            )
        )
    return DockReading(tuple(compositions), dock, len(ships))
