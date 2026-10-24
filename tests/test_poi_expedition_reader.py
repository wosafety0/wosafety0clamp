"""Offline contract tests for the read-only Poi expedition reader."""
import json
import unittest
from datetime import datetime, timezone

from kancollebot.adapters.poi.expedition_reader import PoiExpeditionReader
from kancollebot.adapters.poi.mcp_client import McpTool, PoiMcpError

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def sample_payloads():
    return {
        "info.fleets": {"kind": "array", "total": 1, "returned": 1, "truncated": False,
            "items": [{"api_id": 1, "api_ship": [101, -1, 102], "api_mission": [0, 0, 0, 0]}]},
        "info.ships": {"kind": "object-map", "total": 2, "returned": 2, "truncated": False,
            "items": {
                "101": {"api_ship_id": 11, "api_nowhp": 10, "api_maxhp": 10, "api_fuel": 10, "api_bull": 10},
                "102": {"api_ship_id": 11, "api_nowhp": 10, "api_maxhp": 10, "api_fuel": 10, "api_bull": 10},
            }},
        "lookup": {"11": {"api_fuel_max": 10, "api_bull_max": 10}},
    }


class FakeClient:
    def __init__(self, *, store_ready=True, payloads=None, tools=None, lookup=None):
        self.store_ready = store_ready
        self.payloads = payloads or sample_payloads()
        self.tools = tools or ("poi_describe", "poi_get", "poi_lookup")
        self.lookup = lookup
        self.calls = []
        self.initialize_calls = 0

    def health(self):
        return {"name": "poi-nanodesu-mcp", "status": "ok", "storeReady": self.store_ready}

    def initialize(self):
        self.initialize_calls += 1
        return {"name": "fake-poi"}

    def list_tools(self):
        return tuple(McpTool(name) for name in self.tools)

    def call_read_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "poi_describe":
            payload = {"kind": "array", "path": arguments.get("path")}
        elif name == "poi_lookup":
            payload = self.lookup if self.lookup is not None else self.payloads["lookup"]
        else:
            payload = self.payloads[arguments["path"]]
        return {"content": [{"type": "text", "text": json.dumps(payload)}]}


class PoiExpeditionReaderTests(unittest.TestCase):
    def make_reader(self, **kwargs):
        client = FakeClient(**kwargs)
        return client, PoiExpeditionReader(client=client, clock=lambda: NOW)

    def test_reads_fleet_with_targeted_master_lookup(self):
        client, reader = self.make_reader()
        observed = reader.read_fleets()
        self.assertEqual(observed.observed_at, NOW)
        self.assertEqual(len(observed.payload), 1)
        self.assertEqual(observed.payload[0].fleet_id, 1)
        self.assertTrue(observed.payload[0].fuel_ready)
        self.assertTrue(observed.payload[0].ammo_ready)
        lookup_calls = [args for name, args in client.calls if name == "poi_lookup"]
        self.assertEqual(len(lookup_calls), 1)
        self.assertEqual(lookup_calls[0]["kind"], "ships")
        self.assertEqual(lookup_calls[0]["ids"], [11])
        self.assertEqual(lookup_calls[0]["select"], ["api_fuel_max", "api_bull_max"])
        self.assertFalse(any(args.get("path") == "const.$ships" for _, args in client.calls))

    def test_rejects_truncated_fleet_payload(self):
        payloads = sample_payloads()
        payloads["info.fleets"]["truncated"] = True
        _, reader = self.make_reader(payloads=payloads)
        with self.assertRaisesRegex(PoiMcpError, "truncated"):
            reader.read_fleets()

    def test_missing_mission_data_fails_closed(self):
        payloads = sample_payloads()
        del payloads["info.fleets"]["items"][0]["api_mission"]
        _, reader = self.make_reader(payloads=payloads)
        with self.assertRaisesRegex(PoiMcpError, "normalization failed closed"):
            reader.read_fleets()

    def test_missing_master_record_fails_closed(self):
        _, reader = self.make_reader(lookup={})
        with self.assertRaisesRegex(PoiMcpError, "omitted master ship record"):
            reader.read_fleets()

    def test_wrong_lookup_shape_fails_closed(self):
        _, reader = self.make_reader(lookup={"kind": "ships", "items": {}})
        with self.assertRaisesRegex(PoiMcpError, "invalid master-record object map"):
            reader.read_fleets()

    def test_invalid_required_master_field_fails_closed(self):
        _, reader = self.make_reader(lookup={"11": {"api_fuel_max": True, "api_bull_max": 10}})
        with self.assertRaisesRegex(PoiMcpError, "invalid api_fuel_max"):
            reader.read_fleets()

    def test_wrong_bridge_identity_stops_before_initialization(self):
        class WrongIdentityClient(FakeClient):
            def health(self):
                return {"name": "another-service", "status": "ok", "storeReady": True}
        client = WrongIdentityClient()
        reader = PoiExpeditionReader(client=client, clock=lambda: NOW)
        with self.assertRaisesRegex(PoiMcpError, "identity"):
            reader.read_fleets()
        self.assertEqual(client.initialize_calls, 0)
        self.assertEqual(client.calls, [])

    def test_store_not_ready_stops_before_initialization(self):
        client = FakeClient(store_ready=False)
        reader = PoiExpeditionReader(client=client, clock=lambda: NOW)
        with self.assertRaisesRegex(PoiMcpError, "store is not ready"):
            reader.read_fleets()
        self.assertEqual(client.initialize_calls, 0)
        self.assertEqual(client.calls, [])

    def test_missing_lookup_tool_fails_closed(self):
        _, reader = self.make_reader(tools=("poi_describe", "poi_get"))
        with self.assertRaisesRegex(PoiMcpError, "missing required"):
            reader.read_fleets()

    def test_truncated_ship_payload_fails_closed(self):
        payloads = sample_payloads()
        payloads["info.ships"]["truncated"] = True
        _, reader = self.make_reader(payloads=payloads)
        with self.assertRaisesRegex(PoiMcpError, "truncated"):
            reader.read_fleets()


if __name__ == "__main__":
    unittest.main()
