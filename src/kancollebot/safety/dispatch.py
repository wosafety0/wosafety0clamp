"""Fail-closed readiness gate for a future expedition dispatch.

This module only evaluates supplied observations and verification flags. It
never reads POI, schedules actions, or executes gameplay operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from kancollebot.core.fleet_parser import FleetObservation
from kancollebot.core.models import ShipState
from kancollebot.safety.fleet import evaluate_fleet_safety


@dataclass(frozen=True, slots=True)
class DispatchReadiness:
    ready: bool
    reasons: tuple[str, ...]


def evaluate_dispatch_readiness(
    fleet: FleetObservation,
    ships_by_id: Mapping[int, ShipState],
    *,
    supply_verified: bool = False,
    repair_status_verified: bool = False,
    mission_composition_verified: bool = False,
    rotation_verified: bool = False,
    critical_hp_ratio: float = 0.25,
) -> DispatchReadiness:
    """Require HP safety and explicit verification of all other dispatch gates.

    The boolean flags mean the corresponding check has been performed and
    passed; they do not mean merely that data is available. Defaults fail
    closed so callers cannot accidentally treat unchecked conditions as safe.
    """
    hp_result = evaluate_fleet_safety(
        fleet, ships_by_id, critical_hp_ratio=critical_hp_ratio
    )
    reasons = list(hp_result.reasons)
    required_checks = (
        (supply_verified, "supply state has not been verified"),
        (repair_status_verified, "repair/docking status has not been verified"),
        (mission_composition_verified, "mission fleet requirements have not been verified"),
        (rotation_verified, "fleet rotation constraints have not been verified"),
    )
    reasons.extend(reason for passed, reason in required_checks if not passed)
    return DispatchReadiness(ready=not reasons, reasons=tuple(reasons))
