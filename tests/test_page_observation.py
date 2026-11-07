"""The shared bounded observation engine: budgets, motion and verdicts.

These tests exist because three live failures shared one cause - acting on a
screen that was still transitioning, then declaring the run dead after a fixed
window. The engine is the single place that decides when waiting is still
justified, so every boundary is pinned here:

* a page must be named on two consecutive frames;
* a provably frozen unrecognised screen keeps being observed and does NOT
  consume the changing-frame budget;
* a changing unrecognised screen does consume it and stops at the limit;
* a stable third page stops immediately instead of waiting for a target that
  will never arrive;
* the interval backs off 1.5 -> 2 -> 3 s and resets the moment the screen moves.
"""
from __future__ import annotations

import unittest

from kancollebot.observability.page_observation import (
    BoundedPageObservation,
    MotionTracker,
    ObservationVerdict,
    PageObservationBudget,
    PageSample,
)

STILL = (128,) * 12
MOVED = (200,) * 12
MOVED_AGAIN = (40,) * 12


class FakeClock:
    """Only an explicit sleep moves time, so no test can be timing-dependent."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            raise AssertionError("the observation loop must never busy-loop")
        self.sleeps.append(seconds)
        self.now += seconds


class ScriptedSampler:
    """Return one scripted sample per call; the last one repeats."""

    def __init__(self, samples) -> None:
        self._samples = list(samples)
        self.calls = 0

    def __call__(self) -> PageSample:
        index = min(self.calls, len(self._samples) - 1)
        self.calls += 1
        return self._samples[index]


def sample(state: str, frame=STILL) -> PageSample:
    return PageSample(state=state, scores={state: 0.99}, fingerprint=frame)


def runner(clock: FakeClock, **budget) -> BoundedPageObservation:
    defaults = {
        "total_seconds": 60.0,
        "changed_unknown_limit": 20,
        "settle_samples": 2,
    }
    defaults.update(budget)
    return BoundedPageObservation(
        budget=PageObservationBudget(**defaults),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


class TargetIdentificationTests(unittest.TestCase):
    def test_a_page_must_be_named_on_two_consecutive_frames(self):
        clock = FakeClock()
        outcome = runner(clock).run(
            ScriptedSampler([sample("UNKNOWN"), sample("SORTIE_MENU"), sample("SORTIE_MENU")]),
            target="SORTIE_MENU",
            label="after open_sortie",
        )

        self.assertIs(outcome.verdict, ObservationVerdict.TARGET_REACHED)
        self.assertEqual(outcome.samples, 3)
        self.assertEqual(outcome.state, "SORTIE_MENU")

    def test_a_single_frame_of_the_target_is_not_enough(self):
        clock = FakeClock()
        outcome = runner(clock, total_seconds=4.0).run(
            ScriptedSampler([sample("SORTIE_MENU"), sample("UNKNOWN", MOVED)]),
            target="SORTIE_MENU",
        )

        self.assertIsNot(outcome.verdict, ObservationVerdict.TARGET_REACHED)

    def test_total_budget_exhaustion_is_reported_not_hidden(self):
        clock = FakeClock()
        outcome = runner(clock, total_seconds=3.0).run(
            ScriptedSampler([sample("UNKNOWN", MOVED_AGAIN)]),
            target="SORTIE_MENU",
        )

        self.assertIs(outcome.verdict, ObservationVerdict.TIME_EXHAUSTED)
        self.assertIn("budget exhausted", outcome.reason)
        self.assertTrue(outcome.elapsed_seconds >= 3.0)


class StillVersusChangingTests(unittest.TestCase):
    def test_a_frozen_unrecognised_screen_never_consumes_the_changing_budget(self):
        clock = FakeClock()
        # Twelve provably identical UNKNOWN frames in a row, then the page
        # arrives. With a changing-frame limit of 3 this would have died if a
        # still frame were counted as a change.
        samples = [sample("UNKNOWN", STILL)] * 12 + [sample("HOME_PORT", STILL)] * 2
        outcome = runner(clock, changed_unknown_limit=3, total_seconds=120.0).run(
            ScriptedSampler(samples), target="HOME_PORT"
        )

        self.assertIs(outcome.verdict, ObservationVerdict.TARGET_REACHED)
        self.assertEqual(outcome.changed_unknown_samples, 1)  # the first frame only
        self.assertTrue(outcome.still_unknown_samples >= 10)

    def test_a_changing_unrecognised_screen_consumes_the_budget(self):
        clock = FakeClock()
        frames = [MOVED, MOVED_AGAIN, (255,) * 12, (80,) * 12, (10,) * 12, (200,) * 12]
        samples = [sample("UNKNOWN", frame) for frame in frames]
        outcome = runner(clock, changed_unknown_limit=4, total_seconds=120.0).run(
            ScriptedSampler(samples), target="HOME_PORT"
        )

        self.assertIs(
            outcome.verdict, ObservationVerdict.CHANGED_UNKNOWN_EXHAUSTED
        )
        self.assertEqual(outcome.changed_unknown_samples, 4)

    def test_a_still_unrecognised_frame_is_never_a_safe_start(self):
        clock = FakeClock()
        outcome = runner(clock, total_seconds=5.0).run(
            ScriptedSampler([sample("UNKNOWN", STILL)]),
            target="SORTIE_MENU",
            safe_start="HOME_PORT",
        )

        self.assertIsNot(outcome.verdict, ObservationVerdict.SAFE_START_CONFIRMED)
        self.assertIsNot(outcome.verdict, ObservationVerdict.TARGET_REACHED)

    def test_interval_backs_off_to_three_seconds_while_still(self):
        clock = FakeClock()
        outcome = runner(clock, total_seconds=20.0).run(
            ScriptedSampler([sample("UNKNOWN", STILL)]), target="HOME_PORT"
        )

        self.assertIs(outcome.verdict, ObservationVerdict.TIME_EXHAUSTED)
        # 1.5, 2, 3, 3, 3 ... : the first two intervals are the ramp, then the cap.
        self.assertEqual(clock.sleeps[:4], [1.5, 2.0, 3.0, 3.0])

    def test_a_change_restores_the_fastest_interval(self):
        clock = FakeClock()
        samples = [
            sample("UNKNOWN", STILL),
            sample("UNKNOWN", STILL),
            sample("UNKNOWN", MOVED),
            sample("UNKNOWN", STILL),
        ]
        runner(clock, total_seconds=8.0).run(
            ScriptedSampler(samples), target="HOME_PORT"
        )

        self.assertEqual(clock.sleeps[:3], [1.5, 2.0, 1.5])


class VerdictTests(unittest.TestCase):
    def test_a_stable_start_page_is_reported_as_safe_start(self):
        clock = FakeClock()
        outcome = runner(clock).run(
            ScriptedSampler([sample("HOME_PORT", STILL)]),
            target="SORTIE_MENU",
            safe_start="HOME_PORT",
        )

        self.assertIs(outcome.verdict, ObservationVerdict.SAFE_START_CONFIRMED)
        self.assertEqual(outcome.stable_state, "HOME_PORT")

    def test_a_stable_third_page_stops_the_observation(self):
        clock = FakeClock()
        sampler = ScriptedSampler([sample("EXPEDITION_LIST", STILL)])
        outcome = runner(clock).run(
            sampler,
            target="SORTIE_MENU",
            safe_start="HOME_PORT",
            known_pages=("HOME_PORT", "SORTIE_MENU", "EXPEDITION_LIST"),
        )

        self.assertIs(outcome.verdict, ObservationVerdict.OTHER_PAGE)
        self.assertEqual(outcome.state, "EXPEDITION_LIST")
        self.assertEqual(sampler.calls, 2)

    def test_an_unlisted_page_keeps_being_observed(self):
        clock = FakeClock()
        outcome = runner(clock, total_seconds=4.0).run(
            ScriptedSampler([sample("MISSION_SELECTED", STILL)]),
            target="SORTIE_MENU",
            safe_start="HOME_PORT",
            known_pages=("HOME_PORT", "SORTIE_MENU"),
        )

        self.assertIs(outcome.verdict, ObservationVerdict.TIME_EXHAUSTED)


class MotionTrackerTests(unittest.TestCase):
    def test_first_frame_has_no_verdict_about_motion(self):
        tracker = MotionTracker()

        motion = tracker.observe(STILL)

        self.assertIsNone(motion.changed)
        self.assertEqual(motion.still_run, 0)

    def test_identical_frames_are_still_and_different_frames_are_not(self):
        tracker = MotionTracker()

        tracker.observe(STILL)
        same = tracker.observe(STILL)
        moved = tracker.observe(MOVED)

        self.assertIs(same.changed, False)
        self.assertEqual(same.similarity, 1.0)
        self.assertIs(moved.changed, True)
        self.assertEqual(moved.still_run, 0)

    def test_missing_fingerprints_never_claim_motion(self):
        tracker = MotionTracker()

        tracker.observe(None)
        motion = tracker.observe(None)

        self.assertIsNone(motion.changed)


if __name__ == "__main__":
    unittest.main()
