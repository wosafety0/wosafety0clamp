"""Offline tests for the standard-library Poi MCP client."""

import unittest
from unittest.mock import patch

from kancollebot.adapters.poi.mcp_client import PoiMcpClient, PoiMcpError, _NoRedirectHandler


class PoiMcpClientTests(unittest.TestCase):
    def test_rejects_non_loopback_by_default(self):
        with self.assertRaises(ValueError):
            PoiMcpClient("http://192.0.2.10:12450/mcp")

    def test_accepts_default_loopback_endpoint(self):
        client = PoiMcpClient()
        self.assertEqual(client.endpoint, "http://127.0.0.1:12450/mcp")

    def test_rejects_credentials_in_endpoint_url(self):
        with self.assertRaisesRegex(ValueError, "credentials"):
            PoiMcpClient("http://user:secret@127.0.0.1:12450/mcp")

    def test_redirect_handler_refuses_redirects(self):
        handler = _NoRedirectHandler()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {}, "http://example.com/"))

    def test_rejects_action_tool(self):
        client = PoiMcpClient()
        with self.assertRaises(ValueError):
            client.call_read_tool("poi_hensei_save", {"title": "not allowed"})

    def test_initialize_negotiates_and_sends_initialized_notification(self):
        client = PoiMcpClient()
        responses = [
            {"jsonrpc": "2.0", "id": 1, "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "poi-nanodesu-mcp", "version": "0.1.0"},
            }},
            None,
        ]
        with patch.object(client, "_post", side_effect=responses) as post:
            info = client.initialize()
        self.assertEqual(info["name"], "poi-nanodesu-mcp")
        self.assertTrue(client._initialized)
        self.assertEqual(post.call_count, 2)
        self.assertTrue(post.call_args_list[1].kwargs["notification"])

    def test_tool_error_is_not_silently_accepted(self):
        client = PoiMcpClient()
        client._initialized = True
        with patch.object(client, "_request", return_value={
            "isError": True, "content": [{"type": "text", "text": "path unavailable"}]
        }):
            with self.assertRaisesRegex(PoiMcpError, "path unavailable"):
                client.call_read_tool("poi_get", {"path": "info.fleets"})

    def test_tool_allowlist_rejects_unknown_tool(self):
        client = PoiMcpClient()
        with self.assertRaises(ValueError):
            client.call_read_tool("arbitrary_tool", {})


    def test_opener_is_built_once_and_reused_for_mcp_and_health_requests(self):
        class FakeResponse:
            def __init__(self, body):
                self.status = 200
                self.headers = {
                    "Content-Type": "application/json",
                    "Mcp-Session-Id": "session-1",
                }
                self._body = body

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self, limit=-1):
                return self._body

        class FakeOpener:
            def __init__(self):
                self.responses = [
                    FakeResponse(b'{"jsonrpc":"2.0","id":1,"result":{}}'),
                    FakeResponse(b'{"name":"poi-nanodesu-mcp","status":"ok","storeReady":true}'),
                ]
                self.calls = []

            def open(self, request, timeout):
                self.calls.append((request, timeout))
                return self.responses.pop(0)

        fake_opener = FakeOpener()
        with patch("urllib.request.build_opener", return_value=fake_opener) as build:
            client = PoiMcpClient()
            client._request("ping")
            health = client.health()

        self.assertEqual(build.call_count, 1)
        self.assertEqual(len(fake_opener.calls), 2)
        self.assertEqual(health["status"], "ok")


if __name__ == "__main__":
    unittest.main()
