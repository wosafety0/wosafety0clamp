"""The passive expedition-result observer wrapper.

The wrapper is the only place the child's output reaches the rest of the
project, so its fail-closed behaviour is what matters here: anything the
child prints that is not exactly one well-shaped witness is not a witness.
"""
import json
import subprocess
import threading
import unittest
from pathlib import Path

from kancollebot.adapters.poi.mission_result_observer import (
    PassiveMissionResultObserver,
)


WITNESS = {
    "schemaVersion": 1,
    "source": {
        "kind": "cdp-network-mission-result",
        "path": "/kcsapi/api_req_mission/result",
        "host": "w14h.kancolle-server.com",
        "httpStatus": 200,
        "requestedAt": "2026-11-05T08:51:59.133Z",
        "responseReceivedAt": "2026-11-05T08:51:59.500Z",
    },
    "data": {
        "apiDeckId": 4,
        "apiQuestName": "東京急行",
        "apiClearResult": 2,
        "apiShipId": [1437, 9885, 96144],
    },
    "safety": {
        "gameInputSent": False,
        "gameApiRequestInitiated": False,
        "runtimeEvaluateUsed": False,
    },
}

PREFIX = "[MissionResultObserver] "


def child_output(
    *,
    witness=WITNESS,
    status="CAPTURED",
    detail=None,
    extra_lines=(),
):
    lines = []
    if status is not None:
        lines.append(f"{PREFIX}STATUS: {status}")
    if detail is not None:
        lines.append(f"{PREFIX}DETAIL: {detail}")
    if witness is not None:
        lines.append(f"{PREFIX}WITNESS_JSON:" + json.dumps(witness))
    lines.extend(extra_lines)
    return "\n".join(lines) + "\n"


