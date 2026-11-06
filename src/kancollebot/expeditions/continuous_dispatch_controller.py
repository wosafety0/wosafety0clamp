"""Injected-port dispatch orchestration for the continuous dual-fleet scheduler.

This module owns the dispatch transaction boundary, not the Poi/CDP transport.
A production UI driver must use only visible game UI input and must independently
verify each page transition. The controller requires a shared arbiter, one state
reader, one UI driver/session, and a durable event sink from its composition root.
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from kancollebot.core.observed_state import ObservedState
from kancollebot.core.observation_contracts import UiStateEvidence
from kancollebot.core.task_attribution import apply_trusted_task_attribution
from kancollebot.expeditions.continuous_scheduler import (
    ContinuousExpeditionScheduler,
    FleetPhase,
    MissionTuple,
    UiOperationArbiter,
)
from kancollebot.expeditions.dispatch_recovery import (
    DurableDispatchCoordinator,
)
from kancollebot.expeditions.dispatch_registry import (
    DispatchIdentity,
    DispatchRegistryError,
    SqliteDispatchRegistry,
    new_dispatch_id,
    validate_dispatch_id,
)
from kancollebot.expeditions.expedition_requirement_checker import (
    ExpeditionRequirementChecker,
    normalize_observed_fleets,
)
from kancollebot.expeditions.mission_eligibility import (
    FleetCompositionSnapshot,
    MissionCompositionRequirement,
    MissionEligibilityDecision,
    evaluate_mission_composition,
)
from kancollebot.expeditions.planner import (
    ExpeditionDecision,
    ExpeditionFleetSnapshot,
    evaluate_expedition_fleet,
)


class ContinuousDispatchError(RuntimeError):
    """The observed state or UI transaction cannot safely continue."""


class PoiFleetStateReader(Protocol):
    """One shared read-only Poi Redux state reader."""

    def read_fleets(self) -> ObservedState[tuple[ExpeditionFleetSnapshot, ...]]:
        ...


# ``UiStateEvidence`` is a task-neutral two-frame UI checkpoint. The canonical
# definition moved to ``core.observation_contracts``; this module re-exports the
# SAME class object so old ``from ...continuous_dispatch_controller import
# UiStateEvidence`` callers keep identical isinstance/equality behaviour. See
# docs/DESIGN_task_event_attribution.md for the attribution contract.


class ContinuousUiDriver(Protocol):
    """One shared visible-UI driver bound to one CDP input session.

    dispatch_expedition must verify every pre/post page checkpoint and must not
    send game API/server requests. return_to_home_port must return only after
    two consecutive in-memory HOME_PORT recognitions pass the production gate.
    """

    def dispatch_expedition(self, *, fleet_id: int, mission_id: int) -> None:
        ...

    def return_to_home_port(self, *, fleet_id: int) -> UiStateEvidence:
        ...


@dataclass(frozen=True, slots=True)
class DispatchExecution:
    fleet_id: int
    mission_id: int
    mission: MissionTuple
    baseline_observed_at: datetime
    dispatch_observed_at: datetime
    home_port_observed_at: datetime
    home_port_evidence: str


class SqliteDispatchEventLog:
    """Append-only event sink suitable for sharing the scheduler SQLite database.

    ``task_kind`` is opt-in TRUSTED attribution. When supplied, every event
    payload carries it and caller-supplied attribution keys are discarded, so a
    log shared by several automation tasks can be split by task without any
    emitter being able to forge its identity. When omitted the payload stays
    byte-for-byte unchanged, which keeps existing offline tests and on-disk
    databases valid; composition roots for real runs should always pass it.

    No schema migration: the legacy 4-column ``dispatch_events`` table is kept
    as-is and attribution lives in ``payload_json``. See
    docs/DESIGN_task_event_attribution.md for the compatibility evaluation.
    """

    def __init__(self, path: str, *, task_kind: str | None = None) -> None:
        self.path = str(path)
        self.task_kind = task_kind
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS dispatch_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    event TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )"""
            )

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5.0)
        db.execute("PRAGMA busy_timeout = 5000")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def __call__(self, event: str, fields: Mapping[str, object]) -> None:
        if not isinstance(event, str) or not event.strip():
            raise ValueError("event name must be a non-empty string")
        if not isinstance(fields, Mapping):
            raise TypeError("event fields must be a mapping")
        # Trusted attribution wins unconditionally: ``fields`` may carry any
        # diagnostic value but can never claim, replace or forge the task
        # identity of a row in this shared append-only log. With task_kind=None
        # the mapping is returned unchanged for byte-for-byte legacy payloads.
        payload_fields = apply_trusted_task_attribution(
            fields, task_kind=self.task_kind
        )
        payload = json.dumps(
            payload_fields, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO dispatch_events (created_at, event, payload_json) VALUES (?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), event, payload),
            )

    def load_all(self) -> tuple[dict[str, object], ...]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT created_at, event, payload_json FROM dispatch_events ORDER BY event_id"
            ).fetchall()
        return tuple(
            {
                "created_at": created_at,
                "event": event,
                "fields": json.loads(payload_json),
            }
            for created_at, event, payload_json in rows
        )

