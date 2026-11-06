"""Offline tests for the passive port snapshot consistency validator."""
import copy
import unittest
from datetime import datetime, timedelta, timezone

from kancollebot.adapters.poi.port_freshness import port_snapshot_matches_current_state


NOW = datetime(2026, 10, 29, 12, 0, 5, tzinfo=timezone.utc)


def sample_state():
    fleets = [
        {
            "api_id": 1,
            "api_name": "First Fleet",
            "api_mission": [0, 0, 0, 0],
            "api_ship": [101, -1],
        }
    ]
    ships = {
        "101": {
            "api_id": 101,
            "api_ship_id": 11,
            "api_nowhp": 10,
            "api_maxhp": 10,
            "api_fuel": 10,
            "api_bull": 10,
            "api_cond": 49,
            "unrelated_field": "not part of the compact snapshot",
        }
    }
    snapshot = {
        "schemaVersion": 1,
        "verificationStatus": "CAPTURED_UNDER_STATICALLY_REVIEWED_PROBE",
        "source": {
            "kind": "cdp-network-response",
            "path": "/kcsapi/api_port/port",
            "host": "w14h.kancolle-server.com",
            "httpStatus": 200,
            "responseReceivedAt": "2026-10-29T12:00:00.000Z",
            "bodyCapturedAt": "2026-10-29T12:00:01.000Z",
            "fromDiskCache": False,
            "fromServiceWorker": False,
            "fromPrefetchCache": False,
            "responseBodySha256": "a" * 64,
            "responseBodyBytes": 1024,
        },
        "data": {
            "api_deck_port": [
                {
                    "api_id": 1,
                    "api_name": "First Fleet",
                    "api_mission": [0, 0, 0, 0],
                    "api_ship": [101, -1],
                }
            ],
            "api_ship": {
                "101": {
                    "api_id": 101,
                    "api_ship_id": 11,
                    "api_nowhp": 10,
                    "api_maxhp": 10,
                    "api_fuel": 10,
                    "api_bull": 10,
                    "api_cond": 49,
                }
            },
        },
        "summary": {"fleetCount": 1, "shipCount": 1},
        "safety": {
            "gameInputSent": False,
            "gameApiRequestInitiated": False,
            "runtimeEvaluateUsed": False,
            "fullRawResponseSaved": False,
            "productionFreshnessGateChanged": False,
        },
    }
    return snapshot, fleets, ships


class PortFreshnessTests(unittest.TestCase):
    def validate(self, snapshot, fleets, ships, *, now=NOW, max_age=timedelta(seconds=30)):
        return port_snapshot_matches_current_state(
            snapshot, fleets, ships, now=now, max_age=max_age
        )

    def test_accepts_recent_matching_non_cache_capture(self):
        snapshot, fleets, ships = sample_state()
        self.assertTrue(self.validate(snapshot, fleets, ships))

    def test_rejects_stale_and_future_dated_capture(self):
        snapshot, fleets, ships = sample_state()
        self.assertFalse(
            self.validate(snapshot, fleets, ships, now=NOW + timedelta(minutes=2))
        )
        snapshot, fleets, ships = sample_state()
        snapshot["source"]["bodyCapturedAt"] = "2026-10-29T12:00:06.000Z"
        self.assertFalse(self.validate(snapshot, fleets, ships))
        snapshot, fleets, ships = sample_state()
        snapshot["source"]["responseReceivedAt"] = "2026-10-29T11:59:00.000Z"
        self.assertFalse(self.validate(snapshot, fleets, ships))

    def test_rejects_mutable_fleet_and_ship_differences(self):
        snapshot, fleets, ships = sample_state()
        changed_fleets = copy.deepcopy(fleets)
        changed_fleets[0]["api_mission"] = [1, 2, 3, 4]
        self.assertFalse(self.validate(snapshot, changed_fleets, ships))
        changed_ships = copy.deepcopy(ships)
        changed_ships["101"]["api_fuel"] = 9
        self.assertFalse(self.validate(snapshot, fleets, changed_ships))

    def test_rejects_bad_route_status_cache_and_safety_flags(self):
        for path, value in (
            ("path", "/kcsapi/api_get_member/require_info"),
            ("httpStatus", 304),
            ("fromDiskCache", True),
            ("fromServiceWorker", True),
            ("fromPrefetchCache", True),
        ):
            with self.subTest(field=path):
                snapshot, fleets, ships = sample_state()
                snapshot["source"][path] = value
                self.assertFalse(self.validate(snapshot, fleets, ships))
        snapshot, fleets, ships = sample_state()
        snapshot["safety"]["gameApiRequestInitiated"] = True
        self.assertFalse(self.validate(snapshot, fleets, ships))

    def test_rejects_incomplete_or_inconsistent_payload(self):
        for mutate in (
            lambda s: s["data"]["api_ship"]["101"].pop("api_fuel"),
            lambda s: s["summary"].update(shipCount=2),
            lambda s: s["data"]["api_deck_port"][0].update(api_ship=[999]),
            lambda s: s["source"].update(responseBodySha256="not-a-digest"),
        ):
            with self.subTest(mutate=mutate):
                snapshot, fleets, ships = sample_state()
                mutate(snapshot)
                self.assertFalse(self.validate(snapshot, fleets, ships))

    def test_rejects_duplicate_or_missing_current_records(self):
        snapshot, fleets, ships = sample_state()
        duplicate = copy.deepcopy(fleets)
        duplicate.append(copy.deepcopy(duplicate[0]))
        self.assertFalse(self.validate(snapshot, duplicate, ships))
        self.assertFalse(self.validate(snapshot, fleets, {}))

    def test_rejects_naive_clock_and_negative_age_limit(self):
        snapshot, fleets, ships = sample_state()
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            self.validate(snapshot, fleets, ships, now=datetime(2026, 10, 29, 12, 0, 5))
        with self.assertRaisesRegex(ValueError, "must not be negative"):
            self.validate(snapshot, fleets, ships, max_age=timedelta(seconds=-1))


if __name__ == "__main__":
    unittest.main()
