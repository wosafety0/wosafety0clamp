"""Follow the expedition list to a known row and report where to click it.

The list is only eight rows tall, so most expeditions are not on the first page.
``ExpeditionListIndex.locate`` can answer "which row on *this* page is 30", but
only once the caller knows how many rows sit above the page. That number is the
offset, and there is exactly one honest way to obtain it: start at the proven top
of the list and add up the shifts that ``align_pages`` measures after every
scroll.

This module is that bookkeeping, kept out of the driver so it can be exercised
without a browser. It never guesses a coordinate: every click it sends is either
a measured scroll chevron (``plan_scroll_step``) or an area tab cell whose
receipt is the orange label. The row itself is never clicked here - the caller
receives the point and decides.

Why not remember the offset between runs: it belongs to the live scroll position,
which the game resets whenever the tab changes and which nothing else records.
Carrying a stale offset forward would be the one failure mode that silently
clicks the wrong expedition, so every locate starts by climbing to the top.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from .expedition_list import (
    ExpeditionListError,
    ExpeditionListIndex,
    PageScan,
    align_pages,
    plan_scroll_step,
)

# The shipped position index for the account this project was calibrated on. It
# is per-account state (which expeditions are unlocked, and in what order), so it
# lives next to the calibration data rather than with the account-free catalogue.
DEFAULT_POSITION_INDEX_PATH = (
    Path(__file__).resolve().parent / "data" / "expedition_positions.json"
)


class ExpeditionRowError(RuntimeError):
    """The row could not be placed with evidence. Callers must not click."""


@dataclass(frozen=True, slots=True)
class RowLocation:
    """Where a known expedition sits, and what proved it."""

    expedition_id: str
    tab: int
    position: int
    offset: int
    row_index: int
    point: tuple[int, float]
    scroll_clicks: int
    tab_clicks: int
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "expedition_id": self.expedition_id,
            "tab": self.tab,
            "position": self.position,
            "offset": self.offset,
            "row_index": self.row_index,
            "point": {"x": self.point[0], "y": round(self.point[1], 1)},
            "scroll_clicks": self.scroll_clicks,
            "tab_clicks": self.tab_clicks,
            "detail": self.detail,
        }


ScanFn = Callable[[], PageScan]
ClickFn = Callable[[tuple[int, int], str], None]


class ExpeditionListSession:
    """One pass over the live list: select the tab, climb, scroll, locate.

    ``scan`` must return a fresh ``PageScan`` of the current frame and ``click``
    must send exactly one click without retrying - the same contract the walker
    uses. Both are injected so the whole thing is testable offline.
    """

    def __init__(
        self,
        *,
        scan: ScanFn,
        click: ClickFn,
        index: ExpeditionListIndex,
        settle: Callable[[float], None] = time.sleep,
        settle_seconds: float = 0.55,
        max_scroll_steps: int = 24,
        event_sink: Callable[[str, Mapping[str, object]], None] | None = None,
    ) -> None:
        if not callable(scan):
            raise ValueError("scan must be callable")
        if not callable(click):
            raise ValueError("click must be callable")
        if not isinstance(index, ExpeditionListIndex):
            raise ValueError("index must be an ExpeditionListIndex")
        if (
            isinstance(settle_seconds, bool)
            or not isinstance(settle_seconds, (int, float))
            or settle_seconds < 0
        ):
            raise ValueError("settle_seconds must be a finite non-negative number")
        if (
            isinstance(max_scroll_steps, bool)
            or not isinstance(max_scroll_steps, int)
            or max_scroll_steps < 1
        ):
            raise ValueError("max_scroll_steps must be a positive integer")
        self._scan = scan
        self._click = click
        self._index = index
        self._settle = settle
        self._settle_seconds = float(settle_seconds)
        self._max_scroll_steps = max_scroll_steps
        self._event_sink = event_sink
        self.events: list[tuple[str, dict[str, object]]] = []
        self.scroll_clicks = 0
        self.tab_clicks = 0

    def locate_any_tab(self, expedition_id: str) -> RowLocation:
        """Locate ``expedition_id`` on the tab the index says it lives on.

        Dispatching an arbitrary expedition cannot assume the list is showing the
        right area: the game reopens it on whichever tab was last used. The index
        already knows the tab for every bound position, so it is read from there
        rather than guessed - and a number bound on no tab, or on two, is an
        error.
        """
        tab = self._index.tab_of(expedition_id)
        if tab is None:
            raise ExpeditionRowError(
                f"no area tab is bound to {expedition_id}; walk that expedition before "
                "dispatching it"
            )
        return self.locate(expedition_id, tab=tab)

    def locate(self, expedition_id: str, *, tab: int | None = None) -> RowLocation:
        """Return the click point for ``expedition_id`` on the live list.

        The tab is selected first when ``tab`` is given, because the game reopens
        the list on whichever area tab was last used and a position index is
        per-tab. Then the list is climbed to its top (offset 0 is the only offset
        that can be established without prior knowledge), and the session scrolls
        down until the row's position falls inside the visible page.
        """
        if not isinstance(expedition_id, str) or not expedition_id:
            raise ExpeditionRowError("an expedition number must be a non-empty string")

        current = self._scan()
        if tab is not None:
            current = self._select_tab(current, tab)
        current = self._climb_to_top(current)

        position = self._known_position(expedition_id, current.tab)
        offset = 0
        steps = 0
        while True:
            relocation = self._index.locate(expedition_id, current, offset=offset)
            if relocation.state == "located" and relocation.point is not None:
                return RowLocation(
                    expedition_id=expedition_id,
                    tab=current.tab if current.tab is not None else -1,
                    position=position,
                    offset=offset,
                    row_index=relocation.row_index or 0,
                    point=relocation.point,
                    scroll_clicks=steps,
                    tab_clicks=self.tab_clicks,
                    detail=relocation.detail,
                )
            if relocation.state == "unbound":
                raise ExpeditionRowError(
                    f"no list position is bound to {expedition_id} on tab "
                    f"{current.tab}; the position index has no entry for it"
                )
            if relocation.state == "ambiguous":  # pragma: no cover - index forbids it
                raise ExpeditionRowError(f"{expedition_id} is bound to more than one position")
            # not_visible: the row is below this page, or the page is too short.
            if current.scroll_down is None:
                raise ExpeditionRowError(
                    f"position {position} of {expedition_id} is not reachable: "
                    f"the list has no measured scroll-down control at offset {offset}"
                )
            if steps >= self._max_scroll_steps:
                raise ExpeditionRowError(
                    f"gave up after {steps} scroll steps looking for position {position}"
                )
            proposal = plan_scroll_step(current, direction="down")
            if proposal.action != "click_measured_control" or proposal.point is None:
                raise ExpeditionRowError(
                    f"refusing to scroll without a measured control: {proposal.detail}"
                )
            self._send_click(proposal.point, "expedition-list-down")
            self.scroll_clicks += 1
            self._settle(self._settle_seconds)
            moved = self._scan()
            if moved.tab is not None and current.tab is not None and moved.tab != current.tab:
                raise ExpeditionRowError(
                    f"the area tab changed while scrolling "
                    f"({current.tab} -> {moved.tab}); positions are per-tab"
                )
            try:
                shift, support = align_pages(current, moved)
            except ExpeditionListError as error:
                raise ExpeditionRowError(f"cannot place the scrolled page: {error}") from error
            if shift < 1:  # pragma: no cover - align_pages only reports climbs
                raise ExpeditionRowError(f"the list moved by {shift} rows, which cannot be right")
            self._emit("expedition_list_scrolled", offset=offset, shift=shift, support=support)
            offset += shift
            steps += 1
            current = moved

    def _select_tab(self, current: PageScan, tab: int) -> PageScan:
        centers = current.tabs.centers
        if not 0 <= tab < len(centers):
            raise ExpeditionRowError(
                f"tab {tab} is outside the {len(centers)} area tabs on screen"
            )
        if current.tab == tab:
            return current
        if current.tab is None:
            raise ExpeditionRowError(
                "the selected area tab is not readable, so switching tabs cannot be verified"
            )
        point = centers[tab]
        self._send_click(point, f"expedition-tab-{tab + 1}")
        self.tab_clicks += 1
        self._settle(self._settle_seconds)
        after = self._scan()
        if after.tabs.count != current.tabs.count:
            raise ExpeditionRowError(
                f"the tab strip changed width ({current.tabs.count} -> {after.tabs.count})"
            )
        if after.tab != tab:
            raise ExpeditionRowError(
                f"the orange label did not move to tab {tab} (now {after.tab})"
            )
        self._emit("expedition_list_tab_selected", tab=tab)
        return after

    def _climb_to_top(self, current: PageScan) -> PageScan:
        """Scroll up until no up-chevron is measured; that is offset 0."""
        if not current.scroll_probed:
            raise ExpeditionRowError(
                "the capture is too small to tell whether the list is scrolled, "
                "so the top of the list cannot be established"
            )
        steps = 0
        while current.scroll_up is not None:
            if steps >= self._max_scroll_steps:
                raise ExpeditionRowError(f"the list never reached its top in {steps} steps")
            proposal = plan_scroll_step(current, direction="up")
            if proposal.action != "click_measured_control" or proposal.point is None:
                raise ExpeditionRowError(
                    f"refusing to scroll up without a measured control: {proposal.detail}"
                )
            self._send_click(proposal.point, "expedition-list-up")
            self.scroll_clicks += 1
            self._settle(self._settle_seconds)
            steps += 1
            after = self._scan()
            if not after.rows:
                raise ExpeditionRowError(f"the list went blank while climbing (step {steps})")
            if after.tab is not None and current.tab is not None and after.tab != current.tab:
                raise ExpeditionRowError(
                    f"the area tab changed while climbing ({current.tab} -> {after.tab})"
                )
            current = after
        if not current.rows:
            raise ExpeditionRowError("the list is empty, so there is nothing to locate")
        return current

    def _known_position(self, expedition_id: str, tab: int | None) -> int:
        matches = [
            position
            for (bound_tab, position), bound in self._index.bindings.items()
            if bound == expedition_id and bound_tab == tab
        ]
        if not matches:
            raise ExpeditionRowError(
                f"no list position is bound to {expedition_id} on tab {tab}; "
                "walk and label that tab first"
            )
        if len(matches) > 1:  # pragma: no cover - bind() forbids it
            raise ExpeditionRowError(f"{expedition_id} is bound to {len(matches)} positions")
        return matches[0]

    def _send_click(self, point: tuple[int, int], label: str) -> None:
        """Write a critical audit pair around one measured list click."""
        payload = {
            "target": label,
            "point": {"x": point[0], "y": point[1]},
        }
        self._emit("expedition_list_click_attempted", **payload)
        self._click(point, label)
        self._emit("expedition_list_click_sent", **payload)

    def _emit(self, event: str, **fields: object) -> None:
        payload = dict(fields)
        if self._event_sink is not None:
            self._event_sink(event, payload)
        else:  # keep a local trail even without a sink
            self.events.append((event, payload))
