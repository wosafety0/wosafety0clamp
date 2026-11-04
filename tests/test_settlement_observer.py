import unittest
import hashlib

from kancollebot.adapters.poi.cdp_input import CapturedFrame
from types import SimpleNamespace

from kancollebot.expeditions.settlement_observer import (
    SettlementObservationError,
    SettlementStateObserver,
)


class SettlementStateObserverTests(unittest.TestCase):
    def test_accepts_two_consecutive_expected_frames(self):
        calls = []
        def verify(expected):
            calls.append(expected)
            return SimpleNamespace(
                state=expected,
                consecutive_states=(expected, expected),
                evidence="visual-two-frame-gate",
                frames=frame_pair(),
            )

        observation = SettlementStateObserver(verify).observe(3, "RETURN_RESULT")
        self.assertEqual(calls, ["RETURN_RESULT"])
        self.assertEqual(observation.fleet_id, 3)
        self.assertEqual(observation.state, "RETURN_RESULT")
        self.assertEqual(observation.consecutive_states, ("RETURN_RESULT", "RETURN_RESULT"))

    def test_rejects_evidence_for_a_different_state(self):
        observer = SettlementStateObserver(lambda expected: SimpleNamespace(
            state="BONUS_ITEMS",
            consecutive_states=("BONUS_ITEMS", "BONUS_ITEMS"),
            evidence="visual",
            frames=frame_pair(),
        ))
        with self.assertRaisesRegex(SettlementObservationError, "different UI state"):
            observer.observe(3, "RETURN_RESULT")

    def test_rejects_single_frame_evidence(self):
        observer = SettlementStateObserver(lambda expected: SimpleNamespace(
            state=expected,
            consecutive_states=(expected,),
            evidence="visual",
        ))
        with self.assertRaisesRegex(SettlementObservationError, "two consecutive"):
            observer.observe(3, "RETURN_RESULT")

    def test_rejects_missing_evidence_source(self):
        observer = SettlementStateObserver(lambda expected: SimpleNamespace(
            state=expected,
            consecutive_states=(expected, expected),
            evidence=" ",
            frames=frame_pair(),
        ))
        with self.assertRaisesRegex(SettlementObservationError, "source is missing"):
            observer.observe(3, "BONUS_ITEMS")

    def test_rejects_missing_captured_frame_objects(self):
        observer = SettlementStateObserver(lambda expected: SimpleNamespace(
            state=expected,
            consecutive_states=(expected, expected),
            evidence="visual",
        ))
        with self.assertRaisesRegex(SettlementObservationError, "captured frame objects"):
            observer.observe(3, "BONUS_ITEMS")

    def test_rejects_duplicate_frame_identity_or_digest(self):
        duplicate = captured_frame(3)
        for pair in ((duplicate, duplicate), (captured_frame(4), captured_frame(4))):
            observer = SettlementStateObserver(lambda expected, pair=pair: SimpleNamespace(
                state=expected,
                consecutive_states=(expected, expected),
                evidence="visual",
                frames=pair,
            ))
            with self.assertRaisesRegex(SettlementObservationError, "distinct captured frame"):
                observer.observe(3, "BONUS_ITEMS")

    def test_verifier_failure_propagates_without_retry(self):
        calls = []
        def verify(expected):
            calls.append(expected)
            raise RuntimeError("capture failed")
        observer = SettlementStateObserver(verify)
        with self.assertRaisesRegex(RuntimeError, "capture failed"):
            observer.observe(3, "RETURN_RESULT")
        self.assertEqual(calls, ["RETURN_RESULT"])

    def test_rejects_unsupported_expected_state(self):
        observer = SettlementStateObserver(lambda expected: None)
        with self.assertRaisesRegex(ValueError, "expected_state"):
            observer.observe(3, "HOME_PORT")


if __name__ == "__main__":
    unittest.main()
