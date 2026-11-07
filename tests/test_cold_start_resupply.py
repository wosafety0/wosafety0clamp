# -*- coding: utf-8 -*-
"""Cold start: an idle, under-supplied fleet must be supplied, then dispatched.

Real machine 2026-11-07: Fleet 2 sat in IDLE with ``Insufficient readiness:
fuel.`` and the loop reported ``fleets_2_not_ready`` every tick, dispatching
nothing. Nothing in the loop supplied an IDLE fleet, so the fleet could never
become dispatchable -- the run could only ever pause.

Core logic Step 1 requires the opposite: "If not, perform the normal in-game
resupply action, read the state again, and confirm full supply before
continuing."

These tests wire the **real** monitor into the **real** supervisor, because the
defect lived exactly in the seam between them: the monitor had to move the fleet
into RESUPPLY_REQUIRED for ``_pending_kind`` to see outstanding work. A fake
monitor would have hidden it.
"""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from kancollebot.expeditions.continuous_monitor import ContinuousExpeditionMonitor
from kancollebot.expeditions.continuous_scheduler import (
    ContinuousExpeditionScheduler,
    FleetPhase,
    MissionTuple,
)
from kancollebot.expeditions.continuous_supervisor import ContinuousExpeditionSupervisor
from kancollebot.expeditions.dispatch_recovery import SqliteDispatchIntentStore
from kancollebot.expeditions.fleet_state_store import SqliteFleetStateStore
from kancollebot.expeditions.mission_eligibility import MissionCompositionRequirement
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot
from kancollebot.core.observed_state import ObservedState

BASE = datetime(2026, 11, 2, 0, 0, 0, tzinfo=timezone.utc)
REQUIREMENTS = {
    2: MissionCompositionRequirement(1, 2, 1),
    3: MissionCompositionRequirement(3, 3, 3),
}


def make_snapshot(fleet_id, *, fuel_ready=True, ammo_ready=True):
    ship_ids = (1, 2) if fleet_id == 2 else (3, 4, 5)
    levels = tuple(3 if index == 0 else 1 for index, _ in enumerate(ship_ids))
    return ExpeditionFleetSnapshot(
        fleet_id=fleet_id,
        ship_ids=ship_ids,
        mission_id=None,
        return_at=None,
        fuel_ready=fuel_ready,
        ammo_ready=ammo_ready,
        damaged_ship_ids=(),
        mission_status=0,
        reserved=0,
        ship_levels=levels,
        ship_types=tuple(1 for _ in ship_ids),
    )


def observed(fleets):
    return ObservedState(
        tuple(fleets), BASE, "poi-redux:info.fleets+info.ships",
        source_freshness_verified=True,
    )


class ListingReader:
    def __init__(self, state):
        self.state = state
        self.read_count = 0

    def read_fleets(self):
        self.read_count += 1
        return self.state


class RecordingResupply:
    """Confirms supply through the real scheduler invariant, not by hand."""

    def __init__(self, scheduler, state_reader, event_sink, *, allow_ui_actions=True):
        self.scheduler = scheduler
        self.state_reader = state_reader
        self.event_sink = event_sink
        self.mission_requirements = REQUIREMENTS
        self.allow_ui_actions = allow_ui_actions
        self.calls = []

    def resupply_one_needed(self, fleet_id):
        self.calls.append(fleet_id)
        self.scheduler.confirm_resupply(fleet_id, fully_supplied=True)
        return SimpleNamespace(
            fleet_id=fleet_id, resupplied=True, already_supplied=False
        )


class RecordingDispatch:
    """Walks a fleet through the real dispatch transitions."""

    def __init__(self, scheduler, state_reader, event_sink, *, allow_ui_actions=True):
        self.scheduler = scheduler
        self.state_reader = state_reader
        self.event_sink = event_sink
        self.mission_requirements = REQUIREMENTS
        self.allow_ui_actions = allow_ui_actions
        self.calls = []

    def dispatch_one_ready(self, fleet_id, *, dispatch_id=None):
        self.calls.append(fleet_id)
        mission = MissionTuple(
            mission_status=1,
            mission_id=1,
            final_time_ms=int((BASE + timedelta(minutes=20)).timestamp() * 1000),
            reserved=0,
            ship_ids=(1, 2) if fleet_id == 2 else (3, 4, 5),
        )
        self.scheduler.begin_dispatch(fleet_id)
        self.scheduler.confirm_dispatch(fleet_id, mission)
        self.scheduler.confirm_home_port_after_dispatch(
            fleet_id, ui_state="HOME_PORT", observed_mission=mission
        )
        return SimpleNamespace(fleet_id=fleet_id, dispatched=True)


