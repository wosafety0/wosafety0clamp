"""Bounded, motion-aware observation after one settlement action.

Live 2026-11-07 01:33Z the global drain clicked a return notice, saw the same
page 2.5 s later and stopped the whole run with ``ui_stuck`` while the client was
still animating. Extending that timeout to a larger number would have "fixed"
the symptom and hidden the next one, so the window is no longer the verdict:

* frames still changing -> keep observing, up to the post-action budget;
* frames provably frozen on the acted-on page -> ``ui_stuck`` (a real stuck UI
  must still be reportable);
* server already reports the settlement collected -> observe only, never click,
  and stop with ``settled_ui_pending``;
* moving but never naming a new page -> ``observation_budget``, which is a
  deliberate stop rather than a "needs a human" failure.

An observer with no fingerprint keeps the historical verdict verbatim, because
there is nothing better to base it on.
"""
from __future__ import annotations

import unittest

from kancollebot.expeditions.settlement_drain import (
    DrainErrorType,
    DrainPageState,
    DrainSample,
    SettlementDrainRequest,
    SettlementDrainSession,
)

HOME = DrainPageState.HOME_PORT
NOTICE = DrainPageState.RETURN_NOTICE
RESULT = DrainPageState.EXPEDITION_RESULT

POLL = 0.1
FROZEN = (64,) * 8


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            raise AssertionError("the drain must never busy-loop")
        self.now += seconds


class TimelineObserver:
    """Time-driven page timeline plus a frame that moves while animating."""

    def __init__(self, clock, segments, *, animate_until=0.0, with_sample=True) -> None:
        self.clock = clock
        self._segments = tuple((page, float(until)) for page, until in segments)
        self._animate_until = float(animate_until)
        self._with_sample = with_sample
        self._counter = 0

    def page_at(self, when: float) -> DrainPageState:
        for page, until in self._segments:
            if when < until:
                return page
        return self._segments[-1][0]

    def observe(self) -> DrainPageState:
        return self.page_at(self.clock.now)

    def sample(self) -> DrainSample:
        if not self._with_sample:
            raise AssertionError("sample() must not be called without the probe")
        self._counter += 1
        if self.clock.now < self._animate_until:
            # Alternate the extremes so the frame is unambiguously *different*:
            # a one-step change would score 0.996 similarity and read as still.
            frame = (255,) * 8 if self._counter % 2 else (0,) * 8
        else:
            frame = FROZEN
        return DrainSample(page=self.page_at(self.clock.now), fingerprint=frame)


class FakeDriver:
    def __init__(self, observer) -> None:
        self.observer = observer
        self.actions: list[DrainPageState] = []

    def advance(self, page: DrainPageState) -> None:
        self.actions.append(page)


def request(**overrides) -> SettlementDrainRequest:
    values = {
        "max_actions": 6,
        "timeout_seconds": 60.0,
        "action_interval_seconds": 2.5,
        "stable_frame_count": 2,
        "action_observation_seconds": 30.0,
    }
    values.update(overrides)
    return SettlementDrainRequest(**values)


def make_session(observer, clock, *, settled=None):
    events: list[tuple[str, dict]] = []
    session = SettlementDrainSession(
        observer,
        FakeDriver(observer),
        clock=clock,
        frame_poll_interval_seconds=POLL,
        event_sink=lambda event, fields: events.append((event, dict(fields))),
        settlement_settled=settled,
    )
    return session, events


class PlainObserver:
    """An observer with no frame probe: only ``observe()`` exists."""

    def __init__(self, page) -> None:
        self._page = page

    def observe(self) -> DrainPageState:
        return self._page


