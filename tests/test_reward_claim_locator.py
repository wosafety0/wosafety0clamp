from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None

from kancollebot.adapters.poi.cdp_input import CapturedFrame
from kancollebot.expeditions.continuous_dispatch_controller import UiStateEvidence
from kancollebot.expeditions.settlement_observer import SettlementObservation
from kancollebot.expeditions.reward_claim_gate import RewardClaimGate
from kancollebot.expeditions.reward_claim_locator import (
    RewardClaimLocalizationError,
    locate_reward_claim_target,
)


def make_frame(frame_id: int, payload: bytes) -> CapturedFrame:
    return CapturedFrame(
        frame_id,
        frame_id * 1000,
        payload,
        hashlib.sha256(payload).hexdigest(),
    )


class RewardClaimLocatorValidationTests(unittest.TestCase):
    def setUp(self):
        first = make_frame(10, b"\x89PNG\r\n\x1a\nfirst")
        second = make_frame(12, b"\x89PNG\r\n\x1a\nsecond")
        self.evidence = SettlementObservation(
            fleet_id=3,
            state="BONUS_ITEMS",
            consecutive_states=("BONUS_ITEMS", "BONUS_ITEMS"),
            evidence="two consecutive in-memory fingerprint matches",
            frames=(first, second),
        )

    def test_rejects_caller_fabricated_lookalike_evidence(self):
        lookalike = SimpleNamespace(
            state="BONUS_ITEMS",
            consecutive_states=("BONUS_ITEMS", "BONUS_ITEMS"),
            frames=self.evidence.frames,
        )
        with self.assertRaisesRegex(RewardClaimLocalizationError, "production UiStateEvidence"):
            locate_reward_claim_target(
                lookalike, "bonus_claim_button.png",
                calibration_id="live-calibration", region=(800, 500, 1150, 700),
            )

    def test_rejects_wrong_state_before_loading_vision_dependencies(self):
        wrong_state = SettlementObservation(
            fleet_id=3,
            state="RETURN_RESULT",
            consecutive_states=("RETURN_RESULT", "RETURN_RESULT"),
            evidence="two consecutive result state matches",
            frames=self.evidence.frames,
        )
        with self.assertRaisesRegex(RewardClaimLocalizationError, "two consecutive BONUS_ITEMS"):
            locate_reward_claim_target(
                wrong_state, "bonus_claim_button.png",
                calibration_id="live-calibration", region=(800, 500, 1150, 700),
            )

    def test_rejects_missing_frame_objects(self):
        missing = UiStateEvidence(
            state="BONUS_ITEMS",
            consecutive_states=("BONUS_ITEMS", "BONUS_ITEMS"),
            evidence="test-only state",
            frames=None,
        )
        with self.assertRaisesRegex(RewardClaimLocalizationError, "two retained CapturedFrame"):
            locate_reward_claim_target(
                missing, "bonus_claim_button.png",
                calibration_id="live-calibration", region=(800, 500, 1150, 700),
            )

    def test_rejects_non_dedicated_template_name(self):
        with self.assertRaisesRegex(RewardClaimLocalizationError, "dedicated bonus_claim_button"):
            locate_reward_claim_target(
                self.evidence, "result_next_button.png",
                calibration_id="live-calibration", region=(800, 500, 1150, 700),
            )

    def test_requires_explicit_calibration_and_region(self):
        with self.assertRaisesRegex(RewardClaimLocalizationError, "calibration ID"):
            locate_reward_claim_target(
                self.evidence, "bonus_claim_button.png",
                calibration_id="", region=(800, 500, 1150, 700),
            )
        with self.assertRaisesRegex(RewardClaimLocalizationError, "explicit integer"):
            locate_reward_claim_target(
                self.evidence, "bonus_claim_button.png",
                calibration_id="live-calibration", region=None,
            )


@unittest.skipIf(cv2 is None or np is None, "optional vision dependencies are unavailable")
class RewardClaimLocatorVisionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.template_path = Path(self.temp_dir.name) / "bonus_claim_button.png"
        rng = np.random.default_rng(20261104)
        self.template = rng.integers(0, 255, size=(24, 80), dtype=np.uint8)
        ok, encoded = cv2.imencode(".png", self.template)
        self.assertTrue(ok)
        self.template_path.write_bytes(encoded.tobytes())
        image = rng.integers(0, 255, size=(720, 1200), dtype=np.uint8)
        image[610:634, 940:1020] = self.template
        ok, encoded = cv2.imencode(".png", image)
        self.assertTrue(ok)
        payload = encoded.tobytes()
        self.frames = (
            make_frame(100, b"\x89PNG\r\n\x1a\nfirst-frame"),
            make_frame(102, payload),
        )
        self.evidence = SettlementObservation(
            fleet_id=3,
            state="BONUS_ITEMS",
            consecutive_states=("BONUS_ITEMS", "BONUS_ITEMS"),
            evidence="two consecutive in-memory fingerprint matches",
            frames=self.frames,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_target_is_localized_from_and_retains_the_identical_second_frame(self):
        target = locate_reward_claim_target(
            self.evidence,
            self.template_path,
            calibration_id="synthetic-test-calibration",
            region=(850, 580, 1120, 690),
            minimum_score=0.96,
            minimum_margin=0.10,
        )
        self.assertIs(target.frame, self.frames[1])
        self.assertIs(target.state_frames[0], self.frames[0])
        self.assertIs(target.state_frames[1], self.frames[1])
        self.assertEqual((target.left, target.top, target.right, target.bottom),
                         (940, 610, 1020, 634))
        self.assertEqual(target.frame_id, self.frames[1].frame_id)
        self.assertEqual(target.frame_sha256, self.frames[1].sha256)
        self.assertEqual(target.template_name, "bonus_claim_button.png")


    def test_localized_target_can_be_authorized_only_with_explicit_template_allowlist(self):
        target = locate_reward_claim_target(
            self.evidence,
            self.template_path,
            calibration_id="synthetic-test-calibration",
            region=(850, 580, 1120, 690),
        )
        gate = RewardClaimGate({target.template_sha256: "synthetic-test-calibration"})
        authorization = gate.authorize(target)
        self.assertEqual(authorization.frame_id, self.frames[1].frame_id)
        self.assertEqual(authorization.frame_sha256, self.frames[1].sha256)
        self.assertFalse(hasattr(authorization, "click"))
        self.assertFalse(hasattr(authorization, "execute"))

    def test_duplicate_template_matches_fail_closed(self):
        image = cv2.imdecode(
            np.frombuffer(self.frames[1].png_bytes, dtype=np.uint8), cv2.IMREAD_GRAYSCALE
        )
        image[650:674, 860:940] = self.template
        ok, encoded = cv2.imencode(".png", image)
        self.assertTrue(ok)
        duplicate_frame = make_frame(104, encoded.tobytes())
        self.evidence = SettlementObservation(
            fleet_id=3,
            state="BONUS_ITEMS",
            consecutive_states=("BONUS_ITEMS", "BONUS_ITEMS"),
            evidence="two consecutive in-memory fingerprint matches",
            frames=(self.frames[0], duplicate_frame),
        )
        with self.assertRaisesRegex(RewardClaimLocalizationError, "uniqueness margin"):
            locate_reward_claim_target(
                self.evidence,
                self.template_path,
                calibration_id="synthetic-test-calibration",
                region=(850, 580, 1120, 690),
            )


if __name__ == "__main__":
    unittest.main()
