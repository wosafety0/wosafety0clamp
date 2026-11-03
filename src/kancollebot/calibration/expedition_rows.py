"""Read-only geometric calibration of the expedition list rows.

The expedition list is drawn on a canvas. Neither the DOM nor the
accessibility tree exposes per-row geometry, so row centers must be measured
from pixels. This module projects dark pixels across a horizontal band to find
the candidate text rows, then fits a straight line so callers can address any
row by its display order.

Nothing here sends input, clicks, or mutates game state.
"""
from __future__ import annotations

from dataclasses import dataclass

from .png import RgbImage

DEFAULT_PROJECTION_X = (155, 745)
# Live 1200x720 captures show the header ending at y~231 and the last row
# ending near y~600, so the search band starts below the header.
DEFAULT_SEARCH_Y = (234, 610)
DEFAULT_DARK_THRESHOLD = 110
# Background texture contributes a constant ~6 dark pixels per row, so the
# row-presence threshold must sit above that floor or every row looks like text.
DEFAULT_MIN_ROW_PIXELS = 8
# Row text bodies are ~23px tall while in-row icon fragments are ~4-6px, so a
# height floor keeps the fragments out of the row list.
DEFAULT_MIN_ROW_HEIGHT = 12
# Measured on a live 1200x720 capture: rows are ~44px tall and the gap between
# two rows is only ~1px, so merging across a gap welds the whole column into a
# single band. Keep the default at 0 and widen only when a capture proves that
# an in-row icon split needs re-joining.
DEFAULT_MERGE_GAP = 0


class RowCalibrationError(RuntimeError):
    """The screenshot does not contain a usable row grid."""


@dataclass(frozen=True, slots=True)
class ExpeditionRow:
    """One detected list row, addressed by its 1-based display order."""

    index: int
    top: int
    bottom: int
    center: float

    @property
    def height(self) -> int:
        return self.bottom - self.top + 1


@dataclass(frozen=True, slots=True)
class RowGrid:
    """Detected rows plus the straight-line fit through their centers."""

    rows: tuple[ExpeditionRow, ...]
    pitch: float
    intercept: float
    residuals: tuple[float, ...]
    pitch_errors: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.rows:
            raise ValueError("a row grid needs at least one row")
        if len(self.residuals) != len(self.rows):
            raise ValueError("residuals must match the row count")

    @property
    def count(self) -> int:
        return len(self.rows)

    @property
    def first_center(self) -> float:
        return self.rows[0].center

    @property
    def last_center(self) -> float:
        return self.rows[-1].center

    @property
    def max_residual(self) -> float:
        return max(abs(value) for value in self.residuals)

    @property
    def max_pitch_error(self) -> float:
        return max((abs(value) for value in self.pitch_errors), default=0.0)

    def expected_center(self, index: int) -> float:
        """Return the fitted center for a 1-based display index."""
        if isinstance(index, bool) or not isinstance(index, int) or index < 1:
            raise ValueError("index must be a positive integer")
        return self.intercept + self.pitch * index

    def nearest_row(self, y: float) -> tuple[ExpeditionRow, float]:
        """Return the row closest to ``y`` and the signed distance to it."""
        if isinstance(y, bool) or not isinstance(y, (int, float)):
            raise ValueError("y must be a number")
        best = min(self.rows, key=lambda row: abs(row.center - y))
        return best, y - best.center


def dark_row_projection(
    image: RgbImage,
    *,
    x_range: tuple[int, int] = DEFAULT_PROJECTION_X,
    dark_threshold: int = DEFAULT_DARK_THRESHOLD,
) -> tuple[int, ...]:
    """Count dark pixels per image row inside the horizontal band."""
    left, right = x_range
    if not (0 <= left < right <= image.width):
        raise ValueError("projection columns are outside the image")
    if isinstance(dark_threshold, bool) or not isinstance(dark_threshold, int):
        raise ValueError("dark_threshold must be an integer")
    if not 0 <= dark_threshold <= 255:
        raise ValueError("dark_threshold must be within 0..255")
    channels = image.channels
    pixels = image.pixels
    width = image.width
    counts: list[int] = []
    for y in range(image.height):
        base = y * width * channels
        dark = 0
        for x in range(left, right):
            offset = base + x * channels
            if channels == 1:
                if pixels[offset] < dark_threshold:
                    dark += 1
            elif channels == 2:
                if pixels[offset] < dark_threshold:
                    dark += 1
            elif (pixels[offset] + pixels[offset + 1] + pixels[offset + 2]) // 3 < dark_threshold:
                dark += 1
        counts.append(dark)
    return tuple(counts)


