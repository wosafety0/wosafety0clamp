
import unittest
from types import SimpleNamespace

from kancollebot.expeditions.continuous_supervisor import ContinuousExpeditionSupervisor
from kancollebot.expeditions.continuous_scheduler import FleetPhase
from kancollebot.expeditions.recovery_classification import (
    RecoveryPauseGate,
    classify_recovery_signals,
)


class FakeScheduler:
    dispatch_fleet_ids = (2, 3)

    def __init__(self, phases, home_verified=None):
        self.states = {
            fleet_id: SimpleNamespace(
                phase=phase,
                home_port_verified=(home_verified or {}).get(fleet_id, False),
            )
            for fleet_id, phase in phases.items()
        }

    def fleet(self, fleet_id):
        return self.states[fleet_id]


class FakeMonitor:
    def __init__(self, scheduler, state_reader, event_sink):
        self.scheduler = scheduler
        self.state_reader = state_reader
        self.event_sink = event_sink
        self.calls = 0

    def tick(self):
        self.calls += 1
        return SimpleNamespace(fleets=())


class FakeController:
    def __init__(
        self,
        scheduler,
        state_reader,
        event_sink,
        *,
        enabled=True,
        verify_home_port=True,
    ):
        self.scheduler = scheduler
        self.state_reader = state_reader
        self.event_sink = event_sink
        self.allow_ui_actions = enabled
        self.verify_home_port = verify_home_port
        self.calls = []
        # The supervisor mints one dispatch identity per attempt and hands it
        # to the controller; the double keeps them so a test can prove the
        # scope and the ledger name the same attempt.
        self.dispatch_ids: list[str | None] = []

    def dispatch_one_ready(self, fleet_id, *, dispatch_id=None):
        self.calls.append(fleet_id)
        self.dispatch_ids.append(dispatch_id)
        self.scheduler.states[fleet_id].phase = FleetPhase.EXPEDITION_ACTIVE
        self.scheduler.states[fleet_id].home_port_verified = self.verify_home_port
        return SimpleNamespace(fleet_id=fleet_id)


