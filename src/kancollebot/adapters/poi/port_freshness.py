"""Validate a recent passive port snapshot against a current Poi store read.

This helper is intentionally not wired into the production freshness gate.
It validates consistency claims, not source authenticity. A disk JSON file is
not an authenticated witness because its timestamps and fields can be edited.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_SHIP_FIELDS = (
    "api_id",
    "api_ship_id",
    "api_nowhp",
    "api_maxhp",
    "api_fuel",
    "api_bull",
)
_OPTIONAL_SHIP_FIELDS = ("api_cond",)
_REQUIRED_FLEET_FIELDS = ("api_id", "api_mission", "api_ship")
_CACHE_FLAGS = ("fromDiskCache", "fromServiceWorker", "fromPrefetchCache")
_SAFETY_FLAGS = (
    "gameInputSent",
    "gameApiRequestInitiated",
    "runtimeEvaluateUsed",
    "fullRawResponseSaved",
    "productionFreshnessGateChanged",
)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _is_int(value: Any, *, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _valid_mission_slots(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 4
        and all(_is_int(slot, minimum=0) for slot in value)
    )


def _valid_fleet_ship_slots(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= 6
        and all(
            _is_int(slot, minimum=1) or (type(slot) is int and slot == -1)
            for slot in value
        )
    )


def port_snapshot_matches_current_state(
    snapshot: Any,
    current_fleets: Any,
    current_ships: Any,
    *,
    now: datetime,
    max_age: timedelta,
) -> bool:
    """Validate shape, age, non-cache metadata, and exact captured-field equality.

    The raw response hash cannot be recomputed because the raw body is
    intentionally not retained. This function does not authenticate the
    snapshot, prove an atomic Redux snapshot, or set a production freshness
    flag. Only pass a snapshot object delivered directly by a trusted live
    observer in the current process; never treat a disk snapshot as proof.
    Invalid or incomplete snapshot data fails closed with False.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if max_age < timedelta(0):
        raise ValueError("max_age must not be negative")
    if not isinstance(snapshot, dict):
        return False
    if not _is_int(snapshot.get("schemaVersion"), minimum=1) or snapshot["schemaVersion"] != 1:
        return False
    if snapshot.get("verificationStatus") != "CAPTURED_UNDER_STATICALLY_REVIEWED_PROBE":
        return False

    source = snapshot.get("source")
    if not isinstance(source, dict):
        return False
    if source.get("kind") != "cdp-network-response":
        return False
    if source.get("path") != "/kcsapi/api_port/port" or source.get("httpStatus") != 200:
        return False
    host = source.get("host")
    if not isinstance(host, str):
        return False
    normalized_host = host.lower().rstrip(".")
    if not (
        normalized_host == "kancolle-server.com"
        or normalized_host.endswith(".kancolle-server.com")
    ):
        return False
    if any(source.get(flag) is not False for flag in _CACHE_FLAGS):
        return False
    digest = source.get("responseBodySha256")
    body_bytes = source.get("responseBodyBytes")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        return False
    if not _is_int(body_bytes, minimum=1):
        return False

    received_at = _parse_timestamp(source.get("responseReceivedAt"))
    captured_at = _parse_timestamp(source.get("bodyCapturedAt"))
    if received_at is None or captured_at is None or received_at > captured_at:
        return False
    received_age = now - received_at
    captured_age = now - captured_at
    if (
        received_age < timedelta(0)
        or received_age > max_age
        or captured_age < timedelta(0)
        or captured_age > max_age
    ):
        return False

    safety = snapshot.get("safety")
    if not isinstance(safety, dict) or any(
        safety.get(flag) is not False for flag in _SAFETY_FLAGS
    ):
        return False

    data = snapshot.get("data")
    summary = snapshot.get("summary")
    if not isinstance(data, dict) or not isinstance(summary, dict):
        return False
    witness_fleets = data.get("api_deck_port")
    witness_ships = data.get("api_ship")
    if not isinstance(witness_fleets, list) or not witness_fleets:
        return False
    if not isinstance(witness_ships, dict) or not witness_ships:
        return False
    if not _is_int(summary.get("fleetCount"), minimum=1):
        return False
    if not _is_int(summary.get("shipCount"), minimum=1):
        return False
    if summary["fleetCount"] != len(witness_fleets):
        return False
    if summary["shipCount"] != len(witness_ships):
        return False
    if not isinstance(current_fleets, list) or not isinstance(current_ships, dict):
        return False
    if len(current_fleets) != len(witness_fleets):
        return False

    def index_fleets(items: list[Any]) -> dict[int, dict[str, Any]] | None:
        indexed: dict[int, dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict) or not _is_int(item.get("api_id"), minimum=1):
                return None
            fleet_id = item["api_id"]
            if fleet_id in indexed:
                return None
            indexed[fleet_id] = item
        return indexed

    indexed_witness_fleets = index_fleets(witness_fleets)
    indexed_current_fleets = index_fleets(current_fleets)
    if indexed_witness_fleets is None or indexed_current_fleets is None:
        return False
    if indexed_witness_fleets.keys() != indexed_current_fleets.keys():
        return False

    witness_ship_ids: set[str] = set()
    for key, record in witness_ships.items():
        if not isinstance(key, str) or not key.isdigit() or not isinstance(record, dict):
            return False
        for field in _REQUIRED_SHIP_FIELDS:
            minimum = 1 if field in ("api_id", "api_ship_id") else 0
            if not _is_int(record.get(field), minimum=minimum):
                return False
        if str(record["api_id"]) != key or key in witness_ship_ids:
            return False
        if "api_cond" in record and not _is_int(record["api_cond"], minimum=0):
            return False
        witness_ship_ids.add(key)

    normalized_current_ships: dict[str, dict[str, Any]] = {}
    for key, record in current_ships.items():
        normalized_key = str(key)
        if (
            not normalized_key.isdigit()
            or not isinstance(record, dict)
            or normalized_key in normalized_current_ships
        ):
            return False
        normalized_current_ships[normalized_key] = record
    if normalized_current_ships.keys() != witness_ships.keys():
        return False

    for fleet_id, witness in indexed_witness_fleets.items():
        current = indexed_current_fleets[fleet_id]
        if not all(field in witness and field in current for field in _REQUIRED_FLEET_FIELDS):
            return False
        if not _valid_mission_slots(witness.get("api_mission")) or not _valid_mission_slots(current.get("api_mission")):
            return False
        if not _valid_fleet_ship_slots(witness.get("api_ship")) or not _valid_fleet_ship_slots(current.get("api_ship")):
            return False
        if any(witness[field] != current[field] for field in _REQUIRED_FLEET_FIELDS):
            return False
        if "api_name" in witness and witness.get("api_name") != current.get("api_name"):
            return False
        slots = witness.get("api_ship")
        if not isinstance(slots, list) or len(slots) > 6:
            return False
        for ship_id in slots:
            if (not _is_int(ship_id, minimum=1) and not (type(ship_id) is int and ship_id == -1)):
                return False
            if ship_id != -1 and str(ship_id) not in witness_ship_ids:
                return False

    for key, witness in witness_ships.items():
        current = normalized_current_ships[key]
        fields = _REQUIRED_SHIP_FIELDS + tuple(
            field for field in _OPTIONAL_SHIP_FIELDS if field in witness
        )
        for field in fields:
            if field not in current:
                return False
            minimum = 1 if field in ("api_id", "api_ship_id") else 0
            if not _is_int(current.get(field), minimum=minimum):
                return False
            if witness[field] != current[field]:
                return False

    return True
