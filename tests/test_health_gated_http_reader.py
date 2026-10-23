import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from kancollebot.adapters.poi.cdp_probe import CdpPageProbe
from kancollebot.adapters.poi.health_gated_http_reader import PoiHealthGatedHttpReader
from kancollebot.adapters.poi.state_observation_tracker import PoiStateObservationTracker


class PoiHealthGatedHttpReaderTests(unittest.TestCase):
    def test_page_probe_precedes_http_read_and_source_event_gate(self):
        now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
        events = []
        probe = CdpPageProbe(
            checked_at=now, target_found=True, title="Poi", url="app://poi",
            document_ready_state="complete", error_text="KanColle"
        )
        adapter = Mock()
        adapter.get_observed_states.side_effect = lambda fields: (events.append("read"), ("fleets", "ships"))[1]
        receiver = Mock()
        receiver.get_event.return_value = None
        tracker = PoiStateObservationTracker()
        tracker.observe_event({
            "path": "/kcsapi/api_port/port", "stage": "response",
            "timestamp": now.timestamp(), "response": {"api_result": 1},
        }, now=now)
        reader = PoiHealthGatedHttpReader(adapter, receiver, tracker, probe_page=lambda: (events.append("probe") or probe))

        state, observation = reader.get_observed_states(fields=object())

        self.assertEqual(events, ["probe", "read"])
        self.assertEqual(state, ("fleets", "ships"))
        self.assertEqual(observation.latest_valid_state_at, now)

    def test_missing_event_evidence_blocks_returned_state(self):
        now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
        probe = CdpPageProbe(
            checked_at=now, target_found=True, title="Poi", url="app://poi",
            document_ready_state="complete", error_text="KanColle"
        )
        adapter = Mock()
        adapter.get_observed_states.return_value = ("fleets", "ships")
        receiver = Mock()
        receiver.get_event.return_value = None
        reader = PoiHealthGatedHttpReader(adapter, receiver, PoiStateObservationTracker(), probe_page=lambda: probe)
        with unittest.mock.patch("kancollebot.adapters.poi.health_gated_reader.datetime") as clock:
            clock.now.return_value = now
            clock.now.side_effect = None
            # The real policy clock is not replaced here; absent state evidence must fail closed.
            with self.assertRaises(RuntimeError):
                reader.get_observed_states(fields=object())
        adapter.get_observed_states.assert_called_once()


if __name__ == "__main__":
    unittest.main()