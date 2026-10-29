"""Offline safety tests for the read-only expedition assessment layer."""
import unittest
from datetime import datetime, timedelta, timezone

from kancollebot.core.observed_state import ObservedState
from kancollebot.expeditions.planner import (
    ExpeditionDecision,
    ExpeditionFleetSnapshot,
)
from kancollebot.expeditions.read_only_assessment import assess_observed_fleets

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def sample_state(*, verified: bool, observed_at: datetime = NOW):
    fleet = ExpeditionFleetSnapshot(
        fleet_id=1,
        ship_ids=(11, 12),
        fuel_ready=True,
        ammo_ready=True,
    )
    return ObservedState(
        payload=(fleet,),
        observed_at=observed_at,
        source="offline-test",
        source_freshness_verified=verified,
    )


class ReadOnlyAssessmentTests(unittest.TestCase):
    def test_unverified_source_suppresses_planning_even_when_recent(self):
        result = assess_observed_fleets(
            sample_state(verified=False), now=NOW, max_state_age=timedelta(seconds=30)
        )
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0].decision)
        self.assertFalse(result[0].observation_accepted)
        self.assertFalse(result[0].dispatch_eligible)

    def test_stale_verified_source_suppresses_planning(self):
        old = NOW - timedelta(minutes=2)
        result = assess_observed_fleets(
            sample_state(verified=True, observed_at=old),
            now=NOW,
            max_state_age=timedelta(seconds=30),
        )
        self.assertIsNone(result[0].decision)
        self.assertFalse(result[0].observation_accepted)
        self.assertFalse(result[0].dispatch_eligible)

    def test_fresh_verified_source_only_produces_a_plan(self):
        result = assess_observed_fleets(
            sample_state(verified=True), now=NOW, max_state_age=timedelta(seconds=30)
        )
        self.assertTrue(result[0].observation_accepted)
        self.assertEqual(result[0].decision, ExpeditionDecision.READY_TO_DISPATCH)
        self.assertFalse(result[0].dispatch_eligible)

    def test_future_dated_source_suppresses_planning(self):
        future = NOW + timedelta(seconds=1)
        result = assess_observed_fleets(
            sample_state(verified=True, observed_at=future),
            now=NOW,
            max_state_age=timedelta(seconds=30),
        )
        self.assertIsNone(result[0].decision)
        self.assertFalse(result[0].dispatch_eligible)

    def test_negative_max_age_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must not be negative"):
            assess_observed_fleets(
                sample_state(verified=True), now=NOW, max_state_age=timedelta(seconds=-1)
            )


if __name__ == "__main__":
    unittest.main()
