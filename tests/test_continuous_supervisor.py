
import unittest
from types import SimpleNamespace

from kancollebot.expeditions.continuous_supervisor import ContinuousExpeditionSupervisor
from kancollebot.expeditions.continuous_scheduler import FleetPhase


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

    def confirm_return_result(self, fleet_id, *, ui_state):
        if self.states[fleet_id].phase is not FleetPhase.RETURNED_PENDING_UI or ui_state != "RETURN_RESULT":
            raise AssertionError("invalid RETURN_RESULT transition")
        self.states[fleet_id].phase = FleetPhase.RETURN_RESULT

    def confirm_bonus_items(self, fleet_id, *, ui_state):
        if self.states[fleet_id].phase is not FleetPhase.RETURN_RESULT or ui_state != "BONUS_ITEMS":
            raise AssertionError("invalid BONUS_ITEMS transition")
        self.states[fleet_id].phase = FleetPhase.BONUS_ITEMS


class FakeMonitor:
    def __init__(self, scheduler, state_reader, event_sink):
        self.scheduler = scheduler
        self.state_reader = state_reader
        self.event_sink = event_sink
        self.calls = 0

    def tick(self):
        self.calls += 1
        return SimpleNamespace(fleets=())


class FakeSettlementObserver:
    def __init__(self, states):
        self.states = list(states)
        self.calls = []

    def observe(self, fleet_id, expected_state):
        self.calls.append((fleet_id, expected_state))
        observed = self.states.pop(0)
        if observed != expected_state:
            raise RuntimeError("settlement state mismatch")
        return SimpleNamespace(
            fleet_id=fleet_id,
            state=observed,
            consecutive_states=(observed, observed),
            evidence="two-frame-test",
        )


class FakeController:
    def __init__(
        self,
        scheduler,
        state_reader,
        event_sink,
        *,
        enabled=True,
        verify_home_port=True,
        returned_fleets=(),
    ):
        self.scheduler = scheduler
        self.state_reader = state_reader
        self.event_sink = event_sink
        self.allow_ui_actions = enabled
        self.verify_home_port = verify_home_port
        self.returned_fleets = set(returned_fleets)
        self.return_calls = []
        self.calls = []

    def wait_for_return(self, fleet_id):
        self.return_calls.append(fleet_id)
        if fleet_id in self.returned_fleets:
            self.scheduler.states[fleet_id].phase = FleetPhase.RETURNED_PENDING_UI
            return SimpleNamespace(mission_status=2)
        return None

    def dispatch_one_ready(self, fleet_id):
        self.calls.append(fleet_id)
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
        returned_fleets=(),
        settlement_observer=None,
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
            returned_fleets=returned_fleets,
        )
        return ContinuousExpeditionSupervisor(
            monitor=monitor,
            dispatch_controller=controller,
            settlement_observer=settlement_observer,
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

    def test_due_return_is_observed_before_dispatching_another_fleet(self):
        supervisor, scheduler, _, controller = self.make_supervisor(
            {2: FleetPhase.EXPEDITION_ACTIVE, 3: FleetPhase.READY},
            home_verified={2: True},
            returned_fleets={2},
        )

        report = supervisor.tick()

        self.assertEqual(controller.return_calls, [2])
        self.assertEqual(controller.calls, [])
        self.assertEqual(scheduler.fleet(2).phase, FleetPhase.RETURNED_PENDING_UI)
        self.assertEqual(report.paused_reason, "fleet_2_settlement_pending")

    def test_active_fleet_without_home_port_proof_is_not_return_observed(self):
        supervisor, _, _, controller = self.make_supervisor(
            {2: FleetPhase.EXPEDITION_ACTIVE, 3: FleetPhase.READY},
            home_verified={2: False},
        )

        report = supervisor.tick()

        self.assertEqual(controller.return_calls, [])
        self.assertEqual(controller.calls, [])
        self.assertEqual(
            report.paused_reason,
            "fleet_2_home_port_verification_required",
        )

    def test_returned_pending_ui_advances_to_return_result_from_two_frame_evidence(self):
        observer = FakeSettlementObserver(["RETURN_RESULT"])
        supervisor, scheduler, _, controller = self.make_supervisor(
            {2: FleetPhase.RETURNED_PENDING_UI, 3: FleetPhase.READY},
            settlement_observer=observer,
        )
        report = supervisor.tick()
        self.assertEqual(observer.calls, [(2, "RETURN_RESULT")])
        self.assertEqual(scheduler.fleet(2).phase, FleetPhase.RETURN_RESULT)
        self.assertEqual(controller.calls, [])
        self.assertEqual(report.paused_reason, "fleet_2_settlement_pending")

    def test_return_result_advances_to_bonus_items_from_two_frame_evidence(self):
        observer = FakeSettlementObserver(["BONUS_ITEMS"])
        supervisor, scheduler, _, controller = self.make_supervisor(
            {2: FleetPhase.RETURN_RESULT, 3: FleetPhase.READY},
            settlement_observer=observer,
        )
        report = supervisor.tick()
        self.assertEqual(observer.calls, [(2, "BONUS_ITEMS")])
        self.assertEqual(scheduler.fleet(2).phase, FleetPhase.BONUS_ITEMS)
        self.assertEqual(controller.calls, [])
        self.assertEqual(report.paused_reason, "fleet_2_settlement_pending")

    def test_multiple_pending_settlements_are_not_attributed_to_one_screen(self):
        observer = FakeSettlementObserver(["RETURN_RESULT"])
        supervisor, scheduler, _, controller = self.make_supervisor(
            {
                2: FleetPhase.RETURNED_PENDING_UI,
                3: FleetPhase.RETURN_RESULT,
            },
            settlement_observer=observer,
        )
        report = supervisor.tick()
        self.assertEqual(observer.calls, [])
        self.assertEqual(scheduler.fleet(2).phase, FleetPhase.RETURNED_PENDING_UI)
        self.assertEqual(scheduler.fleet(3).phase, FleetPhase.RETURN_RESULT)
        self.assertEqual(controller.calls, [])
        self.assertEqual(report.paused_reason, "fleet_2_settlement_pending")

    def test_settlement_observation_mismatch_stops_tick_without_dispatch(self):
        observer = FakeSettlementObserver(["BONUS_ITEMS"])
        supervisor, scheduler, _, controller = self.make_supervisor(
            {2: FleetPhase.RETURNED_PENDING_UI, 3: FleetPhase.READY},
            settlement_observer=observer,
        )
        with self.assertRaisesRegex(RuntimeError, "settlement state mismatch"):
            supervisor.tick()
        self.assertEqual(scheduler.fleet(2).phase, FleetPhase.RETURNED_PENDING_UI)
        self.assertEqual(controller.calls, [])

    def test_bonus_items_is_not_advanced_without_reward_claim_evidence(self):
        observer = FakeSettlementObserver(["HOME_PORT"])
        supervisor, scheduler, _, controller = self.make_supervisor(
            {2: FleetPhase.BONUS_ITEMS, 3: FleetPhase.READY},
            settlement_observer=observer,
        )
        report = supervisor.tick()
        self.assertEqual(observer.calls, [])
        self.assertEqual(scheduler.fleet(2).phase, FleetPhase.BONUS_ITEMS)
        self.assertEqual(controller.calls, [])
        self.assertEqual(report.paused_reason, "fleet_2_settlement_pending")

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


if __name__ == "__main__":
    unittest.main()
