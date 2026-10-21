"""Small, dependency-free data models for observed game state."""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class ShipState:
    ship_id: int
    name: str
    hp: int
    max_hp: int

    def __post_init__(self) -> None:
        if self.ship_id < 0:
            raise ValueError("ship_id must be non-negative")
        if not self.name.strip():
            raise ValueError("name must not be empty")
        if self.max_hp < 0 or self.hp < 0 or self.hp > self.max_hp:
            raise ValueError("hp must be between zero and max_hp")


@dataclass(frozen=True, slots=True)
class FleetState:
    fleet_id: int
    ship_ids: tuple[int, ...]
    mission_id: Optional[int] = None

    def __post_init__(self) -> None:
        if self.fleet_id < 1:
            raise ValueError("fleet_id must be positive")
        if len(self.ship_ids) > 6:
            raise ValueError("a fleet cannot contain more than six ships")
        if len(set(self.ship_ids)) != len(self.ship_ids):
            raise ValueError("ship_ids must be unique")
        if self.mission_id is not None and self.mission_id < 0:
            raise ValueError("mission_id must be non-negative")
