"""Offline contract tests for the read-only Poi expedition adapter."""

import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from kancollebot.adapters.poi.expedition_reader import PoiExpeditionReader
from kancollebot.adapters.poi.mcp_client import PoiMcpError


NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


class FakePoiClient:
    def __init__(self, *, health=None, tools=None, responses=None):
        self._health = health or {"status": "ok", "storeReady": True, "sessionReady": True}
        self._tools = tools or ("poi_describe", "poi_get", "poi_lookup")
        self._responses = responses or {}
        self.calls = []
        self.initialized = False

    def health(self):
        return self._health

    def initialize(self):
        self.initialized = True
        return {"name": "poi-nanodesu-mcp"}

    def list_tools(self):
        return tuple(SimpleNamespace(name=name) for name in self._tools)

    def call_read_tool(self, name, arguments):
        self.calls.append((name, arguments))
        key = (name, arguments.get("path"))
        if key not in self._responses:
            raise PoiMcpError(f"read-only path is unavailable: {key[1]}")
        value = self._responses[key]
        if isinstance(value, Exception):
            raise value
        return value


def tool_result(value):
    return {"isError": False, "structuredContent": value}


def valid_responses():
    return {
        ("poi_get", "ext.poi-plugin-kancollebot-session-evidence._.evidence"): tool_result({
            "bridgeInstanceId": "test-instance",
            "responsePath": "api_port/port",
            "responseOk": True,
            "observedAt": "2026-10-22T11:59:30Z",
            "invalidated": False,
        }),
        ("poi_describe", "info.fleets"): tool_result({
            "path": "info.fleets", "kind": "array", "total": 1,
            "sampleFields": ["api_id", "api_ship", "api_mission"],
        }),
        ("poi_get", "info.fleets"): tool_result({
            "kind": "array", "total": 1,
            "items": [{"api_id": 1, "api_ship": [101, -1, -1, -1, -1, -1],
                       "api_mission": [0, 0, 0, 0]}],
        }),
        ("poi_get", "info.ships"): tool_result({
            "kind": "object-map", "total": 1,
            "items": {"101": {"api_ship_id": 11, "api_nowhp": 10,
                              "api_maxhp": 10, "api_fuel": 10, "api_bull": 10}},
        }),
        ("poi_get", "const.$ships"): tool_result({
            "kind": "object-map", "total": 1,
            "items": {"11": {"api_fuel": 10, "api_bull": 10}},
        }),
    }


