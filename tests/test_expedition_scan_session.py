"""Offline tests for the multi-page expedition scan session."""
from __future__ import annotations

import unittest

from kancollebot.calibration import (
    AreaTabs,
    BoundaryState,
    ExpeditionListError,
    ExpeditionScanSession,
    PageScan,
    RowSignature,
    ScanSessionState,
    VerifiedEndMarker,
    VerifiedStartMarker,
    VisibleRow,
)

WIDTH = 1200
HEIGHT = 720


def _scan(markers: list[int], *, tab: int | None = 0) -> PageScan:
    rows = tuple(
        VisibleRow(
            index=index,
            top=250 + (index - 1) * 45,
            bottom=273 + (index - 1) * 45,
            center=261.5 + (index - 1) * 45,
            signature=RowSignature((marker,) * 16, marker * 16),
        )
        for index, marker in enumerate(markers, start=1)
    )
    scores = (100, 0, 0, 0, 0, 0, 0) if tab == 0 else (0, 100, 0, 0, 0, 0, 0)
    tabs = AreaTabs(7, tab, scores, tuple((176 + 60 * i, 679) for i in range(7)))
    return PageScan(rows, tabs, (), (WIDTH, HEIGHT), capacity=8)


class ScanSessionTests(unittest.TestCase):
    def test_new_session_waits_for_first_page(self) -> None:
        snapshot = ExpeditionScanSession(expected_tab=0).snapshot()
        self.assertIs(snapshot.state, ScanSessionState.AWAITING_PAGE)
        self.assertEqual(snapshot.page_count, 0)

    def test_unknown_tab_is_rejected(self) -> None:
        session = ExpeditionScanSession()
        with self.assertRaisesRegex(ExpeditionListError, "unknown"):
            session.observe_page(_scan([1, 2, 3], tab=None))
        self.assertEqual(session.snapshot().page_count, 0)

    def test_session_is_scoped_to_one_area_tab(self) -> None:
        session = ExpeditionScanSession(expected_tab=0)
        session.observe_page(_scan([1, 2, 3, 4]))
        with self.assertRaisesRegex(ExpeditionListError, "expected tab"):
            session.observe_page(_scan([1, 2, 3, 4], tab=1))
        self.assertEqual(session.snapshot().page_count, 1)

    def test_short_page_without_independent_marker_is_not_complete(self) -> None:
        session = ExpeditionScanSession(expected_tab=0)
        snapshot = session.observe_page(_scan([1, 2, 3]))
        self.assertIs(snapshot.state, ScanSessionState.INCOMPLETE)
        self.assertIs(snapshot.boundary.state, BoundaryState.UNKNOWN)

    def test_duplicate_view_reports_no_progress_without_appending(self) -> None:
        session = ExpeditionScanSession(expected_tab=0)
        first = _scan([1, 2, 3, 4])
        session.observe_page(first)
        snapshot = session.observe_page(first)
        self.assertEqual(snapshot.page_count, 1)
        self.assertIs(snapshot.state, ScanSessionState.INCOMPLETE)
        self.assertEqual(snapshot.reason, "unchanged_view_no_progress")

    def test_pages_align_and_deduplicate_across_scrolls(self) -> None:
        session = ExpeditionScanSession(expected_tab=0)
        session.observe_page(_scan([1, 2, 3, 4]))
        for row, mission in enumerate(("01", "02", "03", "04"), start=1):
            session.label_row(0, row, mission)
        second = _scan([3, 4, 5, 6])
        snapshot = session.observe_page(second)
        self.assertEqual(snapshot.page_count, 2)
        self.assertEqual(snapshot.entry_count, 4)
        self.assertEqual(session.index.pages[1].offset, 2)
        self.assertEqual(session.index.pages[1].ids, ("03", "04", None, None))

    def test_complete_requires_exact_end_marker_and_all_identity_labels(self) -> None:
        session = ExpeditionScanSession(expected_tab=0)
        first_page = _scan([1, 2, 3, 4])
        start_proof = VerifiedStartMarker.for_page(
            first_page, marker_id="scrollbar-top", evidence_source="independent-calibrated-detector"
        )
        session.observe_page(first_page, start_marker=start_proof)
        for row, mission in enumerate(("01", "02", "03", "04"), start=1):
            session.label_row(0, row, mission)
        final_page = _scan([3, 4, 5, 6])
        proof = VerifiedEndMarker.for_page(
            final_page, marker_id="scrollbar-bottom", evidence_source="independent-calibrated-detector"
        )
        snapshot = session.observe_page(final_page, end_marker=proof)
        self.assertIs(snapshot.state, ScanSessionState.NEEDS_LABELS)
        self.assertIs(snapshot.boundary.state, BoundaryState.END_CONFIRMED)
        self.assertEqual(snapshot.unbound_rows, ((1, 3), (1, 4)))
        session.label_row(1, 3, "A1")
        snapshot = session.label_row(1, 4, "A2")
        self.assertTrue(snapshot.start_verified)
        self.assertTrue(snapshot.end_verified)
        self.assertIs(snapshot.state, ScanSessionState.COMPLETE)
        self.assertEqual(snapshot.entry_count, 6)
        self.assertEqual(
            tuple(entry.expedition_id for entry in session.entries),
            ("01", "02", "03", "04", "A1", "A2"),
        )

    def test_end_marker_alone_cannot_complete_without_verified_top(self) -> None:
        first = _scan([1, 2, 3])
        session = ExpeditionScanSession(expected_tab=0)
        session.observe_page(first)
        for row, mission in enumerate(("01", "02", "03"), start=1):
            session.label_row(0, row, mission)
        end_proof = VerifiedEndMarker.for_page(
            first, marker_id="bottom", evidence_source="test-fixture"
        )
        snapshot = session.observe_page(first, end_marker=end_proof)
        self.assertIs(snapshot.state, ScanSessionState.INCOMPLETE)
        self.assertFalse(snapshot.start_verified)
        self.assertTrue(snapshot.end_verified)
        self.assertEqual(snapshot.reason, "verified_end_but_start_not_verified")

    def test_start_marker_is_bound_to_the_first_page_not_the_latest_page(self) -> None:
        first = _scan([1, 2, 3, 4])
        second = _scan([3, 4, 5, 6])
        start_proof = VerifiedStartMarker.for_page(
            first, marker_id="top", evidence_source="test-fixture"
        )
        session = ExpeditionScanSession(expected_tab=0)
        session.observe_page(first)
        snapshot = session.observe_page(second, start_marker=start_proof)
        self.assertTrue(snapshot.start_verified)
        self.assertFalse(snapshot.end_verified)
        self.assertIs(snapshot.state, ScanSessionState.INCOMPLETE)

    def test_stale_start_marker_is_rejected(self) -> None:
        first = _scan([1, 2, 3, 4])
        second = _scan([2, 3, 4, 5])
        proof = VerifiedStartMarker.for_page(
            second, marker_id="top", evidence_source="test-fixture"
        )
        session = ExpeditionScanSession(expected_tab=0)
        session.observe_page(first)
        with self.assertRaisesRegex(ExpeditionListError, "start-marker evidence"):
            session.observe_page(first, start_marker=proof)
        self.assertFalse(session.snapshot().start_verified)

    def test_start_marker_with_unknown_tab_is_rejected(self) -> None:
        with self.assertRaisesRegex(ExpeditionListError, "unknown area tab"):
            VerifiedStartMarker.for_page(
                _scan([1, 2], tab=None), marker_id="top", evidence_source="test"
            )

    def test_stale_end_marker_cannot_complete_a_different_page(self) -> None:
        first = _scan([1, 2, 3, 4])
        second = _scan([2, 3, 4, 5])
        proof = VerifiedEndMarker.for_page(
            first, marker_id="bottom", evidence_source="test-fixture"
        )
        session = ExpeditionScanSession(expected_tab=0)
        with self.assertRaisesRegex(ExpeditionListError, "does not match"):
            session.observe_page(second, end_marker=proof)
        self.assertEqual(session.snapshot().page_count, 0)

    def test_end_marker_with_unknown_tab_is_rejected(self) -> None:
        with self.assertRaisesRegex(ExpeditionListError, "unknown area tab"):
            VerifiedEndMarker.for_page(
                _scan([1, 2], tab=None), marker_id="bottom", evidence_source="test"
            )

    def test_end_marker_must_be_bound_to_non_empty_page(self) -> None:
        with self.assertRaisesRegex(ExpeditionListError, "no rows"):
            VerifiedEndMarker.for_page(
                _scan([]), marker_id="bottom", evidence_source="test"
            )

    def test_completed_session_rejects_later_page(self) -> None:
        session = ExpeditionScanSession(expected_tab=0)
        first = _scan([1, 2, 3, 4])
        for row, mission in enumerate(("01", "02", "03", "04"), start=1):
            if row == 1:
                session.observe_page(first)
            session.label_row(0, row, mission)
        start_proof = VerifiedStartMarker.for_page(
            first, marker_id="top", evidence_source="test-fixture"
        )
        end_proof = VerifiedEndMarker.for_page(
            first, marker_id="bottom", evidence_source="test-fixture"
        )
        session.observe_page(first, start_marker=start_proof, end_marker=end_proof)
        self.assertIs(session.snapshot().state, ScanSessionState.COMPLETE)
        with self.assertRaisesRegex(ExpeditionListError, "already complete"):
            session.observe_page(_scan([2, 3, 4, 5]))


if __name__ == "__main__":
    unittest.main()
