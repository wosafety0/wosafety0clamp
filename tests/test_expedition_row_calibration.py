"""Offline tests for read-only expedition-row geometry calibration."""
from __future__ import annotations

import io
import struct
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from kancollebot.calibration import (
    PngError,
    RgbImage,
    RowCalibrationError,
    calibrate_rows,
    dark_row_projection,
    decode_png,
    detect_rows,
    encode_png,
    fit_row_grid,
    stack_vertically,
)
from kancollebot.calibration.expedition_rows import ExpeditionRow
from tools.calibrate_expedition_rows import main

WHITE = 255
BLACK = 0
BAND_LEFT = 100
BAND_RIGHT = 300


def _image_with_bands(
    tops: list[int], *, width: int = 400, height: int = 400, band_height: int = 21
) -> RgbImage:
    """White canvas with a black horizontal band starting at each top."""
    pixels = bytearray([WHITE]) * (width * height * 3)
    for top in tops:
        if top < 0 or top + band_height > height:
            raise ValueError("band is outside the canvas")
        for y in range(top, top + band_height):
            start = (y * width + BAND_LEFT) * 3
            end = (y * width + BAND_RIGHT) * 3
            pixels[start:end] = bytes(end - start)
    return RgbImage(width, height, 3, bytes(pixels))


def _uniform_tops(count: int, *, first: int = 20, pitch: int = 45) -> list[int]:
    return [first + pitch * index for index in range(count)]


class RgbImageTests(unittest.TestCase):
    def test_rejects_pixel_buffer_of_wrong_size(self):
        with self.assertRaisesRegex(ValueError, "pixel buffer"):
            RgbImage(2, 2, 3, b"\x00" * 8)

    def test_rejects_unsupported_channel_count(self):
        with self.assertRaisesRegex(ValueError, "channels"):
            RgbImage(1, 1, 5, b"\x00" * 5)

    def test_luminance_rejects_out_of_range_coordinate(self):
        image = _image_with_bands([20])
        with self.assertRaisesRegex(ValueError, "outside the image"):
            image.luminance(400, 0)


class ProjectionTests(unittest.TestCase):
    def test_dark_pixels_are_counted_per_row(self):
        image = _image_with_bands([20])
        projection = dark_row_projection(image, x_range=(BAND_LEFT, BAND_RIGHT))
        self.assertEqual(len(projection), image.height)
        self.assertEqual(projection[20], BAND_RIGHT - BAND_LEFT)
        self.assertEqual(projection[0], 0)
        # band_height is 21, so rows 20..40 inclusive are painted.
        self.assertEqual(projection[40], BAND_RIGHT - BAND_LEFT)
        self.assertEqual(projection[41], 0)

    def test_projection_columns_must_fit_inside_the_image(self):
        image = _image_with_bands([20])
        with self.assertRaisesRegex(ValueError, "outside the image"):
            dark_row_projection(image, x_range=(300, 900))

    def test_threshold_must_be_a_byte(self):
        image = _image_with_bands([20])
        with self.assertRaisesRegex(ValueError, "dark_threshold"):
            dark_row_projection(
                image, x_range=(BAND_LEFT, BAND_RIGHT), dark_threshold=300
            )