class PoiExpeditionReaderTests(unittest.TestCase):
    def make_reader(self, **kwargs):
        client = FakePoiClient(**kwargs)
        reader = PoiExpeditionReader(
            client, clock=lambda: NOW, enable_session_evidence=True
        )
        return reader, client

    def test_reads_and_normalizes_fleets_using_read_tools_only(self):
        reader, client = self.make_reader(responses=valid_responses())

        state = reader.read_fleets()

        self.assertEqual(state.observed_at, NOW)
        self.assertEqual(state.source, "poi-redux:info.fleets+info.ships")
        self.assertEqual(len(state.payload), 1)
        self.assertEqual(state.payload[0].fleet_id, 1)
        self.assertEqual(state.payload[0].ship_ids, (101,))
        self.assertIsNone(state.payload[0].mission_id)
        self.assertTrue(state.payload[0].fuel_ready)
        self.assertTrue(state.payload[0].ammo_ready)
        self.assertTrue(client.initialized)
        self.assertEqual(
            client.calls,
            [
                ("poi_get", {"path": "ext.poi-plugin-kancollebot-session-evidence._.evidence"}),
                ("poi_describe", {"path": "info.fleets"}),
                ("poi_get", {"path": "info.fleets"}),
                ("poi_get", {"path": "info.ships"}),
                ("poi_get", {"path": "const.$ships"}),
            ],
        )
        self.assertTrue(all(name in {"poi_describe", "poi_get", "poi_lookup"}
                            for name, _ in client.calls))
        self.assertEqual(client.calls[0][1]["path"],
                         "ext.poi-plugin-kancollebot-session-evidence._.evidence")

    def test_accepts_json_text_tool_payload(self):
        responses = valid_responses()
        for key, response in tuple(responses.items()):
            responses[key] = {
                "isError": False,
                "content": [{"type": "text", "text": json.dumps(response["structuredContent"]) }],
            }
        reader, _ = self.make_reader(responses=responses)

        state = reader.read_fleets()

        self.assertEqual(state.payload[0].ship_ids, (101,))

    def test_session_reads_are_disabled_by_default(self):
        client = FakePoiClient(responses=valid_responses())
        reader = PoiExpeditionReader(client, clock=lambda: NOW)

        with self.assertRaisesRegex(PoiMcpError, "disabled until local bridge verification"):
            reader.read_fleets()

        self.assertEqual(client.calls, [])
        self.assertFalse(client.initialized)

    def test_missing_session_evidence_fails_before_fleet_reads(self):
        responses = valid_responses()
        del responses[("poi_get", "ext.poi-plugin-kancollebot-session-evidence._.evidence")]
        reader, client = self.make_reader(responses=responses)

        with self.assertRaisesRegex(PoiMcpError, "session evidence"):
            reader.read_fleets()

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][1]["path"],
                         "ext.poi-plugin-kancollebot-session-evidence._.evidence")

    def test_stale_session_evidence_fails_before_fleet_reads(self):
        responses = valid_responses()
        responses[("poi_get", "ext.poi-plugin-kancollebot-session-evidence._.evidence")] = tool_result({
            "bridgeInstanceId": "test-instance",
            "responsePath": "api_port/port",
            "responseOk": True,
            "observedAt": "2026-10-22T11:57:59Z",
            "invalidated": False,
        })
        reader, client = self.make_reader(responses=responses)

        with self.assertRaisesRegex(PoiMcpError, "evidence rejected: session evidence is stale"):
            reader.read_fleets()

        self.assertEqual(len(client.calls), 1)
        self.assertIn("session-evidence", client.calls[0][1]["path"])

    def test_unexpected_session_evidence_fields_fail_closed(self):
        responses = valid_responses()
        value = responses[("poi_get", "ext.poi-plugin-kancollebot-session-evidence._.evidence")]
        value["structuredContent"]["fleetData"] = {"secret": "not-allowed"}
        reader, client = self.make_reader(responses=responses)

        with self.assertRaisesRegex(PoiMcpError, "unexpected fields"):
            reader.read_fleets()

        self.assertEqual(len(client.calls), 1)

    def test_unhealthy_bridge_fails_before_initialization(self):
        reader, client = self.make_reader(health={"status": "error", "storeReady": False})

        with self.assertRaisesRegex(PoiMcpError, "unhealthy"):
            reader.read_fleets()

        self.assertFalse(client.initialized)
        self.assertEqual(client.calls, [])

    def test_unverified_game_session_blocks_cached_state(self):
        reader, client = self.make_reader(
            health={"status": "ok", "storeReady": True}
        )

        with self.assertRaisesRegex(PoiMcpError, "session evidence unavailable"):
            reader.read_fleets()

        self.assertTrue(client.initialized)
        self.assertEqual(len(client.calls), 1)

    def test_false_game_session_blocks_state(self):
        reader, client = self.make_reader(
            health={"status": "ok", "storeReady": True, "sessionReady": False}
        )

        with self.assertRaisesRegex(PoiMcpError, "session evidence unavailable"):
            reader.read_fleets()

        self.assertTrue(client.initialized)
        self.assertEqual(len(client.calls), 1)

    def test_missing_read_tool_fails_closed(self):
        reader, client = self.make_reader(tools=("poi_describe",))

        with self.assertRaisesRegex(PoiMcpError, "missing required read-only tools"):
            reader.read_fleets()

        self.assertTrue(client.initialized)
        self.assertEqual(client.calls, [])

    def test_truncated_fleet_collection_fails_closed(self):
        responses = valid_responses()
        responses[("poi_get", "info.fleets")] = tool_result({
            "kind": "array", "truncated": True, "items": [],
        })
        reader, _ = self.make_reader(responses=responses)

        with self.assertRaisesRegex(PoiMcpError, "truncated"):
            reader.read_fleets()

    def test_missing_referenced_ship_fails_closed(self):
        responses = valid_responses()
        responses[("poi_get", "info.ships")] = tool_result({
            "kind": "object-map", "items": {},
        })
        reader, _ = self.make_reader(responses=responses)

        with self.assertRaisesRegex(PoiMcpError, "missing ship"):
            reader.read_fleets()

    def test_tool_level_error_is_not_accepted(self):
        responses = valid_responses()
        responses[("poi_get", "info.fleets")] = {
            "isError": True, "content": [{"type": "text", "text": "store not ready"}],
        }
        reader, _ = self.make_reader(responses=responses)

        with self.assertRaisesRegex(PoiMcpError, "tool-level error"):
            reader.read_fleets()

    def test_ambiguous_text_payload_fails_closed(self):
        responses = valid_responses()
        responses[("poi_describe", "info.fleets")] = {
            "isError": False,
            "content": [
                {"type": "text", "text": '{"kind":"array"}'},
                {"type": "text", "text": '{"kind":"array"}'},
            ],
        }
        reader, _ = self.make_reader(responses=responses)

        with self.assertRaisesRegex(PoiMcpError, "ambiguous text payload"):
            reader.read_fleets()


if __name__ == "__main__":
    unittest.main()
