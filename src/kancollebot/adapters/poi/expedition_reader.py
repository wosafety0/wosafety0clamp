"""Read-only adapter for Poi expedition observations.

The adapter only reads live Redux state. It has no dispatch method and cannot
perform gameplay actions.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from kancollebot.adapters.poi.expedition_state_v2 import PoiStateError, normalize_poi_fleets
from kancollebot.adapters.poi.mcp_client import PoiMcpClient, PoiMcpError
from kancollebot.adapters.poi.port_freshness import port_snapshot_matches_current_state
from kancollebot.core.observed_state import ObservedState
from kancollebot.core.reward_inventory import RewardInventorySnapshot, RewardInventoryError
from kancollebot.core.observation_contracts import ObservedFleetSnapshot

from .requirement_evidence import (
    EQUIP_INSTANCE_FIELDS,
    SHIP_DETAIL_FIELDS,
    RequirementEvidenceError,
    build_equipment_where_clause,
    equipment_instance_ids_for_fleets,
    parse_requirement_evidence,
)


# The bridge caps collection responses by serialized size, not by item count:
# this account's 239-instance roster comes back as 111 entries with
# truncated:true, and limit/offset pagination is ignored (offset never
# advances). Projecting only the fields the reader and the port-witness
# validator consume makes the whole roster fit in one untruncated response.
# api_lv is included because normalize_poi_fleets uses it for composition
# completeness; omitting it would silently downgrade every fleet to an
# incomplete composition.
_INFO_SHIPS_FIELDS = (
    "api_id",
    "api_ship_id",
    "api_nowhp",
    "api_maxhp",
    "api_fuel",
    "api_bull",
    "api_cond",
    "api_lv",
)
_INFO_SHIPS_READ_LIMIT = 1000


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
        port_snapshot_provider: Callable[[], Any] | None = None,
        freshness_max_age: timedelta = timedelta(seconds=30),
    ) -> None:
        if freshness_max_age < timedelta(0):
            raise ValueError("freshness_max_age must not be negative")
        self._client = client or PoiMcpClient()
        self._clock = clock
        self._port_snapshot_provider = port_snapshot_provider
        self._freshness_max_age = freshness_max_age
        self._initialized = False
        self._fleets_description_validated = False
        self._mission_name_cache: dict[int, str] = {}

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
    def _referenced_instance_ids(fleets: list[Any]) -> list[int]:
        """Collect the ship instance ids referenced by any fleet slot.

        The supervisor only ever needs the instances that are actually in a
        fleet, which is a bounded set (<= 6 per fleet) even on large accounts.
        """
        ids: list[int] = []
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
                ids.append(ship_id)
        return ids

    @staticmethod
    def _info_ships_complete(value: Any) -> dict[str, Any] | None:
        """Return info.ships items only if the full read is whole and untruncated."""
        if not isinstance(value, dict) or value.get("kind") != "object-map":
            return None
        if value.get("truncated") is not False:
            return None
        items = value.get("items")
        total = value.get("total")
        returned = value.get("returned")
        if not isinstance(items, dict) or not isinstance(total, int) or not isinstance(returned, int):
            return None
        if returned != len(items) or total != returned:
            return None
        return items

    @staticmethod
    def _filtered_items(value: Any, path: str, wanted_ids: tuple[int, ...]) -> dict[str, Any] | None:
        """Validate a `where`-filtered object-map read without demanding all rows."""
        if not isinstance(value, dict) or value.get("kind") != "object-map":
            return None
        if value.get("truncated") is not False:
            return None
        items = value.get("items")
        returned = value.get("returned")
        if not isinstance(items, dict) or not isinstance(returned, int) or returned != len(items):
            return None
        if any(str(instance_id) not in items for instance_id in wanted_ids):
            return None
        return items

    def _read_requirement_evidence_sources(
        self, fleets: list[Any]
    ) -> dict[str, Any] | None:
        """Read equipment/stat evidence inside THIS observation, or return None.

        The 239-ship port-witness projection must stay small, so equipment and
        displayed stats come from a SECOND narrow projection of ``info.ships``
        (239/239 untruncated) plus a filtered ``info.equips`` lookup. Any failed,
        truncated, ambiguous or unresolvable read yields ``None``; the affected
        conditions then fail closed as UNKNOWN and no other fleet is invented.

        This is a read-only helper: it never writes state and never relaxes the
        port-witness freshness check.
        """
        try:
            detail_result = _tool_value(
                self._client.call_read_tool(
                    "poi_get",
                    {
                        "path": "info.ships",
                        "limit": _INFO_SHIPS_READ_LIMIT,
                        "select": list(SHIP_DETAIL_FIELDS),
                    },
                ),
                "poi_get(info.ships requirement detail)",
            )
            detail = self._info_ships_complete(detail_result)
            if detail is None:
                return None
            instance_ids = equipment_instance_ids_for_fleets(fleets, detail)
            equip_instances: dict[str, Any] = {}
            if instance_ids:
                where = build_equipment_where_clause(instance_ids)
                equip_result = _tool_value(
                    self._client.call_read_tool(
                        "poi_get",
                        {
                            "path": "info.equips",
                            "where": where,
                            "limit": _INFO_SHIPS_READ_LIMIT,
                            "select": list(EQUIP_INSTANCE_FIELDS),
                        },
                    ),
                    "poi_get(info.equips requirement detail)",
                )
                resolved = self._filtered_items(equip_result, "info.equips", instance_ids)
                if resolved is None:
                    return None
                equip_instances = resolved
        except (PoiMcpError, RequirementEvidenceError, KeyError, TypeError):
            # Equipment/stat evidence is auxiliary: a bridge error degrades the
            # observation to "no evidence" rather than fabricating a value. The
            # evaluator then refuses any expedition that needs that evidence.
            # A malformed/absent collection must never make the WHOLE read fail
            # in a way that could be mistaken for a dispatch decision.
            return None
        return {"ship_details": detail, "equip_instances": equip_instances}

    def _read_info_ships(self, referenced_ids: list[int]) -> dict[str, Any]:
        """Return the complete info.ships object-map.

        A whole, untruncated roster is needed for two independent reasons: the
        collection gate requires total == returned, and the port-witness
        validator compares the witness against the *complete* ship set. A
        subset of only the instances a fleet references can describe a fleet,
        but it cannot prove that the Poi state matches the port response.

        So, when the plain read is truncated, ask the bridge for a projected
        full read before falling back to per-instance reads. If even that is
        truncated, the per-id subset is still usable for describing fleets,
        but the observation deliberately stays source-freshness *unverified*
        and any supervisor that requires fresh evidence fails closed.
        """
        plain_result = _tool_value(
            self._client.call_read_tool("poi_get", {"path": "info.ships"}),
            "poi_get(info.ships)",
        )
        complete = self._info_ships_complete(plain_result)
        if complete is not None:
            return complete

        projected_result = _tool_value(
            self._client.call_read_tool(
                "poi_get",
                {
                    "path": "info.ships",
                    "limit": _INFO_SHIPS_READ_LIMIT,
                    "select": list(_INFO_SHIPS_FIELDS),
                },
            ),
            "poi_get(info.ships projected)",
        )
        complete = self._info_ships_complete(projected_result)
        if complete is not None:
            return complete

        return self._read_info_ships_by_id(referenced_ids)

    def _read_info_ships_by_id(self, referenced_ids: list[int]) -> dict[str, Any]:
        ships: dict[str, Any] = {}
        for ship_id in referenced_ids:
            result = _tool_value(
                self._client.call_read_tool("poi_get", {"path": f"info.ships.{ship_id}"}),
                f"poi_get(info.ships.{ship_id})",
            )
            if (
                not isinstance(result, dict)
                or result.get("kind") != "object"
                or not isinstance(result.get("value"), dict)
            ):
                raise PoiMcpError(
                    f"Poi info.ships.{ship_id} did not return a ship instance"
                )
            ships[str(ship_id)] = result["value"]
        return ships

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

    def lookup_mission_name(self, mission_id: int) -> str:
        """Resolve the game's exact expedition name through Poi's existing master-data API."""
        if isinstance(mission_id, bool) or not isinstance(mission_id, int) or mission_id < 1:
            raise PoiMcpError("mission id must be a positive integer")
        cached = self._mission_name_cache.get(mission_id)
        if cached is not None:
            return cached
        self._ensure_ready()
        result = _tool_value(
            self._client.call_read_tool(
                "poi_lookup",
                {"kind": "missions", "ids": [mission_id], "select": ["api_name"]},
            ),
            "poi_lookup(missions)",
        )
        if not isinstance(result, dict) or set(result) != {str(mission_id)}:
            raise PoiMcpError(
                f"Poi mission lookup did not return exactly mission {mission_id}"
            )
        record = result.get(str(mission_id))
        if not isinstance(record, dict):
            raise PoiMcpError(f"Poi mission lookup omitted mission {mission_id}")
        name = record.get("api_name")
        if not isinstance(name, str) or not name.strip():
            raise PoiMcpError(f"Poi mission {mission_id} has no valid api_name")
        normalized = name.strip()
        self._mission_name_cache[mission_id] = normalized
        return normalized

    def read_reward_inventory(self) -> RewardInventorySnapshot:
        """Read a complete, read-only Poi material and useitem inventory snapshot."""
        self._ensure_ready()
        resources_value = _tool_value(
            self._client.call_read_tool(
                "poi_get", {"path": "info.resources", "limit": 16}
            ),
            "poi_get(info.resources)",
        )
        resources = self._collection_items(
            resources_value, "info.resources", expected_kind="array"
        )
        if len(resources) != 8 or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in resources
        ):
            raise PoiMcpError("Poi info.resources must contain eight non-negative integers")

        useitems_value = _tool_value(
            self._client.call_read_tool(
                "poi_get",
                {
                    "path": "info.useitems",
                    "limit": 1000,
                    "select": ["api_id", "api_count"],
                },
            ),
            "poi_get(info.useitems)",
        )
        useitems = self._collection_items(
            useitems_value, "info.useitems", expected_kind="object-map"
        )
        normalized_items: list[tuple[int, int]] = []
        for key, item in useitems.items():
            if not isinstance(key, str) or not key.isdigit() or not isinstance(item, dict):
                raise PoiMcpError("Poi info.useitems contains a malformed object-map row")
            item_id = item.get("api_id")
            count = item.get("api_count")
            if (
                isinstance(item_id, bool) or not isinstance(item_id, int) or item_id < 1
                or str(item_id) != key
                or isinstance(count, bool) or not isinstance(count, int) or count < 0
            ):
                raise PoiMcpError("Poi info.useitems contains an invalid ID/count pair")
            normalized_items.append((item_id, count))
        try:
            return RewardInventorySnapshot(
                observed_at=self._clock(),
                resources=tuple(resources),
                useitems=tuple(sorted(normalized_items)),
            )
        except RewardInventoryError as exc:
            raise PoiMcpError(f"Poi reward inventory snapshot is invalid: {exc}") from exc

    def read_fleets(self) -> ObservedState[tuple[ObservedFleetSnapshot, ...]]:
        """Read fleet and ship instances, then lookup only referenced master records."""
        self._ensure_ready()
        # Only inject an in-memory witness from a trusted live observer. A
        # persistent snapshot file is not source-freshness evidence.
        port_snapshot = (
            self._port_snapshot_provider()
            if self._port_snapshot_provider is not None
            else None
        )
        if not self._fleets_description_validated:
            describe = _tool_value(
                self._client.call_read_tool("poi_describe", {"path": "info.fleets"}),
                "poi_describe",
            )
            if not isinstance(describe, dict) or describe.get("kind") not in {"array", "collection", "object-map"}:
                raise PoiMcpError("Poi info.fleets has an unknown collection kind")
            self._fleets_description_validated = True
        fleets_result = _tool_value(
            self._client.call_read_tool("poi_get", {"path": "info.fleets"}), "poi_get(info.fleets)"
        )
        fleets = self._collection_items(fleets_result, "info.fleets", expected_kind="array")
        referenced_ids = sorted(set(self._referenced_instance_ids(fleets)))
        ships = self._read_info_ships(referenced_ids)
        master_ids = self._referenced_master_ids(fleets, ships)
        if master_ids:
            lookup_result = _tool_value(
                self._client.call_read_tool(
                    "poi_lookup",
                    {
                        "kind": "ships",
                        "ids": master_ids,
                        "select": ["api_fuel_max", "api_bull_max", "api_stype"],
                    },
                ),
                "poi_lookup",
            )
            masters = self._lookup_items(lookup_result, master_ids)
        else:
            masters = {}

        # R3-I evidence is read INSIDE this observation, between the two
        # consistency reads below. It is attached to the very same fleet
        # snapshot that carries the freshness verdict, so the dispatch gate
        # cannot evaluate equipment from a different instant than composition.
        evidence_sources = self._read_requirement_evidence_sources(fleets)

        # The MCP API exposes no source revision token. Re-read both mutable
        # Redux collections and refuse to combine records from visibly different
        # states. This is a best-effort consistency guard, not a freshness proof
        # or an atomic snapshot guarantee (an ABA change can still go undetected).
        confirm_fleets_result = _tool_value(
            self._client.call_read_tool("poi_get", {"path": "info.fleets"}),
            "poi_get(info.fleets) confirmation",
        )
        confirm_ships = self._read_info_ships(referenced_ids)
        confirm_fleets = self._collection_items(
            confirm_fleets_result, "info.fleets", expected_kind="array"
        )
        if fleets != confirm_fleets or ships != confirm_ships:
            raise PoiMcpError(
                "Poi fleet/ship state changed during observation; refusing mixed snapshot"
            )
        confirm_evidence_sources = self._read_requirement_evidence_sources(confirm_fleets)
        requirement_evidence = None
        if (
            evidence_sources is not None
            and confirm_evidence_sources is not None
            and evidence_sources == confirm_evidence_sources
        ):
            try:
                requirement_evidence = parse_requirement_evidence(
                    fleets,
                    evidence_sources["ship_details"],
                    evidence_sources["equip_instances"],
                    conditions=ships,
                )
            except RequirementEvidenceError:
                requirement_evidence = None
        # A changed/unavailable equipment read is NOT silently merged: any
        # expedition needing that evidence falls back to UNKNOWN, while an
        # expedition with no equipment/stat requirement is unaffected.

        observed_at = self._clock()
        source_freshness_verified = (
            port_snapshot is not None
            and port_snapshot_matches_current_state(
                port_snapshot,
                fleets,
                ships,
                now=observed_at,
                max_age=self._freshness_max_age,
            )
        )
        try:
            return normalize_poi_fleets(
                fleets,
                ships,
                masters,
                observed_at=observed_at,
                source_freshness_verified=source_freshness_verified,
                requirement_evidence=requirement_evidence,
            )
        except (PoiStateError, ValueError, TypeError, OverflowError) as exc:
            raise PoiMcpError(f"Poi state normalization failed closed: {exc}") from exc
