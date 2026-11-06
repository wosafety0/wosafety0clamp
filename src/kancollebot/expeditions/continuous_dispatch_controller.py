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
from kancollebot.adapters.poi.cdp_input import CapturedFrame
from kancollebot.expeditions.continuous_scheduler import (
    ContinuousExpeditionScheduler,
    FleetPhase,
    MissionTuple,
    UiOperationArbiter,
)
from kancollebot.expeditions.dispatch_recovery import (
    DurableDispatchCoordinator,
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


@dataclass(frozen=True, slots=True)
class UiStateEvidence:
    """Evidence returned by a UI driver after its two-frame state gate passes."""

    state: str
    consecutive_states: tuple[str, str]
    evidence: str
    frames: tuple[CapturedFrame, CapturedFrame] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, str) or not self.state.strip():
            raise ValueError("state must be a non-empty string")
        if (
            not isinstance(self.consecutive_states, tuple)
            or len(self.consecutive_states) != 2
            or any(not isinstance(item, str) or not item.strip() for item in self.consecutive_states)
        ):
            raise ValueError("consecutive_states must contain exactly two state labels")
        if not isinstance(self.evidence, str) or not self.evidence.strip():
            raise ValueError("evidence must be a non-empty string")
        if self.frames is not None:
            if (
                not isinstance(self.frames, tuple)
                or len(self.frames) != 2
                or any(not isinstance(frame, CapturedFrame) for frame in self.frames)
            ):
                raise ValueError("frames must contain exactly two CapturedFrame instances")
            first, second = self.frames
            if (
                first.frame_id >= second.frame_id
                or first.captured_monotonic_ns >= second.captured_monotonic_ns
                or first.sha256 == second.sha256
            ):
                raise ValueError(
                    "frames must have distinct hashes and strictly increasing IDs/timestamps"
                )


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
    """Append-only event sink suitable for sharing the scheduler SQLite database."""

    def __init__(self, path: str) -> None:
        self.path = str(path)
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
        payload = json.dumps(
            dict(fields), ensure_ascii=False, sort_keys=True,
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
        freshness_witness_timeout_seconds: float = 30.0,
        clock: Callable[[], datetime] | None = None,
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
            not isinstance(freshness_witness_timeout_seconds, (int, float))
            or isinstance(freshness_witness_timeout_seconds, bool)
            or not math.isfinite(freshness_witness_timeout_seconds)
            or freshness_witness_timeout_seconds < 0
        ):
            raise ValueError("freshness_witness_timeout_seconds must be finite and non-negative")
        if not callable(event_sink):
            raise ValueError("a durable event_sink is required")
        self.scheduler = scheduler
        self.dispatch_coordinator = dispatch_coordinator
        self.state_reader = state_reader
        self.ui_driver = ui_driver
        self.ui_arbiter = ui_arbiter
        self.mission_requirements = dict(mission_requirements)
        self.event_sink = event_sink
        self.allow_ui_actions = allow_ui_actions
        self.max_state_age = max_state_age
        self.freshness_witness_timeout_seconds = float(freshness_witness_timeout_seconds)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def dispatch_one_ready(self, fleet_id: int) -> DispatchExecution:
        """Dispatch one ready fleet, confirm the new mission, and verify Home Port."""
        if not self.allow_ui_actions:
            raise ContinuousDispatchError("UI actions are disabled; explicit enablement is required")
        if fleet_id not in self.mission_requirements:
            raise ContinuousDispatchError(f"Fleet {fleet_id} has no configured expedition assignment")

        with self.ui_arbiter.operation(f"continuous-dispatch-fleet-{fleet_id}"):
            baseline, baseline_now = self._read_observation()
            snapshot = self._snapshot_for(baseline, fleet_id)
            requirement = self.mission_requirements[fleet_id]
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
                self.event_sink("dispatch_intent_persisted", {
                    "fleet_id": fleet_id,
                    "mission_id": mission_id,
                    "ship_ids": intent.ship_ids,
                    "baseline_observed_at": intent.baseline_observed_at.isoformat(),
                })
                self.ui_driver.dispatch_expedition(fleet_id=fleet_id, mission_id=mission_id)
                action_completed_at = self.clock()
                self.dispatch_coordinator.mark_ui_action_completed(
                    fleet_id, completed_at=action_completed_at
                )

                self._wait_for_fresh_port_witness(
                    after=action_completed_at,
                    phase="post_dispatch",
                )
                after_dispatch, _ = self._read_observation(newer_than=action_completed_at)
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
                self.event_sink("dispatch_confirmed_by_poi", {
                    "fleet_id": fleet_id,
                    "mission_id": mission_id,
                    "final_time_ms": mission.final_time_ms,
                    "ship_ids": mission.ship_ids,
                    "observed_at": after_dispatch.observed_at.isoformat(),
                })

                home_navigation_started_at = self.clock()
                home_evidence = self.ui_driver.return_to_home_port(fleet_id=fleet_id)
                self._require_home_port_evidence(home_evidence)
                self._wait_for_fresh_port_witness(
                    after=home_navigation_started_at,
                    phase="post_home_navigation",
                )
                after_home, _ = self._read_observation(newer_than=home_navigation_started_at)
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
                self._block_after_ambiguous_action(fleet_id, exc)
                raise

    def wait_for_return(self, fleet_id: int) -> MissionTuple | None:
        """Confirm one due expedition returned using a newer passive Poi witness.

        This method performs no UI input and never retries a game action. It is
        callable only after dispatch was confirmed and Home Port was verified.
        """
        current = self.scheduler.fleet(fleet_id)
        if not self.scheduler.may_wait_for_return(fleet_id) or current.mission is None:
            raise ContinuousDispatchError(
                f"Fleet {fleet_id} is not eligible for return observation; "
                "dispatch and Home Port must both be verified"
            )

        active_mission = current.mission
        return_deadline = datetime.fromtimestamp(
            active_mission.final_time_ms / 1000,
            tz=timezone.utc,
        )
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ContinuousDispatchError("return observation clock must be timezone-aware")
        if now < return_deadline:
            self.event_sink("expedition_return_not_due", {
                "fleet_id": fleet_id,
                "mission_id": active_mission.mission_id,
                "expected_return_at": return_deadline.isoformat(),
            })
            return None

        self.event_sink("expedition_return_observation_started", {
            "fleet_id": fleet_id,
            "mission_id": active_mission.mission_id,
            "expected_return_at": return_deadline.isoformat(),
            "source": "passive_port_observer",
        })
        try:
            self._wait_for_fresh_port_witness(
                after=return_deadline,
                phase="post_return_deadline",
            )
            observation, _ = self._read_observation(newer_than=return_deadline)
            snapshot = self._snapshot_for(observation, fleet_id)
            if (
                snapshot.mission_status != 2
                or snapshot.mission_id != active_mission.mission_id
                or snapshot.return_at is None
            ):
                raise ContinuousDispatchError(
                    f"Poi has not confirmed Fleet {fleet_id} returned from its expedition"
                )
            returned_mission = MissionTuple(
                mission_status=2,
                mission_id=snapshot.mission_id,
                final_time_ms=int(snapshot.return_at.timestamp() * 1000),
                reserved=snapshot.reserved,
                ship_ids=snapshot.ship_ids,
            )
            if not returned_mission.same_expedition(active_mission):
                raise ContinuousDispatchError(
                    "returned mission tuple does not match the dispatched mission identity"
                )

            self.scheduler.confirm_returned(fleet_id, returned_mission)
            self.event_sink("expedition_return_confirmed_by_poi", {
                "fleet_id": fleet_id,
                "mission_id": returned_mission.mission_id,
                "final_time_ms": returned_mission.final_time_ms,
                "reserved": returned_mission.reserved,
                "ship_ids": returned_mission.ship_ids,
                "observed_at": observation.observed_at.isoformat(),
            })
            return returned_mission
        except Exception as exc:
            self._block_after_ambiguous_action(fleet_id, exc)
            raise

    def _wait_for_fresh_port_witness(self, *, after: datetime, phase: str) -> None:
        """Wait only on the already-armed passive observer; never poll MCP or retry UI input."""
        waiter = getattr(self.state_reader, "wait_for_fresh_port_witness", None)
        if not callable(waiter):
            return
        self.event_sink("dispatch_freshness_wait_started", {
            "phase": phase,
            "after": after.isoformat(),
            "timeout_seconds": self.freshness_witness_timeout_seconds,
            "source": "passive_port_observer",
        })
        try:
            received = waiter(
                after=after,
                timeout_seconds=self.freshness_witness_timeout_seconds,
            )
        except Exception as exc:
            self.event_sink("dispatch_freshness_wait_failed", {
                "phase": phase,
                "error_type": type(exc).__name__,
            })
            raise ContinuousDispatchError(
                f"Passive Poi freshness wait failed during {phase}"
            ) from exc
        if received is not True:
            self.event_sink("dispatch_freshness_wait_timed_out", {
                "phase": phase,
                "ui_click_retries": 0,
            })
            raise ContinuousDispatchError(
                f"No new natural /api_port/port freshness witness was observed during {phase}"
            )
        self.event_sink("dispatch_freshness_wait_completed", {
            "phase": phase,
            "after": after.isoformat(),
        })

    def _read_observation(
        self, *, newer_than: datetime | None = None
    ) -> tuple[ObservedState[tuple[ExpeditionFleetSnapshot, ...]], datetime]:
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