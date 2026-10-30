from __future__ import annotations

import json
import unittest

from kancollebot.adapters.poi.cdp_fleet_state import CdpFleetStateError, read_poi_fleet_states
from kancollebot.adapters.poi.cdp_websocket import READ_ONLY_FLEET_SNAPSHOT_EXPRESSION


class FakeTransport:
    def __init__(self, value):
        self.value = value
        self.calls = []

    def send(self, method, params):
        self.calls.append((method, params))
        return {"result": {"type": "string", "value": json.dumps(self.value)}}


def fleet(fid, mission=(0, 0, 0, 0), ships=(1, 2, 3)):
    return {"id": fid, "name": f"第{fid}艦隊", "mission": list(mission), "ships": list(ships)}


class CdpFleetStateTests(unittest.TestCase):
    def test_reads_and_sorts_three_fleet_snapshot_using_fixed_expression(self):
        transport = FakeTransport([
            fleet(3, ships=(63, 6, 52, 32, -1, -1)),
            fleet(1, ships=(13, 73, 70, 10, 40, 62)),
            fleet(2, mission=(1, 3, 1793354078676, 0), ships=(1, 4, 8, 25, -1, -1)),
        ])
        states = read_poi_fleet_states(transport)
        self.assertEqual([state.fleet_id for state in states], [1, 2, 3])
        self.assertEqual(states[1].mission_status, 1)
        self.assertEqual(states[1].mission_id, 3)
        self.assertEqual(states[1].final_time_ms, 1793354078676)
        self.assertEqual(transport.calls, [("Runtime.evaluate", {
            "expression": READ_ONLY_FLEET_SNAPSHOT_EXPRESSION,
            "returnByValue": True,
        })])

    def test_rejects_duplicate_fleet_ids(self):
        with self.assertRaisesRegex(CdpFleetStateError, "unique"):
            read_poi_fleet_states(FakeTransport([fleet(1), fleet(1), fleet(3)]))

    def test_rejects_invalid_mission_shape(self):
        bad = fleet(2)
        bad["mission"] = [0, 0, 0]
        with self.assertRaisesRegex(CdpFleetStateError, "exactly four"):
            read_poi_fleet_states(FakeTransport([fleet(1), bad, fleet(3)]))

    def test_rejects_boolean_mission_slot(self):
        bad = fleet(2, mission=(True, 3, 100, 0))
        with self.assertRaisesRegex(CdpFleetStateError, "safe integer"):
            read_poi_fleet_states(FakeTransport([fleet(1), bad, fleet(3)]))

    def test_rejects_noncontiguous_fleet_ids(self):
        with self.assertRaisesRegex(CdpFleetStateError, "contiguous"):
            read_poi_fleet_states(FakeTransport([fleet(1), fleet(2), fleet(4)]))

    def test_rejects_malformed_json(self):
        class BadTransport:
            def send(self, method, params):
                return {"result": {"type": "string", "value": "not json"}}
        with self.assertRaisesRegex(CdpFleetStateError, "valid JSON"):
            read_poi_fleet_states(BadTransport())


if __name__ == "__main__":
    unittest.main()
