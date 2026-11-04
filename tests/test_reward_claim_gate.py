import unittest

from kancollebot.expeditions.reward_claim_gate import (
    RewardClaimAuthorizationError,
    RewardClaimGate,
    RewardClaimTarget,
)


TEMPLATE_HASH = "a" * 64
FRAME_HASH = "b" * 64
CALIBRATION_ID = "live-poi-reward-button-v1"


def target(**changes):
    values = {
        "template_name": "bonus_claim_button.png",
        "template_sha256": TEMPLATE_HASH,
        "calibration_id": CALIBRATION_ID,
        "frame_id": 12,
        "frame_sha256": FRAME_HASH,
        "viewport_width": 1200,
        "viewport_height": 720,
        "left": 900,
        "top": 620,
        "right": 1040,
        "bottom": 680,
        "score": 0.99,
        "second_best_score": 0.70,
        "candidate_count": 1,
        "consecutive_states": ("BONUS_ITEMS", "BONUS_ITEMS"),
    }
    values.update(changes)
    return RewardClaimTarget(**values)


class RewardClaimGateTests(unittest.TestCase):
    def test_default_gate_refuses_without_approved_live_calibration(self):
        with self.assertRaisesRegex(RewardClaimAuthorizationError, "no matching approved"):
            RewardClaimGate().authorize(target())

    def test_approved_unique_target_produces_frame_bound_authorization(self):
        gate = RewardClaimGate({TEMPLATE_HASH: CALIBRATION_ID})
        authorization = gate.authorize(target())
        self.assertEqual(authorization.frame_id, 12)
        self.assertEqual(authorization.frame_sha256, FRAME_HASH)
        self.assertEqual(authorization.target_center, (970, 650))
        self.assertEqual(authorization.target_bounds, (900, 620, 1040, 680))

    def test_changed_frame_invalidates_authorization(self):
        authorization = RewardClaimGate({TEMPLATE_HASH: CALIBRATION_ID}).authorize(target())
        with self.assertRaisesRegex(RewardClaimAuthorizationError, "frame changed"):
            authorization.assert_same_frame(frame_id=13, frame_sha256=FRAME_HASH)
        with self.assertRaisesRegex(RewardClaimAuthorizationError, "frame changed"):
            authorization.assert_same_frame(frame_id=12, frame_sha256="c" * 64)

    def test_rejects_wrong_target_asset(self):
        with self.assertRaisesRegex(ValueError, "only bonus_claim_button"):
            target(template_name="result_next_button.png")

    def test_rejects_non_unique_target(self):
        with self.assertRaisesRegex(ValueError, "must be unique"):
            target(candidate_count=2)

    def test_rejects_low_confidence_or_weak_margin(self):
        with self.assertRaisesRegex(ValueError, "confidence or uniqueness"):
            target(score=0.95)
        with self.assertRaisesRegex(ValueError, "confidence or uniqueness"):
            target(second_best_score=0.95)

    def test_rejects_target_outside_viewport(self):
        with self.assertRaisesRegex(ValueError, "outside the current viewport"):
            target(right=1201)

    def test_rejects_missing_two_frame_bonus_state(self):
        with self.assertRaisesRegex(ValueError, "two consecutive BONUS_ITEMS"):
            target(consecutive_states=("BONUS_ITEMS", "RETURN_RESULT"))

    def test_rejects_unapproved_calibration_id_for_known_template_hash(self):
        gate = RewardClaimGate({TEMPLATE_HASH: "another-calibration"})
        with self.assertRaisesRegex(RewardClaimAuthorizationError, "no matching approved"):
            gate.authorize(target())

    def test_authorization_does_not_expose_a_click_operation(self):
        authorization = RewardClaimGate({TEMPLATE_HASH: CALIBRATION_ID}).authorize(target())
        self.assertFalse(hasattr(authorization, "click"))
        self.assertFalse(hasattr(authorization, "execute"))


if __name__ == "__main__":
    unittest.main()
