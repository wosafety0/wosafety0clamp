"""Pure, read-only parsing of Poi Redux requirement evidence.

R3-I originally had no trustworthy equipment or fleet-stat observation, so any
expedition that required drums or stat totals could only answer UNKNOWN. This
module turns three already-read Redux collections into that evidence:

* ``info.ships`` projected onto equipment/stat fields (a SEPARATE projection --
  adding these fields to the port-witness projection truncates the 239-ship
  roster and would silently destroy source freshness verification);
* ``info.equips`` filtered to the equipment instances those ships actually hold;
* ``api_cond`` / ``api_ndock_time`` from the same reads.

It performs no I/O, sends no game request, and proposes no fleet change. Every
value is strictly validated: one malformed field makes that FLEET's evidence
unavailable (the requirement evaluator then answers UNKNOWN for the affected
conditions), never a partial or invented result. Evidence is deliberately
attached to the fleet snapshot that carries the freshness verdict, so the
evaluator cannot combine a fresh composition with equipment read at another
time.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

# Poi stores a ship's displayed stat as [current_including_equipment, maximum].
# Expedition totals are sums of the displayed values (equipment included), which
# is what the wiki and the upstream rule files compare against.
STAT_KEY_BY_API_FIELD: Mapping[str, str] = MappingProxyType(
    {
        "api_karyoku": "firepower",
        "api_taiku": "anti_air",
        "api_taisen": "asw",
        "api_sakuteki": "los",
    }
)
STAT_KEYS = frozenset(STAT_KEY_BY_API_FIELD.values())

# A deliberately narrow projection. The port-witness projection in
# expedition_reader must stay untouched; see the module docstring.
SHIP_DETAIL_FIELDS: tuple[str, ...] = (
    "api_id",
    "api_slot",
    "api_slot_ex",
    "api_slotnum",
    "api_karyoku",
    "api_taiku",
    "api_taisen",
    "api_sakuteki",
    "api_ndock_time",
)
EQUIP_INSTANCE_FIELDS: tuple[str, ...] = ("api_id", "api_slotitem_id")

# Four fleets x six ships x (five slots + one ex slot) is the hard upper bound.
MAX_EQUIPMENT_INSTANCE_IDS = 144
_MAX_SAFE_INT = 9_007_199_254_740_991


class RequirementEvidenceError(ValueError):
    """A fleet's equipment/stat evidence is missing, malformed or ambiguous."""


@dataclass(frozen=True, slots=True)
class FleetRequirementEvidence:
    """Evidence for exactly one fleet's members, already resolved to master ids."""

    equipment_by_ship: Mapping[int, tuple[int, ...]]
    total_stats: Mapping[str, int]
    conditions: Mapping[int, int]
    in_dock_ship_ids: tuple[int, ...]

    def as_plain(self) -> dict[str, Any]:
        """JSON-safe shape for a live evidence report (never fed back as proof)."""
        return {
            "equipment_by_ship": {
                str(ship_id): list(equipment)
                for ship_id, equipment in sorted(self.equipment_by_ship.items())
            },
            "total_stats": dict(sorted(self.total_stats.items())),
            "conditions": {
                str(ship_id): value
                for ship_id, value in sorted(self.conditions.items())
            },
            "in_dock_ship_ids": list(self.in_dock_ship_ids),
        }


def _safe_int(value: Any, label: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or abs(value) > _MAX_SAFE_INT
    ):
        raise RequirementEvidenceError(f"{label} is not a safe integer")
    return value


def _positive_int(value: Any, label: str) -> int:
    result = _safe_int(value, label)
    if result < 1:
        raise RequirementEvidenceError(f"{label} must be positive")
    return result


def _displayed_stat(value: Any, label: str) -> int:
    """Return the CURRENT displayed stat, rejecting a malformed [cur, max] pair."""
    if not isinstance(value, list) or len(value) != 2:
        raise RequirementEvidenceError(f"{label} is not a [current, maximum] pair")
    current = _safe_int(value[0], f"{label} current")
    maximum = _safe_int(value[1], f"{label} maximum")
    if current < 0 or maximum < 0 or current > maximum:
        raise RequirementEvidenceError(f"{label} has an impossible current/maximum pair")
    return current


