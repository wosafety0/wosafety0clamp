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


def fleet(fid, mission=(0, 0, 0, 0), ships=(1, 2, 3), supply=None):
    record = {"id": fid, "name": f"第{fid}艦隊", "mission": list(mission), "ships": list(ships)}
    if supply is not None:
        record["supply"] = list(supply)
    return record


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

    def test_reads_fleet2_supply_fields_and_marks_full_supply(self):
        supply = [
            {"id": 1, "fuel": 10, "ammo": 20, "max_fuel": 10, "max_ammo": 20},
            {"id": 4, "fuel": 11, "ammo": 21, "max_fuel": 11, "max_ammo": 21},
            {"id": 8, "fuel": 12, "ammo": 22, "max_fuel": 12, "max_ammo": 22},
        ]
        states = read_poi_fleet_states(FakeTransport([
            fleet(1),
            fleet(2, ships=(1, 4, 8), supply=supply),
            fleet(3),
        ]))
        fleet2 = states[1]
        self.assertEqual([item.ship_id for item in fleet2.supply], [1, 4, 8])
        self.assertTrue(all(item.is_fully_supplied for item in fleet2.supply))

    def test_reads_fleet3_supply_and_composition_fields(self):
        supply = [
            {"id": 6, "level": 2, "ship_type": 2, "fuel": 8, "ammo": 12, "max_fuel": 10, "max_ammo": 15},
            {"id": 52, "level": 1, "ship_type": 2, "fuel": 10, "ammo": 15, "max_fuel": 10, "max_ammo": 15},
            {"id": 32, "level": 1, "ship_type": 2, "fuel": 9, "ammo": 15, "max_fuel": 10, "max_ammo": 15},
            {"id": 63, "level": 1, "ship_type": 2, "fuel": 10, "ammo": 14, "max_fuel": 10, "max_ammo": 15},
        ]
        states = read_poi_fleet_states(FakeTransport([
            fleet(1),
            fleet(2),
            fleet(3, ships=(6, 52, 32, 63, -1, -1), supply=supply),
        ]))
        fleet3 = states[2]
        self.assertEqual([item.ship_id for item in fleet3.supply], [6, 52, 32, 63])
        self.assertEqual([item.level for item in fleet3.supply], [2, 1, 1, 1])
        self.assertEqual([item.ship_type for item in fleet3.supply], [2, 2, 2, 2])
        self.assertEqual(sum(item.is_fully_supplied for item in fleet3.supply), 1)

    def test_rejects_zero_ship_level(self):
        supply = [{"id": 1, "level": 0, "ship_type": 2}]
        with self.assertRaisesRegex(CdpFleetStateError, "must be positive"):
            read_poi_fleet_states(FakeTransport([
                fleet(1), fleet(2, ships=(1,), supply=supply), fleet(3),
            ]))

    def test_missing_supply_values_are_not_treated_as_fully_supplied(self):
        supply = [{"id": 1, "fuel": 10, "ammo": None, "max_fuel": 10, "max_ammo": 20}]
        states = read_poi_fleet_states(FakeTransport([
            fleet(1),
            fleet(2, ships=(1,), supply=supply),
            fleet(3),
        ]))
        self.assertFalse(states[1].supply[0].is_fully_supplied)

    def test_rejects_supply_records_for_non_member_ships(self):
        supply = [{"id": 99, "fuel": 10, "ammo": 20, "max_fuel": 10, "max_ammo": 20}]
        with self.assertRaisesRegex(CdpFleetStateError, "not a unique fleet member"):
            read_poi_fleet_states(FakeTransport([
                fleet(1),
                fleet(2, ships=(1,), supply=supply),
                fleet(3),
            ]))

    def test_rejects_incomplete_nonempty_supply_snapshot(self):
        supply = [{"id": 1, "fuel": 10, "ammo": 20, "max_fuel": 10, "max_ammo": 20}]
        with self.assertRaisesRegex(CdpFleetStateError, "supply snapshot is incomplete"):
            read_poi_fleet_states(FakeTransport([
                fleet(1),
                fleet(2, ships=(1, 4), supply=supply),
                fleet(3),
            ]))

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
