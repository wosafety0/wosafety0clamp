"""Offline tests for the read-only Poi bridge verifier."""

import unittest
from unittest.mock import patch

from tools.verify_poi_bridge import run_nanodesu, summarize, validate_loopback_url


class PoiBridgeVerifierTests(unittest.TestCase):
    def test_accepts_loopback_endpoint(self):
        self.assertEqual(
            validate_loopback_url("http://127.0.0.1:17777/"),
            "http://127.0.0.1:17777",
        )

    def test_rejects_remote_endpoint(self):
        with self.assertRaises(ValueError):
            validate_loopback_url("http://192.0.2.1:17777")

    def test_rejects_credentials_in_url(self):
        with self.assertRaises(ValueError):
            validate_loopback_url("http://user:secret@127.0.0.1:17777")

    def test_nanodesu_verifier_queries_master_ship_path(self):
        instances = []

        class FakeTool:
            def __init__(self, name):
                self.name = name

        class FakeClient:
            def __init__(self, endpoint, timeout):
                self.calls = []
                instances.append(self)

            def health(self):
                return {"status": "ok", "storeReady": True}

            def initialize(self):
                return {"name": "fake"}

            def list_tools(self):
                return (FakeTool("poi_describe"), FakeTool("poi_get"))

            def call_read_tool(self, name, arguments):
                self.calls.append((name, arguments))
                return {"content": [{"type": "text", "text": "{}"}]}

        with patch("kancollebot.adapters.poi.mcp_client.PoiMcpClient", FakeClient):
            report = run_nanodesu("http://127.0.0.1:12450/mcp", 1.0)

        self.assertIn("master_ships", report["checks"])
        self.assertIn(("poi_get", {"path": "const.$ships"}), instances[0].calls)
        self.assertIn(("poi_describe", {"path": "info.fleets"}), instances[0].calls)

    def test_summarize_dict_does_not_copy_values(self):
        result = summarize({"ships": [{"name": "private-value", "id": 3}], "status": "ok"})
        self.assertEqual(result["type"], "dict")
        self.assertIn("ships", result["keys"])
        self.assertNotIn("private-value", repr(result))

    def test_summarize_list_reports_shape_only(self):
        result = summarize([{"id": 123, "secret": "hidden"}])
        self.assertEqual(result["item_count"], 1)
        self.assertEqual(result["first_item_keys"], ["id", "secret"])
        self.assertNotIn("hidden", repr(result))


if __name__ == "__main__":
    unittest.main()
