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
from .visual_fingerprint import VisualFingerprintError, _decode_png_rgba


class VisualTargetError(RuntimeError):
    """A visual target could not be localized unambiguously."""


def require_visual_dependencies() -> None:
    """Fail closed with an actionable message if OpenCV/NumPy are unavailable.

    Real expedition UI clicks (supply, settlement) locate small reversible
    controls through template-free contour matching, which needs cv2 and numpy.
    Call this before opening the game/webview so a missing dependency surfaces
    as a clear startup error instead of a cryptic mid-click failure.
    """
    missing: list[str] = []
    try:
        import cv2  # noqa: F401
    except ImportError:
        missing.append("opencv-python-headless")
    try:
        import numpy  # noqa: F401
    except ImportError:
        missing.append("numpy")
    if missing:
        plural = "dependency" if len(missing) == 1 else "dependencies"
        raise VisualTargetError(
            "Visual expedition UI requires the missing "
            f"{plural} {missing}; install with 'pip install "
            + " ".join(missing)
            + "' (headless OpenCV is sufficient)"
        )


FLEET_2_FIRST_CHECKBOX_REGION: Final[tuple[int, int, int, int]] = (150, 225, 55, 50)



SUPPLY_FLEET_TAB_REGIONS: Final[dict[int, tuple[int, int, int, int]]] = {
    1: (195, 155, 50, 50),
    2: (240, 155, 50, 50),
    3: (285, 155, 50, 50),
    # Fleet 4's tab exists on this account (live 2026-11-06) and the strip is
    # evenly spaced at 45 px, so its bounded region is derived, not measured
    # against a template. There is no SUPPLY_FLEET_4_SELECTED fingerprint yet;
    # see classify_supply_tab_selection for the template-free check that
    # replaces it, and the caveat that it is not live-calibrated.
    4: (330, 155, 50, 50),
}

#: Fleets whose supply tab has a bounded, automatically locatable region.
SUPPLY_TAB_FLEET_IDS: Final[tuple[int, ...]] = tuple(sorted(SUPPLY_FLEET_TAB_REGIONS))


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
    if isinstance(fleet_index, bool) or not isinstance(fleet_index, int) or fleet_index not in SUPPLY_TAB_FLEET_IDS:
        raise VisualTargetError(
            f"supply fleet tab index must be one of {SUPPLY_TAB_FLEET_IDS}"
        )
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


# -- template-free fleets -----------------------------------------------------
#
# Fleet 4 has a supply tab on this account but no captured
# ``SUPPLY_FLEET_4_SELECTED`` fingerprint: producing one needs a live capture
# of that exact screen, which is a real-client operation this change is not
# allowed to claim. Refusing Fleet 4 outright would leave the closed loop
# unable to resupply a fleet it can already dispatch, so the selected tab is
# instead identified *relative to its neighbours*, which needs no template.
#
# What this proves: exactly one tab in the strip is painted in the
# selected style, and it is the requested one.
# What it does not prove: that the game's "selected" style looks the way the
# thresholds below assume on this client. The thresholds are therefore
# deliberately strict and produce UNKNOWN (never a guess) when the picture is
# ambiguous, and the caller compensates with the post-action Poi cross-check.

#: Minimum mean luminance of the selected tab. A selected tab is painted on a
#: lighter plate than its neighbours; anything darker is not "selected".
SUPPLY_TAB_MIN_SELECTED_LUMINANCE: Final[float] = 90.0
#: How much brighter the selected tab must be than the next-brightest one.
#: A single-digit difference is repaint noise, not a selection.
SUPPLY_TAB_MIN_LUMINANCE_MARGIN: Final[float] = 12.0


def _region_mean_luminance(
    width: int, height: int, rgba: bytes, region: tuple[int, int, int, int]
) -> float:
    rx, ry, rw, rh = region
    if rx < 0 or ry < 0 or rx + rw > width or ry + rh > height:
        raise VisualTargetError("tab region lies outside the screenshot viewport")
    total = 0
    count = 0
    for y in range(ry, ry + rh):
        row_start = (y * width + rx) * 4
        for x in range(0, rw):
            offset = row_start + x * 4
            red = rgba[offset]
            green = rgba[offset + 1]
            blue = rgba[offset + 2]
            # Rec. 601 luma: the human-visible brightness of the plate.
            total += (299 * red + 587 * green + 114 * blue) // 1000
            count += 1
    if not count:
        raise VisualTargetError("tab region is empty")
    return total / count


def classify_supply_tab_selection(
    screenshot_base64: str,
    *,
    fleet_ids: tuple[int, ...] = SUPPLY_TAB_FLEET_IDS,
    minimum_luminance: float = SUPPLY_TAB_MIN_SELECTED_LUMINANCE,
    minimum_margin: float = SUPPLY_TAB_MIN_LUMINANCE_MARGIN,
) -> tuple[str, dict[int, float]]:
    """Name the selected supply-fleet tab from relative brightness alone.

    Returns ``("SUPPLY_FLEET_<n>_SELECTED", scores)`` or ``("UNKNOWN", scores)``
    when the brightest tab is not bright enough, or not bright enough *by a
    margin*, to be the selected one. The scores are returned either way so a
    refusal can be explained without re-reading the frame.
    """
    if not fleet_ids:
        raise ValueError("fleet_ids must not be empty")
    for fleet_id in fleet_ids:
        if fleet_id not in SUPPLY_FLEET_TAB_REGIONS:
            raise ValueError(f"fleet {fleet_id} has no bounded supply tab region")
    if not math.isfinite(minimum_luminance) or not math.isfinite(minimum_margin):
        raise ValueError("thresholds must be finite")
    if minimum_margin < 0:
        raise ValueError("minimum_margin must not be negative")

    try:
        width, height, rgba = _decode_png_rgba(screenshot_base64)
    except VisualFingerprintError as exc:
        raise VisualTargetError(str(exc)) from exc
    if (width, height) != (1200, 720):
        raise VisualTargetError("screenshot must be the verified 1200x720 viewport")

    scores = {
        fleet_id: _region_mean_luminance(
            width, height, rgba, SUPPLY_FLEET_TAB_REGIONS[fleet_id]
        )
        for fleet_id in fleet_ids
    }
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_fleet, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    if best_score < minimum_luminance:
        return "UNKNOWN", scores
    if best_score - second_score < minimum_margin:
        return "UNKNOWN", scores
    return f"SUPPLY_FLEET_{best_fleet}_SELECTED", scores
