"""The countdown reader: reads what the client shows, or refuses to guess.

The early-completion rule is decided by the displayed remaining time, so this
reader is the only admissible source for it. What is tested here is the reading
contract, not the real font:

* an uncalibrated reader returns ``None`` - it never invents a number and never
  falls back to a locally computed timer;
* a cell that does not match confidently returns ``None`` for the whole reading
  (one ambiguous cell would silently shift the value);
* glyph descriptors are bound to a fixed cell grid, so a misaligned box produces
  no reading rather than a plausible wrong one.

Accuracy against the client's real font is a *calibration* question and is not
claimed here: ``tools/capture_expedition_countdown.py`` records the glyphs from a
live frame, and until that has been run every read is ``None`` by design.
"""
from __future__ import annotations

import unittest

from kancollebot.adapters.poi.expedition_countdown_vision import (
    CountdownReader,
    CountdownTemplateError,
    CountdownTemplates,
)
from kancollebot.adapters.poi.visual_fingerprint import compute_grid_fingerprint
from tests.test_home_port_button_vision import _encode_rgba

ROI = (0, 0, 40, 12)
CELL_WIDTH = 8
CELL_HEIGHT = 12

#: A tiny "font": each glyph is a distinct flat fill, which is all the reader
#: needs to prove the plumbing. It is NOT a claim about the real game font.
GLYPH_COLOURS = {
    "0": (10, 10, 10),
    "1": (60, 10, 10),
    "2": (110, 10, 10),
    "3": (160, 10, 10),
    "4": (210, 10, 10),
    "5": (10, 60, 10),
    "6": (10, 110, 10),
    "7": (10, 160, 10),
    "8": (10, 210, 10),
    "9": (60, 60, 10),
    ":": (10, 10, 60),
}


def frame_for(text: str, *, colours=None) -> str:
    palette = dict(GLYPH_COLOURS if colours is None else colours)
    width, height = ROI[2], ROI[3]
    rows = []
    for _y in range(height):
        row = bytearray()
        for character in text:
            colour = palette[character]
            row.extend((bytes(colour) + b"\xff") * CELL_WIDTH)
        row.extend(b"\x00\x00\x00\xff" * (width - CELL_WIDTH * len(text)))
        rows.append(bytes(row))
    return _encode_rgba(width, height, rows)


def templates_for(text: str = "0:12") -> CountdownTemplates:
    payload = frame_for(text)
    glyphs = {}
    for index, character in enumerate(text):
        box = (index * CELL_WIDTH, 0, CELL_WIDTH, CELL_HEIGHT)
        glyphs[character] = compute_grid_fingerprint(payload, crop=box, grid=(4, 4))
    return CountdownTemplates(
        roi=ROI,
        cell_width=CELL_WIDTH,
        cell_height=CELL_HEIGHT,
        glyphs=glyphs,
        minimum_score=0.99,
        cells=len(text),
        grid=(4, 4),
    )


# A separate, taller region used for grid / vertical-column layouts.
GRID_ROI = (0, 0, 16, 96)
GRID_CELL_WIDTH = 8
GRID_CELL_HEIGHT = 12


def frame_for_grid(text: str, *, rows: int) -> str:
    cols = len(text) // rows
    palette = dict(GLYPH_COLOURS)
    width, height = GRID_ROI[2], GRID_ROI[3]
    pixel_rows: list[bytes] = []
    for cell_row in range(rows):
        characters = text[cell_row * cols:(cell_row + 1) * cols]
        for _y in range(GRID_CELL_HEIGHT):
            row = bytearray()
            for character in characters:
                row.extend((bytes(palette[character]) + b"\xff") * GRID_CELL_WIDTH)
            row.extend(b"\x00\x00\x00\xff" * (width - GRID_CELL_WIDTH * cols))
            pixel_rows.append(bytes(row))
    while len(pixel_rows) < height:
        pixel_rows.append(b"\x00\x00\x00\xff" * width)
    return _encode_rgba(width, height, pixel_rows)


