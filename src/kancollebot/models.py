"""Normalized, read-only game state models.

These models deliberately do not issue commands to POI or the game client.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ShipState:
    ship_id: int
    name: str
    hp: int
    max_hp: int
    condition: int | None = None
    fuel: int | None = None
    ammo: int | None = None

    def __post_init__(self) -> None:
        if self.ship_id <= 0:
            raise ValueError("ship_id must be positive")
        if not self.name.strip():
            raise ValueError("name must not be empty")
        if self.max_hp <= 0:
            raise ValueError("max_hp must be positive")
        if not 0 <= self.hp <= self.max_hp:
            raise ValueError("hp must be between 0 and max_hp")
        if self.condition is not None and not 0 <= self.condition <= 100:
            raise ValueError("condition must be between 0 and 100")
        if self.fuel is not None and self.fuel < 0:
            raise ValueError("fuel must not be negative")
        if self.ammo is not None and self.ammo < 0:
            raise ValueError("ammo must not be negative")


@dataclass(frozen=True, slots=True)
class FleetState:
    fleet_id: int
    name: str
    ship_ids: tuple[int, ...]
    expedition_id: int | None = None
    in_battle: bool = False

    def __post_init__(self) -> None:
        if self.fleet_id <= 0:
            raise ValueError("fleet_id must be positive")
        if not self.name.strip():
            raise ValueError("name must not be empty")
        if any(ship_id <= 0 for ship_id in self.ship_ids):
            raise ValueError("ship_ids must contain only positive IDs")
        if len(set(self.ship_ids)) != len(self.ship_ids):
            raise ValueError("ship_ids must not contain duplicates")
        if self.expedition_id is not None and self.expedition_id <= 0:
            raise ValueError("expedition_id must be positive when provided")


@dataclass(frozen=True, slots=True)
class GameSnapshot:
    """A point-in-time observation; constructing it performs no game actions."""

    observed_at: datetime
    ships: tuple[ShipState, ...]
    fleets: tuple[FleetState, ...]
    source: str = "poi"

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        ship_ids = [ship.ship_id for ship in self.ships]
        fleet_ids = [fleet.fleet_id for fleet in self.fleets]
        if len(ship_ids) != len(set(ship_ids)):
            raise ValueError("snapshot contains duplicate ship IDs")
        if len(fleet_ids) != len(set(fleet_ids)):
            raise ValueError("snapshot contains duplicate fleet IDs")
        if not self.source.strip():
            raise ValueError("source must not be empty")
