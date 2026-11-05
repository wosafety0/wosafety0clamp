"""Adopting a mission the durable scheduler never dispatched.

Live evidence 2026-11-05: Fleet 3 was on expedition 1 while the durable state
said IDLE - the run had been restarted mid-expedition. The monitor blocked with
"scheduler expects idle fleet but Poi reports a mission", which is the correct
fail-closed answer and also a dead end: without adoption, every restart while a
fleet is out bricks that fleet for the rest of the database's life.

These tests pin both halves of that sentence: blocking stays the default, and
the opt-in path only resumes on a mission identity and composition that match
what the scheduler would have dispatched itself.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from kancollebot.core.observed_state import ObservedState
from kancollebot.expeditions.continuous_monitor import ContinuousExpeditionMonitor
from kancollebot.expeditions.continuous_scheduler import (
    ContinuousExpeditionScheduler,
    FleetPhase,
    MissionTuple,
    SchedulerInvariantError,
)
from kancollebot.expeditions.fleet_state_store import SqliteFleetStateStore
from kancollebot.expeditions.mission_eligibility import MissionCompositionRequirement
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot


BASE = datetime(2026, 11, 5, 3, 0, 0, tzinfo=timezone.utc)
RETURN_AT = BASE + timedelta(minutes=15)
REQUIREMENTS = {
    2: MissionCompositionRequirement(1, 2, 1),
    3: MissionCompositionRequirement(3, 3, 3),
}


def snapshot(fleet_id, *, status=1, mission_id=None, ships=None, levels=None, reserved=0):
    if ships is None:
        ships = (1, 2) if fleet_id == 2 else (3, 4, 5)
    if levels is None:
        levels = tuple(3 if index == 0 else 1 for index, _ in enumerate(ships))
    return ExpeditionFleetSnapshot(
        fleet_id=fleet_id,
        ship_ids=tuple(ships),
        mission_id=mission_id,
        return_at=RETURN_AT if status in (1, 2) else None,
        fuel_ready=True,
        ammo_ready=True,
        damaged_ship_ids=(),
        mission_status=status,
        reserved=reserved,
        ship_levels=tuple(levels),
        ship_types=tuple(1 for _ in ships),
    )


def idle(fleet_id):
    return ExpeditionFleetSnapshot(
        fleet_id=fleet_id,
        ship_ids=(1, 2) if fleet_id == 2 else (3, 4, 5),
        mission_id=None,
        return_at=None,
        fuel_ready=True,
        ammo_ready=True,
        damaged_ship_ids=(),
        mission_status=0,
        reserved=0,
        ship_levels=(3, 1) if fleet_id == 2 else (3, 1, 1),
        ship_types=(1, 1) if fleet_id == 2 else (1, 1, 1),
    )


def observation(*fleets):
    return ObservedState(
        tuple(fleets),
        BASE,
        "poi-redux:info.fleets+info.ships",
        source_freshness_verified=True,
    )


class FakeReader:
    def __init__(self, state):
        self.state = state

    def read_fleets(self):
        return self.state


class AdoptionTestCase(unittest.TestCase):
    def make_monitor(self, directory, state, *, adopt):
        self.database = Path(directory) / "scheduler.sqlite3"
        self.state_store = SqliteFleetStateStore(self.database)
        scheduler = ContinuousExpeditionScheduler(state_store=self.state_store)
        self.events = []
        monitor = ContinuousExpeditionMonitor(
            scheduler=scheduler,
            state_reader=FakeReader(state),
            mission_requirements=REQUIREMENTS,
            event_sink=lambda name, fields: self.events.append((name, dict(fields))),
            max_state_age=timedelta(seconds=30),
            clock=lambda: BASE,
            adopt_unplanned_missions=adopt,
        )
        return monitor, scheduler

    def event_names(self):
        return [name for name, _ in self.events]


class AdoptionIsOptInTests(AdoptionTestCase):
    def test_a_fleet_that_is_out_blocks_by_default(self):
        # The monitor must never quietly absorb a mission when the operator has
        # not asked for it: an unexplained expedition is a fact to look at.
        with tempfile.TemporaryDirectory() as directory:
            monitor, scheduler = self.make_monitor(
                directory,
                observation(snapshot(2, mission_id=1), idle(3)),
                adopt=False,
            )
            report = monitor.tick()
            self.assertEqual(report.fleets[0].action, "blocked")
            self.assertEqual(scheduler.fleet(2).phase, FleetPhase.BLOCKED)
            self.assertIn(
                "scheduler expects idle fleet but Poi reports a mission",
                scheduler.fleet(2).blocked_reason,
            )
            self.assertNotIn("fleet_mission_adopted", self.event_names())

    def test_the_option_is_reported_on_the_monitor(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor, _ = self.make_monitor(directory, observation(idle(2), idle(3)), adopt=True)
            self.assertTrue(monitor.adopt_unplanned_missions)

    def test_a_non_bool_option_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "scheduler.sqlite3"
            scheduler = ContinuousExpeditionScheduler(state_store=SqliteFleetStateStore(db))
            with self.assertRaises(ValueError):
                ContinuousExpeditionMonitor(
                    scheduler=scheduler,
                    state_reader=FakeReader(observation(idle(2), idle(3))),
                    mission_requirements=REQUIREMENTS,
                    event_sink=lambda name, fields: None,
                    adopt_unplanned_missions="yes",
                )


class AdoptionResumesTheConfiguredMissionTests(AdoptionTestCase):
    def test_an_active_configured_mission_becomes_an_active_expedition(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor, scheduler = self.make_monitor(
                directory,
                observation(snapshot(2, mission_id=1), idle(3)),
                adopt=True,
            )
            report = monitor.tick()
            self.assertEqual(report.fleets[0].action, "adopted_observed_mission")
            adopted = scheduler.fleet(2)
            self.assertIs(adopted.phase, FleetPhase.EXPEDITION_ACTIVE)
            self.assertTrue(adopted.home_port_verified)
            self.assertEqual(adopted.mission.mission_id, 1)
            self.assertEqual(adopted.mission.ship_ids, (1, 2))
            self.assertIn("fleet_mission_adopted", self.event_names())

    def test_an_already_returned_mission_waits_for_the_reward_screens(self):
        # Status 2 at adoption time is the restart-just-after-return case. It
        # belongs in RETURNED_PENDING_UI, not ACTIVE: the phase invariant that
        # keeps settlement honest is exactly this distinction.
        with tempfile.TemporaryDirectory() as directory:
            monitor, scheduler = self.make_monitor(
                directory,
                observation(snapshot(2, status=2, mission_id=1), idle(3)),
                adopt=True,
            )
            report = monitor.tick()
            self.assertEqual(report.fleets[0].action, "adopted_observed_mission")
            adopted = scheduler.fleet(2)
            self.assertIs(adopted.phase, FleetPhase.RETURNED_PENDING_UI)
            self.assertEqual(adopted.mission.mission_status, 2)
            self.assertFalse(adopted.home_port_verified)

    def test_adoption_is_durable_across_a_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor, scheduler = self.make_monitor(
                directory,
                observation(snapshot(2, mission_id=1), idle(3)),
                adopt=True,
            )
            monitor.tick()
            reloaded = ContinuousExpeditionScheduler(
                state_store=SqliteFleetStateStore(self.database)
            )
            self.assertIs(reloaded.fleet(2).phase, FleetPhase.EXPEDITION_ACTIVE)
            self.assertTrue(reloaded.fleet(2).home_port_verified)

    def test_a_resupply_pending_fleet_can_also_be_adopted(self):
        # A restart between reward claim and resupply leaves RESUPPLY_REQUIRED
        # while the next expedition is already running by hand.
        with tempfile.TemporaryDirectory() as directory:
            monitor, scheduler = self.make_monitor(
                directory,
                observation(snapshot(2, mission_id=1), idle(3)),
                adopt=True,
            )
            scheduler.mark_ready(2)
            scheduler.begin_dispatch(2)
            scheduler.confirm_dispatch(
                2,
                MissionTuple(1, 1, int(RETURN_AT.timestamp() * 1000), 0, (1, 2)),
            )
            scheduler.confirm_home_port_after_dispatch(
                2,
                ui_state="HOME_PORT",
                observed_mission=MissionTuple(1, 1, int(RETURN_AT.timestamp() * 1000), 0, (1, 2)),
            )
            returned = MissionTuple(2, 1, int(RETURN_AT.timestamp() * 1000), 0, (1, 2))
            scheduler.confirm_returned(2, returned)
            scheduler.confirm_return_result(2, ui_state="RETURN_RESULT")
            scheduler.confirm_bonus_items(2, ui_state="BONUS_ITEMS")
            scheduler.confirm_reward_claimed(2, claimed=True)
            report = monitor.tick()
            self.assertEqual(report.fleets[0].action, "adopted_observed_mission")
            self.assertIs(scheduler.fleet(2).phase, FleetPhase.EXPEDITION_ACTIVE)


class AdoptionRefusesAnythingElseTests(AdoptionTestCase):
    def test_a_different_expedition_on_the_same_fleet_blocks(self):
        # Fleet 3 is configured for mission 3. Seeing mission 1 there is a real
        # mismatch, and settlement must not be pressed onto the wrong identity.
        with tempfile.TemporaryDirectory() as directory:
            monitor, scheduler = self.make_monitor(
                directory,
                observation(idle(2), snapshot(3, mission_id=1)),
                adopt=True,
            )
            report = monitor.tick()
            self.assertEqual(report.fleets[1].action, "blocked")
            self.assertIs(scheduler.fleet(3).phase, FleetPhase.BLOCKED)
            self.assertIn("fleet_mission_adoption_rejected", self.event_names())
            reasons = [fields["reason"] for name, fields in self.events if name == "fleet_mission_adoption_rejected"]
            self.assertTrue(any("configured mission 3" in reason for reason in reasons))

    def test_an_ineligible_composition_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor, scheduler = self.make_monitor(
                directory,
                observation(snapshot(2, mission_id=1, ships=(1,), levels=(3,)), idle(3)),
                adopt=True,
            )
            report = monitor.tick()
            self.assertEqual(report.fleets[0].action, "blocked")
            self.assertIs(scheduler.fleet(2).phase, FleetPhase.BLOCKED)
            reasons = [fields["reason"] for name, fields in self.events if name == "fleet_mission_adoption_rejected"]
            self.assertTrue(any("composition" in reason for reason in reasons))

    def test_a_blocked_fleet_is_never_adopted(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor, scheduler = self.make_monitor(
                directory,
                observation(snapshot(2, mission_id=1), idle(3)),
                adopt=True,
            )
            scheduler.block(2, "operator must look at this")
            report = monitor.tick()
            self.assertEqual(report.fleets[0].action, "blocked_hold")
            self.assertEqual(scheduler.fleet(2).blocked_reason, "operator must look at this")


class SchedulerAdoptionTests(unittest.TestCase):
    def make_scheduler(self, directory):
        return ContinuousExpeditionScheduler(
            state_store=SqliteFleetStateStore(Path(directory) / "scheduler.sqlite3")
        )

    @staticmethod
    def mission(status=1, mission_id=1):
        return MissionTuple(status, mission_id, int(RETURN_AT.timestamp() * 1000), 0, (1, 2))

    def test_an_active_mission_needs_a_confirmed_home_port(self):
        with tempfile.TemporaryDirectory() as directory:
            scheduler = self.make_scheduler(directory)
            with self.assertRaises(SchedulerInvariantError):
                scheduler.adopt_observed_mission(2, self.mission(), home_port_verified=False)
            self.assertIs(scheduler.fleet(2).phase, FleetPhase.IDLE)

    def test_a_returned_mission_needs_no_home_port_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            scheduler = self.make_scheduler(directory)
            adopted = scheduler.adopt_observed_mission(
                2, self.mission(status=2), home_port_verified=False
            )
            self.assertIs(adopted.phase, FleetPhase.RETURNED_PENDING_UI)

    def test_only_idle_like_phases_may_adopt(self):
        with tempfile.TemporaryDirectory() as directory:
            scheduler = self.make_scheduler(directory)
            scheduler.mark_ready(2)
            scheduler.begin_dispatch(2)
            scheduler.confirm_dispatch(2, self.mission())
            with self.assertRaises(SchedulerInvariantError):
                scheduler.adopt_observed_mission(2, self.mission(), home_port_verified=True)

    def test_a_zero_status_tuple_cannot_be_built_at_all(self):
        # MissionTuple already refuses status 0, so "adopt an idle fleet" is not
        # even expressible: the guard lives one layer below the adoption call.
        with self.assertRaises(ValueError):
            MissionTuple(0, 0, 0, 0, (1, 2))


if __name__ == "__main__":
    unittest.main()