def _slot_instance_ids(detail: Mapping[str, Any], label: str) -> tuple[int, ...]:
    """Resolve one ship's ordinary + ex slots into equipment INSTANCE ids."""
    slots = detail.get("api_slot")
    if not isinstance(slots, list) or len(slots) > 5:
        raise RequirementEvidenceError(f"{label} api_slot is missing or invalid")
    slotnum = detail.get("api_slotnum")
    if slotnum is not None:
        _safe_int(slotnum, f"{label} api_slotnum")
    ids: list[int] = []
    for index, raw in enumerate(slots):
        value = _safe_int(raw, f"{label} api_slot[{index}]")
        # -1 is the documented empty-slot sentinel; 0 is also treated as empty
        # because some Poi builds normalise the ex slot that way. A positive id
        # must resolve in info.equips before this ship's evidence is accepted.
        if value in (-1, 0):
            continue
        if value < 0:
            raise RequirementEvidenceError(f"{label} api_slot[{index}] is negative")
        ids.append(value)
    ex = detail.get("api_slot_ex")
    if ex is not None:
        ex_value = _safe_int(ex, f"{label} api_slot_ex")
        if ex_value > 0:
            ids.append(ex_value)
        elif ex_value not in (-1, 0):
            raise RequirementEvidenceError(f"{label} api_slot_ex is negative")
    return tuple(ids)


def equipment_instance_ids_for_fleets(
    fleets: Sequence[Mapping[str, Any]],
    ship_details: Mapping[str, Mapping[str, Any]],
) -> tuple[int, ...]:
    """Collect the bounded set of equipment instance ids a `where` filter needs.

    Only fleet members are considered. Duplicate ids are removed and the result
    is sorted so the generated MCP query is deterministic.
    """
    ids: set[int] = set()
    for index, fleet in enumerate(fleets):
        raw_ship_ids = fleet.get("api_ship")
        if not isinstance(raw_ship_ids, list) or len(raw_ship_ids) > 6:
            raise RequirementEvidenceError(f"fleet {index} api_ship is invalid")
        for raw_ship_id in raw_ship_ids:
            ship_id = _safe_int(raw_ship_id, f"fleet {index} ship id")
            if ship_id in (-1, 0):
                continue
            if ship_id < 0:
                raise RequirementEvidenceError(f"fleet {index} has a negative ship id")
            detail = ship_details.get(str(ship_id))
            if not isinstance(detail, dict):
                raise RequirementEvidenceError(f"ship {ship_id} has no detail record")
            ids.update(_slot_instance_ids(detail, f"ship {ship_id}"))
    if len(ids) > MAX_EQUIPMENT_INSTANCE_IDS:
        raise RequirementEvidenceError("equipment instance id set exceeds the safety bound")
    return tuple(sorted(ids))


def build_equipment_where_clause(instance_ids: Sequence[int]) -> str:
    """Build the MCP `where` expression from ALREADY VALIDATED integer ids."""
    if not instance_ids:
        return ""
    if len(instance_ids) > MAX_EQUIPMENT_INSTANCE_IDS:
        raise RequirementEvidenceError("equipment instance id set exceeds the safety bound")
    return " or ".join(
        f"api_id = {_positive_int(instance_id, 'equipment instance id')}"
        for instance_id in instance_ids
    )


