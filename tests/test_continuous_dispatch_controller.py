from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from kancollebot.core.observed_state import ObservedState
from kancollebot.expeditions.continuous_dispatch_controller import (
    ContinuousDispatchController,
    ContinuousDispatchError,
    SqliteDispatchEventLog,
    UiStateEvidence,
)
from kancollebot.expeditions.continuous_scheduler import (
    ContinuousExpeditionScheduler,
    FleetPhase,
    UiOperationArbiter,
)
from kancollebot.expeditions.dispatch_recovery import (
    DurableDispatchCoordinator,
    SqliteDispatchIntentStore,
)
from kancollebot.expeditions.fleet_state_store import SqliteFleetStateStore
from kancollebot.expeditions.mission_eligibility import MissionCompositionRequirement
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot


BASE = datetime(2026, 11, 2, 0, 0, 0, tzinfo=timezone.utc)


def snapshot(
    fleet_id,
    *,
    status=0,
    mission_id=None,
    return_at=None,
    ship_ids=None,
    fuel_ready=True,
    ammo_ready=True,
    damaged=(),
    heavy_damaged=(),
    reserved=0,
    ship_levels=None,
    ship_types=None,
):
    if ship_ids is None:
        ship_ids = (1, 2) if fleet_id == 2 else (3, 4, 5)
    if ship_levels is None:
        ship_levels = tuple(3 if index == 0 else 1 for index, _ in enumerate(ship_ids))
    if ship_types is None:
        ship_types = tuple(1 for _ in ship_ids)
    # 大破 is a subset of damaged: the planner invariant requires every
    # heavy-damage ship to also appear in damaged_ship_ids.
    damaged_ids = tuple(damaged) + tuple(
        ship_id for ship_id in heavy_damaged if ship_id not in damaged
    )
    return ExpeditionFleetSnapshot(
        fleet_id=fleet_id,
        ship_ids=tuple(ship_ids),
        mission_id=mission_id,
        return_at=return_at,
        fuel_ready=fuel_ready,
        ammo_ready=ammo_ready,
        damaged_ship_ids=damaged_ids,
        heavy_damage_ship_ids=tuple(heavy_damaged),
        mission_status=status,
        reserved=reserved,
        ship_levels=tuple(ship_levels),
        ship_types=tuple(ship_types),
    )


def observation(at, fleets, *, fresh=True, source="poi-redux:info.fleets+info.ships"):
    return ObservedState(tuple(fleets), at, source, source_freshness_verified=fresh)


class FakeClock:
    def __init__(self, *values):
        self.values = list(values)

    def __call__(self):
        if not self.values:
            raise AssertionError("unexpected extra clock read")
        return self.values.pop(0)


class FakeReader:
    def __init__(self, *observations):
        self.observations = list(observations)
        self.read_count = 0

    def read_fleets(self):
        self.read_count += 1
        if not self.observations:
            raise AssertionError("unexpected extra Poi read")
        return self.observations.pop(0)


class FakeUi:
    def __init__(self, home_evidence=None, dispatch_error=None):
        self.dispatches = []
        self.home_calls = []
        self.home_evidence = home_evidence or UiStateEvidence(
            "HOME_PORT",
            ("HOME_PORT", "HOME_PORT"),
            "two_consecutive_in_memory_template_matches",
        )
        self.dispatch_error = dispatch_error

    def dispatch_expedition(self, *, fleet_id, mission_id):
        self.dispatches.append((fleet_id, mission_id))
        if self.dispatch_error:
            raise self.dispatch_error

    def return_to_home_port(self, *, fleet_id):
        self.home_calls.append(fleet_id)
        return self.home_evidence


