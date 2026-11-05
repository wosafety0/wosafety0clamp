"""P0-4: return detection with observation rounds.

The rule under test is the one the real client forced: the return timer
expiring is not a return. Only a *fresh* status-2 observation can confirm one,
and a refresh whose round has been superseded may not click.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from kancollebot.core.return_refresh import (
    STATE_AT_SEA,
    STATE_RETURNED_PENDING,
    STATE_UNKNOWN,
    ReturnRefreshCoordinator,
    ReturnRefreshRefused,
    ReturnWatch,
)

BASE = datetime(2026, 11, 6, 3, 0, tzinfo=timezone.utc)
RETURN_AT = BASE + timedelta(minutes=20)


class FakeClock:
    """One monotonic step plus a wall clock that only moves when told to."""

    def __init__(self, start: float = 1000.0, now: datetime = BASE):
        self.value = start
        self.now = now

    def monotonic(self) -> float:
        return self.value

    def advance(self, seconds: float, *, wall: float | None = None) -> None:
        self.value += seconds
        self.now = self.now + timedelta(seconds=seconds if wall is None else wall)

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)

    def clock(self) -> datetime:
        return self.now


class FakeObservation:
    def __init__(self, *, status, observed_at=BASE, verified=True, mission_id=1):
        self.mission_status = status
        self.mission_id = mission_id
        self.source_freshness_verified = verified
        self.observed_at = observed_at


class ScriptedSource:
    """Feeds observations in order and records refreshes."""

    def __init__(self, *observations, refresh_effect=None):
        self.observations = list(observations)
        self.refreshes = 0
        self._refresh_effect = refresh_effect

    def observe(self, fleet_id):
        item = self.observations[0]
        if len(self.observations) > 1:
            self.observations.pop(0)
        return item

    def refresh(self):
        self.refreshes += 1
        if self._refresh_effect is not None:
            self._refresh_effect(self)


def coordinator(clock, *, events=None, grace=0.0, interval=30.0):
    return ReturnRefreshCoordinator(
        clock=clock.clock,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        event_sink=(lambda name, fields: events.append((name, dict(fields))))
        if events is not None
        else None,
        grace_seconds=grace,
        min_refresh_interval_seconds=interval,
    )


class ReturnRefreshTests(unittest.TestCase):
    def test_the_timer_alone_never_confirms_a_return(self):
        clock = FakeClock()
        events = []
        source = ScriptedSource(FakeObservation(status=1))
        coord = coordinator(clock, events=events)

        watch = coord.begin_watch(fleet_id=4, expected_return_at=RETURN_AT)
        outcome = coord.refresh_and_confirm(
            watch=watch, observe=source.observe, refresh=source.refresh
        )

        self.assertFalse(outcome.confirmed_return)
        self.assertEqual(outcome.reason, "return_time_not_reached")
        self.assertEqual(source.refreshes, 0)
        self.assertEqual(outcome.state_before, STATE_AT_SEA)
        self.assertFalse(outcome.return_time_reached)

    def test_a_fresh_returned_observation_needs_no_refresh(self):
        clock = FakeClock()
        source = ScriptedSource(FakeObservation(status=2))
        coord = coordinator(clock)

        watch = coord.begin_watch(fleet_id=4)
        outcome = coord.refresh_and_confirm(
            watch=watch, observe=source.observe, refresh=source.refresh
        )

        self.assertTrue(outcome.confirmed_return)
        self.assertEqual(outcome.reason, "returned_observed_without_refresh")
        self.assertEqual(source.refreshes, 0)

    def test_an_unverified_returned_observation_is_not_enough(self):
        clock = FakeClock()
        source = ScriptedSource(FakeObservation(status=2, verified=False))
        coord = coordinator(clock)

        watch = coord.begin_watch(fleet_id=4, expected_return_at=BASE)
        clock.advance(1)
        outcome = coord.refresh_and_confirm(
            watch=watch, observe=source.observe, refresh=source.refresh
        )

        # The stale status-2 reading is not proof, so a refresh was sent; the
        # post-refresh frame is the same unverified one, so nothing confirms.
        self.assertFalse(outcome.confirmed_return)
        self.assertEqual(outcome.reason, "observation_after_refresh_not_fresh")
        self.assertEqual(source.refreshes, 1)

    def test_a_refresh_that_produces_a_fresh_return_confirms(self):
        clock = FakeClock()
        at_sea = FakeObservation(status=1)
        returned = FakeObservation(status=2, observed_at=BASE + timedelta(minutes=21))
        source = ScriptedSource(at_sea)

        def effect(_source):
            source.observations = [returned]

        source._refresh_effect = effect

        def refresh() -> None:
            source.refresh()
            clock.advance(5)

        coord = coordinator(clock)
        watch = coord.begin_watch(fleet_id=4, expected_return_at=BASE)
        clock.advance(1)
        outcome = coord.refresh_and_confirm(
            watch=watch, observe=source.observe, refresh=refresh
        )

        self.assertTrue(outcome.confirmed_return)
        self.assertEqual(outcome.reason, "returned_confirmed_by_fresh_observation")
        self.assertTrue(outcome.observation_after_refresh_verified)
        self.assertEqual(outcome.state_after, STATE_RETURNED_PENDING)

    def test_a_refresh_that_does_not_confirm_reports_why(self):
        clock = FakeClock()
        source = ScriptedSource(FakeObservation(status=1))
        coord = coordinator(clock)
        watch = coord.begin_watch(fleet_id=4, expected_return_at=BASE)
        clock.advance(1)
        outcome = coord.refresh_and_confirm(
            watch=watch, observe=source.observe, refresh=source.refresh
        )
        self.assertFalse(outcome.confirmed_return)
        self.assertEqual(outcome.reason, "observation_after_refresh_not_fresh")

    def test_a_stale_round_never_clicks(self):
        clock = FakeClock()
        source = ScriptedSource(FakeObservation(status=1))
        coord = coordinator(clock)
        stale = coord.begin_watch(fleet_id=4, expected_return_at=BASE)
        clock.advance(1)
        coord.begin_watch(fleet_id=4)  # a newer round takes over

        outcome = coord.refresh_and_confirm(
            watch=stale, observe=source.observe, refresh=source.refresh
        )

        self.assertFalse(outcome.confirmed_return)
        self.assertTrue(outcome.stale_generation)
        self.assertIn("stale_generation", outcome.reason)
        self.assertEqual(source.refreshes, 0)

    def test_invalidating_before_a_refresh_stops_the_click(self):
        """The timeout/shutdown path: a callback fires after cancellation."""
        clock = FakeClock()
        source = ScriptedSource(FakeObservation(status=1))
        events = []
        coord = coordinator(clock, events=events, grace=0.0)
        watch = coord.begin_watch(fleet_id=4, expected_return_at=BASE)
        clock.advance(1)

        coord.invalidate("process stopping")
        with self.assertRaises(ReturnRefreshRefused):
            coord.require_current(watch)

        outcome = coord.refresh_and_confirm(
            watch=watch, observe=source.observe, refresh=source.refresh
        )
        self.assertTrue(outcome.stale_generation)
        self.assertFalse(outcome.confirmed_return)
        self.assertEqual(source.refreshes, 0)
        self.assertIn("return_refresh_invalidated", [name for name, _ in events])

    def test_a_round_that_goes_stale_after_the_click_is_not_confirmed(self):
        clock = FakeClock()
        source = ScriptedSource(FakeObservation(status=1))
        returned = FakeObservation(status=2, observed_at=BASE + timedelta(minutes=21))
        coord = coordinator(clock)
        watch = coord.begin_watch(fleet_id=4, expected_return_at=BASE)
        clock.advance(1)

        def refresh_that_starts_a_new_round() -> None:
            source.observations = [returned]
            clock.advance(5)
            coord.begin_watch(fleet_id=4)

        outcome = coord.refresh_and_confirm(
            watch=watch, observe=source.observe, refresh=refresh_that_starts_a_new_round
        )
        self.assertFalse(outcome.confirmed_return)
        self.assertTrue(outcome.stale_generation)
        self.assertEqual(outcome.reason, "stale_generation_after_refresh")

    def test_the_cooldown_suppresses_a_second_refresh(self):
        clock = FakeClock()
        source = ScriptedSource(FakeObservation(status=1))
        coord = coordinator(clock, interval=30.0)

        first = coord.begin_watch(fleet_id=4, expected_return_at=BASE)
        clock.advance(1)
        coord.refresh_and_confirm(
            watch=first, observe=source.observe, refresh=source.refresh
        )
        self.assertEqual(source.refreshes, 1)

        second = coord.begin_watch(fleet_id=4, expected_return_at=BASE)
        clock.advance(1)
        outcome = coord.refresh_and_confirm(
            watch=second, observe=source.observe, refresh=source.refresh
        )
        self.assertEqual(source.refreshes, 1)
        self.assertEqual(outcome.reason, "refresh_cooldown")
        self.assertFalse(outcome.confirmed_return)

    def test_a_missing_fleet_reads_as_unknown_not_as_returned(self):
        clock = FakeClock()
        unknown = FakeObservation(status=None, mission_id=None, verified=False)
        source = ScriptedSource(unknown)
        coord = coordinator(clock)
        watch = coord.begin_watch(fleet_id=4, expected_return_at=RETURN_AT)
        outcome = coord.refresh_and_confirm(
            watch=watch, observe=source.observe, refresh=source.refresh
        )
        self.assertEqual(outcome.state_before, STATE_UNKNOWN)
        self.assertFalse(outcome.confirmed_return)

    def test_the_log_rebuilds_the_whole_round(self):
        clock = FakeClock()
        events = []
        source = ScriptedSource(FakeObservation(status=1))
        returned = FakeObservation(status=2, observed_at=BASE + timedelta(minutes=21))
        coord = coordinator(clock, events=events)
        watch = coord.begin_watch(fleet_id=4, expected_return_at=BASE)
        clock.advance(1)

        def refresh() -> None:
            source.observations = [returned]
            clock.advance(5)

        coord.refresh_and_confirm(watch=watch, observe=source.observe, refresh=refresh)

        names = [name for name, _ in events]
        self.assertIn("return_refresh_observed", names)
        self.assertIn("return_refresh_action", names)
        self.assertIn("return_refresh_completed", names)
        completed = dict(events)["return_refresh_completed"]
        for field in (
            "state_before_refresh",
            "refresh_requested",
            "refresh_performed",
            "state_after_refresh",
            "confirmed_return",
            "generation",
            "return_time_reached",
        ):
            self.assertIn(field, completed)

    def test_a_broken_sink_cannot_change_the_decision(self):
        clock = FakeClock()

        def exploding_sink(event, fields):
            raise RuntimeError("sink down")

        coord = ReturnRefreshCoordinator(
            clock=clock.clock,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            event_sink=exploding_sink,
            grace_seconds=0.0,
        )
        source = ScriptedSource(FakeObservation(status=2))
        watch = coord.begin_watch(fleet_id=4)
        outcome = coord.refresh_and_confirm(
            watch=watch, observe=source.observe, refresh=source.refresh
        )
        self.assertTrue(outcome.confirmed_return)

    def test_invalid_construction_is_refused(self):
        with self.assertRaises(ValueError):
            ReturnRefreshCoordinator(grace_seconds=-1)
        with self.assertRaises(ValueError):
            ReturnRefreshCoordinator(min_refresh_interval_seconds=float("nan"))
        with self.assertRaises(ValueError):
            ReturnRefreshCoordinator(event_sink="not callable")


class ReturnWatchTests(unittest.TestCase):
    def test_a_watch_requires_a_positive_fleet_and_aware_timer(self):
        coord = ReturnRefreshCoordinator()
        with self.assertRaises(ValueError):
            coord.begin_watch(fleet_id=0)
        with self.assertRaises(ValueError):
            coord.begin_watch(fleet_id=4, expected_return_at=datetime(2026, 11, 6))
        self.assertIsInstance(coord.begin_watch(fleet_id=4), ReturnWatch)

    def test_generations_increase_monotonically(self):
        coord = ReturnRefreshCoordinator()
        first = coord.begin_watch(fleet_id=4)
        second = coord.begin_watch(fleet_id=4)
        self.assertEqual(second.generation, first.generation + 1)
        self.assertFalse(coord.is_current(first))
        self.assertTrue(coord.is_current(second))


if __name__ == "__main__":
    unittest.main()
