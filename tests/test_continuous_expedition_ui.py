from __future__ import annotations

import json
import unittest
from pathlib import Path

from kancollebot.adapters.poi.continuous_expedition_ui import (
    CdpContinuousExpeditionUiDriver,
    ContinuousUiDriverError,
)
from kancollebot.calibration import DetailMatch, MarkMatch, RowLocation
from kancollebot.expeditions.continuous_dispatch_controller import UiStateEvidence


class FakeInput:
    def __init__(self):
        self.clicks = []

    def click(self, point):
        self.clicks.append(point.label)


class FakeDetails:
    """Name-band evidence: knows a set of expeditions, and one that will be seen."""

    def __init__(self, known=("03", "04", "26"), *, observed=None, score=1.0):
        self.known = set(known)
        self.observed = observed
        self.score = score
        self.asked = []

    def reference(self, expedition_id):
        return object() if expedition_id in self.known else None

    def require(self, expedition_id, screenshot_base64):
        self.asked.append((expedition_id, screenshot_base64))
        seen = self.observed if self.observed is not None else expedition_id
        if seen != expedition_id:
            raise RuntimeError(f"the selected panel is {seen}, not {expedition_id}")
        return DetailMatch(
            expedition_id=expedition_id,
            score=self.score,
            runner_up="99",
            runner_up_score=self.score - 0.05,
        )


class FakeMarks:
    """決定 + fleet tabs, with the selected fleet as state."""

    def __init__(self, *, fleets=(2, 3, 4), selected=2, decision=True, switch_on_click=True):
        self._fleets = tuple(fleets)
        self.selected = selected
        self.decision = decision
        self.switch_on_click = switch_on_click
        self.checks = []

    def fleets(self):
        return self._fleets

    def require_decision(self, screenshot_base64):
        if not self.decision:
            raise RuntimeError("the 決定 control scores 0.79: the expedition is already running")
        return MarkMatch(score=1.0, threshold=0.97, detail="決定 control recognised")

    def require_fleet(self, fleet_id, screenshot_base64):
        self.checks.append(fleet_id)
        if fleet_id == self.selected:
            return MarkMatch(score=1.0, threshold=0.95, detail=f"fleet {fleet_id} tab recognised")
        raise RuntimeError(f"fleet {self.selected} is selected, not {fleet_id}")


class FakeScreen:
    """A screen that reacts to the clicks the driver sends."""

    def __init__(self, *, marks: FakeMarks, fleet_after_confirm=2, detail_seen=None):
        self.marks = marks
        self.detail_seen = detail_seen
        self.fleet_after_confirm = fleet_after_confirm
        self.frames = 0

    def capture(self):
        self.frames += 1
        return f"frame-{self.frames}"

    def click(self, point):
        if point.label == "mission-confirm":
            self.marks.selected = self.fleet_after_confirm
        if point.label.endswith("-tab") and point.label.startswith("fleet-"):
            if self.marks.switch_on_click:
                self.marks.selected = int(point.label.split("-")[1])


