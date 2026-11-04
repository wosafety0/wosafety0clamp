"""Offline tests for the read-only expedition-list scanner.

Every test works on synthetic 1200x720 images laid out like the live screen:
eight row slots at a 45px pitch starting at y=250, an area tab strip at the
bottom, and a signature area to the right of the status column.
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from kancollebot.calibration import (
    BoundaryState,
    ExpeditionListError,
    ExpeditionListIndex,
    PageScan,
    RowSignature,
    align_pages,
    check_display_order,
    classify_boundary,
    decode_png,
    detect_area_tabs,
    disp_no_sort_key,
    encode_png,
    find_button_candidates,
    plan_scroll_step,
    row_signature,
    same_view,
    scan_page,
    signature_from_key,
)
from kancollebot.calibration.png import RgbImage
from tools.scan_expedition_list import main

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
PLATE = (128, 150, 139)
ORANGE = (200, 90, 40)

WIDTH = 1200
HEIGHT = 720
ROW_TOP = 250
ROW_PITCH = 45
ROW_HEIGHT = 23
STATUS_LEFT = 160
STATUS_RIGHT = 232
SIGNATURE_LEFT = 233
SIGNATURE_RIGHT = 745
TAB_X0 = 176
TAB_PITCH = 60
TAB_WIDTH = 52
TAB_Y = (666, 692)


def _blank() -> RgbImage:
    return RgbImage(WIDTH, HEIGHT, 3, bytes(WHITE) * (WIDTH * HEIGHT))


def _fill(image: RgbImage, left: int, top: int, right: int, bottom: int, color) -> None:
    pixels = bytearray(image.pixels)
    for y in range(top, bottom):
        for x in range(left, right):
            base = (y * image.width + x) * 3
            pixels[base:base + 3] = bytes(color)
    return RgbImage(image.width, image.height, image.channels, bytes(pixels))


def _draw_row(image: RgbImage, slot: int, marker: int, *, width: int = 2) -> RgbImage:
    """Draw one row whose signature region marks it distinctively."""
    top = ROW_TOP + ROW_PITCH * slot
    image = _fill(image, STATUS_LEFT, top, STATUS_RIGHT, top + ROW_HEIGHT, BLACK)
    left = SIGNATURE_LEFT + 32 * marker
    return _fill(image, left, top, left + 32 * width, top + ROW_HEIGHT, BLACK)


def _draw_tabs(image: RgbImage, active: int | None, *, cells: int = 7) -> RgbImage:
    for index in range(cells):
        left = TAB_X0 + TAB_PITCH * index
        color = ORANGE if index == active else PLATE
        image = _fill(image, left, TAB_Y[0], left + TAB_WIDTH, TAB_Y[1], color)
    return image


def _page(slots: list[int], *, active_tab: int | None = 0, cells: int = 7) -> RgbImage:
    """slots[i] is the marker of the row drawn in slot i."""
    image = _blank()
    for slot, marker in enumerate(slots):
        image = _draw_row(image, slot, marker)
    return _draw_tabs(image, active_tab, cells=cells)


def _scan(markers: list[int], *, active_tab: int | None = 0) -> PageScan:
    return scan_page(_page(markers, active_tab=active_tab))


class SignatureTests(unittest.TestCase):
    def test_fingerprint_is_slot_independent(self) -> None:
        first = _page([3, 4, 5, 6, 7, 8, 9, 10])
        second = _page([11, 12, 3, 4, 5, 6, 7, 8])
        left = scan_page(first)
        right = scan_page(second)
        self.assertEqual(
            left.rows[0].signature.key, right.rows[2].signature.key,
            "the same row content must fingerprint identically in another slot",
        )

    def test_distinct_rows_have_distinct_fingerprints(self) -> None:
        scan = _scan([0, 1, 2, 3, 4, 5, 6, 7])
        keys = {row.signature.key for row in scan.rows}
        self.assertEqual(len(keys), 8)

    def test_distance_and_tolerance(self) -> None:
        base = RowSignature((1, 2, 3), 10)
        near = RowSignature((1, 2, 4), 10)
        far = RowSignature((9, 2, 3), 10)
        self.assertEqual(base.distance(near), 1)
        self.assertTrue(base.matches(near, tolerance=1))
        self.assertFalse(base.matches(near, tolerance=0))
        self.assertFalse(base.matches(far, tolerance=6))

    def test_key_round_trip(self) -> None:
        signature = RowSignature(tuple(range(0, 16)), 4242)
        self.assertEqual(signature_from_key(signature.key), signature)

    def test_malformed_key_is_rejected(self) -> None:
        for text in ("", "zz/1", "01-02", "01/99/1"):
            with self.assertRaises(ValueError):
                signature_from_key(text)

    def test_signature_rejects_out_of_range_bins(self) -> None:
        with self.assertRaises(ValueError):
            RowSignature((200,), 0)
        with self.assertRaises(ValueError):
            RowSignature((), 0)

    def test_signature_rejects_mutable_or_non_integer_bins(self) -> None:
        for bins in ([1, 2], (True,), (1.0,), ("1",)):
            with self.subTest(bins=bins):
                with self.assertRaises(ValueError):
                    RowSignature(bins, 0)

    def test_signature_rejects_non_integer_dark_total(self) -> None:
        for total in (True, 1.0, "1"):
            with self.subTest(total=total):
                with self.assertRaises(ValueError):
                    RowSignature((1,), total)

    def test_row_signature_validates_columns(self) -> None:
        scan = _scan([0])
        image = _page([0])
        with self.assertRaises(ValueError):
            row_signature(image, scan.rows[0], signature_x=(10, 5))


class AreaTabTests(unittest.TestCase):
    def test_selected_tab_is_found_by_orange_label(self) -> None:
        for active in range(7):
            tabs = detect_area_tabs(_page([0], active_tab=active))
            self.assertEqual(tabs.active_index, active)
            self.assertEqual(tabs.count, 7)

    def test_no_tab_highlight_leaves_selection_unknown(self) -> None:
        tabs = detect_area_tabs(_page([0], active_tab=None))
        self.assertIsNone(tabs.active_index)
        self.assertFalse(tabs.selected)

    def test_ambiguous_highlight_is_refused(self) -> None:
        image = _page([0], active_tab=None)
        image = _fill(image, TAB_X0, TAB_Y[0], TAB_X0 + TAB_WIDTH, TAB_Y[1], ORANGE)
        other = TAB_X0 + TAB_PITCH * 3
        image = _fill(image, other, TAB_Y[0], other + TAB_WIDTH, TAB_Y[1], ORANGE)
        self.assertIsNone(detect_area_tabs(image).active_index)

    def test_scan_reports_the_selected_tab(self) -> None:
        self.assertEqual(_scan([0, 1], active_tab=2).tab, 2)

    def test_tab_centers_follow_the_pitch(self) -> None:
        tabs = detect_area_tabs(_page([0]))
        centers = [center[0] for center in tabs.centers]
        self.assertEqual(centers, [TAB_X0 + TAB_PITCH * index + TAB_WIDTH // 2 for index in range(7)])


class ScanPageTests(unittest.TestCase):
    def test_rows_are_numbered_in_screen_order(self) -> None:
        scan = _scan([0, 1, 2])
        self.assertEqual([row.index for row in scan.rows], [1, 2, 3])
        self.assertEqual([round(row.center) for row in scan.rows],
                         [ROW_TOP + ROW_PITCH * slot + ROW_HEIGHT // 2 for slot in range(3)])

    def test_slots_stay_on_the_measured_pitch(self) -> None:
        scan = _scan(list(range(8)))
        gaps = [scan.rows[index].center - scan.rows[index - 1].center for index in range(1, 8)]
        for gap in gaps:
            self.assertAlmostEqual(gap, ROW_PITCH, delta=1.0)

    def test_full_page_is_not_reported_as_an_end(self) -> None:
        scan = _scan(list(range(8)))
        self.assertTrue(scan.is_full)
        boundary = classify_boundary(scan)
        self.assertIs(boundary.state, BoundaryState.UNKNOWN)
        self.assertEqual(boundary.reason, "full_page_without_scroll_affordance")

    def test_short_page_without_end_marker_remains_unknown(self) -> None:
        boundary = classify_boundary(_scan([0, 1, 2]))
        self.assertIs(boundary.state, BoundaryState.UNKNOWN)
        self.assertEqual(boundary.reason, "short_page_without_verified_end_marker")

    def test_short_page_with_scroll_affordance_means_more_below(self) -> None:
        image = _page([0, 1, 2])
        image = _fill(image, 760, 604, 772, 614, BLACK)
        scan = scan_page(image)
        self.assertTrue(scan.scroll_candidates)
        self.assertIs(classify_boundary(scan).state, BoundaryState.MORE_BELOW)

    def test_scroll_affordance_means_more_below(self) -> None:
        image = _page(list(range(8)))
        image = _fill(image, 760, 604, 772, 614, BLACK)
        scan = scan_page(image)
        self.assertTrue(scan.scroll_candidates)
        self.assertIs(classify_boundary(scan).state, BoundaryState.MORE_BELOW)

    def test_empty_page_reports_unknown(self) -> None:
        scan = scan_page(_page([]))
        self.assertEqual(scan.rows, ())
        self.assertIs(classify_boundary(scan).state, BoundaryState.UNKNOWN)

    def test_capacity_can_be_overridden(self) -> None:
        scan = _scan([0, 1, 2])
        self.assertIs(classify_boundary(scan, capacity=3).state, BoundaryState.UNKNOWN)

    def test_scan_validates_capacity(self) -> None:
        with self.assertRaises(ValueError):
            PageScan(rows=(), tabs=detect_area_tabs(_page([])),
                     scroll_candidates=(), image_size=(WIDTH, HEIGHT), capacity=0)


class ButtonCandidateTests(unittest.TestCase):
    def test_small_glyph_is_reported(self) -> None:
        image = _fill(_blank(), 760, 600, 770, 610, BLACK)
        candidates = find_button_candidates(image, region=(740, 590, 790, 620))
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].center, (764, 604))
        self.assertEqual((candidates[0].width, candidates[0].height), (10, 10))

    def test_oversized_blob_is_ignored(self) -> None:
        image = _fill(_blank(), 740, 590, 790, 620, BLACK)
        self.assertEqual(find_button_candidates(image, region=(740, 590, 790, 620)), ())

    def test_region_is_validated(self) -> None:
        with self.assertRaises(ValueError):
            find_button_candidates(_blank(), region=(900, 100, 800, 200))

    def test_scroll_proposal_never_invents_a_point(self) -> None:
        proposal = plan_scroll_step(_scan(list(range(8))))
        self.assertEqual(proposal.action, "blocked_no_scroll_affordance")
        self.assertIsNone(proposal.point)

    def test_scroll_proposal_needs_confirmation_for_a_candidate(self) -> None:
        image = _fill(_page(list(range(8))), 760, 604, 772, 614, BLACK)
        scan = scan_page(image)
        proposal = plan_scroll_step(scan)
        self.assertEqual(proposal.action, "confirm_candidate")
        self.assertIsNotNone(proposal.point)

    def test_confirmed_point_is_used_verbatim(self) -> None:
        proposal = plan_scroll_step(_scan(list(range(8))), confirmed_point=(1030, 612))
        self.assertEqual(proposal.action, "click_confirmed_point")
        self.assertEqual(proposal.point, (1030, 612))

    def test_short_page_without_scroll_evidence_does_not_invent_a_point(self) -> None:
        proposal = plan_scroll_step(_scan([0, 1, 2]))
        self.assertEqual(proposal.action, "blocked_no_scroll_affordance")
        self.assertIsNone(proposal.point)

    def test_scroll_direction_is_validated(self) -> None:
        with self.assertRaises(ValueError):
            plan_scroll_step(_scan([0]), direction="sideways")


class AlignmentTests(unittest.TestCase):
    def test_shift_is_recovered_from_the_overlap(self) -> None:
        first = _scan([0, 1, 2, 3, 4, 5, 6, 7])
        second = _scan([2, 3, 4, 5, 6, 7, 8, 9])
        shift, support = align_pages(first, second)
        self.assertEqual(shift, 2)
        self.assertEqual(support, 6)

    def test_pages_without_overlap_are_refused(self) -> None:
        first = _scan([0, 1, 2, 3, 4, 5, 6, 7])
        second = _scan([20, 21, 22, 23, 24, 25, 26, 27])
        with self.assertRaises(ExpeditionListError):
            align_pages(first, second)

    def test_single_shared_row_cannot_prove_a_shift(self) -> None:
        first = _scan([0, 1], active_tab=0)
        second = _scan([1, 9], active_tab=0)
        with self.assertRaises(ExpeditionListError):
            align_pages(first, second)

    def test_pages_from_different_tabs_are_refused(self) -> None:
        first = _scan([0, 1, 2, 3], active_tab=0)
        second = _scan([2, 3, 4, 5], active_tab=1)
        with self.assertRaises(ExpeditionListError):
            align_pages(first, second)

    def test_unchanged_view_is_detected(self) -> None:
        first = _scan([0, 1, 2])
        self.assertTrue(same_view(first, _scan([0, 1, 2])))
        self.assertFalse(same_view(first, _scan([0, 1, 3])))
        self.assertFalse(same_view(first, _scan([0, 1, 2, 3])))


class IndexTests(unittest.TestCase):
    def test_labelling_merges_pages_and_deduplicates(self) -> None:
        index = ExpeditionListIndex()
        first = index.add_page(_scan([0, 1, 2, 3]))
        index.label_row(0, 1, "01")
        index.label_row(0, 2, "02")
        index.label_row(0, 3, "03")
        index.label_row(0, 4, "05")
        self.assertEqual(first.offset, 0)
        second = index.add_page(_scan([2, 3, 4, 5]))
        self.assertEqual(second.offset, 2)
        self.assertEqual(second.ids[:2], ("03", "05"))
        self.assertEqual(second.ids[2:], (None, None))
        index.label_row(1, 3, "A1")
        index.label_row(1, 4, "A2")
        entries = index.entries()
        self.assertEqual([entry.expedition_id for entry in entries], ["01", "02", "03", "05", "A1", "A2"])
        by_id = {entry.expedition_id: entry for entry in entries}
        self.assertFalse(by_id["01"].visible)
        self.assertIsNone(by_id["01"].center)
        self.assertTrue(by_id["A1"].visible)
        self.assertEqual(by_id["A1"].scroll_page, 1)
        self.assertEqual(round(by_id["A1"].center[1]), ROW_TOP + ROW_PITCH * 2 + ROW_HEIGHT // 2)

    def test_earlier_pages_are_resolved_when_a_row_is_labelled(self) -> None:
        index = ExpeditionListIndex()
        index.add_page(_scan([0, 1, 2, 3, 4, 5]))
        index.add_page(_scan([2, 3, 4, 5, 6, 7]))
        index.label_row(1, 1, "03")
        self.assertEqual(index.pages[0].ids[2], "03")
        self.assertEqual(index.unbound_rows[0], (0, 1))

    def test_rebinding_a_signature_to_another_number_is_refused(self) -> None:
        index = ExpeditionListIndex()
        index.add_page(_scan([0, 1, 2]))
        index.label_row(0, 1, "01")
        with self.assertRaises(ExpeditionListError):
            index.label_row(0, 1, "02")

    def test_rebinding_a_number_to_another_row_is_refused(self) -> None:
        index = ExpeditionListIndex()
        index.add_page(_scan([0, 1, 2]))
        index.label_row(0, 1, "01")
        with self.assertRaises(ExpeditionListError):
            index.label_row(0, 2, "01")

    def test_duplicate_view_is_refused(self) -> None:
        index = ExpeditionListIndex()
        index.add_page(_scan([0, 1, 2]))
        with self.assertRaises(ExpeditionListError):
            index.add_page(_scan([0, 1, 2]))

    def test_label_targets_are_validated(self) -> None:
        index = ExpeditionListIndex()
        index.add_page(_scan([0, 1, 2]))
        with self.assertRaises(ExpeditionListError):
            index.label_row(1, 1, "01")
        with self.assertRaises(ExpeditionListError):
            index.label_row(0, 9, "01")
        with self.assertRaises(ExpeditionListError):
            index.label_row(0, 1, "")

    def test_locate_returns_a_point_on_the_fresh_page(self) -> None:
        index = ExpeditionListIndex()
        index.add_page(_scan([0, 1, 2, 3]))
        index.label_row(0, 3, "03")
        fresh = _scan([1, 2, 3, 4])
        relocation = index.locate("03", fresh)
        self.assertEqual(relocation.state, "located")
        self.assertEqual(relocation.row_index, 2)
        self.assertEqual(round(relocation.point[1]), ROW_TOP + ROW_PITCH + ROW_HEIGHT // 2)

    def test_locate_reports_a_row_that_left_the_page(self) -> None:
        index = ExpeditionListIndex()
        index.add_page(_scan([0, 1, 2, 3]))
        index.label_row(0, 1, "01")
        relocation = index.locate("01", _scan([4, 5, 6, 7]))
        self.assertEqual(relocation.state, "not_visible")
        self.assertIsNone(relocation.point)

    def test_locate_reports_an_unbound_number(self) -> None:
        index = ExpeditionListIndex()
        index.add_page(_scan([0, 1]))
        self.assertEqual(index.locate("99", _scan([0, 1])).state, "unbound")

    def test_tab_order_follows_list_position(self) -> None:
        index = ExpeditionListIndex()
        index.add_page(_scan([0, 1, 2, 3]))
        index.add_page(_scan([2, 3, 4, 5]))
        index.label_row(0, 1, "01")
        index.label_row(0, 2, "02")
        index.label_row(1, 3, "A1")
        self.assertEqual(index.tab_order(0), ("01", "02", "A1"))


class DisplayOrderTests(unittest.TestCase):
    def test_display_numbers_sort_like_the_game_list(self) -> None:
        numbers = ["07", "A1", "A2", "B1", "01", "46"]
        self.assertEqual(
            sorted(numbers, key=disp_no_sort_key), ["01", "07", "46", "A1", "A2", "B1"]
        )

    def test_unrecognised_number_is_rejected(self) -> None:
        for text in ("", "A", "ABC", "1A"):
            with self.assertRaises(ValueError):
                disp_no_sort_key(text)

    def test_order_check_reports_both_verdicts(self) -> None:
        self.assertEqual(check_display_order(("01", "02", "A1")), "consistent")
        self.assertEqual(check_display_order(("01", "A1", "02")), "violated")
        self.assertEqual(check_display_order(("01",)), "unverified")


class ToolTests(unittest.TestCase):
    def _write(self, directory: Path, name: str, image: RgbImage) -> Path:
        path = directory / name
        encode_png(path, image)
        return path

    def test_full_page_with_unlabelled_rows_exits_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            shot = self._write(directory, "page0.png", _page(list(range(8))))
            buffer = io.StringIO()
            with redirect_stdout(buffer), redirect_stderr(io.StringIO()):
                code = main(["--image", str(shot)])
            self.assertEqual(code, 2)
            self.assertIn("full_page_without_scroll_affordance", buffer.getvalue())
            self.assertIn("need a one-time label", buffer.getvalue())

    def test_labelled_short_page_keeps_boundary_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            shot = self._write(directory, "page0.png", _page([0, 1, 2]))
            out = directory / "scan.json"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main(["--image", str(shot), "--label-all", "01,02,03", "--json-out", str(out)])
            self.assertEqual(code, 2)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["boundary"]["state"], "unknown")
            self.assertEqual(payload["boundary"]["reason"], "short_page_without_verified_end_marker")
            self.assertEqual([row["expedition_id"] for row in payload["rows"]], ["01", "02", "03"])
            self.assertEqual(payload["rows"][0]["center"]["x"], 350)
            self.assertTrue(payload["rows"][0]["visible"])

    def test_index_persists_bindings_between_runs(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            first = self._write(directory, "page0.png", _page([0, 1, 2]))
            index_path = directory / "index.json"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--image", str(first), "--label-all", "01,02,03",
                                       "--index", str(index_path)]), 2)
            self.assertTrue(index_path.exists())
            second = self._write(directory, "page1.png", _page([1, 2, 9]))
            out = directory / "scan.json"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main(["--image", str(second), "--index", str(index_path),
                             "--json-out", str(out)])
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(code, 2)
            self.assertEqual([row["expedition_id"] for row in payload["rows"]], ["02", "03"])
            self.assertEqual(len(payload["unbound_rows"]), 1)
            self.assertEqual(payload["unbound_rows"][0]["row"], 3)

    def test_label_count_mismatch_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            shot = self._write(directory, "page0.png", _page([0, 1, 2]))
            error = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(error):
                code = main(["--image", str(shot), "--label-all", "01,02"])
            self.assertEqual(code, 2)
            self.assertIn("LABEL_COUNT_MISMATCH", error.getvalue())

    def test_unknown_tab_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            shot = self._write(directory, "page0.png", _page([0, 1, 2], active_tab=None))
            error = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(error):
                code = main(["--image", str(shot)])
            self.assertEqual(code, 2)
            self.assertIn("TAB_UNKNOWN", error.getvalue())

    def test_missing_rows_fail_the_scan(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            shot = self._write(directory, "blank.png", _blank())
            error = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(error):
                code = main(["--image", str(shot)])
            self.assertEqual(code, 1)
            self.assertIn("SCAN_FAILED", error.getvalue())

    def test_two_pages_are_aligned_and_merged(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            first = self._write(directory, "page0.png", _page(list(range(8))))
            second = self._write(directory, "page1.png", _page([4, 5, 6, 7, 8, 9]))
            out = directory / "scan.json"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main([
                    "--image", str(first), "--image", str(second),
                    "--label", "0:1=01", "--label", "1:1=05",
                    "--json-out", str(out),
                ])
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(code, 2)
            self.assertEqual(payload["pages"], 2)
            self.assertEqual([row["expedition_id"] for row in payload["rows"]], ["01", "05"])
            self.assertFalse(payload["rows"][0]["visible"])
            self.assertTrue(payload["rows"][1]["visible"])

    def test_propose_scroll_is_reported_but_not_executed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            shot = self._write(directory, "page0.png", _page(list(range(8))))
            buffer = io.StringIO()
            with redirect_stdout(buffer), redirect_stderr(io.StringIO()):
                main(["--image", str(shot), "--propose-scroll", "down",
                      "--confirm-scroll-point", "1030,612"])
            text = buffer.getvalue()
            self.assertIn("click_confirmed_point", text)
            self.assertIn("(1030, 612)", text)

    def test_probe_band_reports_candidates_without_clicking(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            image = _fill(_page([0, 1]), 762, 606, 772, 616, BLACK)
            shot = self._write(directory, "page0.png", image)
            out = directory / "scan.json"
            buffer = io.StringIO()
            with redirect_stdout(buffer), redirect_stderr(io.StringIO()):
                main(["--image", str(shot), "--label-all", "01,02",
                      "--probe-band", "740,590,790,620", "--json-out", str(out)])
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["probe_band"]["candidates"]), 1)
            self.assertEqual(payload["probe_band"]["candidates"][0]["center"], [766, 610])
            self.assertIn("never clicked", buffer.getvalue())

    def test_invalid_probe_band_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            shot = self._write(directory, "page0.png", _page([0, 1]))
            error = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(error):
                code = main(["--image", str(shot), "--probe-band", "740,590,1300,620"])
            self.assertEqual(code, 2)
            self.assertIn("PROBE_BAND_INVALID", error.getvalue())

    def test_annotation_and_capture_artifacts_are_written(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            shot = self._write(directory, "page0.png", _page([0, 1]))
            annotate = directory / "rows.png"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                main(["--image", str(shot), "--label-all", "01,02", "--annotate", str(annotate)])
            self.assertTrue(annotate.exists())
            self.assertGreater(decode_png(annotate).height, 0)


if __name__ == "__main__":
    unittest.main()
