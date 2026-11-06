"""P0.2: one global drain, every pending fleet, nothing blocked by a first match.

The doubles here are deliberately thin: the point of these tests is the
*orchestration* decision, so the scheduler, the drain and the three controllers
record who was called and in what order. The real settlement controller is
exercised separately in ``test_settlement_global_drain_barrier.py``.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from kancollebot.expeditions.continuous_scheduler import FleetPhase
from kancollebot.expeditions.continuous_supervisor import (
    ContinuousExpeditionSupervisor,
    FleetPendingAction,
)
from kancollebot.expeditions.settlement_drain import (
    DrainErrorType,
    DrainPageState,
    DrainResult,
    SettlementDrainRequest,
)


def request() -> SettlementDrainRequest:
    return SettlementDrainRequest(
        max_actions=6,
        timeout_seconds=30.0,
        action_interval_seconds=1.5,
        stable_frame_count=2,
    )


def home_result(**overrides) -> DrainResult:
    fields = {
        "reached_home": True,
        "completed_pages": 2,
        "actions_performed": 2,
        "final_page": DrainPageState.HOME_PORT,
        "error_encountered": None,
        "requires_manual_intervention": False,
    }
    fields.update(overrides)
    return DrainResult(**fields)


def stuck_result() -> DrainResult:
    return DrainResult(
        reached_home=False,
        completed_pages=1,
        actions_performed=2,
        final_page=DrainPageState.UNKNOWN,
        error_encountered=DrainErrorType.UI_STUCK,
        requires_manual_intervention=True,
    )


class FakeScheduler:
    def __init__(self, phases, fleet_ids=(2, 3, 4), home_verified=None):
        self.dispatch_fleet_ids = tuple(fleet_ids)
        self.states = {
            fleet_id: SimpleNamespace(
                fleet_id=fleet_id,
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

    def dispatch_one_ready(self, fleet_id, *, dispatch_id=None):
        self.calls.append(fleet_id)
        state = self.scheduler.states[fleet_id]
        state.phase = FleetPhase.EXPEDITION_ACTIVE
        state.home_port_verified = True
        return SimpleNamespace(fleet_id=fleet_id)


class FakeSettlementController:
    def __init__(self, scheduler, state_reader, event_sink, *, enabled=True):
        self.scheduler = scheduler
        self.state_reader = state_reader
        self.event_sink = event_sink
        self.allow_ui_actions = enabled
        self.calls = []
        self.global_flags = []

    def settle_one_returned(self, fleet_id, *, pages_drained_globally=False):
        self.calls.append(fleet_id)
        self.global_flags.append(pages_drained_globally)
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


class FakeDrainSession:
    def __init__(self, result=None):
        self.result = result if result is not None else home_result()
        self.requests = []

    def drain(self, drain_request):
        self.requests.append(drain_request)
        return self.result


class GlobalDrainWiringTests(unittest.TestCase):
    def make(
        self,
        phases,
        *,
        include_settlement=True,
        settlement_enabled=True,
        include_resupply=True,
        resupply_enabled=True,
        drain=None,
        drain_request="default",
        fleet_ids=(2, 3, 4),
    ):
        scheduler = FakeScheduler(phases, fleet_ids=fleet_ids)
        events: list[tuple[str, dict]] = []
        sink = lambda name, fields: events.append((name, dict(fields)))  # noqa: E731
        reader = object()
        monitor = FakeMonitor(scheduler, reader, sink)
        dispatch = FakeDispatchController(scheduler, reader, sink)
        settlement = (
            FakeSettlementController(
                scheduler, reader, sink, enabled=settlement_enabled
            )
            if include_settlement
            else None
        )
        resupply = (
            FakeResupplyController(scheduler, sink, enabled=resupply_enabled)
            if include_resupply
            else None
        )
        kwargs = {}
        if drain is not None:
            kwargs["drain_session"] = drain
            kwargs["drain_request"] = (
                request() if drain_request == "default" else drain_request
            )
        supervisor = ContinuousExpeditionSupervisor(
            monitor=monitor,
            dispatch_controller=dispatch,
            settlement_controller=settlement,
            resupply_controller=resupply,
            **kwargs,
        )
        return SimpleNamespace(
            supervisor=supervisor,
            scheduler=scheduler,
            dispatch=dispatch,
            settlement=settlement,
            resupply=resupply,
            events=events,
            monitor=monitor,
        )

    # -- collection ------------------------------------------------------

    def test_every_pending_fleet_is_collected_not_just_the_first(self):
        env = self.make(
            {
                2: FleetPhase.RETURNED_PENDING_UI,
                3: FleetPhase.BONUS_ITEMS,
                4: FleetPhase.RESUPPLY_REQUIRED,
            }
        )

        self.assertEqual(env.supervisor.pending_settlement_fleets(), (2, 3))
        self.assertEqual(env.supervisor.pending_resupply_fleets(), (4,))
        kinds = [(a.fleet_id, a.kind) for a in env.supervisor._pending_actions()]
        self.assertEqual(
            kinds, [(2, "settlement"), (3, "settlement"), (4, "resupply")]
        )

    def test_a_pending_fleet_mixed_with_a_blocked_one_reports_in_scheduler_order(self):
        env = self.make({2: FleetPhase.RETURNED_PENDING_UI, 3: FleetPhase.BLOCKED})
        # The label is a description of the first outstanding need in scheduler
        # order; it is no longer used to pick which fleet is acted on.
        self.assertEqual(env.supervisor._pause_reason(), "fleet_2_settlement_pending")
        self.assertEqual(env.supervisor.pending_settlement_fleets(), (2,))

    def test_pending_action_reasons_are_the_documented_labels(self):
        self.assertEqual(
            FleetPendingAction(4, "BONUS_ITEMS", "settlement").pause_reason(),
            "fleet_4_settlement_pending",
        )
        self.assertEqual(
            FleetPendingAction(2, "BLOCKED", "blocked").pause_reason(),
            "fleet_2_blocked",
        )

    # -- one drain, nothing blocked --------------------------------------

    def test_one_drain_settles_both_returned_fleets_and_frees_the_ready_one(self):
        drain = FakeDrainSession()
        env = self.make(
            {
                2: FleetPhase.RETURNED_PENDING_UI,
                3: FleetPhase.RETURNED_PENDING_UI,
                4: FleetPhase.READY,
            },
            drain=drain,
        )

        result = env.supervisor.tick()

        self.assertEqual(len(drain.requests), 1, "exactly one global drain per tick")
        self.assertIs(drain.requests[0], env.supervisor.drain_request)
        self.assertEqual(env.settlement.calls, [2, 3])
        self.assertEqual(env.settlement.global_flags, [True, True])
        # Both settled fleets needed resupply, and the already-ready one was not
        # blocked behind either of them.
        self.assertEqual(env.resupply.calls, [2, 3])
        self.assertEqual(env.dispatch.calls, [2, 3, 4])
        self.assertIsNone(result.paused_reason)
        self.assertIs(result.drain, drain.result)
        self.assertEqual(len(result.settlements), 2)

    def test_a_drain_that_stops_early_clicks_nothing_else(self):
        drain = FakeDrainSession(stuck_result())
        env = self.make(
            {
                2: FleetPhase.RETURNED_PENDING_UI,
                3: FleetPhase.RETURNED_PENDING_UI,
                4: FleetPhase.READY,
            },
            drain=drain,
        )

        result = env.supervisor.tick()

        self.assertEqual(env.settlement.calls, [])
        self.assertEqual(env.resupply.calls, [])
        self.assertEqual(env.dispatch.calls, [])
        self.assertEqual(result.paused_reason, "settlement_drain_stopped:ui_stuck")
        self.assertEqual(result.settlements, ())
        self.assertTrue(drain.result.requires_manual_intervention)

    def test_the_drain_is_summarised_in_the_log(self):
        drain = FakeDrainSession()
        env = self.make(
            {2: FleetPhase.RETURNED_PENDING_UI, 3: FleetPhase.READY}, drain=drain
        )

        env.supervisor.tick()

        rows = [fields for name, fields in env.events if name == "supervisor_settlement_drain"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pending_fleets"], [2])
        self.assertTrue(rows[0]["reached_home"])
        self.assertEqual(rows[0]["completed_pages"], 2)
        self.assertIsNone(rows[0]["error"])

    def test_without_a_usable_settlement_controller_the_drain_is_not_run(self):
        drain = FakeDrainSession()
        env = self.make(
            {2: FleetPhase.RETURNED_PENDING_UI, 3: FleetPhase.READY},
            include_settlement=False,
            drain=drain,
        )

        result = env.supervisor.tick()

        self.assertEqual(drain.requests, [])
        self.assertEqual(env.dispatch.calls, [])
        self.assertEqual(result.paused_reason, "fleet_2_settlement_pending")

    def test_a_disabled_settlement_controller_does_not_drain_either(self):
        drain = FakeDrainSession()
        env = self.make(
            {2: FleetPhase.RETURNED_PENDING_UI, 3: FleetPhase.READY},
            settlement_enabled=False,
            drain=drain,
        )

        result = env.supervisor.tick()

        self.assertEqual(drain.requests, [])
        self.assertEqual(env.dispatch.calls, [])
        self.assertEqual(result.paused_reason, "fleet_2_settlement_pending")

    def test_resupply_pending_fleets_are_all_served_before_dispatch(self):
        drain = FakeDrainSession()
        env = self.make(
            {
                2: FleetPhase.RETURNED_PENDING_UI,
                3: FleetPhase.RESUPPLY_REQUIRED,
                4: FleetPhase.READY,
            },
            drain=drain,
        )

        env.supervisor.tick()

        self.assertEqual(env.resupply.calls, [2, 3])
        self.assertEqual(env.dispatch.calls, [2, 3, 4])

    def test_resupply_without_a_controller_stops_the_tick_after_settlement(self):
        drain = FakeDrainSession()
        env = self.make(
            {2: FleetPhase.RETURNED_PENDING_UI, 3: FleetPhase.READY},
            include_resupply=False,
            drain=drain,
        )

        result = env.supervisor.tick()

        self.assertEqual(env.settlement.calls, [2])
        self.assertEqual(env.dispatch.calls, [])
        self.assertEqual(result.paused_reason, "fleet_2_resupply_pending")

    # -- configuration ---------------------------------------------------

    def test_a_drain_session_requires_an_explicit_request(self):
        scheduler = FakeScheduler({2: FleetPhase.READY})
        sink = object()
        reader = object()
        monitor = FakeMonitor(scheduler, reader, sink)
        dispatch = FakeDispatchController(scheduler, reader, sink)

        with self.assertRaisesRegex(ValueError, "drain_request must be"):
            ContinuousExpeditionSupervisor(
                monitor=monitor,
                dispatch_controller=dispatch,
                drain_session=FakeDrainSession(),
            )

    def test_a_request_without_a_session_is_refused(self):
        scheduler = FakeScheduler({2: FleetPhase.READY})
        sink = object()
        reader = object()
        monitor = FakeMonitor(scheduler, reader, sink)
        dispatch = FakeDispatchController(scheduler, reader, sink)

        with self.assertRaisesRegex(ValueError, "drain_request requires a drain_session"):
            ContinuousExpeditionSupervisor(
                monitor=monitor,
                dispatch_controller=dispatch,
                drain_request=request(),
            )

    def test_a_session_without_a_drain_method_is_refused(self):
        scheduler = FakeScheduler({2: FleetPhase.READY})
        sink = object()
        reader = object()
        monitor = FakeMonitor(scheduler, reader, sink)
        dispatch = FakeDispatchController(scheduler, reader, sink)

        with self.assertRaisesRegex(ValueError, "callable drain"):
            ContinuousExpeditionSupervisor(
                monitor=monitor,
                dispatch_controller=dispatch,
                drain_session=object(),
                drain_request=request(),
            )

    # -- the legacy path is untouched ------------------------------------

    def test_without_a_drain_session_two_returns_still_wait_their_turn(self):
        env = self.make(
            {
                2: FleetPhase.RETURNED_PENDING_UI,
                3: FleetPhase.RETURNED_PENDING_UI,
                4: FleetPhase.READY,
            }
        )

        result = env.supervisor.tick()

        # The per-fleet barrier is still in force: one settlement per tick, and
        # the second return is reported rather than guessed at. The tick does
        # still resupply the fleet it settled, then stops on the second return.
        self.assertEqual(env.settlement.calls, [2])
        self.assertEqual(env.settlement.global_flags, [False])
        self.assertEqual(env.resupply.calls, [2])
        self.assertEqual(env.dispatch.calls, [])
        self.assertEqual(result.paused_reason, "fleet_3_settlement_pending")


if __name__ == "__main__":
    unittest.main()
