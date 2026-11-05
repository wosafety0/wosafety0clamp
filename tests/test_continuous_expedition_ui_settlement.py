"""The production UI driver's settlement chain: one proven click per reward screen.

The three reward screens (return notice -> result -> bonus items -> Home Port)
advance on a click anywhere, so no button is located. What these tests pin down
is everything else: the state before each click is proven, the click happens
exactly once, the resulting screen is proven before the chain moves on, and any
mismatch stops the chain instead of retrying a click whose outcome is unknown.
"""
from __future__ import annotations

import unittest

from kancollebot.adapters.poi.cdp_input import RecordedPoint
from kancollebot.adapters.poi.continuous_expedition_ui import (
    REWARD_ADVANCE,
    CdpContinuousExpeditionUiDriver,
    ContinuousUiDriverError,
)
from kancollebot.core.observation_contracts import UiStateEvidence


class FakeDetails:
    def reference(self, expedition_id: str):
        return None

    def require(self, expedition_id: str, screenshot_base64: str):
        raise AssertionError("settlement must not read expedition details")


class FakeMarks:
    def require_decision(self, screenshot_base64: str):
        raise AssertionError("settlement must not read dispatch marks")

    def require_fleet(self, fleet_id: int, screenshot_base64: str):
        raise AssertionError("settlement must not read fleet marks")

    def fleets(self) -> tuple[int, ...]:
        return (2, 3)


class ScriptedScreen:
    """A UI state that advances only when a click lands."""

    def __init__(self, initial: str, transitions: dict[str, str] | None = None) -> None:
        self.state = initial
        self.transitions = dict(transitions or {})
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


class FakeInput:
    def __init__(self, screen: ScriptedScreen, clicks: list[RecordedPoint]) -> None:
        self._screen = screen
        self._clicks = clicks

    def click(self, point: RecordedPoint) -> None:
        self._clicks.append(point)
        next_state = self._screen.transitions.get(self._screen.state)
        if next_state is not None:
            self._screen.state = next_state


def build_driver(
    screen: ScriptedScreen,
    clicks: list[RecordedPoint],
    *,
    events: list[str] | None = None,
    supported_fleets: tuple[int, ...] = (2, 3, 4),
) -> CdpContinuousExpeditionUiDriver:
    log = events if events is not None else []
    return CdpContinuousExpeditionUiDriver(
        game_input=FakeInput(screen, clicks),
        verify_state=screen.verify,
        event_sink=lambda event, payload: log.append(event),
        details=FakeDetails(),
        marks=FakeMarks(),
        capture=lambda: "unused",
        locate_row=None,
        supported_fleets=supported_fleets,
        sleep=lambda _seconds: None,
    )


class CollectReturnResultTests(unittest.TestCase):
    def test_notice_advances_to_result_with_one_click(self) -> None:
        screen = ScriptedScreen(
            "HOME_PORT_RETURN_NOTICE", {"HOME_PORT_RETURN_NOTICE": "RETURN_RESULT"}
        )
        clicks: list[RecordedPoint] = []
        driver = build_driver(screen, clicks)

        evidence = driver.collect_return_result(fleet_id=3)

        self.assertEqual(screen.state, "RETURN_RESULT")
        self.assertEqual(evidence.state, "RETURN_RESULT")
        self.assertEqual(len(clicks), 1)
        self.assertEqual(clicks[0], REWARD_ADVANCE)
        self.assertEqual(
            screen.verified, ["HOME_PORT_RETURN_NOTICE", "RETURN_RESULT"]
        )

    def test_wrong_precondition_clicks_nothing(self) -> None:
        # A plain Home Port is not a return notice: the chain must refuse rather
        # than click into whatever is on screen.
        screen = ScriptedScreen("HOME_PORT", {"HOME_PORT": "RETURN_RESULT"})
        clicks: list[RecordedPoint] = []
        driver = build_driver(screen, clicks)

        with self.assertRaises(ContinuousUiDriverError):
            driver.collect_return_result(fleet_id=3)

        self.assertEqual(clicks, [])
        self.assertEqual(screen.state, "HOME_PORT")

    def test_unexpected_result_state_stops_after_one_click(self) -> None:
        # The click lands but the client stays on the notice: the driver must not
        # click again, because a second click could skip a reward screen.
        screen = ScriptedScreen("HOME_PORT_RETURN_NOTICE")
        clicks: list[RecordedPoint] = []
        driver = build_driver(screen, clicks)

        with self.assertRaises(ContinuousUiDriverError):
            driver.collect_return_result(fleet_id=3)

        self.assertEqual(len(clicks), 1)


