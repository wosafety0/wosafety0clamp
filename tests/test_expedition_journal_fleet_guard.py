"""Durable duplicate-dispatch safety tests; no game client is used."""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kancollebot.expeditions.action_journal import ExpeditionActionJournal


NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


class ExpeditionJournalFleetGuardTests(unittest.TestCase):
    def test_different_mission_same_observation_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = ExpeditionActionJournal(Path(directory) / "actions.db")
            self.assertTrue(journal.reserve(2, 1, NOW))
            self.assertFalse(journal.reserve(2, 2, NOW))
            self.assertIsNone(journal.get(2, 2, NOW))

    def test_guard_survives_reopening_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "actions.db"
            self.assertTrue(ExpeditionActionJournal(path).reserve(2, 1, NOW))
            self.assertFalse(ExpeditionActionJournal(path).reserve(2, 2, NOW))

    def test_new_observation_can_be_reserved(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = ExpeditionActionJournal(Path(directory) / "actions.db")
            self.assertTrue(journal.reserve(2, 1, NOW))
            journal.transition(2, 1, NOW, state="confirmed", detail="observed")
            self.assertTrue(journal.reserve(2, 2, NOW + timedelta(seconds=1)))

    def test_unresolved_dispatch_blocks_newer_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = ExpeditionActionJournal(Path(directory) / "actions.db")
            self.assertTrue(journal.reserve(2, 1, NOW))
            self.assertFalse(journal.reserve(2, 2, NOW + timedelta(seconds=1)))

    def test_unknown_dispatch_blocks_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "actions.db"
            journal = ExpeditionActionJournal(path)
            self.assertTrue(journal.reserve(2, 1, NOW))
            journal.transition(2, 1, NOW, state="unknown", detail="timeout")
            reopened = ExpeditionActionJournal(path)
            self.assertFalse(reopened.reserve(2, 2, NOW + timedelta(minutes=1)))

    def test_submitted_dispatch_blocks_newer_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = ExpeditionActionJournal(Path(directory) / "actions.db")
            self.assertTrue(journal.reserve(2, 1, NOW))
            journal.transition(2, 1, NOW, state="submitted", detail="awaiting state")
            self.assertFalse(journal.reserve(2, 2, NOW + timedelta(minutes=1)))

    def test_different_fleet_can_be_reserved(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = ExpeditionActionJournal(Path(directory) / "actions.db")
            self.assertTrue(journal.reserve(2, 1, NOW))
            self.assertTrue(journal.reserve(3, 1, NOW))

    def test_conflicting_legacy_records_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            with sqlite3.connect(path) as db:
                db.execute("""CREATE TABLE expedition_actions (
                    action_key TEXT PRIMARY KEY,
                    fleet_id INTEGER NOT NULL,
                    mission_id INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )""")
                for mission in (1, 2):
                    db.execute(
                        "INSERT INTO expedition_actions VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (str(mission), 2, mission, NOW.isoformat(),
                         "unknown", "legacy", NOW.isoformat()),
                    )
            with self.assertRaises(sqlite3.IntegrityError):
                ExpeditionActionJournal(path)


if __name__ == "__main__":
    unittest.main()
