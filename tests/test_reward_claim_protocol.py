import hashlib
import unittest

from kancollebot.adapters.poi.cdp_input import CapturedFrame
from kancollebot.expeditions.reward_claim_gate import (
    RewardClaimAuthorizationError,
    RewardClaimGate,
    RewardClaimTarget,
)
from kancollebot.expeditions.reward_claim_protocol import (
    RewardClaimExecutionPhase,
    RewardClaimExecutionProtocol,
    RewardClaimPostcondition,
    RewardClaimProtocolError,
)


TEMPLATE_HASH = "a" * 64
CALIBRATION_ID = "offline-test-calibration"


def frame(frame_id: int, timestamp: int, label: bytes) -> CapturedFrame:
    payload = b"\x89PNG\r\n\x1a\n" + label
    return CapturedFrame(frame_id, timestamp, payload, hashlib.sha256(payload).hexdigest())


def make_authorization():
    first = frame(1, 1_000, b"bonus-first")
    second = frame(2, 2_000, b"bonus-second")
    target = RewardClaimTarget(
        template_name="bonus_claim_button.png",
        template_sha256=TEMPLATE_HASH,
        calibration_id=CALIBRATION_ID,
        frame_id=2,
        frame_sha256=second.sha256,
        viewport_width=1200,
        viewport_height=720,
        left=900,
        top=620,
        right=1040,
        bottom=680,
        score=0.99,
        second_best_score=0.70,
        candidate_count=1,
        consecutive_states=("BONUS_ITEMS", "BONUS_ITEMS"),
        state_frame_ids=(1, 2),
        state_frame_sha256s=(first.sha256, second.sha256),
        frame=second,
        state_frames=(first, second),
    )
    gate = RewardClaimGate({TEMPLATE_HASH: CALIBRATION_ID})
    authorization = gate.authorize(target, now_monotonic_ns=2_100)
    return gate, authorization


