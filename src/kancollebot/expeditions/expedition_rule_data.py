"""The one static expedition-requirement table used by R3-I.

This module is deliberately *data plus pure lookups*. It does not read Poi, does
not open a CDP session, does not know which expeditions an account has unlocked
and never performs a game action. The numbers live in the adjacent JSON file so
that composition roots do not each carry their own copy of a mission's rules.

Provenance is part of the contract: every rule records where its numbers came
from, how confident that source is, and any known disagreement between sources.
A rule with an unresolved source conflict is never silently trusted - the
evaluator turns it into ``UNKNOWN``.

Data provenance (also recorded verbatim in the JSON ``provenance`` block):

* primary  - ``XVs32/kcauto_custom`` develop ``18d61ab`` data/expedition/expedition.json
* check    - ``wikiwiki.jp/kancolle`` 遠征/詳細一覧表 (World 1, drums, flagship)
* extras   - ``poooi/poi-plugin-ezexped`` master ``d5051e5`` world-1.es (A-series)
* corrob.  - ``keji8/KanColle-JSON-Data`` master ``3e642f2`` (levels, flagship types)

None of these is official game data. The upstream README says its table is
compiled from a community wiki, not extracted from the game client. See
``docs/research/R3I_UPSTREAM_RESEARCH.md`` for the full source assessment.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kancollebot.calibration.expedition_catalog import (
    DEFAULT_CATALOG,
    NUMBER_BY_MISSION_ID,
)

from .mission_eligibility import MissionCompositionRequirement

DATA_PATH = Path(__file__).resolve().parent / "data" / "expedition_requirements.json"
EXPECTED_SCHEMA_VERSION = "r3i-expedition-requirements-1"

STAT_FIELDS = ("firepower", "anti_air", "asw", "los")
CONFLICTABLE_FIELDS = frozenset(
    {
        "minimum_ships",
        "flagship_min_level",
        "fleet_min_total_level",
        "required_drums",
        "required_drum_carriers",
        "minimum_total_stats",
    }
)


class ExpeditionRuleDataError(ValueError):
    """The static rule table is missing, malformed, or internally inconsistent."""


def _int(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ExpeditionRuleDataError(f"{label} must be an integer >= {minimum}")
    return value


def _optional_int(value: Any, label: str, *, minimum: int = 0) -> int | None:
    return None if value is None else _int(value, label, minimum=minimum)


def _stypes(value: Any, label: str) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)):
        raise ExpeditionRuleDataError(f"{label} must be an array of api_stype codes")
    result = tuple(_int(item, f"{label} entry", minimum=1) for item in value)
    if not result or len(set(result)) != len(result):
        raise ExpeditionRuleDataError(f"{label} must contain unique positive stype codes")
    return result


@dataclass(frozen=True, slots=True)
class StypeGroupRequirement:
    """At least ``count`` ships whose api_stype is one of ``stypes``."""

    stypes: tuple[int, ...]
    count: int

    def __post_init__(self) -> None:
        if not isinstance(self.stypes, tuple) or not self.stypes:
            raise ValueError("stypes must be a non-empty tuple")
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in self.stypes):
            raise ValueError("stypes must contain positive api_stype codes")
        if isinstance(self.count, bool) or not isinstance(self.count, int) or self.count < 1:
            raise ValueError("count must be a positive integer")


@dataclass(frozen=True, slots=True)
class SourceConflict:
    """A real, unresolved disagreement between two upstream rule sources."""

    field: str
    values: Mapping[str, int | None]
    note: str

    def __post_init__(self) -> None:
        if self.field not in CONFLICTABLE_FIELDS:
            raise ValueError(f"unknown conflict field {self.field!r}")
        if len(self.values) < 2:
            raise ValueError("a source conflict needs at least two sources")
        if not isinstance(self.note, str) or not self.note.strip():
            raise ValueError("a source conflict needs an explanatory note")


@dataclass(frozen=True, slots=True)
class ExpeditionRequirement:
    """One expedition's dispatch conditions, in our own normalized vocabulary.

    ``None`` means "no such requirement", never "the requirement is satisfied".
    An empty mapping/tuple means the same. Anything the observation cannot
    prove must be resolved by the evaluator as ``UNKNOWN``, not as a pass.
    """

    mission_id: int
    catalog_number: str
    catalog_name: str
    minimum_ships: int
    allow_extra_ships: bool
    required_stype_groups: tuple[StypeGroupRequirement, ...]
    free_slots: int
    flagship_stypes: tuple[int, ...]
    flagship_min_level: int
    fleet_min_total_level: int | None
    required_drums: int
    required_drum_carriers: int
    required_equipment_master_ids: tuple[int, ...]
    minimum_total_stats: Mapping[str, int]
    composition_unstructured: bool
    evidence_class: str
    confidence: str
    source_refs: tuple[str, ...]
    source_conflicts: tuple[SourceConflict, ...]

    @property
    def has_source_conflict(self) -> bool:
        return bool(self.source_conflicts)

    @property
    def requires_equipment_evidence(self) -> bool:
        return bool(
            self.required_drums
            or self.required_drum_carriers
            or self.required_equipment_master_ids
        )

    @property
    def requires_stat_evidence(self) -> bool:
        return bool(self.minimum_total_stats)


@dataclass(frozen=True, slots=True)
class RuleProvenance:
    schema_version: str
    rule_version: str
    kind: str
    generated_on: str
    primary_name: str
    primary_url: str
    primary_commit: str
    primary_retrieved_on: str
    primary_confidence: str
    type_code_space: str
    drum_equipment_master_id: int

    @property
    def rule_source(self) -> str:
        return f"{self.primary_name}@{self.primary_commit[:12]}"


class ExpeditionRuleSet:
    """Immutable validated lookup over the static rule table."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        if not isinstance(payload, Mapping):
            raise ExpeditionRuleDataError("rule payload must be an object")
        schema = payload.get("schema_version")
        if schema != EXPECTED_SCHEMA_VERSION:
            raise ExpeditionRuleDataError(f"unsupported rule schema {schema!r}")
        provenance = payload.get("provenance")
        if not isinstance(provenance, Mapping):
            raise ExpeditionRuleDataError("rule payload needs a provenance block")
        primary = provenance.get("primary")
        if not isinstance(primary, Mapping):
            raise ExpeditionRuleDataError("rule payload needs a primary source")
        for key in ("name", "url", "commit", "retrieved_on", "confidence"):
            if not isinstance(primary.get(key), str) or not primary[key].strip():
                raise ExpeditionRuleDataError(f"primary source is missing {key}")
        if provenance.get("type_code_space") != "api_stype":
            raise ExpeditionRuleDataError("rule ship types must use the api_stype code space")
        self.provenance = RuleProvenance(
            schema_version=schema,
            rule_version=str(payload.get("rule_version") or ""),
            kind=str(payload.get("kind") or ""),
            generated_on=str(payload.get("generated_on") or ""),
            primary_name=primary["name"],
            primary_url=primary["url"],
            primary_commit=primary["commit"],
            primary_retrieved_on=primary["retrieved_on"],
            primary_confidence=primary["confidence"],
            type_code_space=provenance["type_code_space"],
            drum_equipment_master_id=_int(
                provenance.get("drum_equipment_master_id"),
                "drum_equipment_master_id",
                minimum=1,
            ),
        )
        if not self.provenance.rule_version or self.provenance.kind != "static_reference":
            raise ExpeditionRuleDataError("rule_version and static_reference kind are required")

        raw_rules = payload.get("rules")
        if not isinstance(raw_rules, list) or not raw_rules:
            raise ExpeditionRuleDataError("rule payload needs at least one rule")
        rules: list[ExpeditionRequirement] = []
        seen: set[int] = set()
        for raw in raw_rules:
            rule = self._parse_rule(raw)
            if rule.mission_id in seen:
                raise ExpeditionRuleDataError(f"duplicate rule for mission {rule.mission_id}")
            seen.add(rule.mission_id)
            rules.append(rule)
        self._rules = tuple(sorted(rules, key=lambda item: item.mission_id))
        self._by_id = {rule.mission_id: rule for rule in self._rules}

    @staticmethod
    def _parse_rule(raw: Any) -> ExpeditionRequirement:
        if not isinstance(raw, Mapping):
            raise ExpeditionRuleDataError("a rule must be an object")
        mission_id = _int(raw.get("mission_id"), "mission_id", minimum=1)
        number = NUMBER_BY_MISSION_ID.get(mission_id)
        entry = DEFAULT_CATALOG.entry_of(number) if number else None
        if entry is None:
            raise ExpeditionRuleDataError(f"mission {mission_id} is not in the in-repo catalogue")
        if raw.get("catalog_number") != number or raw.get("catalog_name") != entry.name:
            raise ExpeditionRuleDataError(
                f"mission {mission_id} catalogue identity does not match the in-repo catalogue"
            )

        groups_raw = raw.get("required_stype_groups")
        if not isinstance(groups_raw, list):
            raise ExpeditionRuleDataError(f"mission {mission_id} needs required_stype_groups")
        groups = tuple(
            StypeGroupRequirement(_stypes(g.get("stypes"), "stypes"), _int(g.get("count"), "count", minimum=1))
            for g in groups_raw
        )
        minimum_ships = _int(raw.get("minimum_ships"), "minimum_ships", minimum=1)
        if minimum_ships > 6:
            raise ExpeditionRuleDataError("a fleet cannot contain more than six ships")
        free_slots = _int(raw.get("free_slots"), "free_slots")
        if sum(group.count for group in groups) + free_slots != minimum_ships:
            raise ExpeditionRuleDataError(
                f"mission {mission_id} composition slots do not sum to minimum_ships"
            )
        stats_raw = raw.get("minimum_total_stats")
        if not isinstance(stats_raw, Mapping):
            raise ExpeditionRuleDataError("minimum_total_stats must be an object")
        stats: dict[str, int] = {}
        for key, value in stats_raw.items():
            if key not in STAT_FIELDS:
                raise ExpeditionRuleDataError(f"unknown stat field {key!r}")
            stats[key] = _int(value, f"minimum_total_stats.{key}")
        conflicts_raw = raw.get("source_conflicts")
        if not isinstance(conflicts_raw, list):
            raise ExpeditionRuleDataError("source_conflicts must be an array")
        conflicts = tuple(
            SourceConflict(
                field=str(item.get("field") or ""),
                values=dict(item.get("values") or {}),
                note=str(item.get("note") or ""),
            )
            for item in conflicts_raw
        )
        refs = raw.get("source_refs")
        if not isinstance(refs, list) or not refs or any(not isinstance(r, str) or not r.strip() for r in refs):
            raise ExpeditionRuleDataError(f"mission {mission_id} needs at least one source_ref")
        evidence_class = raw.get("evidence_class")
        confidence = raw.get("confidence")
        if not isinstance(evidence_class, str) or not evidence_class.strip():
            raise ExpeditionRuleDataError("evidence_class is required")
        if confidence not in {"cross_checked", "single_source"}:
            raise ExpeditionRuleDataError("confidence must be cross_checked or single_source")
        if not isinstance(raw.get("allow_extra_ships"), bool) or not isinstance(
            raw.get("composition_unstructured"), bool
        ):
            raise ExpeditionRuleDataError("composition flags must be booleans")

        return ExpeditionRequirement(
            mission_id=mission_id,
            catalog_number=number,
            catalog_name=entry.name,
            minimum_ships=minimum_ships,
            allow_extra_ships=raw["allow_extra_ships"],
            required_stype_groups=groups,
            free_slots=free_slots,
            flagship_stypes=() if not raw.get("flagship_stypes") else _stypes(raw["flagship_stypes"], "flagship_stypes"),
            flagship_min_level=_int(raw.get("flagship_min_level"), "flagship_min_level"),
            fleet_min_total_level=_optional_int(raw.get("fleet_min_total_level"), "fleet_min_total_level"),
            required_drums=_int(raw.get("required_drums"), "required_drums"),
            required_drum_carriers=_int(raw.get("required_drum_carriers"), "required_drum_carriers"),
            required_equipment_master_ids=(
                () if not raw.get("required_equipment_master_ids")
                else _stypes(raw["required_equipment_master_ids"], "required_equipment_master_ids")
            ),
            minimum_total_stats=stats,
            composition_unstructured=raw["composition_unstructured"],
            evidence_class=evidence_class,
            confidence=confidence,
            source_refs=tuple(refs),
            source_conflicts=conflicts,
        )

    def __len__(self) -> int:
        return len(self._rules)

    @property
    def requirements(self) -> tuple[ExpeditionRequirement, ...]:
        return self._rules

    @property
    def known_mission_ids(self) -> tuple[int, ...]:
        return tuple(self._by_id)

    @property
    def rule_version(self) -> str:
        return self.provenance.rule_version

    @property
    def rule_source(self) -> str:
        return self.provenance.rule_source

    @property
    def drum_equipment_master_id(self) -> int:
        return self.provenance.drum_equipment_master_id

    def requirement_for(self, mission_id: int) -> ExpeditionRequirement | None:
        if isinstance(mission_id, bool) or not isinstance(mission_id, int):
            return None
        return self._by_id.get(mission_id)

    def provenance_summary(self) -> dict[str, object]:
        """Display-safe provenance; never contains credentials or live state."""
        return {
            "schema_version": self.provenance.schema_version,
            "rule_version": self.provenance.rule_version,
            "kind": self.provenance.kind,
            "generated_on": self.provenance.generated_on,
            "rule_source": self.rule_source,
            "primary_url": self.provenance.primary_url,
            "primary_retrieved_on": self.provenance.primary_retrieved_on,
            "primary_confidence": self.provenance.primary_confidence,
            "type_code_space": self.provenance.type_code_space,
            "drum_equipment_master_id": self.drum_equipment_master_id,
            "rule_count": len(self._rules),
        }


