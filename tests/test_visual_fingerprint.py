from __future__ import annotations

import base64
import struct
import unittest
import zlib
from pathlib import Path

from kancollebot.adapters.poi.visual_fingerprint import (
    VisualFingerprintError,
    average_fingerprints,
    classify_screenshot_fingerprints,
    classify_supply_checkbox_state,
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
    def test_capture_tool_supports_supply_menu_checkpoint(self):
        from tools.capture_ooi_visual_fingerprint import SUPPORTED_STATES

        self.assertIn("SUPPLY_MENU", SUPPORTED_STATES)
        self.assertIn("SUPPLY_FLEET_1_SELECTED", SUPPORTED_STATES)
        self.assertIn("SUPPLY_FLEET_2_SELECTED", SUPPORTED_STATES)
        self.assertIn("SUPPLY_FLEET_3_SELECTED", SUPPORTED_STATES)
        self.assertIn("SUPPLY_FLEET_2_SHIP_1_CHECKED", SUPPORTED_STATES)
        self.assertIn("SUPPLY_FLEET_2_SHIP_1_UNCHECKED", SUPPORTED_STATES)

    def test_live_supply_menu_fingerprints_use_verified_crops(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "src" / "kancollebot" / "adapters" / "poi"
            / "ooi_1200x720_fingerprints.json"
        )
        import json

        document = json.loads(path.read_text(encoding="utf-8"))
        menu = document["states"]["SUPPLY_MENU"]
        self.assertEqual(menu["crop"], [150, 105, 1050, 60])
        self.assertEqual(menu["grid"], [24, 4])
        self.assertEqual(menu["samples"], 3)
        self.assertEqual(len(menu["fingerprint"]), 24 * 4 * 3)
        self.assertEqual(menu["label_source"], "manually_confirmed_current_screen")

        for state, crop in (
            ("SUPPLY_FLEET_1_SELECTED", [203, 155, 30, 40]),
            ("SUPPLY_FLEET_2_SELECTED", [249, 155, 30, 40]),
            ("SUPPLY_FLEET_3_SELECTED", [295, 155, 30, 40]),
        ):
            with self.subTest(state=state):
                descriptor = document["states"][state]
                self.assertEqual(descriptor["crop"], crop)
                self.assertEqual(descriptor["grid"], [10, 8])
                self.assertEqual(descriptor["samples"], 3)
                self.assertEqual(len(descriptor["fingerprint"]), 10 * 8 * 3)
                self.assertEqual(descriptor["label_source"], "manually_confirmed_current_screen")

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

    def test_supply_fleet1_child_state_ignores_only_its_parent_menu_for_margin(self):
        current = [255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255]
        parent = current.copy()
        parent[0] = 254
        document = {
            "version": 1,
            "viewport": [2, 2],
            "states": {
                "SUPPLY_MENU": {
                    "crop": [0, 0, 2, 2], "grid": [2, 2], "fingerprint": parent,
                },
                "SUPPLY_FLEET_1_SELECTED": {
                    "crop": [0, 0, 2, 2], "grid": [2, 2], "fingerprint": current,
                },
                "HOME_PORT": {
                    "crop": [0, 0, 2, 2], "grid": [2, 2], "fingerprint": [0, 0, 0] * 4,
                },
            },
        }
        label, scores = classify_screenshot_fingerprints(self.image, document)
        self.assertEqual(label, "SUPPLY_FLEET_1_SELECTED")
        self.assertGreater(scores["SUPPLY_MENU"], 0.99)

    def test_supply_fleet_child_state_ignores_only_its_parent_menu_for_margin(self):
        current = [255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255]
        parent = current.copy()
        parent[0] = 254
        document = {
            "version": 1,
            "viewport": [2, 2],
            "states": {
                "SUPPLY_MENU": {
                    "crop": [0, 0, 2, 2], "grid": [2, 2], "fingerprint": parent,
                },
                "SUPPLY_FLEET_2_SELECTED": {
                    "crop": [0, 0, 2, 2], "grid": [2, 2], "fingerprint": current,
                },
                "HOME_PORT": {
                    "crop": [0, 0, 2, 2], "grid": [2, 2], "fingerprint": [0, 0, 0] * 4,
                },
            },
        }
        label, scores = classify_screenshot_fingerprints(self.image, document)
        self.assertEqual(label, "SUPPLY_FLEET_2_SELECTED")
        self.assertGreater(scores["SUPPLY_MENU"], 0.99)

    def test_supply_checkbox_classifier_is_parent_scoped_and_distinguishes_states(self):
        current = compute_grid_fingerprint(
            self.image, crop=(0, 0, 2, 2), grid=(2, 2)
        )
        blank = [0] * len(current)
        parent = {
            "version": 1,
            "viewport": [2, 2],
            "states": {
                "SUPPLY_FLEET_2_SELECTED": {
                    "crop": [0, 0, 2, 2], "grid": [2, 2],
                    "fingerprint": list(current),
                },
            },
        }
        checkbox = {
            "version": 1,
            "viewport": [2, 2],
            "states": {
                "SUPPLY_FLEET_2_SHIP_1_CHECKED": {
                    "crop": [0, 0, 2, 2], "grid": [2, 2],
                    "fingerprint": list(current),
                },
                "SUPPLY_FLEET_2_SHIP_1_UNCHECKED": {
                    "crop": [0, 0, 2, 2], "grid": [2, 2],
                    "fingerprint": blank,
                },
            },
        }
        label, scores = classify_supply_checkbox_state(self.image, parent, checkbox)
        self.assertEqual(label, "SUPPLY_FLEET_2_SHIP_1_CHECKED")
        self.assertEqual(scores["parent:SUPPLY_FLEET_2_SELECTED"], 1.0)

        reversed_checkbox = {
            **checkbox,
            "states": {
                "SUPPLY_FLEET_2_SHIP_1_CHECKED": checkbox["states"]["SUPPLY_FLEET_2_SHIP_1_UNCHECKED"],
                "SUPPLY_FLEET_2_SHIP_1_UNCHECKED": checkbox["states"]["SUPPLY_FLEET_2_SHIP_1_CHECKED"],
            },
        }
        label, _ = classify_supply_checkbox_state(self.image, parent, reversed_checkbox)
        self.assertEqual(label, "SUPPLY_FLEET_2_SHIP_1_UNCHECKED")

        wrong_parent = {
            "version": 1,
            "viewport": [2, 2],
            "states": {
                "SUPPLY_FLEET_3_SELECTED": {
                    "crop": [0, 0, 2, 2], "grid": [2, 2],
                    "fingerprint": list(current),
                },
            },
        }
        label, _ = classify_supply_checkbox_state(self.image, wrong_parent, checkbox)
        self.assertEqual(label, "SUPPLY_FLEET_3_SELECTED")

        ambiguous_checkbox = {
            **checkbox,
            "states": {
                "SUPPLY_FLEET_2_SHIP_1_CHECKED": checkbox["states"]["SUPPLY_FLEET_2_SHIP_1_CHECKED"],
                "SUPPLY_FLEET_2_SHIP_1_UNCHECKED": checkbox["states"]["SUPPLY_FLEET_2_SHIP_1_CHECKED"],
            },
        }
        label, _ = classify_supply_checkbox_state(self.image, parent, ambiguous_checkbox)
        self.assertEqual(label, "UNKNOWN")

    def test_supply_checkbox_classifier_selects_the_requested_ship_only(self):
        current = compute_grid_fingerprint(
            self.image, crop=(0, 0, 2, 2), grid=(2, 2)
        )
        blank = [0] * len(current)
        parent = {
            "version": 1, "viewport": [2, 2],
            "states": {
                "SUPPLY_FLEET_2_SELECTED": {
                    "crop": [0, 0, 2, 2], "grid": [2, 2],
                    "fingerprint": list(current),
                },
            },
        }
        for ship_index in (2, 3, 4):
            checked_name = f"SUPPLY_FLEET_2_SHIP_{ship_index}_CHECKED"
            unchecked_name = f"SUPPLY_FLEET_2_SHIP_{ship_index}_UNCHECKED"
            checkbox = {
                "version": 1, "viewport": [2, 2],
                "states": {
                    checked_name: {
                        "crop": [0, 0, 2, 2], "grid": [2, 2],
                        "fingerprint": list(current),
                    },
                    unchecked_name: {
                        "crop": [0, 0, 2, 2], "grid": [2, 2],
                        "fingerprint": blank,
                    },
                },
            }
            label, _ = classify_supply_checkbox_state(
                self.image, parent, checkbox, ship_index=ship_index
            )
            self.assertEqual(label, checked_name)
        with self.assertRaises(ValueError):
            classify_supply_checkbox_state(self.image, parent, checkbox, ship_index=5)

    def test_supply_checkbox_classifier_supports_fleet3_parent_and_children(self):
        current = compute_grid_fingerprint(self.image, crop=(0, 0, 2, 2), grid=(2, 2))
        blank = [0] * len(current)
        parent = {
            "version": 1, "viewport": [2, 2],
            "states": {
                "SUPPLY_FLEET_3_SELECTED": {
                    "crop": [0, 0, 2, 2], "grid": [2, 2],
                    "fingerprint": list(current),
                },
            },
        }
        for ship_index in (1, 2, 3, 4):
            checked_name = f"SUPPLY_FLEET_3_SHIP_{ship_index}_CHECKED"
            unchecked_name = f"SUPPLY_FLEET_3_SHIP_{ship_index}_UNCHECKED"
            checkbox = {
                "version": 1, "viewport": [2, 2],
                "states": {
                    checked_name: {
                        "crop": [0, 0, 2, 2], "grid": [2, 2],
                        "fingerprint": list(current),
                    },
                    unchecked_name: {
                        "crop": [0, 0, 2, 2], "grid": [2, 2],
                        "fingerprint": blank,
                    },
                },
            }
            label, _ = classify_supply_checkbox_state(
                self.image, parent, checkbox, fleet_index=3, ship_index=ship_index
            )
            self.assertEqual(label, checked_name)

    def test_live_supply_checkbox_fingerprints_are_compact_and_distinct(self):
        import json

        path = (
            Path(__file__).resolve().parents[1]
            / "src" / "kancollebot" / "adapters" / "poi"
            / "supply_checkbox_fingerprints.json"
        )
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(document["states"]), 16)
        for fleet_index in (2, 3):
            for ship_index in (1, 2, 3, 4):
                checked = document["states"][f"SUPPLY_FLEET_{fleet_index}_SHIP_{ship_index}_CHECKED"]
                unchecked = document["states"][f"SUPPLY_FLEET_{fleet_index}_SHIP_{ship_index}_UNCHECKED"]
                expected_crop = [164, 238 + (ship_index - 1) * 77, 26, 26]
                for descriptor in (checked, unchecked):
                    self.assertEqual(descriptor["crop"], expected_crop)
                    self.assertEqual(descriptor["grid"], [13, 13])
                    self.assertEqual(descriptor["samples"], 3)
                    self.assertEqual(len(descriptor["fingerprint"]), 13 * 13 * 3)
                    self.assertIn(
                        descriptor["label_source"],
                        {"manually_confirmed_current_screen", "live_checkbox_mark_gate"},
                    )
                self.assertLess(
                    fingerprint_similarity(checked["fingerprint"], unchecked["fingerprint"]),
                    0.97,
                )

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


