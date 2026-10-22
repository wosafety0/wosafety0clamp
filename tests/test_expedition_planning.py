import unittest

from kancollebot.core.expedition_planner import plan_next_expedition
from kancollebot.core.fleet_parser import parse_fleet_payload


class FleetParserTests(unittest.TestCase):
    def test_parses_top_level_list_and_preserves_slots(self):
        fleets = parse_fleet_payload([
            {"api_id": 2, "api_name": "第2艦隊", "api_ship": [1, 3, -1, -1, -1, -1],
             "api_mission": [0, 0, 0, 0]},
        ])
        self.assertEqual(len(fleets), 1)
        self.assertEqual(fleets[0].ship_slots, (1, 3, -1, -1, -1, -1))
        self.assertEqual(fleets[0].occupied_ship_ids, (1, 3))
        self.assertEqual(fleets[0].mission_state_text, "未派遣")

    def test_parses_data_wrapped_list(self):
        fleets = parse_fleet_payload({"data": [
            {"api_id": 1, "api_ship": [4], "api_mission": [1, 5, 123456, 0]}
        ]})
        self.assertEqual(fleets[0].mission_task_id, 5)
        self.assertEqual(fleets[0].mission_completion_time, 123456)

    def test_missing_mission_is_unknown_not_idle(self):
        fleet = parse_fleet_payload([{"api_id": 2, "api_ship": [1]}])[0]
        self.assertIsNone(fleet.mission_state_code)
        self.assertEqual(fleet.mission_state_text, "未知")

    def test_rejects_non_list_payload(self):
        with self.assertRaises(ValueError):
            parse_fleet_payload({"unexpected": []})

    def test_rejects_ship_after_empty_slot(self):
        with self.assertRaisesRegex(ValueError, "follows an empty slot"):
            parse_fleet_payload([
                {"api_id": 2, "api_ship": [-1, 3], "api_mission": [0]}
            ])

    def test_rejects_duplicate_ship_ids(self):
        with self.assertRaisesRegex(ValueError, "duplicate ship id"):
            parse_fleet_payload([
                {"api_id": 2, "api_ship": [3, 3], "api_mission": [0]}
            ])


class ExpeditionPlannerTests(unittest.TestCase):
    def setUp(self):
        self.fleets = parse_fleet_payload([
            {"api_id": 2, "api_name": "第2艦隊", "api_ship": [1, 3, -1, -1, -1, -1],
             "api_mission": [0, 0, 0, 0]},
            {"api_id": 3, "api_name": "第3艦隊", "api_ship": [12, 7, -1, -1, -1, -1],
             "api_mission": [2, 21, 1792155961666, 0]},
        ])

    def test_completed_expedition_has_priority(self):
        decision = plan_next_expedition(
            self.fleets, mission_by_fleet={2: 2, 3: 21}
        )
        self.assertEqual(decision.action, "collect")
        self.assertEqual(decision.fleet_id, 3)

    def test_idle_dispatch_is_only_a_recommendation(self):
        fleets = self.fleets[:1]
        decision = plan_next_expedition(fleets, mission_by_fleet={2: 2})
        self.assertEqual(decision.action, "dispatch")
        self.assertEqual(decision.fleet_id, 2)
        self.assertEqual(decision.mission_id, 2)

    def test_unknown_state_never_dispatches(self):
        fleet = parse_fleet_payload([
            {"api_id": 2, "api_ship": [1], "api_mission": "unknown"}
        ])
        decision = plan_next_expedition(fleet, mission_by_fleet={2: 2})
        self.assertEqual(decision.action, "wait")

    def test_empty_fleet_never_dispatches(self):
        fleet = parse_fleet_payload([
            {"api_id": 2, "api_ship": [-1, -1, -1, -1, -1, -1],
             "api_mission": [0, 0, 0, 0]}
        ])
        decision = plan_next_expedition(fleet, mission_by_fleet={2: 2})
        self.assertEqual(decision.action, "wait")

    def test_unconfigured_fleet_never_dispatches(self):
        decision = plan_next_expedition(self.fleets[:1], mission_by_fleet={})
        self.assertEqual(decision.action, "wait")


if __name__ == "__main__":
    unittest.main()
