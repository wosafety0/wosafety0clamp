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


if __name__ == "__main__":
    unittest.main()
