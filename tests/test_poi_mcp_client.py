"""Offline tests for the standard-library Poi MCP client."""

import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from kancollebot.adapters.poi.mcp_client import PoiMcpClient, PoiMcpError


class PoiMcpClientTests(unittest.TestCase):
    def test_rejects_non_loopback_by_default(self):
        with self.assertRaises(ValueError):
            PoiMcpClient("http://192.0.2.10:12450/mcp")

    def test_accepts_default_loopback_endpoint(self):
        client = PoiMcpClient()
        self.assertEqual(client.endpoint, "http://127.0.0.1:12450/mcp")

    def test_rejects_credentials_in_loopback_endpoint(self):
        with self.assertRaisesRegex(ValueError, "must not contain credentials"):
            PoiMcpClient("http://user:secret@127.0.0.1:12450/mcp")

    def test_rejects_empty_userinfo_in_loopback_endpoint(self):
        with self.assertRaisesRegex(ValueError, "must not contain credentials"):
            PoiMcpClient("http://@127.0.0.1:12450/mcp")

    def test_redirect_handler_rejects_remote_redirect(self):
        from kancollebot.adapters.poi.mcp_client import _NoRedirectHandler
        handler = _NoRedirectHandler()
        request = urllib.request.Request("http://127.0.0.1:12450/mcp")
        self.assertIsNone(handler.redirect_request(
            request, None, 302, "Found", {}, "http://example.com/steal"
        ))

    def test_client_disables_environment_proxy(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, _format, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        proxy_config = {
            "HTTP_PROXY": "http://127.0.0.1:1",
            "HTTPS_PROXY": "http://127.0.0.1:1",
            "ALL_PROXY": "http://127.0.0.1:1",
            "http_proxy": "http://127.0.0.1:1",
            "https_proxy": "http://127.0.0.1:1",
            "all_proxy": "http://127.0.0.1:1",
            "NO_PROXY": "",
            "no_proxy": "",
        }
        try:
            with patch.dict("os.environ", proxy_config, clear=False):
                client = PoiMcpClient(
                    f"http://127.0.0.1:{server.server_port}/mcp"
                )
                request = urllib.request.Request(client.endpoint)
                with client._opener.open(request, timeout=2) as response:
                    self.assertEqual(response.status, 200)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

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


if __name__ == "__main__":
    unittest.main()
