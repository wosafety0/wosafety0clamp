"""What the detail panel and the dispatch marks will and will not prove.

These tests exist because the old ``MISSION_SELECTED`` checkpoint passed on
mission ``04`` while mission ``3`` was intended (0.9871 against a whole-panel
fingerprint). Every case below is a way that misidentification could come back:

* a change *outside* the name band must not move the identity (else layout
  evidence is being mistaken for identity evidence again);
* the panel must name its neighbour when the wrong row was clicked;
* a band that is close to a reference but not close enough must fail on score,
  and two references that render alike must fail on margin;
* a running expedition (決定 replaced by 遠征中止/帰還命令) must not look
  confirmable;
* a fleet tab must match the best of *its* captures, because a tab paints a
  state marker while its fleet is away.

Frames are synthesised: a flat 1200x720 PNG with rectangles painted exactly over
the measured crops. Decoding one real frame costs about 0.6 s in pure Python, so
the matching tests use the descriptors a uniformly painted band produces and
only the capture-path tests pay for a real decode.
"""

from __future__ import annotations

import base64
import json
import struct
import tempfile
import unittest
import zlib
from functools import lru_cache
from pathlib import Path

from kancollebot.calibration import (
    DECISION_POINT,
    DEFAULT_CATALOG,
    DEFAULT_DETAIL_PATH,
    DEFAULT_MARKS_PATH,
    FLEET_IDS,
    FLEET_TAB_POINTS,
    MINIMUM_NAME_SCORE,
    NAME_BAND_CROP,
    NAME_BAND_GRID,
    DetailReference,
    DispatchMarks,
    ExpeditionDetailError,
    ExpeditionDetailIndex,
    load_detail_index,
    load_marks,
)

WIDTH, HEIGHT = 1200, 720
BASE_RGB = (12, 14, 16)
NAME_BAND_LENGTH = NAME_BAND_GRID[0] * NAME_BAND_GRID[1] * 3
DECISION_LENGTH = 48 * 8 * 3
FLEET_TAB_LENGTH = 10 * 12 * 3


def _chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def _png_rgb(width: int, height: int, rows: list[bytes]) -> str:
    raw = b"".join(b"\x00" + row for row in rows)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )
    return base64.b64encode(payload).decode("ascii")


@lru_cache(maxsize=64)
def frame(*rectangles: tuple[int, int, int, int, tuple[int, int, int]]) -> str:
    """One flat 1200x720 frame with the given rectangles painted."""
    base_row = bytes(BASE_RGB) * WIDTH
    rows = []
    for y in range(HEIGHT):
        painted = [rect for rect in rectangles if rect[1] <= y < rect[1] + rect[3]]
        if not painted:
            rows.append(base_row)
            continue
        row = bytearray(base_row)
        for x, _y, width, _height, rgb in painted:
            row[x * 3:(x + width) * 3] = bytes(rgb) * width
        rows.append(bytes(row))
    return _png_rgb(WIDTH, HEIGHT, rows)


def band(rgb: tuple[int, int, int]) -> tuple[int, int, int, int, tuple[int, int, int]]:
    x, y, width, height = NAME_BAND_CROP
    return (x, y, width, height, rgb)


def decision(rgb: tuple[int, int, int]) -> tuple[int, int, int, int, tuple[int, int, int]]:
    return (900, 650, 240, 40, rgb)


def tab(fleet_id: int, rgb: tuple[int, int, int]) -> tuple[int, int, int, int, tuple[int, int, int]]:
    centre_x, _centre_y = FLEET_TAB_POINTS[fleet_id]
    return (centre_x - 25, 145, 50, 60, rgb)


BLUE = (30, 90, 200)
RED = (200, 60, 30)
NEAR_BLUE = (30, 90, 150)
WHITE = (240, 240, 240)


