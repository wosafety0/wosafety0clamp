"""Early return when the displayed remaining time is under one minute.

Sourced mechanic (see the module docstring): under one minute, returning to the
home port completes the expedition; if the client is already at the home port it
must leave first; an external timer is explicitly *not* a valid input.

These tests pin the decision and the safety edges, not the game: the mechanism
itself is only wikis-sourced, so success is never inferred from the navigation -
it requires a fresh Poi read showing the pending-settlement state.
"""
from __future__ import annotations

import unittest

from kancollebot.expeditions.expedition_early_return import (
    CountdownReading,
    EarlyReturnObservation,
    ExpeditionEarlyReturn,
    read_remaining_from_text,
)

HOME = "HOME_PORT"
LIST = "EXPEDITION_LIST"
SORTIE = "SORTIE_MENU"

ON_EXPEDITION = 1
PENDING = 2


class FakeClient:
    """A scripted client: page, Poi readings and the switches it received."""

    def __init__(self, *, page=LIST, readings=(), texts=("00:42",), open_to=LIST):
        self.page = page
        self._readings = list(readings)
        self._texts = list(texts)
        self._open_to = open_to
        self.go_home_calls = 0
        self.leave_calls = 0
        self.open_calls = 0
        self.switches: list[str] = []

    def read_poi(self):
        if len(self._readings) > 1:
            return self._readings.pop(0)
        return self._readings[0]

    def open_expedition_list(self):
        self.open_calls += 1
        self.page = self._open_to
        self.switches.append("open_expedition_list")
        return self._open_to

    def read_countdown(self):
        if not self._texts:
            return None
        text = self._texts.pop(0)
        return None if text is None else read_remaining_from_text(text)

    def current_page(self):
        return self.page

    def go_home_port(self):
        self.go_home_calls += 1
        self.page = HOME
        self.switches.append("go_home_port")

    def leave_home_port(self):
        self.leave_calls += 1
        self.page = SORTIE
        self.switches.append("leave_home_port")


def coordinator(client, **kwargs):
    return ExpeditionEarlyReturn(
        fleet_id=2,
        read_poi=client.read_poi,
        open_expedition_list=client.open_expedition_list,
        read_countdown=client.read_countdown,
        current_page=client.current_page,
        go_home_port=client.go_home_port,
        leave_home_port=client.leave_home_port,
        **kwargs,
    )


