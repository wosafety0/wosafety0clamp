"""Offline tests for the read-only freshness bridge diagnostic summary."""
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from tools.verify_poi_freshness_bridge import main, summarize_state


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

    def test_non_finite_time_limits_are_rejected_before_observation(self):
        with self.assertRaises(SystemExit) as timeout_error:
            main(["--timeout-seconds", "nan"])
        self.assertEqual(timeout_error.exception.code, 2)
        with self.assertRaises(SystemExit) as age_error:
            main(["--max-age-seconds", "inf"])
        self.assertEqual(age_error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
