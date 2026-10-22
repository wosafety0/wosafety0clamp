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


class FakeSseResponse:
    def __init__(self, body):
        self._body = body.encode("utf-8")
        self.headers = {"Content-Type": "text/event-stream"}

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
                {},
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
        self.assertEqual(json.loads(requests[2].data)["method"], "notifications/initialized")
        self.assertNotIn("id", json.loads(requests[2].data))
        self.assertEqual(json.loads(requests[3].data)["method"], "tools/list")
        self.assertEqual(json.loads(requests[4].data)["method"], "tools/call")

    def test_initialize_accepts_sse_and_selects_matching_jsonrpc_id(self):
        notification = json.dumps({
            "jsonrpc": "2.0", "method": "notifications/progress", "params": {}
        })
        response = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "serverInfo": {"name": "poi-mcp"},
            },
        })
        body = (
            "event: message\n"
            "data: " + notification + "\n\n"
            "event: message\n"
            "data: " + response + "\n\n"
        )
        client = PoiMcpClient(
            PoiMcpConfig(token="test-token"),
            opener=lambda request, timeout: FakeSseResponse(body),
        )

        result = client.initialize()

        self.assertEqual(result["protocolVersion"], "2025-03-26")
        self.assertTrue(client._initialized)

    def test_sse_without_matching_response_id_is_rejected(self):
        body = (
            "event: message\n"
            "data: " + json.dumps({
                "jsonrpc": "2.0", "id": 99, "result": {}
            }) + "\n\n"
        )
        client = PoiMcpClient(
            PoiMcpConfig(token="test-token"),
            opener=lambda request, timeout: FakeSseResponse(body),
        )

        with self.assertRaisesRegex(PoiMcpError, "no matching JSON response"):
            client.initialize()

    def test_jsonrpc_response_id_mismatch_is_rejected(self):
        client = PoiMcpClient(
            PoiMcpConfig(token="test-token"),
            opener=lambda request, timeout: FakeResponse({
                "jsonrpc": "2.0",
                "id": 99,
                "result": {"protocolVersion": "2025-03-26"},
            }),
        )
        with self.assertRaisesRegex(PoiMcpError, "ID does not match"):
            client.initialize()

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
