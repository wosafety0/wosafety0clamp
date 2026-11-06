"""Bounded visual target localization for small, reversible UI controls.

This module is an explicit alternative to recorded coordinates. It accepts only
an in-memory 1200x720 PNG and a caller-supplied bounded region; ambiguity fails
closed. Screenshot bytes are never written.
"""
from __future__ import annotations

import base64
import hashlib
import math
from typing import Final

from .cdp_input import VisualTargetPoint


class VisualTargetError(RuntimeError):
    """A visual target could not be localized unambiguously."""


FLEET_2_FIRST_CHECKBOX_REGION: Final[tuple[int, int, int, int]] = (150, 225, 55, 50)



SUPPLY_FLEET_TAB_REGIONS: Final[dict[int, tuple[int, int, int, int]]] = {
    1: (195, 155, 50, 50),
    2: (240, 155, 50, 50),
    3: (285, 155, 50, 50),
}


def locate_supply_fleet_tab(
    screenshot_base64: str,
    *,
    fleet_index: int,
) -> VisualTargetPoint:
    """Locate one supply fleet tab by its nested rectangular contour in a bounded header region."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise VisualTargetError("OpenCV and NumPy are required for visual target localization") from exc
    if isinstance(fleet_index, bool) or not isinstance(fleet_index, int) or fleet_index not in (1, 2, 3):
        raise VisualTargetError("supply fleet tab index must be 1, 2 or 3")
    region = SUPPLY_FLEET_TAB_REGIONS[fleet_index]
    rx, ry, rw, rh = region
    try:
        png_bytes = base64.b64decode(screenshot_base64, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise VisualTargetError("screenshot payload is not valid base64") from exc
    image = cv2.imdecode(np.frombuffer(png_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape[:2] != (720, 1200):
        raise VisualTargetError("screenshot must decode to the verified 1200x720 viewport")
    gray = cv2.cvtColor(image[ry:ry + rh, rx:rx + rw], cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 25, 90)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        ratio = width / height if height else 0.0
        if not (28 <= width <= 40 and 26 <= height <= 38 and 0.75 <= ratio <= 1.4):
            continue
        if float(cv2.contourArea(contour)) < 250:
            continue
        bounds = (rx + int(x), ry + int(y), int(width), int(height))
        center_x = bounds[0] + bounds[2] / 2.0
        center_y = bounds[1] + bounds[3] / 2.0
        duplicate = next(
            (
                index for index, existing in enumerate(candidates)
                if abs(center_x - (existing[0] + existing[2] / 2.0)) <= 6.0
                and abs(center_y - (existing[1] + existing[3] / 2.0)) <= 6.0
            ),
            None,
        )
        if duplicate is None:
            candidates.append(bounds)
        elif width * height > candidates[duplicate][2] * candidates[duplicate][3]:
            candidates[duplicate] = bounds
    if len(candidates) != 1:
        raise VisualTargetError(
            f"expected exactly one supply fleet tab in bounded region; found {len(candidates)}"
        )
    x, y, width, height = candidates[0]
    return VisualTargetPoint(
        x=x + width / 2.0,
        y=y + height / 2.0,
        label=f"supply-fleet{fleet_index}-tab",
        bounds=(x, y, width, height),
        region=region,
        source_frame_sha256=hashlib.sha256(png_bytes).hexdigest(),
        locator="opencv-canny-tab-v1",
    )

def locate_fleet_supply_all_button(screenshot_base64: str) -> VisualTargetPoint:
    """Locate the single fleet-wide resupply button in its fixed lower-right region."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise VisualTargetError("OpenCV and NumPy are required for visual target localization") from exc
    region = (1050, 590, 145, 60)
    rx, ry, rw, rh = region
    if not isinstance(screenshot_base64, str) or not screenshot_base64:
        raise VisualTargetError("screenshot payload must be a non-empty base64 string")
    try:
        png_bytes = base64.b64decode(screenshot_base64, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise VisualTargetError("screenshot payload is not valid base64") from exc
    image = cv2.imdecode(np.frombuffer(png_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape[:2] != (720, 1200):
        raise VisualTargetError("screenshot must decode to the verified 1200x720 viewport")
    gray = cv2.cvtColor(image[ry:ry + rh, rx:rx + rw], cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 30, 100)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        if not (80 <= width <= 105 and 24 <= height <= 36):
            continue
        if float(cv2.contourArea(contour)) < 300:
            continue
        bounds = (rx + int(x), ry + int(y), int(width), int(height))
        center_x = bounds[0] + bounds[2] / 2.0
        center_y = bounds[1] + bounds[3] / 2.0
        duplicate = next(
            (
                index for index, existing in enumerate(candidates)
                if abs(center_x - (existing[0] + existing[2] / 2.0)) <= 4.0
                and abs(center_y - (existing[1] + existing[3] / 2.0)) <= 4.0
            ),
            None,
        )
        if duplicate is None:
            candidates.append(bounds)
        elif width * height > candidates[duplicate][2] * candidates[duplicate][3]:
            candidates[duplicate] = bounds
    if len(candidates) != 1:
        raise VisualTargetError(
            f"expected exactly one fleet-wide supply button in bounded region; found {len(candidates)}"
        )
    x, y, width, height = candidates[0]
    return VisualTargetPoint(
        x=x + width / 2.0,
        y=y + height / 2.0,
        label="fleet-supply-all-button",
        bounds=(x, y, width, height),
        region=region,
        source_frame_sha256=hashlib.sha256(png_bytes).hexdigest(),
        locator="opencv-canny-button-v1",
    )


def checkbox_mark_pixel_count(
    screenshot_base64: str,
    point: VisualTargetPoint,
    *,
    brightness_threshold: int = 170,
    inset: int = 4,
) -> int:
    """Count bright check-mark pixels inside the detected checkbox interior."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise VisualTargetError("OpenCV and NumPy are required for checkbox mark inspection") from exc
    if not isinstance(point, VisualTargetPoint):
        raise VisualTargetError("checkbox mark inspection requires a visual target point")
    if not 0 <= brightness_threshold <= 255 or inset < 1:
        raise VisualTargetError("brightness threshold or inset is invalid")
    try:
        png_bytes = base64.b64decode(screenshot_base64, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise VisualTargetError("screenshot payload is not valid base64") from exc
    image = cv2.imdecode(np.frombuffer(png_bytes, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None or image.shape != (720, 1200):
        raise VisualTargetError("screenshot must decode to the verified 1200x720 viewport")
    x, y, width, height = point.bounds
    if width <= inset * 2 or height <= inset * 2:
        raise VisualTargetError("checkbox bounds are too small for an interior check")
    interior = image[y + inset:y + height - inset, x + inset:x + width - inset]
    return int(np.count_nonzero(interior > brightness_threshold))


def locate_square_checkbox(
    screenshot_base64: str,
    *,
    region: tuple[int, int, int, int] = FLEET_2_FIRST_CHECKBOX_REGION,
    label: str = "fleet2-ship1-checkbox",
) -> VisualTargetPoint:
    """Locate exactly one square checkbox contour in a bounded screenshot region.

    The caller must independently gate the page state before using the returned
    point. The returned point is bound to the exact PNG frame hash.
    """
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise VisualTargetError("OpenCV and NumPy are required for visual target localization") from exc

    if not isinstance(screenshot_base64, str) or not screenshot_base64:
        raise VisualTargetError("screenshot payload must be a non-empty base64 string")
    if (
        not isinstance(region, tuple)
        or len(region) != 4
        or any(isinstance(value, bool) or not isinstance(value, int) for value in region)
    ):
        raise VisualTargetError("region must be an integer (x, y, width, height) tuple")
    rx, ry, rw, rh = region
    if rx < 0 or ry < 0 or rw <= 0 or rh <= 0 or rx + rw > 1200 or ry + rh > 720:
        raise VisualTargetError("region is outside the verified 1200x720 viewport")
    if not isinstance(label, str) or not label.strip():
        raise VisualTargetError("target label must not be empty")

    try:
        png_bytes = base64.b64decode(screenshot_base64, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise VisualTargetError("screenshot payload is not valid base64") from exc
    image = cv2.imdecode(np.frombuffer(png_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape[:2] != (720, 1200):
        raise VisualTargetError("screenshot must decode to the verified 1200x720 viewport")

    x2, y2 = rx + rw, ry + rh
    gray = cv2.cvtColor(image[ry:y2, rx:x2], cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 35, 110)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    candidates: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        if not (18 <= width <= 26 and 18 <= height <= 27 and abs(width - height) <= 4):
            continue
        if float(cv2.contourArea(contour)) < 100:
            continue
        bounds = (rx + int(x), ry + int(y), int(width), int(height))
        center_x = bounds[0] + bounds[2] / 2.0
        center_y = bounds[1] + bounds[3] / 2.0
        duplicate = next(
            (
                index for index, existing in enumerate(candidates)
                if abs(center_x - (existing[0] + existing[2] / 2.0)) <= 3.0
                and abs(center_y - (existing[1] + existing[3] / 2.0)) <= 3.0
            ),
            None,
        )
        if duplicate is None:
            candidates.append(bounds)
        elif width * height > candidates[duplicate][2] * candidates[duplicate][3]:
            candidates[duplicate] = bounds

    if len(candidates) != 1:
        raise VisualTargetError(
            f"expected exactly one square checkbox in bounded region; found {len(candidates)}"
        )

    x, y, width, height = candidates[0]
    center_x = x + width / 2.0
    center_y = y + height / 2.0
    if not math.isfinite(center_x) or not math.isfinite(center_y):
        raise VisualTargetError("detected target center is invalid")

    return VisualTargetPoint(
        x=center_x,
        y=center_y,
        label=label,
        bounds=(x, y, width, height),
        region=region,
        source_frame_sha256=hashlib.sha256(png_bytes).hexdigest(),
        locator="opencv-canny-square-v1",
    )
