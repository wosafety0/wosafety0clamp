"""The return refresh observed in bounded time instead of a fixed frame count.

Live 2026-11-07: the refresh clicked 出撃, saw six UNKNOWN frames in ~8 s and
failed closed, while the client needed ~18 s to paint the next page. Twenty
seconds later a read-only capture showed the page was there all along.

These tests pin the replacement contract:

* a frozen but unrecognised screen is *waited out*, not declared dead;
* a page must be named twice in a row before it counts;
* the start page being re-proven is the ONLY reason a click may be repeated,
  and only once per refresh;
* a stable third page stops the refresh instead of waiting for a target that
  will never arrive;
* a verifier that runs out of frames (the tool layer's real ``LoopError``) is
  normalised into the refresh's own error with its diagnostics intact, while an
  unrelated failure keeps its own type.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from kancollebot.adapters.poi.cdp_input import RecordedPoint
from kancollebot.adapters.poi.home_port_button_vision import StaticHomePortLocator
from kancollebot.adapters.poi.ui_operation_lock import UiOperationArbiter
from kancollebot.adapters.poi.visible_ui_refresh import (
    VisibleUiPortRefreshDriver,
    VisibleUiRefreshError,
)
from kancollebot.observability.page_observation import (
    PageObservationBudget,
    PageSample,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import one_expedition_ui_loop as controller  # noqa: E402

STUB_LOCATOR = StaticHomePortLocator(RecordedPoint(50, 50, "home-port"))

STILL = (100,) * 8
MOVED = (180,) * 8
MOVED2 = (30,) * 8


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            raise AssertionError("the refresh must never busy-loop")
        self.sleeps.append(seconds)
        self.now += seconds


class FakeInput:
    def __init__(self) -> None:
        self.clicks: list[RecordedPoint] = []

    def click(self, point: RecordedPoint) -> None:
        self.clicks.append(point)


class ScriptedFrames:
    """One scripted frame per observation; the last frame repeats for ever."""

    def __init__(self, frames) -> None:
        self._frames = list(frames)
        self.calls = 0

    def __call__(self) -> PageSample:
        index = min(self.calls, len(self._frames) - 1)
        self.calls += 1
        state, fingerprint = self._frames[index]
        return PageSample(state=state, scores={state: 0.99}, fingerprint=fingerprint)


def frames(*items) -> list[tuple[str, tuple[int, ...]]]:
    return list(items)


class BoundedRefreshTests(unittest.TestCase):
    def make_driver(self, scripted, *, budget=None):
        clock = FakeClock()
        game_input = FakeInput()
        events: list[tuple[str, dict]] = []
        driver = VisibleUiPortRefreshDriver(
            home_port_locator=STUB_LOCATOR,
            game_input=game_input,
            verify_state=lambda expected: self.fail("the legacy verifier must not run"),
            ui_arbiter=UiOperationArbiter(),
            event_sink=lambda event, fields: events.append((event, dict(fields))),
            sleep=clock.sleep,
            settle_seconds=0.0,
            observe_page=scripted,
            observation_budget=budget
            or PageObservationBudget(total_seconds=120.0, changed_unknown_limit=20),
            monotonic=clock.monotonic,
        )
        return driver, game_input, events, clock

    def test_a_still_unrecognised_screen_is_waited_out_until_the_page_arrives(self):
        # The exact live shape: six frozen UNKNOWN frames, then the page.
        scripted = ScriptedFrames(
            frames(
                ("HOME_PORT", STILL),
                ("HOME_PORT", STILL),
                # pre-click guard re-proves HOME_PORT before the first click
                ("HOME_PORT", STILL),
                ("HOME_PORT", STILL),
                ("UNKNOWN", STILL),
                ("UNKNOWN", STILL),
                ("UNKNOWN", STILL),
                ("UNKNOWN", STILL),
                ("UNKNOWN", STILL),
                ("UNKNOWN", STILL),
                ("SORTIE_MENU", MOVED),
                ("SORTIE_MENU", MOVED),
                # pre-click guard re-proves SORTIE_MENU before the second click
                ("SORTIE_MENU", MOVED),
                ("SORTIE_MENU", MOVED),
                ("EXPEDITION_LIST", MOVED2),
                ("EXPEDITION_LIST", MOVED2),
                # pre-click guard re-proves EXPEDITION_LIST before the third click
                ("EXPEDITION_LIST", MOVED2),
                ("EXPEDITION_LIST", MOVED2),
                ("HOME_PORT", MOVED),
                ("HOME_PORT", MOVED),
            )
        )
        driver, game_input, events, clock = self.make_driver(scripted)

        result = driver.refresh_port_state()

        self.assertEqual(result.state, "HOME_PORT")
        self.assertEqual(len(game_input.clicks), 3)
        self.assertEqual(events[-1][0], "ui_refresh_completed")
        self.assertEqual(events[-1][1]["clicks_sent"], 3)
        # It really did wait: six frozen frames cost six sleeps, not a failure.
        self.assertGreaterEqual(len(clock.sleeps), 6)
        self.assertNotIn("ui_refresh_blocked", [name for name, _ in events])

    def test_the_start_page_is_repeated_once_and_then_never_clicked_again(self):
        scripted = ScriptedFrames(
            frames(
                ("HOME_PORT", STILL),
                ("HOME_PORT", STILL),
                # after click 1: still home port, stably
                ("HOME_PORT", STILL),
                ("HOME_PORT", STILL),
                # after the repeat: still home port, stably, and now unfixable
                ("HOME_PORT", STILL),
                ("HOME_PORT", STILL),
                ("HOME_PORT", STILL),
                ("HOME_PORT", STILL),
            )
        )
        driver, game_input, events, _ = self.make_driver(scripted)

        with self.assertRaises(VisibleUiRefreshError):
            driver.refresh_port_state()

        self.assertEqual(len(game_input.clicks), 2)
        repeated = [f for name, f in events if name == "ui_refresh_click_repeated"]
        self.assertEqual(len(repeated), 1)
        self.assertEqual(repeated[0]["repeat_index"], 1)
        self.assertEqual(events[-1][0], "ui_refresh_blocked")

    def test_a_stable_third_page_stops_without_another_click(self):
        scripted = ScriptedFrames(
            frames(
                ("HOME_PORT", STILL),
                ("HOME_PORT", STILL),
                # pre-click guard re-proves HOME_PORT, then the click lands on
                # a third page
                ("HOME_PORT", STILL),
                ("HOME_PORT", STILL),
                ("EXPEDITION_LIST", MOVED),
                ("EXPEDITION_LIST", MOVED),
            )
        )
        driver, game_input, events, _ = self.make_driver(scripted)

        with self.assertRaises(VisibleUiRefreshError) as raised:
            driver.refresh_port_state()

        self.assertEqual(len(game_input.clicks), 1)
        self.assertIn("EXPEDITION_LIST", str(raised.exception))
        self.assertNotIn("ui_refresh_click_repeated", [name for name, _ in events])

    def test_a_changing_unrecognised_screen_stops_at_the_budget(self):
        scripted = ScriptedFrames(
            frames(
                ("HOME_PORT", STILL),
                ("HOME_PORT", STILL),
                # pre-click guard re-proves HOME_PORT before the first click
                ("HOME_PORT", STILL),
                ("HOME_PORT", STILL),
                ("UNKNOWN", MOVED),
                ("UNKNOWN", MOVED2),
                ("UNKNOWN", (200,) * 8),
                ("UNKNOWN", (60,) * 8),
            )
        )
        driver, game_input, events, _ = self.make_driver(
            scripted,
            budget=PageObservationBudget(
                total_seconds=120.0, changed_unknown_limit=3
            ),
        )

        with self.assertRaises(VisibleUiRefreshError) as raised:
            driver.refresh_port_state()

        self.assertIn("never recognised", str(raised.exception))
        self.assertEqual(len(game_input.clicks), 1)

    def test_the_total_budget_bounds_a_frozen_unrecognised_screen(self):
        scripted = ScriptedFrames(frames(("UNKNOWN", STILL)))
        driver, game_input, events, _ = self.make_driver(
            scripted, budget=PageObservationBudget(total_seconds=5.0)
        )

        with self.assertRaises(VisibleUiRefreshError) as raised:
            driver.refresh_port_state()

        self.assertIn("budget exhausted", str(raised.exception))
        self.assertEqual(game_input.clicks, [])

    def test_the_observation_mode_is_recorded_in_the_refresh_stats(self):
        scripted = ScriptedFrames(
            frames(
                ("HOME_PORT", STILL),
                ("HOME_PORT", STILL),
                # pre-click guard re-proves HOME_PORT before the first click
                ("HOME_PORT", STILL),
                ("HOME_PORT", STILL),
                ("SORTIE_MENU", MOVED),
                ("SORTIE_MENU", MOVED),
                # pre-click guard re-proves SORTIE_MENU before the second click
                ("SORTIE_MENU", MOVED),
                ("SORTIE_MENU", MOVED),
                ("EXPEDITION_LIST", MOVED2),
                ("EXPEDITION_LIST", MOVED2),
                # pre-click guard re-proves EXPEDITION_LIST before the third click
                ("EXPEDITION_LIST", MOVED2),
                ("EXPEDITION_LIST", MOVED2),
                ("HOME_PORT", MOVED),
                ("HOME_PORT", MOVED),
            )
        )
        driver, _, _, _ = self.make_driver(scripted)

        driver.refresh_port_state()

        self.assertEqual(driver.last_refresh_stats["observation_mode"], "bounded")
        self.assertEqual(driver.last_refresh_stats["outcome"], "completed")


class PreClickSettlementGuardTests(unittest.TestCase):
    """The refresh must never click through a settlement screen.

    Live 2026-11-07: the return notice surfaces on the Home Port exactly at
    timer expiry, so the page can become a settlement screen in the gap between
    the refresh's initial Home Port proof and its first click. Every refresh
    click is therefore preceded by a fresh observation of the page it expects,
    and a settlement screen - which advances on a click anywhere - stops the
    refresh with zero further clicks instead of being clicked through.
    """

    def make_driver(self, scripted):
        clock = FakeClock()
        game_input = FakeInput()
        events: list[tuple[str, dict]] = []
        driver = VisibleUiPortRefreshDriver(
            home_port_locator=STUB_LOCATOR,
            game_input=game_input,
            verify_state=lambda expected: self.fail("the legacy verifier must not run"),
            ui_arbiter=UiOperationArbiter(),
            event_sink=lambda event, fields: events.append((event, dict(fields))),
            sleep=clock.sleep,
            settle_seconds=0.0,
            observe_page=scripted,
            observation_budget=PageObservationBudget(
                total_seconds=120.0, changed_unknown_limit=20
            ),
            monotonic=clock.monotonic,
        )
        return driver, game_input, events

    def test_a_return_notice_before_the_first_click_stops_with_zero_clicks(self):
        scripted = ScriptedFrames(
            frames(
                ("HOME_PORT", STILL),
                ("HOME_PORT", STILL),
                ("HOME_PORT_RETURN_NOTICE", MOVED),
                ("HOME_PORT_RETURN_NOTICE", MOVED),
            )
        )
        driver, game_input, events = self.make_driver(scripted)

        with self.assertRaises(VisibleUiRefreshError) as raised:
            driver.refresh_port_state()

        self.assertEqual(game_input.clicks, [])
        self.assertIn("HOME_PORT_RETURN_NOTICE", str(raised.exception))
        settlement = [
            f for name, f in events if name == "ui_refresh_settlement_page_detected"
        ]
        self.assertEqual(len(settlement), 1)
        self.assertEqual(settlement[0]["state"], "HOME_PORT_RETURN_NOTICE")
        self.assertEqual(settlement[0]["clicks_sent"], 0)
        self.assertEqual(events[-1][0], "ui_refresh_blocked")

    def test_a_settlement_screen_at_a_later_step_stops_without_another_click(self):
        # RETURN_RESULT and BONUS_ITEMS advance on a click anywhere, so they must
        # stop the refresh at any step, not only before the first click.
        scripted = ScriptedFrames(
            frames(
                ("HOME_PORT", STILL),
                ("HOME_PORT", STILL),
                ("HOME_PORT", STILL),
                ("HOME_PORT", STILL),
                ("SORTIE_MENU", MOVED),
                ("SORTIE_MENU", MOVED),
                ("RETURN_RESULT", MOVED2),
                ("RETURN_RESULT", MOVED2),
            )
        )
        driver, game_input, events = self.make_driver(scripted)

        with self.assertRaises(VisibleUiRefreshError) as raised:
            driver.refresh_port_state()

        self.assertEqual(len(game_input.clicks), 1)
        self.assertIn("RETURN_RESULT", str(raised.exception))
        settlement = [
            f for name, f in events if name == "ui_refresh_settlement_page_detected"
        ]
        self.assertEqual(len(settlement), 1)
        self.assertEqual(settlement[0]["state"], "RETURN_RESULT")
        self.assertEqual(settlement[0]["clicks_sent"], 0)


class VerifierErrorNormalisationTests(unittest.TestCase):
    """A real ``LoopError`` must not escape as an "unexpected" exception."""

    def make_driver(self, verifier):
        events: list[tuple[str, dict]] = []
        return (
            VisibleUiPortRefreshDriver(
                home_port_locator=STUB_LOCATOR,
                game_input=FakeInput(),
                verify_state=verifier,
                ui_arbiter=UiOperationArbiter(),
                event_sink=lambda event, fields: events.append((event, dict(fields))),
                sleep=lambda _seconds: None,
                settle_seconds=0.0,
                verifier_error_types=(controller.LoopError,),
            ),
            events,
        )

    def test_the_tool_layer_loop_error_is_normalised_and_keeps_its_diagnostics(self):
        driver, events = self.make_driver(
            lambda _expected: (_ for _ in ()).throw(
                controller.LoopError("Expected HOME_PORT; no stable fingerprint match")
            )
        )

        with self.assertRaises(VisibleUiRefreshError) as raised:
            driver.refresh_port_state()

        self.assertIsInstance(raised.exception.__cause__, controller.LoopError)
        self.assertIn("could not confirm HOME_PORT", str(raised.exception))
        self.assertEqual(events[-1][0], "ui_refresh_blocked")
        self.assertEqual(events[-1][1]["error_type"], "VisibleUiRefreshError")

    def test_an_unrelated_failure_is_not_relabelled(self):
        driver, events = self.make_driver(
            lambda _expected: (_ for _ in ()).throw(OSError("template directory gone"))
        )

        with self.assertRaises(VisibleUiRefreshError) as raised:
            driver.refresh_port_state()

        self.assertIsInstance(raised.exception.__cause__, OSError)
        self.assertEqual(events[-1][1]["error_type"], "OSError")

    def test_a_loop_error_without_the_declared_type_is_still_recognised(self):
        # The class-name fallback: a caller that forgets verifier_error_types
        # must not lose the refresh's own diagnostics.
        class LoopError(RuntimeError):
            pass

        events: list[tuple[str, dict]] = []
        driver = VisibleUiPortRefreshDriver(
            home_port_locator=STUB_LOCATOR,
            game_input=FakeInput(),
            verify_state=lambda _expected: (_ for _ in ()).throw(LoopError("exhausted")),
            ui_arbiter=UiOperationArbiter(),
            event_sink=lambda event, fields: events.append((event, dict(fields))),
            sleep=lambda _seconds: None,
            settle_seconds=0.0,
        )

        with self.assertRaises(VisibleUiRefreshError):
            driver.refresh_port_state()

        self.assertEqual(events[-1][1]["error_type"], "VisibleUiRefreshError")


if __name__ == "__main__":
    unittest.main()
