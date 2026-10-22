"""Durable duplicate-dispatch safety tests; no game client is used."""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kancollebot.expeditions.action_journal import ExpeditionActionJournal, JournalError


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
            journal.transition(2, 1, NOW, state="submitted", detail="sent")
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

    def test_confirmed_cannot_revert_to_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = ExpeditionActionJournal(Path(directory) / "actions.db")
            journal.reserve(2, 1, NOW)
            journal.transition(2, 1, NOW, state="submitted", detail="sent")
            journal.transition(2, 1, NOW, state="confirmed", detail="observed")
            with self.assertRaises(JournalError):
                journal.transition(2, 1, NOW, state="unknown", detail="late timeout")
            self.assertEqual(journal.get(2, 1, NOW)["state"], "confirmed")

    def test_unknown_requires_explicit_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = ExpeditionActionJournal(Path(directory) / "actions.db")
            journal.reserve(2, 1, NOW)
            journal.transition(2, 1, NOW, state="unknown", detail="timeout")
            with self.assertRaises(JournalError):
                journal.transition(2, 1, NOW, state="submitted", detail="retry")
            journal.transition(2, 1, NOW, state="confirmed", detail="verified")
            self.assertEqual(journal.get(2, 1, NOW)["state"], "confirmed")

    def test_rejected_cannot_be_reopened(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = ExpeditionActionJournal(Path(directory) / "actions.db")
            journal.reserve(2, 1, NOW)
            journal.transition(2, 1, NOW, state="rejected", detail="verified rejection")
            with self.assertRaises(JournalError):
                journal.transition(2, 1, NOW, state="submitted", detail="retry")
            self.assertEqual(journal.get(2, 1, NOW)["state"], "rejected")

    def test_invalid_transition_releases_database_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = ExpeditionActionJournal(Path(directory) / "actions.db")
            journal.reserve(2, 1, NOW)
            with self.assertRaises(JournalError):
                journal.transition(2, 1, NOW, state="confirmed", detail="skip submitted")
            journal.transition(2, 1, NOW, state="submitted", detail="sent")
            self.assertEqual(journal.get(2, 1, NOW)["state"], "submitted")

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

    def test_unresolved_actions_are_inspectable_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "actions.db"
            journal = ExpeditionActionJournal(path)
            journal.reserve(2, 1, NOW)
            journal.transition(2, 1, NOW, state="unknown", detail="timeout")
            journal.reserve(3, 4, NOW)
            reopened = ExpeditionActionJournal(path)
            unresolved = reopened.list_unresolved()
            self.assertEqual(len(unresolved), 2)
            self.assertEqual({item["state"] for item in unresolved}, {"unknown", "reserved"})
            self.assertEqual(len(reopened.list_unresolved(fleet_id=2)), 1)

    def test_resolution_requires_explicit_operator_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = ExpeditionActionJournal(Path(directory) / "actions.db")
            journal.reserve(2, 1, NOW)
            journal.transition(2, 1, NOW, state="unknown", detail="timeout")
            with self.assertRaises(JournalError):
                journal.resolve_unresolved(
                    2, 1, NOW, outcome="confirmed", evidence="manual check"
                )
            self.assertEqual(journal.get(2, 1, NOW)["state"], "unknown")

    def test_resolution_requires_nonempty_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = ExpeditionActionJournal(Path(directory) / "actions.db")
            journal.reserve(2, 1, NOW)
            journal.transition(2, 1, NOW, state="unknown", detail="timeout")
            with self.assertRaises(ValueError):
                journal.resolve_unresolved(
                    2, 1, NOW, outcome="confirmed", evidence="  ",
                    operator_confirmed=True,
                )
            self.assertEqual(journal.get(2, 1, NOW)["state"], "unknown")

    def test_operator_can_resolve_unknown_with_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = ExpeditionActionJournal(Path(directory) / "actions.db")
            journal.reserve(2, 1, NOW)
            journal.transition(2, 1, NOW, state="unknown", detail="timeout")
            journal.resolve_unresolved(
                2, 1, NOW, outcome="confirmed",
                evidence="Manually verified in current POI state at 12:05 UTC",
                operator_confirmed=True,
            )
            record = journal.get(2, 1, NOW)
            self.assertEqual(record["state"], "confirmed")
            self.assertIn("Operator reconciliation:", record["detail"])
            self.assertTrue(journal.reserve(2, 2, NOW + timedelta(minutes=6)))

    def test_reserved_action_cannot_be_confirmed_by_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = ExpeditionActionJournal(Path(directory) / "actions.db")
            journal.reserve(2, 1, NOW)
            with self.assertRaises(JournalError):
                journal.resolve_unresolved(
                    2, 1, NOW, outcome="confirmed", evidence="mission visible",
                    operator_confirmed=True,
                )
            self.assertEqual(journal.get(2, 1, NOW)["state"], "reserved")

    def test_reserved_action_requires_operator_verified_not_sent_to_reject(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = ExpeditionActionJournal(Path(directory) / "actions.db")
            journal.reserve(2, 1, NOW)
            journal.resolve_unresolved(
                2, 1, NOW, outcome="rejected",
                evidence="Verified dispatch was never submitted after process restart",
                operator_confirmed=True,
            )
            self.assertEqual(journal.get(2, 1, NOW)["state"], "rejected")
            self.assertTrue(journal.reserve(2, 2, NOW + timedelta(minutes=1)))

    def test_unresolved_listing_rejects_invalid_fleet_id(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = ExpeditionActionJournal(Path(directory) / "actions.db")
            with self.assertRaises(ValueError):
                journal.list_unresolved(fleet_id=0)


if __name__ == "__main__":
    unittest.main()
