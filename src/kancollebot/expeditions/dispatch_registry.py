"""Append-only ledger of dispatch *identities*, keyed by ``dispatch_id``.

Why this exists, and what it is deliberately not
------------------------------------------------
The project already has three different records around a dispatch, and they
answer three different questions. Confusing them is what left the real Fleet 4
dispatch un-settleable:

* ``ExpeditionActionJournal`` (``expedition_actions``) is the **at-most-once
  safety** ledger. It answers "may this fleet be clicked again?" and blocks on
  ``reserved`` / ``submitted`` / ``unknown``. It has no ship set, no mission
  time and no dispatch identity, so it cannot attribute a result.
* ``SqliteDispatchEventLog`` (``dispatch_events``) is the **diagnostic event
  stream**. It is append-only and complete, but it is JSON payloads in a
  shared log; rebuilding identity from it works only when both the
  confirmation row *and* the later Home Port row are present
  (``blocked_recovery.read_dispatch_evidence``), which is exactly the pair a
  hand-run dispatch never produces.
* ``SqliteDispatchIntentStore`` (``dispatch_intents``) is the **pending
  intent**, one row per fleet, deleted the moment Poi confirms the mission.
  After confirmation there is no longer any durable record of which ships were
  sent - so a returned fleet can never be attributed afterwards.

This module is the missing fourth thing: one durable row per *dispatch
instance*, created before the irreversible click and kept after confirmation
until the reward chain for it is settled. It is a **new table in the existing
dispatch database file**, not a second database and not a second safety
ledger. The Action Journal keeps its meaning and its schema; nothing here can
authorise or forbid a click.

Identity rules
--------------
``dispatch_id`` is minted by the emitter (``observability.event_envelope``)
and is never derived from the dispatch's own data. Two dispatches of the same
fleet, mission and ships therefore get two rows - they cannot be merged, which
is what makes "did this result belong to *this* attempt" answerable at all.
The registry validates the shape of an incoming id and refuses anything else,
so an event payload cannot smuggle a forged identity into the ledger.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from kancollebot.observability.event_envelope import new_dispatch_id

__all__ = [
    "DISPATCH_STATUSES",
    "DispatchIdentity",
    "DispatchRegistryError",
    "SqliteDispatchRegistry",
    "new_dispatch_id",
    "validate_dispatch_id",
]

#: ``pending``   - intent persisted, no Poi confirmation yet.
#: ``confirmed`` - Poi reported the mission active with the recorded ship set.
#: ``unknown``   - the click was sent but the outcome could not be proven.
#: ``settled``   - the reward chain for this instance has been closed.
DISPATCH_STATUSES: frozenset[str] = frozenset(
    {"pending", "confirmed", "unknown", "settled"}
)

#: Statuses that still describe a live (unfinished) dispatch instance.
OPEN_STATUSES: frozenset[str] = frozenset({"pending", "confirmed", "unknown"})

_DISPATCH_ID_RE = re.compile(r"^dispatch-\d{8}T\d{6}Z-[0-9a-f]{8,32}$")


class DispatchRegistryError(RuntimeError):
    """The dispatch ledger is unavailable, corrupt, or was asked to forge."""


def validate_dispatch_id(value: object) -> str:
    """Return ``value`` when it is a minted dispatch id, else raise.

    The check is intentionally a shape check, not a lookup: it rejects an
    empty string, a database row id, a mission name or anything else an
    emitter might try to pass off as an identity.
    """
    if not isinstance(value, str) or not value.strip():
        raise DispatchRegistryError("dispatch_id must be a non-empty string")
    candidate = value.strip()
    if not _DISPATCH_ID_RE.match(candidate):
        raise DispatchRegistryError(
            "dispatch_id is not in the minted form dispatch-<utc>-<hex>; "
            "an emitter may not invent its own identity"
        )
    return candidate


def _require_positive_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DispatchRegistryError(f"{name} must be a positive integer")
    return value


def _require_aware(name: str, value: object) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise DispatchRegistryError(f"{name} must be a timezone-aware datetime")
    return value


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(frozen=True, slots=True)
class DispatchIdentity:
    """One dispatch instance, as it was actually sent to the server.

    ``ship_ids`` is the composition recorded on the fresh baseline *before*
    the click; it is the only field that can tell two fleets running the same
    expedition apart, and the only field a returned mission can be matched on
    without looking at a screen.
    """

    dispatch_id: str
    fleet_id: int
    mission_id: int
    ship_ids: tuple[int, ...]
    intent_persisted_at: datetime
    status: str = "pending"
    quest_name: str | None = None
    task_kind: str | None = None
    source: str = "unknown"
    action_completed_at: datetime | None = None
    confirmed_at: datetime | None = None
    final_time_ms: int | None = None
    settled_at: datetime | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        validate_dispatch_id(self.dispatch_id)
        _require_positive_int("fleet_id", self.fleet_id)
        _require_positive_int("mission_id", self.mission_id)
        if not isinstance(self.ship_ids, tuple) or not self.ship_ids:
            raise DispatchRegistryError("ship_ids must be a non-empty tuple")
        if any(
            isinstance(ship_id, bool) or not isinstance(ship_id, int) or ship_id < 1
            for ship_id in self.ship_ids
        ):
            raise DispatchRegistryError("ship_ids must contain positive integers")
        if len(set(self.ship_ids)) != len(self.ship_ids):
            raise DispatchRegistryError("ship_ids must be unique")
        if self.status not in DISPATCH_STATUSES:
            raise DispatchRegistryError(f"unsupported dispatch status: {self.status!r}")
        _require_aware("intent_persisted_at", self.intent_persisted_at)
        for label, value in (
            ("action_completed_at", self.action_completed_at),
            ("confirmed_at", self.confirmed_at),
            ("settled_at", self.settled_at),
        ):
            if value is not None:
                _require_aware(label, value)
        if self.final_time_ms is not None and (
            isinstance(self.final_time_ms, bool)
            or not isinstance(self.final_time_ms, int)
        ):
            raise DispatchRegistryError("final_time_ms must be an integer")
        if self.status == "confirmed" and self.confirmed_at is None:
            raise DispatchRegistryError("a confirmed dispatch needs a confirmation time")
        if self.status == "settled" and self.settled_at is None:
            raise DispatchRegistryError("a settled dispatch needs a settlement time")

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    def identity_key(self) -> tuple[int, int, tuple[int, ...], str]:
        """What makes two rows describe the *same* dispatch instance.

        Deliberately includes the id: two attempts with the same fleet,
        mission and ships are two instances, and must never collapse into one.
        """
        return (self.fleet_id, self.mission_id, self.ship_ids, self.dispatch_id)


class SqliteDispatchRegistry:
    """Durable identity rows in the same SQLite file as the dispatch intents.

    The table is additive. Existing tables (``dispatch_intents``,
    ``dispatch_events``, ``expedition_actions``) keep their schema and their
    meaning; a database written by an older build simply gains this table on
    first use.
    """

    def __init__(self, path: str | Path, *, task_kind: str | None = None) -> None:
        self.path = str(path)
        self.task_kind = task_kind
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS dispatch_identities (
                    dispatch_id TEXT PRIMARY KEY,
                    fleet_id INTEGER NOT NULL,
                    mission_id INTEGER NOT NULL,
                    ship_ids_json TEXT NOT NULL,
                    quest_name TEXT,
                    task_kind TEXT,
                    source TEXT NOT NULL,
                    intent_persisted_at TEXT NOT NULL,
                    action_completed_at TEXT,
                    confirmed_at TEXT,
                    final_time_ms INTEGER,
                    status TEXT NOT NULL,
                    settled_at TEXT,
                    detail TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            db.execute(
                """CREATE INDEX IF NOT EXISTS idx_dispatch_identities_fleet_status
                   ON dispatch_identities (fleet_id, status)"""
            )

    @contextmanager
    def _connect(self):
        try:
            db = sqlite3.connect(self.path, timeout=5.0)
        except sqlite3.Error as exc:
            # A ledger that cannot even be opened is a refusal, not a reason to
            # fall back to screen evidence: the caller must never guess.
            raise DispatchRegistryError(
                f"dispatch ledger {self.path} is unavailable: {exc}"
            ) from exc
        db.execute("PRAGMA busy_timeout = 5000")
        try:
            yield db
            db.commit()
        except sqlite3.IntegrityError:
            # Left raw on purpose: ``record_intent`` turns a duplicate primary
            # key into its own, more specific refusal.
            db.rollback()
            raise
        except sqlite3.Error as exc:
            db.rollback()
            raise DispatchRegistryError(
                f"dispatch ledger {self.path} failed: {exc}"
            ) from exc
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # -- writes -------------------------------------------------------------

    def record_intent(self, identity: DispatchIdentity) -> None:
        """Insert one dispatch instance. Never updates an existing row."""
        if not isinstance(identity, DispatchIdentity):
            raise DispatchRegistryError("identity must be a DispatchIdentity")
        if identity.status != "pending":
            raise DispatchRegistryError("a dispatch is recorded as pending first")
        task_kind = self.task_kind if self.task_kind is not None else identity.task_kind
        try:
            with self._connect() as db:
                db.execute(
                    """INSERT INTO dispatch_identities (
                        dispatch_id, fleet_id, mission_id, ship_ids_json, quest_name,
                        task_kind, source, intent_persisted_at, action_completed_at,
                        confirmed_at, final_time_ms, status, settled_at, detail, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 'pending', NULL, ?, ?)""",
                    (
                        identity.dispatch_id,
                        identity.fleet_id,
                        identity.mission_id,
                        json.dumps(identity.ship_ids, separators=(",", ":")),
                        identity.quest_name,
                        task_kind,
                        identity.source,
                        identity.intent_persisted_at.isoformat(),
                        (
                            identity.action_completed_at.isoformat()
                            if identity.action_completed_at
                            else None
                        ),
                        identity.detail,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DispatchRegistryError(
                f"dispatch {identity.dispatch_id} is already recorded"
            ) from exc

    def record_confirmed(
        self,
        dispatch_id: str,
        *,
        confirmed_at: datetime,
        final_time_ms: int | None = None,
        quest_name: str | None = None,
        action_completed_at: datetime | None = None,
    ) -> DispatchIdentity:
        """Mark one instance as confirmed by Poi.

        Only ``pending`` may become ``confirmed``: an ``unknown`` instance is
        never silently upgraded, because "the click may or may not have
        landed" and "the server accepted it" are different facts.
        """
        key = validate_dispatch_id(dispatch_id)
        _require_aware("confirmed_at", confirmed_at)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status FROM dispatch_identities WHERE dispatch_id = ?", (key,)
            ).fetchone()
            if row is None:
                raise DispatchRegistryError(f"dispatch {key} is not recorded")
            if row[0] != "pending":
                raise DispatchRegistryError(
                    f"dispatch {key} is {row[0]!r}; only a pending dispatch may be confirmed"
                )
            db.execute(
                """UPDATE dispatch_identities SET
                       status = 'confirmed',
                       confirmed_at = ?,
                       final_time_ms = ?,
                       quest_name = COALESCE(?, quest_name),
                       action_completed_at = COALESCE(?, action_completed_at),
                       detail = 'confirmed by Poi active-mission tuple',
                       updated_at = ?
                   WHERE dispatch_id = ?""",
                (
                    confirmed_at.isoformat(),
                    final_time_ms,
                    quest_name,
                    action_completed_at.isoformat() if action_completed_at else None,
                    datetime.now(timezone.utc).isoformat(),
                    key,
                ),
            )
        record = self.get(key)
        assert record is not None
        return record

    def record_unknown(
        self,
        dispatch_id: str,
        *,
        reason: str,
        at: datetime | None = None,
    ) -> DispatchIdentity:
        """Mark an instance whose outcome could not be proven.

        ``confirmed`` is never downgraded: once Poi has stated the mission is
        active, a later UI failure is an ambiguity about the *settlement*, not
        about the dispatch. The unknown status exists for the opposite
        window - click sent, nothing proven either way.
        """
        key = validate_dispatch_id(dispatch_id)
        moment = _require_aware("at", at or datetime.now(timezone.utc))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status FROM dispatch_identities WHERE dispatch_id = ?", (key,)
            ).fetchone()
            if row is None:
                raise DispatchRegistryError(f"dispatch {key} is not recorded")
            if row[0] in ("confirmed", "settled"):
                raise DispatchRegistryError(
                    f"dispatch {key} is already {row[0]!r}; it cannot become unknown"
                )
            db.execute(
                """UPDATE dispatch_identities SET
                       status = 'unknown', detail = ?, updated_at = ?
                   WHERE dispatch_id = ?""",
                (reason.strip() or "unproven dispatch outcome", moment.isoformat(), key),
            )
        record = self.get(key)
        assert record is not None
        return record

    def mark_settled(self, dispatch_id: str, *, settled_at: datetime) -> DispatchIdentity:
        """Close one confirmed instance once its reward chain is done."""
        key = validate_dispatch_id(dispatch_id)
        moment = _require_aware("settled_at", settled_at)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status FROM dispatch_identities WHERE dispatch_id = ?", (key,)
            ).fetchone()
            if row is None:
                raise DispatchRegistryError(f"dispatch {key} is not recorded")
            if row[0] != "confirmed":
                raise DispatchRegistryError(
                    f"dispatch {key} is {row[0]!r}; only a confirmed dispatch may be settled"
                )
            db.execute(
                """UPDATE dispatch_identities SET
                       status = 'settled', settled_at = ?, updated_at = ?
                   WHERE dispatch_id = ?""",
                (moment.isoformat(), datetime.now(timezone.utc).isoformat(), key),
            )
        record = self.get(key)
        assert record is not None
        return record

    # -- reads --------------------------------------------------------------

    @staticmethod
    def _decode(row) -> DispatchIdentity:
        (
            dispatch_id,
            fleet_id,
            mission_id,
            ships_json,
            quest_name,
            task_kind,
            source,
            intent_at,
            action_at,
            confirmed_at,
            final_time_ms,
            status,
            settled_at,
            detail,
        ) = row
        try:
            ships = json.loads(ships_json)
        except json.JSONDecodeError as exc:
            raise DispatchRegistryError(
                f"persisted dispatch {dispatch_id} has an unreadable ship list"
            ) from exc
        if not isinstance(ships, list):
            raise DispatchRegistryError(
                f"persisted dispatch {dispatch_id} ship list is not an array"
            )
        return DispatchIdentity(
            dispatch_id=dispatch_id,
            fleet_id=fleet_id,
            mission_id=mission_id,
            ship_ids=tuple(ships),
            quest_name=quest_name,
            task_kind=task_kind,
            source=source,
            intent_persisted_at=_parse_timestamp(intent_at) or datetime.fromtimestamp(0, timezone.utc),
            action_completed_at=_parse_timestamp(action_at),
            confirmed_at=_parse_timestamp(confirmed_at),
            final_time_ms=final_time_ms,
            status=status,
            settled_at=_parse_timestamp(settled_at),
            detail=detail or "",
        )

    _COLUMNS = (
        "dispatch_id, fleet_id, mission_id, ship_ids_json, quest_name, task_kind, "
        "source, intent_persisted_at, action_completed_at, confirmed_at, "
        "final_time_ms, status, settled_at, detail"
    )

    def get(self, dispatch_id: str) -> DispatchIdentity | None:
        key = validate_dispatch_id(dispatch_id)
        with self._connect() as db:
            row = db.execute(
                f"SELECT {self._COLUMNS} FROM dispatch_identities WHERE dispatch_id = ?",
                (key,),
            ).fetchone()
        return self._decode(row) if row else None

    def load_open(self) -> tuple[DispatchIdentity, ...]:
        """Every instance that has not been settled, oldest first."""
        placeholders = ",".join("?" for _ in OPEN_STATUSES)
        with self._connect() as db:
            rows = db.execute(
                f"SELECT {self._COLUMNS} FROM dispatch_identities "
                f"WHERE status IN ({placeholders}) ORDER BY intent_persisted_at, dispatch_id",
                tuple(sorted(OPEN_STATUSES)),
            ).fetchall()
        return tuple(self._decode(row) for row in rows)

    def load_for_fleet(self, fleet_id: int) -> tuple[DispatchIdentity, ...]:
        _require_positive_int("fleet_id", fleet_id)
        with self._connect() as db:
            rows = db.execute(
                f"SELECT {self._COLUMNS} FROM dispatch_identities "
                "WHERE fleet_id = ? ORDER BY intent_persisted_at, dispatch_id",
                (fleet_id,),
            ).fetchall()
        return tuple(self._decode(row) for row in rows)

    def load_all(self) -> tuple[DispatchIdentity, ...]:
        with self._connect() as db:
            rows = db.execute(
                f"SELECT {self._COLUMNS} FROM dispatch_identities "
                "ORDER BY intent_persisted_at, dispatch_id"
            ).fetchall()
        return tuple(self._decode(row) for row in rows)

    def unsettled_dispatch_ids(self, fleet_id: int) -> tuple[str, ...]:
        """Open instances for one fleet, in send order.

        A non-empty answer is the reason a fresh attempt must be refused: the
        loop cannot know which of these the fleet's current screen belongs to.
        """
        return tuple(item.dispatch_id for item in self.load_for_fleet(fleet_id) if item.is_open)


def as_records(items: Iterable[DispatchIdentity]) -> tuple[DispatchIdentity, ...]:
    """Type-narrowing helper for callers that hold a mixed iterable."""
    records = []
    for item in items:
        if not isinstance(item, DispatchIdentity):
            raise DispatchRegistryError("dispatch records must be DispatchIdentity values")
        records.append(item)
    return tuple(records)
