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
    _cell_ink,
    _ink_similarity,
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


def frame_for_boxes(text: str, boxes, *, width: int = 32, height: int = 24,
                    colours=None) -> str:
    """A frame where each explicit box is filled with one glyph's flat colour."""
    palette = dict(GLYPH_COLOURS if colours is None else colours)
    buf = bytearray(b"\x00\x00\x00\xff" * (width * height))
    for character, (x, y, w, h) in zip(text, boxes):
        colour = bytes(palette[character]) + b"\xff"
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                index = (yy * width + xx) * 4
                buf[index:index + 4] = colour
    rows = [bytes(buf[y * width * 4:(y + 1) * width * 4]) for y in range(height)]
    return _encode_rgba(width, height, rows)


#: The real client shape: HH:MM on the first line, SS on the second. We skip the
#: colon (a fixed literal) and read six digit cells, grouped 2/2/2.
SIX_BOXES = (
    (0, 0, 8, 12),
    (8, 0, 8, 12),
    (16, 0, 8, 12),
    (24, 0, 8, 12),
    (8, 12, 8, 12),
    (16, 12, 8, 12),
)


def templates_for_boxes(text: str) -> CountdownTemplates:
    payload = frame_for_boxes(text, SIX_BOXES)
    glyphs = {}
    for character, box in zip(text, SIX_BOXES):
        glyphs[character] = compute_grid_fingerprint(payload, crop=box, grid=(4, 4))
    return CountdownTemplates(
        roi=(0, 0, 32, 24),
        cell_width=8,
        cell_height=12,
        glyphs=glyphs,
        minimum_score=0.99,
        cells=len(SIX_BOXES),
        boxes=SIX_BOXES,
        groups=(2, 2, 2),
        grid=(4, 4),
    )


class ExplicitBoxLayoutTests(unittest.TestCase):
    def test_six_explicit_boxes_read_as_hh_mm_ss(self):
        # "002532" laid out as HH:MM / SS must come back as "00:25:32", which is
        # what the display actually renders even though the second colon is not
        # drawn.
        reader = CountdownReader(
            capture=lambda: frame_for_boxes("002532", SIX_BOXES),
            templates=templates_for_boxes("002532"),
        )

        self.assertEqual(reader.read_text(), "00:25:32")

    def test_explicit_boxes_round_trip_through_the_document(self):
        templates = templates_for_boxes("002532")
        document = {
            "version": 1,
            "roi": list(templates.roi),
            "cell_width": templates.cell_width,
            "cell_height": templates.cell_height,
            "cells": templates.cells,
            "cell_rows": 1,
            "boxes": [list(box) for box in templates.boxes],
            "groups": list(templates.groups),
            "glyphs": {name: list(value) for name, value in templates.glyphs.items()},
            "grid": list(templates.grid),
        }

        restored = CountdownTemplates.from_document(document)

        self.assertEqual(restored.boxes, templates.boxes)
        self.assertEqual(restored.groups, (2, 2, 2))
        self.assertEqual(restored.cell_boxes(), list(SIX_BOXES))

    def test_groups_must_sum_to_cells(self):
        with self.assertRaises(ValueError):
            CountdownTemplates(
                roi=(0, 0, 32, 24),
                cell_width=8,
                cell_height=12,
                glyphs={"0": (1, 2, 3)},
                cells=6,
                boxes=SIX_BOXES,
                groups=(2, 2, 1),
            )

    def test_boxes_must_match_the_cell_count(self):
        with self.assertRaises(ValueError):
            CountdownTemplates(
                roi=(0, 0, 32, 24),
                cell_width=8,
                cell_height=12,
                glyphs={"0": (1, 2, 3)},
                cells=5,
                boxes=SIX_BOXES,
            )


def _pattern_frame(width: int, height: int, dark):
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            if dark(x / width, y / height):
                row.extend((0, 0, 0, 255))
            else:
                row.extend((200, 200, 200, 255))
        rows.append(bytes(row))
    return _encode_rgba(width, height, rows)


class InkDescriptorTests(unittest.TestCase):
    #: The real box draws the same digit at two sizes (HH:MM larger, SS smaller);
    #: the descriptor must be size-normalised, else one digit needs two templates.
    def test_the_same_shape_at_two_sizes_is_the_same_descriptor(self):
        left = lambda fx, fy: fx < 0.5
        small = _cell_ink(_pattern_frame(10, 14, left), (0, 0, 10, 14), (8, 12))
        large = _cell_ink(_pattern_frame(20, 28, left), (0, 0, 20, 28), (8, 12))

        self.assertGreater(_ink_similarity(small, large), 0.9)

    def test_a_different_shape_is_a_different_descriptor(self):
        left = lambda fx, fy: fx < 0.5
        right = lambda fx, fy: fx >= 0.5
        left_desc = _cell_ink(_pattern_frame(10, 14, left), (0, 0, 10, 14), (8, 12))
        right_desc = _cell_ink(_pattern_frame(10, 14, right), (0, 0, 10, 14), (8, 12))

        self.assertLess(_ink_similarity(left_desc, right_desc), 0.5)

    def test_binarisation_splits_ink_from_a_bright_background(self):
        # The local-median threshold must mark only the dark bar as ink and leave
        # the bright background empty.
        def bar(fx, fy):
            return fx < 0.3

        desc = _cell_ink(_pattern_frame(10, 14, bar), (0, 0, 10, 14), (8, 12))
        # Left third ink, the rest empty: the first grid columns are saturated.
        self.assertTrue(all(value > 500 for value in desc[:2]))
        self.assertTrue(all(value == 0 for value in desc[-2:]))


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


