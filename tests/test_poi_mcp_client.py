import json
import unittest
from io import BytesIO
from urllib.request import Request

from kancollebot.integrations.poi_mcp import (
    PoiMcpClient,
    PoiMcpConfig,
    PoiMcpError,
)


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._body


class PoiMcpClientTests(unittest.TestCase):
    def test_config_rejects_non_loopback_endpoint(self):
        with self.assertRaises(ValueError):
            PoiMcpConfig(base_url="http://192.168.1.20:39271")

    def test_health_and_read_tool_flow(self):
        responses = iter(
            [
                {"ok": True, "player_logged_in": True},
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "poi-plugin-mcp", "version": "1.0.0"},
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {"tools": [{"name": "poi_get_fleets"}]},
                },
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps({"status": "ok", "data": [{"fleet_id": 1}]}),
                            }
                        ]
                    },
                },
            ]
        )
        requests = []

        def opener(request, timeout):
            self.assertGreater(timeout, 0)
            requests.append(request)
            return FakeResponse(next(responses))

        client = PoiMcpClient(
            PoiMcpConfig(token="test-token"),
            opener=opener,
        )
        self.assertTrue(client.health()["ok"])
        initialized = client.initialize()
        self.assertIn("protocolVersion", initialized)
        tools = client.list_tools()
        self.assertEqual(tools[0]["name"], "poi_get_fleets")
        fleets = client.call_read_tool("poi_get_fleets")
        self.assertEqual(fleets["data"][0]["fleet_id"], 1)

        mcp_request = requests[1]
        self.assertIsInstance(mcp_request, Request)
        self.assertEqual(mcp_request.get_header("Authorization"), "Bearer test-token")
        self.assertEqual(json.loads(requests[2].data)["method"], "tools/list")
        self.assertEqual(json.loads(requests[3].data)["method"], "tools/call")

    def test_unknown_tool_is_rejected_before_network_call(self):
        client = PoiMcpClient(opener=lambda *args, **kwargs: self.fail("network must not be called"))
        client.initialize = lambda: None
        client._initialized = True
        with self.assertRaises(PoiMcpError):
            client.call_read_tool("launch_sortie")

    def test_tool_call_requires_initialization(self):
        client = PoiMcpClient(opener=lambda *args, **kwargs: self.fail("network must not be called"))
        with self.assertRaises(PoiMcpError):
            client.list_tools()


if __name__ == "__main__":
    unittest.main()
