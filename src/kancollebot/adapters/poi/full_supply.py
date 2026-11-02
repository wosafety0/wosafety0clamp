"""Fail-closed preflight and verification for the fleet-wide resupply UI action.

This module is pure validation: it never sends input or calls game APIs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


class FullSupplyPreflightError(RuntimeError):
    """Fleet-wide resupply cannot safely proceed."""


@dataclass(frozen=True, slots=True)
class FleetSupplyCost:
    fleet_id: int
    ship_count: int
    fuel_required: int
    ammo_required: int


@dataclass(frozen=True, slots=True)
class FullSupplyPlan:
    fleet_ids: tuple[int, ...]
    ship_ids: tuple[int, ...]
    fleet_costs: tuple[FleetSupplyCost, ...]
    fuel_required: int
    ammo_required: int
    fuel_available: int
    ammo_available: int

    @property
    def ship_count(self) -> int:
        return len(self.ship_ids)


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FullSupplyPreflightError(f"{label} must be a non-negative integer")
    return value


def build_full_supply_plan(
    fleets: Any,
    ships: Any,
    master_ships: Any,
    resources: Any,
    *,
    source_freshness_verified: bool,
    observed_at: datetime,
    now: datetime,
    max_age: timedelta = timedelta(seconds=30),
    expected_fleet_ids: tuple[int, ...] = (1, 2, 3),
) -> FullSupplyPlan:
    """Calculate all-fleet fuel/ammo cost only from a recent verified observation."""
    if source_freshness_verified is not True:
        raise FullSupplyPreflightError("live port freshness witness is not verified")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise FullSupplyPreflightError("observation timestamp must be timezone-aware")
    if max_age < timedelta(0):
        raise ValueError("max_age must not be negative")
    age = now - observed_at
    if age < timedelta(0) or age > max_age:
        raise FullSupplyPreflightError("live port freshness witness is stale or future-dated")
    if not isinstance(expected_fleet_ids, tuple) or not expected_fleet_ids:
        raise ValueError("expected_fleet_ids must be a non-empty tuple")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in expected_fleet_ids):
        raise ValueError("expected fleet IDs must be positive integers")
    if len(set(expected_fleet_ids)) != len(expected_fleet_ids):
        raise ValueError("expected fleet IDs must be unique")
    if not isinstance(fleets, list) or not isinstance(ships, dict) or not isinstance(master_ships, dict):
        raise FullSupplyPreflightError("fleet, ship, or master data has an invalid shape")
    if not isinstance(resources, list) or len(resources) < 2:
        raise FullSupplyPreflightError("resource inventory is incomplete")
    fuel_available = _nonnegative_int(resources[0], "fuel inventory")
    ammo_available = _nonnegative_int(resources[1], "ammo inventory")

    indexed_fleets: dict[int, dict[str, Any]] = {}
    for fleet in fleets:
        if not isinstance(fleet, dict):
            raise FullSupplyPreflightError("fleet record is invalid")
        fleet_id = fleet.get("api_id")
        if isinstance(fleet_id, bool) or not isinstance(fleet_id, int) or fleet_id < 1 or fleet_id in indexed_fleets:
            raise FullSupplyPreflightError("fleet IDs are invalid or duplicated")
        indexed_fleets[fleet_id] = fleet
    if set(indexed_fleets) != set(expected_fleet_ids):
        raise FullSupplyPreflightError("unlocked fleet set differs from the verified configuration")

    seen_ship_ids: set[int] = set()
    costs: list[FleetSupplyCost] = []
    for fleet_id in expected_fleet_ids:
        fleet = indexed_fleets[fleet_id]
        mission = fleet.get("api_mission")
        if not isinstance(mission, list) or len(mission) != 4 or any(
            isinstance(value, bool) or not isinstance(value, int) for value in mission
        ):
            raise FullSupplyPreflightError(f"fleet {fleet_id} mission state is incomplete")
        if mission != [0, 0, 0, 0]:
            raise FullSupplyPreflightError(f"fleet {fleet_id} is not idle; full-fleet resupply blocked")
        slots = fleet.get("api_ship")
        if not isinstance(slots, list) or len(slots) > 6:
            raise FullSupplyPreflightError(f"fleet {fleet_id} ship roster is invalid")
        fuel_required = ammo_required = ship_count = 0
        for ship_id in slots:
            if isinstance(ship_id, bool) or not isinstance(ship_id, int):
                raise FullSupplyPreflightError(f"fleet {fleet_id} has an invalid ship slot")
            if ship_id == -1:
                continue
            if ship_id < 1 or ship_id in seen_ship_ids:
                raise FullSupplyPreflightError("ship IDs are invalid or assigned more than once")
            seen_ship_ids.add(ship_id)
            ship = ships.get(str(ship_id))
            if not isinstance(ship, dict) or ship.get("api_id") != ship_id:
                raise FullSupplyPreflightError(f"ship {ship_id} is missing from the live snapshot")
            master_id = ship.get("api_ship_id")
            if isinstance(master_id, bool) or not isinstance(master_id, int) or master_id < 1:
                raise FullSupplyPreflightError(f"ship {ship_id} has no valid master ID")
            master = master_ships.get(str(master_id))
            if not isinstance(master, dict):
                raise FullSupplyPreflightError(f"ship {ship_id} master data is missing")
            fuel = _nonnegative_int(ship.get("api_fuel"), f"ship {ship_id} fuel")
            ammo = _nonnegative_int(ship.get("api_bull"), f"ship {ship_id} ammo")
            max_fuel = _nonnegative_int(master.get("api_fuel_max"), f"ship {ship_id} max fuel")
            max_ammo = _nonnegative_int(master.get("api_bull_max"), f"ship {ship_id} max ammo")
            if max_fuel < 1 or max_ammo < 1 or fuel > max_fuel or ammo > max_ammo:
                raise FullSupplyPreflightError(f"ship {ship_id} supply values are out of range")
            fuel_required += max_fuel - fuel
            ammo_required += max_ammo - ammo
            ship_count += 1
        if ship_count == 0:
            raise FullSupplyPreflightError(f"fleet {fleet_id} is empty")
        costs.append(FleetSupplyCost(fleet_id, ship_count, fuel_required, ammo_required))

    if not seen_ship_ids:
        raise FullSupplyPreflightError("no ships were found in unlocked fleets")
    total_fuel = sum(item.fuel_required for item in costs)
    total_ammo = sum(item.ammo_required for item in costs)
    if fuel_available < total_fuel or ammo_available < total_ammo:
        raise FullSupplyPreflightError("resource inventory is insufficient for full-fleet resupply")
    return FullSupplyPlan(
        fleet_ids=expected_fleet_ids,
        ship_ids=tuple(sorted(seen_ship_ids)),
        fleet_costs=tuple(costs),
        fuel_required=total_fuel,
        ammo_required=total_ammo,
        fuel_available=fuel_available,
        ammo_available=ammo_available,
    )


def verify_fleet_supply_complete(
    fleet_id: int,
    fleets: Any,
    ships: Any,
    master_ships: Any,
) -> bool:
    """Verify one selected fleet is fully supplied without assuming other fleets."""
    if isinstance(fleet_id, bool) or not isinstance(fleet_id, int) or fleet_id < 1:
        return False
    if not isinstance(fleets, list) or not isinstance(ships, dict) or not isinstance(master_ships, dict):
        return False
    matches = [
        fleet for fleet in fleets
        if isinstance(fleet, dict) and fleet.get("api_id") == fleet_id
    ]
    if len(matches) != 1:
        return False
    fleet = matches[0]
    if fleet.get("api_mission") != [0, 0, 0, 0]:
        return False
    roster = fleet.get("api_ship")
    if not isinstance(roster, list):
        return False
    ship_ids = [ship_id for ship_id in roster if isinstance(ship_id, int) and not isinstance(ship_id, bool) and ship_id > 0]
    if not ship_ids or len(ship_ids) != len(set(ship_ids)):
        return False
    for ship_id in ship_ids:
        ship = ships.get(str(ship_id))
        if not isinstance(ship, dict):
            return False
        master_id = ship.get("api_ship_id")
        master = master_ships.get(str(master_id)) if isinstance(master_id, int) and not isinstance(master_id, bool) else None
        if not isinstance(master, dict):
            return False
        if ship.get("api_fuel") != master.get("api_fuel_max") or ship.get("api_bull") != master.get("api_bull_max"):
            return False
    return True


def verify_full_supply_complete(
    plan: FullSupplyPlan,
    fleets: Any,
    ships: Any,
    master_ships: Any,
) -> bool:
    """Return true only when every ship in the preflight plan is fully supplied."""
    if not isinstance(plan, FullSupplyPlan) or not isinstance(fleets, list):
        return False
    if not isinstance(ships, dict) or not isinstance(master_ships, dict):
        return False
    indexed = {
        fleet.get("api_id"): fleet
        for fleet in fleets
        if isinstance(fleet, dict) and isinstance(fleet.get("api_id"), int)
    }
    if set(indexed) != set(plan.fleet_ids):
        return False
    current_ids: set[int] = set()
    for fleet_id in plan.fleet_ids:
        fleet = indexed[fleet_id]
        if fleet.get("api_mission") != [0, 0, 0, 0]:
            return False
        slots = fleet.get("api_ship")
        if not isinstance(slots, list):
            return False
        current_ids.update(ship_id for ship_id in slots if isinstance(ship_id, int) and not isinstance(ship_id, bool) and ship_id > 0)
    if current_ids != set(plan.ship_ids):
        return False
    for ship_id in plan.ship_ids:
        ship = ships.get(str(ship_id))
        if not isinstance(ship, dict):
            return False
        master_id = ship.get("api_ship_id")
        master = master_ships.get(str(master_id)) if isinstance(master_id, int) and not isinstance(master_id, bool) else None
        if not isinstance(master, dict):
            return False
        if ship.get("api_fuel") != master.get("api_fuel_max") or ship.get("api_bull") != master.get("api_bull_max"):
            return False
    return True
