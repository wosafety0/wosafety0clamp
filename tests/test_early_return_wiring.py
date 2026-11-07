"""Offline tests for the production wiring of the early-return mechanic.

The coordinator itself is covered by ``test_expedition_early_return``. What is
tested here is the glue the production loop adds around it, because every one of
these branches decides whether a real client is navigated away from the home
port while the loop is only waiting:

* the feature is off unless it was requested, and unavailable unless the
  countdown glyphs were calibrated on a real screen;
* a local deadline only decides when to *look*, never whether the mechanic
  applies;
* the sleep after an attempt is capped so the loop comes back exactly when the
  displayed time is due to drop below one minute;
* the two new driver primitives click nothing when the screen is already where
  it needs to be, and refuse a reward screen outright.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import one_expedition_ui_loop as controller  # noqa: E402
from kancollebot.adapters.poi.continuous_expedition_ui import (  # noqa: E402
    CdpContinuousExpeditionUiDriver,
    ContinuousUiDriverError,
)
from kancollebot.expeditions.expedition_early_return import (  # noqa: E402
    EarlyReturnObservation,
    EarlyReturnOutcome,
)


class FakeEarlyReturn:
    """A coordinator stand-in that records calls and replays one outcome."""

    def __init__(self, outcome=None, error=None):
        self.outcome = outcome
        self.error = error
        self.calls = 0

    def attempt(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.outcome


def _outcome(**fields) -> EarlyReturnOutcome:
    base = dict(triggered=False, succeeded=False, reason="not_triggered")
    base.update(fields)
    return EarlyReturnOutcome(**base)


class EarlyReturnSettingsTests(unittest.TestCase):
    def test_the_feature_is_off_by_default(self):
        enabled, lookahead, attempts = controller._early_return_settings(
            MagicMock()
        )
        self.assertFalse(enabled)
        self.assertEqual(lookahead, controller.DEFAULT_EARLY_RETURN_LOOKAHEAD_SECONDS)
        self.assertGreaterEqual(attempts, 1)

    def test_only_a_real_true_opts_in(self):
        # A MagicMock attribute is truthy but is not the flag being passed.
        self.assertFalse(controller._early_return_settings(MagicMock())[0])
        self.assertTrue(
            controller._early_return_settings(Namespace(early_return=True))[0]
        )

    def test_a_malformed_lookahead_falls_back_instead_of_crashing(self):
        args = Namespace(
            early_return=True,
            early_return_lookahead_seconds="soon",
            early_return_max_attempts=-4,
        )
        enabled, lookahead, _attempts = controller._early_return_settings(args)
        self.assertTrue(enabled)
        self.assertEqual(lookahead, controller.DEFAULT_EARLY_RETURN_LOOKAHEAD_SECONDS)


class BuildEarlyReturnTests(unittest.TestCase):
    def _document(self) -> dict:
        return {
            "version": 1,
            "roi": [10, 20, 40, 12],
            "cell_width": 8,
            "cell_height": 12,
            "cells": 5,
            "grid": [4, 4],
            "glyphs": {"0": [1] * 16, "1": [2] * 16},
        }

    def test_not_requested_builds_nothing_and_states_why(self):
        coordinator, settings = controller._build_early_return(
            MagicMock(),
            ui_driver=MagicMock(),
            event_sink=MagicMock(),
            fleet_id=2,
            capture_countdown_frame=MagicMock(),
        )
        self.assertIsNone(coordinator)
        self.assertEqual(settings["reason"], "not_requested")
        self.assertEqual(settings["ui_switches_at_risk"], 0)

    def test_uncalibrated_countdown_makes_the_feature_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            args = Namespace(
                early_return=True,
                countdown_template=Path(directory) / "missing.json",
            )
            coordinator, settings = controller._build_early_return(
                args,
                ui_driver=MagicMock(),
                event_sink=MagicMock(),
                fleet_id=2,
                capture_countdown_frame=MagicMock(),
            )
        self.assertIsNone(coordinator)
        self.assertEqual(settings["reason"], "countdown_templates_unavailable")
        # Zero screens are touched when the reading could never be produced.
        self.assertEqual(settings["ui_switches_at_risk"], 0)

    def test_a_calibrated_template_builds_the_coordinator(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "countdown.json"
            path.write_text(json.dumps(self._document()), encoding="utf-8")
            args = Namespace(early_return=True, countdown_template=path)
            coordinator, settings = controller._build_early_return(
                args,
                ui_driver=MagicMock(),
                event_sink=MagicMock(),
                fleet_id=2,
                capture_countdown_frame=MagicMock(return_value=""),
            )
        self.assertIsNotNone(coordinator)
        self.assertEqual(settings["reason"], "enabled")
        self.assertEqual(settings["threshold_seconds"], 60)


class NearestDeadlineTests(unittest.TestCase):
    def test_the_closest_deadline_wins(self):
        now = 1_000_000
        nearest = controller._nearest_deadline_fleet(
            {2: now + 90_000, 3: now + 5_000}, (2, 3), now_epoch_ms=now
        )
        self.assertEqual(nearest, (3, 5.0))

    def test_no_usable_timer_is_no_answer(self):
        self.assertIsNone(
            controller._nearest_deadline_fleet({}, (2,), now_epoch_ms=1)
        )
        self.assertIsNone(
            controller._nearest_deadline_fleet({2: True}, (2,), now_epoch_ms=1)
        )

    def test_a_fleet_outside_the_plan_is_ignored(self):
        self.assertIsNone(
            controller._nearest_deadline_fleet(
                {4: 2_000}, (2, 3), now_epoch_ms=1_000
            )
        )


class AttemptTests(unittest.TestCase):
    def _attempt(self, *, coordinator, timers, used=None, settings=None):
        events: list[tuple[str, dict]] = []
        outcome, cap = controller._maybe_attempt_early_return(
            coordinator=coordinator,
            settings=settings
            if settings is not None
            else {"lookahead_seconds": 90.0, "max_attempts": 2},
            timers=timers,
            fleet_ids=(2,),
            ui_arbiter=MagicMock(),
            event_sink=lambda event, fields: events.append((event, fields)),
            tick_index=1,
            tick_budget=3,
            attempts_used={} if used is None else used,
        )
        return outcome, cap, events

    def test_nothing_happens_while_the_deadline_is_far_away(self):
        coordinator = FakeEarlyReturn(_outcome())
        outcome, cap, events = self._attempt(
            coordinator=coordinator,
            timers={2: int(__import__("time").time() * 1000) + 600_000},
        )
        self.assertIsNone(outcome)
        self.assertIsNone(cap)
        self.assertEqual(coordinator.calls, 0)
        self.assertEqual(events, [])

    def test_a_successful_attempt_reobserves_after_the_fixed_grace(self):
        coordinator = FakeEarlyReturn(_outcome(triggered=True, succeeded=True))
        outcome, cap, _events = self._attempt(
            coordinator=coordinator,
            timers={2: int(__import__("time").time() * 1000) + 30_000},
        )
        self.assertTrue(outcome.succeeded)
        # Pending settlement is executable work now: do not sleep to a deadline
        # the server has already passed, but keep the same fixed grace.
        self.assertEqual(cap, controller.DEFAULT_POST_EXPIRY_GRACE_SECONDS)
        self.assertEqual(coordinator.calls, 1)

    def test_still_above_a_minute_caps_the_sleep_to_the_crossing(self):
        coordinator = FakeEarlyReturn(_outcome(
            reason="displayed_remaining_time_at_or_above_threshold",
            remaining_seconds=75,
            displayed_remaining="1:15",
        ))
        outcome, cap, _events = self._attempt(
            coordinator=coordinator,
            timers={2: int(__import__("time").time() * 1000) + 20_000},
        )
        self.assertFalse(outcome.succeeded)
        # 75 - 60 + 2: wake just after the display is due to drop below a minute.
        self.assertEqual(cap, 17.0)

    def test_an_unreadable_display_does_not_sleep_to_the_deadline(self):
        coordinator = FakeEarlyReturn(_outcome(
            reason="displayed_remaining_time_unreadable"
        ))
        _outcome_value, cap, _events = self._attempt(
            coordinator=coordinator,
            timers={2: int(__import__("time").time() * 1000) + 20_000},
        )
        self.assertEqual(cap, 15.0)

    def test_the_attempt_budget_is_spent_once(self):
        coordinator = FakeEarlyReturn(_outcome(triggered=True, succeeded=True))
        used = {2: 2}
        _outcome_value, cap, events = self._attempt(
            coordinator=coordinator,
            timers={2: int(__import__("time").time() * 1000) + 20_000},
            used=used,
        )
        self.assertEqual(coordinator.calls, 0)
        self.assertIsNone(cap)
        self.assertEqual(events[0][0], "expedition_early_return_skipped")
        self.assertEqual(events[0][1]["reason"], "attempt_budget_spent")

    def test_a_refused_navigation_asks_for_an_immediate_reobserve(self):
        coordinator = FakeEarlyReturn(error=ContinuousUiDriverError("no"))
        outcome, cap, events = self._attempt(
            coordinator=coordinator,
            timers={2: int(__import__("time").time() * 1000) + 20_000},
        )
        self.assertIsNone(outcome)
        self.assertEqual(cap, 0.0)
        names = [name for name, _fields in events]
        self.assertIn("expedition_early_return_failed", names)
        failed = dict(events)["expedition_early_return_failed"]
        self.assertFalse(failed["succeeded"])
        self.assertEqual(failed["error_type"], "ContinuousUiDriverError")

    def test_disabled_coordinator_touches_nothing(self):
        events: list[tuple[str, dict]] = []
        outcome, cap = controller._maybe_attempt_early_return(
            coordinator=None,
            settings={"lookahead_seconds": 90.0},
            timers={2: 1},
            fleet_ids=(2,),
            ui_arbiter=MagicMock(),
            event_sink=lambda event, fields: events.append((event, fields)),
            tick_index=1,
            tick_budget=1,
            attempts_used={},
        )
        self.assertIsNone(outcome)
        self.assertIsNone(cap)
        self.assertEqual(events, [])


class PoiObservationTests(unittest.TestCase):
    def test_a_failed_read_is_not_fresh_instead_of_raising(self):
        with unittest.mock.patch.object(
            controller, "connect_poi_session", side_effect=OSError("no cdp")
        ):
            observation = controller._read_early_return_observation(2)
        self.assertFalse(observation.fresh)
        self.assertIsNone(observation.mission_status)
        self.assertIn("OSError", observation.detail)

    def test_a_missing_fleet_is_not_fresh(self):
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        with unittest.mock.patch.object(
            controller, "connect_poi_session", return_value=session
        ), unittest.mock.patch.object(
            controller, "read_poi_fleet_states", return_value=()
        ):
            observation = controller._read_early_return_observation(2)
        self.assertFalse(observation.fresh)
        self.assertIn("absent", observation.detail)

    def test_the_reported_status_is_the_fleet_status(self):
        state = MagicMock()
        state.fleet_id = 2
        state.mission_status = 1
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        with unittest.mock.patch.object(
            controller, "connect_poi_session", return_value=session
        ), unittest.mock.patch.object(
            controller, "read_poi_fleet_states", return_value=(state,)
        ):
            observation = controller._read_early_return_observation(2)
        self.assertEqual(
            observation, EarlyReturnObservation(mission_status=1, fresh=True)
        )


class NamedPageTests(unittest.TestCase):
    def test_unknown_and_missing_are_both_no_answer(self):
        driver = MagicMock()
        driver.probe_drain_state.return_value = "UNKNOWN"
        self.assertIsNone(controller._named_page(driver))
        driver.probe_drain_state.return_value = None
        self.assertIsNone(controller._named_page(driver))

    def test_a_named_page_is_returned(self):
        driver = MagicMock()
        driver.probe_drain_state.return_value = "EXPEDITION_LIST"
        self.assertEqual(controller._named_page(driver), "EXPEDITION_LIST")

    def test_a_failing_probe_is_no_answer(self):
        driver = MagicMock()
        driver.probe_drain_state.side_effect = RuntimeError("cdp gone")
        self.assertIsNone(controller._named_page(driver))


class _RecordingInput:
    def __init__(self):
        self.points = []

    def click(self, point):
        self.points.append(point)


def _driver(named_page):
    """A driver whose page probe is fixed and whose clicks are recorded."""
    from kancollebot.expeditions.continuous_dispatch_controller import UiStateEvidence

    game_input = _RecordingInput()
    confirmed: list[str] = []

    def verify_state(expected, **_kwargs):
        confirmed.append(expected)
        return UiStateEvidence(
            state=expected,
            consecutive_states=(expected, expected),
            evidence="test:two_frames",
        )

    driver = CdpContinuousExpeditionUiDriver(
        game_input=game_input,
        verify_state=verify_state,
        event_sink=lambda *_a, **_k: None,
        details=MagicMock(),
        marks=MagicMock(),
        capture=lambda: "",
        home_port_locator=MagicMock(),
        classify_frame=None,
        capture_composited=None,
    )
    driver.probe_drain_state = lambda: named_page
    return driver, game_input, confirmed


class OpenExpeditionListTests(unittest.TestCase):
    def test_already_there_clicks_nothing(self):
        driver, game_input, confirmed = _driver("EXPEDITION_LIST")
        self.assertEqual(driver.open_expedition_list(), "EXPEDITION_LIST")
        self.assertEqual(game_input.points, [])
        self.assertEqual(confirmed, [])

    def test_a_reward_screen_is_refused(self):
        driver, game_input, _confirmed = _driver("RETURN_RESULT")
        with self.assertRaises(ContinuousUiDriverError):
            driver.open_expedition_list()
        self.assertEqual(game_input.points, [])

    def test_the_navigation_is_two_confirmed_steps_and_stops(self):
        driver, game_input, confirmed = _driver("HOME_PORT")
        self.assertEqual(driver.open_expedition_list(), "EXPEDITION_LIST")
        self.assertEqual([p.label for p in game_input.points], ["sortie", "expedition-mode"])
        # No mission row, no confirm button, no start click.
        self.assertEqual(confirmed, ["HOME_PORT", "SORTIE_MENU", "EXPEDITION_LIST"])

    def test_leaving_the_home_port_is_one_confirmed_step(self):
        driver, game_input, confirmed = _driver("HOME_PORT")
        self.assertEqual(driver.leave_home_port(), "SORTIE_MENU")
        self.assertEqual([p.label for p in game_input.points], ["sortie"])
        self.assertEqual(confirmed, ["HOME_PORT", "SORTIE_MENU"])


class WaitCapTests(unittest.TestCase):
    def test_a_zero_cap_reobserves_without_sleeping(self):
        args = Namespace(timer_sleep=True, tick_interval_seconds=10.0)
        sleeper = MagicMock()
        with unittest.mock.patch.object(controller.time, "sleep") as sleep:
            reason = controller._wait_before_next_observation(
                args,
                sleeper,
                runtime=MagicMock(),
                supervisor=MagicMock(),
                target_fleet_ids=(2,),
                tick_index=1,
                tick_budget=3,
                wake_reason="early_return_confirmed",
                sleep_cap_seconds=0.0,
            )
        self.assertEqual(reason, "reobserve_immediately")
        self.assertFalse(sleep.called)
        self.assertFalse(sleeper.wait.called)


if __name__ == "__main__":
    unittest.main()
