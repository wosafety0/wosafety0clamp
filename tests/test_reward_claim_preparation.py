from __future__ import annotations

import hashlib
import tempfile
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
