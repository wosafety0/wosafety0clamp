from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from kancollebot.adapters.poi import ui_operation_lock
from kancollebot.adapters.poi.ui_operation_lock import PoiUiOperationLockError, poi_ui_operation


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"


class PoiUiOperationLockTests(unittest.TestCase):
    def _wait_for_file(self, path: Path, process: subprocess.Popen[str], timeout: float = 8.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists():
                return
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                self.fail(f"child exited before marker: rc={process.returncode}; stdout={stdout}; stderr={stderr}")
            time.sleep(0.02)
        process.kill()
        stdout, stderr = process.communicate()
        self.fail(f"timed out waiting for {path.name}; stdout={stdout}; stderr={stderr}")

    def test_mutex_serializes_separate_processes(self):
        with tempfile.TemporaryDirectory(prefix="kcb-ui-lock-") as directory:
            started = Path(directory) / "started"
            acquired = Path(directory) / "acquired"
            code = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from kancollebot.adapters.poi.ui_operation_lock import poi_ui_operation
Path(sys.argv[2]).write_text('started', encoding='utf-8')
with poi_ui_operation('child-process', timeout_seconds=6):
    Path(sys.argv[3]).write_text('acquired', encoding='utf-8')
"""
            with poi_ui_operation("parent-process", timeout_seconds=6):
                child = subprocess.Popen(
                    [sys.executable, "-c", code, str(SRC_ROOT), str(started), str(acquired)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                self._wait_for_file(started, child)
                time.sleep(0.2)
                self.assertFalse(acquired.exists(), "child entered the critical section while parent held it")
            stdout, stderr = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, f"child failed: stdout={stdout}; stderr={stderr}")
            self.assertTrue(acquired.exists())

    @unittest.skipUnless(os.name == "nt", "named mutex process-exit behavior is Windows-specific")
    def test_process_exit_releases_mutex_for_next_owner(self):
        with tempfile.TemporaryDirectory(prefix="kcb-ui-process-exit-") as directory:
            ready = Path(directory) / "owner-ready"
            code = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from kancollebot.adapters.poi.ui_operation_lock import poi_ui_operation
with poi_ui_operation('crashing-process', timeout_seconds=6):
    Path(sys.argv[2]).write_text('ready', encoding='utf-8')
    os._exit(0)
"""
            child = subprocess.Popen(
                [sys.executable, "-c", code, str(SRC_ROOT), str(ready)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._wait_for_file(ready, child)
            stdout, stderr = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, f"owner process failed: stdout={stdout}; stderr={stderr}")

            # The OS closes the terminated process's mutex handle automatically.
            with poi_ui_operation("next-owner-after-process-exit", timeout_seconds=3):
                pass

    @unittest.skipUnless(os.name == "nt", "abandoned named mutex behavior is Windows-specific")
    def test_abandoned_mutex_fails_closed_once_then_can_be_reacquired(self):
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        keeper = kernel32.CreateMutexW(None, False, ui_operation_lock._MUTEX_NAME)
        self.assertTrue(keeper)
        try:
            with tempfile.TemporaryDirectory(prefix="kcb-ui-abandoned-") as directory:
                ready = Path(directory) / "owner-ready"
                code = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from kancollebot.adapters.poi.ui_operation_lock import poi_ui_operation
with poi_ui_operation('abandoned-owner', timeout_seconds=6):
    Path(sys.argv[2]).write_text('ready', encoding='utf-8')
    os._exit(0)
"""
                child = subprocess.Popen(
                    [sys.executable, "-c", code, str(SRC_ROOT), str(ready)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                self._wait_for_file(ready, child)
                stdout, stderr = child.communicate(timeout=10)
                self.assertEqual(child.returncode, 0, f"owner process failed: stdout={stdout}; stderr={stderr}")

            with self.assertRaisesRegex(PoiUiOperationLockError, "abandoned"):
                with poi_ui_operation("inspect-abandoned-state", timeout_seconds=3):
                    self.fail("an abandoned mutex must not authorize a Poi UI operation")
            with poi_ui_operation("reacquire-after-abandonment", timeout_seconds=3):
                pass
        finally:
            kernel32.CloseHandle(keeper)

    def test_nested_operation_is_reentrant_on_the_same_thread(self):
        with poi_ui_operation("outer"):
            with poi_ui_operation("inner"):
                pass

    def test_expedition_timer_and_read_only_polling_do_not_hold_ui_mutex(self):
        script = Path(__file__).resolve().parents[1] / "tools" / "one_expedition_ui_loop.py"
        tree = ast.parse(script.read_text(encoding="utf-8"))
        run_function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "run"
        )
        lock_labels = set()
        for node in ast.walk(run_function):
            if not isinstance(node, (ast.With, ast.AsyncWith)):
                continue
            lock_calls = [
                item.context_expr for item in node.items
                if isinstance(item.context_expr, ast.Call)
                and isinstance(item.context_expr.func, ast.Name)
                and item.context_expr.func.id == "poi_ui_operation"
            ]
            if not lock_calls:
                continue
            for call in lock_calls:
                if call.args and isinstance(call.args[0], ast.Constant):
                    lock_labels.add(call.args[0].value)
            called_names = {
                item.func.id for item in ast.walk(node)
                if isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
            }
            self.assertNotIn(
                "_wait_for_return", called_names,
                "expedition timer waiting must not hold the shared Poi UI mutex",
            )
            self.assertNotIn(
                "_wait_for_idle", called_names,
                "read-only reward completion polling must not hold the shared Poi UI mutex",
            )

        def assert_read_only_polling_is_unlocked(node, lock_held=False):
            if isinstance(node, (ast.With, ast.AsyncWith)):
                owns_ui_lock = lock_held or any(
                    isinstance(item.context_expr, ast.Call)
                    and isinstance(item.context_expr.func, ast.Name)
                    and item.context_expr.func.id == "poi_ui_operation"
                    for item in node.items
                )
                for statement in node.body:
                    assert_read_only_polling_is_unlocked(statement, owns_ui_lock)
                return
            if isinstance(node, ast.While):
                loop_calls = {
                    item.func.id for item in ast.walk(node)
                    if isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
                }
                if "read_poi_fleet_states" in loop_calls:
                    self.assertFalse(
                        lock_held,
                        "read-only Redux polling must not hold the shared Poi UI mutex",
                    )
            for child in ast.iter_child_nodes(node):
                assert_read_only_polling_is_unlocked(child, lock_held)

        assert_read_only_polling_is_unlocked(run_function)
        self.assertEqual(
            lock_labels,
            {
                "one-expedition-dispatch",
                "one-expedition-return-refresh",
                "one-expedition-reward-screens",
                "one-expedition-reward-home-recovery",
            },
        )


if __name__ == "__main__":
    unittest.main()