class ClaimBonusItemsTests(unittest.TestCase):
    def test_result_advances_to_bonus_items(self) -> None:
        screen = ScriptedScreen("RETURN_RESULT", {"RETURN_RESULT": "BONUS_ITEMS"})
        clicks: list[RecordedPoint] = []
        driver = build_driver(screen, clicks)

        evidence = driver.claim_bonus_items(fleet_id=2)

        self.assertEqual(evidence.state, "BONUS_ITEMS")
        self.assertEqual(len(clicks), 1)
        self.assertEqual(clicks[0], REWARD_ADVANCE)

    def test_bonus_items_is_not_accepted_as_a_result_screen(self) -> None:
        screen = ScriptedScreen("BONUS_ITEMS")
        clicks: list[RecordedPoint] = []
        driver = build_driver(screen, clicks)

        with self.assertRaises(ContinuousUiDriverError):
            driver.claim_bonus_items(fleet_id=2)

        self.assertEqual(clicks, [])


class ClaimRewardTests(unittest.TestCase):
    def test_bonus_items_returns_to_home_port(self) -> None:
        screen = ScriptedScreen("BONUS_ITEMS", {"BONUS_ITEMS": "HOME_PORT"})
        clicks: list[RecordedPoint] = []
        driver = build_driver(screen, clicks)

        evidence = driver.claim_reward(fleet_id=3)

        self.assertEqual(evidence.state, "HOME_PORT")
        self.assertEqual(len(clicks), 1)
        self.assertEqual(screen.verified, ["BONUS_ITEMS", "HOME_PORT"])

    def test_reward_requires_the_bonus_screen(self) -> None:
        screen = ScriptedScreen("RETURN_RESULT")
        clicks: list[RecordedPoint] = []
        driver = build_driver(screen, clicks)

        with self.assertRaises(ContinuousUiDriverError):
            driver.claim_reward(fleet_id=3)

        self.assertEqual(clicks, [])


class FleetScopeTests(unittest.TestCase):
    def test_unsupported_fleet_is_refused_before_any_click(self) -> None:
        screen = ScriptedScreen(
            "HOME_PORT_RETURN_NOTICE", {"HOME_PORT_RETURN_NOTICE": "RETURN_RESULT"}
        )
        clicks: list[RecordedPoint] = []
        driver = build_driver(screen, clicks, supported_fleets=(2, 3))

        with self.assertRaises(ContinuousUiDriverError):
            driver.collect_return_result(fleet_id=4)

        self.assertEqual(clicks, [])
        self.assertEqual(screen.verified, [])

    def test_non_positive_fleet_id_is_refused(self) -> None:
        screen = ScriptedScreen("BONUS_ITEMS", {"BONUS_ITEMS": "HOME_PORT"})
        clicks: list[RecordedPoint] = []
        driver = build_driver(screen, clicks)

        for bad in (0, -1, True):
            with self.subTest(fleet_id=bad):
                with self.assertRaises(ContinuousUiDriverError):
                    driver.claim_reward(fleet_id=bad)  # type: ignore[arg-type]
        self.assertEqual(clicks, [])


class EventTrailTests(unittest.TestCase):
    def test_each_step_reports_start_and_completion(self) -> None:
        screen = ScriptedScreen(
            "HOME_PORT_RETURN_NOTICE",
            {
                "HOME_PORT_RETURN_NOTICE": "RETURN_RESULT",
                "RETURN_RESULT": "BONUS_ITEMS",
                "BONUS_ITEMS": "HOME_PORT",
            },
        )
        clicks: list[RecordedPoint] = []
        events: list[str] = []
        driver = build_driver(screen, clicks, events=events)

        driver.collect_return_result(fleet_id=3)
        driver.claim_bonus_items(fleet_id=3)
        driver.claim_reward(fleet_id=3)

        self.assertEqual(len([c for c in clicks if c == REWARD_ADVANCE]), 3)
        self.assertEqual(
            [name for name in events if name == "continuous_ui_settlement_step_started"],
            ["continuous_ui_settlement_step_started"] * 3,
        )
        self.assertEqual(
            [name for name in events if name == "continuous_ui_settlement_step_completed"],
            ["continuous_ui_settlement_step_completed"] * 3,
        )
        self.assertEqual(
            [name for name in events if name == "continuous_ui_click_sent"],
            ["continuous_ui_click_sent"] * 3,
        )


if __name__ == "__main__":
    unittest.main()
