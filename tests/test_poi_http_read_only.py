import json
import unittest

from kancollebot.adapters.poi.http_read_only import PoiHttpReadOnlyAdapter
from kancollebot.core.ship_parser import ShipFieldMap


class FakeResponse:
    def __init__(self, body, status=200):
        self._body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, limit=-1):
        return self._body[:limit]


class FakeOpener:
    def __init__(self, payloads):
        self.payloads = payloads
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        endpoint = request.full_url.rsplit("/", 1)[-1]
        payload = self.payloads[endpoint]
        if isinstance(payload, tuple):
            body, status = payload
            return FakeResponse(body, status)
        return FakeResponse(payload)


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


class PoiReadOnlyAdapterTests(unittest.TestCase):
    def setUp(self):
        self.payloads = {
            "fleets": encoded([
                {"api_id": 1, "api_name": "第1艦隊", "api_ship": [13, 73, 70, 10, 40, 62],
                 "api_mission": [0, 0, 0, 0]},
                {"api_id": 2, "api_name": "第2艦隊", "api_ship": [1, 3, -1, -1, -1, -1],
                 "api_mission": [1, 2, 123456, 0]},
            ]),
            "ships": encoded({"1": {}, "3": {}, "10": {}, "13": {}, "40": {}, "62": {}, "70": {}, "73": {}}),
        }
        self.opener = FakeOpener(self.payloads)
        self.adapter = PoiHttpReadOnlyAdapter(opener=self.opener)

    def test_reads_only_fleets_and_ships_with_get(self):
        status = self.adapter.get_status()
        self.assertEqual(status["source"], "poi-http-read-only")
        self.assertEqual(status["fleet_count"], 2)
        self.assertEqual(status["ship_record_count"], 8)
        self.assertEqual([request.get_method() for request, _ in self.opener.requests], ["GET", "GET"])
        self.assertEqual([request.full_url.rsplit("/", 1)[-1] for request, _ in self.opener.requests],
                         ["fleets", "ships"])

    def test_get_observed_states_returns_validated_freshness_metadata(self):
        self.payloads["fleets"] = encoded([
            {"api_id": 1, "api_name": "Fleet", "api_ship": [73, -1, -1, -1, -1, -1],
             "api_mission": [0, 0, 0, 0]}
        ])
        self.payloads["ships"] = encoded({
            "73": {"api_name": "赤城", "api_nowhp": 45, "api_maxhp": 69}
        })

        fleets_state, ships_state = self.adapter.get_observed_states(
            ShipFieldMap(name="api_name", hp="api_nowhp", max_hp="api_maxhp")
        )

        self.assertEqual(fleets_state.source, "poi-http-read-only")
        self.assertEqual(ships_state.source, fleets_state.source)
        self.assertEqual(fleets_state.observed_at, ships_state.observed_at)
        self.assertEqual(fleets_state.payload[0].occupied_ship_ids, (73,))
        self.assertEqual(ships_state.payload[73].name, "赤城")
        self.assertEqual(ships_state.payload[73].hp, 45)
        self.assertEqual([request.get_method() for request, _ in self.opener.requests], ["GET", "GET"])

    def test_get_observed_states_rejects_unknown_ship_schema(self):
        self.payloads["fleets"] = encoded([
            {"api_id": 1, "api_name": "Fleet", "api_ship": [73, -1, -1, -1, -1, -1],
             "api_mission": [0, 0, 0, 0]}
        ])
        self.payloads["ships"] = encoded({"73": {"unverified_name": "赤城"}})

        with self.assertRaisesRegex(ValueError, "missing required fields"):
            self.adapter.get_observed_states(
                ShipFieldMap(name="api_name", hp="api_nowhp", max_hp="api_maxhp")
            )

    def test_read_and_plan_collects_completed_expedition_from_poi_observation(self):
        self.payloads["fleets"] = encoded([
            {"api_id": 2, "api_name": "Fleet", "api_ship": [73, -1, -1, -1, -1, -1],
             "api_mission": [2, 5, 123456, 0]}
        ])
        self.payloads["ships"] = encoded({
            "73": {"api_name": "赤城", "api_nowhp": 45, "api_maxhp": 69}
        })

        fleets_state, ships_state, decision = self.adapter.read_and_plan(
            ShipFieldMap(name="api_name", hp="api_nowhp", max_hp="api_maxhp"),
            mission_by_fleet={2: 5},
        )

        self.assertEqual(fleets_state.source, ships_state.source)
        self.assertEqual(decision.action, "collect")
        self.assertEqual(decision.fleet_id, 2)

    def test_read_and_plan_keeps_dispatch_blocked_without_readiness_evidence(self):
        self.payloads["fleets"] = encoded([
            {"api_id": 2, "api_name": "Fleet", "api_ship": [73, -1, -1, -1, -1, -1],
             "api_mission": [0, 0, 0, 0]}
        ])
        self.payloads["ships"] = encoded({
            "73": {"api_name": "赤城", "api_nowhp": 45, "api_maxhp": 69}
        })

        _, _, decision = self.adapter.read_and_plan(
            ShipFieldMap(name="api_name", hp="api_nowhp", max_hp="api_maxhp"),
            mission_by_fleet={2: 5},
            dispatchable_fleet_ids=(2,),
        )

        self.assertEqual(decision.action, "wait")
        self.assertIn("blocked by readiness gate", decision.reason)

    def test_diagnose_reports_runtime_schema_without_raw_ship_data(self):
        self.payloads["fleets"] = encoded([
            {"api_id": 1, "api_name": "Fleet", "api_ship": [73, -1, -1, -1, -1, -1],
             "api_mission": [0, 0, 0, 0]}
        ])
        self.payloads["ships"] = encoded({
            "73": {"api_name": "PRIVATE_SHIP_NAME", "api_nowhp": 45, "api_maxhp": 69},
            "74": {"api_name": "ANOTHER_PRIVATE_NAME", "api_nowhp": 20, "api_maxhp": 30},
            "13": {"api_name": "THIRD_PRIVATE_NAME", "api_nowhp": 10, "api_maxhp": 10},
            "70": {"api_name": "FOURTH_PRIVATE_NAME", "api_nowhp": 15, "api_maxhp": 20},
            "10": {"api_name": "FIFTH_PRIVATE_NAME", "api_nowhp": 8, "api_maxhp": 10},
            "40": {"api_name": "SIXTH_PRIVATE_NAME", "api_nowhp": 12, "api_maxhp": 15},
            "62": {"api_name": "SEVENTH_PRIVATE_NAME", "api_nowhp": 25, "api_maxhp": 30},
            "1": {"api_name": "EIGHTH_PRIVATE_NAME", "api_nowhp": 9, "api_maxhp": 10},
        })
        report = self.adapter.diagnose()
        serialized = json.dumps(report, ensure_ascii=False)

        self.assertTrue(report["ok"])
        self.assertEqual(report["fleet_count"], 1)
        self.assertEqual(report["ship_record_count"], 8)
        self.assertTrue(report["fleet_ship_references_valid"])
        self.assertTrue(report["known_field_map_candidate"]["all_records_type_and_range_valid"])
        self.assertFalse(report["known_field_map_candidate"]["auto_applied"])
        self.assertIn("api_nowhp", serialized)
        self.assertNotIn("PRIVATE_SHIP_NAME", serialized)
        self.assertNotIn("ANOTHER_PRIVATE_NAME", serialized)
        self.assertNotIn('"73"', serialized)

    def test_diagnose_does_not_accept_partial_or_invalid_hp_mapping(self):
        self.payloads["fleets"] = encoded([
            {"api_id": 1, "api_name": "Fleet", "api_ship": [73, -1, -1, -1, -1, -1],
             "api_mission": [0, 0, 0, 0]}
        ])
        self.payloads["ships"] = encoded({
            "73": {"api_name": "PRIVATE_SHIP_NAME", "api_nowhp": 45, "api_maxhp": 69},
            "74": {"api_name": "ANOTHER_PRIVATE_NAME", "api_nowhp": True, "api_maxhp": 30},
        })
        report = self.adapter.diagnose()
        self.assertFalse(report["known_field_map_candidate"]["all_records_type_and_range_valid"])
        self.assertNotIn("PRIVATE_SHIP_NAME", json.dumps(report))

    def test_preserves_japanese_fleet_name(self):
        status = self.adapter.get_status()
        self.assertEqual(status["fleets"][0].fleet_name, "第1艦隊")

    def test_get_ship_states_uses_explicit_field_map(self):
        self.payloads["ships"] = encoded({
            "73": {"api_name": "赤城", "api_nowhp": 45, "api_maxhp": 69}
        })
        fields = ShipFieldMap(name="api_name", hp="api_nowhp", max_hp="api_maxhp")
        states = self.adapter.get_ship_states(fields)
        self.assertEqual(states[73].name, "赤城")
        self.assertEqual((states[73].hp, states[73].max_hp), (45, 69))
        self.assertEqual([request.get_method() for request, _ in self.opener.requests], ["GET"])
        self.assertEqual(self.opener.requests[0][0].full_url.rsplit("/", 1)[-1], "ships")

    def test_rejects_non_loopback_url(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            PoiHttpReadOnlyAdapter("http://example.com:17777")

    def test_rejects_https_and_url_credentials(self):
        with self.assertRaises(ValueError):
            PoiHttpReadOnlyAdapter("https://127.0.0.1:17777")
        with self.assertRaises(ValueError):
            PoiHttpReadOnlyAdapter("http://user:pass@127.0.0.1:17777")

    def test_rejects_missing_ship_record(self):
        self.payloads["ships"] = encoded({"1": {}, "3": {}})
        with self.assertRaisesRegex(ValueError, "missing ship records"):
            self.adapter.get_status()

    def test_rejects_invalid_utf8(self):
        self.payloads["fleets"] = b"\xff"
        with self.assertRaisesRegex(ValueError, "valid UTF-8"):
            self.adapter.get_status()

    def test_rejects_oversized_response(self):
        adapter = PoiHttpReadOnlyAdapter(opener=self.opener, max_response_bytes=4)
        with self.assertRaisesRegex(ValueError, "size limit"):
            adapter.get_status()

    def test_rejects_redirect_response(self):
        self.payloads["fleets"] = (encoded([]), 302)
        with self.assertRaisesRegex(OSError, "HTTP 302"):
            self.adapter.get_status()


if __name__ == "__main__":
    unittest.main()
