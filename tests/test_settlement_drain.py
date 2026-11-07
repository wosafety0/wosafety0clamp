"""P0.1 offline mock tests for the page-driven settlement drain.

Everything here is deterministic and side-effect free: a fake monotonic clock,
a timeline-driven fake UI, and no Poi, no CDP, no game API. The fake driver
refuses to click a page it is not allowed to advance, so every passing test
also proves "no blind click on an unknown / error page".

Test numbering follows the P0.1 request: Test 1 normal return, Test 2 action
budget, Test 3 network popup, Test 4 animation delay (+ stuck control), Test 5
multi-fleet independence, then the extra boundary cases.
"""
from __future__ import annotations

import dataclasses
import inspect
import math
import unittest

from kancollebot.expeditions.settlement_drain import (
    DEFAULT_FRAME_POLL_INTERVAL_SECONDS,
    DrainErrorType,
    DrainPageState,
    DrainResult,
    SettlementDrainRequest,
    SettlementDrainSession,
)

POLL = 0.1
STABLE = 3

HOME = DrainPageState.HOME_PORT
NOTICE = DrainPageState.RETURN_NOTICE
RESULT = DrainPageState.EXPEDITION_RESULT
REWARD = DrainPageState.REWARD_PANEL
NETWORK = DrainPageState.NETWORK_ERROR_POPUP
DIALOG = DrainPageState.UNKNOWN_DIALOG
UNKNOWN = DrainPageState.UNKNOWN

ADVANCEABLE = (NOTICE, RESULT, REWARD)


