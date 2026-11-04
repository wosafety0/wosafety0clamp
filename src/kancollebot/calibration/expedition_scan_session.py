"""Offline orchestration for multi-page expedition-list scans.

This module consumes already captured PageScan values. It never captures a
screen, scrolls, clicks, or invokes a game/UI adapter. A scan is complete only
when a caller supplies independently verified top- and bottom-marker
evidence bound to the first and latest pages, and every visible row has a stable
expedition-number binding.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .expedition_list import (
    BoundaryState,
    ExpeditionListError,
    ExpeditionListIndex,
    ListBoundary,
    ListEntry,
    PageScan,
    classify_boundary,
    same_view,
)


class ScanSessionState(str, Enum):
    """Completion state of one area's multi-page scan."""

    AWAITING_PAGE = "awaiting_page"
    INCOMPLETE = "incomplete"
    NEEDS_LABELS = "needs_labels"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class VerifiedStartMarker:
    """Independent top-of-list evidence tied to the exact first captured page."""

    tab: int
    page_signature: tuple[str, ...]
    marker_id: str
    evidence_source: str

    def __post_init__(self) -> None:
        if self.tab < 0:
            raise ValueError("tab must be non-negative")
        if not self.page_signature:
            raise ValueError("start-marker evidence must be bound to a non-empty page")
        if not self.marker_id.strip():
            raise ValueError("marker_id must not be empty")
        if not self.evidence_source.strip():
            raise ValueError("evidence_source must not be empty")

    @classmethod
    def for_page(
        cls, scan: PageScan, *, marker_id: str, evidence_source: str
    ) -> "VerifiedStartMarker":
        """Bind externally verified top-of-list evidence to the exact page.

        This helper does not detect the marker; callers must establish it
        independently. A capture taken after scrolling cannot be marked as top.
        """
        if scan.tab is None:
            raise ExpeditionListError("cannot bind start evidence to an unknown area tab")
        if not scan.rows:
            raise ExpeditionListError("cannot bind start evidence to a page with no rows")
        return cls(
            tab=scan.tab,
            page_signature=tuple(row.signature.key for row in scan.rows),
            marker_id=marker_id,
            evidence_source=evidence_source,
        )

    def matches(self, scan: PageScan) -> bool:
        return (
            scan.tab == self.tab
            and tuple(row.signature.key for row in scan.rows) == self.page_signature
        )


@dataclass(frozen=True, slots=True)
class VerifiedEndMarker:
    """Independent end-marker evidence tied to one exact captured page.

    Construct this only after a separately calibrated visual detector or
    operator has verified a genuine list-end marker. Row count, a short page,
    an unchanged view, or a suspected scroll affordance are not end evidence.
    """

    tab: int
    page_signature: tuple[str, ...]
    marker_id: str
    evidence_source: str

    def __post_init__(self) -> None:
        if self.tab < 0:
            raise ValueError("tab must be non-negative")
        if not self.page_signature:
            raise ValueError("end-marker evidence must be bound to a non-empty page")
        if not self.marker_id.strip():
            raise ValueError("marker_id must not be empty")
        if not self.evidence_source.strip():
            raise ValueError("evidence_source must not be empty")

    @classmethod
    def for_page(
        cls, scan: PageScan, *, marker_id: str, evidence_source: str
    ) -> "VerifiedEndMarker":
        """Bind externally verified evidence to the exact current page.

        This helper does not detect or validate the visual marker; callers are
        responsible for establishing that evidence independently.
        """
        if scan.tab is None:
            raise ExpeditionListError("cannot bind end evidence to an unknown area tab")
        if not scan.rows:
            raise ExpeditionListError("cannot bind end evidence to a page with no rows")
        return cls(
            tab=scan.tab,
            page_signature=tuple(row.signature.key for row in scan.rows),
            marker_id=marker_id,
            evidence_source=evidence_source,
        )

    def matches(self, scan: PageScan) -> bool:
        return (
            scan.tab == self.tab
            and tuple(row.signature.key for row in scan.rows) == self.page_signature
        )


@dataclass(frozen=True, slots=True)
class ScanSessionSnapshot:
    state: ScanSessionState
    tab: int | None
    page_count: int
    entry_count: int
    unbound_rows: tuple[tuple[int, int], ...]
    boundary: ListBoundary
    start_verified: bool
    end_verified: bool
    reason: str


