from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kancollebot.adapters.poi.visual_state_gate import (
    _TEMPLATE_IMAGE_CACHE,
    _load_grayscale_template,
    classify_template_scores,
)


class VisualStateGateTests(unittest.TestCase):
    def test_home_requires_strong_sortie_and_navigation_markers(self):
        self.assertEqual(classify_template_scores({
            "home_sortie_menu.png": 0.998,
            "negative_home_other_menu.png": 1.0,
            "home_expedition_menu.png": 0.21,
            "expedition_list_panel.png": 0.63,
            "expedition_dispatch_button.png": 0.36,
            "expedition_result_success.png": 0.27,
            "expedition_result_failure.png": 0.17,
            "result_next_button.png": 0.53,
            "return_notice.png": 0.30,
            "bonus_title.png": 0.26,
        }), "HOME_PORT")

    def test_dynamic_home_reference_can_confirm_home_without_stale_button_template(self):
        self.assertEqual(classify_template_scores({
            "home_reference_similarity": 0.979,
            "home_sortie_menu.png": 0.22,
            "negative_home_other_menu.png": 1.0,
            "home_expedition_menu.png": 0.11,
            "expedition_list_panel.png": 0.25,
            "expedition_dispatch_button.png": 0.30,
            "expedition_result_success.png": 0.27,
            "expedition_result_failure.png": 0.17,
            "result_next_button.png": 0.53,
            "return_notice.png": 0.30,
            "bonus_title.png": 0.26,
        }), "HOME_PORT")

    def test_home_without_navigation_or_reference_marker_fails_closed(self):
        self.assertEqual(classify_template_scores({
            "home_sortie_menu.png": 0.998,
            "home_expedition_menu.png": 0.21,
            "expedition_list_panel.png": 0.63,
            "expedition_dispatch_button.png": 0.36,
        }), "UNKNOWN")

    def test_negative_control_template_does_not_override_home_match(self):
        self.assertEqual(classify_template_scores({
            "home_sortie_menu.png": 0.998,
            "negative_home_other_menu.png": 1.0,
            "expedition_result_success.png": 0.17,
            "expedition_result_failure.png": 0.17,
            "result_next_button.png": 0.52,
        }), "HOME_PORT")

    def test_return_result_requires_one_result_variant(self):
        self.assertEqual(classify_template_scores({
            "home_sortie_menu.png": 0.20,
            "expedition_result_success.png": 0.96,
            "expedition_result_failure.png": 0.30,
        }), "RETURN_RESULT")
        self.assertEqual(classify_template_scores({
            "home_sortie_menu.png": 0.20,
            "return_notice.png": 0.97,
            "expedition_result_success.png": 0.25,
            "expedition_result_failure.png": 0.24,
        }), "UNKNOWN")

    def test_live_home_port_return_notice_scores_classify_as_distinct_state(self):
        frame_scores = (
            {
                "home_sortie_menu.png": 0.99608,
                "negative_home_other_menu.png": 0.87841,
                "expedition_result_success.png": 0.16670,
                "expedition_result_failure.png": 0.16829,
                "home_expedition_menu.png": 0.20931,
                "expedition_list_panel.png": 0.38027,
                "expedition_dispatch_button.png": 0.35229,
                "return_notice.png": 0.98995,
                "bonus_title.png": 0.25640,
            },
            {
                "home_sortie_menu.png": 0.99608,
                "negative_home_other_menu.png": 0.87841,
                "expedition_result_success.png": 0.16743,
                "expedition_result_failure.png": 0.16412,
                "home_expedition_menu.png": 0.20765,
                "expedition_list_panel.png": 0.37734,
                "expedition_dispatch_button.png": 0.35688,
                "return_notice.png": 0.98995,
                "bonus_title.png": 0.25517,
            },
        )
        for scores in frame_scores:
            with self.subTest(scores=scores):
                self.assertEqual(classify_template_scores(scores), "HOME_PORT_RETURN_NOTICE")

    def test_return_notice_without_home_evidence_fails_closed(self):
        self.assertEqual(classify_template_scores({
            "return_notice.png": 0.99,
            "expedition_result_success.png": 0.16,
            "expedition_result_failure.png": 0.17,
            "bonus_title.png": 0.25,
        }), "UNKNOWN")

    def test_bonus_page_requires_the_verified_bonus_title_marker(self):
        self.assertEqual(classify_template_scores({
            "home_sortie_menu.png": 0.20,
            "expedition_result_success.png": 0.35,
            "expedition_result_failure.png": 0.28,
            "bonus_title.png": 0.98,
        }), "BONUS_ITEMS")

    def test_known_mismatched_next_button_asset_cannot_authorize_bonus_state(self):
        self.assertEqual(classify_template_scores({
            "home_sortie_menu.png": 0.20,
            "expedition_result_success.png": 0.35,
            "expedition_result_failure.png": 0.28,
            "result_next_button.png": 1.0,
        }), "UNKNOWN")

    def test_bonus_title_conflicting_with_return_notice_fails_closed(self):
        self.assertEqual(classify_template_scores({
            "home_sortie_menu.png": 0.20,
            "expedition_result_success.png": 0.35,
            "expedition_result_failure.png": 0.28,
            "return_notice.png": 0.99,
            "bonus_title.png": 0.99,
        }), "UNKNOWN")

    def test_result_marker_conflicting_with_sortie_title_fails_closed(self):
        self.assertEqual(classify_template_scores({
            "home_sortie_menu.png": 0.20,
            "expedition_result_success.png": 0.99,
            "expedition_result_failure.png": 0.20,
            "sortie_menu_title.png": 0.99,
        }), "UNKNOWN")

    def test_ambiguous_and_weak_matches_fail_closed(self):
        self.assertEqual(classify_template_scores({"home_sortie_menu.png": 0.80}), "UNKNOWN")
        self.assertEqual(classify_template_scores({
            "home_sortie_menu.png": 0.96,
            "negative_home_other_menu.png": 0.96,
            "expedition_result_success.png": 0.96,
        }), "UNKNOWN")
        self.assertEqual(classify_template_scores({
            "expedition_result_success.png": 0.95,
            "expedition_result_failure.png": 0.94,
        }), "UNKNOWN")

    def test_sortie_menu_requires_the_live_title_marker(self):
        self.assertEqual(classify_template_scores({
            "sortie_menu_title.png": 0.997,
            "home_sortie_menu.png": 0.59,
            "negative_home_other_menu.png": 1.0,
            "home_expedition_menu.png": 0.997,
            "expedition_list_panel.png": 0.684,
            "expedition_dispatch_button.png": 0.43,
            "expedition_result_success.png": 0.18,
            "expedition_result_failure.png": 0.12,
            "return_notice.png": 0.29,
            "bonus_title.png": 0.29,
        }), "SORTIE_MENU")

    def test_sortie_menu_weak_or_conflicting_title_fails_closed(self):
        self.assertEqual(classify_template_scores({
            "sortie_menu_title.png": 0.80,
            "home_expedition_menu.png": 0.997,
        }), "UNKNOWN")
        self.assertEqual(classify_template_scores({
            "sortie_menu_title.png": 0.997,
            "home_sortie_menu.png": 0.998,
            "negative_home_other_menu.png": 1.0,
            "expedition_result_success.png": 0.18,
            "expedition_result_failure.png": 0.12,
        }), "UNKNOWN")

    def test_bundled_sortie_menu_template_is_present(self):
        template = (
            Path(__file__).resolve().parents[1]
            / "src" / "kancollebot" / "adapters" / "poi"
            / "assets" / "sortie_menu_title.png"
        )
        self.assertTrue(template.is_file())
        self.assertGreater(template.stat().st_size, 100)

    def test_invalid_scores_are_rejected(self):
        with self.assertRaises(ValueError):
            classify_template_scores({"home_sortie_menu.png": float("nan")})
        with self.assertRaises(ValueError):
            classify_template_scores({}, minimum_score=0)


