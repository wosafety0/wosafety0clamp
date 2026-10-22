"""Durable action journal for at-most-once expedition dispatch attempts.

The journal records intent and outcome locally. It does not send game actions.
Ambiguous or unfinished records remain blocked until a fresh observation
explicitly reconciles them.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class JournalError(RuntimeError):
    """The action journal could not safely record or transition an action."""


class ExpeditionActionJournal:
    STATES = {"reserved", "submitted", "confirmed", "unknown", "rejected"}

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS expedition_actions (
                    action_key TEXT PRIMARY KEY,
                    fleet_id INTEGER NOT NULL,
                    mission_id INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            # One fleet observation can authorize at most one dispatch, even
            # when a later attempt selects a different mission ID.
            # A conflicting legacy database fails closed during initialization.
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "idx_expedition_fleet_observation "
                "ON expedition_actions (fleet_id, observed_at)"
            )

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        db.execute("PRAGMA busy_timeout = 5000")
        return db

    @staticmethod
    def make_key(fleet_id: int, mission_id: int, observed_at: datetime) -> str:
        if fleet_id < 1 or mission_id < 1:
            raise ValueError("fleet_id and mission_id must be positive")
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return f"{fleet_id}:{mission_id}:{observed_at.astimezone(timezone.utc).isoformat()}"

    def reserve(self, fleet_id: int, mission_id: int, observed_at: datetime) -> bool:
        """Atomically reserve an observation; returns False for any prior record."""
        key = self.make_key(fleet_id, mission_id, observed_at)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                """INSERT OR IGNORE INTO expedition_actions
                   (action_key, fleet_id, mission_id, observed_at, state, detail, updated_at)
                   VALUES (?, ?, ?, ?, 'reserved', 'Action reserved before dispatch', ?)""",
                (key, fleet_id, mission_id, observed_at.astimezone(timezone.utc).isoformat(), now),
            )
            inserted = cursor.rowcount == 1
            db.execute("COMMIT")
            return inserted

    def transition(self, fleet_id: int, mission_id: int, observed_at: datetime,
                   *, state: str, detail: str) -> None:
        if state not in self.STATES:
            raise ValueError("unsupported journal state")
        if not detail.strip():
            raise ValueError("detail must not be empty")
        key = self.make_key(fleet_id, mission_id, observed_at)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE expedition_actions SET state = ?, detail = ?, updated_at = ?
                   WHERE action_key = ?""",
                (state, detail, now, key),
            )
            if cursor.rowcount != 1:
                raise JournalError("cannot transition an action that was not reserved")

    def get(self, fleet_id: int, mission_id: int, observed_at: datetime):
        key = self.make_key(fleet_id, mission_id, observed_at)
        with self._connect() as db:
            row = db.execute(
                "SELECT state, detail, updated_at FROM expedition_actions WHERE action_key = ?",
                (key,),
            ).fetchone()
        return None if row is None else {"state": row[0], "detail": row[1], "updated_at": row[2]}
