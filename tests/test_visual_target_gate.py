from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None

from kancollebot.adapters.poi.visual_target_gate import (
    VisualStateError,
    LocatedVisualTarget,
    locate_stable_template_target,
)


@unittest.skipIf(cv2 is None or np is None, "optional OpenCV/NumPy vision dependencies are unavailable")
class VisualTargetGateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.template_path = Path(self.temp_dir.name) / "notice.png"
        rng = np.random.default_rng(20261101)
        self.template = rng.integers(0, 255, size=(24, 72), dtype=np.uint8)
        ok, encoded = cv2.imencode(".png", self.template)
        self.assertTrue(ok)
        self.template_path.write_bytes(encoded.tobytes())
        self.image = rng.integers(0, 255, size=(200, 420), dtype=np.uint8)
        self.image[55:79, 250:322] = self.template
        ok, encoded = cv2.imencode(".png", self.image)
        self.payload = base64.b64encode(encoded.tobytes()).decode("ascii")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_two_frames_locate_one_stable_target(self):
        target = locate_stable_template_target(
            (self.payload, self.payload),
            self.template_path,
            region=(100, 20, 400, 160),
            minimum_score=0.99,
        )
        self.assertEqual((target.x, target.y), (286.0, 67.0))
        self.assertGreaterEqual(target.score, 0.99)
        self.assertEqual(target.frame_count, 2)
        self.assertIsInstance(target, LocatedVisualTarget)

    def test_duplicate_template_matches_fail_closed(self):
        duplicate = self.image.copy()
        duplicate[110:134, 150:222] = self.template
        ok, encoded = cv2.imencode(".png", duplicate)
        payload = base64.b64encode(encoded.tobytes()).decode("ascii")
        with self.assertRaisesRegex(VisualStateError, "multiple competing"):
            locate_stable_template_target(
                (payload, payload),
                self.template_path,
                region=(100, 20, 400, 160),
                minimum_score=0.99,
            )

    def test_target_drift_between_frames_fails_closed(self):
        moved = np.roll(self.image, 30, axis=1)
        ok, encoded = cv2.imencode(".png", moved)
        payload = base64.b64encode(encoded.tobytes()).decode("ascii")
        with self.assertRaisesRegex(VisualStateError, "moved"):
            locate_stable_template_target(
                (self.payload, payload),
                self.template_path,
                region=(100, 20, 400, 160),
                minimum_score=0.99,
                maximum_drift=8,
            )


if __name__ == "__main__":
    unittest.main()