def templates_for_grid(text: str, *, rows: int) -> CountdownTemplates:
    payload = frame_for_grid(text, rows=rows)
    cols = len(text) // rows
    glyphs = {}
    for index, character in enumerate(text):
        cell_row = index // cols
        cell_column = index % cols
        box = (
            cell_column * GRID_CELL_WIDTH,
            cell_row * GRID_CELL_HEIGHT,
            GRID_CELL_WIDTH,
            GRID_CELL_HEIGHT,
        )
        glyphs[character] = compute_grid_fingerprint(payload, crop=box, grid=(4, 4))
    return CountdownTemplates(
        roi=GRID_ROI,
        cell_width=GRID_CELL_WIDTH,
        cell_height=GRID_CELL_HEIGHT,
        glyphs=glyphs,
        minimum_score=0.99,
        cells=len(text),
        cell_rows=rows,
        grid=(4, 4),
    )


class ReadingTests(unittest.TestCase):
    def test_an_uncalibrated_reader_refuses_to_read(self):
        reader = CountdownReader(capture=lambda: self.fail("must not capture"))

        self.assertFalse(reader.calibrated)
        self.assertIsNone(reader.read_text())

    def test_a_matching_frame_reads_the_displayed_text(self):
        reader = CountdownReader(
            capture=lambda: frame_for("0:12"), templates=templates_for("0:12")
        )

        self.assertEqual(reader.read_text(), "0:12")

    def test_an_unknown_glyph_produces_no_reading_at_all(self):
        # "9" was never recorded, so its cell cannot be named: the whole read is
        # refused instead of returning a plausible but wrong countdown.
        reader = CountdownReader(
            capture=lambda: frame_for("9:12"), templates=templates_for("0:12")
        )

        self.assertIsNone(reader.read_text())

    def test_a_capture_failure_is_a_refusal_not_an_exception(self):
        def capture():
            raise OSError("cdp closed")

        reader = CountdownReader(capture=capture, templates=templates_for("0:12"))

        self.assertIsNone(reader.read_text())

    def test_a_vertical_column_layout_is_read_end_to_end(self):
        # The client renders HH:MM:SS in vertically stacked cells (one column
        # of eight). The reader must slice that column, not assume a row.
        reader = CountdownReader(
            capture=lambda: frame_for_grid("00:17:32", rows=8),
            templates=templates_for_grid("00:17:32", rows=8),
        )

        self.assertEqual(reader.read_text(), "00:17:32")


class CellBoxLayoutTests(unittest.TestCase):
    def test_default_layout_is_a_single_horizontal_row(self):
        templates = CountdownTemplates(
            roi=ROI,
            cell_width=CELL_WIDTH,
            cell_height=CELL_HEIGHT,
            glyphs={"0": (1, 2, 3)},
            cells=4,
        )

        boxes = templates.cell_boxes()

        self.assertEqual(len(boxes), 4)
        self.assertEqual([box[1] for box in boxes], [0, 0, 0, 0])
        self.assertEqual([box[0] for box in boxes], [0, 8, 16, 24])

    def test_a_grid_layout_stacks_cells_vertically(self):
        templates = CountdownTemplates(
            roi=GRID_ROI,
            cell_width=GRID_CELL_WIDTH,
            cell_height=GRID_CELL_HEIGHT,
            glyphs={"0": (1, 2, 3)},
            cells=8,
            cell_rows=8,
        )

        boxes = templates.cell_boxes()

        self.assertEqual(len(boxes), 8)
        self.assertEqual([box[0] for box in boxes], [0] * 8)
        self.assertEqual(
            [box[1] for box in boxes],
            [0, 12, 24, 36, 48, 60, 72, 84],
        )

    def test_a_two_by_four_grid_mixes_rows_and_columns(self):
        templates = CountdownTemplates(
            roi=(0, 0, 32, 24),
            cell_width=8,
            cell_height=12,
            glyphs={"0": (1, 2, 3)},
            cells=8,
            cell_rows=2,
        )

        boxes = templates.cell_boxes()

        # cols == 4: x repeats 0,8,16,24 across each of the two rows.
        self.assertEqual(
            [box[0] for box in boxes],
            [0, 8, 16, 24, 0, 8, 16, 24],
        )
        self.assertEqual(
            [box[1] for box in boxes],
            [0, 0, 0, 0, 12, 12, 12, 12],
        )

    def test_cell_rows_that_do_not_divide_cells_are_refused(self):
        with self.assertRaises(ValueError):
            CountdownTemplates(
                roi=GRID_ROI,
                cell_width=GRID_CELL_WIDTH,
                cell_height=GRID_CELL_HEIGHT,
                glyphs={"0": (1, 2, 3)},
                cells=8,
                cell_rows=3,
            )


