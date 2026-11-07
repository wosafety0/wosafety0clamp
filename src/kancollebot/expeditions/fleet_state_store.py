"""SQLite persistence for the continuous expedition scheduler's fleet states."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .continuous_scheduler import FleetPhase, FleetRuntime, MissionTuple


class FleetStateStoreError(RuntimeError):
    """Persisted fleet state is unavailable, corrupt, or inconsistent."""


class SqliteFleetStateStore:
    """Durably save each lifecycle transition before exposing it in memory."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS fleet_runtime (
                    fleet_id INTEGER PRIMARY KEY,
                    phase TEXT NOT NULL,
                    mission_status INTEGER,
                    mission_id INTEGER,
                    final_time_ms INTEGER,
                    reserved INTEGER,
                    ship_ids_json TEXT,
                    home_port_verified INTEGER NOT NULL,
                    blocked_reason TEXT,
                    updated_at TEXT NOT NULL
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

    def load_all(self) -> tuple[FleetRuntime, ...]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT fleet_id, phase, mission_status, mission_id, final_time_ms,
                          reserved, ship_ids_json, home_port_verified, blocked_reason
                   FROM fleet_runtime ORDER BY fleet_id"""
            ).fetchall()
        states = []
        for row in rows:
            fleet_id, phase_value, status, mission_id, final_time, reserved, ships_json, home_verified, blocked = row
            try:
                phase = FleetPhase(phase_value)
                if home_verified not in (0, 1):
                    raise ValueError("invalid home_port_verified flag")
                ship_ids = None
                if ships_json is not None:
                    parsed = json.loads(ships_json)
                    if not isinstance(parsed, list):
                        raise ValueError("persisted ship membership must be a JSON array")
                    ship_ids = tuple(parsed)
                mission = None
                resupply_ship_ids = None
                if mission_id is not None:
                    if status is None or final_time is None or reserved is None or ship_ids is None:
                        raise ValueError("incomplete persisted mission tuple")
                    mission = MissionTuple(
                        mission_status=status,
                        mission_id=mission_id,
                        final_time_ms=final_time,
                        reserved=reserved,
                        ship_ids=ship_ids,
                    )
                else:
                    if status is not None or final_time is not None or reserved is not None:
                        raise ValueError("mission fields exist without a mission ID")
                    # ``ship_ids_json`` may outlive the mission tuple: it is the
                    # composition kept through RESUPPLY_REQUIRED until resupply
                    # is verified.
                    resupply_ship_ids = ship_ids
                if phase is FleetPhase.EXPEDITION_ACTIVE and mission is None:
                    raise ValueError("active fleet has no persisted mission tuple")
                if home_verified and phase is not FleetPhase.EXPEDITION_ACTIVE:
                    raise ValueError("Home Port verification flag is invalid for this phase")
                if phase is FleetPhase.BLOCKED and not blocked:
                    raise ValueError("blocked fleet has no stop reason")
                states.append(
                    FleetRuntime(
                        fleet_id=fleet_id,
                        phase=phase,
                        mission=mission,
                        home_port_verified=bool(home_verified),
                        blocked_reason=blocked,
                        resupply_ship_ids=resupply_ship_ids,
                    )
                )
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise FleetStateStoreError(
                    f"persisted state for fleet {fleet_id} is invalid"
                ) from exc
        return tuple(states)

    def save(self, state: FleetRuntime) -> None:
        if not isinstance(state, FleetRuntime):
            raise TypeError("state must be a FleetRuntime")
        mission = state.mission
        ship_ids = mission.ship_ids if mission is not None else state.resupply_ship_ids
        values = (
            state.fleet_id,
            state.phase.value,
            None if mission is None else mission.mission_status,
            None if mission is None else mission.mission_id,
            None if mission is None else mission.final_time_ms,
            None if mission is None else mission.reserved,
            None if ship_ids is None else json.dumps(ship_ids, separators=(",", ":")),
            int(state.home_port_verified),
            state.blocked_reason,
            datetime.now(timezone.utc).isoformat(),
        )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT INTO fleet_runtime (
                       fleet_id, phase, mission_status, mission_id, final_time_ms,
                       reserved, ship_ids_json, home_port_verified, blocked_reason, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(fleet_id) DO UPDATE SET
                       phase=excluded.phase,
                       mission_status=excluded.mission_status,
                       mission_id=excluded.mission_id,
                       final_time_ms=excluded.final_time_ms,
                       reserved=excluded.reserved,
                       ship_ids_json=excluded.ship_ids_json,
                       home_port_verified=excluded.home_port_verified,
                       blocked_reason=excluded.blocked_reason,
                       updated_at=excluded.updated_at""",
                values,
            )
            db.commit()
