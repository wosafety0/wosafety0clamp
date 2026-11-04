"""Read-only discovery of the scrollable, per-area expedition list.

The expedition selection screen is *not* a fixed eight-row table. Two measured
facts break any "one screenshot is the whole list" assumption:

* The list is split into area tabs (``鎮守府海域`` .. ``中部海域``); each tab is an
  independent list of that area's expeditions.
* A tab only shows the expeditions the player has unlocked, and locked entries
  are omitted from the *middle* of the sequence, not just its tail. On a live
  capture of ``鎮守府海域`` the visible rows were ``01..07, A1`` while ``08`` was
  missing, even though ``08`` precedes ``A1`` in both the master data
  (``const.$missions``) and the community wiki table. A row's display position
  is therefore *not* its expedition number, and cannot be derived from it.

Consequently every row is identified by a pixel signature of its own rendering
and bound to an expedition number once. Scrolling then only needs *order*, which
is recovered by matching signatures across pages, so new expeditions (``A2``,
``B1``, ...) never require a code change: they simply arrive unbound and are
labelled once.

A ``2026-11-04`` walk of ``鎮守府海域`` showed how much that matters: that account
had unlocked twelve entries (``01`` .. ``08`` then ``A1`` .. ``A4``), so four of
them existed only on pages the eight-row viewport never showed. Twelve is that
account's unlock state, *not* the size of the area: ``expedition_catalog``
records fourteen for the same area (adding ``A5``/``A6``), and the two numbers
must not be conflated. One click of the gold
chevron scrolls exactly one row, and the ``PREV``/``NEXT`` chevrons are drawn
only while there is somewhere to go: ``PREV`` was absent on the first page and
``NEXT`` on the last, which is how a paging loop recognises both ends.

Everything in this module is pure: it reads pixels and returns proposals. It
never clicks, never scrolls, and never mutates game state. A caller that wants
to page through the list must obtain its own authorisation for the input; this
module only tells it what a structurally safe step would be.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from .expedition_rows import (
    DEFAULT_DARK_THRESHOLD,
    DEFAULT_MERGE_GAP,
    DEFAULT_MIN_ROW_HEIGHT,
    DEFAULT_MIN_ROW_PIXELS,
    DEFAULT_PROJECTION_X,
    DEFAULT_SEARCH_Y,
    ExpeditionRow,
    RowCalibrationError,
    detect_rows,
    dark_row_projection,
)
from .png import RgbImage

# Measured on a live 1200x720 capture: eight rows fit between y=250 and y=601,
# so eight is the visible capacity of one area tab.
DEFAULT_ROW_CAPACITY = 8
# The status column (completion tick, "NEW" badge) sits left of x=220 and the
# badge disappears once the row has been looked at, so it must stay out of the
# signature or a row would change identity just by being read.
DEFAULT_SIGNATURE_X = (233, 745)
SIGNATURE_BINS = 16
SIGNATURE_MAX_LEVEL = 63
# A row's detected band may or may not merge its second text line depending on
# how dark that line is, so the fingerprint is taken from a fixed-height window
# anchored at the band's top edge. The anchor is content-derived and the height
# is constant, which makes the fingerprint identical when the same row is drawn
# in a different slot after scrolling.
SIGNATURE_HEIGHT = 24
# Identical rows re-render to identical pixels; a rendering of a *different*
# expedition usually differs in several bins. The margin is far thinner than the
# first version of this note claimed ("0 for the same row, >20 for distinct"):
# a live 西方海域 capture showed 29 潜水艦派遣演習 and 30 潜水艦派遣作戦 hashing to
# the *same* key, because their names share length and ink and the material icons
# are too small to move a bin. 28 vs 29 landed exactly on this tolerance, and an
# edge-clipped row drifted by up to 4 across scrolls.
#
# The two ranges therefore overlap, and no value of this constant separates them:
# a gate wide enough for a clipped row is wide enough for its neighbour.
#
# The constant is therefore only used for *alignment* (``align_pages``, where
# many rows vote on one shift) and for asking "is this still the same view"
# (``same_view``). It is never used to decide which number a row is: identity
# comes from the row's list position, which does not care what the row looks
# like. Two expeditions that render identically are still two positions.
DEFAULT_SIGNATURE_TOLERANCE = 6

# Measured on a live capture: seven area tabs of ~52px starting at x=176 with a
# 60px pitch. The selected tab paints its label orange while the others paint
# white, which separates 567 orange pixels from 9 on the same capture.
#
# The pitch is not a guess: on the 2026-11-03 capture the low-saturation
# separator columns between tabs measured 236, 296, 356, 416, 476, 536 and 596,
# i.e. exactly 176 + 60k for k = 1..7, so the strip really is uniform. What has
# only ever been observed is the first tab *selected*; a build that opened on a
# later area would still be read through the same colours, just never seen yet.
DEFAULT_TAB_STRIP_X0 = 176
DEFAULT_TAB_STRIP_PITCH = 60
DEFAULT_TAB_STRIP_WIDTH = 52
DEFAULT_TAB_STRIP_Y = (666, 692)
DEFAULT_TAB_COUNT = 7

# The seven labels, read off a live capture on 2026-11-04 (human-read, not OCR:
# this project decodes pixels, and the labels are Japanese text). Measured order
# is 鎮守府, 南西諸島, 北方, 南西, 西方, 南方, 中部 - which is *not* the order the
# wiki's requirement table lists (it puts 西方 before 南西), so mapping a tab
# index by the wiki's row order would silently mislabel two whole areas.
#
# The last tab, 中部海域, showed an empty list on that capture: the account had
# unlocked nothing there. An empty area is a legitimate state, not a scan
# failure, and the wiki table this project ships does not list it at all.
DEFAULT_TAB_AREAS = (
    "鎮守府海域",
    "南西諸島海域",
    "北方海域",
    "南西海域",
    "西方海域",
    "南方海域",
    "中部海域",
)
DEFAULT_ORANGE_PIXEL_FLOOR = 60
DEFAULT_ORANGE_PIXEL_RATIO = 10

# Measured list panel (inner box) of the live capture.
DEFAULT_PANEL_BOX = (163, 234, 790, 617)
DEFAULT_CLICK_X = 350

# Measured on the 2026-11-04 live capture (1200x720) of 鎮守府海域 while the list
# was showing rows 2..N of a nine-entry area: the list is paged by a gold double
# chevron carrying a PREV (top) or NEXT (bottom) label, drawn over the list and
# centred horizontally, above the first row and below the last one.
#
# The gold response is unambiguous: counting pixels with r>=195, g>=165, b<=130
# and r-b>=90 gave 393 inside the up box and 380 inside the down box, against 0
# anywhere else in the list panel. Both boxes below are the *bounding box of the
# glyph plus its label*, so the centre falls in the middle of the control.
DEFAULT_SCROLL_UP_BOX = (431, 183, 467, 219)
DEFAULT_SCROLL_DOWN_BOX = (433, 607, 468, 645)
GOLD_MIN_RED = 195
GOLD_MIN_GREEN = 165
GOLD_MAX_BLUE = 130
GOLD_MIN_RED_MINUS_BLUE = 90
# Well under the measured 380-393 and well over the 0 seen on gold-free surfaces.
DEFAULT_SCROLL_GOLD_FLOOR = 120


class ExpeditionListError(RuntimeError):
    """The page cannot be scanned or the pages cannot be aligned safely."""


class BoundaryState(str, Enum):
    """What the scan is allowed to claim about the end of the list."""

    END_CONFIRMED = "end_confirmed"
    MORE_BELOW = "more_below"
    # A measured scroll control proves the list is *paged*, which is weaker than
    # "there are rows below this page": the control is drawn as soon as the area
    # holds more entries than one page, and the live capture still showed NEXT
    # while displaying what may well be the last page. Establishing that rows
    # continue needs a scroll plus a content shift, so this state deliberately
    # stays distinct from MORE_BELOW.
    PAGED = "paged"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RowSignature:
    """A position-independent fingerprint of one rendered row."""

    bins: tuple[int, ...]
    dark_total: int

    def __post_init__(self) -> None:
        if not self.bins:
            raise ValueError("a signature needs at least one bin")
        if any(not 0 <= value <= SIGNATURE_MAX_LEVEL for value in self.bins):
            raise ValueError("signature bins are outside the quantisation range")
        if self.dark_total < 0:
            raise ValueError("dark_total must be non-negative")

    @property
    def key(self) -> str:
        """Return a stable string key for persistence."""
        return "-".join(f"{value:02x}" for value in self.bins) + f"/{self.dark_total:05d}"

    def distance(self, other: "RowSignature") -> int:
        if len(self.bins) != len(other.bins):
            raise ValueError("signatures must share a bin count")
        return sum(abs(a - b) for a, b in zip(self.bins, other.bins))

    def matches(self, other: "RowSignature", *, tolerance: int = DEFAULT_SIGNATURE_TOLERANCE) -> bool:
        return self.distance(other) <= tolerance


def signature_from_key(key: str) -> RowSignature:
    """Rebuild a signature from its persisted ``key`` form."""
    try:
        bin_part, total_part = key.split("/")
        bins = tuple(int(part, 16) for part in bin_part.split("-"))
        total = int(total_part)
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"malformed signature key: {key!r}") from exc
    try:
        return RowSignature(bins, total)
    except ValueError as exc:
        raise ValueError(f"malformed signature key: {key!r}") from exc


@dataclass(frozen=True, slots=True)
class VisibleRow:
    """One row that is actually on screen right now."""

    index: int
    top: int
    bottom: int
    center: float
    signature: RowSignature

    @property
    def click_point(self) -> tuple[int, float]:
        return (DEFAULT_CLICK_X, self.center)


@dataclass(frozen=True, slots=True)
class ButtonCandidate:
    """A small isolated glyph cluster that *might* be a scroll affordance.

    Candidates are proposals, never targets: a caller must confirm one before
    any click, because a wrong guess would press a real control.
    """

    left: int
    top: int
    right: int
    bottom: int
    pixels: int

    @property
    def width(self) -> int:
        return self.right - self.left + 1

    @property
    def height(self) -> int:
        return self.bottom - self.top + 1

    @property
    def center(self) -> tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)


@dataclass(frozen=True, slots=True)
class ScrollAffordance:
    """A scroll control that was *measured*, not guessed.

    Unlike :class:`ButtonCandidate` this carries the exact box that was checked
    and the gold-pixel count that justified calling it a control, so a caller
    can audit the verdict instead of trusting a coordinate.
    """

    direction: str
    box: tuple[int, int, int, int]
    gold_pixels: int

    def __post_init__(self) -> None:
        if self.direction not in {"up", "down"}:
            raise ValueError("direction must be 'up' or 'down'")
        left, top, right, bottom = self.box
        if not (left < right and top < bottom):
            raise ValueError("a scroll control box must be increasing")
        if self.gold_pixels < 0:
            raise ValueError("gold_pixels must be non-negative")

    @property
    def width(self) -> int:
        return self.box[2] - self.box[0] + 1

    @property
    def height(self) -> int:
        return self.box[3] - self.box[1] + 1

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.box
        return ((left + right) // 2, (top + bottom) // 2)


@dataclass(frozen=True, slots=True)
class AreaTabs:
    """The area tab strip plus which tab is currently selected."""

    count: int
    active_index: int | None
    scores: tuple[int, ...]
    centers: tuple[tuple[int, int], ...]

    @property
    def selected(self) -> bool:
        return self.active_index is not None


@dataclass(frozen=True, slots=True)
class PageScan:
    """Everything one screenshot can truthfully say about the list."""

    rows: tuple[VisibleRow, ...]
    tabs: AreaTabs
    scroll_candidates: tuple[ButtonCandidate, ...]
    image_size: tuple[int, int]
    capacity: int = DEFAULT_ROW_CAPACITY
    scroll_up: ScrollAffordance | None = None
    scroll_down: ScrollAffordance | None = None
    # True when both control boxes fitted inside the capture, so "no control
    # found" is a real observation rather than an unanswerable question.
    scroll_probed: bool = False

    def __post_init__(self) -> None:
        if self.capacity < 1:
            raise ValueError("capacity must be positive")
        for position, row in enumerate(self.rows, start=1):
            if row.index != position:
                raise ValueError("row indices must be 1..N in screen order")

    @property
    def tab(self) -> int | None:
        return self.tabs.active_index

    @property
    def is_full(self) -> bool:
        return len(self.rows) >= self.capacity

    @property
    def last_row_bottom(self) -> int | None:
        return self.rows[-1].bottom if self.rows else None


@dataclass(frozen=True, slots=True)
class ListBoundary:
    state: BoundaryState
    reason: str

    def __post_init__(self) -> None:
        if not self.reason:
            raise ValueError("a boundary verdict needs a reason")


@dataclass(frozen=True, slots=True)
class ScrollProposal:
    """A structural suggestion for the *next* scroll step, never executed here."""

    action: str
    point: tuple[int, int] | None
    detail: str


def row_signature(
    image: RgbImage,
    row: ExpeditionRow | VisibleRow,
    *,
    signature_x: tuple[int, int] = DEFAULT_SIGNATURE_X,
    dark_threshold: int = DEFAULT_DARK_THRESHOLD,
    bins: int = SIGNATURE_BINS,
    height: int = SIGNATURE_HEIGHT,
) -> RowSignature:
    """Fingerprint a row from the top of its text band, for a fixed height.

    Anchoring at the detected top and using a constant height keeps the window
    on the same *content* whatever slot the row occupies, so a row keeps its
    fingerprint across scrolls even though its y coordinate changes.
    """
    left, right = signature_x
    if not (0 <= left < right <= image.width):
        raise ValueError("signature columns are outside the image")
    if bins < 1:
        raise ValueError("bins must be positive")
    if height < 1:
        raise ValueError("height must be positive")
    top = max(0, row.top)
    bottom = min(image.height, top + height)
    span_rows = bottom - top
    if span_rows < 1:
        raise ValueError("row band is empty")
    span = right - left
    edges = [left + round(span * index / bins) for index in range(bins + 1)]
    values: list[int] = []
    dark_total = 0
    for index in range(bins):
        start, stop = edges[index], edges[index + 1]
        if stop <= start:  # pragma: no cover - only for bins > span
            values.append(0)
            continue
        dark = 0
        for y in range(top, bottom):
            for x in range(start, stop):
                if image.luminance(x, y) < dark_threshold:
                    dark += 1
        dark_total += dark
        values.append(min(SIGNATURE_MAX_LEVEL, dark // span_rows))
    return RowSignature(tuple(values), dark_total)


def _orange_pixels(image: RgbImage, left: int, right: int, top: int, bottom: int) -> int:
    count = 0
    for y in range(top, bottom):
        for x in range(left, right):
            base = (y * image.width + x) * image.channels
            red = image.pixels[base]
            green = image.pixels[base + 1] if image.channels >= 3 else red
            blue = image.pixels[base + 2] if image.channels >= 3 else red
            if red >= 150 and red - blue >= 60 and green >= 60:
                count += 1
    return count


def detect_area_tabs(
    image: RgbImage,
    *,
    x0: int = DEFAULT_TAB_STRIP_X0,
    pitch: int = DEFAULT_TAB_STRIP_PITCH,
    width: int = DEFAULT_TAB_STRIP_WIDTH,
    y_range: tuple[int, int] = DEFAULT_TAB_STRIP_Y,
    count: int = DEFAULT_TAB_COUNT,
    floor: int = DEFAULT_ORANGE_PIXEL_FLOOR,
    ratio: int = DEFAULT_ORANGE_PIXEL_RATIO,
) -> AreaTabs:
    """Return the tab strip and the tab whose label is painted orange.

    The selected area tab is the only one with an orange label, so the verdict
    is taken from a colour count rather than from an assumed tab order. When the
    winner is not clearly ahead of the runner-up the result is "unknown" and the
    caller must stop instead of scoping a list to the wrong area.
    """
    top, bottom = y_range
    if not (0 <= top < bottom <= image.height):
        raise ValueError("tab strip rows are outside the image")
    if count < 1 or pitch < 1 or width < 1:
        raise ValueError("tab strip geometry must be positive")
    scores: list[int] = []
    centers: list[tuple[int, int]] = []
    for index in range(count):
        left = x0 + pitch * index
        right = min(image.width, left + width)
        if right <= left:
            raise ValueError("tab strip extends past the image")
        scores.append(_orange_pixels(image, left, right, top, bottom))
        centers.append(((left + right) // 2, (top + bottom) // 2))
    order = sorted(range(count), key=lambda index: scores[index], reverse=True)
    best = scores[order[0]]
    runner_up = scores[order[1]] if count > 1 else 0
    if best < floor or (runner_up and best < runner_up * ratio):
        return AreaTabs(count, None, tuple(scores), tuple(centers))
    return AreaTabs(count, order[0], tuple(scores), tuple(centers))


def find_button_candidates(
    image: RgbImage,
    *,
    region: tuple[int, int, int, int],
    dark_threshold: int = 120,
    min_size: int = 6,
    max_size: int = 30,
) -> tuple[ButtonCandidate, ...]:
    """Find small dark glyph clusters inside ``region`` (left, top, right, bottom).

    A list that overflows its panel is expected to show ▲/▼ style controls near
    the list edge. This only reports clusters that *look* like such controls so
    a human can confirm the geometry once; nothing here is safe to click blindly.
    """
    left, top, right, bottom = region
    if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
        raise ValueError("candidate region is outside the image")
    seen: set[tuple[int, int]] = set()
    clusters: list[ButtonCandidate] = []
    for y in range(top, bottom):
        for x in range(left, right):
            if (x, y) in seen or image.luminance(x, y) >= dark_threshold:
                continue
            stack = [(x, y)]
            seen.add((x, y))
            pixels = 0
            min_x = max_x = x
            min_y = max_y = y
            while stack:
                cx, cy = stack.pop()
                pixels += 1
                min_x, max_x = min(min_x, cx), max(max_x, cx)
                min_y, max_y = min(min_y, cy), max(max_y, cy)
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if not (left <= nx < right and top <= ny < bottom):
                        continue
                    if (nx, ny) in seen or image.luminance(nx, ny) >= dark_threshold:
                        continue
                    seen.add((nx, ny))
                    stack.append((nx, ny))
            width = max_x - min_x + 1
            height = max_y - min_y + 1
            if not (min_size <= width <= max_size and min_size <= height <= max_size):
                continue
            if not 0.5 <= width / height <= 2.0:
                continue
            if pixels < width * height * 0.15:
                continue
            clusters.append(ButtonCandidate(min_x, min_y, max_x, max_y, pixels))
    clusters.sort(key=lambda item: (item.top, item.left))
    return tuple(clusters)


def _box_inside(image: RgbImage, box: tuple[int, int, int, int]) -> bool:
    left, top, right, bottom = box
    return 0 <= left < right <= image.width and 0 <= top < bottom <= image.height


def count_gold_pixels(
    image: RgbImage,
    box: tuple[int, int, int, int],
    *,
    min_red: int = GOLD_MIN_RED,
    min_green: int = GOLD_MIN_GREEN,
    max_blue: int = GOLD_MAX_BLUE,
    min_red_minus_blue: int = GOLD_MIN_RED_MINUS_BLUE,
) -> int:
    """Count gold pixels inside ``box`` (``left, top, right, bottom``).

    The threshold triple was fitted to the live scroll control and is narrow on
    purpose: on that capture nothing else inside the list panel came close.
    """
    left, top, right, bottom = box
    if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
        raise ValueError("gold probe box is outside the image")
    if image.channels < 3:
        return 0
    pixels = image.pixels
    channels = image.channels
    count = 0
    for y in range(top, bottom):
        base = (y * image.width + left) * channels
        for _ in range(left, right):
            red, green, blue = pixels[base], pixels[base + 1], pixels[base + 2]
            if (
                red >= min_red
                and green >= min_green
                and blue <= max_blue
                and red - blue >= min_red_minus_blue
            ):
                count += 1
            base += channels
    return count


def detect_scroll_affordance(
    image: RgbImage,
    *,
    direction: str,
    box: tuple[int, int, int, int] | None = None,
    gold_floor: int = DEFAULT_SCROLL_GOLD_FLOOR,
) -> ScrollAffordance | None:
    """Return the measured scroll control for ``direction``, or ``None``.

    ``None`` means "this capture does not prove the control is there" — either
    the calibrated box falls outside the image (a smaller capture) or the gold
    response is under the floor. It never means "the list cannot scroll": a
    control may still be present with different geometry, which is why the
    caller keeps the generic candidate probe as a fallback.
    """
    if direction not in {"up", "down"}:
        raise ValueError("direction must be 'up' or 'down'")
    if box is None:
        box = DEFAULT_SCROLL_UP_BOX if direction == "up" else DEFAULT_SCROLL_DOWN_BOX
    left, top, right, bottom = box
    if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
        return None
    gold = count_gold_pixels(image, box)
    if gold < gold_floor:
        return None
    return ScrollAffordance(direction, box, gold)


def scan_page(
    image: RgbImage,
    *,
    x_range: tuple[int, int] = DEFAULT_PROJECTION_X,
    y_range: tuple[int, int] = DEFAULT_SEARCH_Y,
    signature_x: tuple[int, int] = DEFAULT_SIGNATURE_X,
    dark_threshold: int = DEFAULT_DARK_THRESHOLD,
    min_pixels: int = DEFAULT_MIN_ROW_PIXELS,
    min_height: int = DEFAULT_MIN_ROW_HEIGHT,
    merge_gap: int = DEFAULT_MERGE_GAP,
    panel_box: tuple[int, int, int, int] = DEFAULT_PANEL_BOX,
    capacity: int = DEFAULT_ROW_CAPACITY,
    scroll_up_box: tuple[int, int, int, int] = DEFAULT_SCROLL_UP_BOX,
    scroll_down_box: tuple[int, int, int, int] = DEFAULT_SCROLL_DOWN_BOX,
) -> PageScan:
    """Scan the visible page: rows, selected area tab, and scroll candidates."""
    projection = dark_row_projection(image, x_range=x_range, dark_threshold=dark_threshold)
    try:
        detected = detect_rows(
            projection,
            y_range=y_range,
            min_pixels=min_pixels,
            min_height=min_height,
            merge_gap=merge_gap,
        )
    except RowCalibrationError as exc:  # pragma: no cover - detect_rows returns ()
        raise ExpeditionListError(str(exc)) from exc
    rows = tuple(
        VisibleRow(
            index=row.index,
            top=row.top,
            bottom=row.bottom,
            center=row.center,
            signature=row_signature(
                image,
                row,
                signature_x=signature_x,
                dark_threshold=dark_threshold,
            ),
        )
        for row in detected
    )
    panel_left, panel_top, panel_right, panel_bottom = panel_box
    rows_bottom = rows[-1].bottom if rows else panel_top
    # Only the strip under the last row is probed: the vertical band beside the
    # rows is *content* on a live capture (the 参考編成 ship icons and the time
    # column read as 10x7 clusters at every row), so probing it would report six
    # false controls.
    #
    # The scroll controls themselves no longer rely on this probe: their boxes
    # were measured on a live overfull page (see DEFAULT_SCROLL_UP_BOX), so the
    # generic glyph search is kept only as a fallback for a capture whose chrome
    # differs from the calibrated screen.
    candidates: tuple[ButtonCandidate, ...] = ()
    if rows_bottom + 2 < panel_bottom:
        candidates = find_button_candidates(
            image, region=(panel_left, rows_bottom + 2, panel_right - 2, panel_bottom)
        )
    return PageScan(
        rows=rows,
        tabs=detect_area_tabs(image),
        scroll_candidates=candidates,
        image_size=(image.width, image.height),
        capacity=capacity,
        scroll_up=detect_scroll_affordance(image, direction="up", box=scroll_up_box),
        scroll_down=detect_scroll_affordance(
            image, direction="down", box=scroll_down_box
        ),
        scroll_probed=_box_inside(image, scroll_up_box)
        and _box_inside(image, scroll_down_box),
    )


def classify_boundary(scan: PageScan, *, capacity: int | None = None) -> ListBoundary:
    """Decide whether the list provably ends on this page.

    Only two things count as proof of the end: the page is not full (rows are
    painted from the top, so a short page has nothing below it), or a scroll
    step that provably moved nothing. A completely full page proves nothing, so
    it is reported as unknown rather than as the end of the list.

    A measured scroll control makes the page ``PAGED``: the area holds more
    entries than one page, but which direction still has content is not settled
    by the control alone, so ``PAGED`` never collapses into ``MORE_BELOW``.

    Once the control boxes have actually been probed, the measured answer
    outranks the legacy glyph probe. A live capture of the *last* page settled
    why that matters: NEXT is drawn while more rows follow and disappears on the
    final page, and there the old generic probe still found a dark cluster and
    would have claimed ``MORE_BELOW`` on a page that provably had nothing below.
    """
    limit = scan.capacity if capacity is None else capacity
    if not scan.rows:
        return ListBoundary(BoundaryState.UNKNOWN, "no_rows_detected")
    if len(scan.rows) < limit:
        return ListBoundary(BoundaryState.END_CONFIRMED, "rows_below_capacity")
    if scan.scroll_down is not None:
        return ListBoundary(BoundaryState.PAGED, "measured_scroll_control_present")
    if scan.scroll_probed:
        # The boxes were read and held no control. That is not proof of the end
        # either (the control could be dimmed or restyled), so stay unknown.
        return ListBoundary(BoundaryState.UNKNOWN, "full_page_without_measured_control")
    if scan.scroll_candidates:
        return ListBoundary(BoundaryState.MORE_BELOW, "scroll_affordance_present")
    return ListBoundary(BoundaryState.UNKNOWN, "full_page_without_scroll_affordance")


def plan_scroll_step(
    scan: PageScan,
    *,
    direction: str = "down",
    confirmed_point: tuple[int, int] | None = None,
) -> ScrollProposal:
    """Propose the next scroll step without performing it.

    A measured control wins because its box and gold count were read off a live
    capture; a human-confirmed point is the fallback for a screen with different
    chrome. Without either the step is blocked, since guessing a coordinate in
    the expedition screen risks pressing a real control.
    """
    if direction not in {"up", "down"}:
        raise ValueError("direction must be 'up' or 'down'")
    boundary = classify_boundary(scan)
    control = scan.scroll_up if direction == "up" else scan.scroll_down
    if direction == "down" and boundary.state is BoundaryState.END_CONFIRMED:
        return ScrollProposal("blocked_at_end", None, boundary.reason)
    if control is not None:
        return ScrollProposal(
            "click_measured_control",
            control.center,
            f"measured scroll-{control.direction} control {control.box} "
            f"with {control.gold_pixels} gold pixels",
        )
    if confirmed_point is not None:
        return ScrollProposal(
            "click_confirmed_point", confirmed_point, "human-confirmed scroll control"
        )
    if direction == "up":
        return ScrollProposal(
            "blocked_at_top",
            None,
            "no measured scroll-up control on this page, so the top is assumed",
        )
    if scan.scroll_probed:
        # The calibrated boxes were read and held nothing. Falling back to a
        # generic dark cluster here would offer a point that was never verified:
        # on the live last page that fallback pointed at (782, 593), the star
        # icons at the right edge of the rows, which is not a scroll control.
        return ScrollProposal(
            "blocked_no_measured_control",
            None,
            "the calibrated scroll boxes hold no control on this page",
        )
    if scan.scroll_candidates:
        candidate = scan.scroll_candidates[0]
        return ScrollProposal(
            "confirm_candidate", candidate.center, "unconfirmed scroll candidate"
        )
    return ScrollProposal(
        "blocked_no_scroll_affordance", None, "no scroll control was detected on this page"
    )


def align_pages(previous: PageScan, current: PageScan, *,
                tolerance: int = DEFAULT_SIGNATURE_TOLERANCE) -> tuple[int, int]:
    """Return ``(shift, support)`` rows between two consecutive pages.

    ``shift`` is how many rows scrolled off the top, recovered by matching the
    rows that survived. Measuring the shift from content is what makes the scan
    independent of how far a single scroll step happens to move the list.

    The rows at the two ends of the overlap are exempt from the match: the list
    viewport clips them, so their detected band is shorter than a full row's and
    the fingerprint shifts with it. On the 2026-11-04 live scroll the first
    visible band measured 32px before the click and 24px after, while every
    interior row matched exactly (distance 0). An interior mismatch still
    invalidates the shift, which is what keeps two unrelated pages from being
    stitched together.
    """
    if not previous.rows or not current.rows:
        raise ExpeditionListError("both pages need at least one row to align")
    if previous.tab is not None and current.tab is not None and previous.tab != current.tab:
        raise ExpeditionListError("pages belong to different area tabs")
    best: tuple[int, int] | None = None
    saw_single = False
    for shift in range(1, len(previous.rows)):
        overlap = min(len(current.rows), len(previous.rows) - shift)
        if overlap < 1:
            continue
        mismatched = [
            index
            for index in range(overlap)
            if not previous.rows[index + shift].signature.matches(
                current.rows[index].signature, tolerance=tolerance
            )
        ]
        clipped_edges = {0, overlap - 1}
        if any(index not in clipped_edges for index in mismatched):
            continue
        support = overlap - len(mismatched)
        if support < 2:
            saw_single = True
            continue
        best = (shift, support)
        break
    if best is None:
        if saw_single:
            raise ExpeditionListError("a single shared row cannot prove the shift")
        raise ExpeditionListError("consecutive pages share no rows; the shift is unknown")
    return best


def same_view(previous: PageScan, current: PageScan, *,
              tolerance: int = DEFAULT_SIGNATURE_TOLERANCE) -> bool:
    """True when every row is unchanged, i.e. the last scroll did nothing."""
    if len(previous.rows) != len(current.rows):
        return False
    return all(
        left.signature.matches(right.signature, tolerance=tolerance)
        for left, right in zip(previous.rows, current.rows)
    )


@dataclass(frozen=True, slots=True)
class ListEntry:
    """One expedition as far as the scan knows it."""

    expedition_id: str
    tab: int | None
    visible: bool
    scroll_page: int
    center: tuple[int, float] | None
    name: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "expedition_id": self.expedition_id,
            "name": self.name,
            "visible": self.visible,
            "scroll_page": self.scroll_page,
            "tab": self.tab,
            "center": (
                None
                if self.center is None
                else {"x": self.center[0], "y": round(self.center[1], 1)}
            ),
        }


@dataclass(frozen=True, slots=True)
class PageRecord:
    page: int
    scan: PageScan
    offset: int
    ids: tuple[str | None, ...]

    @property
    def unbound_rows(self) -> tuple[int, ...]:
        return tuple(index + 1 for index, value in enumerate(self.ids) if value is None)


@dataclass(frozen=True, slots=True)
class Relocation:
    """The answer to "where is expedition X *right now*"."""

    state: str
    point: tuple[int, float] | None
    row_index: int | None
    detail: str


@dataclass
class ExpeditionListIndex:
    """Position-to-number bindings plus the merged, de-duplicated pages.

    Identity is the row's **list position** - how many rows are above it in the
    area tab - not its appearance. That choice is forced by measurement: in
    ``西方海域`` the rows ``29 潜水艦派遣演習`` and ``30 潜水艦派遣作戦`` render to
    the same 16-bin fingerprint and the same dark-pixel total, so no pixel
    comparison can tell them apart, while their positions differ by exactly one.
    A position survives a re-render, a font change, and the "NEW" badge being
    consumed; a fingerprint does not.

    The binding map is the durable part: it is what lets a later scan name a row
    without reading any glyph, and it is meant to be persisted between runs so
    that labelling is a one-time cost per expedition.
    """

    bindings: dict[tuple[int | None, int], str] = field(default_factory=dict)
    pages: list[PageRecord] = field(default_factory=list)

    def resolve(self, tab: int | None, position: int) -> str | None:
        """Return the number bound to a list position, if that position is known.

        No tolerance and no similarity search: a position either has a number or
        it does not. That is the whole point of moving identity off the pixels.
        """
        return self.bindings.get((tab, position))

    def bind(self, tab: int | None, position: int, expedition_id: str) -> None:
        _validate_expedition_id(expedition_id)
        if position < 0:
            raise ExpeditionListError("a list position cannot be negative")
        key = (tab, position)
        existing = self.bindings.get(key)
        if existing is not None and existing != expedition_id:
            raise ExpeditionListError(
                f"position {position} is already bound to {existing}, "
                f"refusing to rebind to {expedition_id}"
            )
        for (other_tab, other_position), other_id in self.bindings.items():
            if other_tab == tab and other_id == expedition_id and other_position != position:
                raise ExpeditionListError(
                    f"{expedition_id} is already bound to position {other_position}"
                )
        self.bindings[key] = expedition_id

    def add_page(self, scan: PageScan) -> PageRecord:
        """Append a page and resolve as many row->number bindings as possible."""
        tab = scan.tab
        if self.pages:
            previous = self.pages[-1]
            if same_view(previous.scan, scan):
                raise ExpeditionListError("the view did not change; do not append it again")
            shift, _ = align_pages(previous.scan, scan)
            offset = previous.offset + shift
        else:
            offset = 0
        ids = tuple(
            self.resolve(tab, offset + row.index - 1) for row in scan.rows
        )
        record = PageRecord(page=len(self.pages), scan=scan, offset=offset, ids=ids)
        self.pages.append(record)
        return record

    def label_row(self, page: int, row_index: int, expedition_id: str) -> None:
        if not 0 <= page < len(self.pages):
            raise ExpeditionListError("page index is out of range")
        record = self.pages[page]
        if not 1 <= row_index <= len(record.scan.rows):
            raise ExpeditionListError("row index is out of range for that page")
        self.bind(record.scan.tab, record.offset + row_index - 1, expedition_id)
        ids = list(record.ids)
        ids[row_index - 1] = expedition_id
        self.pages[page] = PageRecord(
            page=record.page, scan=record.scan, offset=record.offset, ids=tuple(ids)
        )
        # Earlier pages may hold the same rows; resolve them too.
        for earlier in range(len(self.pages)):
            other = self.pages[earlier]
            if earlier == page:
                continue
            resolved = tuple(
                row_value
                if row_value is not None
                else self.resolve(other.scan.tab, other.offset + other_row.index - 1)
                for row_value, other_row in zip(other.ids, other.scan.rows)
            )
            if resolved != other.ids:
                self.pages[earlier] = PageRecord(
                    page=other.page, scan=other.scan, offset=other.offset, ids=resolved
                )

    @property
    def unbound_rows(self) -> tuple[tuple[int, int], ...]:
        """``(page, row)`` pairs that still need a number."""
        return tuple(
            (record.page, row)
            for record in self.pages
            for row in record.unbound_rows
        )

    def _resolved_positions(self) -> set[tuple[int | None, int]]:
        return set(self.bindings)

    def _positions(self) -> dict[tuple[int | None, int], list[tuple[RowSignature, bool]]]:
        """Every reading of every list position, flagged when it was an edge row.

        The viewport clips the first and last visible band, so the same row
        fingerprints differently at the page edge than mid-page - the client
        height changed from 32px to 24px for one unchanged row in a live scroll.
        A caller that compares readings must therefore know which ones were cut.
        """
        readings: dict[tuple[int | None, int], list[tuple[RowSignature, bool]]] = {}
        for record in self.pages:
            last = len(record.scan.rows)
            for row in record.scan.rows:
                clipped = row.index == 1 or row.index == last
                key = (record.scan.tab, record.offset + row.index - 1)
                readings.setdefault(key, []).append((row.signature, clipped))
        return readings

    def unbound_positions(self) -> tuple[tuple[int | None, int], ...]:
        """``(tab, position)`` pairs with no number yet, in list order.

        Positions rather than ``(page, row)`` pairs: a row visible on two pages
        is one position, so labelling it once resolves both pages.
        """
        if not self.pages:
            return ()
        pending = set(self._positions()) - self._resolved_positions()
        return tuple(sorted(pending, key=lambda key: (key[0] is None, key[0] or 0, key[1])))

    def label_positions(self, numbers: tuple[str, ...]) -> None:
        """Bind every unbound list position, in list order.

        This is the one-time labelling step for a freshly walked tab: the caller
        supplies the numbers of the rows that have no binding yet, in the order
        they appear on screen. The count must match exactly - a mismatch means
        the walk and the labels disagree about what is on the list, and guessing
        a mapping there would silently mislabel every later lookup.

        Readings of one position across pages must agree within tolerance; if
        they do not, the pages were aligned wrongly and nothing is bound. That
        check is about *alignment*, which is what the pixel evidence can still
        prove. Two neighbouring positions that happen to render identically are
        not a contradiction and are bound separately, because identity here is
        positional.
        """
        positions = self.unbound_positions()
        listing = list(numbers)
        if len(listing) != len(positions):
            raise ExpeditionListError(
                f"{len(listing)} numbers for {len(positions)} unbound positions "
                f"({', '.join(str(p[1]) for p in positions)})"
            )
        readings = self._positions()
        for key, number in zip(positions, listing):
            _validate_expedition_id(number)
            observed = readings[key]
            usable = [signature for signature, clipped in observed if not clipped]
            if not usable:
                # The row only ever appeared at a page edge (first or last row of
                # a short list); its own clipped fingerprint is all there is.
                usable = [signature for signature, _ in observed]
            reference = usable[0]
            for other in usable[1:]:
                if not reference.matches(other):
                    raise ExpeditionListError(
                        f"position {key[1]} was read inconsistently across pages"
                    )
            self.bind(key[0], key[1], number)
        for index, record in enumerate(self.pages):
            self.pages[index] = PageRecord(
                page=record.page,
                scan=record.scan,
                offset=record.offset,
                ids=tuple(
                    self.resolve(record.scan.tab, record.offset + row.index - 1)
                    for row in record.scan.rows
                ),
            )

    def entries(
        self, name_resolver: Callable[[str], str | None] | None = None
    ) -> tuple[ListEntry, ...]:
        """Merge the pages, de-duplicating by expedition number.

        A row that survives a scroll is seen more than once; the repeat must sit
        at the same list position, which is what proves the pages were aligned
        correctly. The latest page decides which entries are visible.

        ``name_resolver`` optionally maps a number to its name (``ExpeditionCatalog
        .name_of`` fits). It only decorates the result: identity still comes from
        the list positions, so a catalogue that lacks a number leaves ``name``
        unset rather than invalidating the row.
        """
        latest = self.pages[-1] if self.pages else None
        merged: dict[str, ListEntry] = {}
        positions: dict[str, tuple[int | None, int]] = {}
        for record in self.pages:
            for row, expedition_id in zip(record.scan.rows, record.ids):
                if expedition_id is None:
                    continue
                key = (record.scan.tab, record.offset + row.index - 1)
                known = positions.get(expedition_id)
                if known is not None and known != key:
                    raise ExpeditionListError(
                        f"{expedition_id} appeared at two different list positions"
                    )
                positions[expedition_id] = key
                visible = record is latest
                if expedition_id in merged and not visible:
                    continue
                merged[expedition_id] = ListEntry(
                    expedition_id=expedition_id,
                    tab=record.scan.tab,
                    visible=visible,
                    scroll_page=record.page,
                    center=row.click_point if visible else None,
                    name=(
                        None
                        if name_resolver is None
                        else name_resolver(expedition_id)
                    ),
                )
        return tuple(
            sorted(merged.values(), key=lambda entry: positions[entry.expedition_id][1])
        )

    def locate(
        self, expedition_id: str, scan: PageScan, *, offset: int
    ) -> Relocation:
        """Re-locate a target on a *fresh* scan; never reuse an old coordinate.

        ``offset`` is how many rows sit above the first row of ``scan`` on the
        full list - the number ``add_page`` derives from the alignment chain.
        It must come from that chain, not from a guess: with position-based
        identity the offset *is* the evidence, so a wrong offset would silently
        click the wrong expedition rather than fail.

        The point returned is measured on the page that was just captured, so a
        scroll between planning and clicking cannot leave a stale coordinate.
        When the bound position is not on the captured page the answer is
        ``not_visible``; when the position is on the page the row is the target
        by construction, with no pixel comparison involved.
        """
        matches = [
            position
            for (tab, position), bound in self.bindings.items()
            if bound == expedition_id and (scan.tab is None or tab == scan.tab)
        ]
        if not matches:
            return Relocation(
                "unbound", None, None, "no list position is bound to that number"
            )
        if len(matches) > 1:  # pragma: no cover - bind() forbids this
            return Relocation(
                "ambiguous",
                None,
                None,
                f"{len(matches)} list positions are bound to that number",
            )
        position = matches[0]
        row_index = position - offset
        if not 0 <= row_index < len(scan.rows):
            return Relocation(
                "not_visible",
                None,
                None,
                f"list position {position} is not on a page starting at {offset}",
            )
        row = scan.rows[row_index]
        return Relocation(
            "located",
            row.click_point,
            row.index,
            f"list position {position} on a page starting at {offset}",
        )

    def tab_order(self, tab: int | None) -> tuple[str, ...]:
        """Known numbers of one tab, in list order."""
        return tuple(
            expedition_id
            for _, expedition_id in sorted(
                (
                    (position, bound)
                    for (other_tab, position), bound in self.bindings.items()
                    if other_tab == tab
                ),
                key=lambda item: item[0],
            )
        )


def _validate_expedition_id(expedition_id: str) -> None:
    if not isinstance(expedition_id, str) or not expedition_id:
        raise ExpeditionListError("an expedition number must be a non-empty string")
    if len(expedition_id) > 4:
        raise ExpeditionListError("an expedition number is at most four characters")


def disp_no_sort_key(disp_no: str) -> tuple[int, int, int]:
    """Order display numbers the way the game lists them: 01..46, then A1.., B1..

    The master data, the community wiki table and the live screen all use this
    order, so it is used to *check* a labelling rather than to assume one.
    """
    text = disp_no.strip().upper()
    if not text:
        raise ValueError("disp_no must not be empty")
    if text.isdigit():
        return (0, 0, int(text))
    if len(text) == 2 and text[0].isalpha() and text[1].isdigit():
        return (1, ord(text[0]) - ord("A"), int(text[1]))
    raise ValueError(f"unrecognised display number: {disp_no!r}")


def check_display_order(disp_nos: tuple[str, ...]) -> str:
    """Compare the observed row order against the master/wiki order.

    ``disp_nos`` are the numbers of the identified rows *in screen order*. The
    master data, the community wiki table and the live screen all list 01..46
    before A1.. so a screen order that is not ascending by that key means a row
    was mislabelled, which is worth reporting even though nothing here depends
    on the order being right.
    """
    if len(disp_nos) < 2:
        return "unverified"
    try:
        keys = [disp_no_sort_key(value) for value in disp_nos]
    except ValueError:
        return "unverified"
    return "consistent" if keys == sorted(keys) else "violated"