class TemplateDocumentTests(unittest.TestCase):
    def test_a_document_round_trips(self):
        templates = templates_for("0:12")
        document = {
            "version": 1,
            "roi": list(templates.roi),
            "cell_width": templates.cell_width,
            "cell_height": templates.cell_height,
            "cells": templates.cells,
            "minimum_score": templates.minimum_score,
            "glyphs": {name: list(value) for name, value in templates.glyphs.items()},
        }

        document["grid"] = list(templates.grid)
        restored = CountdownTemplates.from_document(document)

        self.assertEqual(restored.roi, templates.roi)
        self.assertEqual(restored.grid, templates.grid)
        self.assertEqual(restored.glyphs, templates.glyphs)
        self.assertEqual(restored.cells, templates.cells)

    def test_a_wrong_version_is_refused(self):
        with self.assertRaises(CountdownTemplateError):
            CountdownTemplates.from_document({"version": 2})

    def test_an_unsupported_glyph_name_is_refused(self):
        document = {
            "version": 1,
            "roi": [0, 0, 40, 12],
            "cell_width": 8,
            "cell_height": 12,
            "cells": 5,
            "glyphs": {"A": [1, 2, 3]},
        }

        with self.assertRaises(CountdownTemplateError):
            CountdownTemplates.from_document(document)

    def test_cells_that_do_not_fit_the_region_are_refused(self):
        with self.assertRaises(ValueError):
            CountdownTemplates(
                roi=(0, 0, 20, 12),
                cell_width=8,
                cell_height=12,
                glyphs={"0": (1, 2, 3)},
                cells=5,
            )

    def test_cell_rows_round_trips_through_the_document(self):
        templates = CountdownTemplates(
            roi=GRID_ROI,
            cell_width=GRID_CELL_WIDTH,
            cell_height=GRID_CELL_HEIGHT,
            glyphs={"0": (1, 2, 3)},
            cells=8,
            cell_rows=8,
        )
        document = {
            "version": 1,
            "roi": list(templates.roi),
            "cell_width": templates.cell_width,
            "cell_height": templates.cell_height,
            "cells": templates.cells,
            "cell_rows": templates.cell_rows,
            "glyphs": {name: list(value) for name, value in templates.glyphs.items()},
            "grid": list(templates.grid),
        }

        restored = CountdownTemplates.from_document(document)

        self.assertEqual(restored.cell_rows, 8)
        self.assertEqual(restored.cell_boxes(), templates.cell_boxes())

    def test_a_document_without_cell_rows_defaults_to_a_single_row(self):
        # Backward compatibility: old templates recorded before cell_rows
        # existed must still load and behave like a horizontal row.
        document = {
            "version": 1,
            "roi": [0, 0, 40, 12],
            "cell_width": 8,
            "cell_height": 12,
            "cells": 5,
            "glyphs": {"0": [1, 2, 3]},
        }

        restored = CountdownTemplates.from_document(document)

        self.assertEqual(restored.cell_rows, 1)
        self.assertEqual([box[1] for box in restored.cell_boxes()], [0, 0, 0, 0, 0])


if __name__ == "__main__":
    unittest.main()
