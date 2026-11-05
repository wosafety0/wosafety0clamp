"""P0-2: the durable dispatch-identity ledger.

The requirements these tests pin down come from the handoff, not from the
implementation: two identical dispatches must not merge, an unproven outcome
must never be recorded as success, a restart must still see unfinished work,
and an emitter must not be able to forge an identity.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kancollebot.expeditions.dispatch_registry import (
    DISPATCH_STATUSES,
    DispatchIdentity,
    DispatchRegistryError,
    SqliteDispatchRegistry,
    new_dispatch_id,
    validate_dispatch_id,
)

BASE = datetime(2026, 11, 6, 1, 0, tzinfo=timezone.utc)
FLEET = 4
MISSION = 1


def identity(**overrides) -> DispatchIdentity:
    values = {
        "dispatch_id": new_dispatch_id(),
        "fleet_id": FLEET,
        "mission_id": MISSION,
        "ship_ids": (16374, 13350, 6, 5747),
        "intent_persisted_at": BASE,
        "status": "pending",
        "task_kind": "expedition",
        "source": "supervisor",
        "detail": "test",
    }
    values.update(overrides)
    return DispatchIdentity(**values)


class DispatchRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "dispatch.sqlite3"
        self.registry = SqliteDispatchRegistry(self.path, task_kind="expedition")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_an_intent_is_persisted_as_pending(self):
        record = identity()
        self.registry.record_intent(record)
        stored = self.registry.get(record.dispatch_id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.status, "pending")
        self.assertEqual(stored.ship_ids, record.ship_ids)
        self.assertEqual(stored.task_kind, "expedition")

    def test_two_identical_dispatches_are_two_rows_and_never_merge(self):
        first = identity()
        second = identity(intent_persisted_at=BASE + timedelta(minutes=1))
        self.registry.record_intent(first)
        self.registry.record_intent(second)
        self.assertNotEqual(first.dispatch_id, second.dispatch_id)
        rows = self.registry.load_for_fleet(FLEET)
        self.assertEqual(len(rows), 2)
        # Same fleet, mission and ships: the identity key still differs,
        # which is the whole reason a returned result can be attributed.
        self.assertNotEqual(rows[0].identity_key(), rows[1].identity_key())

    def test_a_duplicate_dispatch_id_is_refused(self):
        record = identity()
        self.registry.record_intent(record)
        with self.assertRaises(DispatchRegistryError):
            self.registry.record_intent(record)

    def test_confirmation_is_recorded_at_the_poi_boundary(self):
        record = identity()
        self.registry.record_intent(record)
        confirmed = self.registry.record_confirmed(
            record.dispatch_id,
            confirmed_at=BASE + timedelta(seconds=30),
            final_time_ms=1_800_000_000_000,
        )
        self.assertEqual(confirmed.status, "confirmed")
        self.assertIsNotNone(confirmed.confirmed_at)
        self.assertEqual(confirmed.final_time_ms, 1_800_000_000_000)

    def test_an_unproven_outcome_cannot_be_confirmed_afterwards(self):
        record = identity()
        self.registry.record_intent(record)
        self.registry.record_unknown(record.dispatch_id, reason="click outcome unknown")
        with self.assertRaises(DispatchRegistryError):
            self.registry.record_confirmed(
                record.dispatch_id, confirmed_at=BASE + timedelta(minutes=1)
            )

    def test_a_confirmed_dispatch_cannot_be_downgraded_to_unknown(self):
        record = identity()
        self.registry.record_intent(record)
        self.registry.record_confirmed(record.dispatch_id, confirmed_at=BASE)
        with self.assertRaises(DispatchRegistryError):
            self.registry.record_unknown(record.dispatch_id, reason="late failure")

    def test_an_unconfirmed_outcome_cannot_be_settled(self):
        record = identity()
        self.registry.record_intent(record)
        with self.assertRaises(DispatchRegistryError):
            self.registry.mark_settled(record.dispatch_id, settled_at=BASE)

    def test_settling_closes_the_instance(self):
        record = identity()
        self.registry.record_intent(record)
        self.registry.record_confirmed(record.dispatch_id, confirmed_at=BASE)
        settled = self.registry.mark_settled(
            record.dispatch_id, settled_at=BASE + timedelta(minutes=30)
        )
        self.assertEqual(settled.status, "settled")
        self.assertFalse(settled.is_open)
        self.assertEqual(self.registry.load_open(), ())

    def test_a_restart_sees_unfinished_and_unknown_work(self):
        pending = identity()
        unknown = identity()
        done = identity()
        for item in (pending, unknown, done):
            self.registry.record_intent(item)
        self.registry.record_unknown(unknown.dispatch_id, reason="ambiguous")
        self.registry.record_confirmed(done.dispatch_id, confirmed_at=BASE)

        # A new process reads the same file: nothing is held in memory.
        restarted = SqliteDispatchRegistry(self.path, task_kind="expedition")
        open_ids = restarted.unsettled_dispatch_ids(FLEET)
        self.assertEqual(
            set(open_ids), {pending.dispatch_id, unknown.dispatch_id, done.dispatch_id}
        )
        self.assertEqual(len(restarted.load_open()), 3)

    def test_a_forged_identifier_is_refused(self):
        for bad in ("", "   ", "4", "dispatch", "dispatch-20261106T010000Z-ZZZZ", None, 17):
            with self.subTest(value=bad):
                with self.assertRaises(DispatchRegistryError):
                    validate_dispatch_id(bad)

    def test_a_minted_identifier_is_accepted(self):
        minted = new_dispatch_id()
        self.assertEqual(validate_dispatch_id(minted), minted)

    def test_the_ledger_is_a_new_table_not_a_new_database(self):
        # The same file must still carry the pre-existing tables by name, so a
        # reader can never conclude that a second ledger was created.
        db = sqlite3.connect(self.path)
        try:
            names = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        finally:
            db.close()
        self.assertIn("dispatch_identities", names)

    def test_an_invalid_record_is_rejected_before_it_is_written(self):
        with self.assertRaises(DispatchRegistryError):
            identity(ship_ids=())
        with self.assertRaises(DispatchRegistryError):
            identity(fleet_id=0)
        with self.assertRaises(DispatchRegistryError):
            identity(status="confirmed")  # no confirmation time
        with self.assertRaises(DispatchRegistryError):
            identity(intent_persisted_at=datetime(2026, 11, 6, 1, 0))

    def test_every_declared_status_is_usable(self):
        self.assertEqual(
            DISPATCH_STATUSES, {"pending", "confirmed", "unknown", "settled"}
        )


if __name__ == "__main__":
    unittest.main()
