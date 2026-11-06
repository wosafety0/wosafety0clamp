"""Small, deterministic CDP input primitives for the Poi renderer.

RecordedPoint coordinates must come from a reviewed local Record play capture.
The explicit visual-target path accepts only a frame-bound point from a bounded,
fail-closed detector. The transport is loopback-only and allowlisted; this
module never infers targets or claims that a game action succeeded.
"""
from __future__ import annotations

import base64
import hashlib
import math
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol


class CdpInputError(RuntimeError):
    """Raised when a CDP command cannot be completed safely."""


class CdpTransport(Protocol):
    def send(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send one allowlisted CDP command and return its result object."""
        ...


_APPROVED_VISUAL_TARGETS = {
    "fleet2-ship1-checkbox": (150, 225, 55, 50),
    "fleet2-ship2-checkbox": (150, 302, 55, 50),
    "fleet2-ship3-checkbox": (150, 379, 55, 50),
    "fleet2-ship4-checkbox": (150, 456, 55, 50),
    "fleet3-ship1-checkbox": (150, 225, 55, 50),
    "fleet3-ship2-checkbox": (150, 302, 55, 50),
    "fleet3-ship3-checkbox": (150, 379, 55, 50),
    "fleet3-ship4-checkbox": (150, 456, 55, 50),
    "supply-fleet1-tab": (195, 155, 50, 50),
    "supply-fleet2-tab": (240, 155, 50, 50),
    "supply-fleet3-tab": (285, 155, 50, 50),
    # Derived, not templated: the supply tab strip is evenly spaced at 45 px
    # (195/240/285), and Fleet 4's tab was confirmed on screen on 2026-11-06.
    # The region is approved so the locator can reach it. Since 2026-11-06
    # Fleet 4 also has a captured SUPPLY_FLEET_4_SELECTED fingerprint, so tab
    # selection is proven by the same template gate as Fleets 1-3 (the
    # relative-highlight fallback is retired). Note FLEET_4_SELECTED, the
    # *dispatch* tab, is a different state and still has no template.
    "supply-fleet4-tab": (330, 155, 50, 50),
    "fleet-supply-all-button": (1050, 590, 145, 60),
}


@dataclass(frozen=True, slots=True)
class RecordedPoint:
    """Reviewed WebView CSS-pixel coordinate captured from a manual recording."""

    x: float
    y: float
    label: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.x, bool) or isinstance(self.y, bool)
            or not isinstance(self.x, (int, float))
            or not isinstance(self.y, (int, float))
            or not math.isfinite(self.x) or not math.isfinite(self.y)
        ):
            raise ValueError("recorded coordinates must be finite numbers")
        if self.x < 0 or self.y < 0:
            raise ValueError("recorded coordinates must not be negative")
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("recorded point label must not be empty")


def home_port_point() -> "RecordedPoint":
    """Resolve the Home Port navigation target.

    The 母港 control is the large button at the top-left of the 1200x720 game
    viewport. The default is the corner point that was live-verified to return
    from sub-screens; override it with ``KANCOLLEBOT_HOME_PORT_POINT="x,y"``
    when a specific layout differs. Every click is still verified by the UI
    state gate, so an incorrect override fails closed instead of acting on a
    wrong target.
    """
    override = os.environ.get("KANCOLLEBOT_HOME_PORT_POINT")
    if override:
        try:
            raw_x, raw_y = override.split(",")
            x, y = float(raw_x.strip()), float(raw_y.strip())
        except (ValueError, AttributeError) as exc:
            raise ValueError(
                f"KANCOLLEBOT_HOME_PORT_POINT must be 'x,y' numbers, got {override!r}"
            ) from exc
        return RecordedPoint(x, y, "home-port")
    return RecordedPoint(50.0, 50.0, "home-port")


def home_port_candidates(primary: "RecordedPoint") -> list["RecordedPoint"]:
    """Bounded interior offsets inside the large top-left 母港 button.

    The 母港 control has a large hit area, so if the primary corner point
    misses, nearby interior points of the same control are tried. The set is
    bounded and every point is still verified by the gate; an incorrect layout
    fails closed after the set is exhausted.
    """
    offsets = ((0, 0), (22, 12), (12, 26), (30, 6))
    candidates: list[RecordedPoint] = []
    for dx, dy in offsets:
        x, y = primary.x + dx, primary.y + dy
        if x < 0 or y < 0:
            continue
        candidates.append(RecordedPoint(x, y, "home-port"))
    return candidates or [primary]


@dataclass(frozen=True, slots=True)
class VisualTargetPoint:
    """A bounded visual target tied to the exact screenshot frame that located it."""

    x: float
    y: float
    label: str
    bounds: tuple[int, int, int, int]
    region: tuple[int, int, int, int]
    source_frame_sha256: str
    locator: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.x, bool) or isinstance(self.y, bool)
            or not isinstance(self.x, (int, float))
            or not isinstance(self.y, (int, float))
            or not math.isfinite(self.x) or not math.isfinite(self.y)
        ):
            raise ValueError("visual target coordinates must be finite numbers")
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("visual target label must not be empty")
        if self.locator not in {"opencv-canny-square-v1", "opencv-canny-tab-v1", "opencv-canny-button-v1"}:
            raise ValueError("visual target locator is not approved")
        if (
            not isinstance(self.bounds, tuple) or len(self.bounds) != 4
            or not isinstance(self.region, tuple) or len(self.region) != 4
            or any(isinstance(value, bool) or not isinstance(value, int)
                   for value in (*self.bounds, *self.region))
        ):
            raise ValueError("visual target bounds and region must be integer tuples")
        x, y, width, height = self.bounds
        rx, ry, region_width, region_height = self.region
        if _APPROVED_VISUAL_TARGETS.get(self.label) != self.region:
            raise ValueError("visual target label/region is not approved")
        if (
            rx < 0 or ry < 0 or rx + region_width > 1200 or ry + region_height > 720
            or x < rx or y < ry or width <= 0 or height <= 0
            or x + width > rx + region_width or y + height > ry + region_height
            or region_width <= 0 or region_height <= 0
        ):
            raise ValueError("visual target bounds must be inside its bounded region")
        if self.label.endswith("-checkbox"):
            if (
                self.locator != "opencv-canny-square-v1"
                or not 18 <= width <= 26 or not 18 <= height <= 27
                or abs(width - height) > 4
            ):
                raise ValueError("checkbox target geometry or locator is not approved")
        elif self.label in {
            "supply-fleet1-tab",
            "supply-fleet2-tab",
            "supply-fleet3-tab",
            "supply-fleet4-tab",
        }:
            if (
                self.locator != "opencv-canny-tab-v1"
                or not 28 <= width <= 40 or not 26 <= height <= 38
                or not 0.75 <= width / height <= 1.4
            ):
                raise ValueError("supply fleet tab geometry or locator is not approved")
        elif self.label == "fleet-supply-all-button":
            if (
                self.locator != "opencv-canny-button-v1"
                or not 80 <= width <= 105 or not 24 <= height <= 36
            ):
                raise ValueError("fleet-wide supply button geometry or locator is not approved")
        else:
            raise ValueError("visual target label is not approved")
        if abs(self.x - (x + width / 2.0)) > 1e-6 or abs(self.y - (y + height / 2.0)) > 1e-6:
            raise ValueError("visual target coordinates must equal the detected bounds center")
        if (
            not isinstance(self.source_frame_sha256, str)
            or len(self.source_frame_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.source_frame_sha256)
        ):
            raise ValueError("visual target must include a lowercase SHA-256 frame digest")


class CdpInput:
    """Deterministic mouse input and renderer screenshot helpers.

    This is only an input primitive. It does not navigate menus, infer targets,
    retry clicks, or decide whether a game action succeeded.
    """

    def __init__(self, transport: CdpTransport, *, settle_seconds: float = 0.25) -> None:
        if not math.isfinite(settle_seconds) or settle_seconds < 0:
            raise ValueError("settle_seconds must be finite and non-negative")
        self._transport = transport
        self._settle_seconds = settle_seconds

    def click(self, point: RecordedPoint, *, button: str = "left") -> None:
        """Validate the current viewport, then send one click without retry."""
        if button not in {"left", "right", "middle"}:
            raise ValueError("unsupported mouse button")
        try:
            metrics = self._transport.send("Page.getLayoutMetrics", {})
            viewport = metrics.get("cssVisualViewport") or metrics.get("visualViewport")
            if not isinstance(viewport, dict):
                raise CdpInputError("CDP did not provide viewport dimensions")
            width, height = viewport.get("clientWidth"), viewport.get("clientHeight")
            if (
                isinstance(width, bool) or isinstance(height, bool)
                or not isinstance(width, (int, float)) or not isinstance(height, (int, float))
                or not math.isfinite(width) or not math.isfinite(height)
                or width <= 0 or height <= 0 or point.x >= width or point.y >= height
            ):
                raise CdpInputError("Recorded point is outside the current verified viewport")
            x, y = float(point.x), float(point.y)
            self._transport.send(
                "Input.dispatchMouseEvent",
                {"type": "mouseMoved", "x": x, "y": y, "button": "none"},
            )
            if self._settle_seconds:
                time.sleep(self._settle_seconds)
            self._transport.send(
                "Input.dispatchMouseEvent",
                {"type": "mousePressed", "x": x, "y": y,
                 "button": button, "clickCount": 1},
            )
            self._transport.send(
                "Input.dispatchMouseEvent",
                {"type": "mouseReleased", "x": x, "y": y,
                 "button": button, "clickCount": 1},
            )
        except CdpInputError:
            raise
        except Exception as exc:
            # Once input begins, outcome can be ambiguous. Never replay blindly.
            raise CdpInputError(
                f"CDP click outcome may be ambiguous at recorded target: {point.label}"
            ) from exc

    def click_visual_target(
        self,
        point: VisualTargetPoint,
        *,
        screenshot_base64: str,
        button: str = "left",
    ) -> None:
        """Click a uniquely detected target only when its source frame is supplied unchanged."""
        if not isinstance(point, VisualTargetPoint):
            raise CdpInputError("visual click requires a validated VisualTargetPoint")
        try:
            frame_bytes = base64.b64decode(screenshot_base64, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise CdpInputError("visual click source screenshot is invalid") from exc
        if hashlib.sha256(frame_bytes).hexdigest() != point.source_frame_sha256:
            raise CdpInputError("visual target is stale or belongs to a different screenshot frame")
        self.click(RecordedPoint(point.x, point.y, point.label), button=button)

    def capture_screenshot(self, *, from_surface: bool = True) -> str:
        """Return CDP's base64 PNG payload for diagnostics; does not prove success.

        ``from_surface=True`` is the fast path and the one every calibrated
        fingerprint was recorded with, but it can drop composited overlay
        layers: the Home Port return notice (2026-11-05) is invisible to it
        while the same frame captured with ``from_surface=False`` carries the
        bubble at a 0.999 score. A caller that is looking for such an overlay
        must ask for the composited surface explicitly.
        """
        if not isinstance(from_surface, bool):
            raise CdpInputError("from_surface must be a bool")
        try:
            result = self._transport.send(
                "Page.captureScreenshot",
                {"format": "png", "fromSurface": from_surface, "captureBeyondViewport": False},
            )
        except Exception as exc:
            raise CdpInputError("CDP screenshot capture failed") from exc
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, str) or not data:
            raise CdpInputError("CDP screenshot response contained no image data")
        return data
