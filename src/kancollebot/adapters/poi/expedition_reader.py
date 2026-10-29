"""Read-only adapter for Poi expedition observations.

The adapter only reads live Redux state. It has no dispatch method and cannot
perform gameplay actions.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from kancollebot.adapters.poi.expedition_state_v2 import PoiStateError, normalize_poi_fleets
from kancollebot.adapters.poi.mcp_client import PoiMcpClient, PoiMcpError
from kancollebot.core.observed_state import ObservedState
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot


def _tool_value(result: dict[str, Any], tool_name: str) -> Any:
    """Decode a tool result; refuse missing or ambiguous content."""
    if result.get("isError") is True:
        raise PoiMcpError(f"{tool_name} returned a tool-level error")
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    content = result.get("content")
    if not isinstance(content, list):
        raise PoiMcpError(f"{tool_name} returned no content array")
    texts = [
        item.get("text") for item in content
        if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
    ]
    if len(texts) != 1:
        raise PoiMcpError(f"{tool_name} returned an ambiguous text payload")
    try:
        return json.loads(texts[0])
    except json.JSONDecodeError as exc:
        raise PoiMcpError(f"{tool_name} returned non-JSON text") from exc


class PoiExpeditionReader:
    """Read-only source of normalized fleet observations."""

    def __init__(
        self,
        client: PoiMcpClient | None = None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._client = client or PoiMcpClient()
        self._clock = clock
        self._initialized = False

    def _ensure_ready(self) -> None:
        health = self._client.health()
        if health.get("name") != "poi-nanodesu-mcp":
            raise PoiMcpError("Poi bridge identity is not poi-nanodesu-mcp")
        if health.get("status") != "ok" or health.get("storeReady") is not True:
            raise PoiMcpError("Poi bridge is unhealthy or its Redux store is not ready")
        if not self._initialized:
            self._client.initialize()
            tools = {tool.name for tool in self._client.list_tools()}
            if not {"poi_describe", "poi_get", "poi_lookup"}.issubset(tools):
                raise PoiMcpError("Poi bridge is missing required read-only tools")
            self._initialized = True

    @staticmethod
    def _collection_items(value: Any, path: str, *, expected_kind: str) -> Any:
        if not isinstance(value, dict) or value.get("kind") != expected_kind:
            raise PoiMcpError(f"Poi {path} did not return {expected_kind} data")
        truncated = value.get("truncated")
        if not isinstance(truncated, bool):
            raise PoiMcpError(f"Poi {path} result has no valid truncation marker")
        if truncated:
            raise PoiMcpError(f"Poi {path} response was truncated")
        items = value.get("items")
        expected_type = list if expected_kind == "array" else dict
        if not isinstance(items, expected_type):
            raise PoiMcpError(f"Poi {path} result has an invalid items field")
        total = value.get("total")
        returned = value.get("returned")
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise PoiMcpError(f"Poi {path} result has an invalid total count")
        if isinstance(returned, bool) or not isinstance(returned, int) or returned < 0:
            raise PoiMcpError(f"Poi {path} result has an invalid returned count")
        if returned != len(items) or total != returned:
            raise PoiMcpError(f"Poi {path} result counts do not match its complete items")
        return items

    @staticmethod
    def _referenced_master_ids(fleets: list[Any], ships: dict[str, Any]) -> list[int]:
        """Resolve fleet instance references to a sorted, deduplicated master-ID list."""
        master_ids: set[int] = set()
        for index, fleet in enumerate(fleets):
            if not isinstance(fleet, dict):
                raise PoiMcpError(f"Fleet entry {index} is not an object")
            raw_ship_ids = fleet.get("api_ship")
            if not isinstance(raw_ship_ids, list) or len(raw_ship_ids) > 6:
                raise PoiMcpError(f"Fleet entry {index} has an invalid api_ship array")
            for ship_id in raw_ship_ids:
                if isinstance(ship_id, bool) or not isinstance(ship_id, int):
                    raise PoiMcpError(f"Fleet entry {index} has a non-integer ship slot")
                if ship_id == -1:
                    continue
                if ship_id < 1:
                    raise PoiMcpError(f"Fleet entry {index} has an invalid empty-slot marker")
                ship = ships.get(str(ship_id))
                if not isinstance(ship, dict):
                    raise PoiMcpError(f"Fleet references missing ship instance {ship_id}")
                master_id = ship.get("api_ship_id")
                if isinstance(master_id, bool) or not isinstance(master_id, int) or master_id < 1:
                    raise PoiMcpError(f"Ship instance {ship_id} has an invalid api_ship_id")
                master_ids.add(master_id)
        return sorted(master_ids)

    @staticmethod
    def _lookup_items(value: Any, requested_ids: list[int]) -> dict[str, Any]:
        """Validate poi_lookup's direct ID-to-record object-map response."""
        if not isinstance(value, dict) or any(not str(key).isdigit() for key in value):
            raise PoiMcpError("poi_lookup returned an invalid master-record object map")
        items = {str(key): record for key, record in value.items()}
        for master_id in requested_ids:
            record = items.get(str(master_id))
            if not isinstance(record, dict):
                raise PoiMcpError(f"poi_lookup omitted master ship record {master_id}")
            for field in ("api_fuel_max", "api_bull_max"):
                field_value = record.get(field)
                if isinstance(field_value, bool) or not isinstance(field_value, int) or field_value < 0:
                    raise PoiMcpError(f"Master ship record {master_id} has invalid {field}")
        return items

    def read_fleets(self) -> ObservedState[tuple[ExpeditionFleetSnapshot, ...]]:
        """Read fleet and ship instances, then lookup only referenced master records."""
        self._ensure_ready()
        describe = _tool_value(
            self._client.call_read_tool("poi_describe", {"path": "info.fleets"}),
            "poi_describe",
        )
        if not isinstance(describe, dict) or describe.get("kind") not in {"array", "collection", "object-map"}:
            raise PoiMcpError("Poi info.fleets has an unknown collection kind")
        fleets_result = _tool_value(
            self._client.call_read_tool("poi_get", {"path": "info.fleets"}), "poi_get(info.fleets)"
        )
        ships_result = _tool_value(
            self._client.call_read_tool("poi_get", {"path": "info.ships"}), "poi_get(info.ships)"
        )
        fleets = self._collection_items(fleets_result, "info.fleets", expected_kind="array")
        ships = self._collection_items(ships_result, "info.ships", expected_kind="object-map")
        master_ids = self._referenced_master_ids(fleets, ships)
        if master_ids:
            lookup_result = _tool_value(
                self._client.call_read_tool(
                    "poi_lookup",
                    {"kind": "ships", "ids": master_ids, "select": ["api_fuel_max", "api_bull_max"]},
                ),
                "poi_lookup",
            )
            masters = self._lookup_items(lookup_result, master_ids)
        else:
            masters = {}

        # The MCP API exposes no source revision token. Re-read both mutable
        # Redux collections and refuse to combine records from visibly different
        # states. This is a best-effort consistency guard, not a freshness proof
        # or an atomic snapshot guarantee (an ABA change can still go undetected).
        confirm_fleets_result = _tool_value(
            self._client.call_read_tool("poi_get", {"path": "info.fleets"}),
            "poi_get(info.fleets) confirmation",
        )
        confirm_ships_result = _tool_value(
            self._client.call_read_tool("poi_get", {"path": "info.ships"}),
            "poi_get(info.ships) confirmation",
        )
        confirm_fleets = self._collection_items(
            confirm_fleets_result, "info.fleets", expected_kind="array"
        )
        confirm_ships = self._collection_items(
            confirm_ships_result, "info.ships", expected_kind="object-map"
        )
        if fleets != confirm_fleets or ships != confirm_ships:
            raise PoiMcpError(
                "Poi fleet/ship state changed during observation; refusing mixed snapshot"
            )

        observed_at = self._clock()
        try:
            return normalize_poi_fleets(fleets, ships, masters, observed_at=observed_at)
        except (PoiStateError, ValueError, TypeError, OverflowError) as exc:
            raise PoiMcpError(f"Poi state normalization failed closed: {exc}") from exc
