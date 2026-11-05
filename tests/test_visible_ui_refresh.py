from __future__ import annotations

import unittest
from types import SimpleNamespace

from kancollebot.adapters.poi.cdp_input import CdpInputError
from kancollebot.adapters.poi.visible_ui_refresh import (
    EXPEDITION_MODE,
    HOME_PORT,
    SORTIE,
    FreshnessRefreshingStateReader,
    VisibleUiPortRefreshDriver,
    VisibleUiRefreshError,
)
from kancollebot.expeditions.continuous_dispatch_controller import UiStateEvidence
from kancollebot.expeditions.continuous_scheduler import UiOperationArbiter


class FakeInput:
    def __init__(self, fail_at: int | None = None):
        self.clicks = []
        self.fail_at = fail_at

    def click(self, point):
        self.clicks.append(point)
        if self.fail_at == len(self.clicks):
            raise RuntimeError("ambiguous click")


class FakeReader:
    def __init__(self, observations):
        self.observations = list(observations)
        self.calls = 0

    def read_fleets(self):
        self.calls += 1
        if len(self.observations) > 1:
            return self.observations.pop(0)
        return self.observations[0]


def observation(fresh: bool):
    return SimpleNamespace(source_freshness_verified=fresh)


class VisibleUiPortRefreshDriverTests(unittest.TestCase):
    def make_driver(self, states, *, fail_click_at=None):
        game_input = FakeInput(fail_at=fail_click_at)
        events = []
        sleeps = []

        def verify(expected):
            actual = states.pop(0)
            if isinstance(actual, Exception):
                raise actual
            return UiStateEvidence(
                state=actual,
                consecutive_states=(actual, actual),
                evidence="two_consecutive_test_frames",
            )

        driver = VisibleUiPortRefreshDriver(
            game_input=game_input,
            verify_state=verify,
            ui_arbiter=UiOperationArbiter(),
            event_sink=lambda event, fields: events.append((event, dict(fields))),
            sleep=sleeps.append,
            settle_seconds=1.0,
        )
        return driver, game_input, events, sleeps

    def test_refresh_uses_only_the_verified_navigation_sequence(self):
        driver, game_input, events, sleeps = self.make_driver(
            ["HOME_PORT", "SORTIE_MENU", "EXPEDITION_LIST", "HOME_PORT"]
        )

        result = driver.refresh_port_state()

        self.assertEqual(
            game_input.clicks,
            [SORTIE, EXPEDITION_MODE, HOME_PORT],
        )
        self.assertEqual(sleeps, [1.0, 1.0, 1.0])
        self.assertEqual(result.state, "HOME_PORT")
        self.assertEqual(result.consecutive_states, ("HOME_PORT", "HOME_PORT"))
        self.assertEqual(events[-1][0], "ui_refresh_completed")
        self.assertEqual(events[-1][1]["clicks_sent"], 3)

    def test_initial_state_failure_sends_no_click(self):
        driver, game_input, events, _ = self.make_driver(["UNKNOWN"])

        with self.assertRaises(VisibleUiRefreshError):
            driver.refresh_port_state()

        self.assertEqual(game_input.clicks, [])
        self.assertEqual(events[-1][0], "ui_refresh_blocked")
        self.assertEqual(events[-1][1]["clicks_attempted"], 0)
        self.assertEqual(events[-1][1]["clicks_completed"], 0)

    def test_initial_cdp_error_records_only_safe_method_and_code(self):
        diagnostic_error = CdpInputError(
            "CDP command failed: Accessibility.getFullAXTree (code=-32000)"
        )
        driver, game_input, events, _ = self.make_driver([diagnostic_error])

        with self.assertRaises(VisibleUiRefreshError):
            driver.refresh_port_state()

        fields = events[-1][1]
        self.assertEqual(fields["diagnostic_category"], "cdp_error_response")
        self.assertEqual(fields["cdp_method"], "Accessibility.getFullAXTree")
        self.assertEqual(fields["cdp_error_code"], -32000)
        self.assertEqual(game_input.clicks, [])
        self.assertNotIn("message", fields)
        self.assertNotIn("sensitive", str(fields))

    def test_failed_post_click_checkpoint_stops_without_retry(self):
        driver, game_input, events, _ = self.make_driver(
            ["HOME_PORT", "UNKNOWN"]
        )

        with self.assertRaises(VisibleUiRefreshError):
            driver.refresh_port_state()

        self.assertEqual(game_input.clicks, [SORTIE])
        self.assertEqual(events[-1][0], "ui_refresh_blocked")
        self.assertEqual(events[-1][1]["clicks_attempted"], 1)
        self.assertEqual(events[-1][1]["clicks_completed"], 1)
        self.assertFalse(events[-1][1]["outcome_ambiguous"])

    def test_a_click_that_provably_did_nothing_is_repeated_once(self):
        # Live 2026-11-05: a 出撃 click landed during a transition and the client
        # stayed on HOME_PORT. The precondition being re-proven afterwards makes
        # that a *known* no-op, not an ambiguous outcome, so one repeat is safe.
        driver, game_input, events, _ = self.make_driver(
            [
                "HOME_PORT",       # initial precondition
                "HOME_PORT",       # after the click: nothing happened
                "HOME_PORT",       # precondition re-proven
                "SORTIE_MENU",     # after the repeat
                "EXPEDITION_LIST",
                "HOME_PORT",
            ]
        )

        result = driver.refresh_port_state()

        self.assertEqual(
            game_input.clicks, [SORTIE, SORTIE, EXPEDITION_MODE, HOME_PORT]
        )
        self.assertEqual(result.state, "HOME_PORT")
        names = [name for name, _ in events]
        self.assertIn("ui_refresh_click_repeated", names)
        repeated = next(fields for name, fields in events if name == "ui_refresh_click_repeated")
        self.assertEqual(repeated["target"], SORTIE.label)
        self.assertEqual(repeated["state_after_first_click"], "HOME_PORT")
        self.assertEqual(events[-1][0], "ui_refresh_completed")
        self.assertEqual(events[-1][1]["clicks_sent"], 4)

    def test_a_click_that_changed_the_screen_is_never_repeated(self):
        # The screen moved, so the outcome is ambiguous: no second click, by
        # design. This is the line the repeat must not cross.
        driver, game_input, events, _ = self.make_driver(
            ["HOME_PORT", "UNKNOWN", "EXPEDITION_LIST"]
        )

        with self.assertRaises(VisibleUiRefreshError):
            driver.refresh_port_state()

        self.assertEqual(game_input.clicks, [SORTIE])
        self.assertEqual(events[-1][0], "ui_refresh_blocked")
        self.assertNotIn("ui_refresh_click_repeated", [name for name, _ in events])

    def test_ambiguous_click_is_not_replayed(self):
        driver, game_input, events, _ = self.make_driver(
            ["HOME_PORT"], fail_click_at=1
        )

        with self.assertRaises(VisibleUiRefreshError):
            driver.refresh_port_state()

        self.assertEqual(game_input.clicks, [SORTIE])
        self.assertEqual(events[-1][1]["clicks_attempted"], 1)
        self.assertEqual(events[-1][1]["clicks_completed"], 0)
        self.assertTrue(events[-1][1]["outcome_ambiguous"])


