from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest

from kancollebot.poi_snapshot import (
    SnapshotValidationError,
    parse_snapshot,
    parse_snapshot_text,
)


NOW = datetime(2026, 10, 23, 2, 0, tzinfo=timezone.utc)


def valid_payload():
    return {
        "schema_version": 1,
        "source": "poi-plugin",
        "observed_at": "2026-10-23T02:00:00Z",
        "fleets": [
            {
                "fleet_id": 1,
                "name": "第1艦隊",
                "ship_ids": [1],
                "expedition_id": None,
                "in_battle": False,
            }
        ],
        "ships": [
            {
                "ship_id": 1,
                "name": "吹雪",
                "hp": 15,
                "max_hp": 15,
                "condition": 49,
                "fuel": 15,
                "ammo": 20,
            }
        ],
        "materials": {"fuel": 100, "ammo": 90, "steel": 80, "bauxite": 70},
    }


class PoiSnapshotTests(unittest.TestCase):
    def test_parses_valid_snapshot_and_preserves_japanese_names(self):
        result = parse_snapshot(valid_payload(), now=NOW)
        self.assertEqual(result.game.fleets[0].name, "第1艦隊")
        self.assertEqual(result.game.ships[0].name, "吹雪")
        self.assertEqual(result.materials.fuel, 100)

    def test_rejects_unsupported_schema_version(self):
        payload = valid_payload()
        payload["schema_version"] = 2
        with self.assertRaisesRegex(SnapshotValidationError, "schema_version"):
            parse_snapshot(payload, now=NOW)

    def test_rejects_boolean_schema_version(self):
        payload = valid_payload()
        payload["schema_version"] = True
        with self.assertRaises(SnapshotValidationError):
            parse_snapshot(payload, now=NOW)

    def test_rejects_float_schema_version(self):
        payload = valid_payload()
        payload["schema_version"] = 1.0
        with self.assertRaises(SnapshotValidationError):
            parse_snapshot(payload, now=NOW)

    def test_rejects_naive_timestamp(self):
        payload = valid_payload()
        payload["observed_at"] = "2026-10-23T02:00:00"
        with self.assertRaisesRegex(SnapshotValidationError, "timezone"):
            parse_snapshot(payload, now=NOW)

    def test_rejects_stale_snapshot(self):
        payload = valid_payload()
        payload["observed_at"] = "2026-10-23T01:00:00Z"
        with self.assertRaisesRegex(SnapshotValidationError, "stale"):
            parse_snapshot(payload, now=NOW)

    def test_rejects_timestamp_too_far_in_future(self):
        payload = valid_payload()
        payload["observed_at"] = "2026-10-23T02:02:00Z"
        with self.assertRaisesRegex(SnapshotValidationError, "future"):
            parse_snapshot(payload, now=NOW)

    def test_rejects_duplicate_ship_ids(self):
        payload = valid_payload()
        payload["ships"].append(deepcopy(payload["ships"][0]))
        with self.assertRaisesRegex(SnapshotValidationError, "duplicate ship IDs"):
            parse_snapshot(payload, now=NOW)

    def test_rejects_duplicate_ship_ids_in_fleet(self):
        payload = valid_payload()
        payload["fleets"][0]["ship_ids"] = [1, 1]
        with self.assertRaisesRegex(SnapshotValidationError, "duplicates"):
            parse_snapshot(payload, now=NOW)

    def test_rejects_fleet_referencing_unknown_ship(self):
        payload = valid_payload()
        payload["fleets"][0]["ship_ids"] = [999]
        with self.assertRaisesRegex(SnapshotValidationError, "unknown ship IDs: 999"):
            parse_snapshot(payload, now=NOW)

    def test_rejects_hp_above_maximum(self):
        payload = valid_payload()
        payload["ships"][0]["hp"] = 16
        with self.assertRaisesRegex(SnapshotValidationError, "hp must be"):
            parse_snapshot(payload, now=NOW)

    def test_rejects_boolean_as_numeric_resource(self):
        payload = valid_payload()
        payload["materials"]["fuel"] = True
        with self.assertRaisesRegex(SnapshotValidationError, "materials.fuel"):
            parse_snapshot(payload, now=NOW)

    def test_absent_optional_resources_remain_unknown(self):
        payload = valid_payload()
        payload.pop("materials")
        payload["ships"][0]["fuel"] = None
        result = parse_snapshot(payload, now=NOW)
        self.assertIsNone(result.materials.fuel)
        self.assertIsNone(result.game.ships[0].fuel)

    def test_rejects_malformed_json(self):
        with self.assertRaisesRegex(SnapshotValidationError, "valid JSON"):
            parse_snapshot_text("{", now=NOW)

    def test_rejects_non_object_json_root(self):
        with self.assertRaisesRegex(SnapshotValidationError, "root must be an object"):
            parse_snapshot_text("[]", now=NOW)

    def test_rejects_naive_clock_argument(self):
        with self.assertRaisesRegex(SnapshotValidationError, "now must include"):
            parse_snapshot(valid_payload(), now=datetime(2026, 10, 23, 2, 0))

    def test_accepts_explicitly_recent_timestamp(self):
        payload = valid_payload()
        payload["observed_at"] = (NOW - timedelta(seconds=90)).isoformat()
        result = parse_snapshot(payload, now=NOW)
        self.assertEqual(result.game.source, "poi-plugin")


if __name__ == "__main__":
    unittest.main()
