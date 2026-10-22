import json
import urllib.error
import urllib.request
import unittest

from kancollebot.adapters.poi.webhook_receiver import PoiWebhookReceiver


class PoiWebhookReceiverTests(unittest.TestCase):
    def setUp(self):
        self.receiver = PoiWebhookReceiver(port=0)
        self.port = self.receiver.start()
        self.base_url = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.receiver.stop()

    def post(self, payload, *, path="/webhook/kancolle", raw_body=None):
        body = raw_body if raw_body is not None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_accepts_kcsapi_event_and_preserves_metadata(self):
        payload = {
            "path": "/kcsapi/api_port/port",
            "request": {"api_token": "fixture-token"},
            "response": {"api_result": 1, "api_data": {"api_material": []}},
            "stage": "response",
            "timestamp": 1792670000,
        }
        status, response = self.post(payload)
        self.assertEqual(status, 200)
        self.assertEqual(response["status"], "ACCEPTED & QUEUED")
        event = self.receiver.get_event(timeout=1)
        self.assertEqual(event["path"], payload["path"])
        self.assertEqual(event["request"], payload["request"])
        self.assertEqual(event["response"], payload["response"])
        self.assertEqual(event["stage"], "response")
        self.assertEqual(event["timestamp"], payload["timestamp"])

    def test_accepts_map_resource_and_normalizes_path(self):
        payload = {
            "path": "https://example.invalid/kcs2/resources/map/area.json",
            "stage": "response",
            "timestamp": "fixture-time",
        }
        status, _ = self.post(payload)
        self.assertEqual(status, 200)
        event = self.receiver.get_event(timeout=1)
        self.assertEqual(event["path"], "kcs2/resources/map/area.json")
        self.assertEqual(event["response"]["url"], payload["path"])
        self.assertIsNone(event["request"])

    def test_filters_unknown_resources(self):
        status, response = self.post({"path": "/unrelated/path"})
        self.assertEqual(status, 200)
        self.assertIn("FILTERED OUT", response["status"])
        self.assertIsNone(self.receiver.get_event(timeout=0))

    def test_rejects_invalid_json(self):
        status, response = self.post({}, raw_body=b"{not-json")
        self.assertEqual(status, 400)
        self.assertEqual(response["status"], "INVALID JSON")

    def test_rejects_non_object_json(self):
        status, response = self.post({}, raw_body=b"[]")
        self.assertEqual(status, 400)
        self.assertEqual(response["status"], "JSON OBJECT REQUIRED")

    def test_rejects_wrong_route(self):
        status, _ = self.post({"path": "/kcsapi/api_port/port"}, path="/")
        self.assertEqual(status, 404)
        self.assertIsNone(self.receiver.get_event(timeout=0))

    def test_rejects_oversized_body(self):
        self.receiver.stop()
        self.receiver = PoiWebhookReceiver(port=0, max_body_bytes=8)
        self.port = self.receiver.start()
        self.base_url = f"http://127.0.0.1:{self.port}"
        status, response = self.post({}, raw_body=b"x" * 9)
        self.assertEqual(status, 413)
        self.assertEqual(response["status"], "BODY TOO LARGE")

    def test_queue_full_returns_service_unavailable(self):
        self.receiver.stop()
        self.receiver = PoiWebhookReceiver(port=0, queue_size=1)
        self.port = self.receiver.start()
        self.base_url = f"http://127.0.0.1:{self.port}"
        payload = {"path": "/kcsapi/api_port/port", "response": {}}
        self.assertEqual(self.post(payload)[0], 200)
        status, response = self.post(payload)
        self.assertEqual(status, 503)
        self.assertEqual(response["status"], "EVENT QUEUE FULL")

    def test_receiver_binds_only_to_ipv4_loopback(self):
        with self.assertRaisesRegex(ValueError, "127.0.0.1"):
            PoiWebhookReceiver(host="0.0.0.0")
        with self.assertRaisesRegex(ValueError, "127.0.0.1"):
            PoiWebhookReceiver(host="192.168.1.2")

    def test_rejects_invalid_configuration(self):
        with self.assertRaises(ValueError):
            PoiWebhookReceiver(port=-1)
        with self.assertRaises(ValueError):
            PoiWebhookReceiver(max_body_bytes=0)
        with self.assertRaises(ValueError):
            PoiWebhookReceiver(queue_size=0)


if __name__ == "__main__":
    unittest.main()
