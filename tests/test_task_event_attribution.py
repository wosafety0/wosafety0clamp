"""P0 R2: task attribution cannot be forged, and legacy SQLite stays valid.

Covers the REAL sink and the REAL production jsonl printer, not only the pure
helpers. No schema migration is performed or required.
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from kancollebot.core.task_attribution import (  # noqa: E402
    EVENT_ENVELOPE_KEYS,
    TASK_ATTRIBUTION_KEYS,
    TRUSTED_EVENT_KEYS,
    apply_trusted_task_attribution,
    merge_trusted_event_fields,
)
from kancollebot.expeditions.continuous_dispatch_controller import (  # noqa: E402
    SqliteDispatchEventLog,
)
from kancollebot.task_scope import (  # noqa: E402
    IMPLEMENTED_TASK_KINDS,
    TaskKind,
    task_event_fields,
)

LEGACY_DDL = """CREATE TABLE IF NOT EXISTS dispatch_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    event TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )"""


class PureContractTests(unittest.TestCase):
    def test_reserved_key_sets_are_disjoint_and_match_task_event_fields(self):
        self.assertEqual(
            TASK_ATTRIBUTION_KEYS, frozenset(task_event_fields(TaskKind.EXPEDITION))
        )
        self.assertTrue(TASK_ATTRIBUTION_KEYS.isdisjoint(EVENT_ENVELOPE_KEYS))
        self.assertEqual(TRUSTED_EVENT_KEYS, TASK_ATTRIBUTION_KEYS | EVENT_ENVELOPE_KEYS)

    def test_none_task_kind_returns_payload_unchanged(self):
        original = {"fleet_id": 2, "task_kind": "caller-supplied"}
        result = apply_trusted_task_attribution(original, task_kind=None)
        self.assertEqual(result, original)
        self.assertIsNot(result, original)

    def test_trusted_attribution_strips_every_forged_attribution_key(self):
        fields = {
            "fleet_id": 2,
            "task_kind": "combat",
            "task_implemented": False,
            "task_ui_click_capable": False,
            "note": "kept",
        }
        result = apply_trusted_task_attribution(fields, task_kind="expedition")
        self.assertEqual(
            result,
            {"fleet_id": 2, "note": "kept", "task_kind": "expedition"},
        )
        self.assertEqual(fields["task_kind"], "combat", "input must not be mutated")

    def test_merge_drops_reserved_extras_and_writes_trusted_envelope(self):
        trusted = {
            "timestamp_utc": "2026-11-04T00:00:00+00:00",
            "run_id": "run-A",
            "event": "real_event",
            "task_kind": "expedition",
        }
        extra = {
            "event": "forged",
            "run_id": "forged",
            "timestamp_utc": "forged",
            "task_kind": "combat",
            "task_implemented": False,
            "task_ui_click_capable": False,
            "fleet_id": 3,
        }
        self.assertEqual(
            merge_trusted_event_fields(extra, trusted=trusted),
            {**trusted, "fleet_id": 3},
        )

    def test_merge_rejects_an_unreserved_trusted_key(self):
        with self.assertRaisesRegex(ValueError, "unreserved key"):
            merge_trusted_event_fields({}, trusted={"fleet_id": 2})


class SqliteAttributionTests(unittest.TestCase):
    def _columns(self, path: str) -> list[str]:
        # ``with sqlite3.connect()`` commits but does NOT close on Windows; an
        # open handle would lock the temp directory during cleanup.
        with closing(sqlite3.connect(path)) as db:
            return [row[1] for row in db.execute("PRAGMA table_info(dispatch_events)")]

    def test_legacy_four_column_schema_is_untouched_and_readable(self):
        """An on-disk DB created by the old code must keep working, no ALTER."""
        with tempfile.TemporaryDirectory() as temporary:
            path = str(Path(temporary) / "legacy.sqlite3")
            with closing(sqlite3.connect(path)) as db:
                db.execute(LEGACY_DDL)
                db.execute(
                    "INSERT INTO dispatch_events (created_at, event, payload_json) "
                    "VALUES (?, ?, ?)",
                    ("2026-11-03T00:00:00+00:00", "legacy_event",
                     json.dumps({"fleet_id": 2})),
                )
                db.commit()
            before = self._columns(path)

            sink = SqliteDispatchEventLog(path, task_kind=TaskKind.EXPEDITION.value)
            new_event_id = sink("new_event", {"fleet_id": 3})

            self.assertEqual(new_event_id, 2)
            self.assertEqual(self._columns(path), before)  # no migration happened
            self.assertEqual(len(before), 4)
            rows = sink.load_all()
            self.assertEqual([row["event_id"] for row in rows], [1, 2])
            self.assertEqual(len(rows), 2)
            # Old row: payload byte-for-byte as written, attribution unknown.
            self.assertEqual(rows[0]["fields"], {"fleet_id": 2})
            self.assertNotIn("task_kind", rows[0]["fields"])
            # New row: trusted attribution.
            self.assertEqual(
                rows[1]["fields"], {"fleet_id": 3, "task_kind": "expedition"}
            )

    def test_fields_cannot_forge_task_attribution_in_the_real_sink(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = str(Path(temporary) / "events.sqlite3")
            sink = SqliteDispatchEventLog(path, task_kind="expedition")
            sink(
                "dispatch_verified",
                {
                    "fleet_id": 2,
                    "task_kind": "combat",
                    "task_implemented": False,
                    "task_ui_click_capable": False,
                },
            )
            fields = sink.load_all()[0]["fields"]
            self.assertEqual(fields["task_kind"], "expedition")
            self.assertNotIn("task_implemented", fields)
            self.assertNotIn("task_ui_click_capable", fields)
            self.assertEqual(fields["fleet_id"], 2)

    def test_two_tasks_share_one_table_without_cross_contamination(self):
        """Two composition roots, one append-only table, each row owned by its opener.

        ``combat`` is only a LOG LABEL here. No task is registered, enabled or
        clicked: IMPLEMENTED_TASK_KINDS is asserted unchanged below.
        """
        with tempfile.TemporaryDirectory() as temporary:
            path = str(Path(temporary) / "shared.sqlite3")
            expedition = SqliteDispatchEventLog(path, task_kind="expedition")
            other = SqliteDispatchEventLog(path, task_kind="combat")

            event_ids = (
                expedition("a", {"fleet_id": 2, "task_kind": "combat"}),
                other("b", {"fleet_id": 2, "task_kind": "expedition"}),
                expedition("c", {"fleet_id": 3}),
            )
            self.assertEqual(event_ids, (1, 2, 3))

            rows = expedition.load_all()
            self.assertEqual([row["event_id"] for row in rows], [1, 2, 3])
            self.assertEqual(len(rows), 3)
            self.assertEqual(
                [(row["event"], row["fields"]["task_kind"]) for row in rows],
                [("a", "expedition"), ("b", "combat"), ("c", "expedition")],
            )
            # Shared monotonic id sequence, no per-task table split.
            self.assertEqual(self._columns(path),
                             ["event_id", "created_at", "event", "payload_json"])
            self.assertEqual(IMPLEMENTED_TASK_KINDS, frozenset({TaskKind.EXPEDITION}))

    def test_opt_in_is_still_backward_compatible(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = str(Path(temporary) / "events.sqlite3")
            plain = SqliteDispatchEventLog(path)
            plain("legacy_event", {"fleet_id": 2})
            self.assertEqual(
                plain.load_all()[0]["fields"], {"fleet_id": 2}
            )


class ProductionJsonlAttributionTests(unittest.TestCase):
    def test_print_event_extras_cannot_override_the_trusted_envelope(self):
        import one_expedition_ui_loop as entry

        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "loop.jsonl"
            buffer = io.StringIO()
            with (
                patch.object(entry, "RUN_ID", "run-REAL"),
                patch.object(entry, "LOG_PATH", log),
                redirect_stdout(buffer),
            ):
                # ``event`` is a formal parameter, so Python already refuses a
                # duplicate via **extra; the other three envelope keys were the
                # actual spoofable ones before this fix.
                entry._print_event(
                    "real_event",
                    run_id="FORGED",
                    timestamp_utc="FORGED",
                    task_kind="combat",
                    task_implemented=False,
                    task_ui_click_capable=False,
                    fleet_id=9,
                )
            stdout_payload = json.loads(buffer.getvalue().strip())
            disk_payload = json.loads(log.read_text(encoding="utf-8").strip())

            for payload in (stdout_payload, disk_payload):
                self.assertEqual(payload["event"], "real_event")
                self.assertEqual(payload["run_id"], "run-REAL")
                self.assertEqual(payload["task_kind"], "expedition")
                self.assertNotEqual(payload["timestamp_utc"], "FORGED")
                self.assertEqual(payload["fleet_id"], 9)
                self.assertNotIn("task_implemented", payload)
                self.assertNotIn("task_ui_click_capable", payload)

    def test_print_event_keeps_historical_envelope_key_order(self):
        import one_expedition_ui_loop as entry

        buffer = io.StringIO()
        with (
            patch.object(entry, "LOG_PATH", None),
            redirect_stdout(buffer),
        ):
            entry._print_event("real_event", point="sortie")
        payload = json.loads(buffer.getvalue().strip())
        self.assertEqual(
            list(payload)[:4],
            ["timestamp_utc", "run_id", "task_kind", "event"],
        )
        self.assertEqual(payload["point"], "sortie")

    def test_print_event_state_fields_still_win_over_diagnostics(self):
        from types import SimpleNamespace

        import one_expedition_ui_loop as entry

        state = SimpleNamespace(fleet_id=3, mission=(1, 3, 1000, 0), ship_ids=(1, 2, 3, 0))
        buffer = io.StringIO()
        with (
            patch.object(entry, "LOG_PATH", None),
            redirect_stdout(buffer),
        ):
            entry._print_event("state_event", state, fleet_id=99)
        payload = json.loads(buffer.getvalue().strip())
        self.assertEqual(payload["fleet_id"], 3)
        self.assertEqual(payload["mission"], [1, 3, 1000, 0])
        self.assertEqual(payload["ship_count"], 3)


if __name__ == "__main__":
    unittest.main()
