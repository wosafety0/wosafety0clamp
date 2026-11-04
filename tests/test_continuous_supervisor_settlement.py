"""Offline tests for supervisor-driven settlement orchestration."""

import unittest
from types import SimpleNamespace

from kancollebot.expeditions.continuous_scheduler import FleetPhase
from kancollebot.expeditions.continuous_supervisor import (
    ContinuousExpeditionSupervisor,
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


class FakeDispatchController:
    def __init__(self, scheduler, state_reader, event_sink, *, enabled=True):
        self.scheduler = scheduler
        self.state_reader = state_reader
        self.event_sink = event_sink
        self.allow_ui_actions = enabled
        self.calls = []

    def dispatch_one_ready(self, fleet_id):
        self.calls.append(fleet_id)
        self.scheduler.states[fleet_id].phase = FleetPhase.EXPEDITION_ACTIVE
        self.scheduler.states[fleet_id].home_port_verified = True
        return SimpleNamespace(fleet_id=fleet_id)


class FakeSettlementController:
    def __init__(self, scheduler, state_reader, event_sink, *, enabled=True):
        self.scheduler = scheduler
        self.state_reader = state_reader
        self.event_sink = event_sink
        self.allow_ui_actions = enabled
        self.calls = []

    def settle_one_returned(self, fleet_id):
        self.calls.append(fleet_id)
        self.scheduler.states[fleet_id].phase = FleetPhase.RESUPPLY_REQUIRED
        return SimpleNamespace(fleet_id=fleet_id, settled=True)


class FakeResupplyController:
    def __init__(self, scheduler, event_sink, *, enabled=True):
        self.scheduler = scheduler
        self.event_sink = event_sink
        self.allow_ui_actions = enabled
        self.calls = []

    def resupply_one_needed(self, fleet_id):
        self.calls.append(fleet_id)
        self.scheduler.states[fleet_id].phase = FleetPhase.READY
        return SimpleNamespace(fleet_id=fleet_id, resupplied=True)


class ContinuousExpeditionSupervisorSettlementTests(unittest.TestCase):
    def make_supervisor(
        self,
        phases,
        *,
        dispatch_enabled=True,
        settlement_enabled=True,
        include_settlement=True,
        include_resupply=False,
        resupply_enabled=True,
        settlement_scheduler=None,
    ):
        scheduler = FakeScheduler(phases)
        reader = object()
        event_sink = object()
        monitor = FakeMonitor(scheduler, reader, event_sink)
        dispatch = FakeDispatchController(
            scheduler, reader, event_sink, enabled=dispatch_enabled
        )
        settlement = None
        if include_settlement:
            settlement = FakeSettlementController(
                settlement_scheduler or scheduler,
                reader,
                event_sink,
                enabled=settlement_enabled,
            )
        resupply = None
        if include_resupply:
            resupply = FakeResupplyController(
                scheduler, event_sink, enabled=resupply_enabled
            )
        supervisor = ContinuousExpeditionSupervisor(
            monitor=monitor,
            dispatch_controller=dispatch,
            settlement_controller=settlement,
            resupply_controller=resupply,
        )
        return supervisor, scheduler, dispatch, settlement, resupply

    def test_settlement_phase_still_pauses_without_settlement_controller(self):
        supervisor, _, dispatch, _, _ = self.make_supervisor(
            {2: FleetPhase.RETURNED_PENDING_UI, 3: FleetPhase.READY},
            include_settlement=False,
        )
        report = supervisor.tick()
        self.assertEqual(dispatch.calls, [])
        self.assertEqual(report.paused_reason, "fleet_2_settlement_pending")
        self.assertEqual(report.settlements, ())

    def test_settlement_phase_pauses_when_settlement_not_enabled(self):
        supervisor, _, dispatch, settlement, _ = self.make_supervisor(
            {2: FleetPhase.BONUS_ITEMS, 3: FleetPhase.READY},
            settlement_enabled=False,
        )
        report = supervisor.tick()
        self.assertEqual(settlement.calls, [])
        self.assertEqual(dispatch.calls, [])
        self.assertEqual(report.paused_reason, "fleet_2_settlement_pending")
        self.assertEqual(report.settlements, ())

    def test_settlement_controller_drives_returned_fleet_in_same_tick(self):
        supervisor, scheduler, dispatch, settlement, resupply = self.make_supervisor(
            {2: FleetPhase.RETURNED_PENDING_UI, 3: FleetPhase.READY},
            include_resupply=True,
        )

        report = supervisor.tick()

        # Settlement advances fleet 2 RETURNED_PENDING_UI -> RESUPPLY_REQUIRED,
        # then resupply advances it RESUPPLY_REQUIRED -> READY, all in one tick.
        self.assertEqual(settlement.calls, [2])
        self.assertEqual(resupply.calls, [2])
        # After settle + resupply, fleet 2 returned to READY and was dispatched
        # in the same tick, so it is now EXPEDITION_ACTIVE.
        self.assertEqual(scheduler.fleet(2).phase, FleetPhase.EXPEDITION_ACTIVE)
        self.assertEqual(dispatch.calls, [2, 3])
        self.assertIsNone(report.paused_reason)
        self.assertEqual(len(report.settlements), 1)
        self.assertEqual(report.settlements[0].fleet_id, 2)
        self.assertTrue(report.settlements[0].settled)
        self.assertEqual(len(report.resupplies), 1)
        self.assertEqual(report.resupplies[0].fleet_id, 2)
        self.assertTrue(report.resupplies[0].resupplied)

    def test_settlement_resolved_tick_dispatches_nothing_when_all_settling(self):
        supervisor, scheduler, dispatch, settlement, _ = self.make_supervisor(
            {2: FleetPhase.RETURNED_PENDING_UI, 3: FleetPhase.RETURNED_PENDING_UI}
        )

        report = supervisor.tick()

        # One action per tick: fleet 2 is settled (now RESUPPLY_REQUIRED, which
        # the supervisor now recognizes as the next pending step), fleet 3 still
        # awaiting settlement. The tick pauses on fleet 2's resupply need.
        self.assertEqual(settlement.calls, [2])
        self.assertEqual(dispatch.calls, [])
        self.assertEqual(
            report.paused_reason, "fleet_2_resupply_pending"
        )
        self.assertEqual(len(report.settlements), 1)

    def test_settlement_controller_must_share_scheduler(self):
        scheduler = FakeScheduler({2: FleetPhase.READY, 3: FleetPhase.READY})
        other_scheduler = FakeScheduler({2: FleetPhase.READY, 3: FleetPhase.READY})
        reader = object()
        event_sink = object()
        monitor = FakeMonitor(scheduler, reader, event_sink)
        dispatch = FakeDispatchController(scheduler, reader, event_sink)
        settlement = FakeSettlementController(other_scheduler, reader, event_sink)

        with self.assertRaisesRegex(ValueError, "settlement controller must share one scheduler"):
            ContinuousExpeditionSupervisor(
                monitor=monitor,
                dispatch_controller=dispatch,
                settlement_controller=settlement,
            )

    def test_settlement_controller_must_share_reader_and_event_sink(self):
        scheduler = FakeScheduler({2: FleetPhase.READY, 3: FleetPhase.READY})
        reader = object()
        event_sink = object()
        monitor = FakeMonitor(scheduler, reader, event_sink)
        dispatch = FakeDispatchController(scheduler, reader, event_sink)
        settlement = FakeSettlementController(scheduler, object(), event_sink)

        with self.assertRaisesRegex(ValueError, "settlement controller must share one Poi state reader"):
            ContinuousExpeditionSupervisor(
                monitor=monitor,
                dispatch_controller=dispatch,
                settlement_controller=settlement,
            )

        settlement_other_sink = FakeSettlementController(scheduler, reader, object())
        with self.assertRaisesRegex(ValueError, "settlement controller must share one event sink"):
            ContinuousExpeditionSupervisor(
                monitor=monitor,
                dispatch_controller=dispatch,
                settlement_controller=settlement_other_sink,
            )


class ContinuousExpeditionSupervisorResupplyTests(unittest.TestCase):
    def make_supervisor(
        self,
        phases,
        *,
        dispatch_enabled=True,
        resupply_enabled=True,
        include_resupply=True,
        resupply_scheduler=None,
    ):
        scheduler = FakeScheduler(phases)
        reader = object()
        event_sink = object()
        monitor = FakeMonitor(scheduler, reader, event_sink)
        dispatch = FakeDispatchController(
            scheduler, reader, event_sink, enabled=dispatch_enabled
        )
        resupply = None
        if include_resupply:
            resupply = FakeResupplyController(
                resupply_scheduler or scheduler,
                event_sink,
                enabled=resupply_enabled,
            )
        supervisor = ContinuousExpeditionSupervisor(
            monitor=monitor,
            dispatch_controller=dispatch,
            resupply_controller=resupply,
        )
        return supervisor, scheduler, dispatch, resupply

    def test_resupply_then_dispatch_closes_loop_in_same_tick(self):
        supervisor, scheduler, dispatch, resupply = self.make_supervisor(
            {2: FleetPhase.RESUPPLY_REQUIRED, 3: FleetPhase.READY}
        )

        report = supervisor.tick()

        self.assertEqual(resupply.calls, [2])
        # Resupplied fleet 2 reached READY and was dispatched in the same tick,
        # so it is now EXPEDITION_ACTIVE.
        self.assertEqual(scheduler.fleet(2).phase, FleetPhase.EXPEDITION_ACTIVE)
        # A resupplied fleet reaches READY and is dispatched with the other
        # already-READY fleet in the same tick.
        self.assertEqual(dispatch.calls, [2, 3])
        self.assertIsNone(report.paused_reason)
        self.assertEqual(len(report.resupplies), 1)
        self.assertEqual(report.resupplies[0].fleet_id, 2)

    def test_resupply_required_pauses_without_resupply_controller(self):
        supervisor, _, dispatch, resupply = self.make_supervisor(
            {2: FleetPhase.RESUPPLY_REQUIRED, 3: FleetPhase.READY},
            include_resupply=False,
        )
        report = supervisor.tick()
        self.assertIsNone(resupply)
        # A fleet that needs resupply but has no resupply controller cannot
        # reach READY, so the loop must pause fail-closed rather than dispatch
        # around it.
        self.assertEqual(dispatch.calls, [])
        self.assertEqual(report.paused_reason, "fleet_2_resupply_pending")
        self.assertEqual(report.resupplies, ())

    def test_resupply_required_pauses_when_resupply_not_enabled(self):
        supervisor, _, dispatch, resupply = self.make_supervisor(
            {2: FleetPhase.RESUPPLY_REQUIRED, 3: FleetPhase.READY},
            resupply_enabled=False,
        )
        report = supervisor.tick()
        self.assertEqual(resupply.calls, [])
        self.assertEqual(dispatch.calls, [])
        self.assertEqual(report.paused_reason, "fleet_2_resupply_pending")
        self.assertEqual(report.resupplies, ())

    def test_resupply_controller_must_share_scheduler(self):
        scheduler = FakeScheduler({2: FleetPhase.READY, 3: FleetPhase.READY})
        other_scheduler = FakeScheduler({2: FleetPhase.READY, 3: FleetPhase.READY})
        reader = object()
        event_sink = object()
        monitor = FakeMonitor(scheduler, reader, event_sink)
        dispatch = FakeDispatchController(scheduler, reader, event_sink)
        resupply = FakeResupplyController(other_scheduler, event_sink)

        with self.assertRaisesRegex(ValueError, "resupply controller must share one scheduler"):
            ContinuousExpeditionSupervisor(
                monitor=monitor,
                dispatch_controller=dispatch,
                resupply_controller=resupply,
            )

    def test_resupply_controller_must_share_event_sink(self):
        scheduler = FakeScheduler({2: FleetPhase.READY, 3: FleetPhase.READY})
        reader = object()
        event_sink = object()
        monitor = FakeMonitor(scheduler, reader, event_sink)
        dispatch = FakeDispatchController(scheduler, reader, event_sink)
        resupply = FakeResupplyController(scheduler, object())

        with self.assertRaisesRegex(ValueError, "resupply controller must share one event sink"):
            ContinuousExpeditionSupervisor(
                monitor=monitor,
                dispatch_controller=dispatch,
                resupply_controller=resupply,
            )


if __name__ == "__main__":
    unittest.main()
