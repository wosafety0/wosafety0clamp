"""Tests for the api_start2 / api_deck_num minimum-ships table (M1)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kancollebot.expeditions.mission_catalogue import (
    DEFAULT_CATALOGUE_PATH,
    load_mission_catalogue,
    minimum_ships_for_mission,
)


class LoadMissionCatalogueTests(unittest.TestCase):
    def test_default_shipped_table_loads_and_maps_ids_to_counts(self):
        catalogue = load_mission_catalogue(DEFAULT_CATALOGUE_PATH)
        self.assertIsInstance(catalogue, dict)
        self.assertEqual(catalogue[1], 2)
        self.assertEqual(catalogue[5], 4)
        self.assertEqual(catalogue[7], 6)
        # Ids are normalized to int keys.
        self.assertIn(142, catalogue)
        self.assertNotIn("5", catalogue)

    def test_flat_shape_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flat.json"
            path.write_text(json.dumps({"5": 4, "9": 3}), encoding="utf-8")
            catalogue = load_mission_catalogue(path)
        self.assertEqual(catalogue, {5: 4, 9: 3})

    def test_flat_shape_rejects_boolean_float_and_string_counts(self):
        for value in (True, 4.0, "4"):
            with self.subTest(value=value):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "flat.json"
                    path.write_text(json.dumps({"5": value}), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_mission_catalogue(path)

    def test_api_start2_shape_rejects_non_object_items_and_entries(self):
        invalid_documents = (
            {"items": []},
            {"items": {"5": 4}},
            {"items": {"5": {"api_deck_num": 4.0}}},
        )
        for document in invalid_documents:
            with self.subTest(document=document):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "bad.json"
                    path.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_mission_catalogue(path)

    def test_invalid_or_duplicate_normalized_mission_ids_are_rejected(self):
        invalid_documents = (
            {"0": 2},
            {"-1": 2},
            {"mission-5": 4},
            {"1": 2, "01": 3},
        )
        for document in invalid_documents:
            with self.subTest(document=document):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "bad.json"
                    path.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_mission_catalogue(path)

    def test_api_start2_object_map_shape_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "map.json"
            path.write_text(
                json.dumps({"items": {"5": {"api_id": 5, "api_deck_num": 4}}}),
                encoding="utf-8",
            )
            catalogue = load_mission_catalogue(path)
        self.assertEqual(catalogue, {5: 4})

    def test_missing_file_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            load_mission_catalogue(Path(tempfile.gettempdir()) / "does_not_exist_9f3a.json")

    def test_non_integer_api_deck_num_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps({"items": {"5": {"api_deck_num": "4"}}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_mission_catalogue(path)

    def test_zero_or_over_range_api_deck_num_is_rejected(self):
        for value in (0, 7, 99):
            with self.subTest(value=value):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "bad.json"
                    path.write_text(
                        json.dumps({"items": {"5": {"api_deck_num": value}}}),
                        encoding="utf-8",
                    )
                    with self.assertRaises(ValueError):
                        load_mission_catalogue(path)

    def test_boolean_api_deck_num_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(
                json.dumps({"items": {"5": {"api_deck_num": True}}}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_mission_catalogue(path)


class MinimumShipsForMissionTests(unittest.TestCase):
    def test_lookup_returns_value(self):
        self.assertEqual(minimum_ships_for_mission({5: 4, 9: 3}, 9), 3)

    def test_missing_id_returns_none(self):
        self.assertIsNone(minimum_ships_for_mission({5: 4}, 9))

    def test_none_catalogue_returns_none(self):
        self.assertIsNone(minimum_ships_for_mission(None, 5))


if __name__ == "__main__":
    unittest.main()
