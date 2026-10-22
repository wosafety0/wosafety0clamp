"""Fail-closed safety checks for normalized ship observations.

This module does not parse POI-specific field names and never performs I/O.
Callers must first validate raw POI records into ShipState objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from kancollebot.core.fleet_parser import FleetObservation
from kancollebot.core.models import ShipState


@dataclass(frozen=True, slots=True)
class FleetSafetyResult:
    safe: bool
    reasons: tuple[str, ...]


def evaluate_fleet_safety(
    fleet: FleetObservation,
    ships_by_id: Mapping[int, ShipState],
    *,
    critical_hp_ratio: float = 0.25,
) -> FleetSafetyResult:
    """Check known ship IDs and HP before considering an expedition dispatch.

    An empty fleet, missing ship record, invalid threshold, zero-HP ship, or
    ship at/below the critical HP ratio blocks the recommendation. Supply,
    repair/docking state, and mission composition remain explicitly unchecked.
    """
    if not 0.0 < critical_hp_ratio <= 1.0:
        raise ValueError("critical_hp_ratio must be in (0, 1]")

    reasons: list[str] = []
    ship_ids = fleet.occupied_ship_ids
    if not ship_ids:
        reasons.append("fleet is empty")

    for ship_id in ship_ids:
        ship = ships_by_id.get(ship_id)
        if ship is None:
            reasons.append(f"ship {ship_id} has no validated state")
            continue
        if ship.ship_id != ship_id:
            reasons.append(f"ship mapping key {ship_id} does not match record ID {ship.ship_id}")
            continue
        if ship.max_hp <= 0:
            reasons.append(f"ship {ship_id} has invalid max_hp")
            continue
        if ship.hp == 0:
            reasons.append(f"ship {ship_id} has zero HP")
        elif ship.hp / ship.max_hp <= critical_hp_ratio:
            reasons.append(f"ship {ship_id} is at or below the critical HP threshold")

    return FleetSafetyResult(safe=not reasons, reasons=tuple(reasons))