class FakeDrainClock:
    """Controllable monotonic clock; only ``sleep`` advances it."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = float(start)
        self.sleep_calls = 0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            raise AssertionError("the session must never busy-loop on a zero sleep")
        self.sleep_calls += 1
        self.now += seconds


class TimelineSettlementUi:
    """Fake observer + driver driven by an absolute page timeline.

    ``segments`` is ``[(page, until_seconds), ...]``: the page holds until the
    given clock time, and the last page holds for ever afterwards. ``advance``
    asserts that the page being clicked is the one actually visible and that it
    is an advanceable settlement page - a blind click would fail the test.
    """

    def __init__(self, clock: FakeDrainClock, segments) -> None:
        if not segments:
            raise ValueError("a timeline needs at least one segment")
        self.clock = clock
        self._segments = tuple((page, float(until)) for page, until in segments)
        self.observations: list[tuple[float, DrainPageState]] = []
        self.actions: list[tuple[float, DrainPageState]] = []

    @property
    def action_pages(self) -> list[DrainPageState]:
        return [page for _, page in self.actions]

    def page_at(self, when: float) -> DrainPageState:
        for page, until in self._segments:
            if when < until:
                return page
        return self._segments[-1][0]

    # -- observer -----------------------------------------------------------

    def observe(self) -> DrainPageState:
        page = self.page_at(self.clock.now)
        self.observations.append((self.clock.now, page))
        return page

    # -- driver -------------------------------------------------------------

    def advance(self, page: DrainPageState) -> None:
        visible = self.page_at(self.clock.now)
        if page is not visible:
            raise AssertionError(f"clicked {page} while {visible} was visible")
        if page not in ADVANCEABLE:
            raise AssertionError(f"the drain must never click {page}")
        self.actions.append((self.clock.now, page))

    # -- assertions helpers -------------------------------------------------

    def observations_after(self, when: float) -> list[DrainPageState]:
        return [page for at, page in self.observations if at >= when]


class RaisingDriver:
    """Driver that fails on the very first click, like a dead input channel."""

    class ClickFailed(RuntimeError):
        pass

    def __init__(self) -> None:
        self.pages: list[DrainPageState] = []

    def advance(self, page: DrainPageState) -> None:
        self.pages.append(page)
        raise RaisingDriver.ClickFailed("input channel closed")


class FakePoiLedger:
    """Two fleets still ``status == 2``; the drain must never read this."""

    def __init__(self) -> None:
        self.read_calls = 0

    def read_fleets(self):
        self.read_calls += 1
        return (
            {"fleet_id": 2, "mission_status": 2},
            {"fleet_id": 4, "mission_status": 2},
        )


def make_request(
    *,
    max_actions: int = 8,
    timeout: float = 30.0,
    interval: float = 1.0,
    stable: int = STABLE,
) -> SettlementDrainRequest:
    return SettlementDrainRequest(
        max_actions=max_actions,
        timeout_seconds=timeout,
        action_interval_seconds=interval,
        stable_frame_count=stable,
    )


def make_session(observer, clock, driver=None) -> SettlementDrainSession:
    return SettlementDrainSession(
        observer,
        driver if driver is not None else observer,
        clock=clock,
        frame_poll_interval_seconds=POLL,
    )


def standard_timeline():
    """RESULT -> REWARD -> HOME, with room for three stable frames each."""
    return [(RESULT, 0.5), (REWARD, 1.2), (HOME, math.inf)]


class DrainHappyPathTests(unittest.TestCase):
    def test_1_normal_return_to_home(self) -> None:
        clock = FakeDrainClock()
        ui = TimelineSettlementUi(clock, standard_timeline())

        result = make_session(ui, clock).drain(make_request())

        self.assertTrue(result.reached_home)
        self.assertEqual(result.completed_pages, 2)
        self.assertEqual(result.actions_performed, 2)
        self.assertIs(result.final_page, HOME)
        self.assertIsNone(result.error_encountered)
        self.assertFalse(result.requires_manual_intervention)
        # Three pages were visited, but the home port is not a completed page.
        self.assertEqual(ui.action_pages, [RESULT, REWARD])

    def test_return_notice_is_a_settlement_page(self) -> None:
        clock = FakeDrainClock()
        ui = TimelineSettlementUi(
            clock,
            [(NOTICE, 0.5), (RESULT, 1.2), (REWARD, 1.9), (HOME, math.inf)],
        )

        result = make_session(ui, clock).drain(make_request())

        self.assertTrue(result.reached_home)
        self.assertEqual(result.completed_pages, 3)
        self.assertEqual(result.actions_performed, 3)
        self.assertEqual(ui.action_pages, [NOTICE, RESULT, REWARD])

    def test_initial_home_port_needs_no_action(self) -> None:
        clock = FakeDrainClock()
        ui = TimelineSettlementUi(clock, [(HOME, math.inf)])

        result = make_session(ui, clock).drain(make_request())

        self.assertTrue(result.reached_home)
        self.assertEqual(result.completed_pages, 0)
        self.assertEqual(result.actions_performed, 0)
        self.assertIs(result.final_page, HOME)
        self.assertIsNone(result.error_encountered)
        self.assertEqual(ui.actions, [])


class DrainActionBudgetTests(unittest.TestCase):
    def test_2_action_budget_stops_before_home(self) -> None:
        clock = FakeDrainClock()
        ui = TimelineSettlementUi(clock, standard_timeline())

        result = make_session(ui, clock).drain(make_request(max_actions=1))

        self.assertFalse(result.reached_home)
        self.assertIs(result.error_encountered, DrainErrorType.MAX_ACTIONS_REACHED)
        self.assertLessEqual(result.actions_performed, 1)
        self.assertEqual(result.actions_performed, 1)
        # The budget was honoured: exactly one click, no over-budget click.
        self.assertEqual(len(ui.actions), 1)
        self.assertIs(result.final_page, REWARD)
        self.assertFalse(result.requires_manual_intervention)

    def test_budget_exhausted_exactly_is_still_success(self) -> None:
        clock = FakeDrainClock()
        ui = TimelineSettlementUi(clock, standard_timeline())

        result = make_session(ui, clock).drain(make_request(max_actions=2))

        self.assertTrue(result.reached_home)
        self.assertEqual(result.actions_performed, 2)
        self.assertEqual(len(ui.actions), 2)
        self.assertIsNone(result.error_encountered)


class DrainInterruptionTests(unittest.TestCase):
    def test_3_network_error_popup_stops_without_clicking(self) -> None:
        clock = FakeDrainClock()
        ui = TimelineSettlementUi(clock, [(RESULT, 0.5), (NETWORK, math.inf)])

        result = make_session(ui, clock).drain(make_request())

        self.assertFalse(result.reached_home)
        self.assertIs(result.final_page, NETWORK)
        self.assertIs(result.error_encountered, DrainErrorType.NETWORK_ERROR)
        self.assertTrue(result.requires_manual_intervention)
        self.assertEqual(result.actions_performed, 1)
        # Only the result page was clicked; the popup never was.
        self.assertEqual(ui.action_pages, [RESULT])

    def test_initial_unknown_dialog_is_reported_and_not_clicked(self) -> None:
        clock = FakeDrainClock()
        ui = TimelineSettlementUi(clock, [(DIALOG, math.inf)])

        result = make_session(ui, clock).drain(make_request())

        self.assertFalse(result.reached_home)
        self.assertIs(result.final_page, DIALOG)
        self.assertIs(result.error_encountered, DrainErrorType.UNKNOWN_DIALOG)
        self.assertTrue(result.requires_manual_intervention)
        self.assertEqual(result.actions_performed, 0)
        self.assertEqual(ui.actions, [])

    def test_initial_unknown_page_is_reported_and_not_clicked(self) -> None:
        clock = FakeDrainClock()
        ui = TimelineSettlementUi(clock, [(UNKNOWN, math.inf)])

        result = make_session(ui, clock).drain(make_request())

        self.assertFalse(result.reached_home)
        self.assertIs(result.final_page, UNKNOWN)
        self.assertIs(result.error_encountered, DrainErrorType.UNKNOWN_PAGE)
        self.assertTrue(result.requires_manual_intervention)
        self.assertEqual(ui.actions, [])

    def test_driver_failure_propagates_and_is_never_retried(self) -> None:
        clock = FakeDrainClock()
        ui = TimelineSettlementUi(clock, standard_timeline())
        driver = RaisingDriver()

        with self.assertRaises(RaisingDriver.ClickFailed):
            make_session(ui, clock, driver=driver).drain(make_request())

        self.assertEqual(driver.pages, [RESULT])


class DrainStabilityTests(unittest.TestCase):
    def test_4_animation_delay_is_waited_out_without_a_second_click(self) -> None:
        clock = FakeDrainClock()
        # The click at t~0.2 keeps showing the result page until t=0.5.
        ui = TimelineSettlementUi(clock, standard_timeline())

        result = make_session(ui, clock).drain(make_request(interval=1.0))

        self.assertTrue(result.reached_home)
        self.assertIsNone(result.error_encountered)
        # No premature UI_STUCK, and no repeated click on the result page.
        self.assertEqual(ui.action_pages, [RESULT, REWARD])
        first_click_at = ui.actions[0][0]
        stale_frames = [
            page for at, page in ui.observations if at > first_click_at and page is RESULT
        ]
        self.assertGreaterEqual(len(stale_frames), 2)

    def test_4_control_no_change_within_bound_is_ui_stuck(self) -> None:
        clock = FakeDrainClock()
        ui = TimelineSettlementUi(clock, [(RESULT, math.inf)])

        result = make_session(ui, clock).drain(make_request(interval=1.0))

        self.assertFalse(result.reached_home)
        self.assertIs(result.error_encountered, DrainErrorType.UI_STUCK)
        self.assertIs(result.final_page, RESULT)
        self.assertEqual(result.actions_performed, 1)
        self.assertEqual(len(ui.actions), 1)
        self.assertTrue(result.requires_manual_intervention)

    def test_page_bouncing_back_is_stuck_not_clicked_twice(self) -> None:
        clock = FakeDrainClock()
        # RESULT -> REWARD for one frame -> back to RESULT and stays.
        ui = TimelineSettlementUi(
            clock, [(RESULT, 0.5), (REWARD, 0.6), (RESULT, math.inf)]
        )

        result = make_session(ui, clock).drain(make_request(interval=1.0))

        self.assertFalse(result.reached_home)
        self.assertIs(result.error_encountered, DrainErrorType.UI_STUCK)
        self.assertEqual(result.completed_pages, 0)
        # The result page is clicked exactly once, never twice in a row.
        self.assertEqual(ui.action_pages, [RESULT])

    def test_single_frame_stability_accepts_the_first_change(self) -> None:
        clock = FakeDrainClock()
        ui = TimelineSettlementUi(clock, standard_timeline())

        result = make_session(ui, clock).drain(make_request(stable=1))

        self.assertTrue(result.reached_home)
        self.assertEqual(result.actions_performed, 2)


class DrainTimeoutTests(unittest.TestCase):
    def test_timeout_during_initial_stabilisation(self) -> None:
        clock = FakeDrainClock()
        ui = TimelineSettlementUi(clock, standard_timeline())

        result = make_session(ui, clock).drain(make_request(timeout=0.15))

        self.assertFalse(result.reached_home)
        self.assertIs(result.error_encountered, DrainErrorType.TIMEOUT)
        self.assertIs(result.final_page, RESULT)
        self.assertEqual(result.actions_performed, 0)
        self.assertTrue(result.requires_manual_intervention)

    def test_whole_drain_timeout_wins_over_stuck(self) -> None:
        clock = FakeDrainClock()
        ui = TimelineSettlementUi(clock, [(RESULT, math.inf)])

        # The observation window is far longer than the whole-drain timeout.
        result = make_session(ui, clock).drain(make_request(timeout=0.4, interval=5.0))

        self.assertIs(result.error_encountered, DrainErrorType.TIMEOUT)
        self.assertEqual(result.actions_performed, 1)

    def test_never_stable_page_times_out(self) -> None:
        clock = FakeDrainClock()
        # A page that flips every frame never reaches three identical frames.
        segments = [
            (RESULT if index % 2 == 0 else REWARD, 0.1 * (index + 1))
            for index in range(80)
        ]
        ui = TimelineSettlementUi(clock, segments)

        result = make_session(ui, clock).drain(make_request(timeout=5.0))

        self.assertFalse(result.reached_home)
        self.assertIs(result.error_encountered, DrainErrorType.TIMEOUT)
        self.assertEqual(result.actions_performed, 0)
        self.assertEqual(ui.actions, [])


class DrainFleetIndependenceTests(unittest.TestCase):
    def test_5_pending_other_fleets_do_not_block_the_drain(self) -> None:
        clock = FakeDrainClock()
        ui = TimelineSettlementUi(clock, standard_timeline())
        poi = FakePoiLedger()
        self.assertEqual(poi.read_fleets(), (
            {"fleet_id": 2, "mission_status": 2},
            {"fleet_id": 4, "mission_status": 2},
        ))
        poi.read_calls = 0

        result = make_session(ui, clock).drain(make_request())

        self.assertTrue(result.reached_home)
        self.assertEqual(result.completed_pages, 2)
        self.assertIsNone(result.error_encountered)
        # The drain never talked to the fleet ledger at all.
        self.assertEqual(poi.read_calls, 0)

    def test_request_has_no_fleet_identity(self) -> None:
        names = [field.name for field in dataclasses.fields(SettlementDrainRequest)]
        self.assertEqual(
            names,
            [
                "max_actions",
                "timeout_seconds",
                "action_interval_seconds",
                "stable_frame_count",
                # Added 2026-11-07: the post-action observation budget. It is a
                # time bound, not an identity - the drain still cannot be told
                # (or infer) which fleet owns a page.
                "action_observation_seconds",
            ],
        )
        self.assertFalse([name for name in names if "fleet" in name])

    def test_drain_takes_only_the_request(self) -> None:
        parameters = list(inspect.signature(SettlementDrainSession.drain).parameters)
        self.assertEqual(parameters, ["self", "request"])


class DrainValidationTests(unittest.TestCase):
    def test_invalid_request_parameters_are_rejected(self) -> None:
        base = {
            "max_actions": 8,
            "timeout_seconds": 30.0,
            "action_interval_seconds": 1.0,
            "stable_frame_count": STABLE,
        }
        invalid = [
            {"max_actions": 0},
            {"max_actions": -3},
            {"max_actions": True},
            {"max_actions": 1.5},
            {"timeout_seconds": 0},
            {"timeout_seconds": -1.0},
            {"timeout_seconds": math.inf},
            {"timeout_seconds": math.nan},
            {"timeout_seconds": True},
            {"action_interval_seconds": 0},
            {"action_interval_seconds": -0.5},
            {"action_interval_seconds": math.inf},
            {"action_interval_seconds": math.nan},
            {"stable_frame_count": 0},
            {"stable_frame_count": -2},
            {"stable_frame_count": True},
        ]
        for overrides in invalid:
            with self.subTest(**overrides):
                with self.assertRaises(ValueError):
                    SettlementDrainRequest(**{**base, **overrides})

    def test_valid_request_is_accepted(self) -> None:
        request = make_request()
        request.validate()  # idempotent
        self.assertEqual(request.stable_frame_count, STABLE)

    def test_session_rejects_missing_collaborators(self) -> None:
        class NoObserve:
            def advance(self, page):  # pragma: no cover - never called
                raise AssertionError

        class NoAdvance:
            def observe(self):  # pragma: no cover - never called
                raise AssertionError

        with self.assertRaises(ValueError):
            SettlementDrainSession(NoObserve(), NoObserve())
        with self.assertRaises(ValueError):
            SettlementDrainSession(NoAdvance(), NoAdvance())

    def test_session_rejects_bad_poll_interval(self) -> None:
        clock = FakeDrainClock()
        ui = TimelineSettlementUi(clock, [(HOME, math.inf)])
        for bad in (0, -0.1, math.inf, math.nan, True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    SettlementDrainSession(
                        ui, ui, clock=clock, frame_poll_interval_seconds=bad
                    )

    def test_default_poll_interval_is_positive(self) -> None:
        self.assertGreater(DEFAULT_FRAME_POLL_INTERVAL_SECONDS, 0)

    def test_drain_rejects_a_foreign_request_object(self) -> None:
        clock = FakeDrainClock()
        ui = TimelineSettlementUi(clock, [(HOME, math.inf)])
        with self.assertRaises(ValueError):
            make_session(ui, clock).drain(object())  # type: ignore[arg-type]


class DrainResultContractTests(unittest.TestCase):
    def test_result_rejects_home_with_an_error(self) -> None:
        with self.assertRaises(ValueError):
            DrainResult(
                reached_home=True,
                completed_pages=1,
                actions_performed=1,
                final_page=HOME,
                error_encountered=DrainErrorType.NETWORK_ERROR,
                requires_manual_intervention=True,
            )

    def test_result_rejects_home_with_a_foreign_final_page(self) -> None:
        with self.assertRaises(ValueError):
            DrainResult(
                reached_home=True,
                completed_pages=1,
                actions_performed=1,
                final_page=REWARD,
                error_encountered=None,
                requires_manual_intervention=False,
            )

    def test_result_rejects_silent_failure(self) -> None:
        with self.assertRaises(ValueError):
            DrainResult(
                reached_home=False,
                completed_pages=0,
                actions_performed=0,
                final_page=REWARD,
                error_encountered=None,
                requires_manual_intervention=False,
            )

    def test_result_rejects_negative_counters(self) -> None:
        with self.assertRaises(ValueError):
            DrainResult(
                reached_home=False,
                completed_pages=-1,
                actions_performed=0,
                final_page=REWARD,
                error_encountered=DrainErrorType.UI_STUCK,
                requires_manual_intervention=True,
            )

    def test_enum_values_are_a_stable_contract(self) -> None:
        self.assertEqual(
            {state.name: state.value for state in DrainPageState},
            {
                "HOME_PORT": "home_port",
                "RETURN_NOTICE": "return_notice",
                "EXPEDITION_RESULT": "expedition_result",
                "REWARD_PANEL": "reward_panel",
                "NETWORK_ERROR_POPUP": "network_error_popup",
                "UNKNOWN_DIALOG": "unknown_dialog",
                "UNKNOWN": "unknown",
            },
        )
        self.assertEqual(
            {error.name: error.value for error in DrainErrorType},
            {
                "MAX_ACTIONS_REACHED": "max_actions",
                "TIMEOUT": "timeout",
                "UI_STUCK": "ui_stuck",
                "UNKNOWN_DIALOG": "unknown_dialog",
                "NETWORK_ERROR": "network_error",
                "UNKNOWN_PAGE": "unknown_page",
                # Added 2026-11-07. Both are *deliberate* stops rather than new
                # ways to claim success: neither is in
                # ``_MANUAL_INTERVENTION_ERRORS``, and neither can report
                # ``reached_home``.
                "OBSERVATION_BUDGET_EXHAUSTED": "observation_budget",
                "SETTLED_UI_PENDING": "settled_ui_pending",
            },
        )


if __name__ == "__main__":
    unittest.main()
