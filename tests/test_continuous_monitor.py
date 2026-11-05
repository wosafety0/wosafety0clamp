from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from kancollebot.core.observed_state import ObservedState
from kancollebot.expeditions.continuous_monitor import (
    ContinuousExpeditionMonitor,
    ContinuousMonitorError,
)
from kancollebot.expeditions.continuous_scheduler import (
    ContinuousExpeditionScheduler,
    FleetPhase,
    MissionTuple,
)
from kancollebot.expeditions.dispatch_recovery import SqliteDispatchIntentStore
from kancollebot.expeditions.fleet_state_store import SqliteFleetStateStore
from kancollebot.expeditions.mission_eligibility import MissionCompositionRequirement
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot


BASE = datetime(2026, 11, 2, 0, 0, 0, tzinfo=timezone.utc)
REQUIREMENTS = {
    2: MissionCompositionRequirement(1, 2, 1),
    3: MissionCompositionRequirement(3, 3, 3),
}


def make_snapshot(
    fleet_id,
    *,
    status=0,
    mission_id=None,
    return_at=None,
    ship_ids=None,
    levels=None,
    fuel_ready=True,
    ammo_ready=True,
    reserved=0,
):
    if ship_ids is None:
        ship_ids = (1, 2) if fleet_id == 2 else (3, 4, 5)
    if levels is None:
        levels = tuple(3 if index == 0 else 1 for index, _ in enumerate(ship_ids))
    return ExpeditionFleetSnapshot(
        fleet_id=fleet_id,
        ship_ids=tuple(ship_ids),
        mission_id=mission_id,
        return_at=return_at,
        fuel_ready=fuel_ready,
        ammo_ready=ammo_ready,
        damaged_ship_ids=(),
        mission_status=status,
        reserved=reserved,
        ship_levels=tuple(levels),
        ship_types=tuple(1 for _ in ship_ids),
    )


def observed(fleets, *, at=BASE, fresh=True, source="poi-redux:info.fleets+info.ships"):
    return ObservedState(tuple(fleets), at, source, source_freshness_verified=fresh)


class FakeReader:
    def __init__(self, state):
        self.state = state
        self.read_count = 0

    def read_fleets(self):
        self.read_count += 1
        return self.state


class AttributingReader(FakeReader):
    """A reader that records which caller asked for a fresh observation."""

    def __init__(self, state, refreshed_state=None):
        super().__init__(state)
        self.refreshed_state = refreshed_state
        self.origins = []

    def ensure_fresh(self, *, origin):
        self.origins.append(origin)
        self.read_count += 1
        if self.refreshed_state is not None:
            self.state = self.refreshed_state
        return self.state


