from __future__ import annotations

import threading
import time
import unittest

from kancollebot.adapters.poi.background_port_snapshot_provider import (
    BackgroundPortSnapshotProvider,
)


WITNESS = {
    "schemaVersion": 1,
    "source": {
        "path": "/kcsapi/api_port/port",
        "responseReceivedAt": "2026-11-02T00:00:03Z",
        "bodyCapturedAt": "2026-11-02T00:00:04Z",
    },
    "data": {"api_deck_port": [{"api_id": 2}]},
}

NEW_WITNESS = {
    "schemaVersion": 1,
    "source": {
        "path": "/kcsapi/api_port/port",
        "responseReceivedAt": "2026-11-02T00:00:13Z",
        "bodyCapturedAt": "2026-11-02T00:00:14Z",
    },
    "data": {"api_deck_port": [{"api_id": 2}]},
}


class BlockingObserver:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.last_status = "waiting"
        self.calls = 0

    def capture(self):
        self.calls += 1
        self.entered.set()
        self.release.wait(2)
        self.last_status = "captured"
        return WITNESS


class RetryObserver:
    def __init__(self):
        self.calls = 0
        self.last_status = "not_started"

    def capture(self):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary observer failure")
        self.last_status = "captured"
        return WITNESS


class RepeatingObserver:
    def __init__(self):
        self.calls = 0
        self.second_call_entered = threading.Event()
        self.release_second_call = threading.Event()
        self.last_status = "captured"

    def capture(self):
        self.calls += 1
        if self.calls == 1:
            return WITNESS
        self.second_call_entered.set()
        self.release_second_call.wait(2)
        self.last_status = "natural_response_timeout"
        return None


class CancellableObserver:
    def __init__(self):
        self.entered = threading.Event()
        self.last_status = "waiting"

    def capture_cancellable(self, cancel_event):
        self.entered.set()
        cancel_event.wait(2)
        self.last_status = "cancelled"
        return None


class TimedSecondCaptureObserver:
    def __init__(self):
        self.calls = 0
        self.second_capture_entered = threading.Event()
        self.release_second_capture = threading.Event()
        self.last_status = "captured"

    def capture(self):
        self.calls += 1
        if self.calls == 1:
            return WITNESS
        self.second_capture_entered.set()
        self.release_second_capture.wait(2)
        self.last_status = "captured"
        return NEW_WITNESS


class BackgroundPortSnapshotProviderTests(unittest.TestCase):
    def test_reader_call_is_non_blocking_while_observer_waits(self):
        observer = BlockingObserver()
        provider = BackgroundPortSnapshotProvider(observer, retry_delay_seconds=0.01)
        try:
            self.assertIsNone(provider())
            self.assertTrue(observer.entered.wait(1))
            self.assertIsNone(provider.latest_snapshot)
            observer.release.set()
            self.assertTrue(
                self._wait_for(lambda: provider.latest_snapshot is not None)
            )
            self.assertEqual(provider(), WITNESS)
            self.assertEqual(provider.last_status, "captured")
        finally:
            observer.release.set()
            provider.close(join_timeout_seconds=1)

    def test_observer_exception_is_fail_closed_and_retried(self):
        observer = RetryObserver()
        provider = BackgroundPortSnapshotProvider(observer, retry_delay_seconds=0.01)
        try:
            self.assertIsNone(provider())
            self.assertTrue(
                self._wait_for(lambda: provider.latest_snapshot is not None)
            )
            self.assertGreaterEqual(observer.calls, 2)
            self.assertEqual(provider(), WITNESS)
            self.assertEqual(provider.last_status, "captured")
        finally:
            provider.close(join_timeout_seconds=1)

    def test_successful_capture_is_followed_by_a_new_observer_cycle(self):
        observer = RepeatingObserver()
        provider = BackgroundPortSnapshotProvider(observer, retry_delay_seconds=0.01)
        try:
            self.assertIsNone(provider())
            self.assertTrue(observer.second_call_entered.wait(1))
            self.assertEqual(provider(), WITNESS)
            self.assertGreaterEqual(observer.calls, 2)
        finally:
            observer.release_second_call.set()
            provider.close(join_timeout_seconds=1)


    def test_wait_for_snapshot_after_ignores_old_witness_and_returns_new_capture(self):
        observer = TimedSecondCaptureObserver()
        provider = BackgroundPortSnapshotProvider(observer, retry_delay_seconds=0.01)
        from datetime import datetime, timezone
        after = datetime(2026, 11, 2, 0, 0, 10, tzinfo=timezone.utc)
        try:
            provider()
            self.assertTrue(observer.second_capture_entered.wait(1))
            self.assertIsNone(provider.wait_for_snapshot_after(after=after, timeout_seconds=0.01))
            observer.release_second_capture.set()
            received = provider.wait_for_snapshot_after(after=after, timeout_seconds=1.0)
            self.assertEqual(received, NEW_WITNESS)
            self.assertEqual(provider.latest_snapshot, NEW_WITNESS)
        finally:
            observer.release_second_capture.set()
            provider.close(join_timeout_seconds=1)

    def test_returned_witness_is_defensively_copied(self):
        observer = BlockingObserver()
        provider = BackgroundPortSnapshotProvider(observer, retry_delay_seconds=0.01)
        try:
            provider()
            self.assertTrue(observer.entered.wait(1))
            observer.release.set()
            self.assertTrue(
                self._wait_for(lambda: provider.latest_snapshot is not None)
            )
            result = provider()
            result["data"]["api_deck_port"][0]["api_id"] = 999
            self.assertEqual(
                provider.latest_snapshot["data"]["api_deck_port"][0]["api_id"],
                2,
            )
        finally:
            observer.release.set()
            provider.close(join_timeout_seconds=1)

    def test_close_cancels_an_in_flight_cancellable_observer(self):
        observer = CancellableObserver()
        provider = BackgroundPortSnapshotProvider(observer)
        provider()
        self.assertTrue(observer.entered.wait(1))
        provider.close(join_timeout_seconds=1)
        self.assertFalse(provider.is_running)
        self.assertEqual(provider.last_status, "cancelled")

    def test_close_prevents_late_start_and_validates_timeouts(self):
        observer = RetryObserver()
        provider = BackgroundPortSnapshotProvider(observer)
        provider.close()
        self.assertIsNone(provider())
        self.assertEqual(observer.calls, 0)
        with self.assertRaisesRegex(ValueError, "retry_delay_seconds"):
            BackgroundPortSnapshotProvider(observer, retry_delay_seconds=0)
        with self.assertRaisesRegex(ValueError, "join_timeout_seconds"):
            provider.close(join_timeout_seconds=-1)

    @staticmethod
    def _wait_for(predicate, timeout=1.0):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.005)
        return True


if __name__ == "__main__":
    unittest.main()
