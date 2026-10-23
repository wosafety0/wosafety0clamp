import json
import unittest
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from kancollebot.adapters.poi.state_observation_tracker import PoiStateObservationTracker
from kancollebot.adapters.poi.webhook_receiver import PoiWebhookReceiver, WEBHOOK_PATH


class PoiForwarderContractTests(unittest.TestCase):
    def test_forwarder_response_envelope_survives_receiver_and_advances_tracker(self):
        now = datetime.now(timezone.utc)
        receiver = PoiWebhookReceiver(port=0)
        port = receiver.start()
        try:
            payload = {
                "path": "/kcsapi/api_port/port",
                "request": None,
                "response": {"api_result": 1, "api_data": {}},
                "stage": "response_received",
                "timestamp": now.timestamp() * 1000,
            }
            request = Request(
                f"http://127.0.0.1:{port}{WEBHOOK_PATH}",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=2) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("ACCEPTED", response.read().decode("utf-8"))

            event = receiver.get_event(timeout=1)
            self.assertIsNotNone(event)
            tracker = PoiStateObservationTracker()
            self.assertTrue(tracker.observe_event(event, now=now))
            self.assertEqual(
                tracker.latest_valid_state_at,
                datetime.fromtimestamp(payload["timestamp"] / 1000, tz=timezone.utc),
            )
        finally:
            receiver.stop()

    def test_receiver_rejects_wrong_route(self):
        receiver = PoiWebhookReceiver(port=0)
        port = receiver.start()
        try:
            request = Request(
                f"http://127.0.0.1:{port}/wrong-route",
                data=b'{"path":"/kcsapi/api_port/port"}',
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(Exception):
                urlopen(request, timeout=2)
        finally:
            receiver.stop()


if __name__ == "__main__":
    unittest.main()
