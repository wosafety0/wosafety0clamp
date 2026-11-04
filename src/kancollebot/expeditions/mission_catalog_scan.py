"""Offline aggregation of visually observed expedition-list pages.

This module never scrolls, captures screenshots, performs OCR, or sends game input.
A future visual adapter must provide each page's measured scroll geometry, visible
mission rows, and an independently verified bottom-of-list signal.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math


class ExpeditionListScanError(ValueError):
    """A page cannot be safely incorporated into a complete mission catalogue."""


class ExpeditionListScanStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class MissionRowObservation:
    """One mission row recognized in a single visible list page."""

    mission_id: int
    name: str

    def __post_init__(self) -> None:
        if isinstance(self.mission_id, bool) or not isinstance(self.mission_id, int) or self.mission_id < 1:
            raise ExpeditionListScanError("mission_id must be a positive integer")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ExpeditionListScanError("mission name must be non-empty text")
        object.__setattr__(self, "name", self.name.strip())


@dataclass(frozen=True, slots=True)
class ExpeditionListPageObservation:
    """Normalized facts supplied by a separate visual page recognizer.

    Scroll coordinates are measured in pixels relative to the list viewport.
    bottom_confirmed must come from a visual end-of-list gate, not from a timer
    or from the absence of newly recognized rows.
    """

    scroll_top_px: float
    viewport_height_px: float
    content_height_px: float
    bottom_confirmed: bool
    rows: tuple[MissionRowObservation, ...]

    def __post_init__(self) -> None:
        for label, value in (
            ("scroll_top_px", self.scroll_top_px),
            ("viewport_height_px", self.viewport_height_px),
            ("content_height_px", self.content_height_px),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ExpeditionListScanError(f"{label} must be a finite number")
        if self.scroll_top_px < 0:
            raise ExpeditionListScanError("scroll_top_px cannot be negative")
        if self.viewport_height_px <= 0:
            raise ExpeditionListScanError("viewport_height_px must be positive")
        if self.content_height_px < self.viewport_height_px:
            raise ExpeditionListScanError("content_height_px cannot be smaller than the viewport")
        if self.scroll_top_px > self.max_scroll_top_px + 1.0:
            raise ExpeditionListScanError("scroll_top_px exceeds the measured scroll range")
        if not isinstance(self.bottom_confirmed, bool):
            raise ExpeditionListScanError("bottom_confirmed must be a boolean visual-gate result")
        if not isinstance(self.rows, tuple) or not self.rows:
            raise ExpeditionListScanError("each observed page must contain at least one recognized mission row")
        if any(not isinstance(row, MissionRowObservation) for row in self.rows):
            raise ExpeditionListScanError("rows must contain MissionRowObservation values")
        ids = [row.mission_id for row in self.rows]
        if len(ids) != len(set(ids)):
            raise ExpeditionListScanError("a page cannot contain duplicate mission IDs")

    @property
    def max_scroll_top_px(self) -> float:
        return max(0.0, float(self.content_height_px) - float(self.viewport_height_px))

    @property
    def geometry_reaches_bottom(self) -> bool:
        return self.scroll_top_px + self.viewport_height_px >= self.content_height_px - 1.0


@dataclass(frozen=True, slots=True)
class ExpeditionListScanSnapshot:
    status: ExpeditionListScanStatus
    pages_scanned: int
    missions: tuple[MissionRowObservation, ...]

    @property
    def complete(self) -> bool:
        return self.status is ExpeditionListScanStatus.COMPLETE


class ExpeditionListScanAccumulator:
    """Deduplicate overlapping pages and require verified scroll coverage.

    The accumulator rejects non-progressing/backward/repeated pages, changes in
    measured viewport/content geometry, and jumps larger than one viewport.
    It only reports COMPLETE when the visual bottom gate and scroll geometry
    agree. This validates page aggregation, not the accuracy of OCR or visual
    row detection.
    """

    def __init__(self, *, tolerance_px: float = 1.0) -> None:
        if isinstance(tolerance_px, bool) or not isinstance(tolerance_px, (int, float)):
            raise ExpeditionListScanError("tolerance_px must be a positive finite number")
        if not math.isfinite(tolerance_px) or tolerance_px <= 0:
            raise ExpeditionListScanError("tolerance_px must be a positive finite number")
        self._tolerance = float(tolerance_px)
        self._pages: list[ExpeditionListPageObservation] = []
        self._missions: dict[int, MissionRowObservation] = {}
        self._complete = False

    def add_page(self, page: ExpeditionListPageObservation) -> ExpeditionListScanSnapshot:
        if self._complete:
            raise ExpeditionListScanError("scan is already complete")
        if not isinstance(page, ExpeditionListPageObservation):
            raise ExpeditionListScanError("page must be an ExpeditionListPageObservation")

        if not self._pages:
            if abs(page.scroll_top_px) > self._tolerance:
                raise ExpeditionListScanError("the first page must start at the top of the list")
        else:
            previous = self._pages[-1]
            if abs(page.viewport_height_px - previous.viewport_height_px) > self._tolerance:
                raise ExpeditionListScanError("viewport height changed during the scan")
            if abs(page.content_height_px - previous.content_height_px) > self._tolerance:
                raise ExpeditionListScanError("content height changed during the scan")
            delta = page.scroll_top_px - previous.scroll_top_px
            if delta <= self._tolerance:
                raise ExpeditionListScanError("scroll position did not advance")
            if delta > page.viewport_height_px + self._tolerance:
                raise ExpeditionListScanError("scroll jump exceeds one viewport; list coverage is unproven")

        if page.bottom_confirmed != page.geometry_reaches_bottom:
            raise ExpeditionListScanError("visual bottom confirmation conflicts with measured scroll geometry")

        for row in page.rows:
            existing = self._missions.get(row.mission_id)
            if existing is not None and existing.name != row.name:
                raise ExpeditionListScanError(
                    f"mission {row.mission_id} has conflicting names across pages"
                )
            self._missions.setdefault(row.mission_id, row)

        self._pages.append(page)
        self._complete = page.bottom_confirmed
        return self.snapshot()

    def snapshot(self) -> ExpeditionListScanSnapshot:
        return ExpeditionListScanSnapshot(
            status=(
                ExpeditionListScanStatus.COMPLETE
                if self._complete
                else ExpeditionListScanStatus.IN_PROGRESS
            ),
            pages_scanned=len(self._pages),
            missions=tuple(self._missions.values()),
        )

    def finish(self) -> ExpeditionListScanSnapshot:
        result = self.snapshot()
        if not result.complete:
            raise ExpeditionListScanError("mission catalogue is incomplete: verified list bottom not reached")
        return result
