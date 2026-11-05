"""Deterministic tests for the Fleet 2 handoff verification orchestrator.

These lock the audit invariants of ``tools/verify_r3i_fleet2_gate.py``:
only clicks ACTUALLY SENT are counted, the UI action log is append-only and
complete, the target is Fleet 2, and JSON evidence is always serialisable.
No live client state is touched by this module.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType

from tools.verify_r3i_fleet2_gate import (
    FLEET_ID,
    MISSION_ID,
    UNKNOWN_RULE_CONTROL,
    _SUPPLY_CLICK_EVENT_FIELDS,
    UiActionLog,
    _display_path,
    _fleet_brief,
    _snapshot_to_dict,
    count_supply_clicks,
)


class ScopeTests(unittest.TestCase):
    def test_the_handoff_scope_is_fleet_2_mission_1_with_rule_control(self):
        self.assertEqual(FLEET_ID, 2)
        self.assertEqual(MISSION_ID, 1)
        self.assertEqual(UNKNOWN_RULE_CONTROL, 9999)

    def test_the_read_only_probe_evaluates_the_unknown_rule_control(self):
        # The probe always evaluates {mission_id, 9999}; the control must stay.
        source = (
            Path(__file__).resolve().parents[1]
            / "tools" / "verify_r3i_live_gate.py"
        ).read_text(encoding="utf-8")
        self.assertIn("sorted({args.mission_id, 9999})", source)
        self.assertIn('minimum_confidence="cross_checked"', source)


class CountSupplyClicksTests(unittest.TestCase):
    def test_live_run_shape_is_six_clicks(self):
        events = [
            {"event": "home_port_precondition_already_satisfied", "click_count": 0},
            {"event": "ui_refresh_click_sent", "clicks_sent": 1},
            {"event": "ui_refresh_click_sent", "clicks_sent": 2},
            {"event": "ui_refresh_click_sent", "clicks_sent": 3},
            {"event": "ui_refresh_completed", "clicks_sent": 3},
            {"event": "full_supply_preflight_passed", "planned_fleet_wide_clicks": 1},
            {"event": "supply_menu_navigation_click_sent", "click_count": 1},
            {
                "event": "supply_fleet_tab_selected_for_global_button_visibility",
                "click_count": 1,
            },
            {"event": "fleet_wide_supply_button_verified", "click_count": 1},
            {"event": "fleet_wide_supply_click_sent", "click_count": 1},
            {"event": "full_supply_completed_and_verified", "ui_click_count": 1},
        ]
        self.assertEqual(count_supply_clicks(events), 6)

    def test_planned_verification_is_not_a_sent_click(self):
        self.assertEqual(
            count_supply_clicks(
                [{"event": "fleet_wide_supply_button_verified", "click_count": 1}]
            ),
            0,
        )

    def test_aggregate_completion_counters_are_not_clicks(self):
        self.assertEqual(
            count_supply_clicks(
                [{"event": "full_supply_completed_and_verified", "ui_click_count": 99}]
            ),
            0,
        )

    def test_refresh_running_total_is_counted_once(self):
        self.assertEqual(
            count_supply_clicks(
                [
                    {"event": "ui_refresh_click_sent", "clicks_sent": 1},
                    {"event": "ui_refresh_click_sent", "clicks_sent": 2},
                    {"event": "ui_refresh_click_sent", "clicks_sent": 3},
                ]
            ),
            3,
        )

    def test_bool_is_never_a_click_count(self):
        self.assertEqual(
            count_supply_clicks(
                [
                    {"event": "ui_refresh_click_sent", "clicks_sent": True},
                    {"event": "supply_menu_navigation_click_sent", "click_count": False},
                ]
            ),
            0,
        )

    def test_string_counts_are_ignored(self):
        self.assertEqual(
            count_supply_clicks(
                [{"event": "supply_menu_navigation_click_sent", "click_count": "1"}]
            ),
            0,
        )

    def test_empty_log_is_zero(self):
        self.assertEqual(count_supply_clicks([]), 0)

    def test_whitelist_only_contains_sent_click_events(self):
        self.assertTrue(
            all(
                name.endswith("_sent")
                or name == "supply_fleet_tab_selected_for_global_button_visibility"
                for name in _SUPPLY_CLICK_EVENT_FIELDS
            )
        )


class UiActionLogTests(unittest.TestCase):
    def test_log_is_append_only_with_monotonic_sequence_and_timestamps(self):
        log = UiActionLog()
        log.add(phase="A", action="first", target="x", result="ok", clicks_sent=1)
        log.add(phase="B", action="second", target="y", result="stopped")
        self.assertEqual([e["seq"] for e in log.entries], [1, 2])
        for entry in log.entries:
            self.assertIn("timestamp_utc", entry)
            self.assertIn("before_observation", entry)
            self.assertIn("after_observation", entry)
            self.assertIn("page_stable_confirmed", entry)
            self.assertIn("poi_read", entry)
            self.assertIn("note", entry)
            datetime.fromisoformat(entry["timestamp_utc"])

    def test_log_writes_valid_json_lines(self):
        log = UiActionLog()
        log.add(phase="A", action="a", target="t", result="ok")
        log.add(phase="B", action="b", target="t", result="ok", page_stable=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "ui.jsonl"
            log.write(path)
            lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["phase"], "A")
        self.assertEqual(json.loads(lines[1])["page_stable_confirmed"], True)

    def test_log_serialises_nested_observations(self):
        log = UiActionLog()
        log.add(
            phase="A", action="snapshot", target="fleet 2",
            after={"ship_ids": [1, 2], "fuel_ready": False},
            result="captured",
        )
        json.dumps(log.entries)  # must not raise


@dataclass(frozen=True)
class _FakeRecord:
    ship_ids: tuple[int, ...]
    equipment_by_ship: object
    total_stats: object
    conditions: object
    in_dock_ship_ids: tuple[int, ...]
    return_at: object


class JsonShapeTests(unittest.TestCase):
    def test_mappingproxy_and_datetime_are_json_safe(self):
        stamp = datetime(2026, 11, 5, 15, 43, 14, tzinfo=timezone.utc)
        record = _FakeRecord(
            ship_ids=(1, 2),
            equipment_by_ship=MappingProxyType({1: (75, 75)}),
            total_stats=MappingProxyType({"firepower": 92}),
            conditions=MappingProxyType({1: 49}),
            in_dock_ship_ids=(),
            return_at=stamp,
        )
        data = _snapshot_to_dict(record)
        self.assertEqual(data["equipment_by_ship"], {1: (75, 75)})
        self.assertEqual(data["total_stats"], {"firepower": 92})
        self.assertEqual(data["return_at"], stamp.isoformat())
        encoded = json.dumps(data)
        self.assertIn("2026-11-05T15:43:14+00:00", encoded)

    def test_fleet_brief_only_exposes_safety_relevant_fields(self):
        state = {
            "fleets": [
                {
                    "fleet_id": 2,
                    "ship_ids": [1, 2],
                    "fuel_ready": True,
                    "ammo_ready": True,
                    "mission_status": 0,
                    "mission_id": None,
                    "in_dock_ship_ids": [],
                    "damaged_ship_ids": [],
                }
            ]
        }
        self.assertEqual(
            _fleet_brief(state),
            {
                "2": {
                    "ship_ids": [1, 2],
                    "fuel_ready": True,
                    "ammo_ready": True,
                    "mission_status": 0,
                    "mission_id": None,
                    "in_dock_ship_ids": [],
                    "damaged_ship_ids": [],
                }
            },
        )

    def test_display_path_is_repo_relative_and_posix(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(
            _display_path(root / "docs" / "research" / "x.json"),
            "docs/research/x.json",
        )


if __name__ == "__main__":
    unittest.main()
