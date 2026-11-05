from __future__ import annotations

import base64
import unittest

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None

from kancollebot.adapters.poi.visual_target import (
    VisualTargetError,
    checkbox_mark_pixel_count,
    locate_square_checkbox,
    locate_supply_fleet_tab,
    locate_fleet_supply_all_button,
    require_visual_dependencies,
)


@unittest.skipIf(cv2 is None or np is None, "optional vision dependencies are unavailable")
class VisualTargetTests(unittest.TestCase):
    @staticmethod
    def _frame(rectangles: tuple[tuple[int, int, int, int], ...]) -> str:
        image = np.full((720, 1200, 3), 190, dtype=np.uint8)
        for x, y, width, height in rectangles:
            cv2.rectangle(
                image,
                (x, y),
                (x + width - 1, y + height - 1),
                (45, 45, 45),
                -1,
            )
            cv2.rectangle(
                image,
                (x, y),
                (x + width - 1, y + height - 1),
                (25, 25, 25),
                2,
            )
        success, encoded = cv2.imencode(".png", image)
        if not success:
            raise AssertionError("synthetic PNG encoding failed")
        return base64.b64encode(encoded.tobytes()).decode("ascii")

    def test_locates_one_checkbox_inside_the_bounded_first_row_region(self):
        payload = self._frame(((166, 240, 22, 23),))
        point = locate_square_checkbox(payload)
        self.assertEqual(point.bounds, (164, 238, 25, 26))
        self.assertEqual((point.x, point.y), (176.5, 251.0))
        self.assertEqual(point.region, (150, 225, 55, 50))
        self.assertEqual(point.locator, "opencv-canny-square-v1")
        self.assertEqual(len(point.source_frame_sha256), 64)

    def test_locates_fleet2_ship_rows_two_through_four_in_independent_regions(self):
        rows = (
            ("fleet2-ship2-checkbox", 317, (150, 302, 55, 50)),
            ("fleet2-ship3-checkbox", 394, (150, 379, 55, 50)),
            ("fleet2-ship4-checkbox", 471, (150, 456, 55, 50)),
        )
        for label, y, region in rows:
            with self.subTest(label=label):
                payload = self._frame(((166, y, 22, 23),))
                point = locate_square_checkbox(payload, region=region, label=label)
                self.assertEqual(point.region, region)
                self.assertEqual(point.bounds, (164, y - 2, 25, 26))

    def test_locates_fleet3_checkbox_in_its_own_label_and_region(self):
        payload = self._frame(((166, 317, 22, 23),))
        point = locate_square_checkbox(
            payload, region=(150, 302, 55, 50), label="fleet3-ship2-checkbox"
        )
        self.assertEqual(point.bounds, (164, 315, 25, 26))
        self.assertEqual(point.label, "fleet3-ship2-checkbox")

    def test_locates_supply_fleet1_tab_and_merges_nested_contours(self):
        payload = self._frame(((203, 164, 34, 31), (212, 166, 25, 29)))
        point = locate_supply_fleet_tab(payload, fleet_index=1)
        self.assertEqual(point.bounds, (201, 162, 37, 34))
        self.assertEqual(point.region, (195, 155, 50, 50))
        self.assertEqual(point.label, "supply-fleet1-tab")

    def test_locates_supply_fleet3_tab_and_merges_nested_contours(self):
        payload = self._frame(((293, 164, 34, 31), (302, 166, 25, 29)))
        point = locate_supply_fleet_tab(payload, fleet_index=3)
        self.assertEqual(point.bounds, (291, 162, 37, 34))
        self.assertEqual(point.region, (285, 155, 50, 50))
        self.assertEqual(point.label, "supply-fleet3-tab")
        self.assertEqual(point.locator, "opencv-canny-tab-v1")

    def test_supply_fleet_tab_requires_one_target_and_valid_index(self):
        payload = self._frame(())
        with self.assertRaisesRegex(VisualTargetError, "found 0"):
            locate_supply_fleet_tab(payload, fleet_index=3)
        # Fleet 4 gained a bounded region on 2026-11-06 (the strip is evenly
        # spaced at 45 px), so an empty frame must fail on "no target", not on
        # the index. Fleet 5 does not exist and stays an index error.
        with self.assertRaisesRegex(VisualTargetError, "found 0"):
            locate_supply_fleet_tab(payload, fleet_index=4)
        with self.assertRaisesRegex(VisualTargetError, "index must be one of"):
            locate_supply_fleet_tab(payload, fleet_index=5)

    def test_supply_fleet4_tab_region_is_derived_from_the_45px_strip(self):
        payload = self._frame(((338, 164, 34, 31),))
        point = locate_supply_fleet_tab(payload, fleet_index=4)
        self.assertEqual(point.region, (330, 155, 50, 50))
        self.assertEqual(point.label, "supply-fleet4-tab")

    def test_locates_one_fleet_wide_supply_button(self):
        payload = self._frame(((1064, 606, 88, 26),))
        point = locate_fleet_supply_all_button(payload)
        self.assertEqual(point.label, "fleet-supply-all-button")
        self.assertEqual(point.region, (1050, 590, 145, 60))
        self.assertEqual(point.locator, "opencv-canny-button-v1")
        self.assertEqual(len(point.source_frame_sha256), 64)

    def test_fleet_wide_supply_button_locator_fails_closed_on_zero_and_merges_nested_edges(self):
        with self.assertRaisesRegex(VisualTargetError, "found 0"):
            locate_fleet_supply_all_button(self._frame(()))
        payload = self._frame(((1062, 604, 92, 30), (1066, 608, 84, 24)))
        point = locate_fleet_supply_all_button(payload)
        self.assertEqual(point.label, "fleet-supply-all-button")
        self.assertEqual(point.region, (1050, 590, 145, 60))

    def test_ambiguous_multiple_boxes_fail_closed(self):
        payload = self._frame(((156, 240, 22, 23), (181, 240, 22, 23)))
        with self.assertRaisesRegex(VisualTargetError, "found 2"):
            locate_square_checkbox(payload)

    def test_checkbox_mark_pixel_count_distinguishes_checked_and_unchecked(self):
        image = np.full((720, 1200, 3), 190, dtype=np.uint8)
        cv2.rectangle(image, (166, 240), (187, 262), (45, 45, 45), -1)
        cv2.rectangle(image, (166, 240), (187, 262), (25, 25, 25), 2)
        success, encoded = cv2.imencode(".png", image)
        self.assertTrue(success)
        unchecked = base64.b64encode(encoded.tobytes()).decode("ascii")
        unchecked_point = locate_square_checkbox(unchecked)
        self.assertLess(checkbox_mark_pixel_count(unchecked, unchecked_point), 20)

        cv2.line(image, (170, 252), (174, 256), (245, 245, 245), 2)
        cv2.line(image, (174, 256), (183, 246), (245, 245, 245), 2)
        success, encoded = cv2.imencode(".png", image)
        self.assertTrue(success)
        checked = base64.b64encode(encoded.tobytes()).decode("ascii")
        checked_point = locate_square_checkbox(checked)
        self.assertGreaterEqual(checkbox_mark_pixel_count(checked, checked_point), 20)

    def test_missing_box_fails_closed(self):
        payload = self._frame(())
        with self.assertRaisesRegex(VisualTargetError, "found 0"):
            locate_square_checkbox(payload)

    def test_out_of_viewport_region_is_rejected(self):
        payload = self._frame(())
        with self.assertRaisesRegex(VisualTargetError, "outside"):
            locate_square_checkbox(payload, region=(1190, 710, 20, 20))


@unittest.skipIf(cv2 is not None and np is not None, "vision dependencies are present")
class RequireVisualDependenciesMissingTests(unittest.TestCase):
    def test_fails_closed_with_actionable_message_when_dependencies_missing(self):
        with self.assertRaisesRegex(VisualTargetError, "opencv-python-headless"):
            require_visual_dependencies()
        with self.assertRaisesRegex(VisualTargetError, "numpy"):
            require_visual_dependencies()


@unittest.skipIf(cv2 is None or np is None, "optional vision dependencies are unavailable")
class RequireVisualDependenciesPresentTests(unittest.TestCase):
    def test_passes_when_dependencies_present(self):
        self.assertIsNone(require_visual_dependencies())


if __name__ == "__main__":
    unittest.main()
