"""R3-I: a read-only, deterministic expedition requirement evaluator.

This module is the pure decision layer. It never opens a CDP session, never
reads SQLite, never sends input, never constructs a game API request and never
changes a fleet. It takes an already-normalized fleet snapshot plus a static
rule and returns an explainable PASS / REJECT / UNKNOWN verdict.

Design rules, all enforced below:

* Only ``PASS`` allows a dispatch. ``REJECT`` and ``UNKNOWN`` are both blocking.
* A missing field is never treated as a satisfied condition. Where the rule
  requires evidence the observation cannot supply, that check is ``UNKNOWN``.
* A stale/unverified observation, a missing rule, a rule-source conflict, or a
  mixed/ambiguous fleet identity forces the whole verdict to ``UNKNOWN``.
* Sparkle / great success are reported read-only; they are not dispatch gates.
* No ship replacement, fleet re-composition or equipment change is proposed.

Untrusted state, a missing/conflicting rule, or ambiguous fleet identity are
BLOCKING unknowns: they take precedence over a visible REJECT because with
untrustworthy inputs even a failing condition is not reliable evidence. Other
UNKNOWN checks (missing equipment/stat/supply/HP evidence) do not override an
otherwise definite REJECT, but they can never produce PASS - both verdicts are
dispatch-blocking and both are recorded in ``rejection_reasons``.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from kancollebot.core.observed_state import ObservedState
from kancollebot.core.observation_contracts import ObservedFleetSnapshot

from .expedition_rule_data import (
    DEFAULT_RULE_SET,
    STAT_FIELDS,
    ExpeditionRequirement,
    ExpeditionRuleSet,
)

CONFIDENCE_ORDER: dict[str, int] = {"single_source": 0, "cross_checked": 1}
BLOCKING_UNKNOWN_CHECKS = frozenset({"state_trust", "rule_source", "fleet_identity"})
SPARKLE_CONDITION_THRESHOLD = 50


class RequirementOutcome(str, Enum):
    PASS = "PASS"
    REJECT = "REJECT"
    UNKNOWN = "UNKNOWN"


class CheckStatus(str, Enum):
    PASS = "PASS"
    REJECT = "REJECT"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class SnapshotNormalizationError(ValueError):
    """A fleet observation cannot be normalized into the checker's contract."""


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    status: CheckStatus
    detail: str


@dataclass(frozen=True, slots=True)
class SparkleAssessment:
    """Read-only morale observation. Never a dispatch condition."""

    conditions_available: bool
    sparkled_ship_ids: tuple[int, ...]
    all_sparkled: bool
    great_success_note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "conditions_available": self.conditions_available,
            "sparkled_ship_ids": list(self.sparkled_ship_ids),
            "all_sparkled": self.all_sparkled,
            "great_success_note": self.great_success_note,
            "used_as_dispatch_gate": False,
        }


@dataclass(frozen=True, slots=True)
class NormalizedFleetSnapshot:
    """The evaluator's entire input: observed facts, never decisions.

    ``None`` on an optional evidence field means "the source did not provide
    it", which the evaluator turns into UNKNOWN when the rule needs it. Booleans
    such as ``fuel_ready`` use the same convention.
    """

    fleet_id: int
    ship_ids: tuple[int, ...]
    ship_levels: tuple[int, ...]
    ship_types: tuple[int, ...]
    state_trusted: bool
    state_untrusted_reasons: tuple[str, ...]
    snapshot_source: str
    mission_status: int | None = 0
    reserved: int = 0
    fleet_unlocked: bool = True
    dispatch_policy_allowed: bool = True
    fuel_ready: bool | None = None
    ammo_ready: bool | None = None
    unsupplied_ship_ids: tuple[int, ...] = ()
    heavy_damage_ship_ids: tuple[int, ...] = ()
    hp_evidence_available: bool = True
    in_dock_ship_ids: tuple[int, ...] = ()
    equipment_by_ship: Mapping[int, tuple[int, ...]] | None = None
    total_stats: Mapping[str, int] | None = None
    conditions: Mapping[int, int] | None = None
    identity_ambiguous: bool = False
    identity_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.fleet_id, bool) or not isinstance(self.fleet_id, int) or self.fleet_id < 1:
            raise SnapshotNormalizationError("fleet_id must be a positive integer")
        for label, values in (
            ("ship_ids", self.ship_ids),
            ("ship_levels", self.ship_levels),
            ("ship_types", self.ship_types),
        ):
            if not isinstance(values, tuple):
                raise SnapshotNormalizationError(f"{label} must be a tuple")
            if any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in values):
                raise SnapshotNormalizationError(f"{label} must contain positive integers")
        if len(self.ship_ids) > 6:
            raise SnapshotNormalizationError("a fleet cannot contain more than six ships")
        if len(set(self.ship_ids)) != len(self.ship_ids):
            raise SnapshotNormalizationError("ship_ids must be unique")
        if self.ship_levels and len(self.ship_levels) != len(self.ship_ids):
            raise SnapshotNormalizationError("ship_levels must match ship_ids")
        if self.ship_types and len(self.ship_types) != len(self.ship_ids):
            raise SnapshotNormalizationError("ship_types must match ship_ids")
        if not isinstance(self.state_trusted, bool):
            raise SnapshotNormalizationError("state_trusted must be a boolean")
        if not isinstance(self.snapshot_source, str) or not self.snapshot_source.strip():
            raise SnapshotNormalizationError("snapshot_source must be a non-empty string")
        if self.total_stats is not None:
            for key, value in self.total_stats.items():
                if key not in STAT_FIELDS:
                    raise SnapshotNormalizationError(f"unknown total stat field {key!r}")
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise SnapshotNormalizationError("total stat values must be non-negative integers")

    @property
    def composition_metadata_complete(self) -> bool:
        return (
            len(self.ship_levels) == len(self.ship_ids)
            and len(self.ship_types) == len(self.ship_ids)
        )