class RewardClaimProtocolTests(unittest.TestCase):
    def test_consumes_before_external_input_and_verifies_independent_postcondition(self):
        gate, authorization = make_authorization()
        protocol = RewardClaimExecutionProtocol(gate.authorization_registry)
        protocol.consume_before_input(
            authorization,
            current_frame_id=2,
            current_frame_sha256=authorization.frame_sha256,
            now_monotonic_ns=2_200,
        )
        self.assertEqual(protocol.phase, RewardClaimExecutionPhase.CONSUMED_BEFORE_INPUT)
        self.assertEqual(gate.authorization_registry.pending_count, 0)

        # The test records the protocol boundary; it does not simulate a click.
        protocol.begin_input_attempt(now_monotonic_ns=2_300)
        self.assertEqual(protocol.phase, RewardClaimExecutionPhase.INPUT_ATTEMPTED)

        evidence = RewardClaimPostcondition(
            state="HOME_PORT",
            reward_claimed=True,
            evidence="independent two-frame post-input classifier",
            frames=(frame(3, 2_400, b"home-first"), frame(4, 2_500, b"home-second")),
        )
        protocol.confirm_postcondition(evidence, now_monotonic_ns=2_600)
        self.assertEqual(protocol.phase, RewardClaimExecutionPhase.POSTCONDITION_VERIFIED)
        self.assertIs(protocol.postcondition, evidence)

    def test_rejects_changed_frame_before_consumption(self):
        gate, authorization = make_authorization()
        protocol = RewardClaimExecutionProtocol(gate.authorization_registry)
        with self.assertRaisesRegex(RewardClaimProtocolError, "authorization rejected"):
            protocol.consume_before_input(
                authorization,
                current_frame_id=3,
                current_frame_sha256=authorization.frame_sha256,
                now_monotonic_ns=2_200,
            )
        self.assertEqual(protocol.phase, RewardClaimExecutionPhase.BLOCKED_BEFORE_INPUT)
        self.assertEqual(gate.authorization_registry.pending_count, 1)

    def test_rejects_replay_or_second_input_attempt(self):
        gate, authorization = make_authorization()
        protocol = RewardClaimExecutionProtocol(gate.authorization_registry)
        protocol.consume_before_input(
            authorization, current_frame_id=2,
            current_frame_sha256=authorization.frame_sha256, now_monotonic_ns=2_200,
        )
        protocol.begin_input_attempt(now_monotonic_ns=2_300)
        with self.assertRaisesRegex(RewardClaimProtocolError, "one-shot"):
            protocol.consume_before_input(
                authorization, current_frame_id=2,
                current_frame_sha256=authorization.frame_sha256, now_monotonic_ns=2_300,
            )
        with self.assertRaisesRegex(RewardClaimProtocolError, "requires CONSUMED"):
            protocol.begin_input_attempt(now_monotonic_ns=2_300)

    def test_requires_postcondition_frames_newer_than_input_attempt(self):
        gate, authorization = make_authorization()
        protocol = RewardClaimExecutionProtocol(gate.authorization_registry)
        protocol.consume_before_input(
            authorization, current_frame_id=2,
            current_frame_sha256=authorization.frame_sha256, now_monotonic_ns=2_200,
        )
        protocol.begin_input_attempt(now_monotonic_ns=2_300)
        evidence = RewardClaimPostcondition(
            state="HOME_PORT",
            reward_claimed=True,
            evidence="classifier",
            frames=(frame(3, 2_200, b"too-early"), frame(4, 2_500, b"later")),
        )
        with self.assertRaisesRegex(RewardClaimProtocolError, "outcome is unknown"):
            protocol.confirm_postcondition(evidence, now_monotonic_ns=2_600)
        self.assertEqual(protocol.phase, RewardClaimExecutionPhase.UNKNOWN_AFTER_INPUT)

    def test_rejects_bonus_items_as_claimed_postcondition(self):
        with self.assertRaisesRegex(ValueError, "no longer visible"):
            RewardClaimPostcondition(
                state="BONUS_ITEMS", reward_claimed=True, evidence="wrong state",
                frames=(frame(3, 3_000, b"one"), frame(4, 4_000, b"two")),
            )

    def test_missing_postcondition_becomes_unknown_without_retry(self):
        gate, authorization = make_authorization()
        protocol = RewardClaimExecutionProtocol(gate.authorization_registry)
        protocol.consume_before_input(
            authorization, current_frame_id=2,
            current_frame_sha256=authorization.frame_sha256, now_monotonic_ns=2_200,
        )
        protocol.begin_input_attempt(now_monotonic_ns=2_300)
        protocol.mark_unknown_after_input()
        self.assertEqual(protocol.phase, RewardClaimExecutionPhase.UNKNOWN_AFTER_INPUT)
        with self.assertRaisesRegex(RewardClaimProtocolError, "requires INPUT_ATTEMPTED"):
            protocol.mark_unknown_after_input()

    def test_blocks_consumption_when_first_state_frame_is_stale_but_auth_is_unexpired(self):
        gate, authorization = make_authorization()
        protocol = RewardClaimExecutionProtocol(gate.authorization_registry)
        consume_time = 2_000_001_001
        self.assertLess(consume_time, authorization.expires_monotonic_ns)
        with self.assertRaisesRegex(RewardClaimProtocolError, "authorization rejected"):
            protocol.consume_before_input(
                authorization,
                current_frame_id=2,
                current_frame_sha256=authorization.frame_sha256,
                now_monotonic_ns=consume_time,
            )
        self.assertEqual(protocol.phase, RewardClaimExecutionPhase.BLOCKED_BEFORE_INPUT)

    def test_expired_authorization_is_blocked_before_input(self):
        gate, authorization = make_authorization()
        protocol = RewardClaimExecutionProtocol(gate.authorization_registry)
        with self.assertRaisesRegex(RewardClaimProtocolError, "authorization rejected"):
            protocol.consume_before_input(
                authorization, current_frame_id=2,
                current_frame_sha256=authorization.frame_sha256,
                now_monotonic_ns=authorization.expires_monotonic_ns + 1,
            )
        self.assertEqual(protocol.phase, RewardClaimExecutionPhase.BLOCKED_BEFORE_INPUT)


if __name__ == "__main__":
    unittest.main()