class ContinuousExpeditionUiDriverTests(unittest.TestCase):
    def make_driver(
        self,
        *,
        states=None,
        errors=None,
        locate_row=None,
        details=None,
        marks=None,
        fleet_after_confirm=2,
        supported_fleets=(2, 3, 4),
    ):
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

        marks = marks or FakeMarks()
        details = details or FakeDetails()
        screen = FakeScreen(marks=marks, fleet_after_confirm=fleet_after_confirm)

        def click(point):
            game_input.click(point)
            screen.click(point)

        class Input:
            def click(self, point):
                click(point)

        driver = CdpContinuousExpeditionUiDriver(
            game_input=Input(),
            verify_state=verify,
            event_sink=lambda name, fields: events.append((name, dict(fields))),
            details=details,
            marks=marks,
            capture=screen.capture,
            sleep=lambda _seconds: None,
            locate_row=locate_row,
            supported_fleets=supported_fleets,
        )
        return driver, game_input, events, marks, details

    def location(self, expedition_id="03", *, point=(350, 351.5), position=2):
        return RowLocation(
            expedition_id=expedition_id,
            tab=0,
            position=position,
            offset=0,
            row_index=position + 1,
            point=point,
            scroll_clicks=0,
            tab_clicks=1,
            detail="list position on a page starting at 0",
        )

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
        evidence = UiStateEvidence(
            state="HOME_PORT",
            consecutive_states=("HOME_PORT", "HOME_PORT"),
            evidence="two_consecutive_in_memory_ooi_fingerprints",
        )
        driver = CdpContinuousExpeditionUiDriver(
            game_input=FakeInput(),
            verify_state=lambda _state: (order.append("verify"), evidence)[1],
            event_sink=lambda _name, _fields: None,
            details=FakeDetails(),
            marks=FakeMarks(),
            capture=lambda: (order.append("capture"), "frame")[1],
            neutralize_pointer=lambda: order.append("neutralize"),
            sleep=lambda _seconds: None,
        )
        driver._confirm("HOME_PORT", phase="test")
        self.assertEqual(order, ["neutralize", "verify"])

    def test_dispatches_any_mapped_mission_on_its_located_row(self):
        asked = []

        def locate(expedition_id):
            asked.append(expedition_id)
            return self.location(expedition_id, point=(350, 441.5), position=4)

        driver, game_input, events, marks, details = self.make_driver(
            locate_row=locate,
        )
        driver.dispatch_expedition(fleet_id=3, mission_id=26)  # 26 -> "26"
        self.assertEqual(asked, ["26"])
        self.assertEqual(
            game_input.clicks,
            ["sortie", "expedition-mode", "expedition-row-26-pos4", "mission-confirm", "fleet-3-tab", "expedition-start"],
        )
        self.assertEqual(details.asked[0][0], "26")
        self.assertEqual(marks.checks[-1], 3)

    def test_maps_alternate_series_ids_to_their_display_number(self):
        def locate(expedition_id):
            return self.location(expedition_id, point=(350, 576.0), position=8)

        details = FakeDetails(known=("D1",))
        driver, game_input, _, _, _ = self.make_driver(locate_row=locate, details=details)
        driver.prepare_dispatch(fleet_id=2, mission_id=131)  # D1
        self.assertIn("expedition-row-D1-pos8", game_input.clicks)

    def test_keeps_the_selected_fleet_without_an_extra_tab_click(self):
        driver, game_input, _, _, _ = self.make_driver(
            locate_row=lambda expedition_id: self.location(expedition_id),
            marks=FakeMarks(selected=3),
            fleet_after_confirm=3,
        )
        driver.dispatch_expedition(fleet_id=3, mission_id=3)
        self.assertNotIn("fleet-3-tab", game_input.clicks)
        self.assertEqual(game_input.clicks[-1], "expedition-start")

    def test_switches_to_the_requested_fleet_and_proves_it(self):
        driver, game_input, events, marks, _ = self.make_driver(
            locate_row=lambda expedition_id: self.location(expedition_id),
            marks=FakeMarks(selected=2),
            fleet_after_confirm=2,
        )
        evidence = driver.prepare_dispatch(fleet_id=4, mission_id=3)
        self.assertEqual(evidence.state, "FLEET_4_SELECTED")
        self.assertIn("fleet-4-tab", game_input.clicks)
        self.assertEqual(marks.selected, 4)
        self.assertTrue(any(
            name == "continuous_ui_fleet_switch_required" for name, _ in events
        ))

    def test_a_fleet_that_never_switches_blocks_before_the_start_click(self):
        driver, game_input, events, _, _ = self.make_driver(
            locate_row=lambda expedition_id: self.location(expedition_id),
            marks=FakeMarks(selected=2, switch_on_click=False),
            fleet_after_confirm=2,
        )
        with self.assertRaisesRegex(ContinuousUiDriverError, "fleet 2 is selected, not 4"):
            driver.dispatch_expedition(fleet_id=4, mission_id=3)
        self.assertNotIn("expedition-start", game_input.clicks)
        self.assertEqual(
            [name for name, _ in events].count("continuous_ui_dispatch_blocked"), 1
        )

    def test_prepare_dispatch_stops_before_the_irreversible_start_click(self):
        driver, game_input, events, _, _ = self.make_driver(
            locate_row=lambda expedition_id: self.location(expedition_id),
        )
        evidence = driver.prepare_dispatch(fleet_id=3, mission_id=3)
        self.assertEqual(evidence.state, "FLEET_3_SELECTED")
        self.assertNotIn("expedition-start", game_input.clicks)
        self.assertTrue(any(
            name == "continuous_ui_dispatch_preparation_completed"
            and fields.get("expedition_start_clicked") is False
            for name, fields in events
        ))

    def test_rejects_a_mission_the_master_table_does_not_know(self):
        driver, game_input, _, _, _ = self.make_driver(
            locate_row=lambda expedition_id: self.location(expedition_id),
        )
        with self.assertRaisesRegex(ContinuousUiDriverError, "not in the master-data table"):
            driver.dispatch_expedition(fleet_id=3, mission_id=999)
        self.assertEqual(game_input.clicks, [])

    def test_rejects_fleet_1_before_any_click(self):
        driver, game_input, _, _, _ = self.make_driver(
            locate_row=lambda expedition_id: self.location(expedition_id),
        )
        with self.assertRaisesRegex(ContinuousUiDriverError, "fleet 1 cannot be dispatched"):
            driver.dispatch_expedition(fleet_id=1, mission_id=3)
        self.assertEqual(game_input.clicks, [])

    def test_rejects_a_mission_with_no_detail_reference_before_any_click(self):
        driver, game_input, _, _, _ = self.make_driver(
            locate_row=lambda expedition_id: self.location(expedition_id),
            details=FakeDetails(known=("03",)),
        )
        with self.assertRaisesRegex(ContinuousUiDriverError, "no detail reference for 04"):
            driver.dispatch_expedition(fleet_id=3, mission_id=4)
        self.assertEqual(game_input.clicks, [])

    def test_rejects_a_fleet_with_no_tab_reference_before_any_click(self):
        driver, game_input, _, _, _ = self.make_driver(
            locate_row=lambda expedition_id: self.location(expedition_id),
            marks=FakeMarks(fleets=(2, 3)),
        )
        with self.assertRaisesRegex(ContinuousUiDriverError, "no fleet tab reference for fleet 4"):
            driver.dispatch_expedition(fleet_id=4, mission_id=3)
        self.assertEqual(game_input.clicks, [])

    def test_without_a_locator_nothing_is_clicked(self):
        driver, game_input, events, _, _ = self.make_driver()
        with self.assertRaisesRegex(ContinuousUiDriverError, "cannot place 03's row"):
            driver.dispatch_expedition(fleet_id=3, mission_id=3)
        self.assertEqual(game_input.clicks, ["sortie", "expedition-mode"])
        self.assertTrue(any(
            name == "continuous_ui_dispatch_preparation_blocked" for name, _ in events
        ))

    def test_a_locator_failure_blocks_before_the_row_click(self):
        def locate(expedition_id):
            raise RuntimeError("the list never reached its top")

        driver, game_input, events, _, _ = self.make_driver(locate_row=locate)
        with self.assertRaisesRegex(ContinuousUiDriverError, "cannot place 03's row"):
            driver.dispatch_expedition(fleet_id=3, mission_id=3)
        self.assertEqual(game_input.clicks, ["sortie", "expedition-mode"])

    def test_a_different_panel_blocks_before_the_confirm_click(self):
        driver, game_input, events, marks, _ = self.make_driver(
            locate_row=lambda expedition_id: self.location(expedition_id),
            details=FakeDetails(observed="12"),
        )
        with self.assertRaisesRegex(ContinuousUiDriverError, "the selected panel is not 03"):
            driver.dispatch_expedition(fleet_id=3, mission_id=3)
        self.assertNotIn("mission-confirm", game_input.clicks)
        self.assertEqual(marks.checks, [])

    def test_a_running_expedition_blocks_before_the_confirm_click(self):
        """No 決定 means the panel shows 遠征中止/帰還命令 instead."""
        driver, game_input, _, _, _ = self.make_driver(
            locate_row=lambda expedition_id: self.location(expedition_id),
            marks=FakeMarks(decision=False),
        )
        with self.assertRaisesRegex(ContinuousUiDriverError, "already running"):
            driver.dispatch_expedition(fleet_id=3, mission_id=3)
        self.assertNotIn("mission-confirm", game_input.clicks)
        self.assertNotIn("expedition-start", game_input.clicks)

    def test_failed_checkpoint_stops_before_following_clicks(self):
        driver, game_input, events, _, _ = self.make_driver(
            states=["HOME_PORT", "UNKNOWN"],
            locate_row=lambda expedition_id: self.location(expedition_id),
        )
        with self.assertRaisesRegex(ContinuousUiDriverError, "two consecutive SORTIE_MENU"):
            driver.dispatch_expedition(fleet_id=3, mission_id=3)
        self.assertEqual(game_input.clicks, ["sortie"])
        self.assertEqual(
            [name for name, _ in events].count("continuous_ui_dispatch_blocked"), 1
        )

    def test_return_home_uses_at_most_one_click_from_verified_expedition_list(self):
        driver, game_input, _, _, _ = self.make_driver(states=["EXPEDITION_LIST", "HOME_PORT"])
        evidence = driver.return_to_home_port(fleet_id=3)
        self.assertEqual(evidence.state, "HOME_PORT")
        self.assertEqual(game_input.clicks, ["home-port"])

    def test_home_verification_retries_observation_without_repeating_click(self):
        driver, game_input, events, _, _ = self.make_driver(
            errors={"HOME_PORT": RuntimeError("Home Port is still rendering")},
        )
        evidence = driver.return_to_home_port(fleet_id=3)
        self.assertEqual(evidence.state, "HOME_PORT")
        self.assertEqual(game_input.clicks, ["home-port"])
        self.assertTrue(any(
            name == "continuous_ui_home_port_verification_pending" for name, _ in events
        ))

    def test_return_home_accepts_every_dispatchable_fleet(self):
        for fleet_id in (2, 3, 4):
            with self.subTest(fleet=fleet_id):
                driver, game_input, _, _, _ = self.make_driver(
                    states=["EXPEDITION_LIST", "HOME_PORT"],
                )
                evidence = driver.return_to_home_port(fleet_id=fleet_id)
                self.assertEqual(evidence.state, "HOME_PORT")
                self.assertEqual(game_input.clicks, ["home-port"])

    def test_return_home_rejects_other_fleet_without_click(self):
        driver, game_input, _, _, _ = self.make_driver()
        with self.assertRaisesRegex(ContinuousUiDriverError, "not calibrated for Fleet 1"):
            driver.return_to_home_port(fleet_id=1)
        self.assertEqual(game_input.clicks, [])


if __name__ == "__main__":
    unittest.main()
