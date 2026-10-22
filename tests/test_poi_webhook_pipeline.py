"""Offline loopback contract tests for the Poi webhook-to-normalizer boundary."""

import json
import unittest
from urllib.request import Request, urlopen

from kancollebot.adapters.poi.event_normalizer import normalize_forwarder_event
from kancollebot.adapters.poi.webhook_receiver import PoiWebhookReceiver


class PoiWebhookPipelineTests(unittest.TestCase):
    def setUp(self):
        self.receiver = PoiWebhookReceiver(port=0)
        self.port = self.receiver.start()

    def tearDown(self):
        self.receiver.stop()

    def post(self, payload):
        data = json.dumps(payload).encode("utf-8")
        request = Request(
            "http://127.0.0.1:%d/webhook/kancolle" % self.port,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            return response.status

    def test_success_envelope_passes_through_receiver(self):
        self.assertEqual(self.post({
            "path": "/kcsapi/api_port/port?api_token=secret",
            "stage": "response",
            "timestamp": "2026-10-23T00:00:00Z",
            "request": {"api_token": "secret"},
            "response": {"api_result": 1, "api_data": {"api_material": []}},
        }), 200)
        event = self.receiver.get_event(timeout=1)
        self.assertEqual(event["path"], "/kcsapi/api_port/port")
        self.assertEqual(event["request"]["api_token"], "[REDACTED]")
        normalized = normalize_forwarder_event(event)
        self.assertTrue(normalized.state_eligible)
        self.assertEqual(normalized.path, "/kcsapi/api_port/port")

    def test_request_stage_cannot_become_state(self):
        self.assertEqual(self.post({
            "path": "/kcsapi/api_port/port",
            "stage": "request",
            "timestamp": 1792670000,
            "response": {"api_result": 1},
        }), 200)
        self.assertFalse(normalize_forwarder_event(
            self.receiver.get_event(timeout=1)
        ).state_eligible)

    def test_failed_api_response_cannot_become_state(self):
        self.assertEqual(self.post({
            "path": "/kcsapi/api_port/port",
            "stage": "response",
            "timestamp": 1792670000,
            "response": {"api_result": 0},
        }), 200)
        self.assertFalse(normalize_forwarder_event(
            self.receiver.get_event(timeout=1)
        ).state_eligible)

    def test_unknown_resource_is_filtered_before_queue(self):
        self.assertEqual(self.post({
            "path": "/unrelated/resource",
            "stage": "response",
            "timestamp": 1792670000,
            "response": {"api_result": 1},
        }), 200)
        self.assertIsNone(self.receiver.get_event(timeout=0))


if __name__ == "__main__":
    unittest.main()