@dataclass(frozen=True, slots=True)
class RequirementEvaluation:
    """Explainable, JSON-safe verdict. ``overall_result`` is the only gate."""

    fleet_id: int
    expedition_id: int
    overall_result: RequirementOutcome
    checked_at: datetime
    rule_source: str
    rule_version: str
    rule_confidence: str | None
    rule_evidence_class: str | None
    rule_source_refs: tuple[str, ...]
    passed_checks: tuple[str, ...]
    failed_checks: tuple[str, ...]
    unknown_checks: tuple[str, ...]
    rejection_reasons: tuple[str, ...]
    checks: tuple[CheckResult, ...]
    sparkle_assessment: SparkleAssessment
    safety: Mapping[str, object]

    @property
    def allows_dispatch(self) -> bool:
        return self.overall_result is RequirementOutcome.PASS

    def as_dict(self) -> dict[str, Any]:
        """A deterministic payload suitable for an append-only event sink."""
        return {
            "fleet_id": self.fleet_id,
            "expedition_id": self.expedition_id,
            "overall_result": self.overall_result.value,
            "checked_at": self.checked_at.isoformat(),
            "rule_source": self.rule_source,
            "rule_version": self.rule_version,
            "rule_confidence": self.rule_confidence,
            "rule_evidence_class": self.rule_evidence_class,
            "rule_source_refs": list(self.rule_source_refs),
            "passed_checks": list(self.passed_checks),
            "failed_checks": list(self.failed_checks),
            "unknown_checks": list(self.unknown_checks),
            "rejection_reasons": list(self.rejection_reasons),
            "checks": [
                {"name": c.name, "status": c.status.value, "detail": c.detail}
                for c in self.checks
            ],
            "sparkle_assessment": self.sparkle_assessment.as_dict(),
            "safety": dict(self.safety),
            "allows_dispatch": self.allows_dispatch,
        }


# ---------------------------------------------------------------------------
# Read-only normalizers. These only reshape already-observed data.
# ---------------------------------------------------------------------------


