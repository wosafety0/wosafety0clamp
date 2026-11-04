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
from dataclasses import replace
from pathlib import Path

from kancollebot.calibration import (
    DEFAULT_CATALOG,
    DEFAULT_SCROLL_DOWN_BOX,
    DEFAULT_SCROLL_UP_BOX,
    BoundaryState,
    ButtonCandidate,
    ExpeditionListError,
    ExpeditionListIndex,
    PageScan,
    RowSignature,
    ScrollAffordance,
    VisibleRow,
    align_pages,
    check_display_order,
    classify_boundary,
    count_gold_pixels,
    decode_png,
    detect_area_tabs,
    detect_scroll_affordance,
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
GOLD = (245, 205, 90)

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


def _with_controls(
    slots: list[int], *, up: bool = True, down: bool = True
) -> RgbImage:
    """A page whose measured scroll-control boxes are painted gold."""
    image = _page(slots)
    if up:
        image = _fill(image, *DEFAULT_SCROLL_UP_BOX, GOLD)
    if down:
        image = _fill(image, *DEFAULT_SCROLL_DOWN_BOX, GOLD)
    return image


def _scan_controls(slots: list[int], *, up: bool = True, down: bool = True) -> PageScan:
    return scan_page(_with_controls(slots, up=up, down=down))


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
        self.assertEqual(boundary.reason, "full_page_without_measured_control")

    def test_short_page_confirms_the_end(self) -> None:
        boundary = classify_boundary(_scan([0, 1, 2]))
        self.assertIs(boundary.state, BoundaryState.END_CONFIRMED)
        self.assertEqual(boundary.reason, "rows_below_capacity")

    def test_dark_glyph_no_longer_means_more_below_once_measured(self) -> None:
        # The 2026-11-04 last page: NEXT was gone, yet the generic probe still
        # found a dark cluster in the strip. Measurement has to outrank it.
        image = _page(list(range(8)))
        image = _fill(image, 760, 604, 772, 614, BLACK)
        scan = scan_page(image)
        self.assertTrue(scan.scroll_candidates)
        self.assertTrue(scan.scroll_probed)
        boundary = classify_boundary(scan)
        self.assertIs(boundary.state, BoundaryState.UNKNOWN)
        self.assertEqual(boundary.reason, "full_page_without_measured_control")

    def test_legacy_glyph_probe_still_applies_when_the_boxes_were_not_read(self) -> None:
        legacy = replace(
            _scan(list(range(8))),
            scroll_probed=False,
            scroll_candidates=(ButtonCandidate(760, 604, 772, 614, 100),),
        )
        boundary = classify_boundary(legacy)
        self.assertIs(boundary.state, BoundaryState.MORE_BELOW)
        self.assertEqual(boundary.reason, "scroll_affordance_present")

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
        self.assertEqual(proposal.action, "blocked_no_measured_control")
        self.assertIsNone(proposal.point)

    def test_scroll_proposal_needs_confirmation_for_a_candidate(self) -> None:
        legacy = replace(
            _scan(list(range(8))),
            scroll_probed=False,
            scroll_candidates=(ButtonCandidate(760, 604, 772, 614, 100),),
        )
        proposal = plan_scroll_step(legacy)
        self.assertEqual(proposal.action, "confirm_candidate")
        self.assertIsNotNone(proposal.point)

    def test_last_page_proposes_no_point_at_all(self) -> None:
        # The live last page: NEXT is gone, and the only dark cluster left in the
        # strip is a row icon at the panel edge. Offering it would be a hazard.
        scan = _scan_controls(list(range(8)), down=False)
        proposal = plan_scroll_step(scan)
        self.assertEqual(proposal.action, "blocked_no_measured_control")
        self.assertIsNone(proposal.point)

    def test_confirmed_point_is_used_verbatim(self) -> None:
        proposal = plan_scroll_step(_scan(list(range(8))), confirmed_point=(1030, 612))
        self.assertEqual(proposal.action, "click_confirmed_point")
        self.assertEqual(proposal.point, (1030, 612))

    def test_scroll_down_at_a_confirmed_end_is_blocked(self) -> None:
        proposal = plan_scroll_step(_scan([0, 1, 2]), confirmed_point=(1030, 612))
        self.assertEqual(proposal.action, "blocked_at_end")

    def test_scroll_direction_is_validated(self) -> None:
        with self.assertRaises(ValueError):
            plan_scroll_step(_scan([0]), direction="sideways")


class ScrollControlTests(unittest.TestCase):
    """The measured scroll chevrons: detection, boundary, and the proposal."""

    def test_gold_counter_separates_gold_from_other_surfaces(self) -> None:
        box = (600, 300, 640, 340)
        blank = _blank()
        self.assertEqual(count_gold_pixels(blank, box), 0)
        for color in (BLACK, PLATE, ORANGE):
            image = _fill(blank, 600, 300, 640, 340, color)
            self.assertEqual(
                count_gold_pixels(image, box), 0,
                f"{color} must not read as the gold control colour",
            )
        gold = _fill(blank, 600, 300, 640, 340, GOLD)
        self.assertEqual(count_gold_pixels(gold, box), 40 * 40)

    def test_gold_counter_rejects_a_box_outside_the_image(self) -> None:
        with self.assertRaises(ValueError):
            count_gold_pixels(_blank(), (1100, 700, 1300, 760))

    def test_control_needs_more_gold_than_the_floor(self) -> None:
        blank = _blank()
        speck = _fill(blank, 431, 183, 441, 193, GOLD)  # 100 px, under 120
        self.assertIsNone(detect_scroll_affordance(speck, direction="up"))
        painted = _fill(blank, *DEFAULT_SCROLL_UP_BOX, GOLD)
        self.assertIsNotNone(detect_scroll_affordance(painted, direction="up"))

    def test_control_is_not_claimed_for_a_capture_that_cannot_hold_it(self) -> None:
        small = RgbImage(200, 150, 3, bytes(WHITE) * (200 * 150))
        self.assertIsNone(detect_scroll_affordance(small, direction="up"))
        self.assertIsNone(detect_scroll_affordance(small, direction="down"))

    def test_scan_page_records_both_measured_controls(self) -> None:
        scan = _scan_controls(list(range(8)))
        self.assertIsNotNone(scan.scroll_up)
        self.assertIsNotNone(scan.scroll_down)
        self.assertEqual(scan.scroll_up.center, (449, 201))
        self.assertEqual(scan.scroll_down.center, (450, 626))
        self.assertEqual(scan.scroll_up.box, DEFAULT_SCROLL_UP_BOX)
        self.assertEqual(scan.scroll_down.box, DEFAULT_SCROLL_DOWN_BOX)

    def test_page_without_the_control_reports_none(self) -> None:
        scan = _scan(list(range(8)))
        self.assertIsNone(scan.scroll_up)
        self.assertIsNone(scan.scroll_down)

    def test_paged_full_page_is_neither_an_end_nor_more_below(self) -> None:
        boundary = classify_boundary(_scan_controls(list(range(8))))
        self.assertIs(boundary.state, BoundaryState.PAGED)
        self.assertEqual(boundary.reason, "measured_scroll_control_present")

    def test_paged_page_proposes_the_measured_down_point(self) -> None:
        scan = _scan_controls(list(range(8)))
        proposal = plan_scroll_step(scan)
        self.assertEqual(proposal.action, "click_measured_control")
        self.assertEqual(proposal.point, (450, 626))
        # The audit trail must quote the same gold count the scan measured.
        self.assertIn(
            f"{scan.scroll_down.gold_pixels} gold pixels", proposal.detail
        )

    def test_scroll_up_uses_the_measured_up_control(self) -> None:
        proposal = plan_scroll_step(_scan_controls(list(range(8))), direction="up")
        self.assertEqual(proposal.action, "click_measured_control")
        self.assertEqual(proposal.point, (449, 201))

    def test_scroll_up_without_a_control_is_blocked_at_the_top(self) -> None:
        proposal = plan_scroll_step(
            _scan_controls(list(range(8)), up=False), direction="up"
        )
        self.assertEqual(proposal.action, "blocked_at_top")
        self.assertIsNone(proposal.point)

    def test_short_page_still_wins_over_a_painted_control(self) -> None:
        # Three rows cannot hide anything below them, whatever the chrome says.
        proposal = plan_scroll_step(_scan_controls([0, 1, 2]))
        self.assertEqual(proposal.action, "blocked_at_end")

    def test_affordance_validates_its_own_fields(self) -> None:
        with self.assertRaises(ValueError):
            ScrollAffordance("sideways", (1, 2, 3, 4), 200)
        with self.assertRaises(ValueError):
            ScrollAffordance("up", (10, 4, 3, 99), 200)
        with self.assertRaises(ValueError):
            ScrollAffordance("up", (1, 2, 3, 4), -1)


class AlignmentTests(unittest.TestCase):
    def test_clipped_edge_rows_do_not_break_alignment(self) -> None:
        # The 2026-11-04 live scroll: content moved one row, the viewport clipped
        # the band at both ends, so those two rows fingerprint differently while
        # every interior row still matches exactly.
        before = _scan([0, 1, 2, 3, 4, 5, 6, 7])
        after = _scan([11, 2, 3, 4, 5, 6, 12, 13])
        shift, support = align_pages(before, after)
        self.assertEqual(shift, 1)
        self.assertEqual(support, 5, "only the comparable interior rows count")

    def test_interior_mismatch_still_refuses_the_shift(self) -> None:
        before = _scan([0, 1, 2, 3, 4, 5, 6, 7])
        after = _scan([11, 2, 3, 99, 5, 6, 12, 13])
        with self.assertRaises(ExpeditionListError):
            align_pages(before, after)

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


class ResolveTests(unittest.TestCase):
    """A position either has a number or it does not - no pixel involved."""

    def test_a_bound_position_resolves(self) -> None:
        index = ExpeditionListIndex()
        index.bind(0, 3, "04")
        self.assertEqual(index.resolve(0, 3), "04")

    def test_an_unbound_position_does_not_resolve(self) -> None:
        index = ExpeditionListIndex()
        index.bind(0, 3, "04")
        self.assertIsNone(index.resolve(0, 4))

    def test_bindings_do_not_leak_across_tabs(self) -> None:
        index = ExpeditionListIndex()
        index.bind(0, 3, "04")
        self.assertIsNone(index.resolve(1, 3))

    def test_two_numbers_cannot_share_one_position(self) -> None:
        index = ExpeditionListIndex()
        index.bind(0, 3, "04")
        with self.assertRaisesRegex(ExpeditionListError, "position 3 is already bound to 04"):
            index.bind(0, 3, "05")

    def test_one_number_cannot_occupy_two_positions(self) -> None:
        index = ExpeditionListIndex()
        index.bind(0, 3, "04")
        with self.assertRaisesRegex(ExpeditionListError, "already bound to position 3"):
            index.bind(0, 4, "04")

    def test_a_negative_position_is_refused(self) -> None:
        with self.assertRaisesRegex(ExpeditionListError, "cannot be negative"):
            ExpeditionListIndex().bind(0, -1, "04")

    def test_two_positions_resolve_independently_when_they_look_alike(self) -> None:
        # No signature is passed at all: this is the case that used to be
        # unresolvable, and it now simply does not arise.
        index = ExpeditionListIndex()
        index.bind(0, 6, "29")
        index.bind(0, 7, "30")
        self.assertEqual((index.resolve(0, 6), index.resolve(0, 7)), ("29", "30"))


class PositionLabelTests(unittest.TestCase):
    def _walked_index(self) -> ExpeditionListIndex:
        index = ExpeditionListIndex()
        index.add_page(_scan([0, 1, 2, 3]))
        index.add_page(_scan([2, 3, 4, 5]))
        index.label_row(0, 1, "01")
        index.label_row(0, 2, "02")
        return index

    def test_unbound_positions_are_reported_in_list_order(self) -> None:
        self.assertEqual(
            self._walked_index().unbound_positions(),
            ((0, 2), (0, 3), (0, 4), (0, 5)),
        )

    def test_labelling_positions_completes_the_list(self) -> None:
        index = self._walked_index()
        index.label_positions(("03", "04", "A1", "A2"))
        self.assertEqual(index.unbound_positions(), ())
        self.assertEqual(
            [entry.expedition_id for entry in index.entries()],
            ["01", "02", "03", "04", "A1", "A2"],
        )

    def test_the_count_must_match_exactly(self) -> None:
        with self.assertRaisesRegex(ExpeditionListError, "for 4 unbound positions"):
            self._walked_index().label_positions(("03", "04"))

    def test_a_position_already_bound_is_not_relabelled(self) -> None:
        index = self._walked_index()
        index.label_positions(("03", "04", "A1", "A2"))
        with self.assertRaisesRegex(ExpeditionListError, "for 0 unbound positions"):
            index.label_positions(("05",))


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

    def test_entries_take_names_from_the_catalogue_without_changing_identity(self) -> None:
        index = ExpeditionListIndex()
        index.add_page(_scan([0, 1, 2]))
        index.label_row(0, 1, "01")
        index.label_row(0, 2, "A5")
        index.label_row(0, 3, "ZZ")
        by_id = {entry.expedition_id: entry for entry in index.entries(DEFAULT_CATALOG.name_of)}
        self.assertEqual(by_id["01"].name, "練習航海")
        self.assertEqual(by_id["A5"].name, "小笠原沖哨戒線")
        # A number the catalogue does not know stays unnamed, not invalid.
        self.assertIsNone(by_id["ZZ"].name)

    def test_entries_without_a_resolver_leave_names_unset(self) -> None:
        index = ExpeditionListIndex()
        index.add_page(_scan([0, 1]))
        index.label_row(0, 1, "01")
        self.assertIsNone(index.entries()[0].name)

    def test_earlier_pages_are_resolved_when_a_row_is_labelled(self) -> None:
        index = ExpeditionListIndex()
        index.add_page(_scan([0, 1, 2, 3, 4, 5]))
        index.add_page(_scan([2, 3, 4, 5, 6, 7]))
        index.label_row(1, 1, "03")
        self.assertEqual(index.pages[0].ids[2], "03")
        self.assertEqual(index.unbound_rows[0], (0, 1))

    def test_rebinding_a_position_to_another_number_is_refused(self) -> None:
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
        relocation = index.locate("03", fresh, offset=1)
        self.assertEqual(relocation.state, "located")
        self.assertEqual(relocation.row_index, 2)
        self.assertEqual(round(relocation.point[1]), ROW_TOP + ROW_PITCH + ROW_HEIGHT // 2)

    def test_locate_reports_a_row_that_left_the_page(self) -> None:
        index = ExpeditionListIndex()
        index.add_page(_scan([0, 1, 2, 3]))
        index.label_row(0, 1, "01")
        relocation = index.locate("01", _scan([4, 5, 6, 7]), offset=4)
        self.assertEqual(relocation.state, "not_visible")
        self.assertIsNone(relocation.point)

    def test_locate_reports_an_unbound_number(self) -> None:
        index = ExpeditionListIndex()
        index.add_page(_scan([0, 1]))
        self.assertEqual(index.locate("99", _scan([0, 1]), offset=0).state, "unbound")

    def test_tab_order_follows_list_position(self) -> None:
        index = ExpeditionListIndex()
        index.add_page(_scan([0, 1, 2, 3]))
        index.add_page(_scan([2, 3, 4, 5]))
        index.label_row(0, 1, "01")
        index.label_row(0, 2, "02")
        index.label_row(1, 3, "A1")
        self.assertEqual(index.tab_order(0), ("01", "02", "A1"))


class LocateByPositionTests(unittest.TestCase):
    """Locating a row is arithmetic on its list position, not a pixel search.

    Live measurement forced the model change: in 西方海域, 29 潜水艦派遣演習 and
    30 潜水艦派遣作戦 fingerprint *identically* (same bins, same dark total), so a
    signature lookup has two answers and no tie-break. Positions differ by one,
    so they do - which is why ``bindings`` is keyed by position and ``locate``
    needs the page's offset instead of a tolerance.
    """

    @staticmethod
    def _signature(offset_bin: int = 0, offset_value: int = 0, *, total: int = 100) -> RowSignature:
        bins = [5] * 16
        if offset_bin:
            bins[offset_bin] = bins[offset_bin] + offset_value
        return RowSignature(tuple(bins), total)

    @staticmethod
    def _page(signatures: list[RowSignature], *, active_tab: int = 0) -> PageScan:
        rows = tuple(
            VisibleRow(
                index=position,
                top=ROW_TOP + ROW_PITCH * (position - 1),
                bottom=ROW_TOP + ROW_PITCH * (position - 1) + ROW_HEIGHT,
                center=ROW_TOP + ROW_PITCH * (position - 1) + ROW_HEIGHT / 2,
                signature=signature,
            )
            for position, signature in enumerate(signatures, start=1)
        )
        return PageScan(
            rows=rows,
            tabs=detect_area_tabs(_page([0], active_tab=active_tab)),
            scroll_candidates=(),
            image_size=(WIDTH, HEIGHT),
        )

    def test_position_is_located_whatever_the_row_looks_like(self) -> None:
        index = ExpeditionListIndex()
        index.bind(0, 2, "29")
        relocation = index.locate("29", self._page([self._signature(8, 30)] * 4), offset=0)
        self.assertEqual(relocation.state, "located")
        self.assertEqual(relocation.row_index, 3)
        self.assertEqual(relocation.detail, "list position 2 on a page starting at 0")

    def test_identical_renderings_are_located_separately(self) -> None:
        # The measured 西方 case, now resolvable: same pixels, different slots.
        twin = self._signature()
        index = ExpeditionListIndex()
        index.bind(0, 6, "29")
        index.bind(0, 7, "30")
        page = self._page([twin] * 8)
        first = index.locate("29", page, offset=0)
        second = index.locate("30", page, offset=0)
        self.assertEqual(first.state, "located")
        self.assertEqual(second.state, "located")
        self.assertEqual(first.row_index, 7)
        self.assertEqual(second.row_index, 8)
        self.assertNotEqual(
            first.point[1], second.point[1], "positions 6 and 7 must not collapse"
        )

    def test_the_offset_comes_from_the_page_not_from_the_index(self) -> None:
        # A position below the visible window is only locatable once the caller
        # supplies the offset of the page it actually captured.
        index = ExpeditionListIndex()
        index.bind(0, 9, "30")
        page = self._page([self._signature()] * 8)
        self.assertEqual(index.locate("30", page, offset=0).state, "not_visible")
        located = index.locate("30", page, offset=2)
        self.assertEqual(located.state, "located")
        self.assertEqual(located.row_index, 8)
        self.assertEqual(located.detail, "list position 9 on a page starting at 2")

    def test_a_row_above_the_page_is_not_visible(self) -> None:
        index = ExpeditionListIndex()
        index.bind(0, 1, "25")
        relocation = index.locate("25", self._page([self._signature()] * 8), offset=4)
        self.assertEqual(relocation.state, "not_visible")
        self.assertIsNone(relocation.point)

    def test_a_bound_number_on_another_tab_is_not_found(self) -> None:
        index = ExpeditionListIndex()
        index.bind(0, 0, "25")
        relocation = index.locate("25", self._page([self._signature()], active_tab=1), offset=0)
        self.assertEqual(relocation.state, "unbound")


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
            self.assertIn("full_page_without_measured_control", buffer.getvalue())
            self.assertIn("need a one-time label", buffer.getvalue())

    def test_labelled_short_page_exits_clean(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            shot = self._write(directory, "page0.png", _page([0, 1, 2]))
            out = directory / "scan.json"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main(["--image", str(shot), "--label-all", "01,02,03", "--json-out", str(out)])
            self.assertEqual(code, 0)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["boundary"]["state"], "end_confirmed")
            self.assertEqual([row["expedition_id"] for row in payload["rows"]], ["01", "02", "03"])
            self.assertEqual(payload["rows"][0]["center"]["x"], 350)
            self.assertTrue(payload["rows"][0]["visible"])

    def test_index_persists_bindings_for_a_top_page(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            top = self._write(directory, "page0.png", _page([0, 1, 2]))
            index_path = directory / "index.json"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--image", str(top), "--label-all", "01,02,03",
                                       "--index", str(index_path)]), 0)
            self.assertTrue(index_path.exists())
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], 2)
            self.assertEqual(payload["bindings"], {"0|0": "01", "0|1": "02", "0|2": "03"})

            out = directory / "scan.json"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main(["--image", str(top), "--index", str(index_path),
                             "--json-out", str(out)])
            again = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(code, 0)
            self.assertEqual(
                [row["expedition_id"] for row in again["rows"]], ["01", "02", "03"]
            )
            self.assertEqual(again["unbound_rows"], [])

    def test_index_is_refused_when_the_page_is_not_at_the_top(self) -> None:
        # Bindings are keyed by position, so a capture taken part-way down would
        # name every row one slot off. That is refused, not guessed.
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            top = self._write(directory, "page0.png", _page([0, 1, 2]))
            index_path = directory / "index.json"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--image", str(top), "--label-all", "01,02,03",
                                       "--index", str(index_path)]), 0)
            scrolled = self._write(
                directory, "page1.png", _with_controls([1, 2, 9], up=True, down=True)
            )
            error = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(error):
                code = main(["--image", str(scrolled), "--index", str(index_path)])
            self.assertEqual(code, 2)
            self.assertIn("OFFSET_UNKNOWN", error.getvalue())

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
