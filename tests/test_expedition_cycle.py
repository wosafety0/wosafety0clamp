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

    def observation(self, state, mission, seconds):
        fleets = parse_fleet_payload([{
            "api_id": 2, "api_name": "第2艦隊",
            "api_ship": [73, -1, -1, -1, -1, -1],
            "api_mission": [state, mission, 0, 0],
        }])
        return ObservedState(fleets, self.now - timedelta(seconds=seconds), "poi")

    def resolve(self, cycle, before_state=0, after_state=1, after_mission=5, **kwargs):
        return resolve_expedition_cycle(
            cycle, before=self.observation(before_state, 0, 4),
            after=self.observation(after_state, after_mission, 1),
            now=self.now, max_age=timedelta(seconds=10), **kwargs,
        )

    def test_staging_is_only_an_intent(self):
        staged = stage_expedition_decision(self.decision)
        self.assertEqual(staged.status, "awaiting_observation")
        self.assertEqual(staged.pending, self.decision)

    def test_confirmed_dispatch_releases_cycle(self):
        cycle, outcome = self.resolve(stage_expedition_decision(self.decision))
        self.assertEqual(cycle.status, "ready")
        self.assertEqual(outcome.status, "confirmed")

    def test_unconfirmed_dispatch_stops_without_retry(self):
        cycle, outcome = self.resolve(stage_expedition_decision(self.decision), after_state=0)
        self.assertEqual(cycle.status, "stopped")
        self.assertEqual(outcome.status, "not_confirmed")
        self.assertIsNone(cycle.pending)

    def test_mismatched_mission_stops(self):
        cycle, outcome = self.resolve(stage_expedition_decision(self.decision), after_mission=7)
        self.assertEqual(cycle.status, "stopped")
        self.assertEqual(outcome.status, "unknown")

    def test_error_stops_without_verification(self):
        cycle, outcome = self.resolve(stage_expedition_decision(self.decision), action_error="timeout")
        self.assertEqual(cycle.status, "stopped")
        self.assertIsNone(outcome)

    def test_missing_post_observation_stops(self):
        staged = stage_expedition_decision(self.decision)
        cycle, outcome = resolve_expedition_cycle(
            staged, before=self.observation(0, 0, 4), after=None,
            now=self.now, max_age=timedelta(seconds=10),
        )
        self.assertEqual(cycle.status, "stopped")
        self.assertIsNone(outcome)

    def test_unresolved_cycle_cannot_stage_next_action(self):
        pending = stage_expedition_decision(self.decision)
        blocked = stage_expedition_decision(self.decision, previous=pending)
        self.assertEqual(blocked.status, "stopped")

    def test_stopped_cycle_cannot_stage_next_action(self):
        stopped = ExpeditionCycle("stopped", None, "uncertain")
        self.assertEqual(stage_expedition_decision(self.decision, previous=stopped).status, "stopped")

    def test_collect_transition_confirmed(self):
        collect = ExpeditionDecision("collect", 2, 5, "completed")
        staged = stage_expedition_decision(collect)
        cycle, outcome = self.resolve(staged, before_state=2, after_state=0)
        self.assertEqual(cycle.status, "ready")
        self.assertEqual(outcome.status, "confirmed")

    def test_wait_is_noop(self):
        wait = ExpeditionDecision("wait", None, None, "no candidate")
        self.assertEqual(stage_expedition_decision(wait).status, "ready")

    def test_invalid_dispatch_is_stopped(self):
        invalid = ExpeditionDecision("dispatch", 2, 0, "invalid")
        self.assertEqual(stage_expedition_decision(invalid).status, "stopped")

    def test_resolving_twice_is_blocked(self):
        staged = stage_expedition_decision(self.decision)
        ready, _ = self.resolve(staged)
        stopped, outcome = self.resolve(ready)
        self.assertEqual(stopped.status, "stopped")
        self.assertIsNone(outcome)


if __name__ == "__main__":
    unittest.main()
