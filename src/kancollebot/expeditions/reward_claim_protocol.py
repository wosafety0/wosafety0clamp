"""Fail-closed lifecycle protocol for a future reward-claim input boundary.

This module does not capture screens, classify UI, click, or send game input.
The caller must mark the input attempt immediately before making exactly one
ordinary visible-UI input call, then supply independent post-input evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time

from kancollebot.adapters.poi.cdp_input import CapturedFrame
from kancollebot.expeditions.reward_claim_gate import (
    RewardClaimAuthorization,
    RewardClaimAuthorizationError,
    RewardClaimAuthorizationRegistry,
)


class RewardClaimProtocolError(RuntimeError):
    """Reward claim lifecycle cannot safely advance."""


class RewardClaimExecutionPhase(str, Enum):
    READY = "READY"
    BLOCKED_BEFORE_INPUT = "BLOCKED_BEFORE_INPUT"
    CONSUMED_BEFORE_INPUT = "CONSUMED_BEFORE_INPUT"
    INPUT_ATTEMPTED = "INPUT_ATTEMPTED"
    POSTCONDITION_VERIFIED = "POSTCONDITION_VERIFIED"
    UNKNOWN_AFTER_INPUT = "UNKNOWN_AFTER_INPUT"


@dataclass(frozen=True, slots=True)
class RewardClaimPostcondition:
    """Independent classifier output proving the reward screen was left."""

    state: str
    reward_claimed: bool
    evidence: str
    frames: tuple[CapturedFrame, CapturedFrame]

    def __post_init__(self) -> None:
        if not isinstance(self.state, str) or not self.state.strip():
            raise ValueError("postcondition state must be a non-empty string")
        if self.state == "BONUS_ITEMS":
            raise ValueError("postcondition must prove BONUS_ITEMS is no longer visible")
        if self.reward_claimed is not True:
            raise ValueError("postcondition must explicitly prove reward claimed")
        if not isinstance(self.evidence, str) or not self.evidence.strip():
            raise ValueError("independent postcondition evidence is required")
        if (
            not isinstance(self.frames, tuple)
            or len(self.frames) != 2
            or any(not isinstance(frame, CapturedFrame) for frame in self.frames)
        ):
            raise ValueError("postcondition requires exactly two captured frames")
        first, second = self.frames
        if (
            first.frame_id < 1
            or second.frame_id <= first.frame_id
            or first.sha256 == second.sha256
            or first.captured_monotonic_ns >= second.captured_monotonic_ns
        ):
            raise ValueError("postcondition frames must be distinct and strictly increasing")


class RewardClaimExecutionProtocol:
    """One-shot state ledger; deliberately contains no input executor."""

    def __init__(self, registry: RewardClaimAuthorizationRegistry) -> None:
        if not isinstance(registry, RewardClaimAuthorizationRegistry):
            raise TypeError("registry must be a RewardClaimAuthorizationRegistry")
        self._registry = registry
        self._phase = RewardClaimExecutionPhase.READY
        self._authorization: RewardClaimAuthorization | None = None
        self._input_attempted_monotonic_ns: int | None = None
        self._postcondition: RewardClaimPostcondition | None = None

    @property
    def phase(self) -> RewardClaimExecutionPhase:
        return self._phase

    @property
    def postcondition(self) -> RewardClaimPostcondition | None:
        return self._postcondition

    def consume_before_input(
        self,
        authorization: RewardClaimAuthorization,
        *,
        current_frame_id: int,
        current_frame_sha256: str,
        now_monotonic_ns: int | None = None,
    ) -> None:
        """Validate the exact authorized frame and atomically consume once.

        This must be the final protocol operation before begin_input_attempt().
        It does not itself send input.
        """
        if self._phase is not RewardClaimExecutionPhase.READY:
            raise RewardClaimProtocolError("protocol is one-shot and is not READY")
        now = time.monotonic_ns() if now_monotonic_ns is None else now_monotonic_ns
        try:
            if not isinstance(authorization, RewardClaimAuthorization):
                raise RewardClaimAuthorizationError("authorization object is invalid")
            authorization.assert_same_frame(
                frame_id=current_frame_id,
                frame_sha256=current_frame_sha256,
                now_monotonic_ns=now,
            )
            self._registry.consume(authorization, now_monotonic_ns=now)
        except Exception as exc:
            self._phase = RewardClaimExecutionPhase.BLOCKED_BEFORE_INPUT
            raise RewardClaimProtocolError(
                f"authorization rejected before input: {type(exc).__name__}"
            ) from exc
        self._authorization = authorization
        self._phase = RewardClaimExecutionPhase.CONSUMED_BEFORE_INPUT

    def begin_input_attempt(self, *, now_monotonic_ns: int | None = None) -> None:
        """Mark the single external input attempt immediately before calling it.

        Call this before the external input function. If that function raises or
        its outcome is ambiguous, do not retry; call mark_unknown_after_input().
        """
        if self._phase is not RewardClaimExecutionPhase.CONSUMED_BEFORE_INPUT:
            raise RewardClaimProtocolError("input attempt requires CONSUMED_BEFORE_INPUT")
        now = time.monotonic_ns() if now_monotonic_ns is None else now_monotonic_ns
        assert self._authorization is not None
        if (
            isinstance(now, bool)
            or not isinstance(now, int)
            or now < self._authorization.issued_monotonic_ns
            or now > self._authorization.expires_monotonic_ns
        ):
            self._phase = RewardClaimExecutionPhase.BLOCKED_BEFORE_INPUT
            raise RewardClaimProtocolError("authorization expired before the input attempt")
        self._input_attempted_monotonic_ns = now
        self._phase = RewardClaimExecutionPhase.INPUT_ATTEMPTED

    def confirm_postcondition(
        self,
        evidence: RewardClaimPostcondition,
        *,
        now_monotonic_ns: int | None = None,
    ) -> RewardClaimPostcondition:
        """Accept only two independent frames captured after the input attempt."""
        if self._phase is not RewardClaimExecutionPhase.INPUT_ATTEMPTED:
            raise RewardClaimProtocolError("postcondition requires INPUT_ATTEMPTED")
        now = time.monotonic_ns() if now_monotonic_ns is None else now_monotonic_ns
        try:
            if not isinstance(evidence, RewardClaimPostcondition):
                raise ValueError("structured reward postcondition is required")
            assert self._input_attempted_monotonic_ns is not None
            first, second = evidence.frames
            if (
                first.captured_monotonic_ns <= self._input_attempted_monotonic_ns
                or second.captured_monotonic_ns <= self._input_attempted_monotonic_ns
            ):
                raise ValueError("postcondition frames must be captured after the input attempt")
            if (
                isinstance(now, bool)
                or not isinstance(now, int)
                or now < second.captured_monotonic_ns
            ):
                raise ValueError("postcondition verification clock is invalid")
        except Exception as exc:
            self._phase = RewardClaimExecutionPhase.UNKNOWN_AFTER_INPUT
            raise RewardClaimProtocolError(
                f"reward outcome is unknown after input: {type(exc).__name__}"
            ) from exc
        self._postcondition = evidence
        self._phase = RewardClaimExecutionPhase.POSTCONDITION_VERIFIED
        return evidence

    def mark_unknown_after_input(self) -> None:
        """Record an ambiguous/failed input or missing postcondition; never retry."""
        if self._phase is not RewardClaimExecutionPhase.INPUT_ATTEMPTED:
            raise RewardClaimProtocolError("UNKNOWN_AFTER_INPUT requires INPUT_ATTEMPTED")
        self._phase = RewardClaimExecutionPhase.UNKNOWN_AFTER_INPUT
