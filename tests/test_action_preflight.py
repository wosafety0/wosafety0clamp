import unittest
from datetime import datetime, timedelta, timezone

from kancollebot.core.observed_state import ObservedState
from kancollebot.safety.action_preflight import authorize_planned_action
from kancollebot.safety.policy import SafetyPolicy


class ActionPreflightTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
        self.state = ObservedState(
            payload={"fleet_id": 1},
            observed_at=self.now - timedelta(seconds=5),
            source="unit-test",
            source_freshness_verified=True,
        )

    def test_default_policy_blocks_live_action(self):
        with self.assertRaisesRegex(PermissionError, "disabled"):
            authorize_planned_action(
                "dispatch_expedition",
                self.state,
                now=self.now,
                max_state_age=timedelta(seconds=30),
                policy=SafetyPolicy(),
                confirmed=True,
            )

    def test_requires_explicit_confirmation_when_enabled(self):
        with self.assertRaisesRegex(PermissionError, "confirmation"):
            authorize_planned_action(
                "dispatch_expedition",
                self.state,
                now=self.now,
                max_state_age=timedelta(seconds=30),
                policy=SafetyPolicy(allow_live_actions=True),
            )

    def test_rejects_stale_state_before_authorization(self):
        stale = ObservedState(
            payload={},
            observed_at=self.now - timedelta(minutes=2),
            source="unit-test",
            source_freshness_verified=True,
        )
        with self.assertRaisesRegex(ValueError, "stale"):
            authorize_planned_action(
                "dispatch_expedition",
                stale,
                now=self.now,
                max_state_age=timedelta(seconds=30),
                policy=SafetyPolicy(allow_live_actions=True),
                confirmed=True,
            )

    def test_rejects_future_dated_state(self):
        future = ObservedState(
            payload={},
            observed_at=self.now + timedelta(seconds=1),
            source="unit-test",
            source_freshness_verified=True,
        )
        with self.assertRaisesRegex(ValueError, "stale"):
            authorize_planned_action(
                "dispatch_expedition",
                future,
                now=self.now,
                max_state_age=timedelta(seconds=30),
                policy=SafetyPolicy(allow_live_actions=True),
                confirmed=True,
            )

    def test_rejects_recent_observation_without_source_freshness_evidence(self):
        unverified = ObservedState(
            payload={"fleet_id": 1},
            observed_at=self.now - timedelta(seconds=1),
            source="poi",
        )
        with self.assertRaisesRegex(ValueError, "Source data freshness has not been verified"):
            authorize_planned_action(
                "dispatch_expedition",
                unverified,
                now=self.now,
                max_state_age=timedelta(seconds=30),
                policy=SafetyPolicy(allow_live_actions=True),
                confirmed=True,
            )

    def test_success_returns_preflight_evidence_only(self):
        result = authorize_planned_action(
            "dispatch_expedition",
            self.state,
            now=self.now,
            max_state_age=timedelta(seconds=30),
            policy=SafetyPolicy(
                allow_live_actions=True,
                require_explicit_confirmation=True,
            ),
            confirmed=True,
        )
        self.assertEqual(result.action_name, "dispatch_expedition")
        self.assertEqual(result.state_source, "unit-test")
        self.assertEqual(result.checked_at, self.now)

    def test_empty_action_name_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "action_name"):
            authorize_planned_action(
                " ",
                self.state,
                now=self.now,
                max_state_age=timedelta(seconds=30),
                policy=SafetyPolicy(allow_live_actions=True),
                confirmed=True,
            )


if __name__ == "__main__":
    unittest.main()
