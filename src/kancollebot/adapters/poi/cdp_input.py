"""Small, deterministic CDP input primitives for the Poi renderer.

This module intentionally does not guess coordinates or claim game success.
Coordinates must come from a reviewed local Record play capture. A concrete
CDP WebSocket transport and game-state verifier are injected by the caller.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Protocol


class CdpInputError(RuntimeError):
    """Raised when a CDP command cannot be completed safely."""


class CdpTransport(Protocol):
    def send(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send one CDP command and return its result object."""
        ...


@dataclass(frozen=True, slots=True)
class RecordedPoint:
    """Reviewed WebView CSS-pixel coordinate captured from a manual recording."""

    x: float
    y: float
    label: str

    def __post_init__(self) -> None:
        if not math.isfinite(self.x) or not math.isfinite(self.y):
            raise ValueError("recorded coordinates must be finite")
        if self.x < 0 or self.y < 0:
            raise ValueError("recorded coordinates must not be negative")
        if not self.label.strip():
            raise ValueError("recorded point label must not be empty")


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
        """Dispatch one mouse click at a recorded point; never retry automatically."""
        if button not in {"left", "right", "middle"}:
            raise ValueError("unsupported mouse button")
        x, y = float(point.x), float(point.y)
        try:
            self._transport.send(
                "Input.dispatchMouseEvent",
                {"type": "mouseMoved", "x": x, "y": y},
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
        except Exception as exc:
            # Once input begins, outcome can be ambiguous. The caller must not
            # blindly replay this click; it must inspect fresh game state.
            raise CdpInputError(
                f"CDP click outcome may be ambiguous at recorded target: {point.label}"
            ) from exc

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
