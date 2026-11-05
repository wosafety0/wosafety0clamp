"""Deterministic tests for the read-only Poi equipment/stat evidence parser.

No network, CDP, SQLite or game input is used here. Every case feeds plain
dicts, exactly like the MCP bridge returns them.
"""
import unittest

from kancollebot.adapters.poi.requirement_evidence import (
    EQUIP_INSTANCE_FIELDS,
    MAX_EQUIPMENT_INSTANCE_IDS,
    SHIP_DETAIL_FIELDS,
    STAT_KEYS,
    build_equipment_where_clause,
    equipment_instance_ids_for_fleets,
    parse_requirement_evidence,
)
from kancollebot.expeditions.expedition_rule_data import STAT_FIELDS

DRUM = 75


def detail(ship_id, *, slots=(-1, -1, -1, -1, -1), ex=0,
           karyoku=(20, 30), taiku=(10, 20), taisen=(15, 25),
           sakuteki=(5, 15), ndock=0, **overrides):
    value = {
        "api_id": ship_id,
        "api_slot": list(slots),
        "api_slot_ex": ex,
        "api_slotnum": 4,
        "api_karyoku": list(karyoku),
        "api_taiku": list(taiku),
        "api_taisen": list(taisen),
        "api_sakuteki": list(sakuteki),
        "api_ndock_time": ndock,
    }
    value.update(overrides)
    return value


def equip(instance_id, master_id):
    return {"api_id": instance_id, "api_slotitem_id": master_id}


def fleet(fleet_id, ship_ids):
    return {"api_id": fleet_id, "api_ship": list(ship_ids) + [-1] * (6 - len(ship_ids))}


