from __future__ import annotations

import unittest

from kancollebot.adapters.poi.visual_state_gate import (
    VisualStateError,
    classify_template_scores,
)


class VisualStateGateTests(unittest.TestCase):
    def test_home_requires_a_strong_unique_match(self):
        self.assertEqual(classify_template_scores({
            "home_sortie_menu.png": 0.998,
            "expedition_result_success.png": 0.17,
            "expedition_result_failure.png": 0.17,
            "result_next_button.png": 0.52,
            "negative_home_other_menu.png": 0.88,
        }), "HOME_PORT")

    def test_negative_control_template_does_not_override_home_match(self):
        self.assertEqual(classify_template_scores({
            "home_sortie_menu.png": 0.998,
            "expedition_result_success.png": 0.17,
            "expedition_result_failure.png": 0.17,
            "result_next_button.png": 0.52,
            "negative_home_other_menu.png": 1.0,
        }), "HOME_PORT")
    def test_return_result_requires_one_result_variant(self):
        self.assertEqual(classify_template_scores({
            "home_sortie_menu.png": 0.20,
            "expedition_result_success.png": 0.96,
            "expedition_result_failure.png": 0.30,
            "result_next_button.png": 0.70,
        }), "RETURN_RESULT")

    def test_bonus_page_requires_next_marker_without_result_marker(self):
        self.assertEqual(classify_template_scores({
            "home_sortie_menu.png": 0.20,
            "expedition_result_success.png": 0.35,
            "expedition_result_failure.png": 0.28,
            "result_next_button.png": 0.97,
        }), "BONUS_ITEMS")

    def test_ambiguous_and_weak_matches_fail_closed(self):
        self.assertEqual(classify_template_scores({"home_sortie_menu.png": 0.80}), "UNKNOWN")
        self.assertEqual(classify_template_scores({
            "home_sortie_menu.png": 0.96,
            "expedition_result_success.png": 0.96,
        }), "UNKNOWN")
        self.assertEqual(classify_template_scores({
            "expedition_result_success.png": 0.95,
            "expedition_result_failure.png": 0.94,
        }), "UNKNOWN")

    def test_invalid_scores_are_rejected(self):
        with self.assertRaises(ValueError):
            classify_template_scores({"home_sortie_menu.png": float("nan")})
        with self.assertRaises(ValueError):
            classify_template_scores({}, minimum_score=0)


if __name__ == "__main__":
    unittest.main()