class AnimatingScreenTests(unittest.TestCase):
    def test_an_animating_screen_is_waited_out_instead_of_reported_stuck(self):
        clock = FakeClock()
        # The notice holds for 20 s while animating, then the result page, then
        # home. The old code declared ui_stuck at 2.5 s.
        observer = TimelineObserver(
            clock,
            [(NOTICE, 20.0), (RESULT, 26.0), (HOME, 1e9)],
            animate_until=26.0,
        )
        session, events = make_session(observer, clock)

        result = session.drain(request())

        self.assertTrue(result.reached_home)
        self.assertEqual(result.actions_performed, 2)
        self.assertIsNone(result.error_encountered)
        names = [name for name, _ in events]
        self.assertNotIn("settlement_drain_ui_frozen", names)

    def test_a_frozen_screen_on_the_acted_page_is_still_reported_as_stuck(self):
        clock = FakeClock()
        observer = TimelineObserver(clock, [(NOTICE, 1e9)], animate_until=0.0)
        session, events = make_session(observer, clock)

        result = session.drain(request())

        self.assertFalse(result.reached_home)
        self.assertIs(result.error_encountered, DrainErrorType.UI_STUCK)
        self.assertTrue(result.requires_manual_intervention)
        self.assertEqual(result.actions_performed, 1)
        self.assertIn("settlement_drain_ui_frozen", [name for name, _ in events])

    def test_motion_without_a_page_stops_at_the_observation_budget(self):
        clock = FakeClock()
        observer = TimelineObserver(
            clock, [(NOTICE, 1e9)], animate_until=1e9
        )
        session, _ = make_session(observer, clock)

        result = session.drain(request(action_observation_seconds=10.0))

        self.assertFalse(result.reached_home)
        self.assertIs(
            result.error_encountered, DrainErrorType.OBSERVATION_BUDGET_EXHAUSTED
        )
        # A deliberate stop, not a "someone must look at the client" failure.
        self.assertFalse(result.requires_manual_intervention)
        self.assertEqual(result.actions_performed, 1)

    def test_an_observer_without_a_fingerprint_keeps_the_historical_verdict(self):
        clock = FakeClock()
        observer = PlainObserver(NOTICE)
        session, _ = make_session(observer, clock)

        result = session.drain(request())

        self.assertIs(result.error_encountered, DrainErrorType.UI_STUCK)


class ServerFirstTests(unittest.TestCase):
    def test_a_settled_account_never_gets_another_settlement_click(self):
        clock = FakeClock()
        observer = TimelineObserver(clock, [(NOTICE, 1e9)], animate_until=1e9)
        session, events = make_session(observer, clock, settled=lambda: True)

        result = session.drain(request())

        self.assertFalse(result.reached_home)
        self.assertIs(result.error_encountered, DrainErrorType.SETTLED_UI_PENDING)
        self.assertFalse(result.requires_manual_intervention)
        self.assertEqual(result.actions_performed, 0)
        self.assertEqual(session._driver.actions, [])  # type: ignore[attr-defined]
        self.assertNotIn("settlement_drain_action", [name for name, _ in events])

    def test_settlement_completed_during_the_wait_sends_no_second_click(self):
        clock = FakeClock()
        observer = TimelineObserver(clock, [(NOTICE, 1e9)], animate_until=1e9)
        state = {"settled": False}

        def settled() -> bool:
            # The first action has already been sent; the server now shows the
            # settlement as collected while the client is still animating.
            return state["settled"]

        session, events = make_session(observer, clock, settled=settled)

        original_advance = session._driver.advance  # type: ignore[attr-defined]

        def advance(page):
            state["settled"] = True
            original_advance(page)

        session._driver.advance = advance  # type: ignore[attr-defined]

        result = session.drain(request(action_observation_seconds=10.0))

        self.assertEqual(session._driver.actions, [NOTICE])  # type: ignore[attr-defined]
        self.assertIs(result.error_encountered, DrainErrorType.SETTLED_UI_PENDING)
        self.assertIn("settlement_drain_ui_only_wait", [name for name, _ in events])

    def test_a_failing_settled_check_is_not_read_as_settled(self):
        clock = FakeClock()
        observer = TimelineObserver(
            clock, [(NOTICE, 4.0), (RESULT, 8.0), (HOME, 1e9)], animate_until=8.0
        )

        def settled() -> bool:
            raise OSError("poi read failed")

        session, events = make_session(observer, clock, settled=settled)

        result = session.drain(request())

        self.assertTrue(result.reached_home)
        self.assertIn(
            "settlement_drain_settled_check_failed", [name for name, _ in events]
        )


if __name__ == "__main__":
    unittest.main()
