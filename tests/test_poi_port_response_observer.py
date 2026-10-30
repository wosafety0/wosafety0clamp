"""Offline tests for the one-shot passive port observer wrapper."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kancollebot.adapters.poi.port_response_observer import PassivePortResponseObserver


PREFIX = "[PortObserver] WITNESS_JSON:"
WITNESS = {
    "schemaVersion": 1,
    "source": {"path": "/kcsapi/api_port/port"},
}


class PassivePortResponseObserverTests(unittest.TestCase):
    def make_observer(self, directory, *, timeout_seconds=2):
        script = Path(directory) / "observer.js"
        script.write_text("module.exports = function () {};", encoding="utf-8")
        return PassivePortResponseObserver(script, timeout_seconds=timeout_seconds)

    def test_returns_in_memory_witness_and_sets_child_only_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            observer = self.make_observer(directory)
            output = "[PortObserver] STATUS: CAPTURED\n" + PREFIX + json.dumps(WITNESS) + "\n"
            completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=output, stderr="")
            with patch(
                "kancollebot.adapters.poi.port_response_observer.subprocess.run",
                return_value=completed,
            ) as run:
                self.assertEqual(observer.capture(), WITNESS)
            self.assertEqual(observer.last_status, "captured")
            kwargs = run.call_args.kwargs
            self.assertEqual(kwargs["env"]["KCB_POI_FRESHNESS_WITNESS_STDOUT"], "1")
            self.assertNotIn("KCB_POI_FRESHNESS_WITNESS_STDOUT", __import__("os").environ)
            self.assertFalse(kwargs.get("shell", False))

    def test_no_natural_capture_returns_none(self):
        with tempfile.TemporaryDirectory() as directory:
            observer = self.make_observer(directory)
            completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="[PortObserver] STATUS: TIMEOUT\n", stderr="")
            with patch(
                "kancollebot.adapters.poi.port_response_observer.subprocess.run",
                return_value=completed,
            ):
                self.assertIsNone(observer.capture())
            self.assertEqual(observer.last_status, "natural_response_timeout")

    def test_timeout_detail_counters_are_exposed_as_safe_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            observer = self.make_observer(directory)
            detail = {
                "reason": "timeout",
                "networkEnabled": True,
                "cdpEventMessages": 40,
                "networkRequestWillBeSentEvents": 14,
                "networkResponseEvents": 12,
                "networkLoadingFinishedEvents": 11,
                "networkLoadingFailedEvents": 1,
                "kancolleHostResponseEvents": 3,
                "portPathResponseEvents": 1,
                "portHttp200ResponseEvents": 1,
                "cachedResponsesSkipped": 0,
            }
            output = (
                "[PortObserver] STATUS: TIMEOUT\n"
                "[PortObserver] DETAIL: " + json.dumps(detail) + "\n"
            )
            completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=output, stderr="")
            with patch(
                "kancollebot.adapters.poi.port_response_observer.subprocess.run",
                return_value=completed,
            ):
                self.assertIsNone(observer.capture())
            self.assertEqual(observer.last_status, "natural_response_timeout")
            self.assertEqual(observer.last_detail, detail)

    def test_timeout_detail_rejects_unexpected_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            observer = self.make_observer(directory)
            detail = {"networkResponseEvents": 2, "url": "https://example.invalid/secret"}
            output = (
                "[PortObserver] STATUS: TIMEOUT\n"
                "[PortObserver] DETAIL: " + json.dumps(detail) + "\n"
            )
            completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=output, stderr="")
            with patch(
                "kancollebot.adapters.poi.port_response_observer.subprocess.run",
                return_value=completed,
            ):
                self.assertIsNone(observer.capture())
            self.assertIsNone(observer.last_detail)

    def test_target_discovery_failure_is_not_reported_as_no_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            observer = self.make_observer(directory)
            completed = subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout="[PortObserver] STATUS: TARGET_DISCOVERY_FAILED\n",
                stderr="",
            )
            with patch(
                "kancollebot.adapters.poi.port_response_observer.subprocess.run",
                return_value=completed,
            ):
                self.assertIsNone(observer.capture())
            self.assertEqual(observer.last_status, "target_discovery_failed")

    def test_witness_requires_matching_captured_status(self):
        with tempfile.TemporaryDirectory() as directory:
            observer = self.make_observer(directory)
            completed = subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout=PREFIX + json.dumps(WITNESS) + "\n", stderr=""
            )
            with patch(
                "kancollebot.adapters.poi.port_response_observer.subprocess.run",
                return_value=completed,
            ):
                self.assertIsNone(observer.capture())
            self.assertEqual(observer.last_status, "invalid_output")

    def test_invalid_or_ambiguous_witness_output_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            observer = self.make_observer(directory)
            cases = (
                (PREFIX + "{bad-json}\n", "invalid_json"),
                (PREFIX + json.dumps(WITNESS) + "\n" + PREFIX + json.dumps(WITNESS), "invalid_output"),
                (PREFIX + "[]\n", "invalid_shape"),
            )
            for output, expected_status in cases:
                with self.subTest(expected_status=expected_status):
                    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=output, stderr="")
                    with patch(
                        "kancollebot.adapters.poi.port_response_observer.subprocess.run",
                        return_value=completed,
                    ):
                        self.assertIsNone(observer.capture())
                    self.assertEqual(observer.last_status, expected_status)

    def test_process_timeout_and_nonzero_exit_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            observer = self.make_observer(directory)
            with patch(
                "kancollebot.adapters.poi.port_response_observer.subprocess.run",
                side_effect=subprocess.TimeoutExpired("node", 2),
            ):
                self.assertIsNone(observer.capture())
            self.assertEqual(observer.last_status, "process_timeout")
            completed = subprocess.CompletedProcess(args=[], returncode=1, stdout=PREFIX + json.dumps(WITNESS), stderr="error")
            with patch(
                "kancollebot.adapters.poi.port_response_observer.subprocess.run",
                return_value=completed,
            ):
                self.assertIsNone(observer.capture())
            self.assertEqual(observer.last_status, "observer_failed")

    def test_missing_script_and_invalid_timeout_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            observer = PassivePortResponseObserver(Path(directory) / "missing.js")
            self.assertIsNone(observer.capture())
            self.assertEqual(observer.last_status, "script_missing")
        with self.assertRaisesRegex(ValueError, "positive"):
            PassivePortResponseObserver("observer.js", timeout_seconds=0)
        with self.assertRaisesRegex(ValueError, "finite"):
            PassivePortResponseObserver("observer.js", timeout_seconds=float("nan"))


if __name__ == "__main__":
    unittest.main()
