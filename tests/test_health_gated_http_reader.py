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

    def test_event_arriving_after_read_starts_cannot_authorize_that_read(self):
        now = datetime.now(timezone.utc)
        adapter = Mock()
        adapter.get_observed_states.return_value = ("fleets", "ships")
        event = {
            "path": "/kcsapi/api_port/port",
            "stage": "response",
            "timestamp": now.timestamp(),
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

        with self.assertRaises(RuntimeError):
            reader.get_observed_states(fields=object())

        adapter.get_observed_states.assert_not_called()

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

    def test_status_and_diagnostic_reads_use_the_same_health_gate(self):
        tracker = PoiStateObservationTracker()
        self.assertTrue(tracker.observe_event({
            "path": "/kcsapi/api_port/port", "stage": "response_received",
            "timestamp": self.now.timestamp(), "response": {"api_result": 1},
        }, now=self.now))
        adapter = Mock()
        adapter.get_status.return_value = {"source": "poi-http-read-only"}
        adapter.diagnose.return_value = {"ok": True}
        reader = self.make_reader(adapter, tracker)

        status, status_observation = reader.get_status()
        diagnostic, diagnostic_observation = reader.diagnose()

        self.assertEqual(status, {"source": "poi-http-read-only"})
        self.assertEqual(diagnostic, {"ok": True})
        self.assertEqual(status_observation.latest_valid_state_at, self.now)
        self.assertEqual(diagnostic_observation.latest_valid_state_at, self.now)
        adapter.get_status.assert_called_once_with()
        adapter.diagnose.assert_called_once_with()

    def test_status_and_diagnostic_are_withheld_without_fresh_event_evidence(self):
        adapter = Mock()
        adapter.get_status.return_value = {"source": "poi-http-read-only"}
        adapter.diagnose.return_value = {"ok": True}
        reader = self.make_reader(adapter, PoiStateObservationTracker())

        with self.assertRaises(RuntimeError):
            reader.get_status()
        with self.assertRaises(RuntimeError):
            reader.diagnose()

        adapter.get_status.assert_called_once_with()
        adapter.diagnose.assert_called_once_with()

    def test_read_and_plan_uses_gated_observations(self):
        from unittest.mock import patch

        tracker = PoiStateObservationTracker()
        self.assertTrue(tracker.observe_event({
            "path": "/kcsapi/api_port/port", "stage": "response_received",
            "timestamp": self.now.timestamp(), "response": {"api_result": 1},
        }, now=self.now))
        fleets_state = object()
        ships_state = object()
        adapter = Mock()
        adapter.get_observed_states.return_value = (fleets_state, ships_state)
        reader = self.make_reader(adapter, tracker)
        decision = object()

        with patch(
            "kancollebot.adapters.poi.health_gated_http_reader.plan_fresh_safe_expedition",
            return_value=decision,
        ) as planner:
            result = reader.read_and_plan(fields=object(), mission_by_fleet={2: 1})

        self.assertEqual(result, (fleets_state, ships_state, decision))
        planner.assert_called_once()
        adapter.get_observed_states.assert_called_once_with(unittest.mock.ANY)


if __name__ == "__main__":
    unittest.main()
