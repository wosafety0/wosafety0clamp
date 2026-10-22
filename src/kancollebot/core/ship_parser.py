"""Strict, schema-configurable parser for raw POI ship records.

POI field names are intentionally supplied by the caller because the current
repository does not yet contain an authoritative raw /ships field sample.
No missing or malformed value is inferred or silently defaulted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from kancollebot.core.models import ShipState


@dataclass(frozen=True, slots=True)
class ShipFieldMap:
    name: str
    hp: str
    max_hp: str

    def __post_init__(self) -> None:
        values = (self.name, self.hp, self.max_hp)
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("ship field names must be non-empty strings")
        if len(set(values)) != len(values):
            raise ValueError("ship field names must be distinct")


def parse_ship_payload(
    payload: Any,
    *,
    fields: ShipFieldMap,
) -> dict[int, ShipState]:
    """Parse an object keyed by positive ship IDs using an explicit field map."""
    if not isinstance(payload, dict):
        raise ValueError("POI ships payload must be an object keyed by ship ID")

    parsed: dict[int, ShipState] = {}
    for raw_id, record in payload.items():
        if not isinstance(raw_id, str) or not raw_id.isdecimal():
            raise ValueError("POI ships keys must be decimal ship IDs")
        ship_id = int(raw_id)
        if ship_id <= 0:
            raise ValueError("POI ship IDs must be positive")
        if ship_id in parsed:
            raise ValueError(f"duplicate ship ID {ship_id}")
        if not isinstance(record, dict):
            raise ValueError(f"POI ship record {raw_id} must be an object")

        missing = [key for key in (fields.name, fields.hp, fields.max_hp) if key not in record]
        if missing:
            raise ValueError(f"POI ship record {raw_id} is missing required fields: {missing}")

        name = record[fields.name]
        hp = record[fields.hp]
        max_hp = record[fields.max_hp]
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"POI ship record {raw_id} has an invalid name")
        if type(hp) is not int or type(max_hp) is not int:
            raise ValueError(f"POI ship record {raw_id} HP fields must be integers")
        if max_hp <= 0 or hp < 0 or hp > max_hp:
            raise ValueError(f"POI ship record {raw_id} has invalid HP values")

        parsed[ship_id] = ShipState(
            ship_id=ship_id,
            name=name,
            hp=hp,
            max_hp=max_hp,
        )

    return parsed