def load_rule_set(path: Path | str = DATA_PATH) -> ExpeditionRuleSet:
    """Load and validate the on-disk static table. Fails closed on any defect."""
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExpeditionRuleDataError(f"cannot read expedition rule table: {exc}") from exc
    return ExpeditionRuleSet(payload)


DEFAULT_RULE_SET = load_rule_set()


def mission_composition_requirement_for(
    mission_id: int, *, rule_set: ExpeditionRuleSet = DEFAULT_RULE_SET
) -> MissionCompositionRequirement:
    """Adapt one static rule to the LEGACY composition gate's narrow shape.

    The legacy gate only knows ship count, flagship level and an optional
    whole-fleet type whitelist. Missions with partial ship-type requirements
    (e.g. 1 light cruiser + 2 destroyers + 1 free slot) cannot be expressed as
    a whitelist without wrongly rejecting a valid fleet, so we deliberately
    leave ``allowed_ship_types=None`` here. The R3-I evaluator is the authority
    for precise type/equipment/stat checks; the legacy gate remains an extra,
    blunt pre-check.

    A flagship level of 0 in the upstream table means "no level requirement";
    the legacy dataclass requires a positive integer, so it becomes 1 (which
    every real ship satisfies). This is recorded rather than silently guessed.
    """
    rule = rule_set.requirement_for(mission_id)
    if rule is None:
        raise ExpeditionRuleDataError(
            f"mission {mission_id} has no static requirement rule; refusing to invent one"
        )
    return MissionCompositionRequirement(
        mission_id=mission_id,
        minimum_ships=rule.minimum_ships,
        minimum_flagship_level=max(1, rule.flagship_min_level),
        allowed_ship_types=None,
    )


