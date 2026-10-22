"""Fail-closed journal state machine tests (offline only)."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from kancollebot.expeditions.action_journal import ExpeditionActionJournal, JournalError


NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)


class JournalTransitionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.journal = ExpeditionActionJournal(Path(self.directory.name) / "actions.db")
        self.assertTrue(self.journal.reserve(2, 1, NOW))

    def transition(self, state):
        self.journal.transition(2, 1, NOW, state=state, detail="test")

    def test_normal_submission_and_confirmation(self):
        self.transition("submitted")
        self.transition("confirmed")
        self.assertEqual(self.journal.get(2, 1, NOW)["state"], "confirmed")

    def test_reserved_cannot_skip_submission_to_confirmation(self):
        with self.assertRaises(JournalError):
            self.transition("confirmed")
        self.assertEqual(self.journal.get(2, 1, NOW)["state"], "reserved")

    def test_unknown_cannot_become_confirmed(self):
        self.transition("unknown")
        with self.assertRaises(JournalError):
            self.transition("confirmed")
        self.assertEqual(self.journal.get(2, 1, NOW)["state"], "unknown")

    def test_confirmed_cannot_be_reopened(self):
        self.transition("submitted")
        self.transition("confirmed")
        with self.assertRaises(JournalError):
            self.transition("submitted")
        self.assertEqual(self.journal.get(2, 1, NOW)["state"], "confirmed")

    def test_duplicate_transition_is_rejected(self):
        self.transition("submitted")
        with self.assertRaises(JournalError):
            self.transition("submitted")

    def test_reserved_adapter_error_becomes_unknown(self):
        self.transition("unknown")
        self.assertEqual(self.journal.get(2, 1, NOW)["state"], "unknown")

    def test_submitted_adapter_error_becomes_unknown(self):
        self.transition("submitted")
        self.transition("unknown")
        self.assertEqual(self.journal.get(2, 1, NOW)["state"], "unknown")


if __name__ == "__main__":
    unittest.main()