class FakeCompleted:
    def __init__(self, stdout, returncode=0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class ObserverTests(unittest.TestCase):
    def make_observer(self, directory, *, stdout, returncode=0, timeout_seconds=2):
        script = Path(directory) / "observer.js"
        script.write_text("// stub", encoding="utf-8")
        observer = PassiveMissionResultObserver(
            script, timeout_seconds=timeout_seconds
        )
        observer._consume_result = lambda result: PassiveMissionResultObserver._consume_result(
            observer, result
        )
        observer._run_result = (stdout, returncode)
        return observer

    def consume(self, observer, stdout, returncode=0):
        return PassiveMissionResultObserver._consume_result(
            observer, FakeCompleted(stdout, returncode)
        )

    def test_a_well_formed_witness_is_returned(self):
        with __import__("tempfile").TemporaryDirectory() as directory:
            observer = self.make_observer(directory, stdout="")
            witness = self.consume(observer, child_output())
        self.assertIsNotNone(witness)
        self.assertEqual(witness["data"]["apiDeckId"], 4)
        self.assertEqual(witness["data"]["apiQuestName"], "東京急行")
        self.assertEqual(observer.last_status, "captured")

    def test_the_placeholder_ship_is_filtered(self):
        payload = json.loads(json.dumps(WITNESS))
        payload["data"]["apiShipId"] = [-1, 1437, 9885]
        with __import__("tempfile").TemporaryDirectory() as directory:
            observer = self.make_observer(directory, stdout="")
            witness = self.consume(observer, child_output(witness=payload))
        self.assertEqual(witness["data"]["apiShipId"], [1437, 9885])

    def test_no_capture_returns_none(self):
        with __import__("tempfile").TemporaryDirectory() as directory:
            observer = self.make_observer(directory, stdout="")
            witness = self.consume(observer, child_output(witness=None))
        self.assertIsNone(witness)
        self.assertEqual(observer.last_status, "no_capture")

    def test_a_timeout_is_reported_as_such(self):
        with __import__("tempfile").TemporaryDirectory() as directory:
            observer = self.make_observer(directory, stdout="")
            witness = self.consume(
                observer,
                child_output(witness=None, status="TIMEOUT", detail='{"reason": "x"}'),
            )
        self.assertIsNone(witness)
        self.assertEqual(observer.last_status, "natural_response_timeout")
        self.assertEqual(observer.last_detail, {"reason": "x"})

    def test_a_witness_without_a_captured_status_is_refused(self):
        with __import__("tempfile").TemporaryDirectory() as directory:
            observer = self.make_observer(directory, stdout="")
            witness = self.consume(observer, child_output(status="REJECTED"))
        self.assertIsNone(witness)
        self.assertEqual(observer.last_status, "invalid_output")

    def test_two_witnesses_are_ambiguous_and_refused(self):
        with __import__("tempfile").TemporaryDirectory() as directory:
            observer = self.make_observer(directory, stdout="")
            witness = self.consume(
                observer,
                child_output(extra_lines=[f"{PREFIX}WITNESS_JSON:" + json.dumps(WITNESS)]),
            )
        self.assertIsNone(witness)
        self.assertEqual(observer.last_status, "invalid_output")

    def test_invalid_json_is_refused(self):
        with __import__("tempfile").TemporaryDirectory() as directory:
            observer = self.make_observer(directory, stdout="")
            witness = self.consume(
                observer, f"{PREFIX}STATUS: CAPTURED\n{PREFIX}WITNESS_JSON:{{not json\n"
            )
        self.assertIsNone(witness)
        self.assertEqual(observer.last_status, "invalid_json")

    def test_a_witness_without_a_fleet_is_refused(self):
        payload = json.loads(json.dumps(WITNESS))
        payload["data"]["apiDeckId"] = 0
        with __import__("tempfile").TemporaryDirectory() as directory:
            observer = self.make_observer(directory, stdout="")
            witness = self.consume(observer, child_output(witness=payload))
        self.assertIsNone(witness)
        self.assertEqual(observer.last_status, "invalid_shape")

    def test_a_witness_without_ships_is_refused(self):
        payload = json.loads(json.dumps(WITNESS))
        payload["data"]["apiShipId"] = [-1]
        with __import__("tempfile").TemporaryDirectory() as directory:
            observer = self.make_observer(directory, stdout="")
            witness = self.consume(observer, child_output(witness=payload))
        self.assertIsNone(witness)
        self.assertEqual(observer.last_status, "invalid_shape")

    def test_a_nonzero_exit_is_refused(self):
        with __import__("tempfile").TemporaryDirectory() as directory:
            observer = self.make_observer(directory, stdout="")
            witness = self.consume(observer, child_output(), returncode=1)
        self.assertIsNone(witness)
        self.assertEqual(observer.last_status, "observer_failed")

    def test_a_missing_script_returns_none(self):
        observer = PassiveMissionResultObserver(
            Path("/nonexistent/observer.js"), timeout_seconds=2
        )
        self.assertIsNone(observer.capture())
        self.assertEqual(observer.last_status, "script_missing")

    def test_the_child_is_told_the_callers_timeout(self):
        # The child must not outlive the caller: it gets the same window.
        # The environment is built by a helper, so that is what is checked -
        # the subprocess plumbing itself is the standard library's.
        from kancollebot.adapters.poi.mission_result_observer import (
            _TIMEOUT_ENV,
            _WITNESS_ENV,
            child_environment,
        )

        env = child_environment(45.0)
        self.assertEqual(env[_TIMEOUT_ENV], "45000")
        self.assertEqual(env[_WITNESS_ENV], "1")

    def test_invalid_construction_is_rejected(self):
        with self.assertRaises(ValueError):
            PassiveMissionResultObserver("x.js", node_executable="  ")
        with self.assertRaises(ValueError):
            PassiveMissionResultObserver("x.js", timeout_seconds=0)


    def test_a_complete_reward_receipt_is_normalized(self):
        payload = json.loads(json.dumps(WITNESS))
        payload["data"]["apiRewardReceipt"] = {
            "status": "complete",
            "material": [10, 20, 30, 40],
            "useitemFlags": [1, 0],
            "items": [{"slot": 1, "useitemId": 6, "useitemCount": 2}],
        }
        with __import__("tempfile").TemporaryDirectory() as directory:
            observer = self.make_observer(directory, stdout="")
            witness = self.consume(observer, child_output(witness=payload))
        receipt = witness["data"]["apiRewardReceipt"]
        self.assertEqual(receipt["status"], "complete")
        self.assertEqual(receipt["material"], [10, 20, 30, 40])
        self.assertEqual(receipt["useitemFlags"], [1, 0])
        self.assertEqual(
            receipt["items"],
            [{"slot": 1, "useitemId": 6, "useitemCount": 2}],
        )

    def test_a_declared_complete_receipt_with_missing_material_is_downgraded(self):
        payload = json.loads(json.dumps(WITNESS))
        payload["data"]["apiRewardReceipt"] = {
            "status": "complete",
            "material": None,
            "useitemFlags": [1, 0],
            "items": [{"slot": 1, "useitemId": 6, "useitemCount": 2}],
        }
        with __import__("tempfile").TemporaryDirectory() as directory:
            observer = self.make_observer(directory, stdout="")
            witness = self.consume(observer, child_output(witness=payload))
        self.assertEqual(witness["data"]["apiRewardReceipt"]["status"], "incomplete")

    def test_a_flagged_item_without_a_valid_item_payload_is_incomplete(self):
        payload = json.loads(json.dumps(WITNESS))
        payload["data"]["apiRewardReceipt"] = {
            "status": "complete",
            "material": [10, 20, 30, 40],
            "useitemFlags": [1, 0],
            "items": [],
        }
        with __import__("tempfile").TemporaryDirectory() as directory:
            observer = self.make_observer(directory, stdout="")
            witness = self.consume(observer, child_output(witness=payload))
        self.assertEqual(witness["data"]["apiRewardReceipt"]["status"], "incomplete")

    def test_a_legacy_identity_witness_without_receipt_stays_readable(self):
        with __import__("tempfile").TemporaryDirectory() as directory:
            observer = self.make_observer(directory, stdout="")
            witness = self.consume(observer, child_output())
        self.assertNotIn("apiRewardReceipt", witness["data"])


    def test_a_non_string_receipt_status_is_downgraded_without_crashing(self):
        payload = json.loads(json.dumps(WITNESS))
        payload["data"]["apiRewardReceipt"] = {
            "status": ["complete"],
            "material": [10, 20, 30, 40],
            "useitemFlags": [0, 0],
            "items": [],
        }
        with __import__("tempfile").TemporaryDirectory() as directory:
            observer = self.make_observer(directory, stdout="")
            witness = self.consume(observer, child_output(witness=payload))
        self.assertEqual(witness["data"]["apiRewardReceipt"]["status"], "incomplete")


if __name__ == "__main__":
    unittest.main()
