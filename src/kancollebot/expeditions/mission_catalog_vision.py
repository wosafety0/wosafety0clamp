"""Read-only visual recognition adapter for scrollable expedition lists.

This module consumes an in-memory screenshot plus operator-calibrated row and
scrollbar templates. It does not capture screenshots, scroll, click, use OCR,
write image files, or connect to Poi. The scrollbar-to-content proportional
model must be explicitly calibrated before a page can be accepted.
"""
from __future__ import annotations

import base64
import math
from dataclasses import dataclass

from .mission_catalog_scan import (
    ExpeditionListPageObservation,
    MissionRowObservation,
)


class ExpeditionListVisionError(ValueError):
    """A screenshot or visual match is ambiguous or outside the calibrated profile."""


def _validate_region(region: tuple[int, int, int, int], *, label: str, viewport: tuple[int, int]) -> None:
    if (
        not isinstance(region, tuple)
        or len(region) != 4
        or any(isinstance(value, bool) or not isinstance(value, int) for value in region)
    ):
        raise ExpeditionListVisionError(f"{label} must be an integer (x, y, width, height) tuple")
    x, y, width, height = region
    viewport_width, viewport_height = viewport
    if (
        x < 0 or y < 0 or width <= 0 or height <= 0
        or x + width > viewport_width or y + height > viewport_height
    ):
        raise ExpeditionListVisionError(f"{label} is outside the calibrated viewport")


def _validate_score(value: float, *, label: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0.0 <= value <= 1.0
    ):
        raise ExpeditionListVisionError(f"{label} must be finite and in [0, 1]")


