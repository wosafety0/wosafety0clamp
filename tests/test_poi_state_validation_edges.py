"""Strict validation tests for raw Poi state values."""

import unittest
from datetime import datetime, timezone

from kancollebot.adapters.poi.expedition_state_v2 import (
    PoiStateError,
    normalize_poi_fleets,
)

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def normalize(*, ship=None, master=None, fleet=None):
    fleet = fleet or {"api_id": 2, "api_ship": [101], "api_mission": [0, 0, 0, 0]}
    ship = ship or {
        "api_ship_id": 11,
        "api_nowhp": 10,
        "api_maxhp": 10,
        "api_fuel": 10,
        "api_bull": 10,
    }
    master = master or {"api_fuel": 10, "api_bull": 10}
    return normalize_poi_fleets(
        [fleet], {"101": ship}, {"11": master}, observed_at=NOW
    )


class PoiStateValidationEdgeTests(unittest.TestCase):
    def test_boolean_max_hp_is_rejected(self):
        with self.assertRaisesRegex(PoiStateError, "HP fields"):
            normalize(ship={"api_ship_id": 11, "api_nowhp": 1, "api_maxhp": True,
                            "api_fuel": 10, "api_bull": 10})

    def test_current_hp_above_maximum_is_rejected(self):
        with self.assertRaisesRegex(PoiStateError, "HP fields"):
            normalize(ship={"api_ship_id": 11, "api_nowhp": 11, "api_maxhp": 10,
                            "api_fuel": 10, "api_bull": 10})

    def test_boolean_fuel_is_not_treated_as_valid_supply(self):
        state = normalize(ship={"api_ship_id": 11, "api_nowhp": 10, "api_maxhp": 10,
                                "api_fuel": True, "api_bull": 10})
        self.assertFalse(state.payload[0].fuel_ready)

    def test_boolean_master_consumption_is_not_treated_as_valid_supply(self):
        state = normalize(master={"api_fuel": True, "api_bull": 10})
        self.assertFalse(state.payload[0].fuel_ready)

    def test_empty_fleet_is_never_marked_supplied(self):
        state = normalize(fleet={"api_id": 2, "api_ship": [-1, -1, -1, -1, -1, -1],
                                 "api_mission": [0, 0, 0, 0]})
        self.assertEqual(state.payload[0].ship_ids, ())
        self.assertFalse(state.payload[0].fuel_ready)
        self.assertFalse(state.payload[0].ammo_ready)

    def test_missing_ship_record_fails_closed(self):
        with self.assertRaisesRegex(PoiStateError, "missing ship 101"):
            normalize_poi_fleets(
                [{"api_id": 2, "api_ship": [101], "api_mission": [0, 0, 0, 0]}],
                {},
                {"11": {"api_fuel": 10, "api_bull": 10}},
                observed_at=NOW,
            )


if __name__ == "__main__":
    unittest.main()
