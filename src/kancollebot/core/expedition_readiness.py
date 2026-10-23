"""Fail-closed, read-only expedition readiness assessment; never dispatches."""

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from kancollebot.core.expedition_equipment_planner import (
    EquipmentInstance,
    EquipmentSlotRequirement,
    plan_expedition_equipment,
)
from kancollebot.core.expedition_fleet_builder import CandidateShip, build_expedition_fleet
from kancollebot.core.expedition_rule_validator import validate_expedition_rules

_TOKEN = re.compile(r"^([1-6])(NA|DD|CL|CLT|CA|CAV|BB|BBV|CV|CVL|SS|SSV|AV|AO|AS|AR|CT|DE|LHA|CVB)$")


@dataclass(frozen=True, slots=True)
class ReadinessGate:
    name: str
    passed: bool | None
    detail: str


@dataclass(frozen=True, slots=True)
class ExpeditionReadinessResult:
    expedition_id: int
    candidate_ship_ids: tuple[int, ...]
    equipment_assignments: tuple
    gates: tuple[ReadinessGate, ...]
    ready_for_review: bool
    dispatch_authorized: bool = False


def _parse_composition(raw: Any) -> dict[str, int] | None:
    if not isinstance(raw, str) or not raw:
        return None
    result: dict[str, int] = {}
    for token in raw.split(","):
        match = _TOKEN.fullmatch(token)
        if match is None:
            return None
        kind = match.group(2)
        result[kind] = result.get(kind, 0) + int(match.group(1))
    if sum(result.values()) > 6:
        return None
    return result


def assess_expedition_readiness(
    rule_document: Any,
    expedition_id: int,
    ships: Sequence[CandidateShip],
    equipment_inventory: Sequence[EquipmentInstance],
    equipment_requirements: Sequence[EquipmentSlotRequirement] = (),
    *,
    reserved_ship_ids: Sequence[int] = (),
    protected_ship_ids: Sequence[int] = (),
    live_state_fresh: bool = False,
    morale_verified: bool = False,
    special_requirements_verified: bool = False,
    drum_model_id: int | None = None,
) -> ExpeditionReadinessResult:
    """Assess known gates. Unknown facts block readiness; dispatch is always false."""
    gates: list[ReadinessGate] = []
    empty = ExpeditionReadinessResult(
        expedition_id if type(expedition_id) is int and expedition_id > 0 else 0,
        (), (), (), False, False,
    )
    validation = validate_expedition_rules(rule_document)
    gates.append(ReadinessGate(
        "rule_database", validation.valid,
        "Rule database structure valid." if validation.valid else "; ".join(validation.errors),
    ))
    if not validation.valid or type(expedition_id) is not int or expedition_id <= 0:
        gates.append(ReadinessGate("mission_rule", False, "Rule database invalid or expedition ID invalid."))
        return ExpeditionReadinessResult(empty.expedition_id, (), (), tuple(gates), False, False)

    records = rule_document["expeditions"]
    matches = [item for item in records if item.get("id") == expedition_id]
    if len(matches) != 1:
        gates.append(ReadinessGate("mission_rule", False, "Expedition rule missing or ambiguous."))
        return ExpeditionReadinessResult(expedition_id, (), (), tuple(gates), False, False)
    record = matches[0]
    req = record["requirements"]
    composition = _parse_composition(req.get("composition_raw"))
    gates.append(ReadinessGate(
        "composition_rule", composition is not None,
        "Composition parsed." if composition is not None else "Unsupported composition syntax.",
    ))
    if composition is None:
        return ExpeditionReadinessResult(expedition_id, (), (), tuple(gates), False, False)

    fleet = build_expedition_fleet(
        ships, composition,
        min_flagship_level=max(1, req["flagship_level_min"]),
        min_total_level=max(1, req["total_level_min"]),
        max_ships=6,
        reserved_ship_ids=reserved_ship_ids,
    )
    gates.append(ReadinessGate("fleet_candidate", fleet.eligible, fleet.reason))
    if not fleet.eligible:
        return ExpeditionReadinessResult(expedition_id, (), (), tuple(gates), False, False)

    planned_ids = set(fleet.ship_ids)
    invalid_targets = [item for item in equipment_requirements if item.ship_id not in planned_ids]
    if invalid_targets:
        gates.append(ReadinessGate(
            "equipment_targets", False,
            "At least one equipment requirement targets a ship outside the planned fleet.",
        ))
        equipment_ok = False
        assignments = ()
    else:
        equipment = plan_expedition_equipment(
            equipment_requirements, equipment_inventory,
            target_ship_ids=fleet.ship_ids,
            protected_ship_ids=protected_ship_ids,
        )
        equipment_ok = equipment.eligible
        assignments = equipment.assignments if equipment.eligible else ()
        gates.append(ReadinessGate("equipment_allocation", equipment_ok, equipment.reason))

    drum_count = req.get("drum_count_min")
    drum_carriers = req.get("drum_carriers_min")
    drum_slots = [
        item for item in equipment_requirements
        if drum_model_id is not None and item.model_id == drum_model_id
    ]
    drum_ship_ids = {item.ship_id for item in drum_slots}
    drum_input_valid = type(drum_model_id) is int and drum_model_id > 0
    if type(drum_count) is int and drum_count == 0 and type(drum_carriers) is int and drum_carriers == 0:
        drums_known = True
        drum_detail = "No drums required by imported rule."
    elif (
        drum_input_valid
        and type(drum_count) is int and drum_count >= 0
        and type(drum_carriers) is int and drum_carriers >= 0
        and len(drum_slots) >= drum_count
        and len(drum_ship_ids) >= drum_carriers
        and all(item.ship_id in planned_ids for item in drum_slots)
    ):
        # Equipment count alone cannot prove a ship is a valid drum carrier.
        drums_known = False
        drum_detail = (
            f"Found {len(drum_slots)} declared drum slots across {len(drum_ship_ids)} ships, "
            "but game-specific carrier compatibility is not verified."
        )
    else:
        drums_known = False
        drum_detail = (
            "Drum model ID or explicit drum slot requirements are missing or insufficient."
            if drum_input_valid else
            "Drum model ID is unknown; drum count and carrier requirements cannot be verified."
        )
    gates.append(ReadinessGate("drum_requirements", drums_known, drum_detail))

    additional_known = (
        special_requirements_verified
        and req.get("additional_stats") != "UNKNOWN"
        and req.get("additional_equipment") != "UNKNOWN"
    )
    gates.append(ReadinessGate(
        "additional_requirements", additional_known,
        "Additional requirements verified." if additional_known else
        "Additional stat/equipment conditions are unknown or not verified.",
    ))
    source_verified = record.get("verification") == "VERIFIED"
    gates.append(ReadinessGate(
        "source_verification", source_verified,
        "Reference independently verified." if source_verified else
        "Imported reference record is not independently verified.",
    ))
    gates.append(ReadinessGate(
        "live_state_freshness", live_state_fresh,
        "Live state freshness confirmed." if live_state_fresh else "Fresh live game state not verified.",
    ))
    gates.append(ReadinessGate(
        "morale_and_great_success", morale_verified,
        "Morale conditions verified." if morale_verified else
        "Morale and great-success conditions not verified.",
    ))
    ready = all(gate.passed is True for gate in gates)
    return ExpeditionReadinessResult(
        expedition_id, fleet.ship_ids, assignments, tuple(gates), ready, False,
    )
