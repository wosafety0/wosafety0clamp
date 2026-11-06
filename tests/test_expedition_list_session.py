"""Offline tests for following the live list to a known row.

The point of these is the offset chain, not the pixels: a fake list whose page
window moves on every scroll click lets the test assert exactly how many steps
were taken, which offset was proven, and which point came back. The fake refuses
to invent a row, so a session that guessed would fail here.
"""
from __future__ import annotations

import unittest

from kancollebot.calibration import (
    DEFAULT_SCROLL_DOWN_BOX,
    DEFAULT_SCROLL_UP_BOX,
    ExpeditionListIndex,
    ExpeditionListSession,
    ExpeditionRowError,
    RowLocation,
    decode_png,
    encode_png,
    scan_page,
)
from kancollebot.calibration.expedition_list import PageScan
from kancollebot.calibration.png import RgbImage

WIDTH = 1200
HEIGHT = 720
ROW_TOP = 250
ROW_PITCH = 45
ROW_HEIGHT = 23
STATUS_LEFT = 160
STATUS_RIGHT = 232
SIGNATURE_LEFT = 233
TAB_X0 = 176
TAB_PITCH = 60
TAB_WIDTH = 52
TAB_Y = (666, 692)

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
ORANGE = (255, 140, 0)
PLATE = (60, 60, 60)
GOLD = (255, 215, 0)


def _blank() -> RgbImage:
    return RgbImage(WIDTH, HEIGHT, 3, bytes(WHITE) * (WIDTH * HEIGHT))


def _fill(image: RgbImage, left: int, top: int, right: int, bottom: int, color) -> RgbImage:
    pixels = bytearray(image.pixels)
    for y in range(top, bottom):
        for x in range(left, right):
            base = (y * image.width + x) * 3
            pixels[base:base + 3] = bytes(color)
    return RgbImage(image.width, image.height, image.channels, bytes(pixels))


def _draw_row(image: RgbImage, slot: int, marker: int) -> RgbImage:
    top = ROW_TOP + ROW_PITCH * slot
    image = _fill(image, STATUS_LEFT, top, STATUS_RIGHT, top + ROW_HEIGHT, BLACK)
    left = SIGNATURE_LEFT + 32 * (marker % 14)
    return _fill(image, left, top, left + 64, top + ROW_HEIGHT, BLACK)


def _draw_tabs(image: RgbImage, active: int, *, cells: int = 7) -> RgbImage:
    for index in range(cells):
        left = TAB_X0 + TAB_PITCH * index
        image = _fill(
            image, left, TAB_Y[0], left + TAB_WIDTH, TAB_Y[1],
            ORANGE if index == active else PLATE,
        )
    return image


def _frame(markers: list[int], *, up: bool, down: bool, active_tab: int = 0) -> RgbImage:
    image = _blank()
    for slot, marker in enumerate(markers):
        image = _draw_row(image, slot, marker)
    image = _draw_tabs(image, active_tab)
    if up:
        image = _fill(image, *DEFAULT_SCROLL_UP_BOX, GOLD)
    if down:
        image = _fill(image, *DEFAULT_SCROLL_DOWN_BOX, GOLD)
    return image


class FakeList:
    """A list of N rows shown eight at a time, driven by the clicks it receives."""

    def __init__(self, count: int, *, offset: int = 0, tab: int = 0, capacity: int = 8) -> None:
        self.markers = list(range(count))
        self.offset = offset
        self.tab = tab
        self.capacity = capacity
        self.clicks: list[tuple[tuple[int, int], str]] = []
        self.fail_clicks = 0
        self.tab_after_scroll: int | None = None

    def scan(self) -> PageScan:
        visible = self.markers[self.offset : self.offset + self.capacity]
        return scan_page(
            _frame(
                visible,
                up=self.offset > 0,
                down=self.offset + self.capacity < len(self.markers),
                active_tab=self.tab,
            )
        )

    def click(self, point: tuple[int, int], label: str) -> None:
        self.clicks.append((point, label))
        if self.fail_clicks:
            self.fail_clicks -= 1
            return
        if label.endswith("-up"):
            self.offset = max(0, self.offset - 1)
            if self.tab_after_scroll is not None:
                self.tab = self.tab_after_scroll
        elif label.endswith("-down"):
            self.offset = min(max(0, len(self.markers) - self.capacity), self.offset + 1)
            if self.tab_after_scroll is not None:
                self.tab = self.tab_after_scroll
        elif label.startswith("expedition-tab-"):
            self.tab = int(label.rsplit("-", 1)[1]) - 1


