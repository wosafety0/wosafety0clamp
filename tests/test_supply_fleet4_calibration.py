"""Fleet 4's supply tab is a calibrated, template-verified selection.

Live 2026-11-06: the ``SUPPLY_FLEET_4_SELECTED`` fingerprint template was
recorded (crop 341,155,30,40 - the 46 px family spacing of tabs 1-3) and
discriminates cleanly: fleet-4-selected frames from two separate sessions
score 1.0, while a fleet-1-selected frame scores 0.91 and classifies as
fleet 1. The relative-brightness fallback proved unreliable on this account
(selected-tab margin as low as 1.7; tab 4 outscored tab 1 on a fleet-1 page),
so it was retired. The end-to-end proof ran the same day: the production
full-supply tool supplied Fleet 4 through the UI (16 fuel, one click, Poi
delayed-store verification) where the same call had failed closed at 01:33Z.
"""

from __future__ import annotations

import inspect
import json
import unittest
from pathlib import Path

from kancollebot.adapters.poi import full_supply
from kancollebot.adapters.poi.continuous_expedition_ui import (
    TEMPLATE_FREE_SUPPLY_FLEETS,
)

ROOT = Path(__file__).resolve().parents[1]
FINGERPRINTS = ROOT / "src" / "kancollebot" / "adapters" / "poi" / "ooi_1200x720_fingerprints.json"


class SupplyFleet4CalibrationTests(unittest.TestCase):
    def test_the_relative_brightness_fallback_is_retired(self):
        self.assertEqual(TEMPLATE_FREE_SUPPLY_FLEETS, ())

    def test_the_planning_gate_supports_fleet_4_by_default(self):
        # Live 2026-11-06 01:33Z the same call failed closed because the
        # default supported set was (1, 2, 3).
        default = inspect.signature(
            full_supply.select_full_supply_targets
        ).parameters["supported_fleet_ids"].default
        self.assertEqual(default, (1, 2, 3, 4))

    def test_the_template_is_recorded_with_the_family_crop(self):
        store = json.loads(FINGERPRINTS.read_text(encoding="utf-8"))
        self.assertIn("SUPPLY_FLEET_4_SELECTED", store["states"])
        template = store["states"]["SUPPLY_FLEET_4_SELECTED"]
        self.assertEqual(template["crop"], [341, 155, 30, 40])
        self.assertEqual(template["grid"], [10, 8])
        self.assertEqual(len(template["fingerprint"]), 240)

    def test_the_page_identifier_names_fleet_4_before_the_menu_fallback(self):
        # _supply_page_state must try the fleet-4 template before SUPPLY_MENU,
        # otherwise a fleet-4-selected page could be mistaken for the parent.
        source = (
            (ROOT / "src" / "kancollebot" / "adapters" / "poi" / "continuous_expedition_ui.py")
            .read_text(encoding="utf-8")
        )
        head, tail = source.split("def _supply_page_state", 1)
        candidates = tail.split("def ", 1)[0]
        self.assertIn('"SUPPLY_FLEET_4_SELECTED",', candidates)
        self.assertLess(
            candidates.index('"SUPPLY_FLEET_4_SELECTED"'),
            candidates.index('"SUPPLY_MENU"'),
        )
        # and the module-level import block is untouched by the edit
        self.assertIn("TEMPLATE_FREE_SUPPLY_FLEETS", head)

    def test_capture_tool_offers_the_state(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "capture_ooi", ROOT / "tools" / "capture_ooi_visual_fingerprint.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertIn("SUPPLY_FLEET_4_SELECTED", module.SUPPORTED_STATES)


if __name__ == "__main__":
    unittest.main()
