"""Protocol edge-case tests for the read-only Poi MCP client."""

import unittest
from unittest.mock import patch

from kancollebot.adapters.poi.mcp_client import PoiMcpClient, PoiMcpError


class FakeResponse:
    def __init__(self, body, *, status=200, content_type="application/json", headers=None):
        self.status = status
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self._body = body

    def read(self, size=-1):
        return self._body if size < 0 else self._body[:size]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class PoiMcpProtocolEdgeTests(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_sse_response_is_decoded(self, urlopen):
        urlopen.return_value = FakeResponse(
            b'event: message\ndata: {"jsonrpc":"2.0","id":7,"result":{"ok":true}}\n\n',
            content_type="text/event-stream",
        )
        result = PoiMcpClient()._post({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
        self.assertEqual(result["id"], 7)
        self.assertEqual(result["result"], {"ok": True})

    @patch("urllib.request.urlopen")
    def test_invalid_utf8_becomes_mcp_error(self, urlopen):
        urlopen.return_value = FakeResponse(b"\xff")
        with self.assertRaisesRegex(PoiMcpError, "valid UTF-8"):
            PoiMcpClient()._post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    @patch("urllib.request.urlopen")
    def test_malformed_sse_json_becomes_mcp_error(self, urlopen):
        urlopen.return_value = FakeResponse(
            b"data: {not-json}\n\n",
            content_type="text/event-stream",
        )
        with self.assertRaisesRegex(PoiMcpError, "Malformed JSON"):
            PoiMcpClient()._post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    def test_boolean_response_id_is_not_accepted_as_integer_id(self):
        client = PoiMcpClient()
        with patch.object(client, "_post", return_value={"id": True, "result": {}}):
            with self.assertRaisesRegex(PoiMcpError, "Invalid response id"):
                client._request("tools/list")


if __name__ == "__main__":
    unittest.main()
