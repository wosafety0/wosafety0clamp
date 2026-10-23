import unittest
from datetime import datetime, timedelta, timezone

from kancollebot.adapters.poi.state_observation_tracker import PoiStateObservationTracker


class PoiStateObservationTrackerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
        self.tracker = PoiStateObservationTracker(max_age_seconds=30)

    def event(self, **changes):
        value = {
            "path": "/kcsapi/api_port/port",
            "stage": "response",
            "timestamp": self.now.timestamp(),
            "response": {"api_result": 1, "api_data": {}},
        }
        value.update(changes)
        return value

    def test_accepts_successful_state_response_with_seconds_timestamp(self):
        self.assertTrue(self.tracker.observe_event(self.event(), now=self.now))
        self.assertEqual(self.tracker.latest_valid_state_at, self.now)

    def test_accepts_poi_milliseconds_timestamp(self):
        event = self.event(timestamp=self.now.timestamp() * 1000)
        self.assertTrue(self.tracker.observe_event(event, now=self.now))
        self.assertEqual(self.tracker.latest_valid_state_at, self.now)

    def test_rejects_request_stage_and_unrelated_endpoint(self):
        self.assertFalse(self.tracker.observe_event(self.event(stage="request"), now=self.now))
        self.assertFalse(self.tracker.observe_event(self.event(path="/kcsapi/api_req_map/start"), now=self.now))
        self.assertIsNone(self.tracker.latest_valid_state_at)

    def test_rejects_failed_response_and_malformed_timestamp(self):
        self.assertFalse(self.tracker.observe_event(self.event(response={"api_result": 0}), now=self.now))
        self.assertFalse(self.tracker.observe_event(self.event(response={"api_result": True}), now=self.now))
        self.assertFalse(self.tracker.observe_event(self.event(timestamp="fixture-time"), now=self.now))
        self.assertIsNone(self.tracker.latest_valid_state_at)

    def test_rejects_stale_and_far_future_timestamps(self):
        stale = self.now - timedelta(seconds=31)
        future = self.now + timedelta(seconds=6)
        self.assertFalse(self.tracker.observe_event(self.event(timestamp=stale.timestamp()), now=self.now))
        self.assertFalse(self.tracker.observe_event(self.event(timestamp=future.timestamp()), now=self.now))
        self.assertIsNone(self.tracker.latest_valid_state_at)

    def test_does_not_move_latest_timestamp_backwards(self):
        newer = self.now - timedelta(seconds=1)
        older = self.now - timedelta(seconds=2)
        self.assertTrue(self.tracker.observe_event(self.event(timestamp=newer.timestamp()), now=self.now))
        self.assertTrue(self.tracker.observe_event(self.event(timestamp=older.timestamp()), now=self.now))
        self.assertEqual(self.tracker.latest_valid_state_at, newer)

    def test_naive_now_is_rejected(self):
        with self.assertRaises(ValueError):
            self.tracker.observe_event(self.event(), now=datetime(2026, 10, 23, 12, 0))


if __name__ == "__main__":
    unittest.main()