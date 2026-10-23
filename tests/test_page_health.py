import unittest
from datetime import datetime, timedelta, timezone

from kancollebot.adapters.poi.page_health import (
    PageHealthObservation,
    PageHealthStatus,
    evaluate_page_health,
    require_healthy_page,
)


class PageHealthPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)

    def observation(self, **changes):
        values = {
            "checked_at": self.now,
            "target_found": True,
            "document_ready_state": "complete",
            "title": "Poi - KanColle",
            "url": "app://poi",
            "error_text": "",
            "latest_valid_state_at": self.now - timedelta(seconds=1),
        }
        values.update(changes)
        return PageHealthObservation(**values)

    def test_fresh_page_and_state_are_healthy(self):
        decision = evaluate_page_health(self.observation(), now=self.now)
        self.assertEqual(decision.status, PageHealthStatus.HEALTHY)
        self.assertEqual(decision.reasons, ())

    def test_connection_closed_error_page_is_unhealthy(self):
        decision = evaluate_page_health(
            self.observation(title="Error", error_text="net::ERR_CONNECTION_CLOSED"),
            now=self.now,
        )
        self.assertEqual(decision.status, PageHealthStatus.UNHEALTHY)
        self.assertIn("browser_connection_error_detected", decision.reasons)

    def test_missing_probe_or_target_is_not_healthy(self):
        self.assertEqual(
            evaluate_page_health(None, now=self.now).status,
            PageHealthStatus.UNKNOWN,
        )
        self.assertEqual(
            evaluate_page_health(self.observation(target_found=False), now=self.now).status,
            PageHealthStatus.UNHEALTHY,
        )

    def test_document_loading_is_unknown(self):
        decision = evaluate_page_health(
            self.observation(document_ready_state="loading"), now=self.now
        )
        self.assertEqual(decision.status, PageHealthStatus.UNKNOWN)

    def test_old_state_is_not_healthy_even_if_page_is_complete(self):
        decision = evaluate_page_health(
            self.observation(latest_valid_state_at=self.now - timedelta(seconds=30)),
            now=self.now,
        )
        self.assertEqual(decision.status, PageHealthStatus.UNKNOWN)
        self.assertIn("game_state_stale_or_future_dated", decision.reasons)

    def test_recovery_requires_state_observed_after_connection_failure(self):
        failure_at = self.now - timedelta(seconds=3)
        stale = evaluate_page_health(
            self.observation(latest_valid_state_at=failure_at),
            now=self.now,
            connection_failure_at=failure_at,
        )
        fresh = evaluate_page_health(
            self.observation(latest_valid_state_at=failure_at + timedelta(microseconds=1)),
            now=self.now,
            connection_failure_at=failure_at,
        )
        self.assertEqual(stale.status, PageHealthStatus.UNHEALTHY)
        self.assertEqual(fresh.status, PageHealthStatus.HEALTHY)

    def test_future_probe_and_future_state_fail_closed(self):
        future_probe = evaluate_page_health(
            self.observation(checked_at=self.now + timedelta(seconds=1)), now=self.now
        )
        future_state = evaluate_page_health(
            self.observation(latest_valid_state_at=self.now + timedelta(seconds=1)),
            now=self.now,
        )
        self.assertEqual(future_probe.status, PageHealthStatus.UNKNOWN)
        self.assertEqual(future_state.status, PageHealthStatus.UNKNOWN)

    def test_gate_blocks_unknown_and_unhealthy(self):
        for decision in (
            evaluate_page_health(None, now=self.now),
            evaluate_page_health(
                self.observation(error_text="ERR_CONNECTION_CLOSED"), now=self.now
            ),
        ):
            with self.assertRaises(RuntimeError):
                require_healthy_page(decision)

    def test_naive_timestamps_are_rejected(self):
        with self.assertRaises(ValueError):
            self.observation(checked_at=datetime(2026, 10, 23, 12, 0))


if __name__ == "__main__":
    unittest.main()