import argparse  # noqa: E402
import base64  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

# The calibration tool lives under ``tools/``; make it importable from the suite.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.capture_expedition_countdown import cmd_record  # noqa: E402


class RecordToolTests(unittest.TestCase):
    #: A single horizontal row of eight glyphs, matching ``frame_for``'s metric.
    ROI = (0, 0, 64, 12)
    CELL = (8, 12)

    def _write_frame(self, text: str, tmpdir: str, name: str, *, colours=None) -> str:
        # ``frame_for`` is hard-coded to a 40px-wide ROI, which only fits <=5
        # glyphs; build an eight-glyph frame at the wider 64px metric directly.
        palette = dict(GLYPH_COLOURS if colours is None else colours)
        width, height = 64, CELL_HEIGHT
        pixel_rows = []
        for _y in range(height):
            row = bytearray()
            for character in text:
                row.extend((bytes(palette[character]) + b"\xff") * CELL_WIDTH)
            row.extend(b"\x00\x00\x00\xff" * (width - CELL_WIDTH * len(text)))
            pixel_rows.append(bytes(row))
        payload = _encode_rgba(width, height, pixel_rows)
        path = Path(tmpdir) / name
        path.write_bytes(base64.b64decode(payload))
        return str(path)

    def _record(self, frame: str, text: str, out: str, **overrides) -> None:
        args = argparse.Namespace(
            frame=frame,
            roi=self.ROI,
            cell=self.CELL,
            cells=8,
            cell_rows=overrides.pop("cell_rows", 1),
            text=text,
            merge=overrides.pop("merge", False),
            minimum_score=0.93,
            grid=(4, 4),
            out=out,
        )
        for key, value in overrides.items():
            setattr(args, key, value)
        self.assertEqual(cmd_record(args), 0)

    def test_recording_one_frame_binds_only_its_glyphs(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame = self._write_frame("00:00:00", tmp, "a.png")
            out = Path(tmp) / "glyphs.json"
            self._record(frame, "00:00:00", str(out))

            document = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(sorted(document["glyphs"]), ["0", ":"])

    def test_merge_accumulates_glyphs_across_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "glyphs.json"
            first = self._write_frame("00:00:00", tmp, "a.png")
            self._record(first, "00:00:00", str(out))
            second = self._write_frame("11:11:11", tmp, "b.png")
            self._record(second, "11:11:11", str(out), merge=True)

            document = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(sorted(document["glyphs"]), ["0", "1", ":"])
            # The earlier glyphs must survive the merge.
            self.assertIn("0", document["glyphs"])

    def test_merge_refuses_an_incompatible_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "glyphs.json"
            first = self._write_frame("00:00:00", tmp, "a.png")
            self._record(first, "00:00:00", str(out))
            # A different roi makes the grids incomparable.
            second = self._write_frame("11:11:11", tmp, "b.png")
            args = argparse.Namespace(
                frame=second,
                roi=(0, 0, 32, 12),
                cell=self.CELL,
                cells=8,
                cell_rows=1,
                text="11:11:11",
                merge=True,
                minimum_score=0.93,
                grid=(4, 4),
                out=str(out),
            )
            with self.assertRaises(SystemExit):
                cmd_record(args)

    def test_merge_refuses_a_drifted_glyph(self):
        # If a character that was recorded earlier now renders differently, the
        # cell grid has moved between captures; merging would poison the template.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "glyphs.json"
            first = self._write_frame("00:00:00", tmp, "a.png")
            self._record(first, "00:00:00", str(out))
            # Render "0" with a different colour so its descriptor changes.
            second = self._write_frame(
                "00:00:00", tmp, "b.png", colours={**GLYPH_COLOURS, "0": (200, 200, 200)}
            )
            args = argparse.Namespace(
                frame=second,
                roi=self.ROI,
                cell=self.CELL,
                cells=8,
                cell_rows=1,
                text="00:00:00",
                merge=True,
                minimum_score=0.93,
                grid=(4, 4),
                out=str(out),
            )
            with self.assertRaises(SystemExit):
                cmd_record(args)


if __name__ == "__main__":
    unittest.main()
