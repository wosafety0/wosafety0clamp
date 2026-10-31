from __future__ import annotations

import unittest

from tools.observe_poi_network import project_event


class ProjectEventTests(unittest.TestCase):
    def test_request_projection_omits_host_query_and_body_content(self):
        event = {
            "method": "Network.requestWillBeSent",
            "params": {
                "requestId": "request-7",
                "timestamp": 12.5,
                "request": {
                    "url": "https://game.example/kcsapi/api_port/port?api_token=[REDACTED]&api_ver=1",
                    "method": "POST",
                    "postData": {
                        "present": True,
                        "length": 999,
                        "content": "[OMITTED_FOR_SAFETY]",
                    },
                    "headers": {"Authorization": "must-not-appear"},
                },
            },
        }
        result = project_event(event)
        self.assertEqual(result, {
            "event": "request",
            "path": "/kcsapi/api_port/port",
            "request_method": "POST",
            "body_present": True,
            "body_length": 999,
            "request_id": "request-7",
            "timestamp": 12.5,
        })
        self.assertNotIn("game.example", repr(result))
        self.assertNotIn("api_token", repr(result))
        self.assertNotIn("Authorization", repr(result))
        self.assertNotIn("content", result)

    def test_response_projection_keeps_only_status_and_mime(self):
        result = project_event({
            "method": "Network.responseReceived",
            "params": {
                "requestId": 4,
                "timestamp": 9,
                "response": {
                    "url": "https://game.example/kcsapi/api_port/port?session=secret",
                    "status": 200,
                    "mimeType": "application/json",
                    "headers": {"Set-Cookie": "secret-cookie"},
                    "remoteIPAddress": "192.0.2.1",
                },
            },
        })
        self.assertEqual(result, {
            "event": "response",
            "path": "/kcsapi/api_port/port",
            "status": 200,
            "mime_type": "application/json",
            "request_id": "4",
            "timestamp": 9,
        })
        self.assertNotIn("secret", repr(result))
        self.assertNotIn("192.0.2.1", repr(result))

    def test_unsupported_events_and_malformed_urls_are_ignored(self):
        self.assertIsNone(project_event({"method": "Network.loadingFinished", "params": {}}))
        self.assertIsNone(project_event({
            "method": "Network.requestWillBeSent",
            "params": {"request": {"url": "file:///private/path", "method": "GET"}},
        }))
        self.assertIsNone(project_event({
            "method": "Network.responseReceived",
            "params": {"response": {"url": "not a url", "status": 200}},
        }))
        self.assertIsNone(project_event(None))

    def test_non_finite_metadata_is_omitted(self):
        result = project_event({
            "method": "Network.responseReceived",
            "params": {
                "requestId": True,
                "timestamp": float("nan"),
                "response": {
                    "url": "https://game.example/kcsapi/api_port/port",
                    "status": float("inf"),
                    "mimeType": "application/json",
                },
            },
        })
        self.assertEqual(result, {
            "event": "response",
            "path": "/kcsapi/api_port/port",
            "mime_type": "application/json",
        })


if __name__ == "__main__":
    unittest.main()