class ContinuousExpeditionSupervisorTests(unittest.TestCase):
    def make_supervisor(
        self,
        phases,
        *,
        home_verified=None,
        enabled=True,
        verify_home_port=True,
    ):
        scheduler = FakeScheduler(phases, home_verified)
        reader = object()
        event_sink = object()
        monitor = FakeMonitor(scheduler, reader, event_sink)
        controller = FakeController(
            scheduler,
            reader,
            event_sink,
            enabled=enabled,
            verify_home_port=verify_home_port,
        )
        return ContinuousExpeditionSupervisor(
            monitor=monitor,
            dispatch_controller=controller,
        ), scheduler, monitor, controller

    def test_dispatches_ready_fleets_sequentially_in_configured_order(self):
        supervisor, _, monitor, controller = self.make_supervisor({
            2: FleetPhase.READY,
            3: FleetPhase.READY,
        })
        report = supervisor.tick()
        self.assertEqual(monitor.calls, 1)
        self.assertEqual(controller.calls, [2, 3])
        self.assertEqual([item.fleet_id for item in report.dispatches], [2, 3])
        self.assertIsNone(report.paused_reason)

    def test_active_fleet_with_verified_home_port_does_not_block_other_fleet(self):
        supervisor, _, _, controller = self.make_supervisor(
            {2: FleetPhase.EXPEDITION_ACTIVE, 3: FleetPhase.READY},
            home_verified={2: True},
        )
        report = supervisor.tick()
        self.assertEqual(controller.calls, [3])
        self.assertEqual(report.dispatches[0].fleet_id, 3)

    def test_pending_settlement_pauses_all_dispatches(self):
        for phase in (
            FleetPhase.RETURNED_PENDING_UI,
            FleetPhase.RETURN_RESULT,
            FleetPhase.BONUS_ITEMS,
        ):
            with self.subTest(phase=phase):
                supervisor, _, _, controller = self.make_supervisor({
                    2: phase,
                    3: FleetPhase.READY,
                })
                report = supervisor.tick()
                self.assertEqual(controller.calls, [])
                self.assertEqual(
                    report.paused_reason,
                    "fleet_2_settlement_pending",
                )

    def test_blocked_fleet_pauses_shared_ui_dispatch_queue(self):
        supervisor, _, _, controller = self.make_supervisor({
            2: FleetPhase.BLOCKED,
            3: FleetPhase.READY,
        })
        report = supervisor.tick()
        self.assertEqual(controller.calls, [])
        self.assertEqual(report.paused_reason, "fleet_2_blocked")

    def test_dispatching_fleet_requires_intent_reconciliation_before_any_dispatch(self):
        supervisor, _, _, controller = self.make_supervisor({
            2: FleetPhase.DISPATCHING,
            3: FleetPhase.READY,
        })
        report = supervisor.tick()
        self.assertEqual(controller.calls, [])
        self.assertEqual(
            report.paused_reason,
            "fleet_2_dispatch_reconciliation_required",
        )

    def test_active_fleet_without_home_port_proof_pauses_other_fleet(self):
        supervisor, _, _, controller = self.make_supervisor(
            {2: FleetPhase.EXPEDITION_ACTIVE, 3: FleetPhase.READY},
            home_verified={2: False},
        )
        report = supervisor.tick()
        self.assertEqual(controller.calls, [])
        self.assertEqual(
            report.paused_reason,
            "fleet_2_home_port_verification_required",
        )

    def test_ui_actions_disabled_means_monitor_only(self):
        supervisor, _, monitor, controller = self.make_supervisor(
            {2: FleetPhase.READY, 3: FleetPhase.READY},
            enabled=False,
        )
        report = supervisor.tick()
        self.assertEqual(monitor.calls, 1)
        self.assertEqual(controller.calls, [])
        self.assertEqual(report.paused_reason, "ui_actions_disabled")

    def test_failed_post_dispatch_home_port_proof_stops_next_fleet(self):
        supervisor, scheduler, _, controller = self.make_supervisor(
            {
                2: FleetPhase.READY,
                3: FleetPhase.READY,
            },
            verify_home_port=False,
        )

        report = supervisor.tick()

        self.assertEqual(controller.calls, [2])
        self.assertEqual(len(report.dispatches), 1)
        self.assertEqual(
            report.paused_reason,
            "fleet_2_post_dispatch_home_port_not_verified",
        )
        self.assertFalse(scheduler.fleet(2).home_port_verified)
        self.assertEqual(scheduler.fleet(3).phase, FleetPhase.READY)

    def test_monitor_and_controller_must_share_reader_and_event_sink(self):
        scheduler = FakeScheduler({
            2: FleetPhase.READY,
            3: FleetPhase.READY,
        })
        monitor = FakeMonitor(scheduler, object(), object())
        controller = FakeController(scheduler, object(), object())

        with self.assertRaisesRegex(ValueError, "share one Poi state reader"):
            ContinuousExpeditionSupervisor(
                monitor=monitor,
                dispatch_controller=controller,
            )


    def test_recovery_pause_gate_stops_tick_before_monitor_or_dispatch(self):
        supervisor, _, monitor, controller = self.make_supervisor({
            2: FleetPhase.READY,
            3: FleetPhase.READY,
        })
        gate = RecoveryPauseGate()
        gate.pause(classify_recovery_signals(observer_status="cdp_connection_closed"))
        supervisor.recovery_gate = gate

        report = supervisor.tick()

        self.assertEqual(monitor.calls, 0)
        self.assertEqual(controller.calls, [])
        self.assertEqual(report.dispatches, ())
        self.assertTrue(report.paused_reason.startswith("recovery_paused:cdp_unavailable:"))


if __name__ == "__main__":
    unittest.main()
