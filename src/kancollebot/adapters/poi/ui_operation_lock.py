"""Cross-process arbitration for the shared Poi UI.

The Windows implementation uses a named kernel mutex. The OS releases ownership
when a process exits, including after a crash. If an owning thread is terminated
without releasing it, the abandoned-mutex result fails closed once.
"""
from __future__ import annotations

import math
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


_MUTEX_NAME = "Local" + chr(92) + "KancolleBot.PoiUiOperation.v1"
_THREAD_LOCK = threading.RLock()
_THREAD_STATE = threading.local()


class PoiUiOperationLockError(RuntimeError):
    """Poi UI access could not be acquired safely."""


def _acquire_windows_mutex(timeout_seconds: float) -> tuple[object, object]:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    wait_ms = min(max(int(timeout_seconds * 1000), 1), 0xFFFFFFFE)
    result = kernel32.WaitForSingleObject(handle, wait_ms)
    if result == 0x00000000:
        return kernel32, handle
    if result == 0x00000080:
        # WAIT_ABANDONED means this process owns the mutex, but the previous
        # owner exited while inside a UI operation. Release and fail closed.
        kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)
        raise PoiUiOperationLockError(
            "Poi UI mutex was abandoned by a previous process; inspect UI state before retrying"
        )
    error = ctypes.get_last_error()
    kernel32.CloseHandle(handle)
    if result == 0x00000102:
        raise TimeoutError("timed out waiting for the shared Poi UI mutex")
    if result == 0xFFFFFFFF:
        raise ctypes.WinError(error)
    raise PoiUiOperationLockError(f"unexpected Poi UI mutex wait result: {result:#x}")


@contextmanager
def _process_lock(timeout_seconds: float) -> Iterator[None]:
    if os.name == "nt":
        kernel32, handle = _acquire_windows_mutex(timeout_seconds)
        try:
            yield
        finally:
            kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)
        return

    # Portable fallback for offline tests and non-Windows development.
    import fcntl

    lock_path = Path(tempfile.gettempdir()) / "kancollebot-poi-ui-operation.lock"
    descriptor = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out waiting for the shared Poi UI file lock")
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


@contextmanager
def poi_ui_operation(
    label: str, *, timeout_seconds: float = 120.0
) -> Iterator[None]:
    """Serialize a Poi UI operation across threads and processes."""
    if not isinstance(label, str) or not label.strip():
        raise ValueError("UI operation label must not be empty")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be finite and positive")

    if not _THREAD_LOCK.acquire(timeout=float(timeout_seconds)):
        raise TimeoutError(f"timed out waiting for in-process Poi UI operation: {label}")
    depth = getattr(_THREAD_STATE, "depth", 0)
    try:
        if depth == 0:
            with _process_lock(float(timeout_seconds)):
                _THREAD_STATE.depth = 1
                try:
                    yield
                finally:
                    _THREAD_STATE.depth = 0
        else:
            _THREAD_STATE.depth = depth + 1
            try:
                yield
            finally:
                _THREAD_STATE.depth = depth
    finally:
        _THREAD_LOCK.release()
