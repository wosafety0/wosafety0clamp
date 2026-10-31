"""Offline tests for the read-only freshness bridge diagnostic summary."""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from kancollebot.core.observed_state import ObservedState
from kancollebot.expeditions.planner import ExpeditionDecision, ExpeditionFleetSnapshot
from tools.verify_poi_freshness_bridge import main, summarize_assessments, summarize_state


class FreshnessBridgeDiagnosticTests(unittest.TestCase):
    def test_verified_result_contains_only_safe_summary_fields(self):
        state = SimpleNamespace(
            source_freshness_verified=True,
            observed_at=datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc),
            source="poi-redux:info.fleets+info.ships",
            payload=(object(), object()),
        )
        result = summarize_state(state, "captured")
        self.assertEqual(result["status"], "FRESHNESS_WITNESS_MATCHED")
        self.assertTrue(result["source_freshness_verified"])
        self.assertEqual(result["fleet_count"], 2)
        self.assertEqual(result["observer_status"], "captured")
        self.assertFalse(result["game_actions_enabled"])
        self.assertNotIn("mission_id", result)
        self.assertNotIn("ship_ids", result)

    def test_missing_freshness_flag_fails_closed(self):
        state = SimpleNamespace(
            observed_at=None,
            source="poi-redux",
            payload=(),
        )
        result = summarize_state(state, "no_capture")
        self.assertEqual(result["status"], "FRESHNESS_UNVERIFIED")
        self.assertFalse(result["source_freshness_verified"])
        self.assertFalse(result["game_actions_enabled"])
        self.assertIsNone(result["observed_at"])

    def test_unverified_assessment_is_suppressed_and_never_dispatchable(self):
        now = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
        state = ObservedState(
            payload=(
                ExpeditionFleetSnapshot(
                    fleet_id=1, ship_ids=(11,), fuel_ready=True, ammo_ready=True
                ),
            ),
            observed_at=now,
            source="offline-test",
            source_freshness_verified=False,
        )
        result = summarize_assessments(state, now=now, max_state_age=timedelta(seconds=30))
        self.assertIsNone(result[0]["decision"])
        self.assertFalse(result[0]["observation_accepted"])
        self.assertFalse(result[0]["dispatch_eligible"])

    def test_verified_assessment_reports_plan_but_never_dispatches(self):
        now = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
        state = ObservedState(
            payload=(
                ExpeditionFleetSnapshot(
                    fleet_id=1, ship_ids=(11,), fuel_ready=True, ammo_ready=True
                ),
            ),
            observed_at=now,
            source="offline-test",
            source_freshness_verified=True,
        )
        result = summarize_assessments(state, now=now, max_state_age=timedelta(seconds=30))
        self.assertEqual(result[0]["decision"], ExpeditionDecision.READY_TO_DISPATCH.value)
        self.assertTrue(result[0]["observation_accepted"])
        self.assertFalse(result[0]["dispatch_eligible"])

    def test_non_finite_time_limits_are_rejected_before_observation(self):
        with self.assertRaises(SystemExit) as timeout_error:
            main(["--timeout-seconds", "nan"])
        self.assertEqual(timeout_error.exception.code, 2)
        with self.assertRaises(SystemExit) as age_error:
            main(["--max-age-seconds", "inf"])
        self.assertEqual(age_error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