class ContinuousDispatchControllerTests(unittest.TestCase):
    def make_controller(
        self,
        directory,
        reader,
        ui,
        *,
        clock=None,
        fresh_age=timedelta(seconds=30),
        allow=True,
        events=None,
        mission_requirements=None,
    ):
        db_path = Path(directory) / "state.sqlite3"
        scheduler = ContinuousExpeditionScheduler(
            state_store=SqliteFleetStateStore(db_path)
        )
        intent_store = SqliteDispatchIntentStore(db_path)
        coordinator = DurableDispatchCoordinator(scheduler, intent_store)
        events = events if events is not None else []
        controller = ContinuousDispatchController(
            scheduler=scheduler,
            dispatch_coordinator=coordinator,
            state_reader=reader,
            ui_driver=ui,
            ui_arbiter=UiOperationArbiter(),
            mission_requirements=mission_requirements or {
                2: MissionCompositionRequirement(1, 2, 1),
                3: MissionCompositionRequirement(3, 3, 3),
            },
            event_sink=lambda name, fields: events.append((name, dict(fields))),
            allow_ui_actions=allow,
            max_state_age=fresh_age,
            clock=clock or (lambda: BASE + timedelta(seconds=10)),
        )
        return controller, scheduler, intent_store, events

    def valid_observations(self, fleet_id=2, mission_id=1, *, post_at=None, home_at=None):
        ships = (1, 2) if fleet_id == 2 else (3, 4, 5)
        other_id = 3 if fleet_id == 2 else 2
        other_ships = (3, 4, 5) if fleet_id == 2 else (1, 2)
        return_at = BASE + timedelta(minutes=20)
        baseline = [
            snapshot(fleet_id, ship_ids=ships),
            snapshot(other_id, ship_ids=other_ships),
        ]
        active = [
            snapshot(fleet_id, status=1, mission_id=mission_id, return_at=return_at, ship_ids=ships),
            snapshot(other_id, ship_ids=other_ships),
        ]
        home = [
            snapshot(fleet_id, status=1, mission_id=mission_id, return_at=return_at, ship_ids=ships),
            snapshot(other_id, ship_ids=other_ships),
        ]
        return (
            observation(BASE - timedelta(seconds=1), baseline),
            observation(post_at or BASE + timedelta(seconds=3), active),
            observation(home_at or BASE + timedelta(seconds=5), home),
        )

    def test_sqlite_dispatch_event_log_persists_and_reloads_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shared-state.sqlite3"
            first = SqliteDispatchEventLog(path)
            first("dispatch_intent_persisted", {"fleet_id": 2, "ship_ids": (1, 2)})
            second = SqliteDispatchEventLog(path)
            events = second.load_all()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event"], "dispatch_intent_persisted")
            self.assertEqual(events[0]["fields"], {"fleet_id": 2, "ship_ids": [1, 2]})
            self.assertTrue(events[0]["created_at"])
    def test_dispatch_confirms_active_tuple_and_two_frame_home_port(self):
        with tempfile.TemporaryDirectory() as directory:
            observations = self.valid_observations()
            reader = FakeReader(*observations)
            ui = FakeUi()
            clock = FakeClock(
                BASE,
                BASE + timedelta(seconds=2),
                BASE + timedelta(seconds=3),
                BASE + timedelta(seconds=4),
                BASE + timedelta(seconds=5),
            )
            controller, scheduler, intents, events = self.make_controller(
                directory, reader, ui, clock=clock
            )

            result = controller.dispatch_one_ready(2)

            self.assertEqual(result.fleet_id, 2)
            self.assertEqual(result.mission_id, 1)
            self.assertEqual(result.mission.mission_status, 1)
            self.assertEqual(result.mission.ship_ids, (1, 2))
            self.assertEqual(scheduler.fleet(2).phase, FleetPhase.EXPEDITION_ACTIVE)
            self.assertTrue(scheduler.fleet(2).home_port_verified)
            self.assertEqual(scheduler.fleet(3).phase, FleetPhase.IDLE)
            self.assertIsNone(intents.get(2))
            self.assertEqual(ui.dispatches, [(2, 1)])
            self.assertEqual(ui.home_calls, [2])
            self.assertEqual(reader.read_count, 3)
            self.assertEqual(events[-1][0], "dispatch_home_port_verified")

    def test_fleet_three_uses_its_explicit_mission_assignment(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = FakeReader(*self.valid_observations(3, 3))
            ui = FakeUi()
            clock = FakeClock(
                BASE,
                BASE + timedelta(seconds=2),
                BASE + timedelta(seconds=3),
                BASE + timedelta(seconds=4),
                BASE + timedelta(seconds=5),
            )
            controller, scheduler, _, _ = self.make_controller(
                directory, reader, ui, clock=clock
            )

            result = controller.dispatch_one_ready(3)

            self.assertEqual(result.mission_id, 3)
            self.assertEqual(ui.dispatches, [(3, 3)])
            self.assertTrue(scheduler.fleet(3).home_port_verified)

    def test_ui_actions_require_explicit_enablement(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = FakeReader()
            ui = FakeUi()
            controller, _, _, _ = self.make_controller(
                directory, reader, ui, allow=False
            )
            with self.assertRaisesRegex(ContinuousDispatchError, "disabled"):
                controller.dispatch_one_ready(2)
            self.assertEqual(reader.read_count, 0)
            self.assertEqual(ui.dispatches, [])

    def test_unverified_or_stale_baseline_blocks_before_any_ui_action(self):
        with tempfile.TemporaryDirectory() as directory:
            bad = observation(BASE - timedelta(seconds=1), [
                snapshot(2), snapshot(3, ship_ids=(3, 4))
            ], fresh=False)
            reader = FakeReader(bad)
            ui = FakeUi()
            controller, scheduler, intents, _ = self.make_controller(
                directory, reader, ui
            )
            with self.assertRaisesRegex(ContinuousDispatchError, "not trusted"):
                controller.dispatch_one_ready(2)
            self.assertEqual(ui.dispatches, [])
            self.assertEqual(scheduler.fleet(2).phase, FleetPhase.IDLE)
            self.assertIsNone(intents.get(2))

    def test_large_damage_blocks_before_intent_creation_and_ui_input(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = observation(BASE - timedelta(seconds=1), [
                snapshot(2, heavy_damaged=(2,)), snapshot(3)
            ])
            reader = FakeReader(baseline)
            ui = FakeUi()
            controller, scheduler, intents, events = self.make_controller(directory, reader, ui)
            with self.assertRaisesRegex(ContinuousDispatchError, "Taiha.*大破"):
                controller.dispatch_one_ready(2)
            self.assertEqual(ui.dispatches, [])
            self.assertEqual(scheduler.fleet(2).phase, FleetPhase.IDLE)
            self.assertIsNone(intents.get(2))
            blocked = [fields for name, fields in events if name == "dispatch_blocked_large_damage"]
            self.assertEqual(len(blocked), 1)
            self.assertEqual(blocked[0]["ship_ids"], (2,))
            self.assertEqual(blocked[0]["dispatch_clicks_sent"], 0)
            self.assertFalse(blocked[0]["replacement_implemented"])

    def test_missing_mission_composition_metadata_blocks_before_ui(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = observation(BASE - timedelta(seconds=1), [
                snapshot(2, ship_levels=(), ship_types=()), snapshot(3)
            ])
            reader = FakeReader(baseline)
            ui = FakeUi()
            controller, scheduler, intents, _ = self.make_controller(directory, reader, ui)
            with self.assertRaisesRegex(ContinuousDispatchError, "composition metadata is missing"):
                controller.dispatch_one_ready(2)
            self.assertEqual(ui.dispatches, [])
            self.assertEqual(scheduler.fleet(2).phase, FleetPhase.IDLE)
            self.assertIsNone(intents.get(2))

    def test_flagship_level_requirement_is_enforced_for_fleet_three(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = observation(BASE - timedelta(seconds=1), [
                snapshot(3, ship_ids=(3, 4, 5), ship_levels=(2, 1, 1)),
                snapshot(2),
            ])
            reader = FakeReader(baseline)
            ui = FakeUi()
            controller, scheduler, intents, _ = self.make_controller(directory, reader, ui)
            with self.assertRaisesRegex(ContinuousDispatchError, "flagship_level"):
                controller.dispatch_one_ready(3)
            self.assertEqual(ui.dispatches, [])
            self.assertEqual(scheduler.fleet(3).phase, FleetPhase.IDLE)
            self.assertIsNone(intents.get(3))
    def test_under_supplied_fleet_is_rejected_before_intent_or_ui_action(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = observation(BASE - timedelta(seconds=1), [
                snapshot(2, ammo_ready=False), snapshot(3, ship_ids=(3, 4))
            ])
            reader = FakeReader(baseline)
            ui = FakeUi()
            controller, scheduler, intents, _ = self.make_controller(
                directory, reader, ui
            )
            with self.assertRaisesRegex(ContinuousDispatchError, "not ready to dispatch"):
                controller.dispatch_one_ready(2)
            self.assertEqual(ui.dispatches, [])
            self.assertEqual(scheduler.fleet(2).phase, FleetPhase.IDLE)
            self.assertIsNone(intents.get(2))

    def test_post_dispatch_observation_must_be_newer_than_action_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, _, home = self.valid_observations()
            reader = FakeReader(
                baseline,
                observation(BASE + timedelta(seconds=2), [
                    snapshot(2, status=1, mission_id=1, return_at=BASE + timedelta(minutes=20)),
                    snapshot(3, ship_ids=(3, 4)),
                ]),
                home,
            )
            ui = FakeUi()
            clock = FakeClock(
                BASE,
                BASE + timedelta(seconds=2),
                BASE + timedelta(seconds=3),
                BASE + timedelta(seconds=4),
            )
            controller, scheduler, intents, _ = self.make_controller(
                directory, reader, ui, clock=clock
            )
            with self.assertRaisesRegex(ContinuousDispatchError, "time boundary"):
                controller.dispatch_one_ready(2)
            self.assertEqual(scheduler.fleet(2).phase, FleetPhase.BLOCKED)
            self.assertIsNotNone(intents.get(2))
            # The 母港 click comes first now: it is what makes the client
            # request the port payload the freshness gate needs, so a stale
            # observation is detected *after* navigating home - the click is
            # still the only game input the postcondition check may cause.
            self.assertEqual(len(ui.home_calls), 1)

    def test_poi_return_time_must_be_later_than_dispatch_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, _, home = self.valid_observations()
            expired = observation(BASE + timedelta(seconds=3), [
                snapshot(2, status=1, mission_id=1, return_at=BASE + timedelta(seconds=2)),
                snapshot(3, ship_ids=(3, 4)),
            ])
            reader = FakeReader(baseline, expired, home)
            ui = FakeUi()
            clock = FakeClock(
                BASE,
                BASE + timedelta(seconds=2),
                BASE + timedelta(seconds=3),
                BASE + timedelta(seconds=4),
            )
            controller, scheduler, intents, _ = self.make_controller(
                directory, reader, ui, clock=clock
            )
            with self.assertRaisesRegex(ContinuousDispatchError, "return time is not later"):
                controller.dispatch_one_ready(2)
            self.assertEqual(scheduler.fleet(2).phase, FleetPhase.BLOCKED)
            self.assertIsNotNone(intents.get(2))
            # The 母港 click comes first now: it is what makes the client
            # request the port payload the freshness gate needs, so a stale
            # observation is detected *after* navigating home - the click is
            # still the only game input the postcondition check may cause.
            self.assertEqual(len(ui.home_calls), 1)
    def test_mission_identity_mismatch_blocks_and_preserves_pending_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, _, home = self.valid_observations()
            active = observation(BASE + timedelta(seconds=3), [
                snapshot(2, status=1, mission_id=3, return_at=BASE + timedelta(minutes=20)),
                snapshot(3, ship_ids=(3, 4)),
            ])
            reader = FakeReader(baseline, active, home)
            ui = FakeUi()
            clock = FakeClock(
                BASE,
                BASE + timedelta(seconds=2),
                BASE + timedelta(seconds=3),
                BASE + timedelta(seconds=4),
            )
            controller, scheduler, intents, _ = self.make_controller(
                directory, reader, ui, clock=clock
            )
            with self.assertRaisesRegex(ContinuousDispatchError, "does not match"):
                controller.dispatch_one_ready(2)
            self.assertEqual(scheduler.fleet(2).phase, FleetPhase.BLOCKED)
            self.assertIsNotNone(intents.get(2))
            # The 母港 click comes first now: it is what makes the client
            # request the port payload the freshness gate needs, so a stale
            # observation is detected *after* navigating home - the click is
            # still the only game input the postcondition check may cause.
            self.assertEqual(len(ui.home_calls), 1)

    def test_home_port_requires_two_consecutive_matching_states(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = FakeReader(*self.valid_observations())
            ui = FakeUi(UiStateEvidence(
                "HOME_PORT",
                ("HOME_PORT", "HOME_PORT_RETURN_NOTICE"),
                "two_consecutive_in_memory_template_matches",
            ))
            clock = FakeClock(
                BASE,
                BASE + timedelta(seconds=2),
                BASE + timedelta(seconds=3),
                BASE + timedelta(seconds=4),
            )
            controller, scheduler, intents, _ = self.make_controller(
                directory, reader, ui, clock=clock
            )
            with self.assertRaisesRegex(ContinuousDispatchError, "two consecutive"):
                controller.dispatch_one_ready(2)
            self.assertEqual(scheduler.fleet(2).phase, FleetPhase.BLOCKED)
            self.assertIsNone(intents.get(2))
            self.assertFalse(scheduler.fleet(2).home_port_verified)

    def test_home_port_readback_must_preserve_the_exact_active_mission_tuple(self):
        baseline, active, _ = self.valid_observations()
        changed_home = observation(BASE + timedelta(seconds=5), [
            snapshot(2, status=1, mission_id=1, return_at=BASE + timedelta(minutes=21)),
            snapshot(3, ship_ids=(3, 4)),
        ])
        with tempfile.TemporaryDirectory() as directory:
            reader = FakeReader(baseline, active, changed_home)
            ui = FakeUi()
            clock = FakeClock(
                BASE,
                BASE + timedelta(seconds=2),
                BASE + timedelta(seconds=3),
                BASE + timedelta(seconds=4),
                BASE + timedelta(seconds=5),
            )
            controller, scheduler, _, _ = self.make_controller(
                directory, reader, ui, clock=clock
            )
            with self.assertRaisesRegex(ContinuousDispatchError, "tuple changed"):
                controller.dispatch_one_ready(2)
            self.assertEqual(scheduler.fleet(2).phase, FleetPhase.BLOCKED)
            self.assertFalse(scheduler.fleet(2).home_port_verified)

    def test_fleet_one_and_four_cannot_be_assigned(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = FakeReader()
            ui = FakeUi()
            with self.assertRaisesRegex(ValueError, "every and only"):
                self.make_controller(
                    directory, reader, ui, mission_requirements={1: MissionCompositionRequirement(1, 1, 1), 2: MissionCompositionRequirement(1, 2, 1), 3: MissionCompositionRequirement(3, 3, 3), 4: MissionCompositionRequirement(1, 1, 1)}
                )

    def test_ui_driver_exception_blocks_fleet_and_never_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, _, _ = self.valid_observations()
            reader = FakeReader(baseline)
            ui = FakeUi(dispatch_error=RuntimeError("input transport lost"))
            clock = FakeClock(BASE, BASE + timedelta(seconds=2))
            controller, scheduler, intents, _ = self.make_controller(
                directory, reader, ui, clock=clock
            )
            with self.assertRaisesRegex(RuntimeError, "transport lost"):
                controller.dispatch_one_ready(2)
            self.assertEqual(scheduler.fleet(2).phase, FleetPhase.BLOCKED)
            self.assertIsNotNone(intents.get(2))
            self.assertEqual(ui.dispatches, [(2, 1)])

    def test_missing_poi_redux_source_is_rejected_before_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            bad = observation(
                BASE - timedelta(seconds=1),
                [snapshot(2), snapshot(3, ship_ids=(3, 4))],
                source="synthetic:test",
            )
            reader = FakeReader(bad)
            ui = FakeUi()
            controller, _, _, _ = self.make_controller(directory, reader, ui)
            with self.assertRaisesRegex(ContinuousDispatchError, "Poi Redux"):
                controller.dispatch_one_ready(2)
            self.assertEqual(ui.dispatches, [])


if __name__ == "__main__":
    unittest.main()