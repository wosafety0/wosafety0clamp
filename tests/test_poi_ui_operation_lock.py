from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
