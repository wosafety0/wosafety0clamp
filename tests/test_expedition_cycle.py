import unittest
from datetime import datetime, timedelta, timezone

from kancollebot.core.expedition_cycle import (
    ExpeditionCycle, stage_expedition_decision, resolve_expedition_cycle,
)
from kancollebot.core.expedition_planner import ExpeditionDecision
from kancollebot.core.fleet_parser import parse_fleet_payload
from kancollebot.core.observed_state import ObservedState


class ExpeditionCycleTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 22, 14, tzinfo=timezone.utc)
        self.decision = ExpeditionDecision("dispatch", 2, 5, "candidate")

    def observation(self, state, mission, seconds, source="poi"):
        fleets = parse_fleet_payload([{
            "api_id": 2, "api_name": "第2艦隊",
            "api_ship": [73, -1, -1, -1, -1, -1],
            "api_mission": [state, mission, 0, 0],
        }])
        return ObservedState(fleets, self.now - timedelta(seconds=seconds), source)

    def stage(self, decision=None, before_state=0, before_mission=0):
        return stage_expedition_decision(
            decision or self.decision,
            before=self.observation(before_state, before_mission, 4),
        )

    def resolve(self, cycle, after_state=1, after_mission=5, **kwargs):
        return resolve_expedition_cycle(
            cycle, after=self.observation(after_state, after_mission, 1),
            now=self.now, max_age=timedelta(seconds=10), **kwargs,
        )

    def test_staging_is_bound_to_planning_observation(self):
        staged = self.stage()
        self.assertEqual(staged.status, "awaiting_observation")
        self.assertEqual(staged.pending, self.decision)
        self.assertEqual(staged.baseline, self.observation(0, 0, 4))

    def test_missing_baseline_fails_closed(self):
        staged = stage_expedition_decision(self.decision)
        self.assertEqual(staged.status, "stopped")
        self.assertIsNone(staged.pending)

    def test_confirmed_dispatch_releases_cycle(self):
        cycle, outcome = self.resolve(self.stage())
        self.assertEqual(cycle.status, "ready")
        self.assertEqual(outcome.status, "confirmed")

    def test_unconfirmed_dispatch_stops_without_retry(self):
        cycle, outcome = self.resolve(self.stage(), after_state=0)
        self.assertEqual(cycle.status, "stopped")
        self.assertEqual(outcome.status, "not_confirmed")
        self.assertIsNone(cycle.pending)

    def test_mismatched_mission_stops(self):
        cycle, outcome = self.resolve(self.stage(), after_mission=7)
        self.assertEqual(cycle.status, "stopped")
        self.assertEqual(outcome.status, "unknown")

    def test_error_stops_without_verification(self):
        cycle, outcome = self.resolve(self.stage(), action_error="timeout")
        self.assertEqual(cycle.status, "stopped")
        self.assertIsNone(outcome)

    def test_missing_post_observation_stops(self):
        cycle, outcome = resolve_expedition_cycle(
            self.stage(), after=None, now=self.now,
            max_age=timedelta(seconds=10),
        )
        self.assertEqual(cycle.status, "stopped")
        self.assertIsNone(outcome)

    def test_unresolved_cycle_cannot_stage_next_action(self):
        pending = self.stage()
        blocked = stage_expedition_decision(self.decision, before=self.observation(0, 0, 4), previous=pending)
        self.assertEqual(blocked.status, "stopped")

    def test_stopped_cycle_cannot_stage_next_action(self):
        stopped = ExpeditionCycle("stopped", None, "uncertain")
        self.assertEqual(stage_expedition_decision(self.decision, before=self.observation(0, 0, 4), previous=stopped).status, "stopped")

    def test_collect_transition_confirmed(self):
        collect = ExpeditionDecision("collect", 2, 5, "completed")
        staged = self.stage(collect, before_state=2, before_mission=5)
        cycle, outcome = self.resolve(staged, after_state=0)
        self.assertEqual(cycle.status, "ready")
        self.assertEqual(outcome.status, "confirmed")

    def test_wait_is_noop(self):
        wait = ExpeditionDecision("wait", None, None, "no candidate")
        self.assertEqual(stage_expedition_decision(wait).status, "ready")

    def test_invalid_dispatch_is_stopped(self):
        invalid = ExpeditionDecision("dispatch", 2, 0, "invalid")
        self.assertEqual(stage_expedition_decision(invalid, before=self.observation(0, 0, 4)).status, "stopped")

    def test_resolving_twice_is_blocked(self):
        ready, _ = self.resolve(self.stage())
        stopped, outcome = self.resolve(ready)
        self.assertEqual(stopped.status, "stopped")
        self.assertIsNone(outcome)

    def test_post_snapshot_must_be_newer_than_bound_baseline(self):
        cycle = self.stage()
        same_time = self.observation(1, 5, 4)
        stopped, outcome = resolve_expedition_cycle(
            cycle, after=same_time, now=self.now, max_age=timedelta(seconds=10),
        )
        self.assertEqual(stopped.status, "stopped")
        self.assertEqual(outcome.status, "unknown")

    def test_different_source_stops(self):
        stopped, outcome = resolve_expedition_cycle(
            self.stage(), after=self.observation(1, 5, 1, source="other-source"),
            now=self.now, max_age=timedelta(seconds=10),
        )
        self.assertEqual(stopped.status, "stopped")
        self.assertEqual(outcome.status, "unknown")


if __name__ == "__main__":
    unittest.main()
