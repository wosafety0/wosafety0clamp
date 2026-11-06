from __future__ import annotations

import json
import unittest
from pathlib import Path

from kancollebot.adapters.poi.continuous_expedition_ui import (
    CdpContinuousExpeditionUiDriver,
    ContinuousUiDriverError,
)
from kancollebot.adapters.poi.ui_state_gate import UiState
from kancollebot.calibration import DetailMatch, MarkMatch, RowLocation
from kancollebot.expeditions.continuous_dispatch_controller import UiStateEvidence
from kancollebot.expeditions.recovery_classification import (
    RecoveryPauseGate,
    RecoveryPausedError,
    classify_recovery_signals,
)


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


class HomePortReturnPreconditionTests(unittest.TestCase):
    """The page a start click leaves behind is one of several, not one.

    Live 2026-11-05 the client still showed FLEET_3_SELECTED three seconds after
    an irreversible start, then dropped back to the list with that mission
    selected. Demanding EXPEDITION_LIST there marked a working dispatch BLOCKED.
    """

    class Screen:
        def __init__(self, state: str) -> None:
            self.state = state
            self.clicks: list[str] = []

        def verify(self, expected, *, visual_attempts=None):
            if expected != self.state:
                raise ContinuousUiDriverError(
                    f"expected {expected}, screen shows {self.state}"
                )
            return UiStateEvidence(
                state=expected,
                consecutive_states=(expected, expected),
                evidence="scripted",
            )

    def build(self, state: str):
        screen = self.Screen(state)

        def click(point):
            screen.clicks.append(point.label)
            if point.label == "home-port":
                screen.state = "HOME_PORT"

        driver = CdpContinuousExpeditionUiDriver(
            game_input=type("Input", (), {"click": staticmethod(click)})(),
            verify_state=screen.verify,
            event_sink=lambda event, payload: None,
            details=FakeDetails(),
            marks=FakeMarks(),
            capture=lambda: "frame",
            sleep=lambda seconds: None,
            settle_seconds=0.0,
        )
        return driver, screen

    def test_every_page_the_start_click_can_leave_behind_returns_home(self):
        for state in ("FLEET_3_SELECTED", "MISSION_SELECTED", "EXPEDITION_LIST"):
            with self.subTest(state=state):
                driver, screen = self.build(state)
                evidence = driver.return_to_home_port(fleet_id=3)
                self.assertEqual(evidence.state, "HOME_PORT")
                self.assertEqual(screen.clicks, ["home-port"])

    def test_a_screen_that_is_none_of_those_pages_is_never_clicked(self):
        # The named proof fails, the observed loop takes over, and its reward
        # guard refuses before any click: a reward screen advances on a click
        # anywhere and belongs to the settlement controller.
        driver, screen = self.build("BONUS_ITEMS")
        with self.assertRaisesRegex(ContinuousUiDriverError, "reward screen"):
            driver.return_to_home_port(fleet_id=3)
        self.assertEqual(screen.clicks, [])

    def test_a_named_page_stays_strict(self):
        driver, screen = self.build("FLEET_3_SELECTED")
        with self.assertRaises(ContinuousUiDriverError):
            driver.return_to_home_port(
                fleet_id=3, expected_current_state="EXPEDITION_LIST"
            )
        self.assertEqual(screen.clicks, [])

    def test_every_candidate_page_is_a_declared_ui_state(self):
        """The candidates are strings here but become UiState in production.

        A fleet tab with no enum member raised ValueError inside the verifier,
        which is not in the driver's "not this screen" set: a refusal to click
        turned into a crash. Live 2026-11-05 that is exactly what
        FLEET_4_SELECTED did, one second after a dispatch had already
        succeeded. Every supported fleet must therefore have a real state.
        """
        driver, _ = self.build("EXPEDITION_LIST")
        for fleet_id in driver._supported_fleets:
            with self.subTest(fleet_id=fleet_id):
                name = f"FLEET_{fleet_id}_SELECTED"
                self.assertEqual(UiState(name).value, name)


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
        action_guard=None,
    ):
        game_input = FakeInput()
        events = []
        sequence = list(states or [])
        failures = dict(errors or {})

        def verify(state, *, visual_attempts=None):
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
            action_guard=action_guard,
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


    def test_recovery_pause_blocks_the_next_expedition_click(self):
        gate = RecoveryPauseGate()
        gate.pause(classify_recovery_signals(visual_state="cat"))
        driver, game_input, _, _, _ = self.make_driver(
            locate_row=lambda expedition_id: self.location(expedition_id),
            action_guard=gate.require_actions_allowed,
        )

        with self.assertRaises(RecoveryPausedError):
            driver.prepare_dispatch(fleet_id=3, mission_id=3)

        self.assertEqual(game_input.clicks, [])


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


