"""The production UI driver's settlement chain: one proven click per reward screen.

The first two reward screens (return notice -> result -> bonus items) advance on
a click anywhere, so no button is located there. The bonus screen is the one
exception: it is left through its 次 control, whose coordinates were verified on
the live client. What these tests pin down is everything else: the state before
each click is proven, the click happens exactly once, the resulting screen is
proven before the chain moves on, and any mismatch stops the chain instead of
retrying a click whose outcome is unknown.
"""
from __future__ import annotations

import unittest

from kancollebot.adapters.poi.cdp_input import RecordedPoint
from kancollebot.adapters.poi.continuous_expedition_ui import (
    BONUS_NEXT,
    REWARD_ADVANCE,
    CdpContinuousExpeditionUiDriver,
    ContinuousUiDriverError,
)
from kancollebot.core.observation_contracts import UiStateEvidence

try:  # `unittest discover -s tests` puts this directory on sys.path
    from home_port_locator_stub import STUB_HOME_PORT_LOCATOR
except ImportError:  # `-m unittest tests.test_...` imports it as a package
    from tests.home_port_locator_stub import STUB_HOME_PORT_LOCATOR


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
        home_port_locator=STUB_HOME_PORT_LOCATOR,
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

    def test_a_chain_already_at_bonus_items_resumes_without_a_click(self) -> None:
        # Resuming is the whole point of probing the target first: a settlement
        # interrupted between two screens must finish, not skip a screen with a
        # blind click (live 2026-11-05: api_mission cleared mid-chain).
        screen = ScriptedScreen("BONUS_ITEMS")
        clicks: list[RecordedPoint] = []
        driver = build_driver(screen, clicks)

        evidence = driver.claim_bonus_items(fleet_id=2)

        self.assertEqual(evidence.state, "BONUS_ITEMS")
        self.assertEqual(clicks, [])

    def test_a_screen_past_the_target_is_still_an_error(self) -> None:
        # Only the exact target counts as "already advanced"; a screen beyond it
        # means the chain ran further than the scheduler believes, which is not
        # something to paper over.
        screen = ScriptedScreen("BONUS_ITEMS")
        clicks: list[RecordedPoint] = []
        driver = build_driver(screen, clicks)

        with self.assertRaises(ContinuousUiDriverError):
            driver.collect_return_result(fleet_id=3)

        self.assertEqual(clicks, [])


class ClaimRewardTests(unittest.TestCase):
    def test_bonus_items_returns_to_home_port(self) -> None:
        screen = ScriptedScreen("BONUS_ITEMS", {"BONUS_ITEMS": "HOME_PORT"})
        clicks: list[RecordedPoint] = []
        driver = build_driver(screen, clicks)

        evidence = driver.claim_reward(fleet_id=3)

        self.assertEqual(evidence.state, "HOME_PORT")
        self.assertEqual(len(clicks), 1)
        self.assertEqual(clicks[0], BONUS_NEXT)
        self.assertEqual(screen.verified, ["BONUS_ITEMS", "HOME_PORT"])

    def test_reward_requires_the_bonus_screen(self) -> None:
        screen = ScriptedScreen("RETURN_RESULT")
        clicks: list[RecordedPoint] = []
        driver = build_driver(screen, clicks)

        with self.assertRaises(ContinuousUiDriverError):
            driver.claim_reward(fleet_id=3)

        self.assertEqual(clicks, [])


class FadingScreen:
    """A screen the client reaches through the port background, not instantly.

    ``fade_reads`` is how many times a check for the target has to fail before
    the target is really on screen, which is what the live client did for ~2 s
    after the notice click on 2026-11-05.
    """

    def __init__(self, initial: str, *, target: str, fade_reads: int) -> None:
        self.state = initial
        self.target = target
        self.fade_reads = fade_reads
        self.clicks = 0

    def click(self, point: RecordedPoint) -> None:
        # The click advances the client, but the next screen is still fading in.
        self.clicks += 1

    def verify(self, expected: str, *, visual_attempts: int | None = None) -> UiStateEvidence:
        if expected == self.target:
            if not self.clicks:
                # Before the click the target simply is not there yet.
                raise ContinuousUiDriverError(f"expected {expected}, screen shows {self.state}")
            if self.fade_reads > 0:
                self.fade_reads -= 1
                self.state = "HOME_PORT"
                raise ContinuousUiDriverError(
                    f"expected {expected}, screen shows HOME_PORT"
                )
            self.state = self.target
        if expected != self.state:
            raise ContinuousUiDriverError(f"expected {expected}, screen shows {self.state}")
        return UiStateEvidence(
            state=expected,
            consecutive_states=(expected, expected),
            evidence="scripted",
        )