class TemplateImageCacheTests(unittest.TestCase):
    def setUp(self):
        _TEMPLATE_IMAGE_CACHE.clear()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "template.bin"
        self.path.write_bytes(b"first")

        class FakeNumpy:
            uint8 = "uint8"

            @staticmethod
            def frombuffer(value, dtype):
                return value

        class FakeCv2:
            IMREAD_GRAYSCALE = 0

            @staticmethod
            def imdecode(value, mode):
                return (value, mode)

        self.numpy = FakeNumpy()
        self.cv2 = FakeCv2()

    def tearDown(self):
        _TEMPLATE_IMAGE_CACHE.clear()
        self.temp_dir.cleanup()

    def test_unchanged_template_is_read_and_decoded_once(self):
        original_read_bytes = Path.read_bytes
        calls = []

        def counted_read_bytes(path):
            calls.append(path)
            return original_read_bytes(path)

        from unittest.mock import patch
        with patch.object(Path, "read_bytes", counted_read_bytes):
            first = _load_grayscale_template(self.path, self.cv2, self.numpy)
            second = _load_grayscale_template(self.path, self.cv2, self.numpy)

        self.assertIs(first, second)
        self.assertEqual(len(calls), 1)

    def test_template_file_change_invalidates_cached_image(self):
        original_read_bytes = Path.read_bytes
        calls = []

        def counted_read_bytes(path):
            calls.append(path)
            return original_read_bytes(path)

        from unittest.mock import patch
        with patch.object(Path, "read_bytes", counted_read_bytes):
            first = _load_grayscale_template(self.path, self.cv2, self.numpy)
            self.path.write_bytes(b"changed-content")
            second = _load_grayscale_template(self.path, self.cv2, self.numpy)

        self.assertNotEqual(first, second)
        self.assertEqual(len(calls), 2)




if __name__ == "__main__":
    unittest.main()
