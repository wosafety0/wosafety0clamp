"""Offline tests for switching area tabs inside the expedition-list walker.

The walker is the only tool in this repository that presses anything, so the
tab click needs a receipt rather than a hope. The receipt is the tab strip
itself: the selected tab is the one painting its label orange, so after the
click the label has to sit on the requested cell. These tests drive
``Walker.select_tab`` against a fake CDP session whose screenshots are
synthetic 1200x720 frames with only the strip painted - no rows are needed,
because selecting a tab must not depend on what the list happens to contain.

Nothing here touches a browser: the fake session answers
``Page.captureScreenshot`` with bytes built by the project's own PNG encoder and
records every click instead of sending it.
"""
from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from kancollebot.calibration import encode_png
from kancollebot.calibration.expedition_list import (
    DEFAULT_TAB_COUNT,
    DEFAULT_TAB_STRIP_PITCH,
    DEFAULT_TAB_STRIP_WIDTH,
    DEFAULT_TAB_STRIP_X0,
    DEFAULT_TAB_STRIP_Y,
)
from kancollebot.calibration.png import RgbImage
from tools.page_through_expedition_list import Walker

WHITE = (255, 255, 255)
PLATE = (128, 150, 139)
ORANGE = (200, 90, 40)

WIDTH = 1200
HEIGHT = 720


def _blank() -> RgbImage:
    return RgbImage(WIDTH, HEIGHT, 3, bytes(WHITE) * (WIDTH * HEIGHT))


def _fill(image: RgbImage, left: int, top: int, right: int, bottom: int, color) -> RgbImage:
    pixels = bytearray(image.pixels)
    for y in range(top, bottom):
        for x in range(left, right):
            base = (y * image.width + x) * 3
            pixels[base:base + 3] = bytes(color)
    return RgbImage(image.width, image.height, image.channels, bytes(pixels))


def _strip(active: int | None) -> RgbImage:
    """A frame whose only feature is the area tab strip."""
    image = _blank()
    for index in range(DEFAULT_TAB_COUNT):
        left = DEFAULT_TAB_STRIP_X0 + DEFAULT_TAB_STRIP_PITCH * index
        color = ORANGE if index == active else PLATE
        image = _fill(
            image, left, DEFAULT_TAB_STRIP_Y[0], left + DEFAULT_TAB_STRIP_WIDTH,
            DEFAULT_TAB_STRIP_Y[1], color,
        )
    return image


class _FakeTransport:
    def __init__(self, images: list[RgbImage], directory: Path) -> None:
        self._payloads = [self._encode(image, directory, index)
                          for index, image in enumerate(images)]
        self.calls: list[tuple[str, object]] = []

    @staticmethod
    def _encode(image: RgbImage, directory: Path, index: int) -> str:
        path = directory / f"frame_{index}.png"
        encode_png(path, image)
        return base64.b64encode(path.read_bytes()).decode("ascii")

    def send(self, method: str, params=None):
        self.calls.append((method, params))
        if not self._payloads:
            raise AssertionError("the walker captured more frames than the test provided")
        return {"data": self._payloads.pop(0)}


class _FakeInput:
    def __init__(self) -> None:
        self.clicks: list[object] = []

    def click(self, point) -> None:
        self.clicks.append(point)


class _FakeSession:
    def __init__(self, images: list[RgbImage], directory: Path) -> None:
        self.transport = _FakeTransport(images, directory)
        self.input = _FakeInput()


class TabSwitchTests(unittest.TestCase):
    def _walker(self, directory: Path, images: list[RgbImage]) -> Walker:
        session = _FakeSession(images, directory)
        self.session = session
        return Walker(session, settle_seconds=0.0, frames=directory)

    def test_click_selects_the_requested_tab(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            walker = self._walker(directory, [_strip(0), _strip(3)])
            before = walker.capture("before")
            outcome, after = walker.select_tab(before, 3)

            self.assertEqual(outcome, "selected")
            self.assertIsNotNone(after)
            self.assertEqual(after.tab, 3, "the receipt is the orange label, not the click")
            self.assertEqual(walker.clicks, 1)

            clicks = self.session.input.clicks
            self.assertEqual(len(clicks), 1)
            expected_center = before.tabs.centers[3]
            self.assertEqual((clicks[0].x, clicks[0].y), expected_center)
            self.assertEqual(clicks[0].label, "expedition-tab-4")

    def test_already_selected_tab_is_not_clicked(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            walker = self._walker(directory, [_strip(3)])
            before = walker.capture("before")
            outcome, after = walker.select_tab(before, 3)

            self.assertEqual(outcome, "already_selected")
            self.assertIs(after, before, "an already-selected tab must not be re-captured")
            self.assertEqual(walker.clicks, 0)
            self.assertEqual(self.session.input.clicks, [])

    def test_click_that_changes_nothing_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            walker = self._walker(directory, [_strip(0), _strip(0)])
            before = walker.capture("before")
            outcome, after = walker.select_tab(before, 3)

            self.assertEqual(outcome, "tab_not_selected")
            self.assertIsNone(after)
            self.assertEqual(walker.clicks, 1, "the click was sent, it just did not land")

    def test_unreadable_selection_blocks_instead_of_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            walker = self._walker(directory, [_strip(0), _strip(None)])
            before = walker.capture("before")
            outcome, after = walker.select_tab(before, 3)

            self.assertEqual(outcome, "tab_unreadable")
            self.assertIsNone(after)

    def test_out_of_range_target_sends_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            for target in (-1, 7, 99):
                walker = self._walker(directory, [_strip(0)])
                before = walker.capture("before")
                outcome, after = walker.select_tab(before, target)
                self.assertEqual(outcome, "target_out_of_range")
                self.assertIsNone(after)
                self.assertEqual(walker.clicks, 0)
                self.assertEqual(self.session.input.clicks, [])

    def test_click_point_is_the_cell_centre_of_the_measured_strip(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            walker = self._walker(directory, [_strip(0), _strip(6)])
            before = walker.capture("before")
            walker.select_tab(before, 6)

            point = self.session.input.clicks[0]
            left = DEFAULT_TAB_STRIP_X0 + DEFAULT_TAB_STRIP_PITCH * 6
            self.assertEqual(point.x, (left + left + DEFAULT_TAB_STRIP_WIDTH) // 2)
            self.assertEqual(
                point.y,
                (DEFAULT_TAB_STRIP_Y[0] + DEFAULT_TAB_STRIP_Y[1]) // 2,
            )


if __name__ == "__main__":
    unittest.main()