class DetectRowTests(unittest.TestCase):
    def test_detects_each_uniform_band_separately(self):
        image = _image_with_bands(_uniform_tops(8))
        projection = dark_row_projection(image, x_range=(BAND_LEFT, BAND_RIGHT))
        rows = detect_rows(projection, y_range=(0, image.height))
        self.assertEqual(len(rows), 8)
        self.assertEqual([row.index for row in rows], list(range(1, 9)))

    def test_band_center_matches_the_drawn_band(self):
        image = _image_with_bands([20])
        projection = dark_row_projection(image, x_range=(BAND_LEFT, BAND_RIGHT))
        rows = detect_rows(projection, y_range=(0, image.height))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].top, 20)
        self.assertEqual(rows[0].bottom, 40)
        self.assertEqual(rows[0].center, 30.0)

    def test_oversized_merge_gap_welds_neighbouring_bands(self):
        image = _image_with_bands(_uniform_tops(4))
        projection = dark_row_projection(image, x_range=(BAND_LEFT, BAND_RIGHT))
        separated = detect_rows(projection, y_range=(0, image.height), merge_gap=2)
        welded = detect_rows(projection, y_range=(0, image.height), merge_gap=40)
        self.assertEqual(len(separated), 4)
        self.assertLess(len(welded), len(separated))

    def test_tiny_split_inside_one_band_is_remerged(self):
        image = _image_with_bands([20, 65], band_height=15)
        projection = list(dark_row_projection(image, x_range=(BAND_LEFT, BAND_RIGHT)))
        # Punch a one-row hole in the middle of the first band.
        projection[27] = 0
        rows = detect_rows(
            tuple(projection), y_range=(0, image.height), min_height=3, merge_gap=2
        )
        self.assertEqual(len(rows), 2)

    def test_filtered_fragments_do_not_leave_index_holes(self):
        # A short fragment between two real rows must be dropped, and the kept
        # rows must still be numbered 1..N so the straight-line fit is valid.
        image = _image_with_bands([20, 45, 70], band_height=20)
        projection = list(dark_row_projection(image, x_range=(BAND_LEFT, BAND_RIGHT)))
        for y in range(41, 44):
            projection[y] = BAND_RIGHT - BAND_LEFT
        rows = detect_rows(tuple(projection), y_range=(0, image.height), min_height=12)
        self.assertEqual([row.index for row in rows], list(range(1, len(rows) + 1)))

    def test_bands_below_min_height_are_dropped(self):
        image = _image_with_bands([20], band_height=1)
        projection = dark_row_projection(image, x_range=(BAND_LEFT, BAND_RIGHT))
        rows = detect_rows(projection, y_range=(0, image.height), min_height=3)
        self.assertEqual(rows, ())

    def test_search_range_must_fit_inside_the_projection(self):
        image = _image_with_bands([20])
        projection = dark_row_projection(image, x_range=(BAND_LEFT, BAND_RIGHT))
        with self.assertRaisesRegex(ValueError, "search rows"):
            detect_rows(projection, y_range=(0, image.height + 1))


class FitTests(unittest.TestCase):
    def _rows(self, centers: list[float]) -> tuple[ExpeditionRow, ...]:
        return tuple(
            ExpeditionRow(index=position, top=0, bottom=0, center=center)
            for position, center in enumerate(centers, start=1)
        )

    def test_perfectly_uniform_rows_have_zero_residual(self):
        grid = fit_row_grid(self._rows([100.0, 145.0, 190.0, 235.0]))
        self.assertAlmostEqual(grid.pitch, 45.0)
        self.assertAlmostEqual(grid.max_residual, 0.0)
        self.assertAlmostEqual(grid.max_pitch_error, 0.0)
        self.assertAlmostEqual(grid.expected_center(3), 190.0)

    def test_outlier_row_shows_up_in_residuals(self):
        grid = fit_row_grid(self._rows([100.0, 145.0, 195.0, 235.0]))
        self.assertGreater(grid.max_residual, 3.0)
        self.assertEqual(len(grid.residuals), 4)

    def test_single_row_yields_zero_pitch(self):
        grid = fit_row_grid(self._rows([120.0]))
        self.assertEqual(grid.pitch, 0.0)
        self.assertEqual(grid.first_center, 120.0)
        self.assertEqual(grid.pitch_errors, ())

    def test_empty_rows_are_rejected(self):
        with self.assertRaises(RowCalibrationError):
            fit_row_grid(())

    def test_nearest_row_reports_signed_delta(self):
        grid = fit_row_grid(self._rows([100.0, 145.0, 190.0]))
        row, delta = grid.nearest_row(192.0)
        self.assertEqual(row.index, 3)
        self.assertAlmostEqual(delta, 2.0)

    def test_expected_center_rejects_non_positive_index(self):
        grid = fit_row_grid(self._rows([100.0, 145.0]))
        with self.assertRaisesRegex(ValueError, "positive integer"):
            grid.expected_center(0)


