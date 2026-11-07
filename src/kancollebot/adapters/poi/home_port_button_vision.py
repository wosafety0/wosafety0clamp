"""Rotation-invariant visual localization of the top-left 母港 button.

The 母港 control is a rotating circular badge.  On the Home Port it is a blue
disc with a gold ring, on the supply page a gold disc with a gold ring, and the
lettering around the ring rotates continuously.  A single static template or a
fixed ``(50, 50)`` click therefore cannot be trusted: the template mismatches
half the pages, and a coordinate click does not even know which control is
under it.

This module searches a bounded ROI for a *circular, high-contrast, coloured*
badge using statistics that do not depend on the rotation angle:

* radial edge strength around a circle (the dark rim against the disc/background),
  measured on a sorted, angle-order-free distribution;
* the fraction of visible arc angles that carry that rim (a rectangle or a menu
  tab cannot produce a full circle);
* brightness mean / standard deviation (contrast);
* a coarse warm / cool / neutral colour histogram of the ring and disc.

Rotation changes where the letters are, not any of those statistics.  Thresholds
are configurable and every decision emits its ROI, features, confidence,
threshold set, candidate count and refusal reason.  Low confidence, ambiguous
runners-up, an out-of-ROI centre or an unreadable frame all fail closed: no
coordinate is guessed and no click is sent.

Only the Python standard library is used; the PNG decoder is the project's
existing bounded in-memory decoder.
"""
from __future__ import annotations

import base64
import hashlib
import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from .cdp_input import RecordedPoint
from .visual_fingerprint import VisualFingerprintError, _decode_png_rgba

DEFAULT_ROI: tuple[int, int, int, int] = (0, 0, 190, 190)
DEFAULT_CENTER_BOUNDS: tuple[int, int, int, int] = (25, 25, 115, 115)
DEFAULT_RADIUS_RANGE: tuple[int, int] = (68, 96)


class HomePortVisionError(RuntimeError):
    """The 母港 button could not be located safely; no click may be sent."""


class HomePortButtonRefused(HomePortVisionError):
    """A named refusal carrying the full visual evidence for the run log."""

    def __init__(self, reason: str, evidence: "HomePortButtonDetection") -> None:
        super().__init__(reason)
        self.reason = reason
        self.evidence = evidence


@dataclass(frozen=True, slots=True)
class HomePortVisionThresholds:
    """Every acceptance threshold, configurable and visible in the run log."""

    min_confidence: float = 0.52
    min_margin: float = 0.055
    min_edge_coverage: float = 0.50
    # The badge is partly outside the viewport (its centre is near (60,60)
    # while its radius is ~85), so only 45-90% of the arc can ever be sampled.
    min_visible_arc: float = 0.45
    min_colorfulness: float = 0.10
    min_brightness_std: float = 10.0
    # One badge ~170 px across: nearby high-scoring centres/radii belong to
    # the SAME control and must be clustered, not reported as a conflict.
    # Only a spatially separate circular control counts as a rival target.
    min_center_separation: float = 90.0
    # Hard geometry gate: even with every score threshold relaxed, a rival
    # control elsewhere in the ROI must never be clicked.  The live badge
    # centre measured (50..81, 59..105) on nine captured frames, all within
    # 47 px of (60, 60).
    expected_center: tuple[int, int] = (60, 60)
    max_center_offset: float = 55.0
    roi: tuple[int, int, int, int] = DEFAULT_ROI
    center_bounds: tuple[int, int, int, int] = DEFAULT_CENTER_BOUNDS
    radius_range: tuple[int, int] = DEFAULT_RADIUS_RANGE

    def __post_init__(self) -> None:
        for name in (
            "min_confidence", "min_margin", "min_edge_coverage", "min_visible_arc",
            "min_colorfulness", "min_brightness_std",             "min_center_separation", "max_center_offset",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0
            ):
                raise ValueError(f"{name} must be a finite non-negative number")
        if not 0 <= self.min_confidence <= 1 or not 0 <= self.min_edge_coverage <= 1:
            raise ValueError("confidence and edge coverage must be within 0..1")
        for name in ("roi", "center_bounds"):
            value = getattr(self, name)
            if (
                not isinstance(value, tuple) or len(value) != 4
                or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
                or value[0] < 0 or value[1] < 0 or value[2] <= value[0] or value[3] <= value[1]
            ):
                raise ValueError(f"{name} must be a valid (left, top, right, bottom) tuple")
        cb = self.center_bounds
        roi = self.roi
        if cb[0] < roi[0] or cb[1] < roi[1] or cb[2] > roi[2] or cb[3] > roi[3]:
            raise ValueError("center_bounds must stay inside roi")
        if (
            not isinstance(self.expected_center, tuple)
            or len(self.expected_center) != 2
            or any(isinstance(item, bool) or not isinstance(item, int) for item in self.expected_center)
        ):
            raise ValueError("expected_center must be an (x, y) integer tuple")
        rr = self.radius_range
        if (
            not isinstance(rr, tuple) or len(rr) != 2
            or any(isinstance(item, bool) or not isinstance(item, int) for item in rr)
            or rr[0] < 10 or rr[1] < rr[0]
        ):
            raise ValueError("radius_range must be (min, max) positive integers")

    def as_dict(self) -> dict[str, Any]:
        return {
            "min_confidence": self.min_confidence,
            "min_margin": self.min_margin,
            "min_edge_coverage": self.min_edge_coverage,
            "min_visible_arc": self.min_visible_arc,
            "min_colorfulness": self.min_colorfulness,
            "min_brightness_std": self.min_brightness_std,
            "min_center_separation": self.min_center_separation,
            "expected_center": list(self.expected_center),
            "max_center_offset": self.max_center_offset,
            "roi": list(self.roi),
            "center_bounds": list(self.center_bounds),
            "radius_range": list(self.radius_range),
        }

    @classmethod
    def from_environment(cls, base: "HomePortVisionThresholds | None" = None) -> "HomePortVisionThresholds":
        """Allow an operator to relax/tighten thresholds without a code change."""
        current = base or cls()
        overrides: dict[str, float] = {}
        for name in (
            "min_confidence", "min_margin", "min_edge_coverage", "min_visible_arc",
            "min_colorfulness", "min_brightness_std", "min_center_separation",
            "max_center_offset",
        ):
            raw = os.environ.get(f"KANCOLLEBOT_HOME_PORT_{name.upper()}")
            if raw is None or not raw.strip():
                continue
            try:
                value = float(raw.strip())
            except ValueError as exc:
                raise HomePortVisionError(
                    f"KANCOLLEBOT_HOME_PORT_{name.upper()} must be a number"
                ) from exc
            if not math.isfinite(value) or value < 0:
                raise HomePortVisionError(
                    f"KANCOLLEBOT_HOME_PORT_{name.upper()} must be finite and non-negative"
                )
            overrides[name] = value
        return replace(current, **overrides) if overrides else current


