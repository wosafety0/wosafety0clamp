"""Crash-safe dispatch intent persistence and conservative Poi reconciliation."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kancollebot.core.observed_state import ObservedState
from kancollebot.expeditions.continuous_scheduler import (
    ContinuousExpeditionScheduler,
    FleetPhase,
    FleetRuntime,
    MissionTuple,
    SchedulerInvariantError,
)
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot


class DispatchIntentError(RuntimeError):
    """A dispatch intent cannot be safely prepared, loaded, or reconciled."""


@dataclass(frozen=True, slots=True)
class DispatchIntent:
    fleet_id: int
    mission_id: int
    ship_ids: tuple[int, ...]
    baseline_mission: tuple[int, int, int, int]
    baseline_observed_at: datetime
    prepared_at: datetime
    reserved: int = 0
    action_completed_at: datetime | None = None

    def __post_init__(self) -> None:
        if isinstance(self.fleet_id, bool) or not isinstance(self.fleet_id, int) or self.fleet_id not in (2, 3):
            raise ValueError("dispatch intent fleet_id must be Fleet 2 or Fleet 3")
        if isinstance(self.mission_id, bool) or not isinstance(self.mission_id, int) or self.mission_id < 1:
            raise ValueError("mission_id must be a positive integer")
        if not isinstance(self.ship_ids, tuple) or not self.ship_ids or len(self.ship_ids) > 6:
            raise ValueError("ship_ids must be a non-empty tuple of at most six ships")
        if any(isinstance(ship_id, bool) or not isinstance(ship_id, int) or ship_id < 1 for ship_id in self.ship_ids):
            raise ValueError("ship_ids must contain positive integers")
        if len(set(self.ship_ids)) != len(self.ship_ids):
            raise ValueError("ship_ids must be unique")
        if self.baseline_mission != (0, 0, 0, 0):
            raise ValueError("dispatch intent requires an idle baseline mission tuple")
        for label, value in (("baseline_observed_at", self.baseline_observed_at), ("prepared_at", self.prepared_at)):
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{label} must be timezone-aware")
        if self.prepared_at < self.baseline_observed_at:
            raise ValueError("prepared_at cannot predate the baseline observation")
        if isinstance(self.reserved, bool) or not isinstance(self.reserved, int) or self.reserved < 0:
            raise ValueError("reserved must be a non-negative integer")
        if self.action_completed_at is not None:
            if (self.action_completed_at.tzinfo is None or self.action_completed_at.utcoffset() is None
                    or self.action_completed_at < self.prepared_at):
                raise ValueError("action_completed_at must be timezone-aware and after preparation")


class SqliteDispatchIntentStore:
    """Persist dispatch intent separately but in the same SQLite file as fleet state."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS dispatch_intents (
                    fleet_id INTEGER PRIMARY KEY,
                    mission_id INTEGER NOT NULL,
                    ship_ids_json TEXT NOT NULL,
                    baseline_mission_json TEXT NOT NULL,
                    baseline_observed_at TEXT NOT NULL,
                    prepared_at TEXT NOT NULL,
                    reserved INTEGER NOT NULL,
                    action_completed_at TEXT
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

    @staticmethod
    def _decode(row) -> DispatchIntent:
        fleet_id, mission_id, ships_json, baseline_json, baseline_at, prepared_at, reserved, action_at = row
        try:
            ships = json.loads(ships_json)
            baseline = json.loads(baseline_json)
            if not isinstance(ships, list) or not isinstance(baseline, list):
                raise ValueError("persisted JSON fields must be arrays")
            return DispatchIntent(
                fleet_id=fleet_id,
                mission_id=mission_id,
                ship_ids=tuple(ships),
                baseline_mission=tuple(baseline),
                baseline_observed_at=datetime.fromisoformat(baseline_at),
                prepared_at=datetime.fromisoformat(prepared_at),
                reserved=reserved,
                action_completed_at=datetime.fromisoformat(action_at) if action_at else None,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DispatchIntentError(f"persisted dispatch intent for fleet {fleet_id} is invalid") from exc

    def save(self, intent: DispatchIntent) -> None:
        if not isinstance(intent, DispatchIntent):
            raise TypeError("intent must be a DispatchIntent")
        with self._connect() as db:
            try:
                db.execute(
                    """INSERT INTO dispatch_intents (
                        fleet_id, mission_id, ship_ids_json, baseline_mission_json,
                        baseline_observed_at, prepared_at, reserved, action_completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        intent.fleet_id, intent.mission_id,
                        json.dumps(intent.ship_ids, separators=(",", ":")),
                        json.dumps(intent.baseline_mission, separators=(",", ":")),
                        intent.baseline_observed_at.isoformat(), intent.prepared_at.isoformat(),
                        intent.reserved,
                        intent.action_completed_at.isoformat() if intent.action_completed_at else None,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DispatchIntentError(f"fleet {intent.fleet_id} already has a pending dispatch intent") from exc

    def get(self, fleet_id: int) -> DispatchIntent | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT fleet_id, mission_id, ship_ids_json, baseline_mission_json,
                          baseline_observed_at, prepared_at, reserved, action_completed_at
                   FROM dispatch_intents WHERE fleet_id = ?""",
                (fleet_id,),
            ).fetchone()
        return self._decode(row) if row else None

    def load_all(self) -> tuple[DispatchIntent, ...]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT fleet_id, mission_id, ship_ids_json, baseline_mission_json,
                          baseline_observed_at, prepared_at, reserved, action_completed_at
                   FROM dispatch_intents ORDER BY fleet_id"""
            ).fetchall()
        return tuple(self._decode(row) for row in rows)

    def mark_action_completed(self, fleet_id: int, completed_at: datetime) -> DispatchIntent:
        if completed_at.tzinfo is None or completed_at.utcoffset() is None:
            raise DispatchIntentError("UI action completion timestamp must be timezone-aware")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT fleet_id, mission_id, ship_ids_json, baseline_mission_json,
                          baseline_observed_at, prepared_at, reserved, action_completed_at
                   FROM dispatch_intents WHERE fleet_id = ?""",
                (fleet_id,),
            ).fetchone()
            if row is None:
                raise DispatchIntentError(f"fleet {fleet_id} has no pending dispatch intent")
            intent = self._decode(row)
            if completed_at < intent.prepared_at:
                raise DispatchIntentError("UI action completion predates dispatch preparation")
            if intent.action_completed_at is not None:
                raise DispatchIntentError("UI action completion was already recorded")
            db.execute(
                "UPDATE dispatch_intents SET action_completed_at = ? WHERE fleet_id = ? AND action_completed_at IS NULL",
                (completed_at.isoformat(), fleet_id),
            )
            if db.execute("SELECT changes()").fetchone()[0] != 1:
                raise DispatchIntentError("UI action completion was concurrently updated")
        return DispatchIntent(
            intent.fleet_id, intent.mission_id, intent.ship_ids, intent.baseline_mission,
            intent.baseline_observed_at, intent.prepared_at, intent.reserved, completed_at,
        )

    def delete(self, fleet_id: int) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM dispatch_intents WHERE fleet_id = ?", (fleet_id,))


