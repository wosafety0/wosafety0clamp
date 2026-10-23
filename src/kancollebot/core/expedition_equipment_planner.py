"""Pure expedition equipment allocation planner; never moves equipment or calls game APIs."""

from dataclasses import dataclass
from typing import Literal, Sequence

StarPreference = Literal["closest", "highest", "any"]


@dataclass(frozen=True, slots=True)
class EquipmentInstance:
    equipment_id: int
    model_id: int
    stars: int
    source_ship_id: int | None = None
    source_slot: int | None = None
    reinforcement_eligible_ship_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class EquipmentSlotRequirement:
    ship_id: int
    slot: int
    model_id: int
    preferred_stars: int = 0
    star_preference: StarPreference = "closest"
    reinforcement_slot: bool = False


@dataclass(frozen=True, slots=True)
class EquipmentAssignment:
    ship_id: int
    slot: int
    equipment_id: int
    model_id: int
    stars: int
    source_ship_id: int | None
    source_slot: int | None


@dataclass(frozen=True, slots=True)
class EquipmentPlanResult:
    eligible: bool
    assignments: tuple[EquipmentAssignment, ...]
    reason: str


def plan_expedition_equipment(
    requirements: Sequence[EquipmentSlotRequirement],
    inventory: Sequence[EquipmentInstance],
    *,
    target_ship_ids: Sequence[int],
    protected_ship_ids: Sequence[int] = (),
    max_requirements: int = 24,
) -> EquipmentPlanResult:
    """Plan exact equipment-instance allocation, failing closed on ambiguous input.

    Equipment held by ships outside the target fleet is protected and never
    borrowed. This function only returns a plan; it performs no equipment swaps.
    """
    fail = EquipmentPlanResult(False, (), "Invalid or incomplete equipment state.")
    if (not isinstance(max_requirements, int) or isinstance(max_requirements, bool)
            or not 1 <= max_requirements <= 36
            or not isinstance(requirements, Sequence)
            or isinstance(requirements, (str, bytes, bytearray))
            or not isinstance(inventory, Sequence)
            or isinstance(inventory, (str, bytes, bytearray))
            or len(requirements) > max_requirements
            or any(not isinstance(x, EquipmentSlotRequirement) for x in requirements)
            or any(not isinstance(x, EquipmentInstance) for x in inventory)):
        return fail
    targets = tuple(target_ship_ids)
    protected = set(protected_ship_ids)
    if (not targets or len(targets) != len(set(targets))
            or any(type(x) is not int or x <= 0 for x in targets)
            or any(type(x) is not int or x <= 0 for x in protected)):
        return fail
    if protected.intersection(targets):
        return fail
    target_set = set(targets)
    if len({x.equipment_id for x in inventory}) != len(inventory):
        return fail
    if any(type(x.equipment_id) is not int or x.equipment_id <= 0
           or type(x.model_id) is not int or x.model_id <= 0
           or type(x.stars) is not int or not 0 <= x.stars <= 10
           or (x.source_ship_id is None) != (x.source_slot is None)
           or (x.source_ship_id is not None and
               (type(x.source_ship_id) is not int or x.source_ship_id <= 0
                or type(x.source_slot) is not int or not 0 <= x.source_slot <= 5))
           or any(type(i) is not int or i <= 0 for i in x.reinforcement_eligible_ship_ids)
           for x in inventory):
        return fail
    slot_keys = [(x.ship_id, x.slot) for x in requirements]
    if len(slot_keys) != len(set(slot_keys)):
        return fail
    if any(type(x.ship_id) is not int or x.ship_id not in target_set
           or type(x.slot) is not int or not 0 <= x.slot <= 5
           or type(x.model_id) is not int or x.model_id <= 0
           or type(x.preferred_stars) is not int or not 0 <= x.preferred_stars <= 10
           or x.star_preference not in ("closest", "highest", "any")
           or type(x.reinforcement_slot) is not bool
           or (x.reinforcement_slot and x.slot != 5)
           for x in requirements):
        return fail

    movable = [item for item in inventory
               if item.source_ship_id is None or item.source_ship_id in target_set]
    def priority(req, item):
        if req.star_preference == "highest":
            star_cost = -item.stars
        elif req.star_preference == "any":
            star_cost = 0
        else:
            star_cost = abs(item.stars - req.preferred_stars)
        same_slot = int((item.source_ship_id, item.source_slot) != (req.ship_id, req.slot))
        return (star_cost, same_slot, item.equipment_id)

    candidates = {}
    for req in requirements:
        matches = [item for item in movable
                   if item.model_id == req.model_id
                   and (not req.reinforcement_slot
                        or req.ship_id in item.reinforcement_eligible_ship_ids)]
        candidates[(req.ship_id, req.slot)] = sorted(matches, key=lambda item: priority(req, item))
        if not matches:
            return EquipmentPlanResult(False, (), f"No eligible instance for ship {req.ship_id} slot {req.slot} model {req.model_id}.")

    ordered = sorted(requirements, key=lambda req: (len(candidates[(req.ship_id, req.slot)]), req.ship_id, req.slot))
    # Deterministic augmenting-path matching avoids exponential backtracking.
    item_to_key = {}

    def assign(key, visited):
        req = next(req for req in ordered if (req.ship_id, req.slot) == key)
        for item in candidates[key]:
            if item.equipment_id in visited:
                continue
            visited.add(item.equipment_id)
            previous_key = item_to_key.get(item.equipment_id)
            if previous_key is None or assign(previous_key, visited):
                item_to_key[item.equipment_id] = key
                return True
        return False

    for req in ordered:
        if not assign((req.ship_id, req.slot), set()):
            return EquipmentPlanResult(False, (), "Equipment instances cannot satisfy all requirements without reuse.")
    key_to_item = {key: next(item for item in inventory if item.equipment_id == equipment_id)
                   for equipment_id, key in item_to_key.items()}
    assignments = tuple(
        EquipmentAssignment(req.ship_id, req.slot, key_to_item[(req.ship_id, req.slot)].equipment_id,
                            req.model_id, key_to_item[(req.ship_id, req.slot)].stars,
                            key_to_item[(req.ship_id, req.slot)].source_ship_id,
                            key_to_item[(req.ship_id, req.slot)].source_slot)
        for req in sorted(requirements, key=lambda req: (req.ship_id, req.slot))
    )
    return EquipmentPlanResult(True, assignments, "Read-only allocation plan only; swap and readiness verification are not performed.")