def _index(entries: dict[int, str], *, tab: int = 0) -> ExpeditionListIndex:
    index = ExpeditionListIndex()
    for position, number in entries.items():
        index.bind(tab, position, number)
    return index


class LocateOnFirstPageTests(unittest.TestCase):
    def test_a_row_on_the_first_page_needs_no_scroll(self) -> None:
        fake = FakeList(9)
        session = ExpeditionListSession(scan=fake.scan, click=fake.click, index=_index({2: "03"}))
        location = session.locate("03")
        self.assertIsInstance(location, RowLocation)
        self.assertEqual(location.position, 2)
        self.assertEqual(location.offset, 0)
        self.assertEqual(location.row_index, 3)
        self.assertEqual(location.scroll_clicks, 0)
        self.assertEqual(fake.clicks, [], "nothing should be clicked to find the third row")

    def test_the_point_is_the_measured_row_centre(self) -> None:
        fake = FakeList(9)
        session = ExpeditionListSession(scan=fake.scan, click=fake.click, index=_index({0: "01"}))
        location = session.locate("01")
        self.assertEqual(location.point[0], 350)
        self.assertEqual(
            round(location.point[1]), ROW_TOP + ROW_HEIGHT // 2
        )


class LocateBelowThePageTests(unittest.TestCase):
    def test_a_row_past_the_eighth_scrolls_once_and_is_still_correct(self) -> None:
        # The 西方海域 case: position 8 is the ninth row, one page down.
        fake = FakeList(9)
        session = ExpeditionListSession(scan=fake.scan, click=fake.click, index=_index({8: "D1"}))
        location = session.locate("D1")
        self.assertEqual(location.position, 8)
        self.assertEqual(location.offset, 1)
        self.assertEqual(location.row_index, 8)
        self.assertEqual(location.scroll_clicks, 1)
        self.assertEqual(len(fake.clicks), 1)
        self.assertEqual(fake.clicks[0][1], "expedition-list-down")
        self.assertAlmostEqual(
            location.point[1], ROW_TOP + ROW_PITCH * 7 + ROW_HEIGHT / 2, delta=1.5
        )

    def test_measured_scroll_click_emits_attempt_and_sent_events(self) -> None:
        fake = FakeList(9)
        events = []
        session = ExpeditionListSession(
            scan=fake.scan,
            click=fake.click,
            index=_index({8: "D1"}),
            event_sink=lambda name, fields: events.append((name, dict(fields))),
        )

        session.locate("D1")

        names = [name for name, _ in events]
        self.assertEqual(
            names[:2],
            ["expedition_list_click_attempted", "expedition_list_click_sent"],
        )
        self.assertEqual(events[0][1]["target"], "expedition-list-down")
        self.assertEqual(
            events[0][1]["point"],
            {"x": fake.clicks[0][0][0], "y": fake.clicks[0][0][1]},
        )
        self.assertEqual(fake.clicks, [((1126, 651), "expedition-list-down")])

    def test_failed_pre_click_audit_does_not_send_the_measured_click(self) -> None:
        fake = FakeList(9)

        def fail_attempt(event, fields):
            if event == "expedition_list_click_attempted":
                raise OSError("audit log unavailable")

        session = ExpeditionListSession(
            scan=fake.scan,
            click=fake.click,
            index=_index({8: "D1"}),
            event_sink=fail_attempt,
        )

        with self.assertRaisesRegex(OSError, "audit log unavailable"):
            session.locate("D1")

        self.assertEqual(fake.clicks, [])

    def test_a_row_far_down_scrolls_one_row_at_a_time(self) -> None:
        # A scroll click moves the list by one row, so position 17 needs ten of
        # them to fall inside a page starting at offset 10.
        fake = FakeList(20)
        session = ExpeditionListSession(scan=fake.scan, click=fake.click, index=_index({17: "17"}))
        location = session.locate("17")
        self.assertEqual(location.position, 17)
        self.assertEqual(location.offset, 10)
        self.assertEqual(location.row_index, 8)
        self.assertEqual(location.scroll_clicks, 10)

    def test_a_row_that_does_not_exist_stops_without_clicking_it(self) -> None:
        fake = FakeList(8)
        session = ExpeditionListSession(scan=fake.scan, click=fake.click, index=_index({30: "30"}))
        with self.assertRaisesRegex(ExpeditionRowError, "not reachable"):
            session.locate("30")
        self.assertTrue(all(label.endswith("-down") or label.endswith("-up") for _, label in fake.clicks))


