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
    "9": (10, 110, 10),
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


if __name__ == "__main__":
    unittest.main()
