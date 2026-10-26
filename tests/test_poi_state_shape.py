"""Offline synthetic tests for Poi collection boundaries."""

import unittest

from kancollebot.adapters.poi.state_shape import PoiStateShapeError, validate_poi_collections


class PoiStateShapeTests(unittest.TestCase):
    def test_accepts_matching_positive_reference(self):
        snapshot = validate_poi_collections(
            [{"api_ship": [7, -1, 0]}], {"7": {"api_id": 7}}
        )
        self.assertEqual(len(snapshot.fleets), 1)

    def test_rejects_missing_ship(self):
        with self.assertRaises(PoiStateShapeError):
            validate_poi_collections([{"api_ship": [7]}], {})

    def test_rejects_mismatched_ship_id(self):
        with self.assertRaises(PoiStateShapeError):
            validate_poi_collections([{"api_ship": [7]}], {"7": {"api_id": 8}})

    def test_rejects_wrong_container_shapes(self):
        with self.assertRaises(PoiStateShapeError):
            validate_poi_collections({}, [])
            
    def test_rejects_boolean_reference(self):
        with self.assertRaises(PoiStateShapeError):
            validate_poi_collections([{"api_ship": [True]}], {})


if __name__ == "__main__":
    unittest.main()
