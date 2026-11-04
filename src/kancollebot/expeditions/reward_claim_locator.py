"""Build a reward target only from retained BONUS_ITEMS CapturedFrame evidence.

The locator never clicks. Its search region and approved template are explicit
inputs; missing calibration inputs or ambiguous matches fail closed.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from kancollebot.adapters.poi.cdp_input import CapturedFrame
from kancollebot.adapters.poi.visual_state_gate import (
    _decode_grayscale,
    _vision_modules,
)
from kancollebot.expeditions.continuous_dispatch_controller import UiStateEvidence
from kancollebot.expeditions.reward_claim_gate import RewardClaimTarget
from kancollebot.expeditions.settlement_observer import SettlementObservation


class RewardClaimLocalizationError(RuntimeError):
    """The reward target cannot be uniquely localized from verified evidence."""


def locate_reward_claim_target(
    state_evidence: SettlementObservation | UiStateEvidence,
    template_path: str | Path,
    *,
    calibration_id: str,
    region: tuple[int, int, int, int],
    minimum_score: float = 0.96,
    minimum_margin: float = 0.10,
    cv2_module=None,
    numpy_module=None,
) -> RewardClaimTarget:
    """Locate the unique bonus button in the exact second BONUS_ITEMS frame.

    `state_evidence` must expose state, consecutive_states, and a two-item
    `frames` tuple. The returned target retains the identical second frame
    object; metadata is never accepted as a substitute for that object.
    Region is (left, top, right, bottom) and must come from a reviewed live
    calibration, not an inferred/default coordinate.
    """
    if not isinstance(state_evidence, (SettlementObservation, UiStateEvidence)):
        raise RewardClaimLocalizationError(
            "reward localization requires production UiStateEvidence or SettlementObservation"
        )
    state = state_evidence.state
    consecutive = state_evidence.consecutive_states
    frames = state_evidence.frames
    if state != "BONUS_ITEMS" or consecutive != ("BONUS_ITEMS", "BONUS_ITEMS"):
        raise RewardClaimLocalizationError(
            "reward localization requires two consecutive BONUS_ITEMS state checks"
        )
    if (
        not isinstance(frames, tuple)
        or len(frames) != 2
        or any(not isinstance(frame, CapturedFrame) for frame in frames)
    ):
        raise RewardClaimLocalizationError(
            "reward localization requires two retained CapturedFrame objects"
        )
    first_frame, second_frame = frames
    if (
        first_frame.frame_id >= second_frame.frame_id
        or first_frame.captured_monotonic_ns >= second_frame.captured_monotonic_ns
        or first_frame.sha256 == second_frame.sha256
    ):
        raise RewardClaimLocalizationError(
            "BONUS_ITEMS evidence frames must be distinct and strictly increasing"
        )
    if not isinstance(calibration_id, str) or not calibration_id.strip():
        raise RewardClaimLocalizationError("an explicit live calibration ID is required")
    if (
        not isinstance(region, tuple)
        or len(region) != 4
        or any(isinstance(value, bool) or not isinstance(value, int) for value in region)
    ):
        raise RewardClaimLocalizationError(
            "search region must be an explicit integer (left, top, right, bottom) tuple"
        )
    left, top, right, bottom = region
    if left < 0 or top < 0 or right <= left or bottom <= top:
        raise RewardClaimLocalizationError("search region bounds are invalid")
    if (
        isinstance(minimum_score, bool)
        or not isinstance(minimum_score, (int, float))
        or not math.isfinite(minimum_score)
        or not 0 < minimum_score <= 1
        or isinstance(minimum_margin, bool)
        or not isinstance(minimum_margin, (int, float))
        or not math.isfinite(minimum_margin)
        or not 0 <= minimum_margin <= 1
    ):
        raise RewardClaimLocalizationError("visual confidence thresholds are invalid")

    path = Path(template_path)
    if path.name != "bonus_claim_button.png":
        raise RewardClaimLocalizationError(
            "only the dedicated bonus_claim_button.png asset is accepted"
        )
    try:
        template_bytes = path.read_bytes()
        template_hash = hashlib.sha256(template_bytes).hexdigest()
        cv2_module, numpy_module = _vision_modules(cv2_module, numpy_module)
        image = _decode_grayscale(second_frame.screenshot_base64, cv2_module, numpy_module)
        encoded_template = numpy_module.frombuffer(template_bytes, dtype=numpy_module.uint8)
        template = cv2_module.imdecode(encoded_template, cv2_module.IMREAD_GRAYSCALE)
        if template is None:
            raise RewardClaimLocalizationError("reward button template is not a decodable image")
        height, width = image.shape[:2]
        if (width, height) != (1200, 720):
            raise RewardClaimLocalizationError(
                "reward localization requires the calibrated 1200x720 Poi viewport"
            )
        if right > width or bottom > height:
            raise RewardClaimLocalizationError("search region exceeds the captured viewport")
        template_height, template_width = template.shape[:2]
        roi = image[top:bottom, left:right]
        if template_width > roi.shape[1] or template_height > roi.shape[0]:
            raise RewardClaimLocalizationError("reward template is larger than the search region")
        matches = cv2_module.matchTemplate(
            roi, template, cv2_module.TM_CCOEFF_NORMED
        )
        required_score = max(float(minimum_score), 0.96)
        suppressed = matches.copy()
        pad_x = max(1, template_width // 2)
        pad_y = max(1, template_height // 2)
        candidate_locations: list[tuple[int, int, float]] = []
        while suppressed.size:
            _, candidate_score, _, candidate_location = cv2_module.minMaxLoc(suppressed)
            candidate_score = float(candidate_score)
            if not math.isfinite(candidate_score) or not -1.0 <= candidate_score <= 1.0:
                raise RewardClaimLocalizationError("reward template returned an invalid confidence score")
            if candidate_score < required_score:
                break
            candidate_x, candidate_y = candidate_location
            candidate_locations.append((int(candidate_x), int(candidate_y), candidate_score))
            suppressed[
                max(0, candidate_y - pad_y):min(suppressed.shape[0], candidate_y + template_height + pad_y),
                max(0, candidate_x - pad_x):min(suppressed.shape[1], candidate_x + template_width + pad_x),
            ] = -1.0

        if not candidate_locations:
            raise RewardClaimLocalizationError(
                "reward target confidence or uniqueness margin is insufficient"
            )
        x, y, best_score = candidate_locations[0]
        second_score = candidate_locations[1][2] if len(candidate_locations) > 1 else 0.0
        candidate_count = len(candidate_locations)
        if candidate_count != 1:
            raise RewardClaimLocalizationError(
                "multiple spatially distinct reward target candidates exceed the confidence threshold"
            )
        if best_score - second_score < max(float(minimum_margin), 0.10):
            raise RewardClaimLocalizationError(
                "reward target confidence or uniqueness margin is insufficient"
            )
        target_left = left + int(x)
        target_top = top + int(y)
        return RewardClaimTarget(
            template_name=path.name,
            template_sha256=template_hash,
            calibration_id=calibration_id,
            frame_id=second_frame.frame_id,
            frame_sha256=second_frame.sha256,
            viewport_width=width,
            viewport_height=height,
            left=target_left,
            top=target_top,
            right=target_left + template_width,
            bottom=target_top + template_height,
            score=best_score,
            second_best_score=second_score,
            candidate_count=candidate_count,
            consecutive_states=consecutive,
            state_frame_ids=(first_frame.frame_id, second_frame.frame_id),
            state_frame_sha256s=(first_frame.sha256, second_frame.sha256),
            frame=second_frame,
            state_frames=frames,
        )
    except Exception as exc:
        if isinstance(exc, RewardClaimLocalizationError):
            raise
        raise RewardClaimLocalizationError(
            f"reward target localization failed closed: {type(exc).__name__}"
        ) from exc
