from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import one_expedition_ui_loop as controller
from kancollebot.adapters.poi.cdp_fleet_state import PoiFleetMissionState


class ReturnGraceDelayTests(unittest.TestCase):
    def setUp(self):
        self.active = PoiFleetMissionState(2, "第2艦隊", (1, 3, 1000, 0), (1, 4, 8, 25))
        self.returned = PoiFleetMissionState(2, "第2艦隊", (2, 3, 1000, 0), (1, 4, 8, 25))

    def test_scheduled_expiry_waits_one_grace_period_then_requests_ui_refresh(self):
        with (
            patch.object(controller, "read_poi_fleet_states", return_value=(self.active,)),
            patch.object(controller.time, "monotonic", side_effect=[0.0, 0.0, 11.0]),
            patch.object(controller.time, "time", return_value=2.0),
            patch.object(controller.time, "sleep") as sleep,
        ):
            result = controller._wait_for_return(object(), 1000, 5.0, 60.0, 10.0)
        self.assertEqual(result, self.active)
        sleep.assert_called_once_with(10.0)

    def test_already_returned_state_skips_grace_delay(self):
        with (
            patch.object(controller, "read_poi_fleet_states", return_value=(self.returned,)),
            patch.object(controller.time, "monotonic", side_effect=[0.0, 0.0]),
            patch.object(controller.time, "sleep") as sleep,
        ):
            result = controller._wait_for_return(object(), 1000, 5.0, 60.0, 10.0)
        self.assertEqual(result, self.returned)
        sleep.assert_not_called()

    def test_unexpected_mission_fails_closed_before_sleep(self):
        other = PoiFleetMissionState(2, "第2艦隊", (1, 1, 1000, 0), (1, 4, 8, 25))
        with (
            patch.object(controller, "read_poi_fleet_states", return_value=(other,)),
            patch.object(controller.time, "monotonic", side_effect=[0.0, 0.0]),
            patch.object(controller.time, "sleep") as sleep,
        ):
            with self.assertRaises(controller.LoopError):
                controller._wait_for_return(object(), 1000, 5.0, 60.0, 10.0)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()