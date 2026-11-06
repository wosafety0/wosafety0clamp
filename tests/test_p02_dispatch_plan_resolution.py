"""P0.2-3 / P0.2-5: multi-fleet dispatch-plan resolution and the guard rails.

These are the offline unit/integration checks for the CLI refactor that lets
one process drive every dispatchable fleet at once. The resolution is the single
source of truth consumed by build_runtime, the per-fleet validation, settlement
and the supervisor report, so it is tested directly; the concurrent-settlement
behaviour itself is covered by the global-drain suites.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import one_expedition_ui_loop as entry
from kancollebot.runtime_config import RUNTIME_CONFIG


def _args(**overrides) -> SimpleNamespace:
    """A minimal args object; only the named fields exist (all others None)."""
    base = {
        "fleet_id": None,
        "mission_id": None,
        "fleet_missions": None,
        "exclude_fleets": None,
        "global_drain": False,
        "minimum_ships": None,
        "flagship_level": None,
        "settle_only": False,
        "wait_for_return": False,
        "max_ticks": 1,
        "tick_interval_seconds": 10.0,
        "authorize_ui_actions": True,
        "adopt_observed_mission": False,
        "run_log_dir": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class DispatchPlanResolutionTests(unittest.TestCase):
    def test_single_fleet_from_fleet_id_and_mission(self):
        fleets, missions = entry._resolve_dispatch_plan(_args(fleet_id=3, mission_id=3))
        self.assertEqual(fleets, (3,))
        self.assertEqual(missions, {3: 3})

    def test_fleet_id_without_mission_is_refused(self):
        with self.assertRaises(entry.LoopError):
            entry._resolve_dispatch_plan(_args(fleet_id=3, mission_id=None))

    def test_mission_id_without_fleet_id_is_refused(self):
        with self.assertRaises(entry.LoopError):
            entry._resolve_dispatch_plan(_args(fleet_id=None, mission_id=3))

    def test_multi_fleet_default_map_has_no_duplicate_mission(self):
        fleets, missions = entry._resolve_dispatch_plan(_args(fleet_id=None))
        self.assertEqual(fleets, tuple(RUNTIME_CONFIG.dispatchable_fleet_ids))
        # The default map is conflict-free by construction.
        self.assertEqual(len(set(missions.values())), len(missions))

    def test_multi_fleet_explicit_map(self):
        fleets, missions = entry._resolve_dispatch_plan(
            _args(fleet_id=None, fleet_missions="2:3,3:1,4:2")
        )
        self.assertEqual(fleets, (2, 3, 4))
        self.assertEqual(missions, {2: 3, 3: 1, 4: 2})

    def test_duplicate_mission_across_fleets_is_refused(self):
        # Expedition 1 on both fleet 2 and fleet 3 violates the game rule.
        with self.assertRaises(entry.LoopError):
            entry._resolve_dispatch_plan(_args(fleet_id=None, fleet_missions="2:1,3:1,4:2"))

    def test_fleet_missions_token_without_colon_is_refused(self):
        with self.assertRaises(entry.LoopError):
            entry._parse_fleet_missions("2:3:4")

    def test_fleet_missions_non_integer_is_refused(self):
        with self.assertRaises(entry.LoopError):
            entry._parse_fleet_missions("2:x")

    def test_fleet_missions_non_dispatchable_fleet_is_refused(self):
        # Fleet 1 is reserved / non-dispatchable.
        with self.assertRaises(entry.LoopError):
            entry._parse_fleet_missions("1:3")

    def test_exclude_fleets_removes_one(self):
        fleets, missions = entry._resolve_dispatch_plan(
            _args(fleet_id=None, exclude_fleets="4")
        )
        self.assertEqual(fleets, (2, 3))
        self.assertEqual(len(set(missions.values())), len(missions))

    def test_exclude_fleets_with_explicit_map(self):
        fleets, missions = entry._resolve_dispatch_plan(
            _args(fleet_id=None, fleet_missions="2:3,3:1", exclude_fleets="4")
        )
        self.assertEqual(fleets, (2, 3))
        self.assertEqual(missions, {2: 3, 3: 1})

    def test_exclude_fleets_non_dispatchable_is_refused(self):
        with self.assertRaises(entry.LoopError):
            entry._resolve_dispatch_plan(_args(fleet_id=None, exclude_fleets="1"))

    def test_exclude_fleets_all_is_refused(self):
        with self.assertRaises(entry.LoopError):
            entry._resolve_dispatch_plan(
                _args(fleet_id=None, exclude_fleets="2,3,4")
            )

    def test_exclude_fleets_with_fleet_id_is_refused(self):
        with self.assertRaises(entry.LoopError):
            entry._resolve_dispatch_plan(
                _args(fleet_id=3, mission_id=3, exclude_fleets="4")
            )


class MultiFleetRequiresGlobalDrainTests(unittest.TestCase):
    def test_all_fleets_without_global_drain_is_refused_before_connection(self):
        # Only the attributes read before the guard matter: fleet_id None
        # (all-fleets), mission_id None, fleet_missions None, global_drain False.
        args = MagicMock()
        args.fleet_id = None
        args.mission_id = None
        args.fleet_missions = None
        args.global_drain = False
        with self.assertRaises(entry.LoopError) as exc:
            entry._run_production_supervisor_once(args)
        self.assertIn("all-fleets mode", str(exc.exception))
        self.assertIn("global-drain", str(exc.exception))


if __name__ == "__main__":
    unittest.main()
