"""Prepare a non-executable reward authorization from explicit live calibration.

This module never sends input. Environment configuration is opt-in and must
include the exact approved template digest, calibration ID, and bounded region.
"""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
from typing import Mapping

from kancollebot.expeditions.continuous_dispatch_controller import UiStateEvidence
from kancollebot.expeditions.reward_claim_gate import (
    RewardClaimAuthorization,
    RewardClaimGate,
)
from kancollebot.expeditions.reward_claim_locator import (
    RewardClaimLocalizationError,
    locate_reward_claim_target,
)
from kancollebot.expeditions.settlement_observer import SettlementObservation


class RewardClaimPreparationError(RuntimeError):
    """Reward authorization cannot be prepared from explicit calibration."""


_ENV_KEYS = (
    "KANCOLLEBOT_BONUS_CLAIM_TEMPLATE_PATH",
    "KANCOLLEBOT_BONUS_CLAIM_CALIBRATION_ID",
    "KANCOLLEBOT_BONUS_CLAIM_APPROVED_TEMPLATE_SHA256",
    "KANCOLLEBOT_BONUS_CLAIM_REGION",
)


def _parse_region(value: str) -> tuple[int, int, int, int]:
    parts = value.split(",")
    if len(parts) != 4:
        raise RewardClaimPreparationError(
            "KANCOLLEBOT_BONUS_CLAIM_REGION must be left,top,right,bottom"
        )
    try:
        values = tuple(int(part.strip(), 10) for part in parts)
    except (TypeError, ValueError) as exc:
        raise RewardClaimPreparationError(
            "reward claim region must contain four base-10 integers"
        ) from exc
    left, top, right, bottom = values
    if left < 0 or top < 0 or right <= left or bottom <= top:
        raise RewardClaimPreparationError("reward claim region bounds are invalid")
    if right > 1200 or bottom > 720:
        raise RewardClaimPreparationError("reward claim region exceeds the 1200x720 viewport")
    return values


class RewardClaimPreparer:
    """Create an authorization only; intentionally exposes no click operation."""

    def __init__(
        self,
        *,
        template_path: str | Path,
        calibration_id: str,
        approved_template_sha256: str,
        region: tuple[int, int, int, int],
    ) -> None:
        path = Path(template_path)
        if path.name != "bonus_claim_button.png" or not path.is_file():
            raise RewardClaimPreparationError(
                "an existing dedicated bonus_claim_button.png template is required"
            )
        if (
            not isinstance(calibration_id, str)
            or not calibration_id.strip()
            or not isinstance(approved_template_sha256, str)
            or len(approved_template_sha256) != 64
            or any(char not in "0123456789abcdef" for char in approved_template_sha256)
        ):
            raise RewardClaimPreparationError(
                "calibration ID and lowercase approved template SHA-256 are required"
            )
        if (
            not isinstance(region, tuple)
            or len(region) != 4
            or any(isinstance(value, bool) or not isinstance(value, int) for value in region)
        ):
            raise RewardClaimPreparationError("region must be a four-integer tuple")
        left, top, right, bottom = region
        if left < 0 or top < 0 or right <= left or bottom <= top or right > 1200 or bottom > 720:
            raise RewardClaimPreparationError("region must be bounded by the 1200x720 viewport")
        try:
            actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise RewardClaimPreparationError("reward template cannot be read") from exc
        if actual_digest != approved_template_sha256:
            raise RewardClaimPreparationError(
                "reward template bytes do not match the explicitly approved SHA-256"
            )
        self._template_path = path
        self._calibration_id = calibration_id
        self._region = region
        self._gate = RewardClaimGate({approved_template_sha256: calibration_id})

    def prepare(
        self, evidence: SettlementObservation | UiStateEvidence
    ) -> RewardClaimAuthorization:
        try:
            target = locate_reward_claim_target(
                evidence,
                self._template_path,
                calibration_id=self._calibration_id,
                region=self._region,
            )
            return self._gate.authorize(target)
        except (RewardClaimLocalizationError, ValueError, TypeError) as exc:
            raise RewardClaimPreparationError(
                f"reward claim remains unauthorized: {type(exc).__name__}"
            ) from exc


def build_reward_claim_preparer(
    environ: Mapping[str, str] | None = None,
) -> RewardClaimPreparer | None:
    """Load opt-in calibration; return None only when all four settings are absent."""
    values = dict(os.environ if environ is None else environ)
    configured = [bool(values.get(key, "").strip()) for key in _ENV_KEYS]
    if not any(configured):
        return None
    if not all(configured):
        missing = [
            key for key, present in zip(_ENV_KEYS, configured) if not present
        ]
        raise RewardClaimPreparationError(
            "incomplete reward calibration configuration: " + ", ".join(missing)
        )
    try:
        region = _parse_region(values[_ENV_KEYS[3]])
        return RewardClaimPreparer(
            template_path=values[_ENV_KEYS[0]],
            calibration_id=values[_ENV_KEYS[1]],
            approved_template_sha256=values[_ENV_KEYS[2]],
            region=region,
        )
    except RewardClaimPreparationError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise RewardClaimPreparationError(
            f"reward calibration configuration is invalid: {type(exc).__name__}"
        ) from exc
