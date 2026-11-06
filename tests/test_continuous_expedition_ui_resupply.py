"""The production UI driver's supply chain: verify every click, never guess.

These tests drive ``CdpContinuousExpeditionUiDriver.resupply_fleet`` with a
scripted screen. They prove the order of the clicks, that the supply click only
happens after the fleet tab and a stable button are verified, and that every
failure happens *before* the irreversible click.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from kancollebot.adapters.poi.cdp_input import RecordedPoint
from kancollebot.adapters.poi.continuous_expedition_ui import (
    CdpContinuousExpeditionUiDriver,
    ContinuousUiDriverError,
)
from kancollebot.adapters.poi.visual_target import VisualTargetPoint
from kancollebot.core.observation_contracts import UiStateEvidence


class FakeDetails:
    def reference(self, expedition_id: str):
        return None

    def require(self, expedition_id: str, screenshot_base64: str):
        raise AssertionError("resupply must not read expedition details")


class FakeMarks:
    def require_decision(self, screenshot_base64: str):
        raise AssertionError("resupply must not read dispatch marks")

    def require_fleet(self, fleet_id: int, screenshot_base64: str):
        raise AssertionError("resupply must not read fleet marks")

    def fleets(self) -> tuple[int, ...]:
        return (2, 3)


class ScriptedScreen:
    """A UI state that changes only when the scripted click lands."""

    def __init__(self, state: str) -> None:
        self.state = state
        self.verified: list[str] = []

    def verify(self, expected: str, *, visual_attempts: int | None = None) -> UiStateEvidence:
        if expected != self.state:
            raise ContinuousUiDriverError(
                f"expected {expected}, screen shows {self.state}"
            )
        self.verified.append(expected)
        return UiStateEvidence(
            state=expected,
            consecutive_states=(expected, expected),
            evidence="scripted",
        )


def build_driver(
    screen: ScriptedScreen,
    events: list[str],
    *,
    menu_state: str = "SUPPLY_FLEET_2_SELECTED",
):
    """Wire a driver whose clicks move the scripted screen.

    ``menu_state`` is what the supply screen shows right after the supply-menu
    click: the game reopens it on the last used fleet tab, so either a specific
    fleet or the bare menu is a realistic starting point.
    """
    clicks: list[str] = []
    transitions = {
        "supply-menu": menu_state,
        "supply-fleet2-tab": "SUPPLY_FLEET_2_SELECTED",
        "supply-fleet3-tab": "SUPPLY_FLEET_3_SELECTED",
        "supply-fleet4-tab": "SUPPLY_FLEET_4_SELECTED",
        "home-port": "HOME_PORT",
    }

    def click(point: RecordedPoint) -> None:
        clicks.append(point.label)
        if point.label in transitions:
            screen.state = transitions[point.label]

    driver = CdpContinuousExpeditionUiDriver(
        game_input=type("Input", (), {"click": staticmethod(click)})(),
        verify_state=screen.verify,
        event_sink=lambda event, payload: events.append(event),
        details=FakeDetails(),
        marks=FakeMarks(),
        capture=lambda: "frame",
        sleep=lambda seconds: None,
        settle_seconds=0.0,
    )
    return driver, clicks


def target(bounds: tuple[int, int, int, int], label: str = "fleet-supply-all-button"):
    x, y, width, height = bounds
    return VisualTargetPoint(
        x=x + width / 2.0,
        y=y + height / 2.0,
        label=label,
        bounds=bounds,
        region=(1050, 590, 145, 60),
        source_frame_sha256="0" * 64,
        locator="opencv-canny-button-v1",
    )


class ResupplyChainTests(unittest.TestCase):
    def test_already_selected_fleet_needs_no_tab_click(self):
        screen = ScriptedScreen("HOME_PORT")
        events: list[str] = []
        driver, clicks = build_driver(screen, events)
        button = target((1090, 600, 90, 30))

        with patch(
            "kancollebot.adapters.poi.continuous_expedition_ui.locate_fleet_supply_all_button",
            return_value=button,
        ):
            evidence = driver.resupply_fleet(fleet_id=2)

        self.assertEqual(evidence.state, "HOME_PORT")
        self.assertEqual(evidence.consecutive_states, ("HOME_PORT", "HOME_PORT"))
        self.assertEqual(
            clicks, ["supply-menu", "fleet-supply-all-button", "home-port"]
        )
        self.assertIn("continuous_ui_supply_button_verified", events)
        self.assertIn("continuous_ui_supply_click_sent", events)
        self.assertIn("continuous_ui_resupply_completed", events)

    def test_a_supply_page_for_another_fleet_clicks_the_requested_tab_first(self):
        screen = ScriptedScreen("HOME_PORT")
        events: list[str] = []
        driver, clicks = build_driver(screen, events, menu_state="SUPPLY_MENU")
        button = target((1090, 600, 90, 30))
        tab = VisualTargetPoint(
            x=310,
            y=180,
            label="supply-fleet3-tab",
            bounds=(295, 165, 30, 30),
            region=(285, 155, 50, 50),
            source_frame_sha256="1" * 64,
            locator="opencv-canny-tab-v1",
        )

        with patch(
            "kancollebot.adapters.poi.continuous_expedition_ui.locate_fleet_supply_all_button",
            return_value=button,
        ), patch(
            "kancollebot.adapters.poi.continuous_expedition_ui.locate_supply_fleet_tab",
            return_value=tab,
        ):
            evidence = driver.resupply_fleet(fleet_id=3)

        self.assertEqual(evidence.state, "HOME_PORT")
        self.assertEqual(
            clicks,
            [
                "supply-menu",
                "supply-fleet3-tab",
                "fleet-supply-all-button",
                "home-port",
            ],
        )

    def test_a_moving_supply_button_is_never_clicked(self):
        screen = ScriptedScreen("HOME_PORT")
        events: list[str] = []
        driver, clicks = build_driver(screen, events)
        buttons = [
            target((1090, 600, 90, 30)),
            target((1092, 601, 90, 30)),
            target((1090, 600, 90, 30)),
        ]

        with patch(
            "kancollebot.adapters.poi.continuous_expedition_ui.locate_fleet_supply_all_button",
            side_effect=buttons,
        ):
            with self.assertRaisesRegex(ContinuousUiDriverError, "moved across three frames"):
                driver.resupply_fleet(fleet_id=2)

        self.assertEqual(clicks, ["supply-menu"])
        self.assertNotIn("continuous_ui_supply_click_sent", events)

    def test_a_missing_supply_button_is_never_clicked(self):
        screen = ScriptedScreen("HOME_PORT")
        events: list[str] = []
        driver, clicks = build_driver(screen, events)

        with patch(
            "kancollebot.adapters.poi.continuous_expedition_ui.locate_fleet_supply_all_button",
            side_effect=RuntimeError("no contour"),
        ):
            with self.assertRaisesRegex(
                ContinuousUiDriverError, "supply button could not be located"
            ):
                driver.resupply_fleet(fleet_id=2)

        self.assertEqual(clicks, ["supply-menu"])
        self.assertNotIn("continuous_ui_supply_click_sent", events)

    def test_fleet_4_supply_tab_is_located_and_the_chain_completes(self):
        """P0-1: Fleet 4's supply tab is reachable without a fingerprint template."""
        screen = ScriptedScreen("HOME_PORT")
        events: list[str] = []
        driver, clicks = build_driver(screen, events, menu_state="SUPPLY_MENU")
        button = target((1090, 600, 90, 30))
        tab = VisualTargetPoint(
            x=355,
            y=180,
            label="supply-fleet4-tab",
            bounds=(340, 165, 30, 30),
            region=(330, 155, 50, 50),
            source_frame_sha256="4" * 64,
            locator="opencv-canny-tab-v1",
        )

        with patch(
            "kancollebot.adapters.poi.continuous_expedition_ui.locate_fleet_supply_all_button",
            return_value=button,
        ), patch(
            "kancollebot.adapters.poi.continuous_expedition_ui.locate_supply_fleet_tab",
            return_value=tab,
        ):
            evidence = driver.resupply_fleet(fleet_id=4)

        self.assertEqual(evidence.state, "HOME_PORT")
        self.assertEqual(
            clicks,
            [
                "supply-menu",
                "supply-fleet4-tab",
                "fleet-supply-all-button",
                "home-port",
            ],
        )
        self.assertIn("continuous_ui_supply_click_sent", events)

    def test_fleet_4_on_an_unidentifiable_supply_page_sends_no_tab_click(self):
        """No template names the page: the chain stops before the tab click.

        Fleet 4 has a template now (live 2026-11-06), so the old
        "no template" refusal is gone - but an unrecognised supply page must
        still stop the chain *before* the tab click rather than clicking a tab
        on a screen that was never proven to be a supply page. A screen that
        stays on HOME_PORT after the supply-menu click matches nothing.
        """
        screen = ScriptedScreen("HOME_PORT")
        events: list[str] = []
        driver, clicks = build_driver(screen, events, menu_state="HOME_PORT")

        with patch(
            "kancollebot.adapters.poi.continuous_expedition_ui.locate_fleet_supply_all_button",
            return_value=target((1090, 600, 90, 30)),
        ):
            with self.assertRaisesRegex(
                ContinuousUiDriverError, "not a verified supply page"
            ):
                driver.resupply_fleet(fleet_id=4)

        self.assertEqual(clicks, ["supply-menu"])
        self.assertNotIn("continuous_ui_supply_click_sent", events)

    def test_fleet_4_tab_that_lands_on_another_fleet_sends_no_supply_click(self):
        """Wrong fleet after the tab click: the supply button is never pressed.

        This is the failure mode Fleet 4's missing template makes possible, so
        it is the one that must be covered: the tab click lands, the frame
        proves a *different* fleet, and the chain stops with no supply click.
        """
        screen = ScriptedScreen("HOME_PORT")
        events: list[str] = []
        driver, clicks = build_driver(screen, events, menu_state="SUPPLY_FLEET_2_SELECTED")
        tab = VisualTargetPoint(
            x=355,
            y=180,
            label="supply-fleet4-tab",
            bounds=(340, 165, 30, 30),
            region=(330, 155, 50, 50),
            source_frame_sha256="4" * 64,
            locator="opencv-canny-tab-v1",
        )

        def click_that_selects_fleet3(point: RecordedPoint) -> None:
            clicks.append(point.label)
            if point.label == "supply-menu":
                screen.state = "SUPPLY_FLEET_2_SELECTED"
            elif point.label == "supply-fleet4-tab":
                # The click landed on the neighbouring tab: the frame now
                # proves Fleet 3, not the requested Fleet 4.
                screen.state = "SUPPLY_FLEET_3_SELECTED"

        driver._input = type(
            "Input", (), {"click": staticmethod(click_that_selects_fleet3)}
        )()

        with patch(
            "kancollebot.adapters.poi.continuous_expedition_ui.locate_fleet_supply_all_button",
            return_value=target((1090, 600, 90, 30)),
        ), patch(
            "kancollebot.adapters.poi.continuous_expedition_ui.locate_supply_fleet_tab",
            return_value=tab,
        ):
            with self.assertRaises(ContinuousUiDriverError):
                driver.resupply_fleet(fleet_id=4)

        self.assertEqual(clicks, ["supply-menu", "supply-fleet4-tab"])
        self.assertNotIn("continuous_ui_supply_click_sent", events)
        self.assertNotIn("continuous_ui_resupply_completed", events)

    def test_an_unrecognised_screen_stops_before_any_supply_click(self):
        screen = ScriptedScreen("HOME_PORT")
        events: list[str] = []
        driver, clicks = build_driver(screen, events, menu_state="EXPEDITION_LIST")

        with self.assertRaisesRegex(ContinuousUiDriverError, "not a verified supply page"):
            driver.resupply_fleet(fleet_id=2)

        self.assertEqual(clicks, ["supply-menu"])
        self.assertNotIn("continuous_ui_supply_click_sent", events)
        self.assertNotIn("continuous_ui_resupply_completed", events)


