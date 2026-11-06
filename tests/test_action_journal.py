"""Offline tests for durable expedition action reservations."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kancollebot.expeditions.action_journal import ExpeditionActionJournal, JournalError


NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


class ExpeditionActionJournalTests(unittest.TestCase):
    def make_journal(self, directory):
        return ExpeditionActionJournal(Path(directory) / "actions.sqlite")

    def test_unresolved_record_blocks_new_observation_and_mission(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self.make_journal(directory)
            self.assertTrue(journal.reserve(2, 1, NOW))
            journal.transition(2, 1, NOW, state="unknown", detail="Ambiguous dispatch result")

            self.assertFalse(journal.reserve(2, 1, NOW + timedelta(seconds=1)))
            self.assertFalse(journal.reserve(2, 2, NOW + timedelta(seconds=2)))

    def test_reserved_and_submitted_records_block_retries(self):
        for state in ("reserved", "submitted"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory:
                journal = self.make_journal(directory)
                self.assertTrue(journal.reserve(3, 4, NOW))
                if state != "reserved":
                    journal.transition(3, 4, NOW, state=state, detail="Dispatch not reconciled")
                self.assertFalse(journal.reserve(3, 4, NOW + timedelta(seconds=5)))

    def test_confirmed_record_allows_a_later_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self.make_journal(directory)
            self.assertTrue(journal.reserve(2, 1, NOW))
            journal.transition(2, 1, NOW, state="confirmed", detail="Mission observed")
            self.assertTrue(journal.reserve(2, 1, NOW + timedelta(minutes=10)))

    def test_transition_requires_a_prior_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = self.make_journal(directory)
            with self.assertRaisesRegex(JournalError, "not reserved"):
                journal.transition(2, 1, NOW, state="unknown", detail="No reservation")


if __name__ == "__main__":
    unittest.main()
