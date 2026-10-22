import unittest
from datetime import datetime, timezone

from kancollebot.adapters.poi.expedition_state_v2 import (
    PoiStateError,
    normalize_poi_fleets,
)

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def normalize_one_fleet(mission):
    return normalize_poi_fleets(
        [{"api_id": 1, "api_ship": [101], "api_mission": mission}],
        {
            "101": {
                "api_ship_id": 11,
                "api_nowhp": 10,
                "api_maxhp": 10,
                "api_fuel": 10,
                "api_bull": 10,
            }
        },
        {"11": {"api_fuel": 10, "api_bull": 10}},
        observed_at=NOW,
    )


class PoiStateV2Tests(unittest.TestCase):
    def test_active_mission_timestamp_uses_epoch_milliseconds(self):
        timestamp_ms = 1792670400000
        state = normalize_one_fleet([1, 2, timestamp_ms, 0])
        self.assertEqual(state.payload[0].mission_id, 2)
        self.assertEqual(
            state.payload[0].return_at,
            datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc),
        )

    def test_active_mission_zero_timestamp_remains_unknown(self):
        state = normalize_one_fleet([1, 2, 0, 0])
        self.assertEqual(state.payload[0].mission_id, 2)
        self.assertIsNone(state.payload[0].return_at)

    def test_malformed_active_mission_timestamp_fails_closed(self):
        for value in (True, "1792670400000", 12345, -1, {}):
            with self.subTest(value=value):
                with self.assertRaises(PoiStateError):
                    normalize_one_fleet([1, 2, value, 0])

    def test_master_consumption_controls_supply_readiness(self):
        state = normalize_poi_fleets(
            [{"api_id": 1, "api_ship": [101], "api_mission": [0, 0, 0, 0]}],
            {
                "101": {
                    "api_ship_id": 11,
                    "api_nowhp": 10,
                    "api_maxhp": 10,
                    "api_fuel": 9,
                    "api_bull": 10,
                }
            },
            {"11": {"api_fuel": 10, "api_bull": 10}},
            observed_at=NOW,
        )
        self.assertFalse(state.payload[0].fuel_ready)
        self.assertTrue(state.payload[0].ammo_ready)


if __name__ == "__main__":
    unittest.main()
