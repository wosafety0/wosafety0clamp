"""P0.2 run log: one event format, correlation ids, redaction, rebuildability.

Everything here is offline and uses temporary directories. No real project log
is written, read or deleted by this module.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from kancollebot.observability.audit_policy import (  # noqa: E402
    CRITICAL_AUDIT_EVENTS,
    is_critical_event,
)
from kancollebot.observability.event_envelope import (  # noqa: E402
    DESCRIBED_FIELDS,
    ENVELOPE_FIELDS,
    IDENTITY_ONLY_FIELDS,
    SINK_OWNED_FIELDS,
    TRUSTED_ONLY_FIELDS,
    CorrelationContext,
    build_event_record,
    new_dispatch_id,
    new_event_id,
    new_run_id,
    validate_event_name,
)
from kancollebot.observability.run_log import (  # noqa: E402
    RunLogPolicy,
    RunLogStore,
    summarise_events,
)
from kancollebot.observability.sink import (  # noqa: E402
    RunLogSink,
    TeeEventSink,
    build_run_log_sink,
)
from kancollebot.observability.redaction import REDACTED, redact_event_record

FIXED_NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)


def read_all_lines(directory: Path) -> list[str]:
    lines: list[str] = []
    for path in sorted(directory.glob("run-*.jsonl")):
        lines.extend(path.read_text(encoding="utf-8").splitlines())
    return lines


def read_all_records(directory: Path) -> list[dict]:
    return [json.loads(line) for line in read_all_lines(directory)]


class EnvelopeTests(unittest.TestCase):
    def make_context(self, **overrides) -> CorrelationContext:
        base = {"run_id": "run-20261106T120000Z-aaaaaaaaaaaa", "task_kind": "expedition"}
        base.update(overrides)
        return CorrelationContext(**base)

    def test_envelope_field_list_is_the_documented_twelve(self):
        self.assertEqual(
            ENVELOPE_FIELDS,
            (
                "timestamp_utc",
                "event",
                "run_id",
                "task_kind",
                "event_id",
                "sqlite_event_id",
                "dispatch_id",
                "action_id",
                "battle_id",
                "fleet_id",
                "source",
                "observed_at",
                "correlation_id",
            ),
        )
        self.assertEqual(
            SINK_OWNED_FIELDS,
            frozenset({"timestamp_utc", "event", "run_id", "task_kind", "event_id", "sqlite_event_id"}),
        )
        # Correlation identity is refused to emitters as well: only the runtime
        # may say which dispatch/action/battle/correlation a row belongs to.
        self.assertEqual(
            IDENTITY_ONLY_FIELDS,
            frozenset({"dispatch_id", "action_id", "battle_id", "correlation_id"}),
        )
        self.assertEqual(
            TRUSTED_ONLY_FIELDS, SINK_OWNED_FIELDS | IDENTITY_ONLY_FIELDS
        )
        self.assertEqual(DESCRIBED_FIELDS, ("fleet_id", "source", "observed_at"))

    def test_trusted_identity_cannot_be_forged_by_an_emitter(self):
        context = self.make_context()
        record = build_event_record(
            event="dispatch_confirmed_by_poi",
            fields={
                "run_id": "run-forged",
                "event": "forged_event",
                "task_kind": "combat",
                "event_id": "event-forged",
                "sqlite_event_id": 999,
                "timestamp_utc": "1999-01-01T00:00:00+00:00",
                "mission_id": 1,
            },
            context=context,
            event_id="event-real",
            timestamp_utc="2026-11-06T12:00:00+00:00",
        )
        self.assertEqual(record["run_id"], context.run_id)
        self.assertEqual(record["event"], "dispatch_confirmed_by_poi")
        self.assertEqual(record["task_kind"], "expedition")
        self.assertEqual(record["event_id"], "event-real")
        self.assertIsNone(record["sqlite_event_id"], "emitters cannot forge the SQLite row link")
        self.assertEqual(record["timestamp_utc"], "2026-11-06T12:00:00+00:00")
        self.assertEqual(record["mission_id"], 1)

    def test_context_wins_over_an_emitter_value_for_correlation_slots(self):
        context = self.make_context(dispatch_id="dispatch-real", fleet_id=4)
        record = build_event_record(
            event="continuous_ui_click_attempted",
            fields={"dispatch_id": "dispatch-forged", "fleet_id": 3, "action": "ok"},
            context=context,
            event_id="event-1",
            timestamp_utc="2026-11-06T12:00:00+00:00",
        )
        self.assertEqual(record["dispatch_id"], "dispatch-real")
        self.assertEqual(record["fleet_id"], 4)

    def test_emitter_value_is_promoted_when_the_context_has_none(self):
        # Historical payloads already carry fleet_id / observed_at in the body.
        # Dropping them would be data loss, so they are promoted into the
        # envelope slot instead of being duplicated or discarded.
        record = build_event_record(
            event="fleet_monitor_transition",
            fields={
                "fleet_id": 2,
                "observed_at": datetime(2026, 11, 6, 11, 59, tzinfo=timezone.utc),
            },
            context=self.make_context(),
            event_id="event-1",
            timestamp_utc="2026-11-06T12:00:00+00:00",
        )
        self.assertEqual(record["fleet_id"], 2)
        self.assertEqual(record["observed_at"], "2026-11-06T11:59:00+00:00")
        self.assertEqual(record["observed_at_origin"], "emitter")

    def test_context_observation_is_marked_as_such(self):
        record = build_event_record(
            event="poi_observation",
            fields={},
            context=self.make_context(
                source="poi-redux:info.fleets",
                observed_at=datetime(2026, 11, 6, 11, 59, tzinfo=timezone.utc),
            ),
            event_id="event-1",
            timestamp_utc="2026-11-06T12:00:00+00:00",
        )
        self.assertEqual(record["source"], "poi-redux:info.fleets")
        self.assertEqual(record["observed_at_origin"], "context")

    def test_sqlite_event_id_requires_a_positive_exact_integer(self):
        context = self.make_context()
        for bad in (True, 0, -1, 1.0, "1"):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    build_event_record(
                        event="diagnostic_event",
                        fields={},
                        context=context,
                        event_id="event-1",
                        timestamp_utc="2026-11-06T12:00:00+00:00",
                        sqlite_event_id=bad,
                    )

    def test_event_names_must_be_single_tokens(self):
        for bad in ("", "   ", "has space", "line\nbreak", "nul\x00byte"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                validate_event_name(bad)
        self.assertEqual(validate_event_name("settlement_drain_action"), "settlement_drain_action")

    def test_identifiers_are_unique_and_prefixed(self):
        run_ids = {new_run_id() for _ in range(20)}
        dispatch_ids = {new_dispatch_id() for _ in range(20)}
        event_ids = {new_event_id() for _ in range(20)}
        self.assertEqual(len(run_ids), 20)
        self.assertEqual(len(dispatch_ids), 20)
        self.assertEqual(len(event_ids), 20)
        self.assertTrue(all(item.startswith("run-") for item in run_ids))
        self.assertTrue(all(item.startswith("dispatch-") for item in dispatch_ids))
        self.assertTrue(all(item.startswith("event-") for item in event_ids))
        self.assertTrue(run_ids.isdisjoint(dispatch_ids))

    def test_correlation_context_rejects_a_naive_observed_at(self):
        with self.assertRaises(ValueError):
            self.make_context(observed_at=datetime(2026, 11, 6, 12, 0, 0))

    def test_correlation_context_rejects_unknown_slots(self):
        with self.assertRaises(ValueError):
            self.make_context().with_(nonsense="x")


class SinkFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make_sink(self, run_id: str | None = None, **kwargs) -> RunLogSink:
        return build_run_log_sink(
            directory=self.directory,
            task_kind="expedition",
            run_id=run_id,
            prune_interval_seconds=0,
            **kwargs,
        )

    def test_each_event_is_one_independently_parseable_line(self):
        sink = self.make_sink(run_id="run-20261106T120000Z-000000000001")
        sink("fleet_monitor_transition", {"fleet_id": 3, "note": "line\nbreak and \ttab"})
        sink("supervisor_tick", {"dispatch_count": 0})
        sink("settlement_drain_action", {"page": "return_notice"})

        lines = read_all_lines(self.directory)
        self.assertEqual(len(lines), 3)
        for line in lines:
            self.assertNotIn("\n", line)
            record = json.loads(line)
            self.assertEqual(record["run_id"], "run-20261106T120000Z-000000000001")
            self.assertEqual(record["task_kind"], "expedition")
            self.assertIn(record["event"], {
                "fleet_monitor_transition", "supervisor_tick", "settlement_drain_action"
            })
            # Every line carries the whole identity envelope, not a partial one.
            for field in ("timestamp_utc", "run_id", "task_kind", "event_id"):
                self.assertIn(field, record)

    def test_one_corrupt_line_does_not_break_the_file(self):
        sink = self.make_sink()
        sink("supervisor_tick", {"dispatch_count": 1})
        patch = sorted(self.directory.glob("run-*.jsonl"))[0]
        with patch.open("a", encoding="utf-8") as stream:
            stream.write('{"event": "truncated"\n')
        sink("supervisor_tick", {"dispatch_count": 2})

        store = RunLogStore(RunLogPolicy(directory=self.directory))
        events = store.read_events()
        self.assertEqual([item["event"] for item in events], ["supervisor_tick", "supervisor_tick"])

    def test_events_of_one_dispatch_share_a_dispatch_id(self):
        sink = self.make_sink()
        dispatch_id = new_dispatch_id()
        with sink.correlation(dispatch_id=dispatch_id, fleet_id=4):
            sink("dispatch_intent_persisted", {"fleet_id": 4, "mission_id": 1})
            sink("continuous_ui_click_attempted", {"fleet_id": 4, "action": "dispatch"})
            sink("dispatch_confirmed_by_poi", {"fleet_id": 4, "mission_id": 1})

        records = read_all_records(self.directory)
        self.assertEqual({item["dispatch_id"] for item in records}, {dispatch_id})
        self.assertEqual({item["fleet_id"] for item in records}, {4})

    def test_correlation_scope_is_released_afterwards(self):
        sink = self.make_sink()
        with sink.correlation(dispatch_id=new_dispatch_id()):
            sink("dispatch_intent_persisted", {"fleet_id": 4})
        sink("supervisor_tick", {"dispatch_count": 0})
        records = read_all_records(self.directory)
        self.assertIsNotNone(records[0].get("dispatch_id"))
        self.assertNotIn("dispatch_id", records[1])

    def test_two_identical_dispatches_are_never_merged(self):
        # Same fleet, same mission, same ships, same second: still two events,
        # because identity is an injected id, not a derived payload hash.
        sink = self.make_sink()
        for _ in range(2):
            with sink.correlation(dispatch_id=new_dispatch_id(), fleet_id=4):
                sink("dispatch_intent_persisted", {"fleet_id": 4, "mission_id": 1, "ship_ids": [1, 2]})

        records = read_all_records(self.directory)
        self.assertEqual(len(records), 2)
        self.assertEqual(len({item["dispatch_id"] for item in records}), 2)
        self.assertEqual(len({item["event_id"] for item in records}), 2)
        self.assertEqual(records[0]["mission_id"], records[1]["mission_id"])

    def test_two_runs_in_one_directory_stay_distinguishable(self):
        first = self.make_sink(run_id="run-20261106T120000Z-000000000001")
        second = self.make_sink(run_id="run-20261106T120000Z-000000000002")
        first("supervisor_tick", {"dispatch_count": 0})
        second("supervisor_tick", {"dispatch_count": 0})

        store = RunLogStore(RunLogPolicy(directory=self.directory))
        self.assertEqual(len(store.read_events(run_id="run-20261106T120000Z-000000000001")), 1)
        self.assertEqual(len(store.read_events(run_id="run-20261106T120000Z-000000000002")), 1)
        self.assertEqual(len(store.read_events()), 2)

    def test_promoted_source_and_observed_at_values_are_redacted(self):
        sink = self.make_sink()
        secret = "abcdef123456"
        sink("poi_observation", {
            "source": f"https://example.invalid/?token={secret}",
            "observed_at": f"https://example.invalid/?token={secret}",
        })
        record = read_all_records(self.directory)[0]
        self.assertIn("[REDACTED]", record["source"])
        self.assertIn("[REDACTED]", record["observed_at"])
        self.assertNotIn(secret, record["source"])
        self.assertNotIn(secret, record["observed_at"])

    def test_redaction_fails_closed_when_the_shared_policy_cannot_load(self):
        with patch(
            "kancollebot.observability.redaction._loader",
            side_effect=ImportError("simulated missing adapter"),
        ):
            result = redact_event_record(
                {"event": "safe_event", "diagnostic": "token=must-not-leak"},
                envelope_fields=frozenset({"event"}),
            )
        self.assertEqual(result["event"], "safe_event")
        self.assertEqual(result["diagnostic"], REDACTED)

    def test_sensitive_values_are_redacted_and_long_values_truncated(self):
        sink = self.make_sink()
        sink("continuous_ui_click_attempted", {
            "api_key": "sk-live-should-never-be-written",
            "authorization": "Bearer abcdef",
            "url": "https://example.invalid/path?token=abcdef&x=1",
            "nested": {"cookie": "session=abcdef", "safe": "kept"},
            "notes": "x" * 5000,
        })
        record = read_all_records(self.directory)[0]
        self.assertEqual(record["api_key"], "[REDACTED]")
        self.assertEqual(record["authorization"], "[REDACTED]")
        self.assertIn("[REDACTED]", record["url"])
        self.assertNotIn("abcdef", record["url"])
        self.assertEqual(record["nested"]["cookie"], "[REDACTED]")
        self.assertEqual(record["nested"]["safe"], "kept")
        self.assertLessEqual(len(record["notes"]), 512)

    def test_tee_sink_preserves_the_primary_behaviour_and_adds_the_run_log(self):
        primary: list[tuple[str, dict]] = []

        def sqlite_like(event, fields):
            primary.append((event, dict(fields)))

        sink = self.make_sink()
        tee = TeeEventSink(sqlite_like, sink)
        tee("dispatch_intent_persisted", {"fleet_id": 3, "mission_id": 3})

        self.assertEqual([item[0] for item in primary], ["dispatch_intent_persisted"])
        self.assertEqual(primary[0][1], {"fleet_id": 3, "mission_id": 3})
        self.assertEqual(len(read_all_records(self.directory)), 1)
        self.assertEqual(tee.run_id, sink.run_id)

    def test_tee_links_sqlite_event_id_to_jsonl_without_conflating_event_id(self):
        from kancollebot.expeditions.continuous_dispatch_controller import SqliteDispatchEventLog

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "events.sqlite3"
            log_dir = root / "run-log"
            primary = SqliteDispatchEventLog(str(database), task_kind="expedition")
            run_id = "run-20261106T120000Z-0000000000a1"
            secondary = build_run_log_sink(
                directory=log_dir, task_kind="expedition", run_id=run_id,
                prune_interval_seconds=0,
            )
            tee = TeeEventSink(primary, secondary)
            tee("dispatch_intent_persisted", {"fleet_id": 4, "mission_id": 1})
            tee("dispatch_confirmed_by_poi", {"fleet_id": 4, "mission_id": 1})

            sqlite_rows = primary.load_all()
            jsonl_rows = RunLogStore(RunLogPolicy(directory=log_dir)).read_events(run_id=run_id)
            self.assertEqual([row["event_id"] for row in sqlite_rows], [1, 2])
            self.assertEqual([row["sqlite_event_id"] for row in jsonl_rows], [1, 2])
            self.assertEqual(len({row["event_id"] for row in jsonl_rows}), 2)
            self.assertTrue(all(isinstance(row["event_id"], str) for row in jsonl_rows))
            self.assertEqual([row["event"] for row in sqlite_rows], [row["event"] for row in jsonl_rows])

    def test_tee_rejects_invalid_primary_event_id_receipt(self):
        calls = []
        def primary(event, fields):
            return True
        def secondary(event, fields):
            calls.append(event)
        with self.assertRaisesRegex(TypeError, "positive int event ID"):
            TeeEventSink(primary, secondary)("diagnostic_event", {})
        self.assertEqual(calls, [])

    def test_snapshot_is_replaced_atomically_and_leaves_no_temp_file(self):
        sink = self.make_sink()
        sink.write_snapshot({"phase": "first", "fleet_count": 4})
        first = json.loads((self.directory / "current_state.json").read_text(encoding="utf-8"))
        self.assertEqual(first["phase"], "first")
        self.assertEqual(first["run_id"], sink.run_id)

        sink.write_snapshot({"phase": "second", "fleet_count": 4})
        second = json.loads((self.directory / "current_state.json").read_text(encoding="utf-8"))
        self.assertEqual(second["phase"], "second")
        self.assertEqual(
            sorted(path.name for path in self.directory.glob("*.tmp")), []
        )

    def test_a_stopped_run_can_be_rebuilt_from_the_log_alone(self):
        sink = self.make_sink(run_id="run-20261106T120000Z-00000000000a")
        sink.start({"database": "tmp.sqlite3"})
        dispatch_id = new_dispatch_id()
        with sink.correlation(dispatch_id=dispatch_id, fleet_id=4):
            sink("dispatch_intent_persisted", {"mission_id": 1})
            sink("dispatch_confirmed_by_poi", {"mission_id": 1})
            sink("continuous_ui_settlement_step_started", {"step": 1})
            sink("settlement_drain_finished", {"reached_home": True})
        sink.stop("monitor_exited")

        store = RunLogStore(RunLogPolicy(directory=self.directory))
        events = store.read_events(run_id="run-20261106T120000Z-00000000000a")
        names = [item["event"] for item in events]
        self.assertEqual(names[0], "run_started")
        self.assertEqual(names[-1], "run_stopped")
        self.assertEqual(events[-1]["reason"], "monitor_exited")
        self.assertIn("dispatch_confirmed_by_poi", names)
        self.assertEqual(
            {item.get("dispatch_id") for item in events if item["event"].startswith("dispatch_")},
            {dispatch_id},
        )
        summary = summarise_events(events)
        self.assertEqual(summary["runs"], {"run-20261106T120000Z-00000000000a": len(events)})
        # Every row emitted inside the dispatch scope is attributable to it.
        self.assertEqual(summary["dispatches"], {dispatch_id: 4})

    def test_legacy_sqlite_rows_stay_readable_and_the_schema_is_untouched(self):
        from kancollebot.expeditions.continuous_dispatch_controller import (
            SqliteDispatchEventLog,
        )

        database = self.directory / "legacy.sqlite3"
        with closing(sqlite3.connect(database)) as db:
            db.execute(
                """CREATE TABLE dispatch_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    event TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )"""
            )
            db.execute(
                "INSERT INTO dispatch_events (created_at, event, payload_json) VALUES (?, ?, ?)",
                ("2026-11-05T00:00:00+00:00", "dispatch_confirmed_by_poi", '{"fleet_id": 3}'),
            )
            db.commit()
        sink = SqliteDispatchEventLog(str(database), task_kind="expedition")
        sink("supervisor_tick", {"dispatch_count": 0})

        with closing(sqlite3.connect(database)) as db:
            columns = [row[1] for row in db.execute("PRAGMA table_info(dispatch_events)")]
        self.assertEqual(columns, ["event_id", "created_at", "event", "payload_json"])

        rows = sink.load_all()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["fields"], {"fleet_id": 3})
        self.assertEqual(rows[1]["fields"]["task_kind"], "expedition")


class CriticalEventRegistryTests(unittest.TestCase):
    def test_every_click_and_dispatch_row_is_critical(self):
        for name in (
            "continuous_ui_click_attempted",
            "continuous_ui_click_sent",
            "continuous_ui_click_ambiguous",
            "dispatch_intent_persisted",
            "dispatch_confirmed_by_poi",
            "settlement_drain_action",
            "resupply_confirmed",
            "supervisor_paused",
        ):
            self.assertTrue(is_critical_event(name), name)

    def test_diagnostics_and_lifecycle_rows_are_not_critical(self):
        for name in (
            "fleet_monitor_transition",
            "supervisor_tick",
            "run_started",
            "run_stopped",
            "run_log_prune_completed",
        ):
            self.assertFalse(is_critical_event(name), name)

    def test_registry_is_a_frozenset_of_plain_names(self):
        self.assertIsInstance(CRITICAL_AUDIT_EVENTS, frozenset)
        for name in CRITICAL_AUDIT_EVENTS:
            self.assertEqual(validate_event_name(name), name)


if __name__ == "__main__":
    unittest.main()
