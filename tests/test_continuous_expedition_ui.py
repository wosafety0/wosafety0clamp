from __future__ import annotations

import json
import unittest
from pathlib import Path

from kancollebot.adapters.poi.continuous_expedition_ui import (
    CdpContinuousExpeditionUiDriver,
    ContinuousUiDriverError,
)
from kancollebot.calibration import RowLocation
from kancollebot.expeditions.continuous_dispatch_controller import UiStateEvidence


class FakeInput:
    def __init__(self):
        self.clicks = []

    def click(self, point):
        self.clicks.append(point.label)


class ContinuousExpeditionUiDriverTests(unittest.TestCase):
    def make_driver(self, *, states=None, errors=None, locate_row=None):
        game_input = FakeInput()
        events = []
        sequence = list(states or [])
        failures = dict(errors or {})

        def verify(state):
            if state in failures:
                error = failures.pop(state)
                if error is not None:
                    raise error
            observed = sequence.pop(0) if sequence else state
            return UiStateEvidence(
                state=observed,
                consecutive_states=(observed, observed),
                evidence="two_consecutive_in_memory_ooi_fingerprints",
            )

        driver = CdpContinuousExpeditionUiDriver(
            game_input=game_input,
            verify_state=verify,
            event_sink=lambda name, fields: events.append((name, dict(fields))),
            sleep=lambda _seconds: None,
            locate_row=locate_row,
        )
        return driver, game_input, events

    def test_fleet_tab_fingerprints_use_disjoint_tab_only_crops(self):
        root = Path(__file__).resolve().parents[1]
        fingerprint_path = (
            root / "src" / "kancollebot" / "adapters" / "poi"
            / "ooi_1200x720_fingerprints.json"
        )
        document = json.loads(fingerprint_path.read_text(encoding="utf-8"))
        fleet2 = document["states"]["FLEET_2_SELECTED"]
        fleet3 = document["states"]["FLEET_3_SELECTED"]
        self.assertEqual(fleet2["crop"], [565, 145, 50, 60])
        self.assertEqual(fleet3["crop"], [615, 145, 50, 60])
        self.assertEqual(fleet2["grid"], [10, 12])
        self.assertEqual(fleet3["grid"], [10, 12])
        self.assertEqual(fleet2["samples"], 3)
        self.assertEqual(fleet3["samples"], 3)

    def test_neutralizes_pointer_before_each_visual_checkpoint(self):
        order = []
        game_input = FakeInput()
        evidence = UiStateEvidence(
            state="HOME_PORT",
            consecutive_states=("HOME_PORT", "HOME_PORT"),
            evidence="two_consecutive_in_memory_ooi_fingerprints",
        )
        driver = CdpContinuousExpeditionUiDriver(
            game_input=game_input,
            verify_state=lambda _state: (order.append("verify"), evidence)[1],
            event_sink=lambda _name, _fields: None,
            neutralize_pointer=lambda: order.append("neutralize"),
            sleep=lambda _seconds: None,
        )
        driver._confirm("HOME_PORT", phase="test")
        self.assertEqual(order, ["neutralize", "verify"])

    def test_dispatches_only_calibrated_fleet3_mission3_path(self):
        driver, game_input, events = self.make_driver()
        driver.dispatch_expedition(fleet_id=3, mission_id=3)
        self.assertEqual(
            game_input.clicks,
            [
                "sortie",
                "expedition-mode",
                "mission-3-row",
                "mission-confirm",
                "fleet-3-tab",
                "expedition-start",
            ],
        )
        self.assertEqual(
            [name for name, _ in events].count("continuous_ui_dispatch_input_completed"),
            1,
        )

    def test_prepare_dispatch_stops_before_irreversible_start_click(self):
        driver, game_input, events = self.make_driver()
        evidence = driver.prepare_dispatch(fleet_id=3, mission_id=3)
        self.assertEqual(evidence.state, "FLEET_3_SELECTED")
        self.assertNotIn("expedition-start", game_input.clicks)
        self.assertEqual(game_input.clicks[-1], "fleet-3-tab")
        self.assertTrue(any(
            name == "continuous_ui_dispatch_preparation_completed"
            and fields.get("expedition_start_clicked") is False
            for name, fields in events
        ))

    def test_keeps_fleet3_without_unnecessary_tab_click(self):
        driver, game_input, _ = self.make_driver(states=[
            "HOME_PORT",
            "SORTIE_MENU",
            "EXPEDITION_LIST",
            "MISSION_SELECTED",
            "FLEET_3_SELECTED",
            "FLEET_3_SELECTED",
        ])
        driver.dispatch_expedition(fleet_id=3, mission_id=3)
        self.assertNotIn("fleet-3-tab", game_input.clicks)
        self.assertEqual(game_input.clicks[-1], "expedition-start")

    def test_a_configured_locator_decides_where_mission_3_is_clicked(self):
        # Measured position 2 (third row) but at a y the constant never had:
        # the click must follow the locator, not RecordedPoint(350, 350).
        location = RowLocation(
            expedition_id="03",
            tab=0,
            position=2,
            offset=0,
            row_index=3,
            point=(350, 361.5),
            scroll_clicks=0,
            tab_clicks=1,
            detail="list position 2 on a page starting at 0",
        )
        asked = []

        def locate(expedition_id, tab):
            asked.append((expedition_id, tab))
            return location

        driver, game_input, events = self.make_driver(
            states=[
                "HOME_PORT",
                "SORTIE_MENU",
                "EXPEDITION_LIST",
                "EXPEDITION_LIST",
                "MISSION_SELECTED",
                "FLEET_3_SELECTED",
                "FLEET_3_SELECTED",
            ],
            locate_row=locate,
        )
        driver.dispatch_expedition(fleet_id=3, mission_id=3)

        self.assertEqual(asked, [("03", 0)], "the tab is part of the question")
        self.assertIn("mission-3-row-pos2", game_input.clicks)
        self.assertNotIn("mission-3-row", game_input.clicks)
        located = [fields for name, fields in events if name == "continuous_ui_row_located"]
        self.assertEqual(len(located), 1)
        self.assertEqual(located[0]["offset"], 0)
        self.assertEqual(located[0]["position"], 2)

    def test_a_locator_failure_blocks_before_the_row_click(self):
        def locate(expedition_id, tab):
            raise RuntimeError("the list never reached its top")

        driver, game_input, events = self.make_driver(
            states=["HOME_PORT", "SORTIE_MENU", "EXPEDITION_LIST"],
            locate_row=locate,
        )
        with self.assertRaisesRegex(ContinuousUiDriverError, "cannot place mission 3's row"):
            driver.dispatch_expedition(fleet_id=3, mission_id=3)
        self.assertEqual(game_input.clicks, ["sortie", "expedition-mode"])
        self.assertTrue(any(
            name == "continuous_ui_dispatch_preparation_blocked" for name, _ in events
        ))

    def test_without_a_locator_the_constant_is_used_and_recorded_as_unverified(self):
        driver, game_input, events = self.make_driver()
        driver.dispatch_expedition(fleet_id=3, mission_id=3)
        self.assertIn("mission-3-row", game_input.clicks)
        flagged = [fields for name, fields in events if name == "continuous_ui_row_located_unverified"]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]["target"], "mission-3-row")

    def test_rejects_unverified_fleet_or_mission_before_any_click(self):
        driver, game_input, _ = self.make_driver()
        with self.assertRaisesRegex(ContinuousUiDriverError, "No independently verified UI mapping"):
            driver.dispatch_expedition(fleet_id=2, mission_id=1)
        self.assertEqual(game_input.clicks, [])

    def test_failed_checkpoint_stops_before_following_clicks(self):
        driver, game_input, events = self.make_driver(states=[
            "HOME_PORT",
            "UNKNOWN",
        ])
        with self.assertRaisesRegex(ContinuousUiDriverError, "two consecutive SORTIE_MENU"):
            driver.dispatch_expedition(fleet_id=3, mission_id=3)
        self.assertEqual(game_input.clicks, ["sortie"])
        self.assertEqual(
            [name for name, _ in events].count("continuous_ui_dispatch_blocked"),
            1,
        )

    def test_return_home_uses_at_most_one_click_from_verified_expedition_list(self):
        driver, game_input, _ = self.make_driver(states=[
            "EXPEDITION_LIST",
            "HOME_PORT",
        ])
        evidence = driver.return_to_home_port(fleet_id=3)
        self.assertEqual(evidence.state, "HOME_PORT")
        self.assertEqual(game_input.clicks, ["home-port"])

    def test_home_verification_retries_observation_without_repeating_click(self):
        driver, game_input, events = self.make_driver(errors={
            "HOME_PORT": RuntimeError("Home Port is still rendering"),
        })
        evidence = driver.return_to_home_port(fleet_id=3)
        self.assertEqual(evidence.state, "HOME_PORT")
        self.assertEqual(game_input.clicks, ["home-port"])
        self.assertTrue(any(
            name == "continuous_ui_home_port_verification_pending"
            for name, _ in events
        ))

    def test_return_home_can_recover_from_verified_pre_dispatch_state(self):
        driver, game_input, _ = self.make_driver(states=[
            "MISSION_SELECTED",
            "HOME_PORT",
        ])
        evidence = driver.return_to_home_port(
            fleet_id=3,
            expected_current_state="MISSION_SELECTED",
        )
        self.assertEqual(evidence.state, "HOME_PORT")
        self.assertEqual(game_input.clicks, ["home-port"])

    def test_return_home_rejects_other_fleet_without_click(self):
        driver, game_input, _ = self.make_driver()
        with self.assertRaisesRegex(ContinuousUiDriverError, "not calibrated"):
            driver.return_to_home_port(fleet_id=2)
        self.assertEqual(game_input.clicks, [])


if __name__ == "__main__":
    unittest.main()