class ContinuousExpeditionMonitorTests(unittest.TestCase):
    def make_monitor(self, directory, state, *, clock=None, events=None, reader=None):
        db = Path(directory) / "scheduler.sqlite3"
        scheduler = ContinuousExpeditionScheduler(state_store=SqliteFleetStateStore(db))
        SqliteDispatchIntentStore(db)
        events = events if events is not None else []
        reader = reader if reader is not None else FakeReader(state)
        monitor = ContinuousExpeditionMonitor(
            scheduler=scheduler,
            state_reader=reader,
            mission_requirements=REQUIREMENTS,
            event_sink=lambda name, fields: events.append((name, dict(fields))),
            max_state_age=timedelta(seconds=30),
            clock=clock or (lambda: BASE + timedelta(seconds=1)),
        )
        return monitor, scheduler, reader, events

    @staticmethod
    def active_tuple(fleet_id=2, mission_id=1):
        ships = (1, 2) if fleet_id == 2 else (3, 4, 5)
        return MissionTuple(
            mission_status=1,
            mission_id=mission_id,
            final_time_ms=int((BASE + timedelta(minutes=20)).timestamp() * 1000),
            reserved=0,
            ship_ids=ships,
        )

    def set_active(self, scheduler, fleet_id=2, mission_id=1, *, home_verified=True, due_in=None):
        scheduler.mark_ready(fleet_id)
        scheduler.begin_dispatch(fleet_id)
        mission = self.active_tuple(fleet_id, mission_id)
        if due_in is not None:
            ships = (1, 2) if fleet_id == 2 else (3, 4, 5)
            mission = MissionTuple(
                mission_status=1,
                mission_id=mission_id,
                final_time_ms=int((BASE + due_in).timestamp() * 1000),
                reserved=0,
                ship_ids=ships,
            )
        scheduler.confirm_dispatch(fleet_id, mission)
        if home_verified:
            scheduler.confirm_home_port_after_dispatch(
                fleet_id, ui_state="HOME_PORT", observed_mission=mission
            )
        return mission

    def test_one_fresh_read_reconciles_both_fleets_and_marks_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            state = observed([make_snapshot(2), make_snapshot(3)])
            monitor, scheduler, reader, events = self.make_monitor(directory, state)
            report = monitor.tick()
            self.assertEqual(reader.read_count, 1)
            self.assertEqual(report.fleets[0].current_phase, FleetPhase.READY)
            self.assertEqual(report.fleets[1].current_phase, FleetPhase.READY)
            self.assertEqual(len(events), 2)

    def test_a_tick_names_itself_only_when_it_asks_for_a_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshots = [make_snapshot(2), make_snapshot(3)]
            reader = AttributingReader(
                observed(snapshots, fresh=False), refreshed_state=observed(snapshots)
            )
            monitor, _, _, _ = self.make_monitor(
                directory, reader.state, reader=reader
            )

            monitor.tick()

            self.assertEqual(reader.origins, ["monitor_tick"])

    def test_a_tick_with_nothing_pending_refuses_to_navigate(self):
        """Every fleet is out with its return far away: no navigation is worth it."""
        with tempfile.TemporaryDirectory() as directory:
            far = BASE + timedelta(minutes=20)
            snapshots = [
                make_snapshot(2, status=1, mission_id=1, return_at=far),
                make_snapshot(3, status=1, mission_id=3, return_at=far),
            ]
            reader = AttributingReader(observed(snapshots, fresh=False))
            monitor, scheduler, _, _ = self.make_monitor(
                directory, reader.state, reader=reader
            )
            self.set_active(scheduler, 2, 1)
            self.set_active(scheduler, 3, 3)

            with self.assertRaisesRegex(ContinuousMonitorError, "will not navigate"):
                monitor.tick()

            self.assertEqual(reader.origins, [])
            self.assertEqual(reader.read_count, 1)

    def test_a_return_due_within_the_lead_time_still_asks_for_a_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshots = [
                make_snapshot(2, status=1, mission_id=1, return_at=BASE + timedelta(seconds=30)),
                make_snapshot(3, status=1, mission_id=3, return_at=BASE + timedelta(minutes=20)),
            ]
            reader = AttributingReader(
                observed(snapshots, fresh=False), refreshed_state=observed(snapshots)
            )
            monitor, scheduler, _, events = self.make_monitor(
                directory, reader.state, reader=reader
            )
            self.set_active(scheduler, 2, 1, due_in=timedelta(seconds=30))
            self.set_active(scheduler, 3, 3, due_in=timedelta(minutes=20))

            monitor.tick()

            self.assertEqual(reader.origins, ["monitor_tick"])
            reasons = [
                fields["reason"] for name, fields in events
                if name == "monitor_refresh_requested"
            ]
            self.assertEqual(len(reasons), 1)
            self.assertIn("due to return", reasons[0])

    def test_a_refresh_that_still_lacks_a_witness_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshots = [make_snapshot(2), make_snapshot(3)]
            reader = AttributingReader(observed(snapshots, fresh=False))
            monitor, _, _, _ = self.make_monitor(
                directory, reader.state, reader=reader
            )

            with self.assertRaisesRegex(ContinuousMonitorError, "not trusted"):
                monitor.tick()

            self.assertEqual(reader.origins, ["monitor_tick"])

    def test_an_expired_observation_still_fails_closed(self):
        """Aging out is not a reason to trust a state; refresh first, then judge."""
        with tempfile.TemporaryDirectory() as directory:
            stale_at = BASE - timedelta(minutes=5)
            monitor, scheduler, _, _ = self.make_monitor(
                directory,
                observed([make_snapshot(2), make_snapshot(3)], at=stale_at),
            )

            with self.assertRaisesRegex(ContinuousMonitorError, "not trusted"):
                monitor.tick()

            self.assertEqual(scheduler.fleet(2).phase, FleetPhase.IDLE)
            self.assertEqual(scheduler.fleet(3).phase, FleetPhase.IDLE)

    def test_report_carries_source_freshness_verified_when_trusted(self):
        with tempfile.TemporaryDirectory() as directory:
            state = observed([make_snapshot(2), make_snapshot(3)], fresh=True)
            monitor, scheduler, reader, events = self.make_monitor(directory, state)
            report = monitor.tick()
            self.assertIs(report.source_freshness_verified, True)

    def test_unverified_source_freshness_blocks_tick_without_report(self):
        with tempfile.TemporaryDirectory() as directory:
            state = observed([make_snapshot(2), make_snapshot(3)], fresh=False)
            monitor, scheduler, reader, events = self.make_monitor(directory, state)
            with self.assertRaises(ContinuousMonitorError):
                monitor.tick()
            self.assertEqual(reader.read_count, 1)

    def test_active_status_two_confirms_return_only_for_matching_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor, scheduler, _, _ = self.make_monitor(
                directory,
                observed([
                    make_snapshot(2, status=2, mission_id=1, return_at=BASE + timedelta(minutes=20)),
                    make_snapshot(3),
                ]),
            )
            self.set_active(scheduler)
            report = monitor.tick()
            self.assertEqual(report.fleets[0].action, "return_detected")
            self.assertEqual(scheduler.fleet(2).phase, FleetPhase.RETURNED_PENDING_UI)

    def test_status_two_before_home_port_verification_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor, scheduler, _, _ = self.make_monitor(
                directory,
                observed([
                    make_snapshot(2, status=2, mission_id=1, return_at=BASE + timedelta(minutes=20)),
                    make_snapshot(3),
                ]),
            )
            self.set_active(scheduler, home_verified=False)
            report = monitor.tick()
            self.assertEqual(report.fleets[0].current_phase, FleetPhase.BLOCKED)
            self.assertIn("Home Port verification", report.fleets[0].reason)

    def test_active_status_one_with_same_tuple_remains_active(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor, scheduler, _, _ = self.make_monitor(
                directory,
                observed([
                    make_snapshot(2, status=1, mission_id=1, return_at=BASE + timedelta(minutes=20)),
                    make_snapshot(3),
                ]),
            )
            self.set_active(scheduler)
            report = monitor.tick()
            self.assertEqual(report.fleets[0].action, "active_confirmed")
            self.assertEqual(scheduler.fleet(2).phase, FleetPhase.EXPEDITION_ACTIVE)

    def test_unchanged_active_mission_does_not_spam_persistent_event_log(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor, scheduler, _, events = self.make_monitor(
                directory,
                observed([
                    make_snapshot(2, status=1, mission_id=1, return_at=BASE + timedelta(minutes=20)),
                    make_snapshot(3),
                ]),
            )
            self.set_active(scheduler)
            monitor.tick()
            self.assertEqual([item for item in events if item[1]["fleet_id"] == 2], [])
    def test_timer_expiry_without_status_two_never_confirms_return(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor, scheduler, _, _ = self.make_monitor(
                directory,
                observed([
                    make_snapshot(2, status=1, mission_id=1, return_at=BASE + timedelta(minutes=20)),
                    make_snapshot(3),
                ], at=BASE + timedelta(minutes=20, seconds=1)),
                clock=lambda: BASE + timedelta(minutes=20, seconds=2),
            )
            self.set_active(scheduler)
            report = monitor.tick()
            self.assertEqual(report.fleets[0].current_phase, FleetPhase.EXPEDITION_ACTIVE)
            self.assertNotEqual(report.fleets[0].action, "return_detected")

    def test_mismatched_active_tuple_blocks_fleet(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor, scheduler, _, _ = self.make_monitor(
                directory,
                observed([
                    make_snapshot(2, status=1, mission_id=3, return_at=BASE + timedelta(minutes=20)),
                    make_snapshot(3),
                ]),
            )
            self.set_active(scheduler)
            report = monitor.tick()
            self.assertEqual(report.fleets[0].current_phase, FleetPhase.BLOCKED)

    def test_stale_or_unverified_observation_changes_no_fleet_state(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor, scheduler, _, _ = self.make_monitor(
                directory, observed([make_snapshot(2), make_snapshot(3)], fresh=False)
            )
            with self.assertRaisesRegex(ContinuousMonitorError, "not trusted"):
                monitor.tick()
            self.assertEqual(scheduler.fleet(2).phase, FleetPhase.IDLE)
            self.assertEqual(scheduler.fleet(3).phase, FleetPhase.IDLE)

    def test_missing_dispatchable_fleet_record_fails_before_any_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor, scheduler, _, _ = self.make_monitor(
                directory, observed([make_snapshot(2)])
            )
            with self.assertRaisesRegex(ContinuousMonitorError, "omitted dispatchable"):
                monitor.tick()
            self.assertEqual(scheduler.fleet(2).phase, FleetPhase.IDLE)

    def test_idle_under_supplied_fleet_remains_idle(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor, scheduler, _, _ = self.make_monitor(
                directory,
                observed([make_snapshot(2, fuel_ready=False), make_snapshot(3)]),
            )
            report = monitor.tick()
            self.assertEqual(report.fleets[0].action, "not_ready")
            self.assertEqual(scheduler.fleet(2).phase, FleetPhase.IDLE)
            self.assertEqual(scheduler.fleet(3).phase, FleetPhase.READY)

    def test_resupply_required_moves_to_ready_only_after_full_supply_is_observed(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor, scheduler, _, _ = self.make_monitor(
                directory, observed([make_snapshot(2), make_snapshot(3)])
            )
            mission = self.set_active(scheduler)
            scheduler.confirm_returned(
                2,
                MissionTuple(
                    mission_status=2,
                    mission_id=mission.mission_id,
                    final_time_ms=mission.final_time_ms,
                    reserved=mission.reserved,
                    ship_ids=mission.ship_ids,
                ),
            )
            scheduler.confirm_return_result(2, ui_state="RETURN_RESULT")
            scheduler.confirm_bonus_items(2, ui_state="BONUS_ITEMS")
            scheduler.confirm_reward_claimed(2, claimed=True)
            report = monitor.tick()
            self.assertEqual(report.fleets[0].action, "resupply_confirmed")
            self.assertEqual(scheduler.fleet(2).phase, FleetPhase.READY)

    def test_blocked_fleet_is_never_automatically_unblocked(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor, scheduler, _, _ = self.make_monitor(
                directory, observed([make_snapshot(2), make_snapshot(3)])
            )
            scheduler.block(2, "manual reconciliation required")
            report = monitor.tick()
            self.assertEqual(report.fleets[0].action, "blocked_hold")
            self.assertEqual(scheduler.fleet(2).phase, FleetPhase.BLOCKED)

    def test_dispatching_fleet_is_left_for_intent_coordinator_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor, scheduler, _, _ = self.make_monitor(
                directory, observed([make_snapshot(2), make_snapshot(3)])
            )
            scheduler.mark_ready(2)
            scheduler.begin_dispatch(2)
            report = monitor.tick()
            self.assertEqual(report.fleets[0].action, "dispatch_reconciliation_required")
            self.assertEqual(scheduler.fleet(2).phase, FleetPhase.DISPATCHING)

    def test_returned_settlement_phase_requires_same_status_two_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor, scheduler, _, _ = self.make_monitor(
                directory,
                observed([
                    make_snapshot(2, status=2, mission_id=1, return_at=BASE + timedelta(minutes=20)),
                    make_snapshot(3),
                ]),
            )
            mission = self.set_active(scheduler)
            returned = MissionTuple(2, mission.mission_id, mission.final_time_ms, mission.reserved, mission.ship_ids)
            scheduler.confirm_returned(2, returned)
            scheduler.confirm_return_result(2, ui_state="RETURN_RESULT")
            report = monitor.tick()
            self.assertEqual(report.fleets[0].action, "settlement_pending")
            self.assertEqual(scheduler.fleet(2).phase, FleetPhase.RETURN_RESULT)


if __name__ == "__main__":
    unittest.main()