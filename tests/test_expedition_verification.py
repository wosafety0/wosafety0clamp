import unittest
from datetime import datetime, timedelta, timezone

from kancollebot.core.fleet_parser import parse_fleet_payload
from kancollebot.core.observed_state import ObservedState
from kancollebot.core.expedition_verification import verify_expedition_action


class ExpeditionActionVerificationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 22, 14, 0, tzinfo=timezone.utc)
        self.before_time = self.now - timedelta(seconds=4)
        self.after_time = self.now - timedelta(seconds=1)

    def fleet(self, state, mission_id=0):
        return parse_fleet_payload([{
            "api_id": 2,
            "api_name": "第2艦隊",
            "api_ship": [73, 70, -1, -1, -1, -1],
            "api_mission": [state, mission_id, 123456 if state == 1 else 0, 0],
        }])

    def observation(self, payload, at, source="poi-http-read-only"):
        return ObservedState(payload=payload, observed_at=at, source=source)

    def verify(self, action, before_state, after_state, *, before_mission=0, after_mission=0,
               before_at=None, after_at=None, before_source="poi-http-read-only",
               after_source="poi-http-read-only", mission_id=5):
        return verify_expedition_action(
            action,
            fleet_id=2,
            mission_id=mission_id if action == "dispatch" else None,
            before=self.observation(self.fleet(before_state, before_mission),
                                    before_at or self.before_time, before_source),
            after=self.observation(self.fleet(after_state, after_mission),
                                   after_at or self.after_time, after_source),
            now=self.now,
            max_age=timedelta(seconds=10),
        )

    def test_collection_confirmed_only_after_completed_to_idle(self):
        result = self.verify("collect", 2, 0)
        self.assertEqual(result.status, "confirmed")
        self.assertFalse(result.stop_required)

    def test_collection_still_completed_is_not_confirmed_and_stops(self):
        result = self.verify("collect", 2, 2)
        self.assertEqual(result.status, "not_confirmed")
        self.assertTrue(result.stop_required)
        self.assertIn("do not retry", result.reason)

    def test_dispatch_confirmed_for_requested_mission(self):
        result = self.verify("dispatch", 0, 1, after_mission=5)
        self.assertEqual(result.status, "confirmed")
        self.assertFalse(result.stop_required)

    def test_dispatch_to_different_mission_is_unknown(self):
        result = self.verify("dispatch", 0, 1, after_mission=7)
        self.assertEqual(result.status, "unknown")
        self.assertTrue(result.stop_required)

    def test_unchanged_idle_dispatch_is_not_confirmed_and_stops(self):
        result = self.verify("dispatch", 0, 0)
        self.assertEqual(result.status, "not_confirmed")
        self.assertTrue(result.stop_required)

    def test_unknown_pre_action_state_is_unknown(self):
        result = self.verify("collect", 1, 0)
        self.assertEqual(result.status, "unknown")
        self.assertTrue(result.stop_required)

    def test_mismatched_sources_are_unknown(self):
        result = self.verify("collect", 2, 0, after_source="other-source")
        self.assertEqual(result.status, "unknown")

    def test_stale_post_action_observation_is_unknown(self):
        result = self.verify("collect", 2, 0, after_at=self.now - timedelta(minutes=2))
        self.assertEqual(result.status, "unknown")

    def test_post_observation_must_be_newer(self):
        result = self.verify("collect", 2, 0, after_at=self.before_time)
        self.assertEqual(result.status, "unknown")
        self.assertIn("not newer", result.reason)

    def test_missing_target_fleet_is_unknown(self):
        empty = self.observation((), self.after_time)
        result = verify_expedition_action(
            "collect", fleet_id=2, mission_id=None,
            before=self.observation(self.fleet(2), self.before_time),
            after=empty, now=self.now, max_age=timedelta(seconds=10),
        )
        self.assertEqual(result.status, "unknown")

    def test_invalid_dispatch_mission_id_is_unknown(self):
        result = verify_expedition_action(
            "dispatch", fleet_id=2, mission_id=True,
            before=self.observation(self.fleet(0), self.before_time),
            after=self.observation(self.fleet(1, 5), self.after_time),
            now=self.now, max_age=timedelta(seconds=10),
        )
        self.assertEqual(result.status, "unknown")


if __name__ == "__main__":
    unittest.main()