class FreshnessRefreshingStateReaderTests(unittest.TestCase):
    def test_already_fresh_observation_does_not_navigate(self):
        reader = FakeReader([observation(True)])
        driver = SimpleNamespace(refresh_port_state=lambda: self.fail("unexpected refresh"))
        wrapper = FreshnessRefreshingStateReader(reader, driver)

        result = wrapper.read_fleets()

        self.assertTrue(result.source_freshness_verified)
        self.assertEqual(reader.calls, 1)

    def test_unverified_observation_refreshes_then_waits_for_matching_witness(self):
        reader = FakeReader([
            observation(False),
            observation(False),
            observation(True),
        ])
        refresh_calls = []
        driver = SimpleNamespace(refresh_port_state=lambda: refresh_calls.append("refresh"))
        clock = [0.0]
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds

        wrapper = FreshnessRefreshingStateReader(
            reader,
            driver,
            max_wait_seconds=2.0,
            poll_interval_seconds=0.5,
            refresh_cooldown_seconds=10.0,
            monotonic=lambda: clock[0],
            sleep=sleep,
        )

        result = wrapper.read_fleets()

        self.assertTrue(result.source_freshness_verified)
        self.assertEqual(refresh_calls, ["refresh"])
        self.assertEqual(reader.calls, 3)
        self.assertEqual(sleeps, [0.5])

    def test_timeout_returns_unverified_observation_without_claiming_success(self):
        reader = FakeReader([observation(False)])
        refresh_calls = []
        driver = SimpleNamespace(refresh_port_state=lambda: refresh_calls.append("refresh"))
        clock = [0.0]

        def sleep(seconds):
            clock[0] += seconds

        wrapper = FreshnessRefreshingStateReader(
            reader,
            driver,
            max_wait_seconds=1.0,
            poll_interval_seconds=0.5,
            monotonic=lambda: clock[0],
            sleep=sleep,
        )

        result = wrapper.read_fleets()

        self.assertFalse(result.source_freshness_verified)
        self.assertEqual(refresh_calls, ["refresh"])
        self.assertEqual(reader.calls, 3)

    def test_cooldown_prevents_repeating_an_ambiguous_navigation(self):
        reader = FakeReader([observation(False)])
        refresh_calls = []
        driver = SimpleNamespace(refresh_port_state=lambda: refresh_calls.append("refresh"))
        clock = [0.0]

        def sleep(seconds):
            clock[0] += seconds

        wrapper = FreshnessRefreshingStateReader(
            reader,
            driver,
            max_wait_seconds=0.5,
            poll_interval_seconds=0.5,
            refresh_cooldown_seconds=10.0,
            monotonic=lambda: clock[0],
            sleep=sleep,
        )

        first = wrapper.read_fleets()
        second = wrapper.read_fleets()

        self.assertFalse(first.source_freshness_verified)
        self.assertFalse(second.source_freshness_verified)
        self.assertEqual(refresh_calls, ["refresh"])

    def test_cooldown_starts_after_the_witness_wait_finishes(self):
        reader = FakeReader([observation(False)])
        refresh_calls = []
        driver = SimpleNamespace(refresh_port_state=lambda: refresh_calls.append("refresh"))
        clock = [0.0]

        def sleep(seconds):
            clock[0] += seconds

        wrapper = FreshnessRefreshingStateReader(
            reader,
            driver,
            max_wait_seconds=1.0,
            poll_interval_seconds=0.5,
            refresh_cooldown_seconds=0.5,
            monotonic=lambda: clock[0],
            sleep=sleep,
        )

        first = wrapper.read_fleets()
        self.assertFalse(first.source_freshness_verified)
        self.assertEqual(clock[0], 1.0)

        second = wrapper.read_fleets()
        self.assertFalse(second.source_freshness_verified)
        self.assertEqual(refresh_calls, ["refresh"])

        clock[0] += 0.5
        wrapper.read_fleets()
        self.assertEqual(refresh_calls, ["refresh", "refresh"])


if __name__ == "__main__":
    unittest.main()