class ContinuousDispatchController:
    """Execute one durable dispatch transaction for one configured fleet.

    Instantiate exactly one controller per Poi/CDP session and share its
    UiOperationArbiter with every other UI workflow. This class never opens
    transports, never creates game requests, and never retries an ambiguous
    dispatch. Live UI actions are disabled unless explicitly enabled.
    """

    def __init__(
        self,
        *,
        scheduler: ContinuousExpeditionScheduler,
        dispatch_coordinator: DurableDispatchCoordinator,
        state_reader: PoiFleetStateReader,
        ui_driver: ContinuousUiDriver,
        ui_arbiter: UiOperationArbiter,
        mission_requirements: Mapping[int, MissionCompositionRequirement],
        event_sink: Callable[[str, Mapping[str, object]], None],
        allow_ui_actions: bool = False,
        max_state_age: timedelta = timedelta(seconds=30),
        clock: Callable[[], datetime] | None = None,
        passive_witness_wait_seconds: float = 10.0,
        requirement_checker: ExpeditionRequirementChecker | None = None,
        unlocked_fleet_ids: tuple[int, ...] | None = None,
        dispatchable_fleet_ids: tuple[int, ...] | None = None,
        audit_gate: Callable[[], None] | None = None,
        dispatch_registry: SqliteDispatchRegistry | None = None,
        dispatch_source: str = "supervisor",
        mission_name_resolver: Callable[[int], str] | None = None,
        require_mission_name: bool = False,
    ) -> None:
        if set(mission_requirements) != set(scheduler.dispatch_fleet_ids):
            raise ValueError("mission_requirements must configure every and only dispatchable fleet")
        if any(
            isinstance(fleet_id, bool) or not isinstance(fleet_id, int) or fleet_id < 1
            or not isinstance(requirement, MissionCompositionRequirement)
            for fleet_id, requirement in mission_requirements.items()
        ):
            raise ValueError("each dispatch fleet requires a valid mission composition requirement")
        if dispatch_coordinator.scheduler is not scheduler:
            raise ValueError("dispatch coordinator and controller must share the same scheduler")
        if max_state_age < timedelta(0):
            raise ValueError("max_state_age must not be negative")
        if (
            isinstance(passive_witness_wait_seconds, bool)
            or not isinstance(passive_witness_wait_seconds, (int, float))
            or not math.isfinite(passive_witness_wait_seconds)
            or passive_witness_wait_seconds < 0
        ):
            raise ValueError(
                "passive_witness_wait_seconds must be a finite non-negative number"
            )
        if not callable(event_sink):
            raise ValueError("a durable event_sink is required")
        if requirement_checker is not None and not isinstance(
            requirement_checker, ExpeditionRequirementChecker
        ):
            raise ValueError("requirement_checker must be an ExpeditionRequirementChecker")
        if audit_gate is not None and not callable(audit_gate):
            raise ValueError("audit_gate must be callable or None")
        if dispatch_registry is not None and not isinstance(
            dispatch_registry, SqliteDispatchRegistry
        ):
            raise ValueError("dispatch_registry must be a SqliteDispatchRegistry or None")
        if not isinstance(dispatch_source, str) or not dispatch_source.strip():
            raise ValueError("dispatch_source must be a non-empty string")
        if mission_name_resolver is not None and not callable(mission_name_resolver):
            raise ValueError("mission_name_resolver must be callable or None")
        if type(require_mission_name) is not bool:
            raise ValueError("require_mission_name must be a bool")
        if require_mission_name:
            if dispatch_registry is None:
                raise ValueError("required mission name needs the durable dispatch registry")
            if mission_name_resolver is None:
                raise ValueError("required mission name needs a Poi master-data resolver")
        for label, value in (
            ("unlocked_fleet_ids", unlocked_fleet_ids),
            ("dispatchable_fleet_ids", dispatchable_fleet_ids),
        ):
            if value is not None and (
                not isinstance(value, tuple)
                or any(isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in value)
            ):
                raise ValueError(f"{label} must be a tuple of positive integers")
        self.requirement_checker = requirement_checker
        self.unlocked_fleet_ids = unlocked_fleet_ids
        self.dispatchable_fleet_ids = dispatchable_fleet_ids
        self.scheduler = scheduler
        self.dispatch_coordinator = dispatch_coordinator
        self.state_reader = state_reader
        self.ui_driver = ui_driver
        self.ui_arbiter = ui_arbiter
        self.mission_requirements = dict(mission_requirements)
        self.event_sink = event_sink
        self.allow_ui_actions = allow_ui_actions
        self.max_state_age = max_state_age
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.passive_witness_wait_seconds = float(passive_witness_wait_seconds)
        self.audit_gate = audit_gate
        self.dispatch_registry = dispatch_registry
        self.dispatch_source = dispatch_source.strip()
        self.mission_name_resolver = mission_name_resolver
        self.require_mission_name = require_mission_name

    def dispatch_one_ready(
        self, fleet_id: int, *, dispatch_id: str | None = None
    ) -> DispatchExecution:
        """Dispatch one ready fleet, confirm the new mission, and verify Home Port.

        ``dispatch_id`` names this *attempt*. The caller may pass the id it
        already bound the correlation scope to; when it does not, one is minted
        here so a dispatch is never anonymous. The id is minted (or validated)
        before the first click, and the durable identity row is written before
        the irreversible input - a dispatch that cannot be recorded must not be
        sent.
        """
        if not self.allow_ui_actions:
            raise ContinuousDispatchError("UI actions are disabled; explicit enablement is required")
        if fleet_id not in self.mission_requirements:
            raise ContinuousDispatchError(f"Fleet {fleet_id} has no configured expedition assignment")
        # The audit log must be provably writable BEFORE an intent is persisted
        # or a control is clicked. Checking here (not only at the click) keeps a
        # broken log from leaving an unresolved intent behind, which is exactly
        # the state that needs the log to reconcile later.
        if self.audit_gate is not None:
            self.audit_gate()
        # One identity per attempt, minted (or shape-checked) before the first
        # observation and before any input. A caller that already bound a
        # correlation scope passes its id here so the log and the durable
        # ledger name the same attempt.
        attempt_id = validate_dispatch_id(
            dispatch_id if dispatch_id is not None else new_dispatch_id()
        )
        click_attempted = False

        with self.ui_arbiter.operation(f"continuous-dispatch-fleet-{fleet_id}"):
            baseline, baseline_now = self._read_observation(
                origin="dispatch_preflight"
            )
            snapshot = self._snapshot_for(baseline, fleet_id)
            requirement = self.mission_requirements[fleet_id]
            # R3-I: the full static-rule evaluation happens on the SAME fresh
            # baseline, before the first click and before an intent is written.
            # REJECT and UNKNOWN both leave dispatch_clicks_sent at zero.
            if self.requirement_checker is not None:
                normalized = normalize_observed_fleets(
                    baseline,
                    fleet_id,
                    now=baseline_now,
                    max_state_age=self.max_state_age,
                    fleet_unlocked=(
                        fleet_id in self.unlocked_fleet_ids
                        if self.unlocked_fleet_ids is not None else True
                    ),
                    dispatch_policy_allowed=(
                        fleet_id in self.dispatchable_fleet_ids
                        if self.dispatchable_fleet_ids is not None else True
                    ),
                )
                # Prove the gate did not wander onto another observation: the
                # normalizer's snapshot source and ship set must be the exact
                # ones prepare_dispatch() will bind the intent to below.
                if (
                    normalized.snapshot_source != baseline.source
                    or normalized.ship_ids != snapshot.ship_ids
                ):
                    raise ContinuousDispatchError(
                        "R3-I requirement evaluation diverged from the dispatch baseline"
                    )
                verdict = self.requirement_checker.evaluate(
                    normalized, requirement.mission_id, fleet_id=fleet_id
                )
                evaluation_payload = verdict.as_dict()
                evaluation_payload.update(
                    {
                        "baseline_observed_at": baseline.observed_at.isoformat(),
                        "baseline_source": baseline.source,
                        "baseline_source_freshness_verified": baseline.source_freshness_verified,
                        "baseline_ship_ids": list(snapshot.ship_ids),
                        "equipment_evidence_present": normalized.equipment_by_ship is not None,
                        "fleet_stats_evidence_present": normalized.total_stats is not None,
                        "fleet_stats": (
                            dict(normalized.total_stats)
                            if normalized.total_stats is not None
                            else None
                        ),
                        "in_dock_ship_ids": list(normalized.in_dock_ship_ids),
                    }
                )
                self.event_sink("dispatch_requirement_evaluation", evaluation_payload)
                if not verdict.allows_dispatch:
                    reasons = " | ".join(verdict.rejection_reasons[:4])
                    self.event_sink("dispatch_blocked_requirement_check", {
                        "fleet_id": fleet_id,
                        "mission_id": requirement.mission_id,
                        "overall_result": verdict.overall_result.value,
                        "failed_checks": list(verdict.failed_checks),
                        "unknown_checks": list(verdict.unknown_checks),
                        "baseline_observed_at": baseline.observed_at.isoformat(),
                        "baseline_source_freshness_verified": baseline.source_freshness_verified,
                        "dispatch_clicks_sent": 0,
                        "game_api_requests_sent": 0,
                        "fleet_composition_changes_proposed": False,
                    })
                    raise ContinuousDispatchError(
                        f"Fleet {fleet_id} / mission {requirement.mission_id} "
                        f"requirement check {verdict.overall_result.value}: {reasons}"
                    )
            if snapshot.heavy_damage_ship_ids:
                blocked_ship_ids = tuple(snapshot.heavy_damage_ship_ids)
                self.event_sink("dispatch_blocked_large_damage", {
                    "fleet_id": fleet_id,
                    "ship_ids": blocked_ship_ids,
                    "hp_threshold_percent": 25,
                    "dispatch_clicks_sent": 0,
                    "replacement_implemented": False,
                })
                raise ContinuousDispatchError(
                    f"Fleet {fleet_id} has Taiha (大破) ship(s) {blocked_ship_ids}; "
                    "dispatch is blocked before intent creation or UI input"
                )
            self._require_idle_ready_snapshot(snapshot, now=baseline_now, requirement=requirement)

            current = self.scheduler.fleet(fleet_id)
            if current.phase in (FleetPhase.IDLE, FleetPhase.RESUPPLY_REQUIRED):
                self.scheduler.mark_ready(fleet_id)
            elif current.phase is not FleetPhase.READY:
                raise ContinuousDispatchError(
                    f"Fleet {fleet_id} cannot dispatch from phase {current.phase.value}"
                )

            mission_id = requirement.mission_id
            intent = self.dispatch_coordinator.prepare_dispatch(
                fleet_id,
                mission_id,
                baseline,
                now=baseline_now,
                max_age=self.max_state_age,
                reserved=snapshot.reserved,
            )

            try:
                # The durable identity row is written before the event row and
                # before the click: the event stream is diagnostic, the ledger
                # is the record a later settlement is attributed against.
                self._persist_dispatch_identity(attempt_id, intent)
                self.event_sink("dispatch_intent_persisted", {
                    "fleet_id": fleet_id,
                    "mission_id": mission_id,
                    "ship_ids": intent.ship_ids,
                    "baseline_observed_at": intent.baseline_observed_at.isoformat(),
                    "dispatch_id": attempt_id,
                })
                click_attempted = True
                self.ui_driver.dispatch_expedition(fleet_id=fleet_id, mission_id=mission_id)
                action_completed_at = self.clock()
                self.dispatch_coordinator.mark_ui_action_completed(
                    fleet_id, completed_at=action_completed_at
                )

                # Return home BEFORE asking for a new observation. The 母港
                # click is what makes the client request a fresh port payload,
                # so reading first leaves the freshness reader no option but to
                # navigate on its own - and that navigation starts from whatever
                # screen the dispatch just left, where its HOME_PORT
                # precondition cannot hold. Live 2026-11-05: that ordering
                # marked a successful dispatch BLOCKED at 03:24:18 even though
                # Poi had already reported the new mission at 03:24:08.
                home_navigation_started_at = self.clock()
                home_evidence = self.ui_driver.return_to_home_port(fleet_id=fleet_id)

                # The 母港 click already made the client ask for a fresh port
                # payload, so give that natural response a bounded chance to
                # arrive before reading. Reading first is what used to turn the
                # post-dispatch check into a second full three-click round trip;
                # live 2026-11-05 one run spent 27 navigations (75 clicks) on two
                # dispatches. This wait never clicks - if no witness arrives the
                # observation policy below is exactly as strict as before.
                self._await_passive_witness(
                    fleet_id=fleet_id,
                    newer_than=max(home_navigation_started_at, action_completed_at),
                )
                after_dispatch, _ = self._read_observation(
                    newer_than=action_completed_at,
                    origin="dispatch_post_action",
                )
                active_snapshot = self._snapshot_for(after_dispatch, fleet_id)
                mission = self._mission_from_snapshot(
                    active_snapshot,
                    fleet_id=fleet_id,
                    mission_id=mission_id,
                    expected_ship_ids=intent.ship_ids,
                    expected_reserved=intent.reserved,
                )
                if mission.final_time_ms <= int(action_completed_at.timestamp() * 1000):
                    raise ContinuousDispatchError("Poi mission return time is not later than dispatch completion")
                self.dispatch_coordinator.confirm_dispatch(fleet_id, mission)
                self._confirm_dispatch_identity(attempt_id, mission)
                self.event_sink("dispatch_confirmed_by_poi", {
                    "fleet_id": fleet_id,
                    "mission_id": mission_id,
                    "final_time_ms": mission.final_time_ms,
                    "ship_ids": mission.ship_ids,
                    "observed_at": after_dispatch.observed_at.isoformat(),
                    "dispatch_id": attempt_id,
                })

                # Evidence is judged only after the mission is on record: a
                # dispatch that Poi confirms is never re-labelled "unconfirmed"
                # just because the port frame was still animating.
                self._require_home_port_evidence(home_evidence)

                after_home, _ = self._read_observation(
                    newer_than=max(home_navigation_started_at, action_completed_at),
                    origin="dispatch_home_verification",
                )
                home_snapshot = self._snapshot_for(after_home, fleet_id)
                home_mission = self._mission_from_snapshot(
                    home_snapshot,
                    fleet_id=fleet_id,
                    mission_id=mission_id,
                    expected_ship_ids=intent.ship_ids,
                    expected_reserved=intent.reserved,
                )
                if home_mission != mission:
                    raise ContinuousDispatchError(
                        "mission tuple changed while returning to Home Port"
                    )
                state = self.scheduler.confirm_home_port_after_dispatch(
                    fleet_id,
                    ui_state=home_evidence.state,
                    observed_mission=home_mission,
                )
                self.event_sink("dispatch_home_port_verified", {
                    "fleet_id": fleet_id,
                    "mission_id": mission_id,
                    "evidence": home_evidence.evidence,
                    "consecutive_states": home_evidence.consecutive_states,
                    "observed_at": after_home.observed_at.isoformat(),
                })
                return DispatchExecution(
                    fleet_id=fleet_id,
                    mission_id=mission_id,
                    mission=mission,
                    baseline_observed_at=baseline.observed_at,
                    dispatch_observed_at=after_dispatch.observed_at,
                    home_port_observed_at=after_home.observed_at,
                    home_port_evidence=home_evidence.evidence,
                )
            except Exception as exc:
                if click_attempted:
                    self._mark_dispatch_outcome_unknown(attempt_id, exc)
                self._block_after_ambiguous_action(fleet_id, exc)
                raise

    # -- durable dispatch identity ------------------------------------------

    def _persist_dispatch_identity(self, dispatch_id: str, intent) -> None:
        """Record the attempt before the irreversible input.

        No registry wired means offline doubles keep working unchanged. With a
        registry, a write failure propagates: an unrecorded dispatch must not
        be sent, because the settlement half of the loop could never attribute
        its result.
        """
        if self.dispatch_registry is None:
            return
        quest_name = None
        if self.mission_name_resolver is not None:
            try:
                quest_name = self.mission_name_resolver(intent.mission_id)
            except Exception as exc:
                self.event_sink("dispatch_mission_name_lookup_failed", {
                    "dispatch_id": dispatch_id,
                    "fleet_id": intent.fleet_id,
                    "mission_id": intent.mission_id,
                    "error_type": type(exc).__name__,
                    "game_input_sent": False,
                    "game_api_requests": False,
                })
                if self.require_mission_name:
                    raise ContinuousDispatchError(
                        "Poi master-data mission name lookup failed before dispatch"
                    ) from exc
        if self.require_mission_name and (
            not isinstance(quest_name, str) or not quest_name.strip()
        ):
            raise ContinuousDispatchError(
                "Poi master-data lookup did not provide a mission name before dispatch"
            )
        if isinstance(quest_name, str):
            quest_name = quest_name.strip() or None
        if quest_name is not None:
            self.event_sink("dispatch_mission_name_resolved", {
                "dispatch_id": dispatch_id,
                "fleet_id": intent.fleet_id,
                "mission_id": intent.mission_id,
                "quest_name": quest_name,
                "source": "poi_lookup(kind=missions)",
                "game_input_sent": False,
                "game_api_requests": False,
            })
        self.dispatch_registry.record_intent(
            DispatchIdentity(
                dispatch_id=dispatch_id,
                fleet_id=intent.fleet_id,
                mission_id=intent.mission_id,
                ship_ids=tuple(intent.ship_ids),
                quest_name=quest_name,
                intent_persisted_at=self.clock(),
                status="pending",
                task_kind=getattr(self.dispatch_registry, "task_kind", None),
                source=self.dispatch_source,
                action_completed_at=None,
                detail="intent persisted before the expedition start click",
            )
        )

    def _confirm_dispatch_identity(self, dispatch_id: str, mission) -> None:
        """Record Poi's confirmation of the active mission.

        This is the one boundary where a dispatch becomes a durable *fact*:
        the server is running this mission for this fleet with these ships. The
        Home Port verification that follows is a separate, later proof.
        """
        if self.dispatch_registry is None:
            return
        identity = self.dispatch_registry.get(dispatch_id)
        self.dispatch_registry.record_confirmed(
            dispatch_id,
            confirmed_at=self.clock(),
            final_time_ms=mission.final_time_ms,
            quest_name=identity.quest_name if identity is not None else None,
        )

    def _mark_dispatch_outcome_unknown(self, dispatch_id: str, error: Exception) -> None:
        """Record an attempt whose outcome was never proven.

        Best-effort by necessity: this runs inside an exception handler, and a
        ledger failure here must not mask the original failure. It never
        invents a confirmation.
        """
        if self.dispatch_registry is None:
            return
        try:
            self.dispatch_registry.record_unknown(
                dispatch_id,
                reason=(
                    f"UI action outcome unknown after {type(error).__name__}; "
                    "manual reconciliation required"
                ),
            )
        except DispatchRegistryError:
            pass

    def _await_passive_witness(self, *, fleet_id: int, newer_than: datetime) -> None:
        """Give the port response that the Home Port click caused a chance to land.

        Only the refreshing reader has this capability. A plain reader (offline
        fake, or a composition root with UI refresh disabled) is left untouched,
        and the observation gate in ``_read_observation`` stays the single
        authority on whether the state is trustworthy.
        """
        waiter = getattr(self.state_reader, "await_passive_witness", None)
        if not callable(waiter) or self.passive_witness_wait_seconds <= 0:
            return
        self.event_sink("dispatch_post_action_passive_wait", {
            "fleet_id": fleet_id,
            "timeout_seconds": self.passive_witness_wait_seconds,
            "game_actions_sent": 0,
        })
        waiter(
            timeout_seconds=self.passive_witness_wait_seconds,
            newer_than=newer_than,
        )

    def _read_observation(
        self,
        *,
        newer_than: datetime | None = None,
        origin: str = "dispatch_read",
    ) -> tuple[ObservedState[tuple[ExpeditionFleetSnapshot, ...]], datetime]:
        ensure_fresh = getattr(self.state_reader, "ensure_fresh", None)
        if callable(ensure_fresh):
            observation = ensure_fresh(origin=origin)
        else:
            observation = self.state_reader.read_fleets()
        now = self.clock()
        try:
            observation.require_fresh(now=now, max_age=self.max_state_age)
        except ValueError as exc:
            raise ContinuousDispatchError(f"Poi observation is not trusted: {exc}") from exc
        if not observation.source.startswith("poi-redux:"):
            raise ContinuousDispatchError("state observation must originate from Poi Redux")
        if newer_than is not None and observation.observed_at <= newer_than:
            raise ContinuousDispatchError("Poi observation did not cross the UI action time boundary")
        return observation, now

    @staticmethod
    def _snapshot_for(
        observation: ObservedState[tuple[ExpeditionFleetSnapshot, ...]], fleet_id: int
    ) -> ExpeditionFleetSnapshot:
        matches = [item for item in observation.payload if item.fleet_id == fleet_id]
        if len(matches) != 1:
            raise ContinuousDispatchError(
                f"Poi observation must contain exactly one record for Fleet {fleet_id}"
            )
        return matches[0]

    @staticmethod
    def _require_idle_ready_snapshot(
        snapshot: ExpeditionFleetSnapshot, *, now: datetime, requirement: MissionCompositionRequirement
    ) -> None:
        if (
            snapshot.mission_status != 0
            or snapshot.mission_id is not None
            or snapshot.return_at is not None
            or snapshot.reserved != 0
        ):
            raise ContinuousDispatchError("dispatch baseline does not prove an idle mission tuple")
        plan = evaluate_expedition_fleet(snapshot, now=now)
        if plan.decision is not ExpeditionDecision.READY_TO_DISPATCH:
            raise ContinuousDispatchError(
                f"fleet is not ready to dispatch: {plan.decision.value}: {plan.reason}"
            )

        if (
            len(snapshot.ship_levels) != len(snapshot.ship_ids)
            or len(snapshot.ship_types) != len(snapshot.ship_ids)
        ):
            raise ContinuousDispatchError("mission composition metadata is missing or incomplete")
        composition = FleetCompositionSnapshot(
            snapshot.ship_ids, snapshot.ship_levels, snapshot.ship_types
        )
        composition_plan = evaluate_mission_composition(composition, requirement)
        if composition_plan.decision is not MissionEligibilityDecision.ELIGIBLE:
            raise ContinuousDispatchError(
                f"mission composition is not eligible: {composition_plan.decision.value}: "
                f"{composition_plan.reason}"
            )
    @staticmethod
    def _mission_from_snapshot(
        snapshot: ExpeditionFleetSnapshot,
        *,
        fleet_id: int,
        mission_id: int,
        expected_ship_ids: tuple[int, ...],
        expected_reserved: int,
    ) -> MissionTuple:
        if (
            snapshot.fleet_id != fleet_id
            or snapshot.mission_status != 1
            or snapshot.mission_id != mission_id
            or snapshot.return_at is None
            or snapshot.ship_ids != expected_ship_ids
            or snapshot.reserved != expected_reserved
        ):
            raise ContinuousDispatchError(
                f"Poi active mission does not match Fleet {fleet_id} dispatch intent"
            )
        final_time_ms = int(snapshot.return_at.timestamp() * 1000)
        return MissionTuple(
            mission_status=1,
            mission_id=mission_id,
            final_time_ms=final_time_ms,
            reserved=snapshot.reserved,
            ship_ids=snapshot.ship_ids,
        )

    @staticmethod
    def _require_home_port_evidence(evidence: UiStateEvidence) -> None:
        if not isinstance(evidence, UiStateEvidence):
            raise ContinuousDispatchError("UI driver did not return structured Home Port evidence")
        if (
            evidence.state != "HOME_PORT"
            or evidence.consecutive_states != ("HOME_PORT", "HOME_PORT")
        ):
            raise ContinuousDispatchError(
                "Home Port requires two consecutive matching in-memory state observations"
            )

    def _block_after_ambiguous_action(self, fleet_id: int, error: Exception) -> None:
        try:
            current = self.scheduler.fleet(fleet_id)
            if current.phase in (FleetPhase.DISPATCHING, FleetPhase.EXPEDITION_ACTIVE):
                self.scheduler.block(
                    fleet_id,
                    f"continuous dispatch stopped after {type(error).__name__}; manual reconciliation required",
                )
        except Exception:
            pass
        try:
            self.event_sink("dispatch_blocked", {
                "fleet_id": fleet_id,
                "error_type": type(error).__name__,
                "reason": "ambiguous UI or Poi postcondition; automatic retry forbidden",
            })
        except Exception:
            pass