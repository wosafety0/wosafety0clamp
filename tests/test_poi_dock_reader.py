"""Offline contract tests for the read-only Poi dock adapter."""

from __future__ import annotations

import json
import unittest

from kancollebot.adapters.poi.dock_reader import PoiDockReadError, read_dock_and_fleets
from kancollebot.adapters.poi.mcp_client import McpTool

DESTROYER = 2
LIGHT_CRUISER = 3


def payloads():
    return {
        "info.fleets": {
            "kind": "array", "total": 1, "returned": 1, "truncated": False,
            "items": [{"api_id": 3, "api_ship": [101, 102, -1, -1, -1, -1]}],
        },
        "info.ships": {
            "kind": "object-map", "total": 4, "returned": 4, "truncated": False,
            "items": {
                "101": {"api_id": 101, "api_ship_id": 11, "api_lv": 12},
                "102": {"api_id": 102, "api_ship_id": 12, "api_lv": 4},
                "103": {"api_id": 103, "api_ship_id": 13, "api_lv": 7},
                "104": {"api_id": 104, "api_ship_id": 14, "api_lv": 2},
            },
        },
        "lookup": {
            "11": {"api_stype": DESTROYER},
            "12": {"api_stype": DESTROYER},
            "13": {"api_stype": LIGHT_CRUISER},
            "14": {"api_stype": DESTROYER},
        },
    }


class FakeClient:
    def __init__(self, *, data=None, store_ready=True, tools=("poi_get", "poi_lookup")):
        self.data = data or payloads()
        self.store_ready = store_ready
        self.tools = tools
        self.calls = []

    def health(self):
        return {"name": "poi-nanodesu-mcp", "status": "ok", "storeReady": self.store_ready}

    def initialize(self):
        return {"name": "fake-poi"}

    def list_tools(self):
        return tuple(McpTool(name) for name in self.tools)

    def call_read_tool(self, name, arguments):
        self.calls.append((name, arguments))
        payload = self.data["lookup"] if name == "poi_lookup" else self.data[arguments["path"]]
        return {"content": [{"type": "text", "text": json.dumps(payload)}]}


class DockReaderTests(unittest.TestCase):
    def read(self, **kwargs):
        client = FakeClient(**kwargs)
        return client, read_dock_and_fleets(client=client)

    def test_reads_fleet_composition_and_unassigned_dock(self):
        _, reading = self.read()
        self.assertEqual(reading.ship_count, 4)
        self.assertEqual([ship.ship_id for ship in reading.dock], [103, 104])
        composition = reading.composition(3)
        self.assertEqual(composition.ship_ids, (101, 102))
        self.assertEqual(composition.ship_levels, (12, 4))
        self.assertEqual(composition.ship_types, (DESTROYER, DESTROYER))

    def test_lookup_requests_ship_type_for_every_instance(self):
        client, _ = self.read()
        lookups = [arguments for name, arguments in client.calls if name == "poi_lookup"]
        self.assertEqual(len(lookups), 1)
        self.assertEqual(lookups[0]["select"], ["api_stype"])
        self.assertEqual(lookups[0]["ids"], [11, 12, 13, 14])

    def test_unknown_fleet_composition_raises(self):
        _, reading = self.read()
        with self.assertRaises(PoiDockReadError):
            reading.composition(9)

    def test_truncated_ship_collection_fails_closed(self):
        data = payloads()
        data["info.ships"]["truncated"] = True
        with self.assertRaisesRegex(PoiDockReadError, "truncated"):
            self.read(data=data)

    def test_unhealthy_store_fails_closed(self):
        with self.assertRaisesRegex(PoiDockReadError, "not ready"):
            self.read(store_ready=False)

    def test_missing_read_tool_fails_closed(self):
        with self.assertRaisesRegex(PoiDockReadError, "missing required read-only tools"):
            self.read(tools=("poi_get",))

    def test_missing_master_record_fails_closed(self):
        data = payloads()
        del data["lookup"]["13"]
        with self.assertRaisesRegex(PoiDockReadError, "omitted master ship records"):
            self.read(data=data)

    def test_fleet_referencing_missing_ship_fails_closed(self):
        data = payloads()
        del data["info.ships"]["items"]["102"]
        data["info.ships"]["total"] = data["info.ships"]["returned"] = 3
        with self.assertRaisesRegex(PoiDockReadError, "missing ship 102"):
            self.read(data=data)

    def test_missing_level_on_fleet_ship_fails_closed(self):
        data = payloads()
        data["info.ships"]["items"]["101"]["api_lv"] = None
        with self.assertRaisesRegex(PoiDockReadError, "api_lv"):
            self.read(data=data)


if __name__ == "__main__":
    unittest.main()
