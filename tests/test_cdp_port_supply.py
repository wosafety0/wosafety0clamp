"""Tests for the fixed read-only Redux port-supply snapshot reader."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from kancollebot.adapters.poi.cdp_port_supply import (
    CdpPortSupplyError,
    PoiPortSupplySnapshot,
    read_poi_port_supply_snapshot,
)
from kancollebot.adapters.poi.cdp_websocket import (
    READ_ONLY_PORT_SUPPLY_SNAPSHOT_EXPRESSION,
)


def base_payload() -> dict:
    return {
        "fleets": [
            {
                "api_id": 1,
                "api_name": "first",
                "api_mission": [0, 0, 0, 0],
                "api_ship": [11, 12, -1, -1, -1, -1],
            },
            {
                "api_id": 2,
                "api_name": "second",
                "api_mission": [0, 0, 0, 0],
                "api_ship": [21, 22],
            },
            {
                "api_id": 3,
                "api_name": "third",
                "api_mission": [0, 0, 0, 0],
                "api_ship": [31, 32],
            },
        ],
        "ships": {
            "11": {
                "api_id": 11, "api_ship_id": 101, "api_nowhp": 30, "api_maxhp": 30,
                "api_fuel": 15, "api_bull": 20, "api_lv": 20, "api_cond": 49,
            },
            "12": {
                "api_id": 12, "api_ship_id": 102, "api_nowhp": 30, "api_maxhp": 30,
                "api_fuel": 15, "api_bull": 20, "api_lv": 20, "api_cond": 49,
            },
            "21": {
                "api_id": 21, "api_ship_id": 101, "api_nowhp": 30, "api_maxhp": 30,
                "api_fuel": 6, "api_bull": 20, "api_lv": 20, "api_cond": 49,
            },
            "22": {
                "api_id": 22, "api_ship_id": 102, "api_nowhp": 30, "api_maxhp": 30,
                "api_fuel": 6, "api_bull": 10, "api_lv": 20, "api_cond": 49,
            },
            "31": {
                "api_id": 31, "api_ship_id": 101, "api_nowhp": 30, "api_maxhp": 30,
                "api_fuel": 6, "api_bull": 10, "api_lv": 20, "api_cond": 49,
            },
            "32": {
                "api_id": 32, "api_ship_id": 102, "api_nowhp": 30, "api_maxhp": 30,
                "api_fuel": 6, "api_bull": 10, "api_lv": 20, "api_cond": 49,
            },
        },
        "master_ships": {
            "101": {"api_id": 101, "api_fuel_max": 15, "api_bull_max": 20, "api_stype": 1},
            "102": {"api_id": 102, "api_fuel_max": 15, "api_bull_max": 20, "api_stype": 1},
        },
        "resources": [5000, 5000, 5000, 5000],
    }


class FakeTransport:
    def __init__(self, value, *, remote_type="string"):
        self.value = value
        self.remote_type = remote_type
        self.calls = []

    def send(self, method, params):
        self.calls.append((method, params))
        return {"result": {"type": self.remote_type, "value": self.value}}


def transport_for(payload, *, remote_type="string"):
    return FakeTransport(json.dumps(payload), remote_type=remote_type)


class CdpPortSupplyTests(unittest.TestCase):
    def read(self, payload, *, clock=None):
        transport = transport_for(payload)
        snapshot = read_poi_port_supply_snapshot(transport, clock=clock)
        return snapshot, transport

    def test_reads_the_fixed_expression_and_returns_validated_snapshot(self):
        payload = base_payload()
        fixed = datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)
        snapshot, transport = self.read(payload, clock=lambda: fixed)

        self.assertEqual(transport.calls[0][0], "Runtime.evaluate")
        self.assertEqual(
            transport.calls[0][1]["expression"], READ_ONLY_PORT_SUPPLY_SNAPSHOT_EXPRESSION
        )
        self.assertIs(transport.calls[0][1]["returnByValue"], True)
        self.assertIsInstance(snapshot, PoiPortSupplySnapshot)
        self.assertEqual([fleet["api_id"] for fleet in snapshot.fleets], [1, 2, 3])
        self.assertEqual(snapshot.resources, [5000, 5000, 5000, 5000])
        self.assertEqual(snapshot.ships["31"]["api_fuel"], 6)
        self.assertEqual(snapshot.master_ships["101"]["api_fuel_max"], 15)
        self.assertEqual(snapshot.observed_at, fixed)

    def test_observed_at_is_taken_after_the_payload_arrives(self):
        order: list[str] = []

        class OrderedTransport(FakeTransport):
            def send(self, method, params):
                order.append("read")
                return super().send(method, params)

        transport = OrderedTransport(json.dumps(base_payload()))
        snapshot = read_poi_port_supply_snapshot(
            transport, clock=lambda: (order.append("clock"), datetime.now(timezone.utc))[1]
        )
        self.assertEqual(order, ["read", "clock"])
        self.assertIsNotNone(snapshot)

    def test_null_payload_fails_closed(self):
        transport = FakeTransport("null")
        with self.assertRaisesRegex(CdpPortSupplyError, "no complete port-supply snapshot"):
            read_poi_port_supply_snapshot(transport)

    def test_non_string_result_fails_closed(self):
        transport = FakeTransport(None, remote_type="object")
        with self.assertRaisesRegex(CdpPortSupplyError, "string port-supply snapshot"):
            read_poi_port_supply_snapshot(transport)

    def test_invalid_json_fails_closed(self):
        transport = FakeTransport("{not json")
        with self.assertRaisesRegex(CdpPortSupplyError, "not valid JSON"):
            read_poi_port_supply_snapshot(transport)

    def test_missing_resources_fails_closed(self):
        payload = base_payload()
        payload["resources"] = [5000]
        with self.assertRaisesRegex(CdpPortSupplyError, "material inventory is incomplete"):
            self.read(payload)

    def test_negative_ship_fuel_fails_closed(self):
        payload = base_payload()
        payload["ships"]["31"]["api_fuel"] = -1
        with self.assertRaisesRegex(CdpPortSupplyError, "fuel"):
            self.read(payload)

    def test_ship_hp_above_maximum_fails_closed(self):
        payload = base_payload()
        payload["ships"]["31"]["api_nowhp"] = 99
        with self.assertRaisesRegex(CdpPortSupplyError, "HP values are out of range"):
            self.read(payload)

    def test_negative_master_fuel_maximum_fails_closed(self):
        payload = base_payload()
        payload["master_ships"]["101"]["api_fuel_max"] = -5
        with self.assertRaisesRegex(CdpPortSupplyError, "max fuel"):
            self.read(payload)

    def test_too_many_fleet_slots_fails_closed(self):
        payload = base_payload()
        payload["fleets"][0]["api_ship"] = [11, 12, 13, 14, 15, 21, 22]
        with self.assertRaisesRegex(CdpPortSupplyError, "ship roster is invalid"):
            self.read(payload)

    def test_mission_without_four_slots_fails_closed(self):
        payload = base_payload()
        payload["fleets"][0]["api_mission"] = [0, 0, 0]
        with self.assertRaisesRegex(CdpPortSupplyError, "four slots"):
            self.read(payload)

    def test_fleet_referencing_an_absent_ship_fails_closed(self):
        payload = base_payload()
        payload["fleets"][1]["api_ship"] = [21, 99]
        with self.assertRaisesRegex(CdpPortSupplyError, "missing from the snapshot"):
            self.read(payload)

    def test_fleet_ship_without_master_record_fails_closed(self):
        payload = base_payload()
        del payload["master_ships"]["102"]
        with self.assertRaisesRegex(CdpPortSupplyError, "master record"):
            self.read(payload)

    def test_non_contiguous_fleet_ids_fail_closed(self):
        payload = base_payload()
        payload["fleets"][0]["api_id"] = 4
        payload["fleets"].sort(key=lambda item: item["api_id"])
        with self.assertRaisesRegex(CdpPortSupplyError, "contiguous"):
            self.read(payload)

    def test_ship_key_mismatch_fails_closed(self):
        payload = base_payload()
        payload["ships"]["31"]["api_id"] = 32
        with self.assertRaisesRegex(CdpPortSupplyError, "does not match its key"):
            self.read(payload)

    def test_boolean_material_value_is_not_an_integer(self):
        payload = base_payload()
        payload["resources"] = [True, 5000, 5000, 5000]
        with self.assertRaisesRegex(CdpPortSupplyError, "material 1"):
            self.read(payload)

    def test_naive_clock_fails_closed(self):
        transport = transport_for(base_payload())
        with self.assertRaisesRegex(CdpPortSupplyError, "timezone-aware"):
            read_poi_port_supply_snapshot(
                transport, clock=lambda: datetime(2026, 11, 5, 9, 0)
            )

    def test_transport_without_send_is_rejected_before_any_read(self):
        with self.assertRaisesRegex(ValueError, "send"):
            read_poi_port_supply_snapshot(object())


if __name__ == "__main__":
    unittest.main()
