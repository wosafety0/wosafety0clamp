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


@dataclass(frozen=True, slots=True)
class FleetSupplyPlan:
    """The deficit of exactly one fleet, which is what a single-fleet action needs.

    ``build_full_supply_plan`` requires *every* unlocked fleet to be idle because
    the UI button it guards supplies them all at once. The continuous loop cannot
    satisfy that: at any moment another fleet is out on an expedition, so a
    single-fleet plan must be able to ignore the others - and must therefore say
    so explicitly rather than quietly relaxing the all-idle rule.
    """

    fleet_id: int
    ship_ids: tuple[int, ...]
    fuel_required: int
    ammo_required: int
    fuel_available: int
    ammo_available: int

    @property
    def ship_count(self) -> int:
        return len(self.ship_ids)

    @property
    def needs_supply(self) -> bool:
        return self.fuel_required > 0 or self.ammo_required > 0


def build_fleet_supply_plan(
    fleet_id: int,
    fleets: Any,
    ships: Any,
    master_ships: Any,
    resources: Any,
    *,
    source_freshness_verified: bool,
    observed_at: datetime,
    now: datetime,
    max_age: timedelta = timedelta(seconds=30),
) -> FleetSupplyPlan:
    """Calculate one fleet's fuel/ammo deficit from a recent verified observation.

    Other fleets may be anywhere - on an expedition, empty, or absent - because
    this plan only ever drives one fleet's supply. The fleet being supplied must
    still be idle: supplying a fleet that is under way is meaningless and would
    mean the observation disagreed with the scheduler.
    """
    if isinstance(fleet_id, bool) or not isinstance(fleet_id, int) or fleet_id < 1:
        raise ValueError("fleet_id must be a positive integer")
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
    if not isinstance(fleets, list) or not isinstance(ships, dict) or not isinstance(master_ships, dict):
        raise FullSupplyPreflightError("fleet, ship, or master data has an invalid shape")
    if not isinstance(resources, list) or len(resources) < 2:
        raise FullSupplyPreflightError("resource inventory is incomplete")
    fuel_available = _nonnegative_int(resources[0], "fuel inventory")
    ammo_available = _nonnegative_int(resources[1], "ammo inventory")

    matches = [
        fleet for fleet in fleets
        if isinstance(fleet, dict) and fleet.get("api_id") == fleet_id
    ]
    if len(matches) != 1:
        raise FullSupplyPreflightError(f"fleet {fleet_id} is missing or duplicated")
    fleet = matches[0]
    mission = fleet.get("api_mission")
    if not isinstance(mission, list) or len(mission) != 4 or any(
        isinstance(value, bool) or not isinstance(value, int) for value in mission
    ):
        raise FullSupplyPreflightError(f"fleet {fleet_id} mission state is incomplete")
    if mission != [0, 0, 0, 0]:
        raise FullSupplyPreflightError(f"fleet {fleet_id} is not idle; single-fleet resupply blocked")
    slots = fleet.get("api_ship")
    if not isinstance(slots, list) or len(slots) > 6:
        raise FullSupplyPreflightError(f"fleet {fleet_id} ship roster is invalid")

    fuel_required = ammo_required = 0
    ship_ids: list[int] = []
    for ship_id in slots:
        if isinstance(ship_id, bool) or not isinstance(ship_id, int):
            raise FullSupplyPreflightError(f"fleet {fleet_id} has an invalid ship slot")
        if ship_id == -1:
            continue
        if ship_id < 1 or ship_id in ship_ids:
            raise FullSupplyPreflightError("ship IDs are invalid or assigned more than once")
        ship_ids.append(ship_id)
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
    if not ship_ids:
        raise FullSupplyPreflightError(f"fleet {fleet_id} is empty")
    if fuel_available < fuel_required or ammo_available < ammo_required:
        raise FullSupplyPreflightError("resource inventory is insufficient for this fleet's resupply")
    return FleetSupplyPlan(
        fleet_id=fleet_id,
        ship_ids=tuple(ship_ids),
        fuel_required=fuel_required,
        ammo_required=ammo_required,
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


def select_full_supply_targets(
    plan: FullSupplyPlan,
    *,
    supported_fleet_ids: tuple[int, ...] = (1, 2, 3),
    target_fleet_ids: tuple[int, ...] | None = None,
) -> tuple[FleetSupplyCost, ...]:
    """Select deficits only from explicit targets; reject unsupported targets."""
    if not isinstance(plan, FullSupplyPlan):
        raise TypeError("plan must be a FullSupplyPlan")
    if not isinstance(supported_fleet_ids, tuple) or not supported_fleet_ids:
        raise ValueError("supported_fleet_ids must be a non-empty tuple")
    if any(isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in supported_fleet_ids):
        raise ValueError("supported fleet IDs must be positive integers")
    if len(set(supported_fleet_ids)) != len(supported_fleet_ids):
        raise ValueError("supported fleet IDs must be unique")
    selected_ids = plan.fleet_ids if target_fleet_ids is None else target_fleet_ids
    if not isinstance(selected_ids, tuple) or not selected_ids:
        raise ValueError("target_fleet_ids must be a non-empty tuple")
    if any(isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in selected_ids):
        raise ValueError("target fleet IDs must be positive integers")
    if len(set(selected_ids)) != len(selected_ids):
        raise ValueError("target fleet IDs must be unique")
    if not set(selected_ids).issubset(plan.fleet_ids):
        raise FullSupplyPreflightError("target fleet IDs are outside the verified fleet set")
    targets = tuple(
        item for item in plan.fleet_costs
        if item.fleet_id in selected_ids
        and (item.fuel_required > 0 or item.ammo_required > 0)
    )
    unsupported = tuple(item.fleet_id for item in targets if item.fleet_id not in supported_fleet_ids)
    if unsupported:
        raise FullSupplyPreflightError(
            "fleet(s) require supply but lack independently verified UI selection gates: "
            + ", ".join(str(item) for item in unsupported)
        )
    return targets


def validate_full_supply_progress(
    initial_plan: FullSupplyPlan,
    current_plan: FullSupplyPlan,
    completed_fleet_ids: tuple[int, ...] = (),
    *,
    target_fleet_ids: tuple[int, ...] | None = None,
) -> tuple[FleetSupplyCost, ...]:
    """Verify selected target progress and reject changes to every unprocessed fleet."""
    if not isinstance(initial_plan, FullSupplyPlan) or not isinstance(current_plan, FullSupplyPlan):
        raise TypeError("initial_plan and current_plan must be FullSupplyPlan instances")
    if current_plan.fleet_ids != initial_plan.fleet_ids or current_plan.ship_ids != initial_plan.ship_ids:
        raise FullSupplyPreflightError("fleet or ship roster changed during the full-supply batch")
    targets = select_full_supply_targets(initial_plan, target_fleet_ids=target_fleet_ids)
    target_ids = tuple(item.fleet_id for item in targets)
    if not isinstance(completed_fleet_ids, tuple):
        raise ValueError("completed_fleet_ids must be a tuple")
    if len(set(completed_fleet_ids)) != len(completed_fleet_ids):
        raise ValueError("completed fleet IDs must be unique")
    if completed_fleet_ids != target_ids[:len(completed_fleet_ids)]:
        raise FullSupplyPreflightError("completed fleets do not match the configured supply order")
    initial_costs = {item.fleet_id: item for item in initial_plan.fleet_costs}
    current_costs = {item.fleet_id: item for item in current_plan.fleet_costs}
    consumed_fuel = consumed_ammo = 0
    for fleet_id in initial_plan.fleet_ids:
        before = initial_costs[fleet_id]
        after = current_costs.get(fleet_id)
        if after is None:
            raise FullSupplyPreflightError("current supply plan omitted a fleet")
        if fleet_id in completed_fleet_ids:
            if after.fuel_required != 0 or after.ammo_required != 0:
                raise FullSupplyPreflightError(f"completed fleet {fleet_id} is not fully supplied")
            consumed_fuel += before.fuel_required
            consumed_ammo += before.ammo_required
        elif after != before:
            raise FullSupplyPreflightError(f"unprocessed fleet {fleet_id} supply state changed unexpectedly")
    if (
        current_plan.fuel_available != initial_plan.fuel_available - consumed_fuel
        or current_plan.ammo_available != initial_plan.ammo_available - consumed_ammo
    ):
        raise FullSupplyPreflightError("material inventory delta does not match completed fleet-wide actions")
    if (
        current_plan.fuel_required != sum(item.fuel_required for item in current_plan.fleet_costs)
        or current_plan.ammo_required != sum(item.ammo_required for item in current_plan.fleet_costs)
    ):
        raise FullSupplyPreflightError("current supply plan totals are inconsistent")
    return tuple(current_costs[fleet_id] for fleet_id in target_ids[len(completed_fleet_ids):])