def _one_fleet(
    fleet: Mapping[str, Any],
    ship_details: Mapping[str, Mapping[str, Any]],
    equip_instances: Mapping[str, Mapping[str, Any]],
    conditions: Mapping[str, Any],
) -> FleetRequirementEvidence:
    raw_ship_ids = fleet.get("api_ship")
    if not isinstance(raw_ship_ids, list) or len(raw_ship_ids) > 6:
        raise RequirementEvidenceError("fleet api_ship is invalid")

    equipment_by_ship: dict[int, tuple[int, ...]] = {}
    totals: dict[str, int] = {key: 0 for key in STAT_KEYS}
    ship_conditions: dict[int, int] = {}
    in_dock: list[int] = []

    for raw_ship_id in raw_ship_ids:
        ship_id = _safe_int(raw_ship_id, "fleet ship id")
        if ship_id in (-1, 0):
            continue
        if ship_id < 0:
            raise RequirementEvidenceError("fleet has a negative ship id")

        detail = ship_details.get(str(ship_id))
        if not isinstance(detail, dict):
            raise RequirementEvidenceError(f"ship {ship_id} has no detail record")
        detail_id = _positive_int(detail.get("api_id"), f"ship {ship_id} api_id")
        if detail_id != ship_id:
            raise RequirementEvidenceError(f"ship {ship_id} detail record identity mismatch")

        master_ids: list[int] = []
        for instance_id in _slot_instance_ids(detail, f"ship {ship_id}"):
            record = equip_instances.get(str(instance_id))
            if not isinstance(record, dict):
                raise RequirementEvidenceError(
                    f"equipment instance {instance_id} was not resolved"
                )
            record_id = _positive_int(
                record.get("api_id"), f"equipment instance {instance_id} api_id"
            )
            if record_id != instance_id:
                raise RequirementEvidenceError(
                    f"equipment instance {instance_id} record identity mismatch"
                )
            master_ids.append(
                _positive_int(
                    record.get("api_slotitem_id"),
                    f"equipment instance {instance_id} api_slotitem_id",
                )
            )
        equipment_by_ship[ship_id] = tuple(master_ids)

        for api_field, stat_key in STAT_KEY_BY_API_FIELD.items():
            totals[stat_key] += _displayed_stat(detail.get(api_field), f"ship {ship_id} {api_field}")

        raw_condition = conditions.get(str(ship_id))
        if isinstance(raw_condition, dict):
            raw_condition = raw_condition.get("api_cond")
        if isinstance(raw_condition, int) and not isinstance(raw_condition, bool):
            ship_conditions[ship_id] = raw_condition

        ndock_time = detail.get("api_ndock_time")
        if ndock_time is not None:
            remaining = _safe_int(ndock_time, f"ship {ship_id} api_ndock_time")
            if remaining < 0:
                raise RequirementEvidenceError(f"ship {ship_id} has a negative dock time")
            if remaining > 0:
                in_dock.append(ship_id)

    return FleetRequirementEvidence(
        equipment_by_ship=MappingProxyType(equipment_by_ship),
        total_stats=MappingProxyType(totals),
        conditions=MappingProxyType(ship_conditions),
        in_dock_ship_ids=tuple(in_dock),
    )


def parse_requirement_evidence(
    fleets: Sequence[Mapping[str, Any]],
    ship_details: Mapping[str, Mapping[str, Any]],
    equip_instances: Mapping[str, Mapping[str, Any]],
    *,
    conditions: Mapping[str, Any] | None = None,
) -> dict[int, FleetRequirementEvidence | None]:
    """Return per-fleet evidence; ``None`` means that fleet must fail closed.

    A bad ship never contaminates a good one: the checker needs evidence for
    exactly the target fleet, and a missing field on Fleet 4 must not turn a
    Fleet 2 equipment check into a guess.
    """
    condition_records: Mapping[str, Any] = conditions or {}
    result: dict[int, FleetRequirementEvidence | None] = {}
    for index, fleet in enumerate(fleets):
        if not isinstance(fleet, dict):
            raise RequirementEvidenceError(f"fleet {index} is not an object")
        fleet_id = _positive_int(fleet.get("api_id"), f"fleet {index} api_id")
        try:
            result[fleet_id] = _one_fleet(
                fleet, ship_details, equip_instances, condition_records
            )
        except RequirementEvidenceError:
            result[fleet_id] = None
    return result


__all__ = [
    "EQUIP_INSTANCE_FIELDS",
    "FleetRequirementEvidence",
    "MAX_EQUIPMENT_INSTANCE_IDS",
    "RequirementEvidenceError",
    "SHIP_DETAIL_FIELDS",
    "STAT_KEYS",
    "STAT_KEY_BY_API_FIELD",
    "build_equipment_where_clause",
    "equipment_instance_ids_for_fleets",
    "parse_requirement_evidence",
]