@dataclass(frozen=True, slots=True)
class ButtonCandidate:
    center_x: float
    center_y: float
    radius: int
    confidence: float
    features: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class HomePortButtonDetection:
    """One accepted, frame-bound visual detection."""

    center_x: float
    center_y: float
    radius: int
    confidence: float
    roi: tuple[int, int, int, int]
    features: Mapping[str, float]
    thresholds: Mapping[str, Any]
    candidate_count: int
    runner_up_confidence: float
    margin: float
    frame_sha256: str
    reason: str = "accepted"

    @property
    def point(self) -> RecordedPoint:
        # The label stays the reviewed control name so the existing action-id
        # audit trail is unchanged; *where* the point comes from is recorded by
        # as_log_fields() (ROI, features, confidence, frame digest).
        return RecordedPoint(
            float(self.center_x), float(self.center_y), "home-port"
        )

    def as_log_fields(self) -> dict[str, Any]:
        return {
            "roi": list(self.roi),
            "center": [round(self.center_x, 2), round(self.center_y, 2)],
            "radius": self.radius,
            "confidence": round(self.confidence, 4),
            "features": {key: round(float(value), 4) for key, value in self.features.items()},
            "thresholds": dict(self.thresholds),
            "candidate_count": self.candidate_count,
            "runner_up_confidence": round(self.runner_up_confidence, 4),
            "margin": round(self.margin, 4),
            "frame_sha256": self.frame_sha256,
            "reason": self.reason,
            "click_authorized": self.reason == "accepted",
        }