class FadingPortScreen:
    """A supply screen whose 母港 click starts a scene fade, not an instant cut.

    Live 2026-11-05 the client was still painting the supply page 2.6 s after the
    母港 click; the single post-click check called that a failure and ended a
    resupply that had in fact worked.
    """

    def __init__(self, *, fade_reads: int) -> None:
        self.state = "HOME_PORT"
        self.fade_reads = fade_reads
        self.left_supply = False
        self.clicks: list[str] = []

    def click(self, point: RecordedPoint) -> None:
        self.clicks.append(point.label)
        if point.label == "supply-menu":
            self.state = "SUPPLY_FLEET_2_SELECTED"
        elif point.label == "home-port":
            self.left_supply = True

    def verify(self, expected: str, *, visual_attempts: int | None = None) -> UiStateEvidence:
        if expected == "HOME_PORT" and self.left_supply:
            if self.fade_reads > 0:
                self.fade_reads -= 1
                raise ContinuousUiDriverError(
                    "expected HOME_PORT, screen shows SUPPLY_FLEET_2_SELECTED"
                )
            self.state = "HOME_PORT"
        if expected != self.state:
            raise ContinuousUiDriverError(f"expected {expected}, screen shows {self.state}")
        return UiStateEvidence(
            state=expected,
            consecutive_states=(expected, expected),
            evidence="scripted",
        )


