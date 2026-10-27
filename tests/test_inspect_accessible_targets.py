"""Offline tests for the read-only accessible-label inspector."""
from __future__ import annotations

import unittest

from tools.inspect_accessible_targets import matching_accessible_nodes


class AccessibleNodeFilterTests(unittest.TestCase):
    def test_filters_relevant_labels_and_deduplicates(self):
        nodes = [
            {"role": {"value": "button"}, "name": {"value": "Expedition"}, "ignored": False},
            {"role": {"value": "button"}, "name": {"value": "Expedition"}, "ignored": False},
            {"role": {"value": "button"}, "name": {"value": "Settings"}, "ignored": False},
            {"role": {"value": "button"}, "name": {"value": "遠征"}, "ignored": False},
        ]
        result = matching_accessible_nodes(nodes)
        self.assertEqual(result, (
            {"role": "button", "name": "Expedition"},
            {"role": "button", "name": "遠征"},
        ))

    def test_ignores_hidden_nodes_and_invalid_shapes(self):
        nodes = [
            {"role": {"value": "button"}, "name": {"value": "Expedition"}, "ignored": True},
            {"role": {"value": "button"}, "name": {"value": "Mission"}, "ignored": False},
            {"role": "button", "name": {"value": "Reward"}, "ignored": False},
            "not-an-object",
        ]
        self.assertEqual(matching_accessible_nodes(nodes), (
            {"role": "button", "name": "Mission"},
        ))

    def test_custom_keywords_are_case_insensitive(self):
        nodes = [
            {"role": {"value": "button"}, "name": {"value": "MISSION RESULT"}, "ignored": False},
            {"role": {"value": "button"}, "name": {"value": "Settings"}, "ignored": False},
        ]
        self.assertEqual(
            matching_accessible_nodes(nodes, keywords=("mission",)),
            ({"role": "button", "name": "MISSION RESULT"},),
        )

    def test_rejects_non_list_nodes(self):
        with self.assertRaisesRegex(ValueError, "must be a list"):
            matching_accessible_nodes({"nodes": []})


if __name__ == "__main__":
    unittest.main()
