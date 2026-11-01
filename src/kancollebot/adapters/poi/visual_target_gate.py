"""Conservative in-memory localization of a visible UI target from two frames."""
from __future__ import annotations

import base64
import math
from dataclasses import dataclass
from pathlib import Path

from .visual_state_gate import VisualStateError, _vision_modules


@dataclass(frozen=True, slots=True)
class LocatedVisualTarget:
    """A visual element localized in two frames; location is not click authorization."""

    x: float
    y: float
    score: float
    template_name: str
    frame_count: int = 2

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in (self.x, self.y, self.score)
        ):
            raise ValueError("visual target coordinates and score must be finite numbers")
        if self.x < 0 or self.y < 0 or not 0 <= self.score <= 1:
            raise ValueError("visual target coordinates or score are outside valid bounds")
        if self.frame_count != 2:
            raise ValueError("visual target must be confirmed by exactly two frames")
        if not isinstance(self.template_name, str) or not self.template_name:
            raise ValueError("visual target template name is required")


def locate_template_in_region(
    screenshot_base64: str,
    template_path: str | Path,
    *,
    region: tuple[int, int, int, int] = (700, 0, 1200, 180),
    minimum_score: float = 0.95,
    minimum_margin: float = 0.08,
    cv2_module=None,
    numpy_module=None,
) -> tuple[float, float, float]:
    """Return (center_x, center_y, score) for one unambiguous match in a bounded ROI."""
    if not isinstance(screenshot_base64, str) or not screenshot_base64:
        raise VisualStateError("empty screenshot payload")
    if (
        not math.isfinite(minimum_score) or not 0 < minimum_score <= 1
        or not math.isfinite(minimum_margin) or not 0 <= minimum_margin <= 1
    ):
        raise ValueError("invalid visual target score thresholds")
    if (
        not isinstance(region, tuple) or len(region) != 4
        or any(isinstance(v, bool) or not isinstance(v, int) for v in region)
    ):
        raise ValueError("region must be an integer (left, top, right, bottom) tuple")
    left, top, right, bottom = region
    if left < 0 or top < 0 or right <= left or bottom <= top:
        raise ValueError("region bounds are invalid")

    cv2_module, numpy_module = _vision_modules(cv2_module, numpy_module)
    try:
        raw = base64.b64decode(screenshot_base64, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise VisualStateError("image payload is not valid base64") from exc
    image = cv2_module.imdecode(
        numpy_module.frombuffer(raw, dtype=numpy_module.uint8),
        cv2_module.IMREAD_GRAYSCALE,
    )
    if image is None:
        raise VisualStateError("image payload is not a decodable image")
    height, width = image.shape[:2]
    if right > width or bottom > height:
        raise VisualStateError("visual target region exceeds screenshot dimensions")

    path = Path(template_path)
    try:
        template_bytes = path.read_bytes()
    except OSError as exc:
        raise VisualStateError(f"visual target template is unavailable: {path.name}") from exc
    template = cv2_module.imdecode(
        numpy_module.frombuffer(template_bytes, dtype=numpy_module.uint8),
        cv2_module.IMREAD_GRAYSCALE,
    )
    if template is None:
        raise VisualStateError("visual target template is not a decodable image")

    template_height, template_width = template.shape[:2]
    roi = image[top:bottom, left:right]
    if template_width > roi.shape[1] or template_height > roi.shape[0]:
        raise VisualStateError("visual target template is larger than its search region")
    matches = cv2_module.matchTemplate(roi, template, cv2_module.TM_CCOEFF_NORMED)
    _, best_score, _, best_location = cv2_module.minMaxLoc(matches)
    if best_score < minimum_score:
        raise VisualStateError(
            f"visual target confidence {best_score:.4f} is below {minimum_score:.4f}"
        )

    second = matches.copy()
    x0, y0 = best_location
    pad_x = max(1, template_width // 2)
    pad_y = max(1, template_height // 2)
    second[
        max(0, y0 - pad_y):min(second.shape[0], y0 + template_height + pad_y),
        max(0, x0 - pad_x):min(second.shape[1], x0 + template_width + pad_x),
    ] = -1.0
    second_score = float(second.max()) if second.size else -1.0
    if second_score >= best_score - minimum_margin:
        raise VisualStateError("visual target has multiple competing matches")

    center_x = left + x0 + template_width / 2
    center_y = top + y0 + template_height / 2
    return float(center_x), float(center_y), float(best_score)


def locate_stable_template_target(
    frame_payloads: tuple[str, str],
    template_path: str | Path,
    *,
    template_name: str = "return_notice.png",
    region: tuple[int, int, int, int] = (700, 0, 1200, 180),
    minimum_score: float = 0.95,
    maximum_drift: float = 8.0,
    cv2_module=None,
    numpy_module=None,
) -> LocatedVisualTarget:
    """Require the same unique target location in two consecutive screenshots."""
    if not isinstance(frame_payloads, tuple) or len(frame_payloads) != 2:
        raise ValueError("exactly two consecutive screenshot payloads are required")
    if not math.isfinite(maximum_drift) or maximum_drift < 0:
        raise ValueError("maximum_drift must be finite and non-negative")
    first = locate_template_in_region(
        frame_payloads[0], template_path, region=region,
        minimum_score=minimum_score, cv2_module=cv2_module, numpy_module=numpy_module,
    )
    second = locate_template_in_region(
        frame_payloads[1], template_path, region=region,
        minimum_score=minimum_score, cv2_module=cv2_module, numpy_module=numpy_module,
    )
    drift = math.hypot(first[0] - second[0], first[1] - second[1])
    if drift > maximum_drift:
        raise VisualStateError(
            f"visual target moved {drift:.1f}px between frames; limit is {maximum_drift:.1f}px"
        )
    return LocatedVisualTarget(
        x=(first[0] + second[0]) / 2,
        y=(first[1] + second[1]) / 2,
        score=min(first[2], second[2]),
        template_name=template_name,
        frame_count=2,
    )
