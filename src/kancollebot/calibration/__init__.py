"""Read-only screen-geometry calibration helpers (standard library only)."""

from .expedition_rows import (
    DEFAULT_DARK_THRESHOLD,
    DEFAULT_MERGE_GAP,
    DEFAULT_MIN_ROW_HEIGHT,
    DEFAULT_MIN_ROW_PIXELS,
    DEFAULT_PROJECTION_X,
    DEFAULT_SEARCH_Y,
    ExpeditionRow,
    RowCalibrationError,
    RowGrid,
    calibrate_rows,
    dark_row_projection,
    detect_rows,
    fit_row_grid,
)
from .png import PngError, RgbImage, decode_png, encode_png, stack_vertically

__all__ = [
    "DEFAULT_DARK_THRESHOLD",
    "DEFAULT_MERGE_GAP",
    "DEFAULT_MIN_ROW_HEIGHT",
    "DEFAULT_MIN_ROW_PIXELS",
    "DEFAULT_PROJECTION_X",
    "DEFAULT_SEARCH_Y",
    "ExpeditionRow",
    "PngError",
    "RgbImage",
    "RowCalibrationError",
    "RowGrid",
    "calibrate_rows",
    "dark_row_projection",
    "decode_png",
    "detect_rows",
    "encode_png",
    "fit_row_grid",
    "stack_vertically",
]
