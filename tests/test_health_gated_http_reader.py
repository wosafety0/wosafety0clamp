import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from kancollebot.adapters.poi.cdp_probe import CdpPageProbe
from kancollebot.adapters.poi.health_gated_http_reader import PoiHealthGatedHttpReader
from kancollebot.adapters.poi.state_observation_tracker import PoiStateObservationTracker


class PoiHealthGatedHttpReaderTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
        self.probe = CdpPageProbe(
            checked_at=self.now, target_found=True, title="Poi", url="app://poi",
            document_ready_state="complete", error_text="KanColle"
        )

    def make_reader(self, adapter, tracker):
        receiver = Mock()
        receiver.get_event.return_value = None
        return PoiHealthGatedHttpReader(
            adapter, receiver, tracker, probe_page=lambda: self.probe,
            now_provider=lambda: self.now,
        )

    def test_page_probe_precedes_http_read_and_source_event_gate(self):
        events = []
        adapter = Mock()
        adapter.get_observed_states.side_effect = lambda fields: (events.append("read"), ("fleets", "ships"))[1]
        tracker = PoiStateObservationTracker()
        self.assertTrue(tracker.observe_event({
            "path": "/kcsapi/api_port/port", "stage": "response",
            "timestamp": self.now.timestamp(), "response": {"api_result": 1},
        }, now=self.now))
        reader = self.make_reader(adapter, tracker)
        reader._probe_page = lambda: (events.append("probe") or self.probe)

        state, observation = reader.get_observed_states(fields=object())

        self.assertEqual(events, ["probe", "read"])
        self.assertEqual(state, ("fleets", "ships"))
        self.assertEqual(observation.latest_valid_state_at, self.now)

    def test_missing_event_evidence_blocks_returned_state(self):
        adapter = Mock()
        adapter.get_observed_states.return_value = ("fleets", "ships")
        reader = self.make_reader(adapter, PoiStateObservationTracker())

        with self.assertRaises(RuntimeError):
            reader.get_observed_states(fields=object())

        adapter.get_observed_states.assert_called_once()

    def test_drains_post_read_webhook_event_for_recovery(self):
        now = datetime.now(timezone.utc)
        failure_at = now.replace(microsecond=0)
        if failure_at >= now:
            from datetime import timedelta
            failure_at = failure_at - timedelta(seconds=1)
        event_time = now
        adapter = Mock()
        adapter.get_observed_states.return_value = ("fleets", "ships")
        event = {
            "path": "/kcsapi/api_port/port",
            "stage": "response",
            "timestamp": event_time.timestamp(),
            "response": {"api_result": 1},
        }
        receiver = Mock()
        receiver.get_event.side_effect = [None, event, None]
        tracker = PoiStateObservationTracker()
        probe = CdpPageProbe(
            checked_at=now, target_found=True, title="Poi", url="app://poi",
            document_ready_state="complete", error_text="KanColle",
        )
        reader = PoiHealthGatedHttpReader(
            adapter, receiver, tracker, probe_page=lambda: probe,
            now_provider=lambda: now,
        )

        state, observation = reader.get_observed_states(
            fields=object(), connection_failure_at=failure_at,
        )

        self.assertEqual(state, ("fleets", "ships"))
        self.assertEqual(observation.latest_valid_state_at, event_time)
        self.assertGreater(observation.latest_valid_state_at, failure_at)

    def test_connection_failure_requires_a_later_event(self):
        adapter = Mock()
        adapter.get_observed_states.return_value = ("fleets", "ships")
        tracker = PoiStateObservationTracker()
        tracker.observe_event({
            "path": "/kcsapi/api_port/port", "stage": "response",
            "timestamp": self.now.timestamp(), "response": {"api_result": 1},
        }, now=self.now)
        reader = self.make_reader(adapter, tracker)

        with self.assertRaises(RuntimeError):
            reader.get_observed_states(fields=object(), connection_failure_at=self.now)


if __name__ == "__main__":
    unittest.main()