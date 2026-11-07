"""Offline contract tests for the manual walkthrough wrapper.

The wrapper deliberately adds no control logic: these tests pin the pre-launch
gate, the rolling run-log time window and the exact production argv so that a
future edit cannot silently turn the wrapper into a second game-click entry.
"""
from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import manual_expedition_walkthrough as walk  # noqa: E402


def _layers(default_state="HOME_PORT", default_accepted=True,
            composited_state="UNKNOWN", composited_accepted=False):
    return {
        "default": {"state": default_state, "accepted": default_accepted,
                    "best_name": default_state, "best_score": 0.98},
        "composited": {"state": composited_state, "accepted": composited_accepted,
                       "best_name": composited_state, "best_score": 0.87},
    }


class PrelaunchGateTests(unittest.TestCase):
    def test_clean_home_port_is_go(self):
        decision, reasons = walk._decide_gate(_layers(), None)
        self.assertEqual(decision, "GO")
        self.assertEqual(reasons, [])

    def test_production_two_layer_reward_probe_blocks(self):
        decision, reasons = walk._decide_gate(_layers(), "HOME_PORT_RETURN_NOTICE")
        self.assertEqual(decision, "FAIL_CLOSED")
        self.assertTrue(any("two-layer reward probe" in r for r in reasons))

    def test_default_layer_result_page_blocks(self):
        decision, reasons = walk._decide_gate(
            _layers(default_state="RETURN_RESULT"), None)
        self.assertEqual(decision, "FAIL_CLOSED")
        self.assertTrue(any("reward screen" in r for r in reasons))

    def test_non_home_port_default_blocks(self):
        decision, reasons = walk._decide_gate(
            _layers(default_state="SORTIE_MENU"), None)
        self.assertEqual(decision, "FAIL_CLOSED")
        self.assertTrue(any("not an accepted HOME_PORT" in r for r in reasons))

    def test_unexpected_composited_state_blocks(self):
        decision, reasons = walk._decide_gate(
            _layers(composited_state="EXPEDITION_LIST"), None)
        self.assertEqual(decision, "FAIL_CLOSED")
        self.assertTrue(any("composited" in r for r in reasons))

    def test_missing_layer_fails_closed(self):
        decision, reasons = walk._decide_gate({"default": _layers()["default"]}, None)
        self.assertEqual(decision, "FAIL_CLOSED")
        self.assertTrue(any("missing capture layer" in r for r in reasons))

    def test_plain_composited_unknown_is_allowed(self):
        """Historical fallback: composited UNKNOWN is not a reward screen."""
        decision, _ = walk._decide_gate(
            _layers(composited_state="UNKNOWN", composited_accepted=False), None)
        self.assertEqual(decision, "GO")


class RunLogWindowTests(unittest.TestCase):
    def test_time_window_excludes_earlier_rounds(self):
        rows = [
            {"timestamp_utc": "2026-11-06T14:20:00Z", "event": "settlement_drain_action"},
            {"timestamp_utc": "2026-11-06T14:43:00Z", "event": "settlement_drain_action"},
        ]
        kept = walk._filter_rows(rows, "2026-11-06T14:42:00", None)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["timestamp_utc"], "2026-11-06T14:43:00Z")

    def test_run_id_excludes_other_processes_in_same_window(self):
        rows = [
            {"timestamp_utc": "2026-11-06T14:43:00Z", "run_id": "A", "event": "x"},
            {"timestamp_utc": "2026-11-06T14:43:01Z", "run_id": "B", "event": "y"},
            {"timestamp_utc": "2026-11-06T14:43:02Z", "event": "z"},
        ]
        kept = walk._filter_rows(rows, "2026-11-06T14:42:00", "A")
        self.assertEqual([r.get("run_id") for r in kept], ["A", None])

    def test_a_run_id_from_the_other_sink_never_empties_the_window(self):
        """stdout and the run log label one run differently.

        Live 2026-11-07 the summary reported "0 rows" for a run that wrote 113,
        because stdout said ``20261107T024140.740206Z`` while the run log said
        ``run-20261107T023729Z-87dca0685e53``. A run id that matches nothing is
        no evidence, so the time window stands on its own.
        """
        rows = [
            {"timestamp_utc": "2026-11-07T02:37:29Z", "run_id": "run-20261107T023729Z-abc", "event": "x"},
            {"timestamp_utc": "2026-11-07T02:37:31Z", "run_id": "run-20261107T023729Z-abc", "event": "y"},
        ]

        kept = walk._filter_rows(rows, "2026-11-07T02:37:00", "20261107T024140.740206Z")

        self.assertEqual(len(kept), 2)


class ProductionArgvTests(unittest.TestCase):
    def _args(self, strict=False):
        return argparse.Namespace(
            fleet_id=2, mission_id=1,
            database=ROOT / "logs" / "manual.sqlite3",
            run_log_dir=ROOT / "logs" / "run_log_manual",
            drain_interval=10.0, drain_timeout=240.0, drain_max_actions=24,
            strict=strict,
        )

    def test_argv_uses_existing_single_tick_entry(self):
        argv = walk._build_production_argv(self._args(), ROOT / "logs" / "x.log")
        self.assertIn(str(walk.PRODUCTION_ENTRY), argv)
        for flag in ("--authorize-ui-actions", "--global-drain", "--fleet-id",
                     "--mission-id", "--database", "--run-log-dir", "--log-path"):
            self.assertIn(flag, argv)
        # Two-stage, single tick: the wrapper never starts a long-running loop.
        self.assertNotIn("--wait-for-return", argv)
        self.assertNotIn("--all-fleets", argv)

    def test_strict_is_passed_through_only_when_requested(self):
        self.assertNotIn("--strict", walk._build_production_argv(
            self._args(), ROOT / "logs" / "x.log"))
        self.assertIn("--strict", walk._build_production_argv(
            self._args(strict=True), ROOT / "logs" / "x.log"))

    def test_reward_states_match_production_constant(self):
        entry = walk._production_module()
        self.assertEqual(tuple(walk.REWARD_STATES), tuple(entry.REWARD_SCREEN_STATES))


if __name__ == "__main__":
    unittest.main()