class CanonicalVariantMergeTests(unittest.TestCase):
    """Variant state scores merge into their canonical state before ranking.

    Live 2026-11-06 (L4 soak): the bonus-items content crop is
    expedition-specific, so per-expedition variants (BONUS_ITEMS_M1...)
    carry the same-crop fingerprints and merge into BONUS_ITEMS (max). The
    variant never answers by its own name, and a low variant score must not
    raise the canonical one on a *different* page.
    """

    def setUp(self):
        self.image = _png_rgb(
            2,
            2,
            [
                bytes((255, 0, 0, 0, 255, 0)),
                bytes((0, 0, 255, 255, 255, 255)),
            ],
        )
        main = [255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255]
        self.document = {
            "version": 1,
            "viewport": [2, 2],
            "states": {
                "BONUS_ITEMS": {"crop": [0, 0, 2, 2], "grid": [2, 2],
                                "fingerprint": main},
                "BONUS_ITEMS_M1": {"crop": [0, 0, 2, 2], "grid": [2, 2],
                                   "fingerprint": main},
                "HOME_PORT": {"crop": [0, 0, 2, 2], "grid": [2, 2],
                              "fingerprint": [0, 0, 0] * 4},
            },
        }

    def test_a_variant_match_answers_with_the_canonical_name(self):
        label, scores = classify_screenshot_fingerprints(self.image, self.document)
        self.assertEqual(label, "BONUS_ITEMS")
        self.assertEqual(scores["BONUS_ITEMS"], 1.0)
        self.assertIn("BONUS_ITEMS_M1", scores)

    def test_two_variants_both_merge_and_neither_answers_alone(self):
        # Live 2026-11-06 (L4 soak, expedition 2): a second per-expedition
        # variant joins the registry; the canonical answer must be reachable
        # through either variant and no variant name may win the ranking.
        self.document["states"]["BONUS_ITEMS_M2"] = dict(
            self.document["states"]["BONUS_ITEMS_M1"]
        )
        label, scores = classify_screenshot_fingerprints(self.image, self.document)
        self.assertEqual(label, "BONUS_ITEMS")
        self.assertEqual(scores["BONUS_ITEMS"], 1.0)
        self.assertGreaterEqual(scores["BONUS_ITEMS_M1"], 0.0)
        self.assertGreaterEqual(scores["BONUS_ITEMS_M2"], 0.0)
        # Suppress the canonical template itself so only the variant can
        # match; the merged canonical score must still carry the answer.
        self.document["states"]["BONUS_ITEMS"]["fingerprint"] = [0] * 12
        label, scores = classify_screenshot_fingerprints(self.image, self.document)
        self.assertEqual(label, "BONUS_ITEMS")
        self.assertEqual(scores["BONUS_ITEMS"], 1.0)

    def test_a_low_variant_score_never_lowers_or_wins_the_ranking(self):
        variant = self.document["states"]["BONUS_ITEMS_M1"]["fingerprint"]
        # The variant template matches a *different* page worse than the
        # canonical one does; the merged canonical score must stay the max
        # of the two, and the variant itself must not appear as the label.
        self.document["states"]["BONUS_ITEMS_M1"]["fingerprint"] = [
            min(value, 30) for value in variant
        ]
        label, scores = classify_screenshot_fingerprints(self.image, self.document)
        self.assertEqual(label, "BONUS_ITEMS")
        self.assertEqual(scores["BONUS_ITEMS"], 1.0)
        self.assertLess(scores["BONUS_ITEMS_M1"], scores["BONUS_ITEMS"])

    def test_a_variant_only_document_with_no_canonical_is_left_alone(self):
        # No canonical state present: the merge is a no-op and the variant
        # still answers by its own name (no silent re-labelling).
        document = {
            "version": 1,
            "viewport": [2, 2],
            "states": {
                "BONUS_ITEMS_M1": dict(self.document["states"]["BONUS_ITEMS_M1"]),
            },
        }
        label, _scores = classify_screenshot_fingerprints(self.image, document)
        self.assertEqual(label, "BONUS_ITEMS_M1")


if __name__ == "__main__":
    unittest.main()