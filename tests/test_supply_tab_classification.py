"""P0-1: template-free supply-tab identification for Fleet 4.

No ``SUPPLY_FLEET_4_SELECTED`` fingerprint exists, so the selected tab is
identified relative to its neighbours. These tests drive that classifier with
synthetic 1200x720 frames: it must name the bright tab, and must answer
UNKNOWN - never a guess - when the picture is dark, flat or ambiguous.

What these tests do NOT prove: that the game's selected-tab style matches the
thresholds on the real client. That needs a live capture and is recorded as
unverified in the progress document.
"""
from __future__ import annotations

import base64
import struct
import unittest
import zlib

from kancollebot.adapters.poi.visual_target import (
    SUPPLY_FLEET_TAB_REGIONS,
    SUPPLY_TAB_FLEET_IDS,
    VisualTargetError,
    classify_supply_tab_selection,
)

WIDTH, HEIGHT = 1200, 720
BASE_GREY = 40
SELECTED_GREY = 210


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


_FRAME_CACHE: dict[tuple, str] = {}


def frame(
    *,
    selected: int | None,
    selected_grey: int = SELECTED_GREY,
    base_grey: int = BASE_GREY,
    width: int = WIDTH,
    height: int = HEIGHT,
) -> str:
    """A flat grey frame with one tab painted brighter, as a base64 PNG."""
    key = (selected, selected_grey, base_grey, width, height)
    if key in _FRAME_CACHE:
        return _FRAME_CACHE[key]
    pixels = bytearray()
    tab_region = SUPPLY_FLEET_TAB_REGIONS.get(selected) if selected else None
    for y in range(height):
        pixels.append(0)  # filter type: none
        for x in range(width):
            grey = base_grey
            if tab_region is not None:
                rx, ry, rw, rh = tab_region
                if rx <= x < rx + rw and ry <= y < ry + rh:
                    grey = selected_grey
            pixels.extend((grey, grey, grey, 255))
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(pixels), 6))
        + _chunk(b"IEND", b"")
    )
    encoded = base64.b64encode(payload).decode("ascii")
    _FRAME_CACHE[key] = encoded
    return encoded


class SupplyTabSelectionTests(unittest.TestCase):
    def test_fleet4_selected_is_named(self):
        state, scores = classify_supply_tab_selection(frame(selected=4))
        self.assertEqual(state, "SUPPLY_FLEET_4_SELECTED")
        self.assertGreater(scores[4], scores[2])

    def test_fleet2_selected_is_not_reported_as_fleet4(self):
        state, _scores = classify_supply_tab_selection(frame(selected=2))
        self.assertEqual(state, "SUPPLY_FLEET_2_SELECTED")
        self.assertNotEqual(state, "SUPPLY_FLEET_4_SELECTED")

    def test_a_flat_frame_is_unknown(self):
        state, scores = classify_supply_tab_selection(
            frame(selected=4, selected_grey=BASE_GREY)
        )
        self.assertEqual(state, "UNKNOWN")
        self.assertEqual(set(scores), set(SUPPLY_TAB_FLEET_IDS))

    def test_a_marginal_difference_is_unknown(self):
        # Brighter than the neighbours, but not by the calibrated margin.
        state, _scores = classify_supply_tab_selection(
            frame(selected=4, selected_grey=BASE_GREY + 5)
        )
        self.assertEqual(state, "UNKNOWN")

    def test_a_dark_frame_has_no_selected_tab(self):
        state, _scores = classify_supply_tab_selection(
            frame(selected=4, selected_grey=60, base_grey=30)
        )
        self.assertEqual(state, "UNKNOWN")

    def test_the_classifier_can_be_restricted_to_one_fleet(self):
        state, scores = classify_supply_tab_selection(
            frame(selected=4), fleet_ids=(2, 4)
        )
        self.assertEqual(state, "SUPPLY_FLEET_4_SELECTED")
        self.assertEqual(set(scores), {2, 4})

    def test_a_wrong_viewport_is_refused(self):
        with self.assertRaises(VisualTargetError):
            classify_supply_tab_selection(frame(selected=4, width=800, height=600))

    def test_a_payload_that_is_not_a_png_is_refused(self):
        with self.assertRaises(VisualTargetError):
            classify_supply_tab_selection("not base64 at all")
        with self.assertRaises(VisualTargetError):
            classify_supply_tab_selection(base64.b64encode(b"nope").decode("ascii"))

    def test_an_unknown_fleet_index_is_refused(self):
        with self.assertRaises(ValueError):
            classify_supply_tab_selection(frame(selected=4), fleet_ids=(4, 5))
        with self.assertRaises(ValueError):
            classify_supply_tab_selection(frame(selected=4), fleet_ids=())

    def test_every_fleet_has_a_bounded_tab_region(self):
        self.assertEqual(SUPPLY_TAB_FLEET_IDS, (1, 2, 3, 4))
        for fleet_id in SUPPLY_TAB_FLEET_IDS:
            rx, ry, rw, rh = SUPPLY_FLEET_TAB_REGIONS[fleet_id]
            self.assertGreater(rw, 0)
            self.assertGreater(rh, 0)
            self.assertLessEqual(rx + rw, WIDTH)
            self.assertLessEqual(ry + rh, HEIGHT)
        # The strip is evenly spaced; Fleet 4's region is derived, not measured.
        self.assertEqual(
            SUPPLY_FLEET_TAB_REGIONS[4][0] - SUPPLY_FLEET_TAB_REGIONS[3][0], 45
        )


if __name__ == "__main__":
    unittest.main()