def detect_rows(
    projection: tuple[int, ...],
    *,
    y_range: tuple[int, int] = DEFAULT_SEARCH_Y,
    min_pixels: int = DEFAULT_MIN_ROW_PIXELS,
    min_height: int = DEFAULT_MIN_ROW_HEIGHT,
    merge_gap: int = DEFAULT_MERGE_GAP,
) -> tuple[ExpeditionRow, ...]:
    """Group the projection into text rows and return their centers.

    Bands are split where the dark-pixel count falls below ``min_pixels`` and
    re-merged across gaps no wider than ``merge_gap`` so icons inside one row do
    not split it in two.
    """
    if not projection:
        raise ValueError("projection must not be empty")
    start, stop = y_range
    if not (0 <= start < stop <= len(projection)):
        raise ValueError("search rows are outside the projection")
    for name, value in (("min_pixels", min_pixels), ("min_height", min_height), ("merge_gap", merge_gap)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")

    bands: list[list[int]] = []
    current: list[int] | None = None
    for y in range(start, stop):
        if projection[y] >= min_pixels:
            if current is None:
                current = [y, y]
            else:
                current[1] = y
        else:
            if current is not None:
                bands.append(current)
                current = None
    if current is not None:
        bands.append(current)

    merged: list[list[int]] = []
    for band in bands:
        if merged and merge_gap and band[0] - merged[-1][1] - 1 <= merge_gap:
            merged[-1][1] = band[1]
        else:
            merged.append(list(band))

    # Filter before numbering so a discarded fragment cannot leave a hole in the
    # display order (indices must be 1..N for the straight-line fit).
    kept = [band for band in merged if band[1] - band[0] + 1 >= min_height]
    return tuple(
        ExpeditionRow(
            index=position,
            top=band[0],
            bottom=band[1],
            center=(band[0] + band[1]) / 2,
        )
        for position, band in enumerate(kept, start=1)
    )


def fit_row_grid(rows: tuple[ExpeditionRow, ...]) -> RowGrid:
    """Fit centers against display order with ordinary least squares."""
    if not rows:
        raise RowCalibrationError("no rows were detected")
    count = len(rows)
    if count == 1:
        return RowGrid(
            rows=rows,
            pitch=0.0,
            intercept=rows[0].center,
            residuals=(0.0,),
            pitch_errors=(),
        )
    mean_index = (count + 1) / 2
    mean_center = sum(row.center for row in rows) / count
    numerator = sum(
        (row.index - mean_index) * (row.center - mean_center) for row in rows
    )
    denominator = sum((row.index - mean_index) ** 2 for row in rows)
    if denominator == 0:  # pragma: no cover - indices are always distinct
        raise RowCalibrationError("row indices are degenerate")
    pitch = numerator / denominator
    intercept = mean_center - pitch * mean_index
    residuals = tuple(
        row.center - (intercept + pitch * row.index) for row in rows
    )
    gaps = [rows[index].center - rows[index - 1].center for index in range(1, count)]
    pitch_errors = tuple(gap - pitch for gap in gaps)
    return RowGrid(
        rows=rows,
        pitch=pitch,
        intercept=intercept,
        residuals=residuals,
        pitch_errors=pitch_errors,
    )


def calibrate_rows(
    image: RgbImage,
    *,
    x_range: tuple[int, int] = DEFAULT_PROJECTION_X,
    y_range: tuple[int, int] = DEFAULT_SEARCH_Y,
    dark_threshold: int = DEFAULT_DARK_THRESHOLD,
    min_pixels: int = DEFAULT_MIN_ROW_PIXELS,
    min_height: int = DEFAULT_MIN_ROW_HEIGHT,
    merge_gap: int = DEFAULT_MERGE_GAP,
) -> RowGrid:
    """Project, detect and fit in one call."""
    projection = dark_row_projection(
        image, x_range=x_range, dark_threshold=dark_threshold
    )
    rows = detect_rows(
        projection,
        y_range=y_range,
        min_pixels=min_pixels,
        min_height=min_height,
        merge_gap=merge_gap,
    )
    if not rows:
        raise RowCalibrationError("no text rows were detected in the search band")
    return fit_row_grid(rows)