class CalibrateTests(unittest.TestCase):
    def test_calibrate_rows_reports_the_synthetic_grid(self):
        image = _image_with_bands(_uniform_tops(8))
        grid = calibrate_rows(
            image, x_range=(BAND_LEFT, BAND_RIGHT), y_range=(0, image.height)
        )
        self.assertEqual(grid.count, 8)
        self.assertAlmostEqual(grid.first_center, 30.0)
        self.assertAlmostEqual(grid.last_center, 345.0)
        self.assertAlmostEqual(grid.pitch, 45.0)
        self.assertLess(grid.max_residual, 0.01)

    def test_empty_image_raises_calibration_error(self):
        image = RgbImage(400, 400, 3, bytes([WHITE]) * (400 * 400 * 3))
        with self.assertRaisesRegex(RowCalibrationError, "no text rows"):
            calibrate_rows(image, x_range=(BAND_LEFT, BAND_RIGHT), y_range=(0, 400))


class PngRoundTripTests(unittest.TestCase):
    def test_encode_then_decode_preserves_pixels(self):
        image = _image_with_bands([20, 65])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.png"
            encode_png(path, image)
            restored = decode_png(path)
        self.assertEqual((restored.width, restored.height), (image.width, image.height))
        self.assertEqual(restored.channels, 3)
        self.assertEqual(restored.pixels, image.pixels)

    def test_decode_rejects_non_png_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fake.png"
            path.write_bytes(b"not a png at all")
            with self.assertRaisesRegex(PngError, "not a PNG"):
                decode_png(path)

    def test_crop_returns_requested_window(self):
        image = _image_with_bands([20])
        crop = image.crop(BAND_LEFT, 20, BAND_RIGHT, 41)
        self.assertEqual((crop.width, crop.height), (BAND_RIGHT - BAND_LEFT, 21))
        self.assertEqual(crop.luminance(0, 0), BLACK)

    def test_crop_rejects_empty_window(self):
        image = _image_with_bands([20])
        with self.assertRaisesRegex(ValueError, "crop columns"):
            image.crop(BAND_LEFT, 0, BAND_LEFT, 10)

    def test_stack_places_images_in_order_with_gaps(self):
        top = _image_with_bands([20])
        bottom = _image_with_bands([20])
        stacked = stack_vertically([top, bottom], gap=4)
        self.assertEqual(stacked.height, top.height * 2 + 4)
        self.assertEqual(stacked.luminance(150, 20), BLACK)

    def test_stack_rejects_empty_input(self):
        with self.assertRaisesRegex(ValueError, "at least one image"):
            stack_vertically([])


class ToolTests(unittest.TestCase):
    def _write_screenshot(self, directory: str, count: int = 8) -> Path:
        path = Path(directory) / "screen.png"
        encode_png(path, _image_with_bands(_uniform_tops(count)))
        return path

    def test_main_reports_eight_rows_and_a_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_screenshot(directory)
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = main([
                    "--image", str(path),
                    "--x-range", f"{BAND_LEFT},{BAND_RIGHT}",
                    "--y-range", "0,400",
                    "--labels", "01,02,03,04,05,06,07,A1",
                    "--anchor-y", "200",
                ])
        output = buffer.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("rows detected    : 8", output)
        self.assertIn("01: (350, 30.0)", output)
        self.assertIn("A1: (350, 345.0)", output)

    def test_label_count_mismatch_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_screenshot(directory)
            with redirect_stdout(io.StringIO()):
                code = main([
                    "--image", str(path),
                    "--x-range", f"{BAND_LEFT},{BAND_RIGHT}",
                    "--y-range", "0,400",
                    "--labels", "01,02",
                ])
        self.assertEqual(code, 2)

    def test_missing_rows_exit_with_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "blank.png"
            encode_png(path, RgbImage(400, 400, 3, bytes([WHITE]) * (400 * 400 * 3)))
            with redirect_stdout(io.StringIO()):
                code = main([
                    "--image", str(path),
                    "--x-range", f"{BAND_LEFT},{BAND_RIGHT}",
                    "--y-range", "0,400",
                ])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
