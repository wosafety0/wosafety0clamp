"""Pure planner that completes a fleet's mission composition from the dock.

Nothing here touches Poi, Redux state, or the game UI: the planner only returns
a proposal. Applying it (編成 界面 加船/撤船/换旗舰) is a separate, gated step.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from .mission_eligibility import (
    FleetCompositionSnapshot,
    MissionCompositionRequirement,
    MissionEligibilityDecision,
    evaluate_mission_composition,
)

MAX_FLEET_SIZE = 6


class DockInventoryError(ValueError):
    """Dock inventory does not meet the planner's contract."""


class DockCompositionDecision(str, Enum):
    NO_CHANGE_NEEDED = "no_change_needed"
    PLAN_AVAILABLE = "plan_available"
    BLOCKED_NO_ALLOWED_TYPE_CANDIDATE = "blocked_no_allowed_type_candidate"
    BLOCKED_INSUFFICIENT_DOCK_SHIPS = "blocked_insufficient_dock_ships"
    BLOCKED_NO_QUALIFYING_FLAGSHIP = "blocked_no_qualifying_flagship"
    BLOCKED_PLAN_FAILED_VERIFICATION = "blocked_plan_failed_verification"


class CompositionActionKind(str, Enum):
    REMOVE_SHIP = "remove_ship"
    ADD_SHIP = "add_ship"
    SET_FLAGSHIP = "set_flagship"


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


