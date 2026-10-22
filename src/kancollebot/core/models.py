"""Small, dependency-free data models for observed game state."""

from dataclasses import dataclass
from typing import Optional


def _require_integer(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")


@dataclass(frozen=True, slots=True)
class ShipState:
    ship_id: int
    name: str
    hp: int
    max_hp: int

    def __post_init__(self) -> None:
        _require_integer(self.ship_id, "ship_id")
        _require_integer(self.hp, "hp")
        _require_integer(self.max_hp, "max_hp")
        if self.ship_id < 0:
            raise ValueError("ship_id must be non-negative")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must be a non-empty string")
        if self.max_hp < 0 or self.hp < 0 or self.hp > self.max_hp:
            raise ValueError("hp must be between zero and max_hp")


@dataclass(frozen=True, slots=True)
class FleetState:
    fleet_id: int
    ship_ids: tuple[int, ...]
    mission_id: Optional[int] = None

    def __post_init__(self) -> None:
        _require_integer(self.fleet_id, "fleet_id")
        if self.fleet_id < 1:
            raise ValueError("fleet_id must be positive")
        if not isinstance(self.ship_ids, tuple):
            raise ValueError("ship_ids must be a tuple")
        if any(isinstance(ship_id, bool) or not isinstance(ship_id, int) for ship_id in self.ship_ids):
            raise ValueError("ship_ids must contain only integers")
        if any(ship_id <= 0 for ship_id in self.ship_ids):
            raise ValueError("ship_ids must be positive")
        if len(self.ship_ids) > 6:
            raise ValueError("a fleet cannot contain more than six ships")
        if len(set(self.ship_ids)) != len(self.ship_ids):
            raise ValueError("ship_ids must be unique")
        if self.mission_id is not None:
            _require_integer(self.mission_id, "mission_id")
            if self.mission_id < 0:
                raise ValueError("mission_id must be non-negative")
