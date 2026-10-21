"""Offline contract tests for conservative Poi state normalization."""

import unittest
from datetime import datetime, timezone

from kancollebot.adapters.poi.expedition_state import (
    PoiStateError,
    normalize_poi_fleets,
)


NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


class PoiFleetNormalizerTests(unittest.TestCase):
    def test_normalizes_fleet_and_marks_unknown_supply_not_ready(self):
        state = normalize_poi_fleets(
            [{"api_id": 1, "api_ship": [101, 102, -1, -1, -1, -1], "api_mission": [0, 0, 0, 0]}],
            {
                "101": {"api_nowhp": 10, "api_maxhp": 10, "api_fuel": 10, "api_bull": 10},
                "102": {"api_nowhp": 20, "api_maxhp": 20, "api_fuel": 20, "api_bull": 20},
            },
            observed_at=NOW,
        )
        self.assertEqual(state.payload[0].fleet_id, 1)
        self.assertEqual(state.payload[0].ship_ids, (101, 102))
        self.assertFalse(state.payload[0].fuel_ready)
        self.assertFalse(state.payload[0].ammo_ready)
        self.assertEqual(state.source, "poi-redux:info.fleets+info.ships")

    def test_damage_blocks_from_current_and_max_hp(self):
        state = normalize_poi_fleets(
            [{"api_id": 1, "api_ship": [101], "api_mission": [0, 0, 0, 0]}],
            {"101": {"api_nowhp": 9, "api_maxhp": 10}},
            observed_at=NOW,
        )
        self.assertEqual(state.payload[0].damaged_ship_ids, (101,))

    def test_active_mission_with_return_timestamp_is_converted_to_utc(self):
        timestamp = int(NOW.timestamp()) + 60
        state = normalize_poi_fleets(
            [{"api_id": 1, "api_ship": [101], "api_mission": [1, 2, 3, timestamp]}],
            {"101": {"api_nowhp": 10, "api_maxhp": 10}},
            observed_at=NOW,
        )
        self.assertEqual(state.payload[0].mission_id, 3)
        self.assertEqual(int(state.payload[0].return_at.timestamp()), timestamp)

    def test_active_mission_without_valid_timestamp_remains_blocked(self):
        state = normalize_poi_fleets(
            [{"api_id": 1, "api_ship": [101], "api_mission": [1, 2, 3, 0]}],
            {"101": {"api_nowhp": 10, "api_maxhp": 10}},
            observed_at=NOW,
        )
        self.assertEqual(state.payload[0].mission_id, 3)
        self.assertIsNone(state.payload[0].return_at)

    def test_missing_ship_record_fails_closed(self):
        with self.assertRaisesRegex(PoiStateError, "missing ship"):
            normalize_poi_fleets(
                [{"api_id": 1, "api_ship": [101], "api_mission": [0, 0, 0, 0]}],
                {},
                observed_at=NOW,
            )

    def test_unknown_mission_shape_fails_closed(self):
        with self.assertRaisesRegex(PoiStateError, "unknown shape"):
            normalize_poi_fleets(
                [{"api_id": 1, "api_ship": [], "api_mission": [1]}],
                {},
                observed_at=NOW,
            )

    def test_rejects_naive_observation_time(self):
        with self.assertRaisesRegex(PoiStateError, "timezone-aware"):
            normalize_poi_fleets([], {}, observed_at=datetime(2026, 10, 22, 12, 0))


if __name__ == "__main__":
    unittest.main()
