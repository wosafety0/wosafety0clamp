"""Pure, offline expedition composition checks; never performs game actions."""

from dataclasses import dataclass
from enum import Enum


class MissionEligibilityDecision(str, Enum):
    ELIGIBLE = "eligible"
    BLOCKED_EMPTY_FLEET = "blocked_empty_fleet"
    BLOCKED_TOO_FEW_SHIPS = "blocked_too_few_ships"
    BLOCKED_FLAGSHIP_LEVEL = "blocked_flagship_level"
    BLOCKED_SHIP_TYPE = "blocked_ship_type"


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
        if count > 6:
            raise ValueError("a fleet cannot contain more than six ships")
        if len(self.ship_levels) != count or len(self.ship_types) != count:
            raise ValueError("ship IDs, levels, and types must have matching lengths")
        for label, values in (("ship IDs", self.ship_ids), ("ship levels", self.ship_levels), ("ship types", self.ship_types)):
            if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in values):
                raise ValueError(f"{label} must contain positive integers")
        if len(set(self.ship_ids)) != count:
            raise ValueError("ship IDs must be unique")


@dataclass(frozen=True, slots=True)
class MissionCompositionRequirement:
    """The composition constraints required by one expedition."""

    mission_id: int
    minimum_ships: int
    minimum_flagship_level: int
    allowed_ship_types: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        numeric_fields = (self.mission_id, self.minimum_ships, self.minimum_flagship_level)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in numeric_fields):
            raise ValueError("mission requirement values must be integers")
        if self.mission_id < 1:
            raise ValueError("mission_id must be positive")
        if not 1 <= self.minimum_ships <= 6:
            raise ValueError("minimum_ships must be between 1 and 6")
        if self.minimum_flagship_level < 1:
            raise ValueError("minimum_flagship_level must be positive")
        if self.allowed_ship_types is not None:
            if not isinstance(self.allowed_ship_types, tuple):
                raise ValueError("allowed_ship_types must be a tuple")
            if not self.allowed_ship_types or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 1
                for value in self.allowed_ship_types
            ):
                raise ValueError("allowed_ship_types must contain positive ship type IDs")
            if len(set(self.allowed_ship_types)) != len(self.allowed_ship_types):
                raise ValueError("allowed_ship_types must be unique")


@dataclass(frozen=True, slots=True)
class MissionCompositionPlan:
    mission_id: int
    decision: MissionEligibilityDecision
    reason: str


def evaluate_mission_composition(
    fleet: FleetCompositionSnapshot,
    requirement: MissionCompositionRequirement,
) -> MissionCompositionPlan:
    """Check count, flagship level, and allowed types without inferring actions."""
    def result(decision: MissionEligibilityDecision, reason: str) -> MissionCompositionPlan:
        return MissionCompositionPlan(requirement.mission_id, decision, reason)

    if not fleet.ship_ids:
        return result(MissionEligibilityDecision.BLOCKED_EMPTY_FLEET, "Fleet has no ships.")
    if len(fleet.ship_ids) < requirement.minimum_ships:
        return result(
            MissionEligibilityDecision.BLOCKED_TOO_FEW_SHIPS,
            f"Mission requires at least {requirement.minimum_ships} ships; observed {len(fleet.ship_ids)}.",
        )
    if fleet.ship_levels[0] < requirement.minimum_flagship_level:
        return result(
            MissionEligibilityDecision.BLOCKED_FLAGSHIP_LEVEL,
            f"Flagship level {fleet.ship_levels[0]} is below required level {requirement.minimum_flagship_level}.",
        )
    if requirement.allowed_ship_types is not None:
        allowed = set(requirement.allowed_ship_types)
        incompatible = tuple(
            ship_type for ship_type in fleet.ship_types if ship_type not in allowed
        )
        if incompatible:
            return result(
                MissionEligibilityDecision.BLOCKED_SHIP_TYPE,
                f"Fleet contains disallowed ship type IDs: {incompatible}.",
            )
    return result(
        MissionEligibilityDecision.ELIGIBLE,
        "Observed fleet composition satisfies the supplied mission requirements.",
    )