def requirements_for_assignments(
    assignments: Mapping[int, int], *, rule_set: ExpeditionRuleSet = DEFAULT_RULE_SET
) -> dict[int, MissionCompositionRequirement]:
    """Build the legacy fleet-id -> requirement mapping from the static table.

    ``assignments`` maps a fleet id to the mission id that fleet should run.
    Which fleet runs which mission is a scheduling decision (R3-J); only the
    per-mission NUMBERS come from the static table, so no composition root has
    to hardcode them.
    """
    if not isinstance(assignments, Mapping):
        raise ExpeditionRuleDataError("assignments must be a mapping")
    result: dict[int, MissionCompositionRequirement] = {}
    for fleet_id, mission_id in assignments.items():
        if isinstance(fleet_id, bool) or not isinstance(fleet_id, int) or fleet_id < 2:
            raise ExpeditionRuleDataError("assignment fleet ids must be integers >= 2")
        if isinstance(mission_id, bool) or not isinstance(mission_id, int) or mission_id < 1:
            raise ExpeditionRuleDataError("assignment mission ids must be positive integers")
        result[fleet_id] = mission_composition_requirement_for(mission_id, rule_set=rule_set)
    return result


__all__ = [
    "CONFLICTABLE_FIELDS",
    "DATA_PATH",
    "DEFAULT_RULE_SET",
    "STAT_FIELDS",
    "ExpeditionRequirement",
    "ExpeditionRuleDataError",
    "ExpeditionRuleSet",
    "RuleProvenance",
    "SourceConflict",
    "StypeGroupRequirement",
    "load_rule_set",
    "mission_composition_requirement_for",
    "requirements_for_assignments",
]