@dataclass(frozen=True, slots=True)
class CompositionAction:
    """One proposed 編成 change; ``slot`` is the current fleet index for removals."""

    kind: CompositionActionKind
    ship_id: int
    slot: int | None = None
    master_id: int | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.kind, CompositionActionKind):
            raise ValueError("kind must be a CompositionActionKind")
        if isinstance(self.ship_id, bool) or not isinstance(self.ship_id, int) or self.ship_id < 1:
            raise ValueError("ship_id must be a positive integer")
        if self.slot is not None and (
            isinstance(self.slot, bool) or not isinstance(self.slot, int) or self.slot < 0
        ):
            raise ValueError("slot must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class DockCompositionPlan:
    mission_id: int
    decision: DockCompositionDecision
    actions: tuple[CompositionAction, ...]
    resulting_composition: FleetCompositionSnapshot | None
    reason: str

    @property
    def actionable(self) -> bool:
        return self.decision is DockCompositionDecision.PLAN_AVAILABLE


def _validate_dock(dock: tuple[DockShip, ...], fleet: FleetCompositionSnapshot) -> None:
    for index, ship in enumerate(dock):
        if not isinstance(ship, DockShip):
            raise DockInventoryError(f"dock entry {index} is not a DockShip")
    ids = [ship.ship_id for ship in dock]
    if len(set(ids)) != len(ids):
        raise DockInventoryError("dock ship IDs must be unique")
    overlap = sorted(set(ids) & set(fleet.ship_ids))
    if overlap:
        raise DockInventoryError(
            f"dock ships are already assigned to a fleet: {overlap}"
        )


def plan_dock_composition(
    fleet: FleetCompositionSnapshot,
    requirement: MissionCompositionRequirement,
    dock: Sequence[DockShip],
) -> DockCompositionPlan:
    """Propose the smallest 編成 change that satisfies ``requirement``.

    Selection policy: the flagship slot goes to the highest-level qualifying
    ship (the current flagship if it still qualifies), while filler ships are
    the lowest-level allowed candidates so expensive ships stay in reserve.
    Every result is re-verified by ``evaluate_mission_composition``; a plan that
    would not verify is refused instead of returned. Removals are limited to
    ships whose type the mission forbids, plus (only when the fleet is full and
    a dock flagship must be added) the lowest-level remaining ship.
    """
    if not isinstance(fleet, FleetCompositionSnapshot):
        raise ValueError("fleet must be a FleetCompositionSnapshot")
    if not isinstance(requirement, MissionCompositionRequirement):
        raise ValueError("requirement must be a MissionCompositionRequirement")
    dock_ships = tuple(dock)
    _validate_dock(dock_ships, fleet)

    def result(
        decision: DockCompositionDecision,
        actions: Sequence[CompositionAction],
        composition: FleetCompositionSnapshot | None,
        reason: str,
    ) -> DockCompositionPlan:
        return DockCompositionPlan(
            requirement.mission_id, decision, tuple(actions), composition, reason
        )

    current = evaluate_mission_composition(fleet, requirement)
    if current.decision is MissionEligibilityDecision.ELIGIBLE:
        return result(
            DockCompositionDecision.NO_CHANGE_NEEDED, (), fleet, current.reason
        )

    allowed = (
        set(requirement.allowed_ship_types)
        if requirement.allowed_ship_types is not None
        else None
    )

    def type_allowed(ship_type: int) -> bool:
        return allowed is None or ship_type in allowed

    # Ships the mission's type rule forbids leave unconditionally.
    kept_ids: list[int] = []
    kept_levels: list[int] = []
    kept_types: list[int] = []
    removals: list[CompositionAction] = []
    for index, ship_id in enumerate(fleet.ship_ids):
        ship_type = fleet.ship_types[index]
        if type_allowed(ship_type):
            kept_ids.append(ship_id)
            kept_levels.append(fleet.ship_levels[index])
            kept_types.append(ship_type)
        else:
            removals.append(
                CompositionAction(
                    CompositionActionKind.REMOVE_SHIP,
                    ship_id,
                    slot=index,
                    reason=(
                        f"Ship type {ship_type} is not allowed by mission "
                        f"{requirement.mission_id}."
                    ),
                )
            )

    levels = dict(zip(fleet.ship_ids, fleet.ship_levels))
    types = dict(zip(fleet.ship_ids, fleet.ship_types))
    kept_count = len(kept_ids)

    added_ids: list[int] = []
    additions: list[CompositionAction] = []
    flagship_requirement = requirement.minimum_flagship_level

    current_flagship = fleet.ship_ids[0] if fleet.ship_ids else None
    flagship_id: int | None = None
    if (
        current_flagship is not None
        and current_flagship in kept_ids
        and levels[current_flagship] >= flagship_requirement
    ):
        flagship_id = current_flagship
    else:
        qualified_kept = [
            (level, ship_id)
            for ship_id, level in zip(kept_ids, kept_levels)
            if level >= flagship_requirement
        ]
        if qualified_kept:
            flagship_id = max(qualified_kept, key=lambda item: (item[0], -item[1]))[1]

    if flagship_id is None:
        qualified_dock = [
            ship
            for ship in dock_ships
            if type_allowed(ship.ship_type) and ship.level >= flagship_requirement
        ]
        if not qualified_dock:
            return result(
                DockCompositionDecision.BLOCKED_NO_QUALIFYING_FLAGSHIP,
                removals,
                None,
                (
                    f"No ship in the fleet or dock reaches level "
                    f"{flagship_requirement} for the flagship slot of mission "
                    f"{requirement.mission_id}."
                ),
            )
        chosen = max(qualified_dock, key=lambda ship: (ship.level, -ship.ship_id))
        flagship_id = chosen.ship_id
        added_ids.append(chosen.ship_id)
        levels[chosen.ship_id] = chosen.level
        types[chosen.ship_id] = chosen.ship_type
        additions.append(
            CompositionAction(
                CompositionActionKind.ADD_SHIP,
                chosen.ship_id,
                slot=0,
                master_id=chosen.master_id,
                reason=(
                    f"Dock ship {chosen.ship_id} (level {chosen.level}) satisfies the "
                    "flagship level requirement."
                ),
            )
        )

    # Only reachable when a dock flagship was required. Keeping the fleet at its
    # current size avoids paying expedition consumption for an extra hull, so a
    # swap removes the lowest-level remaining ship whenever the count allows it.
    desired_total = min(MAX_FLEET_SIZE, max(requirement.minimum_ships, kept_count))
    while len(kept_ids) + len(added_ids) > desired_total and kept_ids:
        victim_index = min(
            range(len(kept_ids)), key=lambda index: (kept_levels[index], kept_ids[index])
        )
        victim_id = kept_ids.pop(victim_index)
        kept_levels.pop(victim_index)
        kept_types.pop(victim_index)
        removals.append(
            CompositionAction(
                CompositionActionKind.REMOVE_SHIP,
                victim_id,
                slot=fleet.ship_ids.index(victim_id),
                reason=(
                    "A dock ship must join to satisfy the flagship level; removing "
                    "the lowest-level remaining ship to keep the fleet size."
                ),
            )
        )

    used_ids = set(kept_ids) | set(added_ids)
    need = max(0, requirement.minimum_ships - len(used_ids))
    if need:
        pool = [
            ship
            for ship in dock_ships
            if type_allowed(ship.ship_type) and ship.ship_id not in used_ids
        ]
        if len(pool) < need:
            if allowed is not None and not pool:
                return result(
                    DockCompositionDecision.BLOCKED_NO_ALLOWED_TYPE_CANDIDATE,
                    removals,
                    None,
                    (
                        f"No dock ship has a ship type allowed by mission "
                        f"{requirement.mission_id} "
                        f"({sorted(allowed)}); {need} more ship(s) are required."
                    ),
                )
            return result(
                DockCompositionDecision.BLOCKED_INSUFFICIENT_DOCK_SHIPS,
                removals,
                None,
                (
                    f"Mission {requirement.mission_id} needs {requirement.minimum_ships} "
                    f"ships; only {len(used_ids) + len(pool)} can be formed from the "
                    "current fleet and dock."
                ),
            )
        for chosen in sorted(pool, key=lambda ship: (ship.level, ship.ship_id))[:need]:
            added_ids.append(chosen.ship_id)
            levels[chosen.ship_id] = chosen.level
            types[chosen.ship_id] = chosen.ship_type
            additions.append(
                CompositionAction(
                    CompositionActionKind.ADD_SHIP,
                    chosen.ship_id,
                    master_id=chosen.master_id,
                    reason=(
                        f"Adds dock ship {chosen.ship_id} to reach "
                        f"{requirement.minimum_ships} ships."
                    ),
                )
            )

    order = [flagship_id] + [ship_id for ship_id in kept_ids if ship_id != flagship_id]
    order += [ship_id for ship_id in added_ids if ship_id != flagship_id]
    planned = FleetCompositionSnapshot(
        tuple(order),
        tuple(levels[ship_id] for ship_id in order),
        tuple(types[ship_id] for ship_id in order),
    )

    verification = evaluate_mission_composition(planned, requirement)
    if verification.decision is not MissionEligibilityDecision.ELIGIBLE:
        return result(
            DockCompositionDecision.BLOCKED_PLAN_FAILED_VERIFICATION,
            (),
            None,
            f"Proposed composition does not verify: {verification.reason}",
        )

    actions = list(removals)
    actions.extend(additions)
    if (
        current_flagship in kept_ids
        and current_flagship != flagship_id
        and flagship_id not in added_ids
    ):
        actions.append(
            CompositionAction(
                CompositionActionKind.SET_FLAGSHIP,
                flagship_id,
                slot=0,
                reason=(
                    f"Move ship {flagship_id} to the flagship slot to satisfy level "
                    f"{flagship_requirement}."
                ),
            )
        )
    return result(
        DockCompositionDecision.PLAN_AVAILABLE,
        actions,
        planned,
        (
            f"{len(actions)} 編成 change(s) make mission {requirement.mission_id} "
            f"composition eligible."
        ),
    )


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
