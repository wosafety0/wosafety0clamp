from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from kancollebot.core.observed_state import ObservedState
from kancollebot.expeditions.continuous_dispatch_controller import (
    ContinuousDispatchController,
    ContinuousDispatchError,
)
from kancollebot.expeditions.continuous_scheduler import (
    ContinuousExpeditionScheduler,
    FleetPhase,
    MissionTuple,
    UiOperationArbiter,
)
from kancollebot.expeditions.dispatch_recovery import (
    DurableDispatchCoordinator,
    SqliteDispatchIntentStore,
)
from kancollebot.expeditions.fleet_state_store import SqliteFleetStateStore
from kancollebot.expeditions.mission_eligibility import MissionCompositionRequirement
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot


BASE = datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)


def fleet_snapshot(
    *, fleet_id=3, status=2, mission_id=3, return_at=None, ship_ids=(3, 4, 5),
    reserved=0,
):
    return ExpeditionFleetSnapshot(
        fleet_id=fleet_id,
        ship_ids=tuple(ship_ids),
        mission_id=mission_id,
        return_at=return_at,
        fuel_ready=True,
        ammo_ready=True,
        damaged_ship_ids=(),
        heavy_damage_ship_ids=(),
        mission_status=status,
        reserved=reserved,
        ship_levels=tuple(3 if index == 0 else 1 for index, _ in enumerate(ship_ids)),
        ship_types=tuple(1 for _ in ship_ids),
    )


class FakeClock:
    def __init__(self, *values):
        self.values = list(values)

    def __call__(self):
        if not self.values:
            raise AssertionError("unexpected clock read")
        return self.values.pop(0)


class FakeReader:
    def __init__(self, observation, *, wait_result=True):
        self.observation = observation
        self.wait_result = wait_result
        self.read_count = 0
        self.wait_calls = []

    def wait_for_fresh_port_witness(self, *, after, timeout_seconds):
        self.wait_calls.append((after, timeout_seconds))
        return self.wait_result

    def read_fleets(self):
        self.read_count += 1
        return self.observation


class ContinuousReturnObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "state.sqlite3"
        self.scheduler = ContinuousExpeditionScheduler(
            state_store=SqliteFleetStateStore(self.db_path)
        )
        self.active = MissionTuple(
            1, 3, int((BASE + timedelta(minutes=10)).timestamp() * 1000),
            0, (3, 4, 5),
        )
        self.scheduler.mark_ready(3)
        self.scheduler.begin_dispatch(3)
        self.scheduler.confirm_dispatch(3, self.active)
        self.scheduler.confirm_home_port_after_dispatch(
            3, ui_state="HOME_PORT", observed_mission=self.active
        )

    def make_controller(self, reader, clock, events=None):
        event_log = events if events is not None else []
        coordinator = DurableDispatchCoordinator(
            self.scheduler, SqliteDispatchIntentStore(self.db_path)
        )
        return ContinuousDispatchController(
            scheduler=self.scheduler,
            dispatch_coordinator=coordinator,
            state_reader=reader,
            ui_driver=object(),
            ui_arbiter=UiOperationArbiter(),
            mission_requirements={
                2: MissionCompositionRequirement(1, 2, 1),
                3: MissionCompositionRequirement(3, 3, 3),
            },
            event_sink=lambda name, fields: event_log.append((name, dict(fields))),
            allow_ui_actions=False,
            clock=clock,
        )

    def returned_observation(self, *, ship_ids=(3, 4, 5), status=2, fresh=True):
        due = datetime.fromtimestamp(self.active.final_time_ms / 1000, tz=timezone.utc)
        observed_at = due + timedelta(seconds=1)
        returned = fleet_snapshot(
            status=status,
            mission_id=3,
            return_at=due,
            ship_ids=ship_ids,
        )
        return ObservedState(
            (returned,),
            observed_at,
            "poi-redux:info.fleets+info.ships",
            source_freshness_verified=fresh,
        ), due

    def test_confirms_return_only_after_due_time_and_new_fresh_witness(self):
        observation, due = self.returned_observation()
        reader = FakeReader(observation)
        events = []
        controller = self.make_controller(
            reader,
            FakeClock(due + timedelta(seconds=2), due + timedelta(seconds=3)),
            events,
        )

        returned = controller.wait_for_return(3)

        self.assertEqual(returned.mission_status, 2)
        self.assertTrue(returned.same_expedition(self.active))
        self.assertEqual(self.scheduler.fleet(3).phase, FleetPhase.RETURNED_PENDING_UI)
        self.assertEqual(reader.read_count, 1)
        self.assertEqual(reader.wait_calls, [(due, 30.0)])
        self.assertTrue(any(name == "expedition_return_confirmed_by_poi" for name, _ in events))

    def test_refuses_to_read_or_wait_before_return_deadline(self):
        observation, due = self.returned_observation()
        reader = FakeReader(observation)
        controller = self.make_controller(
            reader,
            FakeClock(due - timedelta(seconds=1)),
        )

        self.assertIsNone(controller.wait_for_return(3))

        self.assertEqual(reader.read_count, 0)
        self.assertEqual(reader.wait_calls, [])
        self.assertEqual(self.scheduler.fleet(3).phase, FleetPhase.EXPEDITION_ACTIVE)

    def test_missing_natural_witness_blocks_without_any_ui_action(self):
        observation, due = self.returned_observation()
        reader = FakeReader(observation, wait_result=False)
        controller = self.make_controller(
            reader,
            FakeClock(due + timedelta(seconds=2)),
        )

        with self.assertRaisesRegex(ContinuousDispatchError, "No new natural"):
            controller.wait_for_return(3)

        self.assertEqual(reader.read_count, 0)
        self.assertEqual(self.scheduler.fleet(3).phase, FleetPhase.BLOCKED)

    def test_wrong_returned_ship_roster_fails_closed(self):
        observation, due = self.returned_observation(ship_ids=(3, 4))
        reader = FakeReader(observation)
        controller = self.make_controller(
            reader,
            FakeClock(due + timedelta(seconds=2), due + timedelta(seconds=3)),
        )

        with self.assertRaisesRegex(ContinuousDispatchError, "does not match"):
            controller.wait_for_return(3)

        self.assertEqual(self.scheduler.fleet(3).phase, FleetPhase.BLOCKED)

    def test_unverified_poi_snapshot_fails_closed(self):
        observation, due = self.returned_observation(fresh=False)
        reader = FakeReader(observation)
        controller = self.make_controller(
            reader,
            FakeClock(due + timedelta(seconds=2), due + timedelta(seconds=3)),
        )

        with self.assertRaisesRegex(ContinuousDispatchError, "not trusted"):
            controller.wait_for_return(3)

        self.assertEqual(self.scheduler.fleet(3).phase, FleetPhase.BLOCKED)

    def test_wrong_mission_status_is_not_treated_as_returned(self):
        observation, due = self.returned_observation(status=1)
        reader = FakeReader(observation)
        controller = self.make_controller(
            reader,
            FakeClock(due + timedelta(seconds=2), due + timedelta(seconds=3)),
        )

        with self.assertRaisesRegex(ContinuousDispatchError, "has not confirmed"):
            controller.wait_for_return(3)

        self.assertEqual(self.scheduler.fleet(3).phase, FleetPhase.BLOCKED)


if __name__ == "__main__":
    unittest.main()
