from __future__ import annotations

import unittest

from kancollebot.expeditions.recovery_classification import (
    RecoveryCategory,
    classify_recovery_signals,
)


class RecoveryClassificationTests(unittest.TestCase):
    def assert_never_resumes(self, result):
        self.assertFalse(result.resume_allowed)

    def test_captured_natural_witness_is_healthy_but_not_resume_authority(self):
        result = classify_recovery_signals(observer_status="captured")
        self.assertEqual(result.category, RecoveryCategory.HEALTHY_WITNESS)
        self.assertFalse(result.pause_required)
        self.assertFalse(result.manual_required)
        self.assert_never_resumes(result)

    def test_natural_response_timeout_is_not_assumed_to_be_network_failure(self):
        result = classify_recovery_signals(
            observer_status="natural_response_timeout",
            cdp_connected=True,
        )
        self.assertEqual(result.category, RecoveryCategory.NO_NATURAL_WITNESS)
        self.assertFalse(result.pause_required)
        self.assertFalse(result.reconnect_observer)
        self.assert_never_resumes(result)

    def test_cdp_connection_failures_request_observer_reconnect(self):
        for status in (
            "target_discovery_failed",
            "cdp_connection_failed",
            "cdp_connection_closed",
        ):
            with self.subTest(status=status):
                result = classify_recovery_signals(observer_status=status)
                self.assertEqual(result.category, RecoveryCategory.CDP_UNAVAILABLE)
                self.assertTrue(result.pause_required)
                self.assertTrue(result.reconnect_observer)
                self.assertFalse(result.manual_required)
                self.assert_never_resumes(result)

    def test_explicit_disconnected_transport_takes_precedence_over_timeout(self):
        result = classify_recovery_signals(
            observer_status="natural_response_timeout",
            cdp_connected=False,
        )
        self.assertEqual(result.category, RecoveryCategory.CDP_UNAVAILABLE)
        self.assertTrue(result.pause_required)
        self.assertTrue(result.reconnect_observer)

    def test_target_crash_requires_manual_review_even_when_reconnect_is_possible(self):
        result = classify_recovery_signals(target_crashed=True)
        self.assertEqual(result.category, RecoveryCategory.CDP_UNAVAILABLE)
        self.assertTrue(result.pause_required)
        self.assertTrue(result.reconnect_observer)
        self.assertTrue(result.manual_required)

    def test_observer_process_failures_pause_and_allow_only_observer_reconnect(self):
        for status in (
            "process_timeout",
            "launch_failed",
            "observer_failed",
            "script_missing",
            "cdp_protocol_error",
            "cdp_command_failed",
        ):
            with self.subTest(status=status):
                result = classify_recovery_signals(observer_status=status)
                self.assertEqual(result.category, RecoveryCategory.OBSERVER_UNAVAILABLE)
                self.assertTrue(result.pause_required)
                self.assertTrue(result.reconnect_observer)
                self.assertFalse(result.manual_required)
                self.assert_never_resumes(result)

    def test_api_result_201_is_a_manual_circuit_breaker(self):
        result = classify_recovery_signals(
            observer_status="cdp_connection_closed",
            api_result=201,
        )
        self.assertEqual(result.category, RecoveryCategory.SERVER_REJECTED)
        self.assertTrue(result.pause_required)
        self.assertFalse(result.reconnect_observer)
        self.assertTrue(result.manual_required)
        self.assertEqual(result.reason, "api_result_201_circuit_open")

    def test_other_non_success_api_results_are_not_retried(self):
        result = classify_recovery_signals(api_result=503)
        self.assertEqual(result.category, RecoveryCategory.SERVER_REJECTED)
        self.assertTrue(result.manual_required)
        self.assertFalse(result.reconnect_observer)

    def test_login_and_maintenance_signals_require_manual_action(self):
        cases = (
            ({"login_required": True}, RecoveryCategory.LOGIN_REQUIRED),
            ({"visual_state": "login"}, RecoveryCategory.LOGIN_REQUIRED),
            ({"maintenance": True}, RecoveryCategory.MAINTENANCE),
            ({"visual_state": "maintenance"}, RecoveryCategory.MAINTENANCE),
        )
        for kwargs, category in cases:
            with self.subTest(kwargs=kwargs):
                result = classify_recovery_signals(**kwargs)
                self.assertEqual(result.category, category)
                self.assertTrue(result.pause_required)
                self.assertTrue(result.manual_required)
                self.assertFalse(result.reconnect_observer)
                self.assert_never_resumes(result)

    def test_cat_screen_pauses_without_automatically_refreshing(self):
        result = classify_recovery_signals(visual_state="cat")
        self.assertEqual(result.category, RecoveryCategory.CAT_SCREEN)
        self.assertTrue(result.pause_required)
        self.assertFalse(result.reconnect_observer)
        self.assertFalse(result.manual_required)
        self.assert_never_resumes(result)

    def test_invalid_and_unknown_signals_fail_closed(self):
        for status in ("invalid_output", "invalid_json", "invalid_shape", "response_rejected", "mystery"):
            with self.subTest(status=status):
                result = classify_recovery_signals(observer_status=status)
                self.assertEqual(result.category, RecoveryCategory.UNKNOWN)
                self.assertTrue(result.pause_required)
                self.assertTrue(result.manual_required)
                self.assertFalse(result.reconnect_observer)
                self.assert_never_resumes(result)

    def test_inactive_observer_does_not_create_a_false_network_alarm(self):
        for status in ("not_started", "cancelled"):
            with self.subTest(status=status):
                result = classify_recovery_signals(observer_status=status)
                self.assertEqual(result.category, RecoveryCategory.INACTIVE)
                self.assertFalse(result.pause_required)
                self.assertFalse(result.manual_required)
                self.assert_never_resumes(result)

    def test_unknown_visual_state_does_not_override_a_captured_witness(self):
        result = classify_recovery_signals(
            observer_status="captured",
            visual_state="unknown",
        )
        self.assertEqual(result.category, RecoveryCategory.HEALTHY_WITNESS)
        self.assertFalse(result.pause_required)
        self.assert_never_resumes(result)


if __name__ == "__main__":
    unittest.main()
