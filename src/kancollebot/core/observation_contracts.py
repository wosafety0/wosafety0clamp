"""Task-neutral shapes for OBSERVED game state.

This module is deliberately at the bottom of the dependency graph. Adapters may
import it; it imports no adapter, task-runtime or ``expeditions`` module. What
lives here is a DATA SHAPE, not a decision: a UI two-frame checkpoint, a fleet
composition, a dock inventory entry, and a Poi fleet observation.

Boundary note (P0 review R1/R8): moving these shapes here fixes the *import
direction* (``adapters`` no longer imports ``expeditions``). It does NOT
reclassify them as shareable by a second task. ``SHARED_CAPABILITIES`` still
marks ``fleet_snapshot`` / ``resupply_preflight`` / ``supply_tab_coordinates``
as ``needs_decoupling``: the "fleet is busy" predicate, morale/HP fields and
per-screen coordinates have not been decoupled. In particular
``ObservedFleetSnapshot`` still carries the legacy ``api_mission`` tuple
fields; consumers must not interpret them for combat/sparkle/quest automation.

Nothing here opens a connection, sends input, reads SQLite or performs a game
action.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime


MAX_FLEET_SIZE = 6


@dataclass(frozen=True, slots=True)
class UiStateEvidence:
    """Evidence returned by a UI driver after its two-frame state gate passes."""

    state: str
    consecutive_states: tuple[str, str]
    evidence: str

    def __post_init__(self) -> None:
        if not isinstance(self.state, str) or not self.state.strip():
            raise ValueError("state must be a non-empty string")
        if (
            not isinstance(self.consecutive_states, tuple)
            or len(self.consecutive_states) != 2
            or any(not isinstance(item, str) or not item.strip() for item in self.consecutive_states)
        ):
            raise ValueError("consecutive_states must contain exactly two state labels")
        if not isinstance(self.evidence, str) or not self.evidence.strip():
            raise ValueError("evidence must be a non-empty string")


@dataclass(frozen=True, slots=True)
class FleetCompositionSnapshot:
    """Observed ship levels and master ship types in current fleet order."""

    ship_ids: tuple[int, ...]
    ship_levels: tuple[int, ...]
    ship_types: tuple[int, ...]

    def __post_init__(self) -> None:
        fields = (self.ship_ids, self.ship_levels, self.ship_types)
        if any(not isinstance(values, tuple) for values in fields):
            raise ValueError("ship IDs, levels, and types must be tuples")
        count = len(self.ship_ids)
        if count > MAX_FLEET_SIZE:
            raise ValueError("a fleet cannot contain more than six ships")
        if len(self.ship_levels) != count or len(self.ship_types) != count:
            raise ValueError("ship IDs, levels, and types must have matching lengths")
        for label, values in (("ship IDs", self.ship_ids), ("ship levels", self.ship_levels), ("ship types", self.ship_types)):
            if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in values):
                raise ValueError(f"{label} must contain positive integers")
        if len(set(self.ship_ids)) != count:
            raise ValueError("ship IDs must be unique")


class DockInventoryError(ValueError):
    """Dock inventory does not meet the observation contract."""


@dataclass(frozen=True, slots=True)
class DockShip:
    """One unassigned ship available in the dock."""

    ship_id: int
    master_id: int
    ship_type: int
    level: int

    def __post_init__(self) -> None:
        for label, value in (
            ("ship_id", self.ship_id),
            ("master_id", self.master_id),
            ("ship_type", self.ship_type),
            ("level", self.level),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise DockInventoryError(f"{label} must be a positive integer")


def dock_ships_from_poi_payloads(
    fleets_payload: Sequence[object],
    ships_payload: object,
    master_ships_payload: object,
) -> tuple[DockShip, ...]:
    """Build dock entries from already-fetched read-only Poi payloads.

    Accepts the same shapes as ``normalize_poi_fleets``: an ``info.fleets``
    array, an ``info.ships`` numeric-key object map, and ``poi_lookup`` master
    records that include ``api_stype``. This performs no I/O and returns only
    ships that no fleet currently references.
    """
    if not isinstance(fleets_payload, list):
        raise DockInventoryError("info.fleets must be an array")
    if not isinstance(ships_payload, dict) or any(
        not str(key).isdigit() for key in ships_payload
    ):
        raise DockInventoryError("info.ships must be an object keyed by numeric IDs")
    if not isinstance(master_ships_payload, dict) or any(
        not str(key).isdigit() for key in master_ships_payload
    ):
        raise DockInventoryError("master ship records must be keyed by numeric IDs")

    assigned: set[int] = set()
    for index, fleet in enumerate(fleets_payload):
        if not isinstance(fleet, dict):
            raise DockInventoryError(f"fleet entry {index} is not an object")
        raw_slots = fleet.get("api_ship")
        if not isinstance(raw_slots, list) or len(raw_slots) > MAX_FLEET_SIZE:
            raise DockInventoryError(f"fleet entry {index} has an invalid api_ship array")
        for raw_id in raw_slots:
            if isinstance(raw_id, bool) or not isinstance(raw_id, int):
                raise DockInventoryError(f"fleet entry {index} has a non-integer ship slot")
            if raw_id == -1:
                continue
            if raw_id < 1:
                raise DockInventoryError(f"fleet entry {index} has an invalid empty-slot marker")
            assigned.add(raw_id)

    dock: list[DockShip] = []
    for key, ship in sorted(ships_payload.items(), key=lambda item: int(item[0])):
        if not isinstance(ship, dict):
            raise DockInventoryError(f"ship instance {key} is not an object")
        ship_id = ship.get("api_id")
        if isinstance(ship_id, bool) or not isinstance(ship_id, int) or ship_id < 1:
            raise DockInventoryError(f"ship instance {key} has an invalid api_id")
        if str(ship_id) != str(key):
            raise DockInventoryError(f"ship instance {key} does not match its api_id {ship_id}")
        if ship_id in assigned:
            continue
        master_id = ship.get("api_ship_id")
        level = ship.get("api_lv")
        if isinstance(master_id, bool) or not isinstance(master_id, int) or master_id < 1:
            raise DockInventoryError(f"ship instance {ship_id} has an invalid api_ship_id")
        if isinstance(level, bool) or not isinstance(level, int) or level < 1:
            raise DockInventoryError(f"ship instance {ship_id} has an invalid api_lv")
        master = master_ships_payload.get(str(master_id))
        if not isinstance(master, dict):
            raise DockInventoryError(
                f"master ship record {master_id} is missing for dock ship {ship_id}"
            )
        ship_type = master.get("api_stype")
        if isinstance(ship_type, bool) or not isinstance(ship_type, int) or ship_type < 1:
            raise DockInventoryError(
                f"master ship record {master_id} has an invalid api_stype"
            )
        dock.append(DockShip(ship_id, master_id, ship_type, level))
    return tuple(dock)


@dataclass(frozen=True, slots=True)
class ObservedFleetSnapshot:
    """Raw Poi fleet observation, kept task-neutral as a DATA SHAPE.

    Legacy name: ``ExpeditionFleetSnapshot`` (re-exported from
    ``kancollebot.expeditions.planner`` for backwards compatibility).

    The ``mission_*`` / ``return_at`` fields are the legacy ``api_mission``
    four-tuple payload AS OBSERVED, not an expedition decision. They are kept
    byte-for-byte to avoid changing any existing readiness evaluation. No
    second task may interpret them until the ``needs_decoupling`` work in
    docs/DESIGN_multi_task_runtime.md (B) is done and re-verified.
    """

    fleet_id: int
    ship_ids: tuple[int, ...]
    mission_id: int | None = None
    return_at: datetime | None = None
    fuel_ready: bool = False
    ammo_ready: bool = False
    damaged_ship_ids: tuple[int, ...] = ()
    # Ships at or below 25% HP (大破 / heavy damage / Taiha). Only these block
    # dispatch; 小破/中破 (damaged but above 25%) are allowed by operator policy.
    heavy_damage_ship_ids: tuple[int, ...] = ()
    mission_status: int | None = None
    reserved: int = 0
    ship_levels: tuple[int, ...] = ()
    ship_types: tuple[int, ...] = ()
    # R3-I: equipment master ids, fleet stat totals, morale and dock state are
    # READ IN THE SAME OBSERVATION as ship_ids/ship_levels/ship_types. Keeping
    # them on this record is what prevents the requirement evaluator from
    # mixing a fresh composition with equipment read at a different instant.
    # ``None`` means the evidence could not be established; the evaluator must
    # then answer UNKNOWN for the affected conditions, never guess.
    equipment_by_ship: Mapping[int, tuple[int, ...]] | None = None
    total_stats: Mapping[str, int] | None = None
    conditions: Mapping[int, int] | None = None
    in_dock_ship_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.fleet_id < 1:
            raise ValueError("fleet_id must be positive")
        if len(self.ship_ids) > MAX_FLEET_SIZE:
            raise ValueError("a fleet cannot contain more than six ships")
        if any(ship_id < 1 for ship_id in self.ship_ids):
            raise ValueError("ship_ids must be positive")
        if len(set(self.ship_ids)) != len(self.ship_ids):
            raise ValueError("ship_ids must be unique")
        if len(set(self.damaged_ship_ids)) != len(self.damaged_ship_ids):
            raise ValueError("damaged_ship_ids must be unique")
        if not set(self.damaged_ship_ids).issubset(self.ship_ids):
            raise ValueError("damaged_ship_ids must belong to this fleet")
        if len(set(self.heavy_damage_ship_ids)) != len(self.heavy_damage_ship_ids):
            raise ValueError("heavy_damage_ship_ids must be unique")
        if not set(self.heavy_damage_ship_ids).issubset(self.ship_ids):
            raise ValueError("heavy_damage_ship_ids must belong to this fleet")
        if not set(self.heavy_damage_ship_ids).issubset(self.damaged_ship_ids):
            raise ValueError(
                "heavy_damage_ship_ids must also be listed in damaged_ship_ids"
            )
        if self.mission_status not in (None, 0, 1, 2):
            raise ValueError("mission_status must be 0, 1, 2, or unknown")
        if isinstance(self.reserved, bool) or not isinstance(self.reserved, int) or self.reserved < 0:
            raise ValueError("reserved must be a non-negative integer")
        if not isinstance(self.ship_levels, tuple) or not isinstance(self.ship_types, tuple):
            raise ValueError("ship_levels and ship_types must be tuples")
        if bool(self.ship_levels) != bool(self.ship_types):
            raise ValueError("ship_levels and ship_types must both be present or both be empty")
        if self.ship_levels or self.ship_types:
            if len(self.ship_levels) != len(self.ship_ids) or len(self.ship_types) != len(self.ship_ids):
                raise ValueError("composition metadata must match ordered ship membership")
            for label, values in (("ship levels", self.ship_levels), ("ship types", self.ship_types)):
                if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in values):
                    raise ValueError(f"{label} must contain positive integers")
        if self.mission_status == 0 and self.mission_id is not None:
            raise ValueError("idle mission_status cannot have an active mission")
        if self.mission_status in (1, 2) and self.mission_id is None:
            raise ValueError("active mission_status requires a mission ID")
        if self.mission_id is not None and self.mission_id < 1:
            raise ValueError("mission_id must be positive")
        if self.return_at is not None and (
            self.return_at.tzinfo is None or self.return_at.utcoffset() is None
        ):
            raise ValueError("return_at must be timezone-aware")
        if self.mission_id is None and self.return_at is not None:
            raise ValueError("return_at requires an active mission")
        if self.equipment_by_ship is not None:
            if not isinstance(self.equipment_by_ship, Mapping):
                raise ValueError("equipment_by_ship must be a mapping or None")
            if set(self.equipment_by_ship) != set(self.ship_ids):
                raise ValueError("equipment_by_ship must cover exactly the fleet members")
            for ship_id, equipment in self.equipment_by_ship.items():
                if not isinstance(equipment, tuple) or any(
                    isinstance(item, bool) or not isinstance(item, int) or item < 1
                    for item in equipment
                ):
                    raise ValueError(f"ship {ship_id} equipment must be master-id tuples")
        if self.total_stats is not None:
            if not isinstance(self.total_stats, Mapping):
                raise ValueError("total_stats must be a mapping or None")
            for value in self.total_stats.values():
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError("total_stats values must be non-negative integers")
        if self.conditions is not None:
            if not isinstance(self.conditions, Mapping):
                raise ValueError("conditions must be a mapping or None")
            if not set(self.conditions).issubset(self.ship_ids):
                raise ValueError("conditions must only describe fleet members")
        if not isinstance(self.in_dock_ship_ids, tuple):
            raise ValueError("in_dock_ship_ids must be a tuple")
        if not set(self.in_dock_ship_ids).issubset(self.ship_ids):
            raise ValueError("in_dock_ship_ids must belong to this fleet")


__all__ = [
    "MAX_FLEET_SIZE",
    "UiStateEvidence",
    "FleetCompositionSnapshot",
    "DockInventoryError",
    "DockShip",
    "dock_ships_from_poi_payloads",
    "ObservedFleetSnapshot",
]