class ColdStartResupplyTests(unittest.TestCase):
    def make_loop(self, state, *, resupply_enabled=True):
        directory = tempfile.mkdtemp()
        db = Path(directory) / "scheduler.sqlite3"
        scheduler = ContinuousExpeditionScheduler(state_store=SqliteFleetStateStore(db))
        SqliteDispatchIntentStore(db)
        reader = ListingReader(state)
        events = []

        def sink(name, fields):
            events.append((name, dict(fields)))

        monitor = ContinuousExpeditionMonitor(
            scheduler=scheduler,
            state_reader=reader,
            mission_requirements=REQUIREMENTS,
            event_sink=sink,
            max_state_age=timedelta(seconds=30),
            clock=lambda: BASE + timedelta(seconds=1),
        )
        resupply = RecordingResupply(
            scheduler, reader, sink, allow_ui_actions=resupply_enabled
        )
        dispatch = RecordingDispatch(scheduler, reader, sink)
        supervisor = ContinuousExpeditionSupervisor(
            monitor=monitor,
            dispatch_controller=dispatch,
            resupply_controller=resupply,
        )
        return supervisor, scheduler, resupply, dispatch, events

    def test_under_supplied_idle_fleet_is_supplied_then_dispatched(self):
        supervisor, scheduler, resupply, dispatch, events = self.make_loop(
            observed([
                make_snapshot(2, fuel_ready=False),
                make_snapshot(3),
            ])
        )

        report = supervisor.tick()

        # The supply the fleet needs actually happened, and dispatch followed it
        # in the same tick -- that is the whole point of Step 1.
        self.assertEqual(resupply.calls, [2])
        self.assertIn(2, dispatch.calls)
        self.assertEqual(scheduler.fleet(2).phase, FleetPhase.EXPEDITION_ACTIVE)
        self.assertIsNone(report.paused_reason)

    def test_monitor_states_the_real_cause_instead_of_a_bare_not_ready(self):
        supervisor, _, _, _, events = self.make_loop(
            observed([make_snapshot(2, fuel_ready=False), make_snapshot(3)])
        )

        report = supervisor.tick()

        rows = [
            row for row in supervisor._fleet_decisions(report.monitor_result)
            if row["fleet_id"] == 2
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "resupply_required")
        # The label alone ("not ready") did not say which precondition failed;
        # "fuel" is what tells an operator what to look at.
        self.assertIn("fuel", rows[0]["reason"])

    def test_missing_supply_capability_pauses_instead_of_dispatching(self):
        """A loop that cannot supply must stop, never dispatch an unsupplied fleet."""
        supervisor, scheduler, resupply, dispatch, _ = self.make_loop(
            observed([make_snapshot(2, fuel_ready=False), make_snapshot(3)]),
            resupply_enabled=False,
        )

        report = supervisor.tick()

        self.assertEqual(resupply.calls, [])
        self.assertEqual(dispatch.calls, [])
        self.assertEqual(scheduler.fleet(2).phase, FleetPhase.RESUPPLY_REQUIRED)
        self.assertEqual(report.paused_reason, "fleet_2_resupply_pending")

    def test_fully_supplied_idle_fleet_needs_no_supply_call(self):
        supervisor, scheduler, resupply, dispatch, _ = self.make_loop(
            observed([make_snapshot(2), make_snapshot(3)])
        )

        supervisor.tick()

        self.assertEqual(resupply.calls, [])
        self.assertEqual(sorted(dispatch.calls), [2, 3])


if __name__ == "__main__":
    unittest.main()