class FleetPageFallbackTests(unittest.TestCase):
    """The fleet-selection page has no usable fingerprint; the marks do.

    Live 2026-11-05 the post-start page could not be named at all: the
    ``FLEET_*_SELECTED`` templates crop a 50x60 window that misses the tab
    label, so Fleet 3 and Fleet 2 scored within 0.03 of each other and the
    dispatch was registered as BLOCKED after the start click had already
    worked. The calibrated fleet tab reaches its threshold only on that page,
    so it is the fallback proof - with the same two-frame rule.
    """

    NO_VISUAL_STATE = {
        "EXPEDITION_LIST": ContinuousUiDriverError("not this screen"),
        "MISSION_SELECTED": ContinuousUiDriverError("not this screen"),
        "FLEET_2_SELECTED": ContinuousUiDriverError("not this screen"),
        "FLEET_3_SELECTED": ContinuousUiDriverError("not this screen"),
        "FLEET_4_SELECTED": ContinuousUiDriverError("not this screen"),
    }

    def make_driver(self, *, marks, errors=None):
        game_input = FakeInput()
        events = []
        failures = dict(errors if errors is not None else self.NO_VISUAL_STATE)

        def verify(state, *, visual_attempts=None):
            if state in failures:
                error = failures.pop(state)
                if error is not None:
                    raise error
            return UiStateEvidence(
                state=state,
                consecutive_states=(state, state),
                evidence="two_consecutive_in_memory_ooi_fingerprints",
            )

        class Screen:
            def capture(self):
                return "frame"

            def click(self, point):
                game_input.click(point)

        driver = CdpContinuousExpeditionUiDriver(
            game_input=game_input,
            verify_state=verify,
            event_sink=lambda name, fields: events.append((name, dict(fields))),
            details=FakeDetails(),
            marks=marks,
            capture=lambda: "frame",
            sleep=lambda _seconds: None,
            settle_seconds=0.0,
        )
        return driver, game_input, events

    def test_the_fleet_tab_proof_allows_the_single_home_click(self):
        driver, game_input, events = self.make_driver(marks=FakeMarks(selected=3))
        evidence = driver.return_to_home_port(fleet_id=3)
        self.assertEqual(evidence.state, "HOME_PORT")
        self.assertEqual(game_input.clicks, ["home-port"])
        self.assertTrue(any(
            name == "continuous_ui_fleet_page_confirmed" for name, _ in events
        ))

    def test_without_either_proof_the_observed_loop_takes_over(self):
        # Neither the named states nor the fleet tab prove the page: the
        # click-and-observe loop takes over and the port is confirmed.
        marks = FakeMarks(selected=2)
        driver, game_input, _ = self.make_driver(marks=marks)
        evidence = driver.return_to_home_port(fleet_id=3)
        self.assertEqual(evidence.state, "HOME_PORT")
        # The fleet tab was consulted for both frames and refused.
        self.assertEqual(marks.checks, [3, 3])

    def test_the_fleet_tab_proof_needs_two_frames(self):
        class OneFrameMarks(FakeMarks):
            def __init__(self):
                super().__init__(selected=3)
                self.calls = 0

            def require_fleet(self, fleet_id, screenshot_base64):
                self.calls += 1
                if self.calls == 1:
                    raise ContinuousUiDriverError("the tab is still rendering")
                return super().require_fleet(fleet_id, screenshot_base64)

        marks = OneFrameMarks()
        driver, game_input, _ = self.make_driver(marks=marks)
        driver.return_to_home_port(fleet_id=3)
        # Two frames were asked for, and the single matching frame was not
        # accepted as a proof of the page.
        self.assertEqual(marks.calls, 2)

    def test_a_named_page_stays_strict_and_never_uses_the_fallback(self):
        driver, game_input, _ = self.make_driver(marks=FakeMarks(selected=3))
        with self.assertRaises(ContinuousUiDriverError):
            driver.return_to_home_port(
                fleet_id=3, expected_current_state="EXPEDITION_LIST"
            )
        self.assertEqual(game_input.clicks, [])


