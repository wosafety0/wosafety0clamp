"""P0.2 run log: day rotation, capacity caps, three-day retention, path safety.

The retention rules are the risky part of a log system: they are the only place
that deletes something. Every test here states the negative case explicitly -
what must SURVIVE - because "it deleted the right files" is worthless if it also
deleted the action journal.
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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from kancollebot.observability.run_log import (  # noqa: E402
    RunLogError,
    RunLogPathError,
    RunLogPolicy,
    RunLogStore,
)
from kancollebot.observability.sink import build_run_log_sink  # noqa: E402

TODAY = date(2026, 11, 6)


def write_part(directory: Path, day: str, size: int = 200, part: int | None = None) -> Path:
    name = f"run-{day}.jsonl" if part in (None, 0) else f"run-{day}.{part:03d}.jsonl"
    path = directory / name
    payload = json.dumps({"event": "filler", "pad": "x" * max(size - 40, 1)}) + "\n"
    path.write_text(payload, encoding="utf-8")
    return path


class RotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make_store(self, **overrides) -> RunLogStore:
        defaults = {
            "directory": self.directory,
            "max_file_bytes": 1024,
            "max_total_bytes": 64 * 1024,
        }
        defaults.update(overrides)
        return RunLogStore(RunLogPolicy(**defaults), today=lambda: TODAY)

    def test_a_part_never_exceeds_the_configured_file_cap(self):
        store = self.make_store()
        for index in range(60):
            store.append({"event": "filler", "index": index, "pad": "y" * 80})

        parts = store.log_files()
        self.assertGreater(len(parts), 1, "60 events must not fit in one 1 KiB part")
        for part in parts:
            self.assertLessEqual(part.size_bytes, 1024, part.path.name)
        self.assertEqual(parts[0].path.name, "run-2026-11-06.jsonl")
        self.assertEqual(parts[1].path.name, "run-2026-11-06.001.jsonl")

    def test_single_event_larger_than_file_cap_is_rejected(self):
        store = self.make_store(max_file_bytes=1024, max_total_bytes=64 * 1024)
        with self.assertRaises(RunLogError):
            store.append({"event": "oversized", "pad": "x" * 2048})
        self.assertEqual(store.log_files(), ())

    def test_log_part_symlink_is_rejected_without_following_target(self):
        target = self.directory / "current_state.json"
        target.write_text('{"phase":"IDLE"}', encoding="utf-8")
        before = target.read_text(encoding="utf-8")
        link = self.directory / "run-2026-11-06.jsonl"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

        store = self.make_store()
        with self.assertRaises(RunLogPathError):
            store.append({"event": "must_not_touch_snapshot"})
        self.assertEqual(target.read_text(encoding="utf-8"), before)
        self.assertEqual(store.log_files(), ())

    def test_rotation_never_splits_one_event_across_two_files(self):
        store = self.make_store(max_file_bytes=1024, max_total_bytes=64 * 1024)
        for index in range(40):
            store.append({"event": "filler", "index": index, "pad": "z" * 120})

        records = store.read_events()
        self.assertEqual(len(records), 40)
        self.assertEqual([item["index"] for item in records], list(range(40)))

    def test_a_new_day_starts_a_new_file(self):
        days = iter([date(2026, 11, 5), date(2026, 11, 5), date(2026, 11, 6)])
        store = RunLogStore(
            RunLogPolicy(directory=self.directory), today=lambda: next(days)
        )
        store.append({"event": "one"})
        store.append({"event": "two"})
        store.append({"event": "three"})
        names = [item.path.name for item in store.log_files()]
        self.assertEqual(names, ["run-2026-11-05.jsonl", "run-2026-11-06.jsonl"])


class RetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make_store(self, **overrides) -> RunLogStore:
        defaults = {
            "directory": self.directory,
            "max_file_bytes": 1024,
            "max_total_bytes": 1024 * 1024,
        }
        defaults.update(overrides)
        return RunLogStore(RunLogPolicy(**defaults), today=lambda: TODAY)

    def test_only_days_outside_the_three_day_window_are_removed(self):
        paths = {
            day: write_part(self.directory, day)
            for day in ("2026-11-01", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06")
        }
        report = self.make_store().prune()

        self.assertEqual(report.removed, ["run-2026-11-01.jsonl", "run-2026-11-03.jsonl"])
        self.assertEqual(sorted(path.name for path in self.directory.glob("run-*.jsonl")),
                         ["run-2026-11-04.jsonl", "run-2026-11-05.jsonl", "run-2026-11-06.jsonl"])
        self.assertTrue(all(path.exists() for day, path in paths.items() if day >= "2026-11-04"))
        self.assertTrue(report.ok)

    def test_retention_window_is_configurable_but_defaults_to_three_days(self):
        self.assertEqual(RunLogPolicy(directory=self.directory).retention_days, 3)

    def test_reports_databases_and_snapshots_are_never_deleted(self):
        write_part(self.directory, "2026-11-01")
        report_file = self.directory / "P0_FLEET4_VERIFICATION.json"
        report_file.write_text('{"verdict": "PASS"}', encoding="utf-8")
        notes = self.directory / "notes.md"
        notes.write_text("# keep me", encoding="utf-8")
        journal = self.directory / "action_journal.sqlite3"
        with closing(sqlite3.connect(journal)) as db:
            db.execute("CREATE TABLE expedition_actions (action_key TEXT PRIMARY KEY)")
            db.execute("INSERT INTO expedition_actions VALUES ('4:1:2026-11-06')")
            db.commit()
        snapshot = self.directory / "current_state.json"
        snapshot.write_text('{"phase": "IDLE"}', encoding="utf-8")

        self.make_store().prune()

        self.assertTrue(report_file.exists())
        self.assertTrue(notes.exists())
        self.assertTrue(journal.exists())
        self.assertTrue(snapshot.exists())
        with closing(sqlite3.connect(journal)) as db:
            self.assertEqual(
                db.execute("SELECT action_key FROM expedition_actions").fetchone()[0],
                "4:1:2026-11-06",
            )

    def test_directory_cap_removes_the_oldest_parts_first(self):
        day_paths = [
            write_part(self.directory, day, size=900)
            for day in ("2026-11-04", "2026-11-05", "2026-11-06")
        ]
        report = self.make_store(max_file_bytes=1024, max_total_bytes=2000).prune()

        self.assertLessEqual(report.total_bytes, 2000)
        self.assertFalse(day_paths[0].exists(), "oldest part must go first")
        self.assertTrue(day_paths[-1].exists())

    def test_the_part_being_appended_to_is_never_sacrificed(self):
        store = self.make_store(max_file_bytes=1024, max_total_bytes=1024)
        store.append({"event": "filler", "pad": "q" * 400})
        store.append({"event": "filler", "pad": "q" * 400})  # forces a second part
        current = store._last_written
        report = store.prune()
        self.assertIsNotNone(current)
        self.assertTrue(current.exists())
        self.assertTrue(report.ok)

    def test_a_path_outside_the_log_directory_cannot_be_touched(self):
        with tempfile.TemporaryDirectory() as outside_dir:
            victim = Path(outside_dir) / "victim.jsonl"
            victim.write_text("do not delete", encoding="utf-8")
            store = self.make_store()
            with self.assertRaises(RunLogPathError):
                store._assert_inside(victim)
            self.assertTrue(victim.exists())

    def test_a_cleanup_failure_is_reported_and_never_raised(self):
        doomed = write_part(self.directory, "2026-11-01")
        journal = self.directory / "action_journal.sqlite3"
        with closing(sqlite3.connect(journal)) as db:
            db.execute("CREATE TABLE expedition_actions (action_key TEXT PRIMARY KEY)")
            db.commit()

        original_unlink = Path.unlink

        def failing_unlink(self, *args, **kwargs):
            if self.name == doomed.name:
                raise PermissionError("simulated lock on the log file")
            return original_unlink(self, *args, **kwargs)

        Path.unlink = failing_unlink  # type: ignore[assignment]
        try:
            report = self.make_store().prune()
        finally:
            Path.unlink = original_unlink  # type: ignore[assignment]

        self.assertFalse(report.ok)
        self.assertTrue(any("simulated lock" in item for item in report.errors))
        self.assertTrue(doomed.exists())
        # The safety database is untouched by a failed log cleanup.
        with closing(sqlite3.connect(journal)) as db:
            self.assertEqual(
                [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")],
                ["expedition_actions"],
            )

    def test_cleanup_reports_itself_as_an_event(self):
        write_part(self.directory, "2026-11-01")
        sink = build_run_log_sink(
            directory=self.directory,
            task_kind="expedition",
            run_id="run-20261106T120000Z-0000000000ff",
        )
        sink("supervisor_tick", {"dispatch_count": 0})

        store = RunLogStore(RunLogPolicy(directory=self.directory), today=lambda: TODAY)
        events = [item["event"] for item in store.read_events()]
        self.assertIn("run_log_prune_completed", events)
        record = [item for item in store.read_events() if item["event"] == "run_log_prune_completed"][0]
        self.assertEqual(record["removed"], ["run-2026-11-01.jsonl"])
        self.assertIn("retention_days", record)
        self.assertIn("max_total_bytes", record)

    def test_a_failing_cleanup_is_reported_as_a_warning_row(self):
        store = RunLogStore(
            RunLogPolicy(directory=self.directory, max_file_bytes=1024, max_total_bytes=2048),
            today=lambda: TODAY,
        )
        doomed = write_part(self.directory, "2026-11-01")
        sink = build_run_log_sink(
            directory=self.directory,
            task_kind="expedition",
            run_id="run-20261106T120000Z-0000000000fe",
        )
        original_unlink = Path.unlink

        def failing_unlink(self, *args, **kwargs):
            if self.name == doomed.name:
                raise PermissionError("simulated lock on the log file")
            return original_unlink(self, *args, **kwargs)

        Path.unlink = failing_unlink  # type: ignore[assignment]
        try:
            sink("supervisor_tick", {"dispatch_count": 0})
        finally:
            Path.unlink = original_unlink  # type: ignore[assignment]

        records = store.read_events()
        names = [item["event"] for item in records]
        self.assertIn("supervisor_tick", names)
        self.assertIn("run_log_prune_failed", names)
        warning = [item for item in records if item["event"] == "run_log_prune_failed"][0]
        self.assertTrue(warning["errors"])
        self.assertEqual(warning["removed"], [])


class SnapshotPathSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make_store(self) -> RunLogStore:
        return RunLogStore(RunLogPolicy(directory=self.directory))

    def test_snapshot_target_symlink_is_rejected_for_read_and_write(self):
        sentinel = self.directory / "sentinel.txt"
        sentinel.write_text("keep", encoding="utf-8")
        target = self.directory / "current_state.json"
        try:
            target.symlink_to(sentinel)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

        store = self.make_store()
        with self.assertRaises(RunLogPathError):
            store.write_snapshot({"phase": "DISPATCHING"})
        with self.assertRaises(RunLogPathError):
            store.read_snapshot()
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
        self.assertTrue(target.is_symlink())

    def test_predictable_snapshot_temp_symlink_is_never_followed(self):
        sentinel = self.directory / "sentinel.txt"
        sentinel.write_text("keep", encoding="utf-8")
        planted = self.directory / "current_state.json.tmp"
        try:
            planted.symlink_to(sentinel)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

        store = self.make_store()
        store.write_snapshot({"phase": "IDLE"})
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
        self.assertEqual(
            json.loads((self.directory / "current_state.json").read_text(encoding="utf-8"))["phase"],
            "IDLE",
        )
        self.assertTrue(planted.is_symlink())


class PolicyValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_policy_rejects_impossible_limits(self):
        for overrides in (
            {"retention_days": 0},
            {"retention_days": True},
            {"max_file_bytes": 100},
            {"max_total_bytes": 512, "max_file_bytes": 1024},
            {"snapshot_name": "../escape.json"},
            {"snapshot_name": "state.txt"},
        ):
            with self.assertRaises(ValueError, msg=repr(overrides)):
                RunLogPolicy(directory=self.directory, **overrides)
        with self.assertRaises(ValueError):
            RunLogPolicy(directory="")

    def test_default_policy_keeps_three_days_and_is_bounded(self):
        policy = RunLogPolicy(directory=self.directory)
        self.assertEqual(policy.retention_days, 3)
        self.assertEqual(policy.max_file_bytes, 8 * 1024 * 1024)
        self.assertEqual(policy.max_total_bytes, 64 * 1024 * 1024)
        self.assertLessEqual(policy.max_file_bytes, policy.max_total_bytes)


if __name__ == "__main__":
    unittest.main()
