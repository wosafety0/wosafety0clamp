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
        if self.locator != "opencv-canny-square-v1":
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
            raise ValueError("visual target is not an approved Fleet 2 ship checkbox region")
        if (
            rx < 0 or ry < 0 or rx + region_width > 1200 or ry + region_height > 720
            or x < rx or y < ry or width <= 0 or height <= 0
            or not 18 <= width <= 26 or not 18 <= height <= 27
            or abs(width - height) > 4
            or x + width > rx + region_width or y + height > ry + region_height
            or region_width <= 0 or region_height <= 0
        ):
            raise ValueError("visual target bounds must be inside its bounded region")
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

    def capture_screenshot(self) -> str:
        """Return CDP's base64 PNG payload for diagnostics; does not prove success."""
        try:
            result = self._transport.send(
                "Page.captureScreenshot",
                {"format": "png", "fromSurface": True, "captureBeyondViewport": False},
            )
        except Exception as exc:
            raise CdpInputError("CDP screenshot capture failed") from exc
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, str) or not data:
            raise CdpInputError("CDP screenshot response contained no image data")
        return data
