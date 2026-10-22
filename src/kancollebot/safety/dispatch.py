"""Fail-closed readiness gate for a future expedition dispatch.

This module only evaluates supplied observations and verification flags. It
never reads POI, schedules actions, or executes gameplay operations.
"""

from __future__ import annotations

import math
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
    """Require HP safety and explicit boolean verification of every other gate.

    Only the literal boolean True passes a verification gate. Truthy values
    such as 1 or "yes" are not accepted as evidence. Defaults fail closed.
    """
    if (
        isinstance(critical_hp_ratio, bool)
        or not isinstance(critical_hp_ratio, (int, float))
        or not math.isfinite(critical_hp_ratio)
        or not 0.0 < critical_hp_ratio <= 1.0
    ):
        raise ValueError("critical_hp_ratio must be a finite number in (0, 1]")

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
    for passed, reason in required_checks:
        if passed is not True:
            suffix = (
                " (verification flag must be boolean True)"
                if not isinstance(passed, bool)
                else ""
            )
            reasons.append(reason + suffix)
    return DispatchReadiness(ready=not reasons, reasons=tuple(reasons))
