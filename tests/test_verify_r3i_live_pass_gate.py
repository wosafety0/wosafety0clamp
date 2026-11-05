"""Deterministic tests for the live PASS-gate tool's click accounting.

The tool's ``safety`` payload is an audit claim: it must count clicks that were
ACTUALLY SENT, never planned or aggregate counters. The first build summed every
``click_count`` field, which counted a *planned* button click and missed the
refresh driver's running ``clicks_sent`` total (live 2026-11-05, 8 reported vs
10 actual). These tests lock the corrected contract.
"""
from __future__ import annotations

import unittest

from tools.verify_r3i_live_pass_gate import (
    _SUPPLY_CLICK_EVENT_FIELDS,
    count_supply_clicks,
)


class CountSupplyClicksTests(unittest.TestCase):
    def test_counts_only_sent_clicks_from_the_live_run_shape(self):
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
            {
                "event": "full_supply_completed_and_verified_after_delayed_store_sync",
                "ui_click_count": 1,
            },
            {"event": "fleet_full_supply_completed", "ui_click_count": 1},
            {"event": "full_supply_completed_and_verified", "ui_click_count": 1},
        ]
        self.assertEqual(count_supply_clicks(events), 6)

    def test_a_planned_button_verification_is_not_a_click(self):
        events = [{"event": "fleet_wide_supply_button_verified", "click_count": 1}]
        self.assertEqual(count_supply_clicks(events), 0)

    def test_aggregate_completion_counts_are_not_clicks(self):
        events = [
            {"event": "full_supply_completed_and_verified", "ui_click_count": 99},
            {"event": "fleet_full_supply_completed", "ui_click_count": 99},
        ]
        self.assertEqual(count_supply_clicks(events), 0)

    def test_refresh_running_total_is_counted_once(self):
        events = [
            {"event": "ui_refresh_click_sent", "clicks_sent": 1},
            {"event": "ui_refresh_click_sent", "clicks_sent": 2},
        ]
        self.assertEqual(count_supply_clicks(events), 2)

    def test_home_port_navigation_click_is_counted(self):
        events = [{"event": "home_port_navigation_click_sent", "click_count": 1}]
        self.assertEqual(count_supply_clicks(events), 1)

    def test_non_integer_counts_are_ignored(self):
        events = [
            {"event": "supply_menu_navigation_click_sent", "click_count": "1"},
            {"event": "ui_refresh_click_sent", "clicks_sent": True},
        ]
        self.assertEqual(count_supply_clicks(events), 0)

    def test_empty_event_log_is_zero(self):
        self.assertEqual(count_supply_clicks([]), 0)

    def test_every_whitelisted_event_is_a_sent_click_event(self):
        self.assertTrue(
            all(name.endswith("_sent") or name == "supply_fleet_tab_selected_for_global_button_visibility"
                for name in _SUPPLY_CLICK_EVENT_FIELDS)
        )


if __name__ == "__main__":
    unittest.main()
