"""Focused regression tests for observed Poi expedition mission states."""

import unittest
from datetime import datetime, timezone

from kancollebot.adapters.poi.expedition_state_v2 import (
    PoiStateError,
    normalize_poi_fleets,
)
from kancollebot.expeditions.planner import (
    ExpeditionDecision,
    evaluate_expedition_fleet,
)


OBSERVED_AT = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
SHIPS = {
    "101": {
        "api_nowhp": 10,
        "api_maxhp": 10,
        "api_ship_id": 1,
        "api_fuel": 10,
        "api_bull": 10,
    }
}
MASTERS = {"1": {"api_fuel_max": 10, "api_bull_max": 10}}


def normalize(mission):
    fleets = [{"api_id": 2, "api_ship": [101], "api_mission": mission}]
    return normalize_fleets(fleets).payload[0]


def normalize_fleets(fleets, ships=SHIPS):
    return normalize_poi_fleets(
        fleets, ships, MASTERS, observed_at=OBSERVED_AT
    )


class PoiMissionStateTests(unittest.TestCase):
    def test_idle_state_normalizes_without_mission(self):
        snapshot = normalize([0, 0, 0, 0])
        self.assertIsNone(snapshot.mission_id)
        self.assertIsNone(snapshot.return_at)

    def test_underway_state_retains_mission_and_blocks_dispatch(self):
        snapshot = normalize([1, 1, 1793011949741, 0])
        self.assertEqual(snapshot.mission_id, 1)
        self.assertIsNone(snapshot.return_at)
        plan = evaluate_expedition_fleet(snapshot, now=OBSERVED_AT)
        self.assertEqual(
            plan.decision, ExpeditionDecision.BLOCKED_UNKNOWN_RETURN_TIME
        )

    def test_completed_pending_reward_state_blocks_dispatch(self):
        snapshot = normalize([2, 1, 1793011949741, 0])
        self.assertEqual(snapshot.mission_id, 1)
        plan = evaluate_expedition_fleet(snapshot, now=OBSERVED_AT)
        self.assertEqual(
            plan.decision, ExpeditionDecision.BLOCKED_UNKNOWN_RETURN_TIME
        )

    def test_unknown_status_fails_closed(self):
        with self.assertRaises(PoiStateError):
            normalize([3, 1, 1793011949741, 0])

    def test_idle_state_with_nonzero_slots_fails_closed(self):
        with self.assertRaises(PoiStateError):
            normalize([0, 1, 1793011949741, 0])

    def test_active_state_without_timestamp_fails_closed(self):
        with self.assertRaises(PoiStateError):
            normalize([1, 1, 0, 0])

    def test_wrong_slot_count_fails_closed(self):
        with self.assertRaises(PoiStateError):
            normalize([0, 0, 0])

    def test_duplicate_fleet_ids_fail_closed(self):
        fleets = [
            {"api_id": 2, "api_ship": [101], "api_mission": [0, 0, 0, 0]},
            {"api_id": 2, "api_ship": [], "api_mission": [0, 0, 0, 0]},
        ]
        with self.assertRaisesRegex(PoiStateError, "duplicate fleet ID"):
            normalize_fleets(fleets)

    def test_ship_cannot_appear_in_multiple_fleets(self):
        fleets = [
            {"api_id": 1, "api_ship": [101], "api_mission": [0, 0, 0, 0]},
            {"api_id": 2, "api_ship": [101], "api_mission": [0, 0, 0, 0]},
        ]
        with self.assertRaisesRegex(PoiStateError, "appears in multiple fleets"):
            normalize_fleets(fleets)


if __name__ == "__main__":
    unittest.main()