class ClimbToTopTests(unittest.TestCase):
    def test_a_scrolled_list_is_climbed_before_measuring(self) -> None:
        fake = FakeList(20, offset=6)
        session = ExpeditionListSession(scan=fake.scan, click=fake.click, index=_index({8: "D1"}))
        location = session.locate("D1")
        # Six up-clicks to reach the top, then one down to reach position 8.
        up = [label for _, label in fake.clicks if label.endswith("-up")]
        down = [label for _, label in fake.clicks if label.endswith("-down")]
        self.assertEqual(len(up), 6)
        self.assertEqual(len(down), 1)
        self.assertEqual(location.offset, 1, "the offset must be counted from the top, not the start")

    def test_an_unprovable_top_is_refused(self) -> None:
        class Tiny:
            def scan(self) -> PageScan:
                # No scroll boxes inside the capture: scroll_probed is False.
                return scan_page(_blank())

            def click(self, point, label) -> None:  # pragma: no cover - must not run
                raise AssertionError("nothing should be clicked when the top is unknown")

        session = ExpeditionListSession(
            scan=Tiny().scan, click=Tiny().click, index=_index({0: "01"})
        )
        with self.assertRaisesRegex(ExpeditionRowError, "too small|empty"):
            session.locate("01")


class TabTests(unittest.TestCase):
    def test_a_tab_that_is_already_selected_is_not_clicked(self) -> None:
        fake = FakeList(8, tab=0)
        session = ExpeditionListSession(scan=fake.scan, click=fake.click, index=_index({0: "01"}))
        location = session.locate("01", tab=0)
        self.assertEqual(location.tab_clicks, 0)
        self.assertEqual(fake.clicks, [])

    def test_another_tab_is_selected_and_verified(self) -> None:
        fake = FakeList(9, tab=4)
        session = ExpeditionListSession(
            scan=fake.scan, click=fake.click, index=_index({8: "D1"}, tab=4)
        )
        location = session.locate("D1", tab=4)
        self.assertEqual(location.tab_clicks, 0, "tab 4 is already selected")
        self.assertEqual(fake.clicks[-1][1], "expedition-list-down")

    def test_switching_a_tab_costs_one_click(self) -> None:
        fake = FakeList(9, tab=4)
        session = ExpeditionListSession(scan=fake.scan, click=fake.click, index=_index({0: "01"}))
        location = session.locate("01", tab=0)
        self.assertEqual(location.tab_clicks, 1)
        self.assertEqual(fake.clicks[0][1], "expedition-tab-1")
        self.assertEqual(location.tab, 0)

    def test_an_out_of_range_tab_sends_nothing(self) -> None:
        fake = FakeList(9, tab=0)
        session = ExpeditionListSession(scan=fake.scan, click=fake.click, index=_index({0: "01"}))
        with self.assertRaisesRegex(ExpeditionRowError, "outside"):
            session.locate("01", tab=9)
        self.assertEqual(fake.clicks, [])