def normalize_observed_fleets(
    observation: ObservedState[tuple[ObservedFleetSnapshot, ...]],
    fleet_id: int,
    *,
    now: datetime,
    max_state_age: timedelta,
    fleet_unlocked: bool = True,
    dispatch_policy_allowed: bool = True,
    equipment_by_ship: Mapping[int, tuple[int, ...]] | None = None,
    total_stats: Mapping[str, int] | None = None,
    conditions: Mapping[int, int] | None = None,
) -> NormalizedFleetSnapshot:
    """Normalize the production Poi reader's observed state for one fleet.

    Freshness is judged here exactly as the dispatch controller judges it:
    ``source_freshness_verified`` AND a local age within ``max_state_age``.
    A stale observation is still returned (as an UNKNOWN snapshot) so the
    verdict can explain why instead of silently dropping the evidence.

    Equipment/stat/morale/dock evidence is taken from the SAME fleet record
    that carries ship_ids and the freshness verdict. The three keyword
    arguments remain only as explicit test overrides; the production dispatch
    gate passes none of them, so it cannot combine a fresh composition with
    equipment read at a different instant.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if max_state_age < timedelta(0):
        raise ValueError("max_state_age must not be negative")
    reasons: list[str] = []
    if not observation.source_freshness_verified:
        reasons.append("Poi source freshness has not been independently verified")
    if not observation.is_fresh(now=now, max_age=max_state_age):
        reasons.append("Poi observation is stale or future-dated")
    if not observation.source.startswith("poi-redux:"):
        reasons.append(f"unexpected observation source {observation.source!r}")

    matches = [item for item in observation.payload if item.fleet_id == fleet_id]
    identity_reasons: list[str] = []
    if len(matches) != 1:
        identity_reasons.append(
            f"expected exactly one fleet record for Fleet {fleet_id}, found {len(matches)}"
        )
        record = matches[0] if matches else None
    else:
        record = matches[0]
    if record is not None and record.fleet_id != fleet_id:
        identity_reasons.append("fleet record identity does not match the requested fleet")
    if record is not None:
        other_ship_ids = {
            ship_id
            for item in observation.payload
            if item.fleet_id != fleet_id
            for ship_id in item.ship_ids
        }
        mixed = set(record.ship_ids) & other_ship_ids
        if mixed:
            identity_reasons.append(
                f"ship ids {sorted(mixed)} also appear in another fleet's record"
            )

    if record is None:
        return NormalizedFleetSnapshot(
            fleet_id=fleet_id,
            ship_ids=(),
            ship_levels=(),
            ship_types=(),
            state_trusted=not reasons,
            state_untrusted_reasons=tuple(reasons),
            snapshot_source=observation.source,
            identity_ambiguous=True,
            identity_reasons=tuple(identity_reasons),
            equipment_by_ship=equipment_by_ship,
            total_stats=total_stats,
            conditions=conditions,
        )

    return NormalizedFleetSnapshot(
        fleet_id=fleet_id,
        ship_ids=record.ship_ids,
        ship_levels=record.ship_levels,
        ship_types=record.ship_types,
        state_trusted=not reasons,
        state_untrusted_reasons=tuple(reasons),
        snapshot_source=observation.source,
        mission_status=record.mission_status,
        reserved=record.reserved,
        fleet_unlocked=fleet_unlocked,
        dispatch_policy_allowed=dispatch_policy_allowed,
        fuel_ready=record.fuel_ready,
        ammo_ready=record.ammo_ready,
        heavy_damage_ship_ids=record.heavy_damage_ship_ids,
        hp_evidence_available=True,
        in_dock_ship_ids=record.in_dock_ship_ids,
        equipment_by_ship=(
            equipment_by_ship
            if equipment_by_ship is not None
            else record.equipment_by_ship
        ),
        total_stats=total_stats if total_stats is not None else record.total_stats,
        conditions=conditions if conditions is not None else record.conditions,
        identity_ambiguous=bool(identity_reasons),
        identity_reasons=tuple(identity_reasons),
    )


def normalize_raw_port_payload(
    fleets: Sequence[Mapping[str, Any]],
    ships: Mapping[str, Mapping[str, Any]],
    fleet_id: int,
    *,
    state_trusted: bool,
    state_untrusted_reasons: Sequence[str] = (),
    snapshot_source: str = "captured-port-payload",
    fleet_unlocked: bool = True,
    dispatch_policy_allowed: bool = True,
    equipment_by_ship: Mapping[int, tuple[int, ...]] | None = None,
    total_stats: Mapping[str, int] | None = None,
) -> NormalizedFleetSnapshot:
    """Normalize a captured ``api_port/port`` shaped payload, read-only.

    A bare port payload carries neither ship master data nor levels; that is
    exactly why the evaluator must answer UNKNOWN for composition checks rather
    than guess. This builder deliberately leaves those fields empty.
    """
    matches = [item for item in fleets if item.get("api_id") == fleet_id]
    identity_reasons: list[str] = []
    if len(matches) != 1:
        identity_reasons.append(
            f"expected exactly one fleet record for Fleet {fleet_id}, found {len(matches)}"
        )
        record = matches[0] if matches else None
    else:
        record = matches[0]
    if record is None:
        return NormalizedFleetSnapshot(
            fleet_id=fleet_id,
            ship_ids=(),
            ship_levels=(),
            ship_types=(),
            state_trusted=state_trusted,
            state_untrusted_reasons=tuple(state_untrusted_reasons),
            snapshot_source=snapshot_source,
            identity_ambiguous=True,
            identity_reasons=tuple(identity_reasons),
        )

    raw_ids = record.get("api_ship") or []
    ship_ids = tuple(
        value for value in raw_ids
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
    )
    other_ship_ids = {
        value
        for item in fleets
        if item.get("api_id") != fleet_id
        for value in (item.get("api_ship") or [])
        if isinstance(value, int) and value > 0
    }
    mixed = set(ship_ids) & other_ship_ids
    if mixed:
        identity_reasons.append(f"ship ids {sorted(mixed)} also appear in another fleet's record")

    mission = record.get("api_mission") or [0, 0, 0, 0]
    status = mission[0] if isinstance(mission, Sequence) and len(mission) >= 1 else None
    reserved = mission[3] if isinstance(mission, Sequence) and len(mission) >= 4 else 0

    heavy_damage: list[int] = []
    conditions: dict[int, int] = {}
    hp_available = True
    for ship_id in ship_ids:
        ship = ships.get(str(ship_id))
        if not isinstance(ship, dict):
            hp_available = False
            continue
        now_hp, max_hp = ship.get("api_nowhp"), ship.get("api_maxhp")
        if (
            not isinstance(now_hp, int) or isinstance(now_hp, bool)
            or not isinstance(max_hp, int) or isinstance(max_hp, bool)
            or max_hp < 1
        ):
            hp_available = False
        elif now_hp * 4 <= max_hp:
            heavy_damage.append(ship_id)
        condition = ship.get("api_cond")
        if isinstance(condition, int) and not isinstance(condition, bool):
            conditions[ship_id] = condition
    if len(conditions) != len(ship_ids):
        conditions = {}

    return NormalizedFleetSnapshot(
        fleet_id=fleet_id,
        ship_ids=ship_ids,
        ship_levels=(),
        ship_types=(),
        state_trusted=state_trusted,
        state_untrusted_reasons=tuple(state_untrusted_reasons),
        snapshot_source=snapshot_source,
        mission_status=status if isinstance(status, int) and not isinstance(status, bool) else None,
        reserved=reserved if isinstance(reserved, int) and not isinstance(reserved, bool) else 0,
        fleet_unlocked=fleet_unlocked,
        dispatch_policy_allowed=dispatch_policy_allowed,
        # A bare port payload has no master max_fuel/max_bull, so full-supply
        # cannot be proven. None -> UNKNOWN, never an assumed PASS.
        fuel_ready=None,
        ammo_ready=None,
        heavy_damage_ship_ids=tuple(heavy_damage),
        hp_evidence_available=hp_available,
        equipment_by_ship=equipment_by_ship,
        total_stats=total_stats,
        conditions=conditions or None,
        identity_ambiguous=bool(identity_reasons),
        identity_reasons=tuple(identity_reasons),
    )


# ---------------------------------------------------------------------------
# Pure evaluator
# ---------------------------------------------------------------------------


def _comparable_signature(rule: ExpeditionRequirement) -> tuple[Any, ...]:
    """Fields that two independent rule sources must agree on."""
    return (
        rule.minimum_ships,
        rule.allow_extra_ships,
        tuple((tuple(g.stypes), g.count) for g in rule.required_stype_groups),
        rule.free_slots,
        rule.flagship_stypes,
        rule.flagship_min_level,
        rule.fleet_min_total_level,
        rule.required_drums,
        rule.required_drum_carriers,
        rule.required_equipment_master_ids,
        tuple(sorted(rule.minimum_total_stats.items())),
        rule.composition_unstructured,
    )


class ExpeditionRequirementChecker:
    """Stateless, injectable-clock evaluator over one static rule set."""

    def __init__(
        self,
        rule_set: ExpeditionRuleSet = DEFAULT_RULE_SET,
        *,
        clock: Callable[[], datetime] | None = None,
        minimum_confidence: str = "single_source",
        corroborating_rule_set: ExpeditionRuleSet | None = None,
    ) -> None:
        if not isinstance(rule_set, ExpeditionRuleSet):
            raise ValueError("rule_set must be an ExpeditionRuleSet")
        if minimum_confidence not in CONFIDENCE_ORDER:
            raise ValueError("minimum_confidence must be single_source or cross_checked")
        if corroborating_rule_set is not None and not isinstance(
            corroborating_rule_set, ExpeditionRuleSet
        ):
            raise ValueError("corroborating_rule_set must be an ExpeditionRuleSet or None")
        self.rule_set = rule_set
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.minimum_confidence = minimum_confidence
        self.corroborating_rule_set = corroborating_rule_set

    @property
    def rule_source(self) -> str:
        return self.rule_set.rule_source

    @property
    def rule_version(self) -> str:
        return self.rule_set.rule_version

    def evaluate(
        self,
        fleet: NormalizedFleetSnapshot,
        mission_id: int,
        *,
        fleet_id: int | None = None,
    ) -> RequirementEvaluation:
        """Evaluate every required condition and return an explainable verdict.

        ``fleet_id`` is the fleet the caller intends to dispatch. Passing it
        explicitly protects against a normalized snapshot from another fleet
        being used to clear a different fleet's gate.
        """
        if not isinstance(fleet, NormalizedFleetSnapshot):
            raise ValueError("fleet must be a NormalizedFleetSnapshot")
        if isinstance(mission_id, bool) or not isinstance(mission_id, int) or mission_id < 1:
            raise ValueError("mission_id must be a positive integer")
        target_fleet_id = fleet.fleet_id if fleet_id is None else fleet_id
        if isinstance(target_fleet_id, bool) or not isinstance(target_fleet_id, int) or target_fleet_id < 1:
            raise ValueError("fleet_id must be a positive integer")
        checked_at = self.clock()
        if checked_at.tzinfo is None or checked_at.utcoffset() is None:
            raise ValueError("the checker clock must return a timezone-aware datetime")

        rule = self.rule_set.requirement_for(mission_id)
        checks: list[CheckResult] = []

        def add(name: str, status: CheckStatus, detail: str) -> None:
            checks.append(CheckResult(name, status, detail))

        # --- G. data trust -------------------------------------------------
        if fleet.state_trusted:
            add("state_trust", CheckStatus.PASS, f"Observation source {fleet.snapshot_source!r} is trusted.")
        else:
            add(
                "state_trust",
                CheckStatus.UNKNOWN,
                "Observation is not trusted: " + "; ".join(fleet.state_untrusted_reasons or ("no reason given",)),
            )

        # --- rule availability / provenance / conflicts ---------------------
        if rule is None:
            add(
                "rule_source",
                CheckStatus.UNKNOWN,
                f"Mission {mission_id} has no record in static rule table "
                f"{self.rule_version}; dispatch cannot be cleared without a rule.",
            )
        elif rule.has_source_conflict:
            fields = ", ".join(
                f"{c.field} ({c.values})" for c in rule.source_conflicts
            )
            add(
                "rule_source",
                CheckStatus.UNKNOWN,
                f"Mission {mission_id} has unresolved upstream source conflicts: {fields}. "
                "The rule is not authoritative; the condition results below are "
                "informational only and the verdict stays UNKNOWN.",
            )
        elif CONFIDENCE_ORDER[rule.confidence] < CONFIDENCE_ORDER[self.minimum_confidence]:
            add(
                "rule_source",
                CheckStatus.UNKNOWN,
                f"Rule confidence {rule.confidence!r} is below the required "
                f"{self.minimum_confidence!r}.",
            )
        elif self.corroborating_rule_set is not None:
            other = self.corroborating_rule_set.requirement_for(mission_id)
            if other is not None and _comparable_signature(rule) != _comparable_signature(other):
                add(
                    "rule_source",
                    CheckStatus.UNKNOWN,
                    f"Two rule sources disagree on mission {mission_id} requirements; "
                    "refusing to pick one.",
                )
            else:
                add("rule_source", CheckStatus.PASS, self._rule_detail(rule))
        else:
            add("rule_source", CheckStatus.PASS, self._rule_detail(rule))

        # --- non-target fleet data -----------------------------------------
        if fleet.fleet_id != target_fleet_id or fleet.identity_ambiguous:
            reasons = [f"snapshot fleet id {fleet.fleet_id} != target {target_fleet_id}"] if fleet.fleet_id != target_fleet_id else []
            reasons.extend(fleet.identity_reasons)
            add("fleet_identity", CheckStatus.UNKNOWN, "; ".join(reasons))
        else:
            add("fleet_identity", CheckStatus.PASS, f"Exactly one trusted record for Fleet {target_fleet_id}.")

        # --- E. fleet availability -----------------------------------------
        unavailable: list[str] = []
        if not fleet.fleet_unlocked:
            unavailable.append("fleet is not unlocked on this account")
        if not fleet.dispatch_policy_allowed:
            unavailable.append("fleet is not permitted by the expedition dispatch policy")
        if fleet.mission_status is None:
            unavailable.append("mission status is unavailable")
        elif fleet.mission_status != 0:
            unavailable.append(f"fleet is already on an expedition (status {fleet.mission_status})")
        if fleet.reserved != 0:
            unavailable.append(f"fleet is reserved (reserved={fleet.reserved})")
        if fleet.in_dock_ship_ids:
            unavailable.append(f"ships in repair dock: {sorted(fleet.in_dock_ship_ids)}")
        if unavailable:
            add("fleet_available", CheckStatus.REJECT, "; ".join(unavailable))
        else:
            add("fleet_available", CheckStatus.PASS, "Fleet is idle, unlocked and policy-permitted.")

        # --- A. ship count --------------------------------------------------
        if rule is None:
            add("ship_count", CheckStatus.UNKNOWN, "No rule, so no ship count can be checked.")
        else:
            count = len(fleet.ship_ids)
            if count < rule.minimum_ships:
                add(
                    "ship_count",
                    CheckStatus.REJECT,
                    f"Mission {mission_id} requires at least {rule.minimum_ships} ships; observed {count}.",
                )
            elif not rule.allow_extra_ships and count != rule.minimum_ships:
                add(
                    "ship_count",
                    CheckStatus.REJECT,
                    f"Mission {mission_id} has no free slots and requires exactly "
                    f"{rule.minimum_ships} ships; observed {count}.",
                )
            else:
                add("ship_count", CheckStatus.PASS, f"Observed {count} ships meets the minimum {rule.minimum_ships}.")

        # --- A/B. flagship validity and level --------------------------------
        if not fleet.ship_ids:
            add("flagship_valid", CheckStatus.UNKNOWN, "Fleet has no ships; flagship cannot be identified.")
            add("flagship_level", CheckStatus.UNKNOWN, "Fleet has no ships; flagship level cannot be read.")
        elif not fleet.composition_metadata_complete:
            add(
                "flagship_valid",
                CheckStatus.UNKNOWN,
                "Ship level/type metadata is incomplete; flagship cannot be validated.",
            )
            add(
                "flagship_level",
                CheckStatus.UNKNOWN,
                "Ship level metadata is incomplete; flagship level cannot be validated.",
            )
        else:
            flagship_type = fleet.ship_types[0]
            if rule is not None and rule.flagship_stypes and flagship_type not in rule.flagship_stypes:
                add(
                    "flagship_valid",
                    CheckStatus.REJECT,
                    f"Flagship api_stype {flagship_type} is not one of the required "
                    f"flagship types {list(rule.flagship_stypes)}.",
                )
            else:
                allowed = list(rule.flagship_stypes) if rule is not None and rule.flagship_stypes else "any"
                add("flagship_valid", CheckStatus.PASS, f"Flagship api_stype {flagship_type} is valid (allowed: {allowed}).")
            level = fleet.ship_levels[0]
            required_level = rule.flagship_min_level if rule is not None else None
            if required_level is None:
                add("flagship_level", CheckStatus.UNKNOWN, "No rule, so no flagship level can be checked.")
            elif level < required_level:
                add(
                    "flagship_level",
                    CheckStatus.REJECT,
                    f"Flagship level {level} is below required level {required_level}.",
                )
            else:
                add("flagship_level", CheckStatus.PASS, f"Flagship level {level} meets minimum {required_level}.")

        # --- B. fleet total level -------------------------------------------
        if rule is None or rule.fleet_min_total_level is None:
            add("fleet_total_level", CheckStatus.NOT_APPLICABLE, "No fleet total-level requirement.")
        elif not fleet.composition_metadata_complete:
            add("fleet_total_level", CheckStatus.UNKNOWN, "Ship level metadata is incomplete.")
        else:
            total = sum(fleet.ship_levels)
            if total < rule.fleet_min_total_level:
                add(
                    "fleet_total_level",
                    CheckStatus.REJECT,
                    f"Fleet total level {total} is below required {rule.fleet_min_total_level}.",
                )
            else:
                add("fleet_total_level", CheckStatus.PASS, f"Fleet total level {total} meets minimum {rule.fleet_min_total_level}.")

        # --- A. ship type composition ---------------------------------------
        if rule is None:
            add("ship_type_composition", CheckStatus.UNKNOWN, "No rule, so composition cannot be checked.")
        elif rule.composition_unstructured:
            add(
                "ship_type_composition",
                CheckStatus.UNKNOWN,
                "This expedition has alternative compositions that are not yet "
                "structured in the static table; refusing to guess one.",
            )
        elif not rule.required_stype_groups:
            add("ship_type_composition", CheckStatus.NOT_APPLICABLE, "No fixed ship-type requirement.")
        elif not fleet.composition_metadata_complete:
            add("ship_type_composition", CheckStatus.UNKNOWN, "Ship type metadata is incomplete.")
        else:
            remaining = Counter(fleet.ship_types)
            shortages: list[str] = []
            # Narrower groups first, so overlapping type groups are allocated
            # deterministically and do not double-count the same ship.
            for group in sorted(rule.required_stype_groups, key=lambda g: (len(g.stypes), -g.count)):
                need = group.count
                for stype in sorted(group.stypes):
                    taken = min(need, remaining[stype])
                    remaining[stype] -= taken
                    need -= taken
                    if need == 0:
                        break
                if need:
                    shortages.append(f"need {group.count} of api_stype {list(group.stypes)}, short by {need}")
            if shortages:
                add("ship_type_composition", CheckStatus.REJECT, "; ".join(shortages))
            else:
                add("ship_type_composition", CheckStatus.PASS, "All required ship-type groups are satisfied.")

        # --- C. equipment ----------------------------------------------------
        if rule is None:
            add("equipment", CheckStatus.UNKNOWN, "No rule, so equipment cannot be checked.")
        elif not rule.requires_equipment_evidence:
            add("equipment", CheckStatus.NOT_APPLICABLE, "No equipment requirement for this expedition.")
        elif fleet.equipment_by_ship is None:
            add(
                "equipment",
                CheckStatus.UNKNOWN,
                "The observation carries no equipment data; drum and equipment "
                "requirements cannot be proven.",
            )
        elif set(fleet.equipment_by_ship) != set(fleet.ship_ids):
            add("equipment", CheckStatus.UNKNOWN, "Equipment data does not cover exactly the fleet members.")
        else:
            drum_id = self.rule_set.drum_equipment_master_id
            carriers = 0
            drums = 0
            found: Counter[int] = Counter()
            for ship_id, equipment in fleet.equipment_by_ship.items():
                drum_count = sum(1 for item in equipment if item == drum_id)
                if drum_count:
                    carriers += 1
                    drums += drum_count
                found.update(item for item in equipment if item != drum_id)
            problems: list[str] = []
            if drums < rule.required_drums:
                problems.append(f"drums {drums} < required {rule.required_drums}")
            if carriers < rule.required_drum_carriers:
                problems.append(f"drum-carrying ships {carriers} < required {rule.required_drum_carriers}")
            for master_id in rule.required_equipment_master_ids:
                if found[master_id] < 1:
                    problems.append(f"required equipment master id {master_id} is missing")
            if problems:
                add("equipment", CheckStatus.REJECT, "; ".join(problems))
            else:
                add("equipment", CheckStatus.PASS, f"Drums {drums} on {carriers} ships meet the requirement.")

        # --- D. fleet stats --------------------------------------------------
        if rule is None:
            add("fleet_stats", CheckStatus.UNKNOWN, "No rule, so stat requirements cannot be checked.")
        elif not rule.requires_stat_evidence:
            add("fleet_stats", CheckStatus.NOT_APPLICABLE, "No fleet stat requirement.")
        elif fleet.total_stats is None:
            add("fleet_stats", CheckStatus.UNKNOWN, "The observation carries no fleet stat totals.")
        else:
            missing_fields = [key for key in rule.minimum_total_stats if key not in fleet.total_stats]
            if missing_fields:
                add("fleet_stats", CheckStatus.UNKNOWN, f"Missing stat totals: {missing_fields}.")
            else:
                short = [
                    f"{key} {fleet.total_stats[key]} < {value}"
                    for key, value in rule.minimum_total_stats.items()
                    if fleet.total_stats[key] < value
                ]
                if short:
                    add("fleet_stats", CheckStatus.REJECT, "; ".join(short))
                else:
                    add("fleet_stats", CheckStatus.PASS, "All required fleet stat totals are met.")

        # --- F. supply -------------------------------------------------------
        if fleet.fuel_ready is None or fleet.ammo_ready is None:
            add(
                "supply",
                CheckStatus.UNKNOWN,
                "Fuel/ammo fullness cannot be proven from this observation "
                "(missing current or maximum values).",
            )
        elif not fleet.fuel_ready or not fleet.ammo_ready:
            missing = []
            if not fleet.fuel_ready:
                missing.append("fuel")
            if not fleet.ammo_ready:
                missing.append("ammunition")
            detail = "Full-supply requirement not met: " + " and ".join(missing) + "."
            if fleet.unsupplied_ship_ids:
                detail += f" Ship ids: {sorted(fleet.unsupplied_ship_ids)}."
            add("supply", CheckStatus.REJECT, detail)
        else:
            add("supply", CheckStatus.PASS, "Every member is fully supplied with fuel and ammunition.")

        # --- E/F. damage -----------------------------------------------------
        if not fleet.hp_evidence_available:
            add("damage", CheckStatus.UNKNOWN, "HP evidence is unavailable; heavy damage cannot be ruled out.")
        elif fleet.heavy_damage_ship_ids:
            add(
                "damage",
                CheckStatus.REJECT,
                f"Taiha (大破) ships block dispatch: {sorted(fleet.heavy_damage_ship_ids)}.",
            )
        else:
            add("damage", CheckStatus.PASS, "No heavy damage observed.")

        # --- read-only sparkle / great success -------------------------------
        if fleet.conditions is None or set(fleet.conditions) != set(fleet.ship_ids):
            sparkle = SparkleAssessment(
                conditions_available=False,
                sparkled_ship_ids=(),
                all_sparkled=False,
                great_success_note="Morale data unavailable; great success cannot be estimated. Read-only, never a gate.",
            )
        else:
            sparkled = tuple(
                ship_id for ship_id in fleet.ship_ids
                if fleet.conditions[ship_id] >= SPARKLE_CONDITION_THRESHOLD
            )
            sparkle = SparkleAssessment(
                conditions_available=True,
                sparkled_ship_ids=sparkled,
                all_sparkled=len(sparkled) == len(fleet.ship_ids) and bool(fleet.ship_ids),
                great_success_note=(
                    "Great success is only estimated from observed morale. No ship "
                    "is sparkled, replaced or resupplied by this evaluator. Read-only."
                ),
            )

        passed = tuple(c.name for c in checks if c.status in (CheckStatus.PASS, CheckStatus.NOT_APPLICABLE))
        failed = tuple(c.name for c in checks if c.status is CheckStatus.REJECT)
        unknown = tuple(c.name for c in checks if c.status is CheckStatus.UNKNOWN)
        reasons = tuple(
            f"[{c.status.value}] {c.name}: {c.detail}"
            for c in checks
            if c.status in (CheckStatus.REJECT, CheckStatus.UNKNOWN)
        )

        blocking_unknown = any(name in BLOCKING_UNKNOWN_CHECKS for name in unknown)
        if blocking_unknown:
            overall = RequirementOutcome.UNKNOWN
        elif failed:
            overall = RequirementOutcome.REJECT
        elif unknown:
            overall = RequirementOutcome.UNKNOWN
        else:
            overall = RequirementOutcome.PASS

        return RequirementEvaluation(
            fleet_id=target_fleet_id,
            expedition_id=mission_id,
            overall_result=overall,
            checked_at=checked_at,
            rule_source=self.rule_source,
            rule_version=self.rule_version,
            rule_confidence=rule.confidence if rule else None,
            rule_evidence_class=rule.evidence_class if rule else None,
            rule_source_refs=rule.source_refs if rule else (),
            passed_checks=passed,
            failed_checks=failed,
            unknown_checks=unknown,
            rejection_reasons=reasons,
            checks=tuple(checks),
            sparkle_assessment=sparkle,
            safety={
                "game_clicks_sent": 0,
                "game_api_requests_sent": 0,
                "game_input_sent": False,
                "fleet_composition_changes_proposed": False,
                "ship_state_changes_proposed": False,
                "evaluation_read_only": True,
            },
        )

    @staticmethod
    def _rule_detail(rule: ExpeditionRequirement) -> str:
        return (
            f"Rule {rule.catalog_number} {rule.catalog_name!r} "
            f"from {rule.evidence_class} (confidence {rule.confidence}); "
            f"refs: {', '.join(rule.source_refs)}."
        )


def evaluate_expedition_requirements(
    fleet: NormalizedFleetSnapshot,
    mission_id: int,
    *,
    fleet_id: int | None = None,
    checker: ExpeditionRequirementChecker | None = None,
) -> RequirementEvaluation:
    """Convenience wrapper around the default stateless checker."""
    active = checker or ExpeditionRequirementChecker()
    return active.evaluate(fleet, mission_id, fleet_id=fleet_id)


__all__ = [
    "BLOCKING_UNKNOWN_CHECKS",
    "CONFIDENCE_ORDER",
    "CheckResult",
    "CheckStatus",
    "ExpeditionRequirementChecker",
    "NormalizedFleetSnapshot",
    "RequirementEvaluation",
    "RequirementOutcome",
    "SnapshotNormalizationError",
    "SparkleAssessment",
    "evaluate_expedition_requirements",
    "normalize_observed_fleets",
    "normalize_raw_port_payload",
]