class ReturnByObservationTests(unittest.TestCase):
    """Clicking and observing, for the pages that cannot be named.

    A proof that never succeeds is a proof that never sends the click that
    would fix it. These tests pin the loop: it clicks the 母港 control, looks
    again, stops at a recognised Home Port, never clicks while a reward screen
    is up, and never runs unbounded.
    """

    def make_driver(self, *, verify, marks=None):
        game_input = FakeInput()
        events = []
        driver = CdpContinuousExpeditionUiDriver(
            game_input=game_input,
            verify_state=verify,
            event_sink=lambda name, fields: events.append((name, dict(fields))),
            details=FakeDetails(),
            marks=marks or FakeMarks(selected=2),
            capture=lambda: "frame",
            sleep=lambda _seconds: None,
            settle_seconds=0.0,
        )
        return driver, game_input, events

    @staticmethod
    def verify_with_port_after(failures):
        """A verifier that reports the port only after N failed attempts."""
        calls = {"home_port": 0}

        def verify(state, *, visual_attempts=None):
            if state == "HOME_PORT":
                calls["home_port"] += 1
                if calls["home_port"] <= failures:
                    raise ContinuousUiDriverError("the Home Port is not on screen yet")
                return UiStateEvidence(
                    state="HOME_PORT",
                    consecutive_states=("HOME_PORT", "HOME_PORT"),
                    evidence="two_consecutive_in_memory_ooi_fingerprints",
                )
            raise ContinuousUiDriverError(f"not this screen: {state}")

        return verify

    def test_the_loop_waits_before_its_first_click(self):
        # The client often settles on its own - after a start click it shows
        # the fleet page and then drops back to the expedition list - so the
        # first round waits instead of clicking.
        driver, game_input, events = self.make_driver(
            verify=self.verify_with_port_after(2)
        )
        evidence = driver.return_to_home_port(fleet_id=3)
        self.assertEqual(evidence.state, "HOME_PORT")
        self.assertEqual(game_input.clicks, ["home-port"])
        self.assertTrue(any(
            name == "continuous_ui_return_by_observation_wait" for name, _ in events
        ))
        self.assertTrue(any(
            name == "continuous_ui_return_by_observation_attempt" for name, _ in events
        ))

    def test_the_loop_never_clicks_when_the_port_appears_on_its_own(self):
        driver, game_input, events = self.make_driver(
            verify=self.verify_with_port_after(1)
        )
        evidence = driver.return_to_home_port(fleet_id=3)
        self.assertEqual(evidence.state, "HOME_PORT")
        self.assertEqual(game_input.clicks, [])
        self.assertTrue(any(
            name == "continuous_ui_return_by_observation_wait" for name, _ in events
        ))

    def test_a_reward_screen_stops_the_loop_before_any_click(self):
        def verify(state, *, visual_attempts=None):
            if state == "RETURN_RESULT":
                return UiStateEvidence(
                    state="RETURN_RESULT",
                    consecutive_states=("RETURN_RESULT", "RETURN_RESULT"),
                    evidence="two_consecutive_frames",
                )
            raise ContinuousUiDriverError(f"not this screen: {state}")

        driver, game_input, events = self.make_driver(verify=verify)
        with self.assertRaisesRegex(ContinuousUiDriverError, "reward screen"):
            driver.return_to_home_port(fleet_id=3)
        self.assertEqual(game_input.clicks, [])
        self.assertTrue(any(
            name == "continuous_ui_reward_screen_blocks_return" for name, _ in events
        ))

    def test_the_loop_is_bounded(self):
        def verify(state, *, visual_attempts=None):
            raise ContinuousUiDriverError(f"not this screen: {state}")

        driver, game_input, _ = self.make_driver(verify=verify)
        with self.assertRaisesRegex(ContinuousUiDriverError, "not reached after"):
            driver.return_to_home_port(fleet_id=3)
        self.assertLessEqual(len(game_input.clicks), 6)

    def test_a_named_page_never_reaches_the_loop(self):
        driver, game_input, _ = self.make_driver(
            verify=self.verify_with_port_after(1)
        )
        with self.assertRaises(ContinuousUiDriverError):
            driver.return_to_home_port(
                fleet_id=3, expected_current_state="EXPEDITION_LIST"
            )
        self.assertEqual(game_input.clicks, [])


if __name__ == "__main__":
    unittest.main()
