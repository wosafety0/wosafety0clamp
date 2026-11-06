from unittestfuture__ import annotations

import unittest

from kancollebot.core.network_fault_classifier import (
    NetworkRecoveryGate,
    ObserverFaultClass,
    RecoveryState,
    classify_observer_status,
)


class ObserverStatusClassificationTests(unittest.TestCase):
    def test_captured_is_the_only_positive_observer_signal(self):
        self.assertIs(classify_observer_status("captured"), ObserverFaultClass.HEALTHY)

    def test_natural_response_timeout_is_ambiguous_not_disconnected(self):
        self.assertIs(
            classify_observer_status("natural_response_timeout"),
            ObserverFaultClass.SUSPECT,
        )

    def test_cdp_disconnects_are_classified_separately(self):
        for status in (
            "target_discovery_failed",
            "cdp_connection_failed",
            "cdp_connection_closed",
        ):
            with self.subTest(status=status):
                self.assertIs(
                    classify_observer_status(status),
                    ObserverFaultClass.DISCONNECTED,
                )

    def test_observer_failures_are_not_misreported_as_network_disconnects(self):
        for status in (
            "launch_failed",
            "script_missing",
            "observer_failed",
            "cdp_protocol_error",
            "cdp_command_failed",
            "output_too_large",
            "invalid_output",
            "invalid_json",
            "invalid_shape",
            "observer_exception:RuntimeError",
        ):
            with self.subTest(status=status):
                self.assertIs(
                    classify_observer_status(status),
                    ObserverFaultClass.OBSERVER_UNAVAILABLE,
                )

    def test_server_and_session_rejections_are_separate(self):
        for status in ("api_result_201", "login_expired", "maintenance"):
            with self.subTest(status=status):
                self.assertIs(
                    classify_observer_status(status),
                    ObserverFaultClass.SERVER_REJECTED,
                )

    def test_unknown_and_startup_statuses_do_not_claim_health(self):
        for status in ("not_started", "cancelled", "future_status", "", None):
            with self.subTest(status=status):
                self.assertIs(classify_observer_status(status), ObserverFaultClass.UNKNOWN)


class NetworkRecoveryGateTests(unittest.TestCase):
    def test_initial_gate_is_open_but_unknown_does_not_claim_recovery(self):
        gate = NetworkRecoveryGate()
        self.assertTrue(gate.automation_allowed)
        decision = gate.observe("not_started", revision=0)
        self.assertIs(decision.state, RecoveryState.RUNNING)
        self.assertIs(decision.fault_class, ObserverFaultClass.UNKNOWN)

    def test_one_timeout_pauses_as_suspect_without_claiming_disconnect(self):
        gate = NetworkRecoveryGate()
        decision = gate.observe("natural_response_timeout", revision=1)
        self.assertIs(decision.state, RecoveryState.SUSPECT)
        self.assertIs(decision.fault_class, ObserverFaultClass.SUSPECT)
        self.assertFalse(decision.automation_allowed)
        self.assertIn("ambiguous", decision.reason)

    def test_definitive_cdp_disconnect_pauses_automation(self):
        gate = NetworkRecoveryGate()
        decision = gate.observe("cdp_connection_closed", revision=1)
        self.assertIs(decision.state, RecoveryState.PAUSED)
        self.assertFalse(decision.automation_allowed)

    def test_repeated_reads_of_same_revision_do_not_change_state(self):
        gate = NetworkRecoveryGate()
        first = gate.observe("cdp_connection_closed", revision=2)
        repeated = gate.observe("captured", revision=2)
        self.assertEqual(repeated.state, first.state)
        self.assertEqual(repeated.observer_status, first.observer_status)
        self.assertFalse(repeated.changed)

    def test_recovered_witness_requires_business_reconciliation(self):
        gate = NetworkRecoveryGate()
        gate.observe("cdp_connection_failed", revision=1)
        decision = gate.observe("captured", revision=2)
        self.assertIs(decision.state, RecoveryState.RECONCILE_REQUIRED)
        self.assertFalse(decision.automation_allowed)
        released = gate.mark_reconciled(safe=True, reason="journal and fleet tuples agree")
        self.assertIs(released.state, RecoveryState.RUNNING)
        self.assertTrue(released.automation_allowed)

    def test_inconclusive_reconciliation_latches_manual_required(self):
        gate = NetworkRecoveryGate()
        gate.observe("cdp_connection_closed", revision=1)
        gate.observe("captured", revision=2)
        decision = gate.mark_reconciled(safe=False, reason="dispatch outcome unknown")
        self.assertIs(decision.state, RecoveryState.MANUAL_REQUIRED)
        self.assertFalse(decision.automation_allowed)
        gate.observe("captured", revision=3)
        self.assertIs(gate.state, RecoveryState.MANUAL_REQUIRED)

    def test_server_rejection_requires_manual_handling(self):
        gate = NetworkRecoveryGate()
        decision = gate.observe("api_result_201", revision=1)
        self.assertIs(decision.state, RecoveryState.MANUAL_REQUIRED)
        self.assertFalse(decision.automation_allowed)

    def test_invalid_or_stale_revisions_are_rejected_or_ignored(self):
        gate = NetworkRecoveryGate()
        with self.assertRaises(ValueError):
            gate.observe("captured", revision=-1)
        gate.observe("captured", revision=3)
        self.assertIs(gate.observe("cdp_connection_closed", revision=2).state, RecoveryState.RUNNING)

    def test_manual_required_can_only_be_latched_more_strictly(self):
        gate = NetworkRecoveryGate()
        decision = gate.require_manual("operator intervention required")
        self.assertIs(decision.state, RecoveryState.MANUAL_REQUIRED)
        self.assertFalse(decision.automation_allowed)
        gate.observe("captured", revision=1)
        self.assertIs(gate.state, RecoveryState.MANUAL_REQUIRED)


if __name__ == "__main__":
    unittest.main()
