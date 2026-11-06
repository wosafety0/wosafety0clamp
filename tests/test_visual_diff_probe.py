"""Offline tests for the visual difference probe using synthetic RGB arrays only."""
from __future__ import annotations
import unittest
try:
    import numpy as np
except ImportError:
    np = None
from tools.visual_diff_probe import compare_arrays

@unittest.skipIf(np is None, 'NumPy optional vision dependency is not installed')
class VisualDiffProbeTests(unittest.TestCase):
    def test_identical_1200x720_images_have_zero_difference(self):
        image = np.zeros((720, 1200, 3), dtype=np.uint8)
        result = compare_arrays(image, image.copy())
        self.assertEqual(result['changed_pixel_percent'], 0.0)
        self.assertEqual(result['absolute_mean_rgb_delta'], [0.0, 0.0, 0.0])

    def test_synthetic_glow_is_measured_only_in_selected_roi(self):
        before = np.zeros((720, 1200, 3), dtype=np.uint8)
        after = before.copy()
        after[100:120, 200:220] = (255, 120, 30)
        result = compare_arrays(before, after, roi=(190, 90, 40, 40))
        self.assertGreater(result['changed_pixel_percent'], 0.0)
        self.assertEqual(result['changed_pixel_percent'], 25.0)
        self.assertEqual(result['absolute_mean_rgb_delta'], [63.75, 30.0, 7.5])

    def test_rejects_roi_outside_image(self):
        image = np.zeros((720, 1200, 3), dtype=np.uint8)
        with self.assertRaisesRegex(ValueError, 'ROI'):
            compare_arrays(image, image, roi=(1190, 710, 20, 20))

if __name__ == '__main__':
    unittest.main()
