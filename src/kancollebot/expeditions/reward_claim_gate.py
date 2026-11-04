"""Fail-closed authorization for a uniquely localized expedition reward claim.

This module never clicks. It produces a short-lived authorization only when a
caller supplies an explicitly approved live-calibration fingerprint and a
unique target from the exact current BONUS_ITEMS frame.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Mapping

from kancollebot.adapters.poi.cdp_input import CapturedFrame


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_APPROVED_TARGET = "bonus_claim_button.png"
_MIN_SCORE = 0.96
_MIN_MARGIN = 0.10
_DEFAULT_AUTHORIZATION_TTL_NS = 2_000_000_000


class RewardClaimAuthorizationError(RuntimeError):
    """Reward target cannot be uniquely and freshly authorized."""


@dataclass(frozen=True, slots=True)
class RewardClaimTarget:
    template_name: str
    template_sha256: str
    calibration_id: str
    frame_id: int
    frame_sha256: str
    viewport_width: int
    viewport_height: int
    left: int
    top: int
    right: int
    bottom: int
    score: float
    second_best_score: float
    candidate_count: int
    consecutive_states: tuple[str, str]
    state_frame_ids: tuple[int, int]
    state_frame_sha256s: tuple[str, str]
    frame: CapturedFrame
    state_frames: tuple[CapturedFrame, CapturedFrame]

    def __post_init__(self) -> None:
        if self.template_name != _APPROVED_TARGET:
            raise ValueError("only bonus_claim_button.png can identify a reward claim target")
        if (
            not isinstance(self.template_sha256, str)
            or not isinstance(self.frame_sha256, str)
            or not _SHA256.fullmatch(self.template_sha256)
            or not _SHA256.fullmatch(self.frame_sha256)
        ):
            raise ValueError("template and frame fingerprints must be lowercase SHA-256")
        if not isinstance(self.calibration_id, str) or not self.calibration_id.strip():
            raise ValueError("a live calibration identifier is required")
        integer_fields = (
            self.frame_id, self.viewport_width, self.viewport_height,
            self.left, self.top, self.right, self.bottom, self.candidate_count,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in integer_fields):
            raise ValueError("frame, viewport, bounds, and candidate count must be integers")
        if self.frame_id < 1 or self.viewport_width < 1 or self.viewport_height < 1:
            raise ValueError("frame and viewport dimensions must be positive")
        if not (0 <= self.left < self.right <= self.viewport_width):
            raise ValueError("target horizontal bounds are outside the current viewport")
        if not (0 <= self.top < self.bottom <= self.viewport_height):
            raise ValueError("target vertical bounds are outside the current viewport")
        if self.candidate_count != 1:
            raise ValueError("reward target must be unique in the current frame")
        if (
            isinstance(self.score, bool) or not isinstance(self.score, (int, float))
            or isinstance(self.second_best_score, bool)
            or not isinstance(self.second_best_score, (int, float))
        ):
            raise ValueError("template scores must be numeric")
        if not (0.0 <= self.second_best_score <= 1.0 and 0.0 <= self.score <= 1.0):
            raise ValueError("template scores must be normalized")
        if self.score < _MIN_SCORE or self.score - self.second_best_score < _MIN_MARGIN:
            raise ValueError("reward target confidence or uniqueness margin is insufficient")
        if self.consecutive_states != ("BONUS_ITEMS", "BONUS_ITEMS"):
            raise ValueError("two consecutive BONUS_ITEMS state checks are required")
        if (
            not isinstance(self.state_frame_ids, tuple)
            or len(self.state_frame_ids) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) for value in self.state_frame_ids)
            or self.state_frame_ids[0] < 1
            or self.state_frame_ids[1] <= self.state_frame_ids[0]
        ):
            raise ValueError("two distinct, increasing state frame IDs are required")
        if (
            not isinstance(self.state_frame_sha256s, tuple)
            or len(self.state_frame_sha256s) != 2
            or any(not isinstance(value, str) or not _SHA256.fullmatch(value) for value in self.state_frame_sha256s)
            or self.state_frame_sha256s[0] == self.state_frame_sha256s[1]
        ):
            raise ValueError("two distinct state frame SHA-256 fingerprints are required")
        if self.frame_id != self.state_frame_ids[1]:
            raise ValueError("target localization must use the second verified state frame")
        if self.frame_sha256 != self.state_frame_sha256s[1]:
            raise ValueError("target frame fingerprint must match the second verified state frame")
        if (
            not isinstance(self.state_frames, tuple)
            or len(self.state_frames) != 2
            or any(not isinstance(item, CapturedFrame) for item in self.state_frames)
            or not isinstance(self.frame, CapturedFrame)
        ):
            raise ValueError("reward target must retain the exact captured state frame objects")
        first_frame, second_frame = self.state_frames
        if (
            first_frame.frame_id != self.state_frame_ids[0]
            or second_frame.frame_id != self.state_frame_ids[1]
            or first_frame.sha256 != self.state_frame_sha256s[0]
            or second_frame.sha256 != self.state_frame_sha256s[1]
        ):
            raise ValueError("state frame metadata must match the retained CapturedFrame objects")
        if (
            first_frame.frame_id >= second_frame.frame_id
            or first_frame.captured_monotonic_ns >= second_frame.captured_monotonic_ns
            or first_frame.sha256 == second_frame.sha256
        ):
            raise ValueError("retained state frames must be distinct and strictly increasing")
        if self.frame is not second_frame:
            raise ValueError("target must retain the identical second verified CapturedFrame object")


@dataclass(frozen=True, slots=True)
class RewardClaimAuthorization:
    frame_id: int
    frame_sha256: str
    calibration_id: str
    target_center: tuple[int, int]
    target_bounds: tuple[int, int, int, int]
    template_sha256: str
    issued_monotonic_ns: int
    expires_monotonic_ns: int

    def assert_same_frame(
        self,
        *,
        frame_id: int,
        frame_sha256: str,
        now_monotonic_ns: int | None = None,
    ) -> None:
        """Reject stale authorization or any frame change before a future action."""
        now = time.monotonic_ns() if now_monotonic_ns is None else now_monotonic_ns
        if (
            isinstance(now, bool)
            or not isinstance(now, int)
            or now < self.issued_monotonic_ns
            or now > self.expires_monotonic_ns
        ):
            raise RewardClaimAuthorizationError(
                "reward authorization expired; recapture and reauthorize"
            )
        if frame_id != self.frame_id or frame_sha256 != self.frame_sha256:
            raise RewardClaimAuthorizationError(
                "visible frame changed after reward target localization; recapture and reauthorize"
            )


class RewardClaimGate:
    """Issue non-executable, frame-bound claim authorization from approved calibration."""

    def __init__(
        self,
        approved_calibrations: Mapping[str, str] | None = None,
        *,
        authorization_ttl_ns: int = _DEFAULT_AUTHORIZATION_TTL_NS,
    ) -> None:
        if (
            isinstance(authorization_ttl_ns, bool)
            or not isinstance(authorization_ttl_ns, int)
            or authorization_ttl_ns <= 0
        ):
            raise ValueError("authorization TTL must be a positive integer number of nanoseconds")
        self._authorization_ttl_ns = authorization_ttl_ns
        approved = dict(approved_calibrations or {})
        if any(
            not isinstance(template_hash, str)
            or not _SHA256.fullmatch(template_hash)
            or not isinstance(calibration_id, str)
            or not calibration_id.strip()
            for template_hash, calibration_id in approved.items()
        ):
            raise ValueError("approved calibrations must map template SHA-256 to calibration IDs")
        self._approved = approved

    def authorize(
        self,
        target: RewardClaimTarget,
        *,
        now_monotonic_ns: int | None = None,
    ) -> RewardClaimAuthorization:
        if not isinstance(target, RewardClaimTarget):
            raise TypeError("target must be a RewardClaimTarget")
        approved_id = self._approved.get(target.template_sha256)
        if approved_id is None or approved_id != target.calibration_id:
            raise RewardClaimAuthorizationError(
                "reward target template has no matching approved live Poi calibration"
            )
        now = time.monotonic_ns() if now_monotonic_ns is None else now_monotonic_ns
        if isinstance(now, bool) or not isinstance(now, int):
            raise RewardClaimAuthorizationError("authorization clock must be monotonic nanoseconds")
        if now < target.frame.captured_monotonic_ns:
            raise RewardClaimAuthorizationError("target frame timestamp is later than authorization time")
        if now - target.frame.captured_monotonic_ns > self._authorization_ttl_ns:
            raise RewardClaimAuthorizationError("target frame is stale; recapture before authorization")
        return RewardClaimAuthorization(
            frame_id=target.frame_id,
            frame_sha256=target.frame_sha256,
            calibration_id=target.calibration_id,
            target_center=(
                (target.left + target.right) // 2,
                (target.top + target.bottom) // 2,
            ),
            target_bounds=(target.left, target.top, target.right, target.bottom),
            template_sha256=target.template_sha256,
            issued_monotonic_ns=now,
            expires_monotonic_ns=now + self._authorization_ttl_ns,
        )