def _luma(rgba: bytes, width: int, height: int, x: int, y: int) -> tuple[int, int, int, int] | None:
    if x < 0 or y < 0 or x >= width or y >= height:
        return None
    base = (y * width + x) * 4
    r, g, b, a = rgba[base], rgba[base + 1], rgba[base + 2], rgba[base + 3]
    return r, g, b, (r * 299 + g * 587 + b * 114) // 1000


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _candidate_score(
    rgba: bytes,
    width: int,
    height: int,
    cx: float,
    cy: float,
    radius: int,
    *,
    angles: int = 24,
) -> tuple[float, dict[str, float]] | None:
    """Rotation-invariant circular-badge score; ``None`` when too little is visible."""
    gradients: list[float] = []
    luminances: list[int] = []
    warm = cool = neutral = 0
    visible = 0
    for index in range(angles):
        theta = 2 * math.pi * index / angles
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        samples: dict[str, tuple[int, int, int, int] | None] = {}
        for label, offset in (("inside", -6), ("rim", 0), ("outside", 7)):
            r = radius + offset
            x = int(round(cx + r * cos_t))
            y = int(round(cy + r * sin_t))
            samples[label] = _luma(rgba, width, height, x, y)
        rim = samples["rim"]
        inside = samples["inside"]
        outside = samples["outside"]
        if rim is None:
            continue
        visible += 1
        luminances.append(rim[3])
        red, green, blue = rim[0], rim[1], rim[2]
        if red - blue > 30:
            warm += 1
        elif blue - red > 20:
            cool += 1
        else:
            neutral += 1
        # Radial rim edge: rotate the circle and this distribution is unchanged.
        if inside is not None and outside is not None:
            left_edge = abs(rim[3] - inside[3])
            right_edge = abs(rim[3] - outside[3])
            gradients.append(max(left_edge, right_edge))
    if visible == 0 or not gradients:
        return None
    visible_arc = visible / angles
    gradients.sort()
    edge_coverage = sum(1 for value in gradients if value >= 18) / len(gradients)
    mean_gradient = sum(gradients) / len(gradients)
    median_gradient = gradients[len(gradients) // 2]
    mean_luma = sum(luminances) / len(luminances)
    variance = sum((value - mean_luma) ** 2 for value in luminances) / len(luminances)
    luma_std = math.sqrt(variance)
    coloured = (warm + cool) / max(1, warm + cool + neutral)
    # Geometry prior is deliberately weak: it only prefers the expected
    # top-left badge over an equally circular neighbour.
    centre_prior = _clamp01(1.0 - (abs(cx - 65) + abs(cy - 65)) / 160.0)
    radius_prior = _clamp01(1.0 - abs(radius - 82) / 40.0)
    confidence = (
        0.40 * edge_coverage
        + 0.18 * _clamp01(mean_gradient / 55.0)
        + 0.12 * _clamp01(coloured / 0.60)
        + 0.12 * _clamp01(luma_std / 55.0)
        + 0.10 * _clamp01(visible_arc)
        + 0.05 * centre_prior
        + 0.03 * radius_prior
    )
    features = {
        "edge_coverage": edge_coverage,
        "mean_radial_gradient": mean_gradient,
        "median_radial_gradient": float(median_gradient),
        "mean_luminance": mean_luma,
        "brightness_std": luma_std,
        "colorfulness": coloured,
        "warm_ratio": warm / visible,
        "cool_ratio": cool / visible,
        "neutral_ratio": neutral / visible,
        "visible_arc": visible_arc,
        "contrast_ratio": _clamp01(luma_std / 55.0),
    }
    return confidence, features


def locate_home_port_button(
    screenshot_base64: str,
    *,
    thresholds: HomePortVisionThresholds | None = None,
) -> HomePortButtonDetection:
    """Locate the 母港 badge in one frame, or fail closed with a named reason."""
    limits = thresholds or HomePortVisionThresholds()
    if not isinstance(screenshot_base64, str) or not screenshot_base64:
        raise HomePortVisionError("empty_screenshot_frame")
    try:
        frame = base64.b64decode(screenshot_base64, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise HomePortVisionError("invalid_base64_screenshot") from exc
    frame_digest = hashlib.sha256(frame).hexdigest()
    try:
        width, height, rgba = _decode_png_rgba(screenshot_base64)
    except VisualFingerprintError as exc:
        raise HomePortVisionError(f"undecodable_screenshot_frame:{exc}") from exc
    if width < limits.roi[2] or height < limits.roi[3]:
        raise HomePortVisionError(
            f"frame_smaller_than_roi:{width}x{height}_vs_{limits.roi[2]}x{limits.roi[3]}"
        )

    rx0, ry0, rx1, ry1 = limits.roi
    cx0, cy0, cx1, cy1 = limits.center_bounds
    r_min, r_max = limits.radius_range
    raw: list[ButtonCandidate] = []
    for cx in range(cx0, cx1 + 1, 8):
        for cy in range(cy0, cy1 + 1, 8):
            for radius in range(r_min, r_max + 1, 6):
                scored = _candidate_score(rgba, width, height, float(cx), float(cy), radius)
                if scored is None:
                    continue
                confidence, features = scored
                raw.append(ButtonCandidate(float(cx), float(cy), radius, confidence, features))
    if not raw:
        raise HomePortVisionError("no_visible_circular_candidate_in_roi")

    # Cluster nearby centres/radii into ONE control.  A rotating badge produces
    # several almost equally good centres; treating those as rival targets was
    # a false "candidate conflict" on every real frame (2026-11-07 probe).  Only
    # a spatially separate circle is a genuine rival.  Within a cluster the
    # centre is confidence-weighted, which keeps the click near the true centre
    # instead of whichever grid point happened to score highest.
    ranked = sorted(raw, key=lambda item: item.confidence, reverse=True)
    clusters: list[list[ButtonCandidate]] = []
    for item in ranked:
        for members in clusters:
            anchor = members[0]
            if (
                math.hypot(item.center_x - anchor.center_x, item.center_y - anchor.center_y)
                <= limits.min_center_separation
            ):
                members.append(item)
                break
        else:
            clusters.append([item])
        if len(clusters) >= 6:
            break
    candidates: list[ButtonCandidate] = []
    for members in clusters:
        weights = [member.confidence ** 4 for member in members[:8]]
        total_weight = sum(weights)
        cx = sum(member.center_x * weight for member, weight in zip(members[:8], weights)) / total_weight
        cy = sum(member.center_y * weight for member, weight in zip(members[:8], weights)) / total_weight
        radius = round(sum(member.radius * weight for member, weight in zip(members[:8], weights)) / total_weight)
        best_member = members[0]
        candidates.append(
            ButtonCandidate(cx, cy, radius, best_member.confidence, best_member.features)
        )
    candidates.sort(key=lambda item: item.confidence, reverse=True)
    best = candidates[0]
    runner_up = candidates[1].confidence if len(candidates) > 1 else 0.0
    margin = best.confidence - runner_up
    thresholds_dict = limits.as_dict()
    centre_offset = math.hypot(
        best.center_x - limits.expected_center[0],
        best.center_y - limits.expected_center[1],
    )
    features = {**best.features, "center_offset": centre_offset}

    def refusal(reason: str) -> HomePortButtonDetection:
        # Returned only through the exception below; kept as a helper so the
        # caller can log the full evidence even on a refusal.
        return HomePortButtonDetection(
            center_x=best.center_x, center_y=best.center_y, radius=best.radius,
            confidence=best.confidence, roi=limits.roi, features=features,
            thresholds=thresholds_dict, candidate_count=len(candidates),
            runner_up_confidence=runner_up, margin=margin,
            frame_sha256=frame_digest, reason=reason,
        )
    checks = (
        (centre_offset > limits.max_center_offset, "center_outside_expected_navigation_area"),
        (best.confidence < limits.min_confidence, "confidence_below_threshold"),
        (margin < limits.min_margin, "candidate_conflict"),
        (best.features["edge_coverage"] < limits.min_edge_coverage, "rim_edge_coverage_too_low"),
        (best.features["visible_arc"] < limits.min_visible_arc, "visible_arc_too_small"),
        (best.features["colorfulness"] < limits.min_colorfulness, "color_distribution_flat"),
        (best.features["brightness_std"] < limits.min_brightness_std, "contrast_too_low"),
    )
    for failed, reason in checks:
        if failed:
            raise HomePortButtonRefused(reason, refusal(reason))
    return refusal("accepted")


@dataclass(frozen=True, slots=True)
class StaticHomePortDetection:
    """TEST SEAM ONLY.  A pre-approved point with the same log shape."""

    point: RecordedPoint

    def as_log_fields(self) -> dict[str, Any]:
        return {
            "locator": "test-static-seam",
            "reason": "accepted",
            "click_authorized": True,
            "center": [self.point.x, self.point.y],
            "confidence": None,
            "thresholds": {},
            "candidate_count": 1,
            "roi": list(DEFAULT_ROI),
            "test_only": True,
        }


class StaticHomePortLocator:
    """TEST SEAM ONLY: never constructed by production composition roots.

    Offline driver tests have no real game frame to classify.  They inject this
    locator so the click still comes from a locator object rather than from a
    hardcoded coordinate inside the driver.  Production always passes a real
    :class:`HomePortButtonLocator`; leaving it unset fails closed.
    """

    def __init__(self, point: RecordedPoint) -> None:
        if not isinstance(point, RecordedPoint):
            raise ValueError("static locator requires a RecordedPoint")
        self._point = point

    def locate(self) -> StaticHomePortDetection:
        return StaticHomePortDetection(self._point)


class HomePortButtonLocator:
    """Frame-bound locator used by the production UI drivers before a 母港 click."""

    def __init__(
        self,
        *,
        capture: Callable[[], str],
        thresholds: HomePortVisionThresholds | None = None,
        probe_kind: str = "screenshot",
    ) -> None:
        if not callable(capture):
            raise ValueError("capture must return one fresh base64 PNG frame")
        self._capture = capture
        self.thresholds = thresholds or HomePortVisionThresholds.from_environment()

    def locate(self) -> HomePortButtonDetection:
        frame = self._capture()
        return locate_home_port_button(frame, thresholds=self.thresholds)
