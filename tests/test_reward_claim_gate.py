import hashlib
import unittest

from kancollebot.adapters.poi.cdp_input import CapturedFrame

from kancollebot.expeditions.reward_claim_gate import (
    RewardClaimAuthorizationError,
    RewardClaimGate,
    RewardClaimTarget,
)


TEMPLATE_HASH = "a" * 64
FRAME_HASH = "b" * 64
CALIBRATION_ID = "live-poi-reward-button-v1"


def make_frame(frame_id: int, payload: bytes) -> CapturedFrame:
    return CapturedFrame(
        frame_id,
        frame_id * 1000,
        payload,
        hashlib.sha256(payload).hexdigest(),
    )


FIRST_FRAME = make_frame(10, b"\\x89PNG\\r\\n\\x1a\\nstate-one")
SECOND_FRAME = make_frame(12, b"\\x89PNG\\r\\n\\x1a\\nstate-two")


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
        "state_frame_ids": (10, 12),
        "state_frame_sha256s": (FIRST_FRAME.sha256, SECOND_FRAME.sha256),
        "frame": SECOND_FRAME,
        "state_frames": (FIRST_FRAME, SECOND_FRAME),
    }
    values.update(changes)
    return RewardClaimTarget(**values)


class RewardClaimGateTests(unittest.TestCase):
    def test_rejects_non_string_fingerprints_and_calibration_map_keys(self):
        with self.assertRaisesRegex(ValueError, "fingerprints"):
            target(template_sha256=None)
        with self.assertRaisesRegex(ValueError, "fingerprints"):
            target(frame_sha256=None)
        with self.assertRaisesRegex(ValueError, "approved calibrations"):
            RewardClaimGate({None: CALIBRATION_ID})

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

    def test_rejects_reused_or_non_increasing_state_frame_ids(self):
        with self.assertRaisesRegex(ValueError, "distinct, increasing"):
            target(state_frame_ids=(12, 12))
        with self.assertRaisesRegex(ValueError, "distinct, increasing"):
            target(state_frame_ids=(13, 12))

    def test_rejects_reused_state_frame_fingerprints(self):
        with self.assertRaisesRegex(ValueError, "two distinct state frame"):
            target(state_frame_sha256s=(FRAME_HASH, FRAME_HASH))

    def test_target_must_be_bound_to_second_verified_state_frame(self):
        with self.assertRaisesRegex(ValueError, "second verified state frame"):
            target(frame_id=11)
        with self.assertRaisesRegex(ValueError, "second verified state frame"):
            target(frame_sha256="d" * 64)


    def test_rejects_metadata_not_bound_to_retained_captured_frames(self):
        with self.assertRaisesRegex(ValueError, "metadata must match"):
            target(frame_id=11)
        with self.assertRaisesRegex(ValueError, "metadata must match"):
            target(frame_sha256="d" * 64)

    def test_target_must_retain_the_identical_second_frame_object(self):
        duplicate = make_frame(12, b"\\x89PNG\\r\\n\\x1a\\nstate-two")
        with self.assertRaisesRegex(ValueError, "identical second"):
            target(frame=duplicate)

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