def flat_levels(rgb: tuple[int, int, int], length: int = NAME_BAND_LENGTH) -> tuple[int, ...]:
    """The descriptor that one uniformly painted region produces."""
    return tuple(rgb) * (length // 3)


class NameBandIdentityTests(unittest.TestCase):
    def test_a_change_outside_the_name_band_does_not_move_the_identity(self):
        """The old whole-panel checkpoint moved on layout; the band must not."""
        index = ExpeditionDetailIndex()
        index.bind("03", frame(band(BLUE)))
        match = index.require("03", frame(band(BLUE), decision((210, 210, 210))))
        self.assertEqual(match.expedition_id, "03")
        self.assertAlmostEqual(match.score, 1.0)

    def test_a_neighbour_on_screen_is_reported_instead_of_accepted(self):
        index = ExpeditionDetailIndex({
            "03": DetailReference("03", flat_levels(BLUE)),
            "04": DetailReference("04", flat_levels(RED)),
        })
        with self.assertRaisesRegex(ExpeditionDetailError, "the selected panel is 04"):
            index.require("03", frame(band(RED)))

    def test_a_band_that_resembles_the_reference_but_does_not_match_fails_on_score(self):
        index = ExpeditionDetailIndex({
            "03": DetailReference("03", flat_levels(BLUE)),
            "04": DetailReference("04", flat_levels(RED)),
        })
        with self.assertRaisesRegex(
            ExpeditionDetailError, f"only at .*; below {MINIMUM_NAME_SCORE}"
        ):
            index.require("03", frame(band(NEAR_BLUE)))

    def test_two_references_that_render_alike_fail_on_margin_not_score(self):
        index = ExpeditionDetailIndex({
            "03": DetailReference("03", flat_levels((30, 90, 200))),
            "04": DetailReference("04", flat_levels((30, 90, 196))),
        })
        with self.assertRaisesRegex(ExpeditionDetailError, "outscores 04 by only"):
            index.require("03", frame(band((30, 90, 200))))

    def test_an_unwalked_expedition_is_refused_before_any_scoring(self):
        index = ExpeditionDetailIndex({"03": DetailReference("03", flat_levels(BLUE))})
        with self.assertRaisesRegex(ExpeditionDetailError, "26 has no detail reference"):
            index.require("26", frame(band(BLUE)))

    def test_an_empty_index_refuses_to_name_anything(self):
        with self.assertRaisesRegex(ExpeditionDetailError, "no expedition detail references"):
            ExpeditionDetailIndex().match(frame(band(BLUE)))

    def test_binding_the_same_expedition_again_replaces_its_capture(self):
        index = ExpeditionDetailIndex()
        index.bind("03", frame(band(RED)))
        self.assertEqual(index.expedition_ids, ("03",))
        self.assertEqual(index.require("03", frame(band(RED))).expedition_id, "03")
        self.assertEqual(index.reference("03").fingerprint, flat_levels(RED))

    def test_a_frame_that_is_not_a_png_is_refused(self):
        with self.assertRaisesRegex(ExpeditionDetailError, "cannot read the name band"):
            ExpeditionDetailIndex().bind("03", "not-a-png")


class DispatchMarkTests(unittest.TestCase):
    def static_marks(self, *, decision_levels=None, tabs=None):
        return DispatchMarks(
            decision=decision_levels,
            fleet_tabs=tabs or {},
        )

    def test_the_decision_control_needs_a_reference_first(self):
        with self.assertRaisesRegex(ExpeditionDetailError, "no 決定 reference"):
            self.static_marks().require_decision(frame(decision(BLUE)))

    def test_a_captured_decision_is_recognised_on_the_same_frame(self):
        marks = DispatchMarks()
        marks.capture_decision(frame(decision(BLUE)))
        self.assertEqual(marks.decision, flat_levels(BLUE, DECISION_LENGTH))
        self.assertAlmostEqual(marks.require_decision(frame(decision(BLUE))).score, 1.0)

    def test_a_running_expedition_does_not_look_confirmable(self):
        """遠征中止/帰還命令 replaces 決定, and measured 0.79 against it."""
        marks = self.static_marks(decision_levels=flat_levels(BLUE, DECISION_LENGTH))
        with self.assertRaisesRegex(ExpeditionDetailError, "already running"):
            marks.require_decision(frame((900, 650, 240, 15, (20, 20, 20))))

    def test_a_captured_fleet_tab_is_recognised_on_the_same_frame(self):
        marks = DispatchMarks()
        marks.add_fleet_tab(3, frame(tab(3, WHITE)))
        self.assertEqual(marks.fleet_tabs[3], [flat_levels(WHITE, FLEET_TAB_LENGTH)])
        self.assertEqual(
            marks.require_fleet(3, frame(tab(3, WHITE))).detail, "fleet 3 tab recognised"
        )

    def test_a_fleet_tab_matches_the_best_of_its_captures(self):
        """The tab paints a state marker while its fleet is away."""
        marks = self.static_marks(tabs={
            3: [flat_levels(WHITE, FLEET_TAB_LENGTH), flat_levels((255, 180, 40), FLEET_TAB_LENGTH)],
        })
        self.assertAlmostEqual(
            marks.require_fleet(3, frame(tab(3, WHITE))).score, 1.0
        )
        self.assertAlmostEqual(
            marks.require_fleet(3, frame(tab(3, (255, 180, 40)))).score, 1.0
        )

    def test_another_fleet_being_selected_is_reported(self):
        marks = self.static_marks(tabs={
            2: [flat_levels((90, 30, 30), FLEET_TAB_LENGTH)],
            3: [flat_levels(WHITE, FLEET_TAB_LENGTH)],
        })
        with self.assertRaisesRegex(ExpeditionDetailError, "fleet 3 is selected, not 2"):
            marks.require_fleet(2, frame(tab(3, WHITE)))

    def test_a_frame_matching_no_tab_fails_closed(self):
        marks = self.static_marks(tabs={
            2: [flat_levels((90, 30, 30), FLEET_TAB_LENGTH)],
            3: [flat_levels(WHITE, FLEET_TAB_LENGTH)],
        })
        with self.assertRaisesRegex(ExpeditionDetailError, "no fleet tab matched above"):
            marks.require_fleet(3, frame(band(BLUE)))

    def test_fleet_1_cannot_be_proven_because_it_cannot_be_dispatched(self):
        marks = self.static_marks(tabs={2: [flat_levels((90, 30, 30), FLEET_TAB_LENGTH)]})
        with self.assertRaisesRegex(ExpeditionDetailError, "cannot run an expedition"):
            marks.require_fleet(1, frame(tab(2, (90, 30, 30))))

    def test_a_fleet_tab_cannot_be_captured_for_fleet_1(self):
        with self.assertRaisesRegex(ExpeditionDetailError, "no calibrated tab"):
            DispatchMarks().add_fleet_tab(1, frame(tab(2, (90, 30, 30))))

    def test_marks_without_tabs_refuse_to_prove_a_fleet(self):
        marks = self.static_marks()
        self.assertEqual(marks.fleets(), ())
        with self.assertRaisesRegex(ExpeditionDetailError, "no fleet tab references"):
            marks.require_fleet(3, frame(tab(3, WHITE)))


class DetailDocumentTests(unittest.TestCase):
    def test_a_detail_index_round_trips_through_a_compact_file(self):
        index = ExpeditionDetailIndex()
        index.bind("03", frame(band(BLUE)))
        index.bind("D1", frame(band(RED)))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "details.json"
            index.save(path)
            text = path.read_text(encoding="utf-8")
            self.assertIn('"fingerprint_b64"', text)
            # 2,880 levels per descriptor are stored as bytes, not as integers.
            self.assertLess(len(text), 12000)
            restored = load_detail_index(path)
        self.assertEqual(restored.expedition_ids, ("03", "D1"))
        self.assertEqual(restored.reference("03").fingerprint, index.reference("03").fingerprint)
        self.assertEqual(restored.reference("D1").fingerprint, flat_levels(RED))
        self.assertEqual(restored.crop, NAME_BAND_CROP)
        self.assertEqual(restored.grid, NAME_BAND_GRID)

    def test_marks_round_trip_including_every_fleet_capture(self):
        marks = DispatchMarks()
        marks.capture_decision(frame(decision(BLUE)))
        for fleet_id, rgb in ((2, (90, 30, 30)), (3, WHITE)):
            marks.add_fleet_tab(fleet_id, frame(tab(fleet_id, rgb)))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "marks.json"
            marks.save(path)
            restored = load_marks(path)
        self.assertEqual(restored.fleets(), (2, 3))
        self.assertEqual(restored.decision, marks.decision)
        self.assertEqual(restored.fleet_tabs[3], [flat_levels(WHITE, FLEET_TAB_LENGTH)])

    def test_a_missing_file_is_an_empty_index_not_a_crash(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "nothing.json"
            self.assertEqual(len(load_detail_index(missing)), 0)
            self.assertEqual(load_marks(missing).fleets(), ())

    def test_a_document_without_a_version_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "details.json"
            path.write_text(json.dumps({"entries": {}}), encoding="utf-8")
            with self.assertRaisesRegex(ExpeditionDetailError, "not a version 1 detail index"):
                load_detail_index(path)
            marks_path = Path(temporary) / "marks.json"
            marks_path.write_text(json.dumps({"fleet_tabs": {}}), encoding="utf-8")
            with self.assertRaisesRegex(ExpeditionDetailError, "not a version 1 dispatch marks"):
                load_marks(marks_path)

    def test_a_descriptor_that_does_not_fit_its_grid_is_refused(self):
        document = {
            "version": 1,
            "viewport": [1200, 720],
            "name_band": {"crop": list(NAME_BAND_CROP), "grid": [4, 4]},
            "entries": {"03": {"fingerprint_b64": base64.b64encode(bytes(6)).decode("ascii")}},
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "details.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ExpeditionDetailError, "holds 6 levels but its grid needs 48"):
                load_detail_index(path)


class ShippedCalibrationTests(unittest.TestCase):
    """The assets committed beside the code have to describe this account."""

    def test_the_walked_detail_index_covers_fifty_expeditions_compactly(self):
        index = load_detail_index(DEFAULT_DETAIL_PATH)
        numbers = {entry.number for entry in DEFAULT_CATALOG.entries}
        self.assertGreaterEqual(len(index), 50)
        self.assertTrue(set(index.expedition_ids) <= numbers)
        self.assertIn("03", index.expedition_ids)
        for expedition_id in index.expedition_ids:
            self.assertEqual(
                len(index.reference(expedition_id).fingerprint), NAME_BAND_LENGTH
            )
        self.assertLess(DEFAULT_DETAIL_PATH.stat().st_size, 400_000)

    def test_the_marks_cover_every_dispatchable_fleet(self):
        marks = load_marks(DEFAULT_MARKS_PATH)
        self.assertEqual(marks.fleets(), FLEET_IDS)
        self.assertEqual(len(marks.decision), DECISION_LENGTH)
        self.assertEqual(DECISION_POINT, (1030, 670))
        for fleet_id in FLEET_IDS:
            self.assertEqual(len(marks.fleet_tabs[fleet_id][0]), FLEET_TAB_LENGTH)

    def test_the_panel_caption_is_read_from_the_documented_crop(self):
        self.assertEqual(NAME_BAND_CROP, (850, 188, 190, 46))
        self.assertEqual(NAME_BAND_GRID, (64, 15))


if __name__ == "__main__":
    unittest.main()
