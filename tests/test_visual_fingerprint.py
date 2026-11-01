from __future__ import annotations

import base64
import struct
import unittest
import zlib

from kancollebot.adapters.poi.visual_fingerprint import (
    VisualFingerprintError,
    average_fingerprints,
    classify_screenshot_fingerprints,
    classify_fingerprints,
    compute_grid_fingerprint,
    fingerprint_similarity,
)


def _chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def _png_rgb(width: int, height: int, rows: list[bytes]) -> str:
    raw = b"".join(b"\x00" + row for row in rows)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )
    return base64.b64encode(payload).decode("ascii")


def _png_rgb_filtered(width: int, rows: list[bytes], filters: list[int]) -> str:
    previous = bytes(width * 3)
    scanlines = []
    for row, filter_type in zip(rows, filters):
        encoded = bytearray()
        for index, value in enumerate(row):
            left = row[index - 3] if index >= 3 else 0
            above = previous[index]
            upper_left = previous[index - 3] if index >= 3 else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            else:
                estimate = left + above - upper_left
                distances = (abs(estimate - left), abs(estimate - above), abs(estimate - upper_left))
                predictor = (left, above, upper_left)[distances.index(min(distances))]
            encoded.append((value - predictor) & 0xFF)
        scanlines.append(bytes((filter_type,)) + bytes(encoded))
        previous = row
    header = struct.pack(">IIBBBBB", width, len(rows), 8, 2, 0, 0, 0)
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(b"".join(scanlines)))
        + _chunk(b"IEND", b"")
    )
    return base64.b64encode(payload).decode("ascii")

class VisualFingerprintTests(unittest.TestCase):
    def setUp(self):
        self.image = _png_rgb(
            2,
            2,
            [
                bytes((255, 0, 0, 0, 255, 0)),
                bytes((0, 0, 255, 255, 255, 255)),
            ],
        )

    def test_grid_fingerprint_preserves_cell_rgb_averages(self):
        self.assertEqual(
            compute_grid_fingerprint(self.image, crop=(0, 0, 2, 2), grid=(2, 2)),
            (255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255),
        )

    def test_all_png_scanline_filters_decode_to_the_same_rgb_pixels(self):
        rows = [
            bytes((255, 0, 0, 0, 255, 0)),
            bytes((0, 0, 255, 255, 255, 255)),
            bytes((20, 40, 60, 80, 100, 120)),
            bytes((130, 140, 150, 160, 170, 180)),
            bytes((190, 200, 210, 220, 230, 240)),
        ]
        encoded = _png_rgb_filtered(2, rows, [0, 1, 2, 3, 4])
        expected = tuple(channel for row in rows for channel in row)
        self.assertEqual(
            compute_grid_fingerprint(encoded, crop=(0, 0, 2, 5), grid=(2, 5)),
            expected,
        )
    def test_crop_limits_descriptor_to_requested_region(self):
        self.assertEqual(
            compute_grid_fingerprint(self.image, crop=(1, 0, 1, 2), grid=(1, 2)),
            (0, 255, 0, 255, 255, 255),
        )

    def test_identical_and_opposite_fingerprints_score_correctly(self):
        first = (0, 0, 0)
        second = (255, 255, 255)
        self.assertEqual(fingerprint_similarity(first, first), 1.0)
        self.assertEqual(fingerprint_similarity(first, second), 0.0)

    def test_average_fingerprints_reduces_sample_noise(self):
        self.assertEqual(average_fingerprints(((10, 20), (20, 30), (30, 40))), (20, 30))

    def test_classifier_requires_threshold_and_clear_margin(self):
        current = (10, 20, 30)
        label, scores = classify_fingerprints(
            current,
            {"HOME_PORT": (10, 20, 30), "SORTIE_MENU": (200, 210, 220)},
        )
        self.assertEqual(label, "HOME_PORT")
        self.assertEqual(scores["HOME_PORT"], 1.0)

        label, _ = classify_fingerprints(
            current,
            {"HOME_PORT": (10, 20, 30), "SORTIE_MENU": (11, 21, 31)},
        )
        self.assertEqual(label, "UNKNOWN")

    def test_screenshot_classifier_uses_state_specific_crop_and_clear_margin(self):
        document = {
            "version": 1,
            "viewport": [2, 2],
            "states": {
                "HOME_PORT": {"crop": [0, 0, 2, 2], "grid": [2, 2],
                              "fingerprint": [255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255]},
                "SORTIE_MENU": {"crop": [0, 0, 2, 2], "grid": [2, 2],
                                "fingerprint": [0, 0, 0] * 4},
            },
        }
        label, scores = classify_screenshot_fingerprints(self.image, document)
        self.assertEqual(label, "HOME_PORT")
        self.assertEqual(scores["HOME_PORT"], 1.0)

    def test_screenshot_classifier_rejects_viewport_mismatch(self):
        with self.assertRaises(VisualFingerprintError):
            classify_screenshot_fingerprints(
                self.image,
                {"version": 1, "viewport": [1200, 720], "states": {}},
            )
    def test_invalid_payload_crop_grid_and_values_are_rejected(self):
        with self.assertRaises(VisualFingerprintError):
            compute_grid_fingerprint("not-base64", crop=(0, 0, 1, 1))
        with self.assertRaises(VisualFingerprintError):
            compute_grid_fingerprint(self.image, crop=(-1, 0, 1, 1))
        with self.assertRaises(VisualFingerprintError):
            compute_grid_fingerprint(self.image, crop=(0, 0, 2, 2), grid=(3, 1))
        with self.assertRaises(VisualFingerprintError):
            average_fingerprints(((1, 2), (3,)))
        with self.assertRaises(VisualFingerprintError):
            fingerprint_similarity((1, 2), (1, 2, 3))


if __name__ == "__main__":
    unittest.main()