def build_fading_driver(
    screen: FadingScreen,
    *,
    events: list[str] | None = None,
) -> CdpContinuousExpeditionUiDriver:
    log = events if events is not None else []
    return CdpContinuousExpeditionUiDriver(
        home_port_locator=STUB_HOME_PORT_LOCATOR,
        game_input=screen,
        verify_state=screen.verify,
        event_sink=lambda event, payload: log.append(event),
        details=FakeDetails(),
        marks=FakeMarks(),
        capture=lambda: "unused",
        locate_row=None,
        sleep=lambda _seconds: None,
    )


class RewardScreenFadeTests(unittest.TestCase):
    def test_a_screen_behind_a_fade_is_waited_for(self) -> None:
        # The post-click frame is the port background, a *known* state that is
        # not the target. It must be polled through, not treated as a failure.
        screen = FadingScreen("HOME_PORT_RETURN_NOTICE", target="RETURN_RESULT", fade_reads=3)
        events: list[str] = []
        driver = build_fading_driver(screen, events=events)

        evidence = driver.collect_return_result(fleet_id=3)

        self.assertEqual(evidence.state, "RETURN_RESULT")
        self.assertEqual(screen.state, "RETURN_RESULT")
        self.assertEqual(screen.clicks, 1)
        self.assertEqual(
            [name for name in events if name == "continuous_ui_reward_screen_pending"],
            ["continuous_ui_reward_screen_pending"] * 3,
        )

    def test_a_screen_that_never_arrives_stops_after_the_single_click(self) -> None:
        # Waiting is read-only: a fade that never lands must still stop the
        # chain with exactly the one click the caller already sent.
        screen = FadingScreen("HOME_PORT_RETURN_NOTICE", target="RETURN_RESULT", fade_reads=99)
        driver = build_fading_driver(screen)

        with self.assertRaises(ContinuousUiDriverError):
            driver.collect_return_result(fleet_id=3)

        self.assertEqual(screen.clicks, 1)


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

        # Two empty-area clicks (notice, result) plus one 次 click (bonus).
        self.assertEqual(len([c for c in clicks if c == REWARD_ADVANCE]), 2)
        self.assertEqual(len([c for c in clicks if c == BONUS_NEXT]), 1)
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


class SettlementScreenAbsenceTests(unittest.TestCase):
    """The settlement screen probe only claims "absent" on a positive Home Port.

    Defect 1: a result screen still fading in scores below the accept line, so
    it is neither RETURN_RESULT nor HOME_PORT. Treating that negative as "no
    settlement screen" closed a chain one click from its reward (live 2026-11-07).
    """

    def test_a_settlement_screen_is_detected_not_absent(self) -> None:
        screen = ScriptedScreen("RETURN_RESULT")
        clicks: list[RecordedPoint] = []
        driver = build_driver(screen, clicks)
        self.assertEqual(driver.settlement_screen(fleet_id=2), "RETURN_RESULT")
        self.assertEqual(clicks, [])

    def test_home_port_is_the_only_proof_of_absence(self) -> None:
        screen = ScriptedScreen("HOME_PORT")
        clicks: list[RecordedPoint] = []
        events: list[str] = []
        driver = build_driver(screen, clicks, events=events)
        self.assertIsNone(driver.settlement_screen(fleet_id=2))
        self.assertEqual(clicks, [])
        self.assertIn("continuous_ui_settlement_screen_absent", events)

    def test_an_unrecognized_non_home_screen_refuses_absence(self) -> None:
        # Not a settlement screen and not Home Port: absence is unprovable, so
        # the probe must refuse rather than return None.
        screen = ScriptedScreen("SORTIE_MENU")
        clicks: list[RecordedPoint] = []
        events: list[str] = []
        driver = build_driver(screen, clicks, events=events)
        with self.assertRaises(ContinuousUiDriverError):
            driver.settlement_screen(fleet_id=2)
        self.assertEqual(clicks, [])
        self.assertIn("continuous_ui_settlement_absence_unproven", events)
        self.assertNotIn("continuous_ui_settlement_screen_absent", events)


if __name__ == "__main__":
    unittest.main()