class DecisionTests(unittest.TestCase):
    def test_a_minute_or_more_does_not_trigger(self):
        client = FakeClient(
            readings=[EarlyReturnObservation(ON_EXPEDITION, True)],
            texts=("01:00",),
        )

        outcome = coordinator(client).attempt()

        self.assertFalse(outcome.triggered)
        self.assertEqual(outcome.reason, "displayed_remaining_time_at_or_above_threshold")
        self.assertEqual(outcome.remaining_seconds, 60)
        # It still put the client back at the home port: one switch, no more.
        self.assertEqual(client.go_home_calls, 1)
        self.assertEqual(client.page, HOME)

    def test_under_a_minute_triggers_exactly_one_home_port_switch(self):
        client = FakeClient(
            readings=[
                EarlyReturnObservation(ON_EXPEDITION, True),
                EarlyReturnObservation(PENDING, True),
            ],
            texts=("00:59",),
        )

        outcome = coordinator(client).attempt()

        self.assertTrue(outcome.triggered)
        self.assertTrue(outcome.succeeded)
        self.assertEqual(outcome.remaining_seconds, 59)
        self.assertEqual(outcome.displayed_remaining, "00:59")
        self.assertEqual(outcome.poi_before_status, ON_EXPEDITION)
        self.assertEqual(outcome.poi_after_status, PENDING)
        self.assertEqual(client.go_home_calls, 1)
        self.assertEqual(client.switches.count("go_home_port"), 1)

    def test_a_client_already_at_home_is_taken_away_first(self):
        client = FakeClient(
            page=LIST,
            readings=[
                EarlyReturnObservation(ON_EXPEDITION, True),
                EarlyReturnObservation(PENDING, True),
            ],
            texts=("00:30",),
        )
        # The countdown has already been read on the list; the client then shows
        # the home port (e.g. a refresh raced us), so leaving is required.
        original = client.read_countdown

        def read_countdown():
            reading = original()
            client.page = HOME
            return reading

        client.read_countdown = read_countdown

        outcome = coordinator(client).attempt()

        self.assertTrue(outcome.succeeded)
        self.assertEqual(client.leave_calls, 1)
        self.assertEqual(
            client.switches[-2:], ["leave_home_port", "go_home_port"]
        )

    def test_still_on_expedition_is_never_reported_as_success(self):
        client = FakeClient(
            readings=[
                EarlyReturnObservation(ON_EXPEDITION, True),
                EarlyReturnObservation(ON_EXPEDITION, True),
            ],
            texts=("00:10",),
        )

        outcome = coordinator(client).attempt()

        self.assertTrue(outcome.triggered)
        self.assertFalse(outcome.succeeded)
        self.assertEqual(
            outcome.reason, "client_still_reports_the_fleet_on_expedition"
        )

    def test_a_stale_or_failed_poi_read_stops_before_any_navigation(self):
        client = FakeClient(
            readings=[EarlyReturnObservation(None, False, "read failed")]
        )

        outcome = coordinator(client).attempt()

        self.assertFalse(outcome.triggered)
        self.assertEqual(outcome.reason, "poi_observation_not_fresh")
        self.assertEqual(client.open_calls, 0)
        self.assertEqual(client.go_home_calls, 0)

    def test_an_unreadable_countdown_triggers_nothing(self):
        client = FakeClient(
            readings=[EarlyReturnObservation(ON_EXPEDITION, True)],
            texts=(None,),
        )

        outcome = coordinator(client).attempt()

        self.assertFalse(outcome.triggered)
        self.assertEqual(outcome.reason, "displayed_remaining_time_unreadable")
        self.assertEqual(client.go_home_calls, 1)

    def test_a_fleet_that_is_not_on_an_expedition_is_left_alone(self):
        client = FakeClient(readings=[EarlyReturnObservation(0, True)], texts=("00:05",))

        outcome = coordinator(client).attempt()

        self.assertFalse(outcome.triggered)
        self.assertEqual(outcome.reason, "fleet_is_not_on_an_expedition")
        self.assertEqual(client.open_calls, 0)
        self.assertEqual(client.go_home_calls, 0)

    def test_a_post_switch_stale_read_does_not_claim_success(self):
        client = FakeClient(
            readings=[
                EarlyReturnObservation(ON_EXPEDITION, True),
                EarlyReturnObservation(PENDING, False),
            ],
            texts=("00:20",),
        )

        outcome = coordinator(client).attempt()

        self.assertTrue(outcome.triggered)
        self.assertFalse(outcome.succeeded)
        self.assertEqual(outcome.reason, "post_switch_poi_observation_not_fresh")

    def test_the_outcome_carries_every_log_field_the_contract_asks_for(self):
        client = FakeClient(
            readings=[
                EarlyReturnObservation(ON_EXPEDITION, True),
                EarlyReturnObservation(PENDING, True),
            ],
            texts=("00:45",),
        )

        outcome = coordinator(client).attempt()
        fields = outcome.as_log_fields(fleet_id=2)

        for name in (
            "fleet_id",
            "triggered",
            "succeeded",
            "reason",
            "remaining_seconds",
            "displayed_remaining",
            "page_before",
            "page_after",
            "poi_before_status",
            "poi_after_status",
            "poi_before_fresh",
            "poi_after_fresh",
            "screen_switched",
        ):
            self.assertIn(name, fields)
        self.assertFalse(fields["game_api_requests"])


class ParsingTests(unittest.TestCase):
    def test_minutes_and_seconds_are_parsed_from_the_display(self):
        reading = read_remaining_from_text("12:34")

        self.assertEqual(reading.remaining_seconds, 754)
        self.assertEqual(reading.text, "12:34")

    def test_a_bare_second_count_still_parses(self):
        self.assertEqual(
            read_remaining_from_text("45").remaining_seconds, 45
        )

    def test_the_fullwidth_colon_is_accepted(self):
        self.assertEqual(
            read_remaining_from_text("00：30").remaining_seconds, 30
        )

    def test_hours_minutes_seconds_are_parsed_from_the_display(self):
        # The client renders HH:MM on one line and SS on the next, so a faithful
        # read is three colon-separated fields.
        reading = read_remaining_from_text("00:25:32")

        self.assertEqual(reading.remaining_seconds, 25 * 60 + 32)
        self.assertEqual(reading.text, "00:25:32")

    def test_impossible_values_are_refused(self):
        for text in (
            "",
            "abc",
            "00:99",
            "-1:20",
            "１２:３０",
            "1:99:00",
            "1:00:99",
            "1:2:3:4",
        ):
            with self.subTest(text=text):
                self.assertIsNone(read_remaining_from_text(text))


class ConstructionTests(unittest.TestCase):
    def test_a_zero_threshold_is_refused(self):
        client = FakeClient(readings=[EarlyReturnObservation(ON_EXPEDITION, True)])
        with self.assertRaises(ValueError):
            coordinator(client, threshold_seconds=0)

    def test_a_non_callable_dependency_is_refused(self):
        client = FakeClient(readings=[EarlyReturnObservation(ON_EXPEDITION, True)])
        with self.assertRaises(ValueError):
            ExpeditionEarlyReturn(
                fleet_id=2,
                read_poi=client.read_poi,
                open_expedition_list=None,  # type: ignore[arg-type]
                read_countdown=client.read_countdown,
                current_page=client.current_page,
                go_home_port=client.go_home_port,
                leave_home_port=client.leave_home_port,
            )


if __name__ == "__main__":
    unittest.main()