class LocateAnyTabTests(unittest.TestCase):
    """The list reopens on whichever tab was last used; the index knows better."""

    def test_the_tab_comes_from_the_index_not_from_the_open_list(self) -> None:
        fake = FakeList(9, tab=4)
        session = ExpeditionListSession(
            scan=fake.scan, click=fake.click, index=_index({8: "D1"}, tab=1)
        )
        location = session.locate_any_tab("D1")
        self.assertEqual(location.tab, 1)
        self.assertEqual(location.position, 8)
        self.assertEqual(fake.clicks[0][1], "expedition-tab-2")
        self.assertEqual(location.tab_clicks, 1)

    def test_a_number_bound_on_no_tab_is_refused_before_any_click(self) -> None:
        fake = FakeList(9, tab=4)
        session = ExpeditionListSession(
            scan=fake.scan, click=fake.click, index=_index({0: "01"}, tab=1)
        )
        with self.assertRaisesRegex(ExpeditionRowError, "no area tab is bound to 26"):
            session.locate_any_tab("26")
        self.assertEqual(fake.clicks, [])

    def test_the_index_reports_the_tab_it_bound(self) -> None:
        index = ExpeditionListIndex()
        index.bind(3, 2, "D1")
        self.assertEqual(index.tab_of("D1"), 3)
        self.assertIsNone(index.tab_of("26"))
        index.bind(0, 2, "D1")
        self.assertIsNone(index.tab_of("D1"), "two tabs for one number is not an answer")


class RefusalTests(unittest.TestCase):
    def test_an_unbound_number_is_refused_before_any_click(self) -> None:
        fake = FakeList(9)
        session = ExpeditionListSession(scan=fake.scan, click=fake.click, index=_index({0: "01"}))
        with self.assertRaisesRegex(ExpeditionRowError, "no list position is bound"):
            session.locate("A5")
        self.assertEqual(fake.clicks, [])

    def test_a_click_that_does_not_move_the_list_is_refused(self) -> None:
        fake = FakeList(20)
        fake.fail_clicks = 1
        session = ExpeditionListSession(scan=fake.scan, click=fake.click, index=_index({17: "17"}))
        with self.assertRaisesRegex(ExpeditionRowError, "cannot place the scrolled page"):
            session.locate("17")

    def test_a_tab_change_mid_scroll_is_refused(self) -> None:
        fake = FakeList(20)
        fake.tab_after_scroll = 3
        session = ExpeditionListSession(scan=fake.scan, click=fake.click, index=_index({17: "17"}))
        with self.assertRaisesRegex(ExpeditionRowError, "area tab changed"):
            session.locate("17")

    def test_a_position_bound_on_another_tab_is_not_found(self) -> None:
        fake = FakeList(9, tab=0)
        session = ExpeditionListSession(scan=fake.scan, click=fake.click, index=_index({0: "01"}, tab=3))
        with self.assertRaisesRegex(ExpeditionRowError, "no list position is bound"):
            session.locate("01")

    def test_construction_validates_its_inputs(self) -> None:
        with self.assertRaises(ValueError):
            ExpeditionListSession(scan=None, click=lambda p, l: None, index=ExpeditionListIndex())
        with self.assertRaises(ValueError):
            ExpeditionListSession(scan=lambda: None, click=None, index=ExpeditionListIndex())
        with self.assertRaises(ValueError):
            ExpeditionListSession(scan=lambda: None, click=lambda p, l: None, index={})
        with self.assertRaises(ValueError):
            ExpeditionListSession(
                scan=lambda: None, click=lambda p, l: None,
                index=ExpeditionListIndex(), max_scroll_steps=0,
            )


class FrameHelpersTests(unittest.TestCase):
    """The fake list must be a real capture, or the tests would prove nothing."""

    def test_a_top_page_reports_no_up_control(self) -> None:
        scan = scan_page(_frame([0, 1, 2], up=False, down=True))
        self.assertTrue(scan.scroll_probed, "both control boxes fit, so absence is an answer")
        self.assertEqual(len(scan.rows), 3)
        self.assertIsNone(scan.scroll_up)
        self.assertIsNotNone(scan.scroll_down)

    def test_a_scrolled_frame_reports_both_controls(self) -> None:
        scan = scan_page(_frame([0, 1, 2], up=True, down=True))
        self.assertIsNotNone(scan.scroll_up)
        self.assertIsNotNone(scan.scroll_down)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