@dataclass(frozen=True, slots=True)
class MissionRowTemplate:
    """A cropped, stable mission-number marker and its trusted catalogue label."""

    mission_id: int
    name: str
    png_base64: str

    def __post_init__(self) -> None:
        if isinstance(self.mission_id, bool) or not isinstance(self.mission_id, int) or self.mission_id < 1:
            raise ExpeditionListVisionError("mission template ID must be a positive integer")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ExpeditionListVisionError("mission template name must be non-empty text")
        object.__setattr__(self, "name", self.name.strip())
        if not isinstance(self.png_base64, str) or not self.png_base64:
            raise ExpeditionListVisionError("mission template image must be non-empty base64 text")
        try:
            base64.b64decode(self.png_base64, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise ExpeditionListVisionError("mission template image is not valid base64") from exc


@dataclass(frozen=True, slots=True)
class ExpeditionListVisualProfile:
    """Operator-calibrated geometry and image templates for one Poi viewport."""

    list_region: tuple[int, int, int, int]
    scrollbar_track_region: tuple[int, int, int, int]
    scrollbar_thumb_template_base64: str
    mission_templates: tuple[MissionRowTemplate, ...]
    viewport: tuple[int, int] = (1200, 720)
    minimum_row_score: float = 0.94
    row_ambiguity_margin: float = 0.04
    row_center_tolerance_px: float = 14.0
    minimum_scrollbar_score: float = 0.95
    scrollbar_ambiguity_margin: float = 0.05
    bottom_tolerance_px: float = 2.0
    scrollbar_scale_verified: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.viewport, tuple)
            or len(self.viewport) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in self.viewport)
        ):
            raise ExpeditionListVisionError("viewport must contain two positive integer dimensions")
        _validate_region(self.list_region, label="list_region", viewport=self.viewport)
        _validate_region(self.scrollbar_track_region, label="scrollbar_track_region", viewport=self.viewport)
        if not isinstance(self.scrollbar_thumb_template_base64, str) or not self.scrollbar_thumb_template_base64:
            raise ExpeditionListVisionError("scrollbar thumb template must be non-empty base64 text")
        try:
            base64.b64decode(self.scrollbar_thumb_template_base64, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise ExpeditionListVisionError("scrollbar thumb template is not valid base64") from exc
        if not isinstance(self.mission_templates, tuple) or not self.mission_templates:
            raise ExpeditionListVisionError("at least one mission row template is required")
        if any(not isinstance(item, MissionRowTemplate) for item in self.mission_templates):
            raise ExpeditionListVisionError("mission_templates must contain MissionRowTemplate values")
        ids = [item.mission_id for item in self.mission_templates]
        if len(ids) != len(set(ids)):
            raise ExpeditionListVisionError("mission template IDs must be unique")
        for field, value in (
            ("minimum_row_score", self.minimum_row_score),
            ("minimum_scrollbar_score", self.minimum_scrollbar_score),
        ):
            _validate_score(value, label=field)
            if value == 0:
                raise ExpeditionListVisionError(f"{field} must be greater than zero")
        for field, value in (
            ("row_ambiguity_margin", self.row_ambiguity_margin),
            ("scrollbar_ambiguity_margin", self.scrollbar_ambiguity_margin),
        ):
            _validate_score(value, label=field)
        for field, value in (
            ("row_center_tolerance_px", self.row_center_tolerance_px),
            ("bottom_tolerance_px", self.bottom_tolerance_px),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ExpeditionListVisionError(f"{field} must be a positive finite number")
        if not isinstance(self.scrollbar_scale_verified, bool):
            raise ExpeditionListVisionError("scrollbar_scale_verified must be boolean")


@dataclass(frozen=True, slots=True)
class MissionRowDetection:
    """One row-template match in screenshot coordinates."""

    row: MissionRowObservation
    bounds: tuple[int, int, int, int]
    score: float

    def __post_init__(self) -> None:
        if not isinstance(self.row, MissionRowObservation):
            raise ExpeditionListVisionError("row detection requires a MissionRowObservation")
        if (
            not isinstance(self.bounds, tuple)
            or len(self.bounds) != 4
            or any(isinstance(value, bool) or not isinstance(value, int) for value in self.bounds)
        ):
            raise ExpeditionListVisionError("row detection bounds must be an integer tuple")
        _, _, width, height = self.bounds
        if width <= 0 or height <= 0:
            raise ExpeditionListVisionError("row detection bounds must have positive dimensions")
        _validate_score(self.score, label="row detection score")


@dataclass(frozen=True, slots=True)
class ScrollbarThumbDetection:
    """The unique matched scrollbar thumb, bound to a calibrated track region."""

    bounds: tuple[int, int, int, int]
    track_region: tuple[int, int, int, int]
    score: float

    def __post_init__(self) -> None:
        for value, label in ((self.bounds, "thumb bounds"), (self.track_region, "track region")):
            if (
                not isinstance(value, tuple)
                or len(value) != 4
                or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
            ):
                raise ExpeditionListVisionError(f"{label} must be an integer tuple")
        x, y, width, height = self.bounds
        tx, ty, track_width, track_height = self.track_region
        if (
            width <= 0 or height <= 0 or track_width <= 0 or track_height <= 0
            or x < tx or y < ty or x + width > tx + track_width or y + height > ty + track_height
        ):
            raise ExpeditionListVisionError("scrollbar thumb must be inside its calibrated track")
        if height >= track_height:
            raise ExpeditionListVisionError("scrollbar thumb must be shorter than its track")
        _validate_score(self.score, label="scrollbar match score")


@dataclass(frozen=True, slots=True)
class ExpeditionListVisualRecognition:
    """One recognized page and its non-sensitive visual confidence summary."""

    page: ExpeditionListPageObservation
    row_matches: tuple[MissionRowDetection, ...]
    scroll_progress: float
    scrollbar_score: float


def build_expedition_list_page(
    profile: ExpeditionListVisualProfile,
    row_detections: tuple[MissionRowDetection, ...],
    scrollbar: ScrollbarThumbDetection,
) -> ExpeditionListVisualRecognition:
    """Convert calibrated visual detections into a safe page observation.

    The track and thumb heights are used as normalized scroll-space units:
    viewport=thumb height, content=track height, scroll top=thumb offset.
    This preserves the proportional scroll geometry needed by the page
    accumulator. It is accepted only after the operator has verified that the
    Poi scrollbar thumb scales proportionally with the list's content length.
    """
    if not isinstance(profile, ExpeditionListVisualProfile):
        raise ExpeditionListVisionError("profile must be an ExpeditionListVisualProfile")
    if not profile.scrollbar_scale_verified:
        raise ExpeditionListVisionError(
            "scrollbar proportional geometry has not been calibrated; refusing complete-scan evidence"
        )
    if not isinstance(row_detections, tuple) or not row_detections:
        raise ExpeditionListVisionError("at least one mission row must be recognized")
    if any(not isinstance(item, MissionRowDetection) for item in row_detections):
        raise ExpeditionListVisionError("row_detections must contain MissionRowDetection values")
    if not isinstance(scrollbar, ScrollbarThumbDetection):
        raise ExpeditionListVisionError("scrollbar detection is required")

    if scrollbar.track_region != profile.scrollbar_track_region:
        raise ExpeditionListVisionError("scrollbar match belongs to a different track region")
    if scrollbar.score < profile.minimum_scrollbar_score:
        raise ExpeditionListVisionError("scrollbar match score is below the calibrated threshold")
    if scrollbar.bounds[2] > profile.scrollbar_track_region[2]:
        raise ExpeditionListVisionError("scrollbar thumb width exceeds its track")

    template_names = {item.mission_id: item.name for item in profile.mission_templates}
    lx, ly, lw, lh = profile.list_region
    for detection in row_detections:
        x, y, width, height = detection.bounds
        if detection.row.mission_id not in template_names:
            raise ExpeditionListVisionError("row detection references an unknown mission template")
        if template_names[detection.row.mission_id] != detection.row.name:
            raise ExpeditionListVisionError("row detection name does not match its trusted template label")
        if (
            detection.score < profile.minimum_row_score
            or x < lx or y < ly or x + width > lx + lw or y + height > ly + lh
        ):
            raise ExpeditionListVisionError("row match is below threshold or outside the expedition list region")

    ranked = sorted(
        row_detections,
        key=lambda item: (item.bounds[1] + item.bounds[3] / 2.0, -item.score, item.row.mission_id),
    )
    groups: list[list[MissionRowDetection]] = []
    for detection in ranked:
        x, y, width, height = detection.bounds
        center_y = y + height / 2.0
        overlapping_group = None
        for index, group in enumerate(groups):
            group_y = group[0].bounds[1]
            group_height = group[0].bounds[3]
            group_center = group_y + group_height / 2.0
            vertical_overlap = max(
                0,
                min(y + height, group_y + group_height) - max(y, group_y),
            )
            overlap_ratio = vertical_overlap / min(height, group_height)
            if abs(center_y - group_center) <= profile.row_center_tolerance_px or overlap_ratio >= 0.5:
                overlapping_group = index
                break
        if overlapping_group is None:
            groups.append([detection])
        else:
            groups[overlapping_group].append(detection)

    selected_rows: list[MissionRowDetection] = []
    for group in groups:
        group.sort(key=lambda item: item.score, reverse=True)
        if (
            len(group) > 1
            and group[0].score - group[1].score < profile.row_ambiguity_margin
        ):
            raise ExpeditionListVisionError("multiple mission templates ambiguously match the same visible row")
        selected_rows.append(group[0])

    mission_ids = [item.row.mission_id for item in selected_rows]
    if len(mission_ids) != len(set(mission_ids)):
        raise ExpeditionListVisionError("the same mission template matched multiple rows on one page")

    tx, ty, track_width, track_height = scrollbar.track_region
    thumb_x, thumb_y, thumb_width, thumb_height = scrollbar.bounds
    del tx, thumb_x, thumb_width  # Horizontal placement is bounded by the validated track.
    maximum_offset = track_height - thumb_height
    offset = thumb_y - ty
    if offset < 0 or offset > maximum_offset:
        raise ExpeditionListVisionError("scrollbar thumb offset is outside the calibrated scroll range")
    remaining = maximum_offset - offset
    bottom_confirmed = remaining <= profile.bottom_tolerance_px
    page = ExpeditionListPageObservation(
        scroll_top_px=float(offset),
        viewport_height_px=float(thumb_height),
        content_height_px=float(track_height),
        bottom_confirmed=bottom_confirmed,
        rows=tuple(item.row for item in selected_rows),
    )
    return ExpeditionListVisualRecognition(
        page=page,
        row_matches=tuple(selected_rows),
        scroll_progress=offset / maximum_offset,
        scrollbar_score=float(scrollbar.score),
    )


def _vision_modules(cv2_module=None, numpy_module=None):
    if cv2_module is None or numpy_module is None:
        try:
            import cv2 as cv2_module  # type: ignore[no-redef]
            import numpy as numpy_module  # type: ignore[no-redef]
        except ImportError as exc:
            raise ExpeditionListVisionError("OpenCV and NumPy are required for screenshot recognition") from exc
    return cv2_module, numpy_module


def _decode_gray_png(payload: str, *, cv2_module, numpy_module):
    if not isinstance(payload, str) or not payload:
        raise ExpeditionListVisionError("screenshot payload must be non-empty base64 text")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ExpeditionListVisionError("screenshot is not valid base64") from exc
    image = cv2_module.imdecode(
        numpy_module.frombuffer(raw, dtype=numpy_module.uint8),
        cv2_module.IMREAD_GRAYSCALE,
    )
    if image is None:
        raise ExpeditionListVisionError("screenshot is not a decodable PNG image")
    return image


def _unique_template_match(
    image,
    template,
    *,
    cv2_module,
    minimum_score: float,
    ambiguity_margin: float,
    label: str,
) -> tuple[float, tuple[int, int]] | None:
    if template is None or len(template.shape) != 2 or template.size == 0:
        raise ExpeditionListVisionError(f"{label} template is not a valid grayscale image")
    image_height, image_width = image.shape[:2]
    template_height, template_width = template.shape[:2]
    if template_height > image_height or template_width > image_width:
        raise ExpeditionListVisionError(f"{label} template is larger than its search region")
    scores = cv2_module.matchTemplate(image, template, cv2_module.TM_CCOEFF_NORMED)
    _, maximum, _, location = cv2_module.minMaxLoc(scores)
    if not math.isfinite(maximum):
        raise ExpeditionListVisionError(f"{label} matcher returned a non-finite score")
    if maximum < minimum_score:
        return None

    suppressed = scores.copy()
    x, y = location
    pad = 2
    x0 = max(0, x - template_width + 1 - pad)
    x1 = min(scores.shape[1], x + template_width + pad)
    y0 = max(0, y - template_height + 1 - pad)
    y1 = min(scores.shape[0], y + template_height + pad)
    suppressed[y0:y1, x0:x1] = -1.0
    _, second, _, _ = cv2_module.minMaxLoc(suppressed)
    if second >= maximum - ambiguity_margin:
        raise ExpeditionListVisionError(f"{label} template has multiple similarly strong matches")
    return float(maximum), (int(x), int(y))


def recognize_expedition_list_screenshot(
    screenshot_base64: str,
    profile: ExpeditionListVisualProfile,
    *,
    cv2_module=None,
    numpy_module=None,
) -> ExpeditionListVisualRecognition:
    """Recognize visible mission rows and scrollbar position from one PNG frame.

    All work is in memory. The function is intentionally not wired to a CDP
    input method; it only returns an observation for the offline accumulator.
    """
    if not isinstance(profile, ExpeditionListVisualProfile):
        raise ExpeditionListVisionError("profile must be an ExpeditionListVisualProfile")
    if not profile.scrollbar_scale_verified:
        raise ExpeditionListVisionError(
            "scrollbar proportional geometry must be calibrated before screenshot scanning"
        )
    cv2_module, numpy_module = _vision_modules(cv2_module, numpy_module)
    image = _decode_gray_png(
        screenshot_base64,
        cv2_module=cv2_module,
        numpy_module=numpy_module,
    )
    if (image.shape[1], image.shape[0]) != profile.viewport:
        raise ExpeditionListVisionError("screenshot viewport does not match the calibrated profile")

    lx, ly, list_width, list_height = profile.list_region
    list_image = image[ly:ly + list_height, lx:lx + list_width]
    row_detections: list[MissionRowDetection] = []
    for mission in profile.mission_templates:
        template = _decode_gray_png(
            mission.png_base64,
            cv2_module=cv2_module,
            numpy_module=numpy_module,
        )
        match = _unique_template_match(
            list_image,
            template,
            cv2_module=cv2_module,
            minimum_score=profile.minimum_row_score,
            ambiguity_margin=profile.row_ambiguity_margin,
            label=f"mission {mission.mission_id}",
        )
        if match is None:
            continue
        score, (match_x, match_y) = match
        height, width = template.shape[:2]
        row_detections.append(
            MissionRowDetection(
                row=MissionRowObservation(mission.mission_id, mission.name),
                bounds=(lx + match_x, ly + match_y, int(width), int(height)),
                score=score,
            )
        )

    tx, ty, track_width, track_height = profile.scrollbar_track_region
    track_image = image[ty:ty + track_height, tx:tx + track_width]
    thumb_template = _decode_gray_png(
        profile.scrollbar_thumb_template_base64,
        cv2_module=cv2_module,
        numpy_module=numpy_module,
    )
    thumb_match = _unique_template_match(
        track_image,
        thumb_template,
        cv2_module=cv2_module,
        minimum_score=profile.minimum_scrollbar_score,
        ambiguity_margin=profile.scrollbar_ambiguity_margin,
        label="scrollbar thumb",
    )
    if thumb_match is None:
        raise ExpeditionListVisionError("scrollbar thumb could not be recognized")
    thumb_score, (thumb_x, thumb_y) = thumb_match
    thumb_height, thumb_width = thumb_template.shape[:2]
    scrollbar = ScrollbarThumbDetection(
        bounds=(tx + thumb_x, ty + thumb_y, int(thumb_width), int(thumb_height)),
        track_region=profile.scrollbar_track_region,
        score=thumb_score,
    )
    return build_expedition_list_page(profile, tuple(row_detections), scrollbar)
