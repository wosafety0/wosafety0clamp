from __future__ import annotations

import hashlib
import tempfile
import time
import unittest
from pathlib import Path

from kancollebot.expeditions.reward_claim_preparation import (
    RewardClaimPreparationError,
    RewardClaimPreparer,
    _parse_region,
    build_reward_claim_preparer,
)


class RewardClaimPreparationConfigTests(unittest.TestCase):
    def test_no_settings_disables_preparation(self):
        self.assertIsNone(build_reward_claim_preparer({}))

    def test_partial_settings_fail_closed(self):
        with self.assertRaisesRegex(RewardClaimPreparationError, "incomplete"):
            build_reward_claim_preparer({
                "KANCOLLEBOT_BONUS_CLAIM_TEMPLATE_PATH": "bonus_claim_button.png",
            })

    def test_region_requires_four_bounded_integers(self):
        self.assertEqual(_parse_region("800,500,1150,700"), (800, 500, 1150, 700))
        for invalid in ("1,2,3", "-1,0,10,10", "0,0,1201,720", "0,0,10,x"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(RewardClaimPreparationError):
                    _parse_region(invalid)

    def test_template_digest_must_match_explicit_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bonus_claim_button.png"
            payload = b"template fixture bytes"
            path.write_bytes(payload)
            with self.assertRaisesRegex(RewardClaimPreparationError, "do not match"):
                RewardClaimPreparer(
                    template_path=path,
                    calibration_id="reviewed-calibration",
                    approved_template_sha256=hashlib.sha256(b"other bytes").hexdigest(),
                    region=(800, 500, 1150, 700),
                )

    def test_requires_dedicated_template_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "other.png"
            path.write_bytes(b"fixture")
            with self.assertRaisesRegex(RewardClaimPreparationError, "dedicated"):
                RewardClaimPreparer(
                    template_path=path,
                    calibration_id="reviewed-calibration",
                    approved_template_sha256=hashlib.sha256(b"fixture").hexdigest(),
                    region=(800, 500, 1150, 700),
                )


    def test_preparer_builds_frame_bound_authorization_from_production_evidence(self):
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("optional vision dependencies are unavailable")
        from kancollebot.adapters.poi.cdp_input import CapturedFrame
        from kancollebot.expeditions.continuous_dispatch_controller import UiStateEvidence

        with tempfile.TemporaryDirectory() as directory:
            template_path = Path(directory) / "bonus_claim_button.png"
            rng = np.random.default_rng(20261104)
            template = rng.integers(0, 255, size=(24, 80), dtype=np.uint8)
            ok, encoded = cv2.imencode(".png", template)
            self.assertTrue(ok)
            template_bytes = encoded.tobytes()
            template_path.write_bytes(template_bytes)

            first_image = rng.integers(0, 255, size=(720, 1200), dtype=np.uint8)
            ok, first_encoded = cv2.imencode(".png", first_image)
            self.assertTrue(ok)
            first_png = first_encoded.tobytes()

            second_image = rng.integers(0, 255, size=(720, 1200), dtype=np.uint8)
            second_image[610:634, 940:1020] = template
            ok, second_encoded = cv2.imencode(".png", second_image)
            self.assertTrue(ok)
            second_png = second_encoded.tobytes()

            captured_at = time.monotonic_ns()
            first = CapturedFrame(301, captured_at - 1_000_000, first_png, hashlib.sha256(first_png).hexdigest())
            second = CapturedFrame(302, captured_at - 500_000, second_png, hashlib.sha256(second_png).hexdigest())
            evidence = UiStateEvidence(
                state="BONUS_ITEMS",
                consecutive_states=("BONUS_ITEMS", "BONUS_ITEMS"),
                evidence="two independent production visual checks",
                frames=(first, second),
            )
            digest = hashlib.sha256(template_bytes).hexdigest()
            preparer = RewardClaimPreparer(
                template_path=template_path,
                calibration_id="synthetic-reviewed-calibration",
                approved_template_sha256=digest,
                region=(850, 580, 1120, 690),
            )
            authorization = preparer.prepare(evidence)
            self.assertEqual(authorization.frame_id, second.frame_id)
            self.assertEqual(authorization.frame_sha256, second.sha256)
            self.assertEqual(authorization.target_center, (980, 622))
            self.assertFalse(hasattr(authorization, "click"))
            self.assertFalse(hasattr(authorization, "execute"))

    def test_factory_requires_all_four_explicit_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bonus_claim_button.png"
            payload = b"template fixture bytes"
            path.write_bytes(payload)
            values = {
                "KANCOLLEBOT_BONUS_CLAIM_TEMPLATE_PATH": str(path),
                "KANCOLLEBOT_BONUS_CLAIM_CALIBRATION_ID": "reviewed-calibration",
                "KANCOLLEBOT_BONUS_CLAIM_APPROVED_TEMPLATE_SHA256": hashlib.sha256(payload).hexdigest(),
                "KANCOLLEBOT_BONUS_CLAIM_REGION": "800,500,1150,700",
            }
            preparer = build_reward_claim_preparer(values)
            self.assertIsInstance(preparer, RewardClaimPreparer)


if __name__ == "__main__":
    unittest.main()
