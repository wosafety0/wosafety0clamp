"""Durable action journal for at-most-once expedition dispatch attempts.

The journal records intent and outcome locally. It does not send game actions.
Ambiguous or unfinished records remain blocked until a fresh observation
explicitly reconciles them.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict


class JournalError(RuntimeError):
    """The action journal could not safely record or transition an action."""


class UnresolvedAction(TypedDict):
    fleet_id: int
    mission_id: int
    observed_at: str
    state: str
    detail: str
    updated_at: str


class ExpeditionActionJournal:
    STATES = {"reserved", "submitted", "confirmed", "unknown", "rejected"}
    UNRESOLVED_STATES = ("reserved", "submitted", "unknown")
    TRANSITIONS = {
        "reserved": {"submitted", "unknown", "rejected"},
        "submitted": {"confirmed", "unknown", "rejected"},
        "unknown": {"confirmed", "rejected"},
        "confirmed": set(),
        "rejected": set(),
    }

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        with closing(self._connect()) as db:
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
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            # A newer observation does not resolve an ambiguous dispatch.
            # Keep the check and insertion inside one write transaction.
            pending = db.execute(
                "SELECT 1 FROM expedition_actions "
                "WHERE fleet_id = ? AND state IN ('reserved', 'submitted', 'unknown') "
                "LIMIT 1",
                (fleet_id,),
            ).fetchone()
            if pending is not None:
                db.execute("ROLLBACK")
                return False
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
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state FROM expedition_actions WHERE action_key = ?",
                (key,),
            ).fetchone()
            if row is None:
                raise JournalError("cannot transition an action that was not reserved")
            if state not in self.TRANSITIONS.get(row[0], set()):
                raise JournalError("invalid journal state transition")
            db.execute(
                """UPDATE expedition_actions SET state = ?, detail = ?, updated_at = ?
                   WHERE action_key = ?""",
                (state, detail, now, key),
            )
            db.execute("COMMIT")

    def list_unresolved(self, *, fleet_id: int | None = None) -> list[UnresolvedAction]:
        """Return pending/ambiguous actions for inspection; this performs no reconciliation."""
        states = self.UNRESOLVED_STATES
        placeholders = ", ".join("?" for _ in states)
        query = (
            "SELECT fleet_id, mission_id, observed_at, state, detail, updated_at "
            f"FROM expedition_actions WHERE state IN ({placeholders})"
        )
        parameters: tuple[object, ...] = states
        if fleet_id is not None:
            if fleet_id < 1:
                raise ValueError("fleet_id must be positive")
            query += " AND fleet_id = ?"
            parameters += (fleet_id,)
        query += " ORDER BY fleet_id, observed_at"
        with closing(self._connect()) as db:
            rows = db.execute(query, parameters).fetchall()
        return [
            {
                "fleet_id": row[0],
                "mission_id": row[1],
                "observed_at": row[2],
                "state": row[3],
                "detail": row[4],
                "updated_at": row[5],
            }
            for row in rows
        ]

    def resolve_unresolved(
        self,
        fleet_id: int,
        mission_id: int,
        observed_at: datetime,
        *,
        outcome: str,
        evidence: str,
        operator_confirmed: bool = False,
    ) -> None:
        """Resolve a pending record only after an explicit, evidence-backed human check.

        Submitted/unknown records may be confirmed or rejected. A reserved record
        may only be rejected when the operator explicitly verifies that dispatch
        was never sent. Absence from a game-state observation is not evidence.
        """
        if outcome not in {"confirmed", "rejected"}:
            raise ValueError("outcome must be 'confirmed' or 'rejected'")
        if operator_confirmed is not True:
            raise JournalError("operator confirmation is required")
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("evidence must not be empty")
        key = self.make_key(fleet_id, mission_id, observed_at)
        now = datetime.now(timezone.utc).isoformat()
        detail = "Operator reconciliation: " + evidence.strip()
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state FROM expedition_actions WHERE action_key = ?",
                (key,),
            ).fetchone()
            if row is None:
                raise JournalError("cannot resolve an action that was not reserved")
            current = row[0]
            if current == "reserved":
                if outcome != "rejected":
                    raise JournalError(
                        "reserved action can only be rejected after verifying it was never sent"
                    )
            elif current not in {"submitted", "unknown"}:
                raise JournalError("action is not in a resolvable state")
            if outcome not in self.TRANSITIONS.get(current, set()):
                raise JournalError("invalid journal state transition")
            db.execute(
                """UPDATE expedition_actions SET state = ?, detail = ?, updated_at = ?
                   WHERE action_key = ?""",
                (outcome, detail, now, key),
            )
            db.execute("COMMIT")

    def get(self, fleet_id: int, mission_id: int, observed_at: datetime):
        key = self.make_key(fleet_id, mission_id, observed_at)
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT state, detail, updated_at FROM expedition_actions WHERE action_key = ?",
                (key,),
            ).fetchone()
        return None if row is None else {"state": row[0], "detail": row[1], "updated_at": row[2]}