def build_fading_driver(screen: FadingPortScreen, events: list[str]):
    driver = CdpContinuousExpeditionUiDriver(
        game_input=screen,
        verify_state=screen.verify,
        event_sink=lambda event, payload: events.append(event),
        details=FakeDetails(),
        marks=FakeMarks(),
        capture=lambda: "frame",
        sleep=lambda seconds: None,
        settle_seconds=0.0,
    )
    return driver


class ResupplyExitFadeTests(unittest.TestCase):
    def test_the_home_port_behind_a_fade_is_waited_for(self):
        screen = FadingPortScreen(fade_reads=2)
        events: list[str] = []
        driver = build_fading_driver(screen, events)

        with patch(
            "kancollebot.adapters.poi.continuous_expedition_ui.locate_fleet_supply_all_button",
            return_value=target((1090, 600, 90, 30)),
        ):
            evidence = driver.resupply_fleet(fleet_id=2)

        self.assertEqual(evidence.state, "HOME_PORT")
        self.assertEqual(screen.state, "HOME_PORT")
        self.assertEqual(
            screen.clicks, ["supply-menu", "fleet-supply-all-button", "home-port"]
        )
        self.assertIn("continuous_ui_reward_screen_pending", events)

    def test_a_port_that_never_appears_stops_after_the_single_exit_click(self):
        screen = FadingPortScreen(fade_reads=99)
        driver = build_fading_driver(screen, [])

        with patch(
            "kancollebot.adapters.poi.continuous_expedition_ui.locate_fleet_supply_all_button",
            return_value=target((1090, 600, 90, 30)),
        ):
            with self.assertRaises(ContinuousUiDriverError):
                driver.resupply_fleet(fleet_id=2)

        self.assertEqual(
            screen.clicks, ["supply-menu", "fleet-supply-all-button", "home-port"]
        )


if __name__ == "__main__":
    unittest.main()
