"""Read-only parser for the versioned Poi JSON snapshot contract.

This module does not connect to Poi, perform network requests, or issue game actions.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from .models import FleetState, GameSnapshot, ShipState


class SnapshotValidationError(ValueError):
    """Raised when a Poi snapshot is missing, stale, or invalid."""


@dataclass(frozen=True, slots=True)
class MaterialsState:
    fuel: int | None = None
    ammo: int | None = None
    steel: int | None = None
    bauxite: int | None = None


@dataclass(frozen=True, slots=True)
class PoiSnapshot:
    game: GameSnapshot
    materials: MaterialsState = MaterialsState()


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise SnapshotValidationError(f"{field} must be an object")
    return value


def _array(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise SnapshotValidationError(f"{field} must be an array")
    return value


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    # bool is a subclass of int in Python, but is not a valid numeric ID/value.
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SnapshotValidationError(f"{field} must be an integer >= {minimum}")
    return value


def _optional_integer(value: Any, field: str, *, minimum: int = 0) -> int | None:
    if value is None:
        return None
    return _integer(value, field, minimum=minimum)


def _name(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SnapshotValidationError(f"{field} must be a non-empty string")
    return value


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SnapshotValidationError("observed_at must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SnapshotValidationError("observed_at is not valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SnapshotValidationError("observed_at must include a timezone")
    return parsed


def parse_snapshot(
    payload: Mapping[str, Any],
    *,
    now: datetime | None = None,
    max_age: timedelta = timedelta(minutes=2),
    allowed_future_skew: timedelta = timedelta(seconds=30),
) -> PoiSnapshot:
    """Validate and normalize one snapshot document.

    Time is injectable for deterministic tests. Snapshots that are too old or
    implausibly far in the future are rejected (fail closed).
    """
    root = _mapping(payload, "snapshot")
    version = root.get("schema_version")
    if isinstance(version, bool) or version != 1:
        raise SnapshotValidationError("unsupported schema_version; expected 1")

    observed_at = _timestamp(root.get("observed_at"))
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise SnapshotValidationError("now must include a timezone")
    age = current.astimezone(timezone.utc) - observed_at.astimezone(timezone.utc)
    if age > max_age:
        raise SnapshotValidationError("snapshot is stale")
    if age < -allowed_future_skew:
        raise SnapshotValidationError("observed_at is too far in the future")

    source = _name(root.get("source", "poi-plugin"), "source")
    raw_ships = _array(root.get("ships"), "ships")
    raw_fleets = _array(root.get("fleets"), "fleets")

    ships: list[ShipState] = []
    for index, raw in enumerate(raw_ships):
        field = f"ships[{index}]"
        item = _mapping(raw, field)
        try:
            ship = ShipState(
                ship_id=_integer(item.get("ship_id"), f"{field}.ship_id", minimum=1),
                name=_name(item.get("name"), f"{field}.name"),
                hp=_integer(item.get("hp"), f"{field}.hp"),
                max_hp=_integer(item.get("max_hp"), f"{field}.max_hp", minimum=1),
                condition=_optional_integer(item.get("condition"), f"{field}.condition"),
                fuel=_optional_integer(item.get("fuel"), f"{field}.fuel"),
                ammo=_optional_integer(item.get("ammo"), f"{field}.ammo"),
            )
        except ValueError as exc:
            raise SnapshotValidationError(str(exc)) from exc
        ships.append(ship)

    fleets: list[FleetState] = []
    for index, raw in enumerate(raw_fleets):
        field = f"fleets[{index}]"
        item = _mapping(raw, field)
        raw_ship_ids = _array(item.get("ship_ids"), f"{field}.ship_ids")
        ship_ids = tuple(
            _integer(ship_id, f"{field}.ship_ids[{position}]", minimum=1)
            for position, ship_id in enumerate(raw_ship_ids)
        )
        in_battle = item.get("in_battle", False)
        if not isinstance(in_battle, bool):
            raise SnapshotValidationError(f"{field}.in_battle must be a boolean")
        expedition_id = _optional_integer(
            item.get("expedition_id"), f"{field}.expedition_id", minimum=1
        )
        try:
            fleet = FleetState(
                fleet_id=_integer(item.get("fleet_id"), f"{field}.fleet_id", minimum=1),
                name=_name(item.get("name"), f"{field}.name"),
                ship_ids=ship_ids,
                expedition_id=expedition_id,
                in_battle=in_battle,
            )
        except ValueError as exc:
            raise SnapshotValidationError(str(exc)) from exc
        fleets.append(fleet)

    try:
        game = GameSnapshot(
            observed_at=observed_at,
            ships=tuple(ships),
            fleets=tuple(fleets),
            source=source,
        )
    except ValueError as exc:
        raise SnapshotValidationError(str(exc)) from exc

    raw_materials = root.get("materials")
    if raw_materials is None:
        materials = MaterialsState()
    else:
        material_obj = _mapping(raw_materials, "materials")
        materials = MaterialsState(
            fuel=_optional_integer(material_obj.get("fuel"), "materials.fuel"),
            ammo=_optional_integer(material_obj.get("ammo"), "materials.ammo"),
            steel=_optional_integer(material_obj.get("steel"), "materials.steel"),
            bauxite=_optional_integer(material_obj.get("bauxite"), "materials.bauxite"),
        )

    return PoiSnapshot(game=game, materials=materials)


def parse_snapshot_text(
    text: str,
    *,
    now: datetime | None = None,
    max_age: timedelta = timedelta(minutes=2),
) -> PoiSnapshot:
    """Parse JSON text and validate the resulting snapshot."""
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise SnapshotValidationError("snapshot is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise SnapshotValidationError("snapshot root must be an object")
    return parse_snapshot(payload, now=now, max_age=max_age)


def read_snapshot(
    path: str | Path,
    *,
    now: datetime | None = None,
    max_age: timedelta = timedelta(minutes=2),
) -> PoiSnapshot:
    """Read a snapshot file; missing/unreadable files fail explicitly."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise SnapshotValidationError("snapshot file is missing or unreadable") from exc
    return parse_snapshot_text(text, now=now, max_age=max_age)