class DurableDispatchCoordinator:
    """Persist intent before UI input; reconcile only from fresh positive Poi evidence."""

    def __init__(
        self,
        scheduler: ContinuousExpeditionScheduler,
        intent_store: SqliteDispatchIntentStore,
    ) -> None:
        self.scheduler = scheduler
        self.intent_store = intent_store

    def prepare_dispatch(
        self,
        fleet_id: int,
        mission_id: int,
        baseline: ObservedState[tuple[ExpeditionFleetSnapshot, ...]],
        *,
        now: datetime,
        max_age: timedelta = timedelta(seconds=30),
        reserved: int = 0,
    ) -> DispatchIntent:
        state = self.scheduler.fleet(fleet_id)
        if state.phase is not FleetPhase.READY:
            raise DispatchIntentError("dispatch preparation requires a READY fleet")
        if self.intent_store.get(fleet_id) is not None:
            self.scheduler.block(
                fleet_id, "an earlier dispatch intent is still pending; recovery is required before retry"
            )
            raise DispatchIntentError("a pending dispatch intent forbids a new dispatch attempt")
        try:
            baseline.require_fresh(now=now, max_age=max_age)
        except ValueError as exc:
            raise DispatchIntentError(f"dispatch baseline is not trusted: {exc}") from exc
        matches = [item for item in baseline.payload if item.fleet_id == fleet_id]
        if len(matches) != 1:
            raise DispatchIntentError("dispatch baseline must contain exactly one target fleet")
        observed = matches[0]
        if (observed.mission_status != 0 or observed.mission_id is not None
                or observed.return_at is not None or observed.reserved != 0):
            raise DispatchIntentError("dispatch baseline must prove an idle fleet")
        if not baseline.source.startswith("poi-redux:"):
            raise DispatchIntentError("dispatch baseline must come from a Poi Redux observation")
        if not observed.ship_ids:
            raise DispatchIntentError("empty fleet cannot be dispatched")
        intent = DispatchIntent(
            fleet_id=fleet_id,
            mission_id=mission_id,
            ship_ids=observed.ship_ids,
            baseline_mission=(0, 0, 0, 0),
            baseline_observed_at=baseline.observed_at,
            prepared_at=now,
            reserved=reserved,
        )
        self.intent_store.save(intent)
        try:
            self.scheduler.begin_dispatch(fleet_id)
        except Exception:
            try:
                self.scheduler.block(fleet_id, "dispatch intent persisted but scheduler transition failed")
            except Exception:
                pass
            raise
        return intent

    def mark_ui_action_completed(self, fleet_id: int, *, completed_at: datetime) -> DispatchIntent:
        return self.intent_store.mark_action_completed(fleet_id, completed_at)

    def confirm_dispatch(self, fleet_id: int, mission: MissionTuple) -> FleetRuntime:
        intent = self.intent_store.get(fleet_id)
        if intent is None:
            raise DispatchIntentError("dispatch confirmation requires a persisted intent")
        if (mission.mission_status != 1 or mission.mission_id != intent.mission_id
                or mission.ship_ids != intent.ship_ids or mission.reserved != intent.reserved
                or datetime.fromtimestamp(mission.final_time_ms / 1000, tz=timezone.utc) <= intent.prepared_at):
            raise DispatchIntentError("observed active mission does not match the persisted dispatch intent")
        state = self.scheduler.confirm_dispatch(fleet_id, mission)
        self.intent_store.delete(fleet_id)
        return state

    def reconcile_after_restart(
        self,
        fleet_id: int,
        observation: ObservedState[tuple[ExpeditionFleetSnapshot, ...]],
        *,
        now: datetime,
        max_age: timedelta = timedelta(seconds=30),
    ) -> FleetRuntime:
        state = self.scheduler.fleet(fleet_id)
        intent = self.intent_store.get(fleet_id)
        if state.phase is FleetPhase.EXPEDITION_ACTIVE and intent is not None:
            mission = state.mission
            if (mission is not None and mission.mission_id == intent.mission_id
                    and mission.ship_ids == intent.ship_ids and mission.reserved == intent.reserved
                    and mission.final_time_ms > int(intent.prepared_at.timestamp() * 1000)):
                self.intent_store.delete(fleet_id)
                return state
            return self.scheduler.block(fleet_id, "persisted active mission conflicts with pending dispatch intent")
        if state.phase is not FleetPhase.DISPATCHING or intent is None:
            raise DispatchIntentError("restart reconciliation requires DISPATCHING and a persisted intent")
        try:
            observation.require_fresh(now=now, max_age=max_age)
        except ValueError as exc:
            raise DispatchIntentError(f"restart observation is not trusted: {exc}") from exc
        if not observation.source.startswith("poi-redux:"):
            raise DispatchIntentError("restart reconciliation requires a Poi Redux observation")
        if observation.observed_at <= intent.prepared_at:
            raise DispatchIntentError("restart observation predates the persisted dispatch intent")
        matches = [item for item in observation.payload if item.fleet_id == fleet_id]
        if len(matches) != 1:
            raise DispatchIntentError("restart observation must contain exactly one target fleet")
        observed = matches[0]
        if (observed.mission_status == 1 and observed.mission_id == intent.mission_id
                and observed.ship_ids == intent.ship_ids and observed.return_at is not None
                and observed.reserved == intent.reserved
                and int(observed.return_at.timestamp() * 1000) > int(intent.prepared_at.timestamp() * 1000)):
            mission = MissionTuple(
                1, intent.mission_id, int(observed.return_at.timestamp() * 1000),
                intent.reserved, intent.ship_ids,
            )
            state = self.scheduler.confirm_dispatch(fleet_id, mission)
            self.intent_store.delete(fleet_id)
            return state
        if (observed.mission_status == 2 and observed.mission_id == intent.mission_id
                and observed.ship_ids == intent.ship_ids and observed.return_at is not None
                and observed.reserved == intent.reserved
                and int(observed.return_at.timestamp() * 1000) > int(intent.prepared_at.timestamp() * 1000)):
            reason = "mission already returned during interruption; manual UI settlement is required"
        elif observed.mission_status == 0:
            reason = "fresh Poi state remains idle after interrupted dispatch; automatic retry is forbidden"
        else:
            reason = "fresh Poi state does not match the persisted dispatch intent; manual review required"
        return self.scheduler.block(fleet_id, reason)

    def recover_pending_intents(self) -> tuple[int, ...]:
        """Repair crash windows around scheduler-state persistence without dispatching."""
        unresolved: list[int] = []
        for intent in self.intent_store.load_all():
            state = self.scheduler.fleet(intent.fleet_id)
            if state.phase is FleetPhase.EXPEDITION_ACTIVE and state.mission is not None:
                mission = state.mission
                if (mission.mission_id == intent.mission_id and mission.ship_ids == intent.ship_ids
                        and mission.reserved == intent.reserved
                        and mission.final_time_ms > int(intent.prepared_at.timestamp() * 1000)):
                    self.intent_store.delete(intent.fleet_id)
                    continue
            if state.phase not in (FleetPhase.DISPATCHING, FleetPhase.BLOCKED):
                self.scheduler.block(
                    intent.fleet_id,
                    "orphaned dispatch intent found outside DISPATCHING; automatic retry is forbidden",
                )
            unresolved.append(intent.fleet_id)
        return tuple(unresolved)
