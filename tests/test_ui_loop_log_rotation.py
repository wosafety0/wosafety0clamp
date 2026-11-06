"""The legacy loop jsonl must be bounded, and bounding must not delete evidence.

``logs/one_expedition_ui_loop.jsonl`` is the human-facing stream the one-shot
tools print. Before this guard it had no size limit at all: a loop left running
could only stop by filling the disk. These tests pin the two properties that
matter:

* the file at the configured path is ALWAYS the live stream, so a reader that
  opens ``LOG_PATH`` never reads stale bytes, and
* rotation is a size guard only. It keeps exactly one previous file next to the
  data (a JSONL part that can be replayed), never touches the unified run log
  directory, and propagates a rotation failure instead of silently dropping rows.

Nothing here re-tests retention: the three-day window and the "databases and
snapshots are never deleted" rules belong to ``test_run_log_retention``.
"""
from __future__ import annotations

import dataclasses
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import one_expedition_ui_loop as controller  # noqa: E402
from kancollebot.runtime_config import RUNTIME_CONFIG  # noqa: E402


CAP = 1024
# One row large enough that four of them cannot fit under a 1 KiB cap, small
# enough to stay readable when an assertion fails.
ROW = '{"event":"row","padding":"' + "x" * 350 + '"}'


def _capped_config():
    # dataclasses.replace on the frozen singleton: the import-time assertion
    # already ran, so a test may lower the cap without redefining the default.
    return dataclasses.replace(RUNTIME_CONFIG, ui_loop_log_max_bytes=CAP)


class AppendLogLineTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.path = Path(self._directory.name) / "one_expedition_ui_loop.jsonl"
        patcher = patch.object(controller, "RUNTIME_CONFIG", _capped_config())
        patcher.start()
        self.addCleanup(patcher.stop)
        path_patcher = patch.object(controller, "LOG_PATH", self.path)
        path_patcher.start()
        self.addCleanup(path_patcher.stop)

    def _write_rows(self, count: int, start: int = 0) -> None:
        for index in range(start, start + count):
            controller._append_log_line(f'{{"event":"row","index":{index},"padding":"{"x" * 350}"}}')

    def test_nothing_is_written_when_no_path_is_configured(self):
        with patch.object(controller, "LOG_PATH", None):
            controller._append_log_line(ROW)
        self.assertFalse(self.path.exists())
        self.assertEqual(list(Path(self._directory.name).iterdir()), [])

    def test_rows_append_in_order_and_stay_one_json_object_per_line(self):
        self._write_rows(3)
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual([f'"index":{index}' in line for index, line in enumerate(lines)], [True, True, True])

    def test_the_first_writes_do_not_rotate_before_the_cap_is_reached(self):
        self._write_rows(2)
        self.assertFalse(self.path.with_name(self.path.name + ".1").exists())
        self.assertEqual(len(self.path.read_text(encoding="utf-8").splitlines()), 2)

    def test_reaching_the_cap_moves_the_old_stream_to_one_previous_file(self):
        self._write_rows(3)
        self.assertGreaterEqual(self.path.stat().st_size, CAP)
        self._write_rows(1, start=3)  # this append sees the full file and rotates first
        rotated = self.path.with_name(self.path.name + ".1")
        self.assertTrue(rotated.exists())
        # The live path holds only the newest row: readers never re-read history.
        live = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(live), 1)
        self.assertIn('"index":3', live[0])
        # The previous stream is kept verbatim, replayable, next to the data.
        self.assertEqual(len(rotated.read_text(encoding="utf-8").splitlines()), 3)
        self.assertIn('"index":0', rotated.read_text(encoding="utf-8"))
        self.assertIn('"index":2', rotated.read_text(encoding="utf-8"))
        self.assertNotIn('"index":3', rotated.read_text(encoding="utf-8"))

    def test_a_second_rotation_replaces_the_previous_one_instead_of_growing(self):
        rotated = self.path.with_name(self.path.name + ".1")
        rotated.write_text("sentinel\n", encoding="utf-8")
        self._write_rows(3)
        self._write_rows(1, start=3)
        self.assertNotIn("sentinel", rotated.read_text(encoding="utf-8"))
        self.assertIn('"index":0', rotated.read_text(encoding="utf-8"))
        # Exactly one previous file, never .2 / .3: bounded disk, one rotate pair.
        self.assertEqual(
            sorted(item.name for item in Path(self._directory.name).iterdir()),
            ["one_expedition_ui_loop.jsonl", "one_expedition_ui_loop.jsonl.1"],
        )

    def test_the_previous_file_lives_beside_the_stream_not_in_the_run_log(self):
        self._write_rows(3)
        self._write_rows(1, start=3)
        rotated = self.path.with_name(self.path.name + ".1")
        self.assertEqual(rotated.parent, self.path.parent)
        self.assertNotEqual(rotated.parent, RUNTIME_CONFIG.run_log_directory)

    def test_a_rotation_failure_propagates_and_leaves_the_stream_untouched(self):
        self._write_rows(3)
        before = self.path.read_text(encoding="utf-8")
        # A directory squatting on the rotation target makes unlink/replace fail.
        self.path.with_name(self.path.name + ".1").mkdir()
        with self.assertRaises(OSError):
            controller._append_log_line(ROW)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)


class LogLocationPolicyTests(unittest.TestCase):
    def test_the_legacy_stream_is_outside_the_directory_cleanup_owns(self):
        # The three-day cleanup only ever walks ``logs/run``. Keeping the legacy
        # loop log in ``logs/`` means no deny list is needed to protect it.
        self.assertEqual(RUNTIME_CONFIG.run_log_directory.name, "run")
        self.assertNotIn(
            RUNTIME_CONFIG.run_log_directory,
            RUNTIME_CONFIG.dispatch_log_path.parents,
        )

    def test_the_rotation_cap_is_a_real_bound_and_not_a_retention_window(self):
        self.assertGreaterEqual(RUNTIME_CONFIG.ui_loop_log_max_bytes, 1024)
        # Retention stays where it already lived; this cap must not imply a TTL.
        self.assertEqual(RUNTIME_CONFIG.run_log_retention_days, 3)


if __name__ == "__main__":
    unittest.main()