@dataclass
class ExpeditionScanSession:
    """Accumulate consecutive captures for exactly one area tab.

    Page alignment and expedition-number de-duplication are delegated to
    ExpeditionListIndex. The session deliberately does not decide how to obtain
    the next screenshot or how to scroll the UI.
    """

    expected_tab: int | None = None
    index: ExpeditionListIndex = field(default_factory=ExpeditionListIndex)
    _start_marker: VerifiedStartMarker | None = field(default=None, init=False, repr=False)
    _end_marker: VerifiedEndMarker | None = field(default=None, init=False, repr=False)
    _last_reason: str = field(default="awaiting_first_page", init=False, repr=False)

    def __post_init__(self) -> None:
        if self.expected_tab is not None and self.expected_tab < 0:
            raise ValueError("expected_tab must be non-negative")

    def observe_page(
        self,
        scan: PageScan,
        *,
        start_marker: VerifiedStartMarker | None = None,
        end_marker: VerifiedEndMarker | None = None,
    ) -> ScanSessionSnapshot:
        """Append a fresh page, or report no progress without duplicating it."""
        if scan.tab is None:
            raise ExpeditionListError("the active area tab is unknown")
        if self.expected_tab is None:
            self.expected_tab = scan.tab
        if scan.tab != self.expected_tab:
            raise ExpeditionListError(
                f"page belongs to tab {scan.tab}, expected tab {self.expected_tab}"
            )
        if start_marker is not None:
            first_page = self.index.pages[0].scan if self.index.pages else scan
            if not start_marker.matches(first_page):
                raise ExpeditionListError("start-marker evidence does not match the first page")
            if self._start_marker is not None and self._start_marker != start_marker:
                raise ExpeditionListError("conflicting start-marker evidence")
        if end_marker is not None and not end_marker.matches(scan):
            raise ExpeditionListError("end-marker evidence does not match the observed page")
        if self._end_marker is not None and self.snapshot().state is ScanSessionState.COMPLETE:
            raise ExpeditionListError("scan session is already complete")

        if self.index.pages and same_view(self.index.pages[-1].scan, scan):
            if start_marker is not None:
                self._start_marker = start_marker
            if end_marker is not None:
                self._end_marker = end_marker
            if start_marker is not None or end_marker is not None:
                self._last_reason = "edge_marker_evidence_bound_to_unchanged_page"
            else:
                self._last_reason = "unchanged_view_no_progress"
            return self.snapshot()

        # add_page validates cross-page overlap before mutating the index.
        self.index.add_page(scan)
        if start_marker is not None:
            self._start_marker = start_marker
        self._end_marker = end_marker
        self._last_reason = (
            "verified_end_marker_observed" if end_marker is not None else "more_pages_not_proven"
        )
        return self.snapshot()

    def label_row(self, page: int, row_index: int, expedition_id: str) -> ScanSessionSnapshot:
        """Add a one-time identity binding and refresh completion status."""
        self.index.label_row(page, row_index, expedition_id)
        self._last_reason = "row_identity_bound"
        return self.snapshot()

    @property
    def entries(self) -> tuple[ListEntry, ...]:
        return self.index.entries()

    def snapshot(self) -> ScanSessionSnapshot:
        if not self.index.pages:
            empty_boundary = ListBoundary(BoundaryState.UNKNOWN, "no_pages_observed")
            return ScanSessionSnapshot(
                ScanSessionState.AWAITING_PAGE, self.expected_tab, 0, 0, (), empty_boundary,
                False, False, self._last_reason,
            )

        latest = self.index.pages[-1].scan
        boundary = classify_boundary(latest)
        if self._end_marker is not None and self._end_marker.matches(latest):
            boundary = ListBoundary(
                BoundaryState.END_CONFIRMED,
                f"verified_end_marker:{self._end_marker.marker_id}",
            )

        start_verified = (
            self._start_marker is not None
            and self._start_marker.matches(self.index.pages[0].scan)
        )
        end_verified = self._end_marker is not None and self._end_marker.matches(latest)
        unbound = self.index.unbound_rows
        if boundary.state is BoundaryState.END_CONFIRMED and not start_verified:
            state = ScanSessionState.INCOMPLETE
            reason = "verified_end_but_start_not_verified"
        elif boundary.state is BoundaryState.END_CONFIRMED and unbound:
            state = ScanSessionState.NEEDS_LABELS
            reason = "verified_edges_but_rows_need_identity_labels"
        elif boundary.state is BoundaryState.END_CONFIRMED and start_verified and end_verified:
            state = ScanSessionState.COMPLETE
            reason = "verified_start_and_end_and_all_rows_identified"
        else:
            state = ScanSessionState.INCOMPLETE
            reason = self._last_reason

        return ScanSessionSnapshot(
            state=state,
            tab=self.expected_tab,
            page_count=len(self.index.pages),
            entry_count=len(self.index.entries()),
            unbound_rows=unbound,
            boundary=boundary,
            start_verified=start_verified,
            end_verified=end_verified,
            reason=reason,
        )