class EvidenceParsingTests(unittest.TestCase):
    def setUp(self):
        self.details = {
            "101": detail(101, slots=(9001, 9002, -1, -1, -1)),
            "102": detail(102, slots=(9003, -1, -1, -1, -1), ex=9004),
            "103": detail(103),
        }
        self.equips = {
            "9001": equip(9001, DRUM),
            "9002": equip(9002, DRUM),
            "9003": equip(9003, 68),
            "9004": equip(9004, DRUM),
        }
        self.fleets = [fleet(2, (101, 102, 103))]
        self.conditions = {
            "101": {"api_cond": 49},
            "102": {"api_cond": 52},
            "103": {"api_cond": 49},
        }

    def parse(self, **overrides):
        kwargs = dict(
            fleets=self.fleets,
            ship_details=self.details,
            equip_instances=self.equips,
            conditions=self.conditions,
        )
        kwargs.update(overrides)
        return parse_requirement_evidence(**kwargs)

    def test_drums_are_resolved_from_instance_ids_to_master_ids(self):
        evidence = self.parse()[2]
        self.assertIsNotNone(evidence)
        self.assertEqual(evidence.equipment_by_ship[101], (DRUM, DRUM))
        # The ex slot is a real equipment slot and must not be dropped.
        self.assertEqual(evidence.equipment_by_ship[102], (68, DRUM))
        self.assertEqual(evidence.equipment_by_ship[103], ())

    def test_stat_totals_use_displayed_values_including_equipment(self):
        evidence = self.parse()[2]
        self.assertEqual(evidence.total_stats["firepower"], 60)
        self.assertEqual(evidence.total_stats["anti_air"], 30)
        self.assertEqual(evidence.total_stats["asw"], 45)
        self.assertEqual(evidence.total_stats["los"], 15)

    def test_stat_keys_match_the_rule_table_alphabet(self):
        self.assertEqual(STAT_KEYS, frozenset(STAT_FIELDS))

    def test_conditions_and_dock_state_are_carried(self):
        self.details["102"] = detail(102, ndock=1234)
        evidence = self.parse()[2]
        self.assertEqual(evidence.conditions[101], 49)
        self.assertEqual(evidence.conditions[102], 52)
        self.assertEqual(evidence.in_dock_ship_ids, (102,))

    def test_projections_stay_separate_and_narrow(self):
        # Widening the port-witness projection truncates a real 239-ship roster,
        # so the two field sets must never be merged into one query.
        self.assertNotIn("api_nowhp", SHIP_DETAIL_FIELDS)
        self.assertNotIn("api_lv", SHIP_DETAIL_FIELDS)
        self.assertIn("api_slot", SHIP_DETAIL_FIELDS)
        self.assertEqual(EQUIP_INSTANCE_FIELDS, ("api_id", "api_slotitem_id"))

    def test_unresolved_equipment_instance_fails_that_fleet_closed(self):
        broken = dict(self.equips)
        del broken["9002"]
        self.assertIsNone(self.parse(equip_instances=broken)[2])

    def test_malformed_stat_pair_fails_that_fleet_closed(self):
        self.details["101"] = detail(101, karyoku=[999, 30])
        self.assertIsNone(self.parse()[2])

    def test_missing_ship_detail_fails_that_fleet_closed(self):
        details = dict(self.details)
        del details["103"]
        self.assertIsNone(self.parse(ship_details=details)[2])

    def test_wrong_detail_identity_fails_closed(self):
        wrong = detail(999, slots=(9001, -1, -1, -1, -1))
        self.details["101"] = wrong
        self.assertIsNone(self.parse()[2])

    def test_bad_slot_value_fails_closed(self):
        self.details["101"] = detail(101, slots=(-7, -1, -1, -1, -1))
        self.assertIsNone(self.parse()[2])

    def test_empty_string_where_clause_when_nothing_equipped(self):
        ids = equipment_instance_ids_for_fleets(
            [fleet(2, (103,))], {"103": detail(103)}
        )
        self.assertEqual(ids, ())
        self.assertEqual(build_equipment_where_clause(ids), "")

    def test_where_clause_is_deterministic_and_integer_only(self):
        details = {
            "101": detail(101, slots=(9002, 9001, -1, -1, -1)),
            "102": detail(102, slots=(9003, -1, -1, -1, -1)),
            "103": detail(103),
        }
        ids = equipment_instance_ids_for_fleets(self.fleets, details)
        self.assertEqual(ids, (9001, 9002, 9003))
        self.assertEqual(
            build_equipment_where_clause(ids),
            "api_id = 9001 or api_id = 9002 or api_id = 9003",
        )
        for bad in ("1 or 1", True, -1, 1.5, 2 ** 60):
            with self.subTest(bad=bad):
                with self.assertRaises(Exception):
                    build_equipment_where_clause((bad,))

    def test_instance_id_bound_is_enforced(self):
        with self.assertRaises(Exception):
            build_equipment_where_clause(tuple(range(1, MAX_EQUIPMENT_INSTANCE_IDS + 2)))

    def test_one_bad_fleet_does_not_poison_another_fleet(self):
        fleets = [fleet(2, (101, 102, 103)), fleet(3, (103,))]
        self.details["101"] = detail(101, taiku="broken")
        result = self.parse(fleets=fleets)
        self.assertIsNone(result[2])
        self.assertIsNotNone(result[3])
        self.assertEqual(result[3].equipment_by_ship[103], ())

    def test_fleets_are_keyed_by_api_id_not_position(self):
        fleets = [fleet(4, (101,)), fleet(2, (103,))]
        result = self.parse(fleets=fleets)
        self.assertEqual(set(result), {2, 4})
        self.assertEqual(result[4].equipment_by_ship[101], (DRUM, DRUM))

    def test_plain_payload_is_json_safe_for_the_evidence_report(self):
        import json

        payload = self.parse()[2].as_plain()
        json.dumps(payload)
        self.assertEqual(payload["equipment_by_ship"]["101"], [DRUM, DRUM])
        self.assertEqual(payload["total_stats"]["firepower"], 60)


if __name__ == "__main__":
    unittest.main()
