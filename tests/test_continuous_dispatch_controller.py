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
from kancollebot.expeditions.expedition_requirement_checker import (
    ExpeditionRequirementChecker,
)
from kancollebot.expeditions.fleet_state_store import SqliteFleetStateStore
from kancollebot.expeditions.dispatch_registry import (
    SqliteDispatchRegistry,
    new_dispatch_id,
)
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
    equipment_by_ship=None,
    total_stats=None,
    in_dock_ship_ids=(),
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
        equipment_by_ship=equipment_by_ship,
        total_stats=total_stats,
        in_dock_ship_ids=tuple(in_dock_ship_ids),
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


class WitnessAwareReader(FakeReader):
    """A reader that can be asked to wait for a natural port witness.

    The wait must never come before the Home Port click: that click is what
    makes the client request the port payload being waited for.
    """

    def __init__(self, ui, *observations):
        super().__init__(*observations)
        self.ui = ui
        self.waits = []
        self.origins = []

    def ensure_fresh(self, *, origin):
        self.origins.append(origin)
        if not self.observations:
            raise AssertionError("unexpected extra Poi read")
        self.read_count += 1
        return self.observations.pop(0)

    def await_passive_witness(self, *, timeout_seconds, newer_than):
        if not self.ui.home_calls:
            raise AssertionError(
                "the passive witness wait must follow the Home Port click"
            )
        self.waits.append(
            {"timeout_seconds": timeout_seconds, "newer_than": newer_than}
        )
        return None


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
        passive_witness_wait_seconds=10.0,
        requirement_checker=None,
        unlocked_fleet_ids=None,
        dispatchable_fleet_ids=None,
        dispatch_registry=None,
        mission_name_resolver=None,
        require_mission_name=False,
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
            passive_witness_wait_seconds=passive_witness_wait_seconds,
            requirement_checker=requirement_checker,
            unlocked_fleet_ids=unlocked_fleet_ids,
            dispatchable_fleet_ids=dispatchable_fleet_ids,
            dispatch_registry=dispatch_registry,
            mission_name_resolver=mission_name_resolver,
            require_mission_name=require_mission_name,
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
    def test_dispatch_waits_for_the_natural_witness_after_returning_home(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = FakeUi()
            reader = WitnessAwareReader(ui, *self.valid_observations())
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
            self.assertEqual(ui.home_calls, [2])
            self.assertEqual(
                reader.origins,
                [
                    "dispatch_preflight",
                    "dispatch_post_action",
                    "dispatch_home_verification",
                ],
            )
            self.assertEqual(len(reader.waits), 1)
            self.assertEqual(reader.waits[0]["timeout_seconds"], 10.0)
            names = [name for name, _ in events]
            self.assertIn("dispatch_post_action_passive_wait", names)
            self.assertLess(
                names.index("dispatch_post_action_passive_wait"),
                names.index("dispatch_confirmed_by_poi"),
            )

    def test_a_reader_without_the_wait_capability_is_left_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = FakeReader(*self.valid_observations())
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

            controller.dispatch_one_ready(2)

            names = [name for name, _ in events]
            self.assertNotIn("dispatch_post_action_passive_wait", names)

    def test_a_negative_passive_wait_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = FakeReader(*self.valid_observations())
            with self.assertRaisesRegex(ValueError, "finite non-negative"):
                self.make_controller(
                    directory,
                    reader,
                    FakeUi(),
                    passive_witness_wait_seconds=-1.0,
                )

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


    def test_required_mission_name_is_persisted_before_dispatch_click(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "state.sqlite3"
            registry = SqliteDispatchRegistry(db_path, task_kind="expedition")
            controller, _, _, events = self.make_controller(
                directory,
                FakeReader(),
                FakeUi(),
                dispatch_registry=registry,
                mission_name_resolver=lambda mission_id: "????",
                require_mission_name=True,
            )
            dispatch_id = new_dispatch_id()
            intent = type("Intent", (), {
                "fleet_id": 2,
                "mission_id": 1,
                "ship_ids": (1, 2),
            })()
            controller._persist_dispatch_identity(dispatch_id, intent)
            record = registry.get(dispatch_id)
        self.assertEqual(record.quest_name, "????")
        self.assertEqual(record.status, "pending")
        names = [name for name, _ in events]
        self.assertIn("dispatch_mission_name_resolved", names)

    def test_required_mission_name_lookup_failure_refuses_to_persist_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "state.sqlite3"
            registry = SqliteDispatchRegistry(db_path, task_kind="expedition")

            def fail_lookup(mission_id):
                raise RuntimeError("Poi lookup unavailable")

            controller, _, _, events = self.make_controller(
                directory,
                FakeReader(),
                FakeUi(),
                dispatch_registry=registry,
                mission_name_resolver=fail_lookup,
                require_mission_name=True,
            )
            dispatch_id = new_dispatch_id()
            intent = type("Intent", (), {
                "fleet_id": 2,
                "mission_id": 1,
                "ship_ids": (1, 2),
            })()
            with self.assertRaisesRegex(
                ContinuousDispatchError, "mission name lookup failed before dispatch"
            ):
                controller._persist_dispatch_identity(dispatch_id, intent)
            self.assertIsNone(registry.get(dispatch_id))
        self.assertIn(
            "dispatch_mission_name_lookup_failed",
            [name for name, _ in events],
        )


class R3IRequirementGateTests(unittest.TestCase):
    """R3-I is wired into the dispatch transaction BEFORE the first click.

    The two builders are borrowed from the legacy controller test case (they
    compose the same real scheduler/store/arbiter), without inheriting and
    re-running every legacy test method.
    """

    make_controller = ContinuousDispatchControllerTests.make_controller
    valid_observations = ContinuousDispatchControllerTests.valid_observations

    def r3i_checker(self, **kwargs):
        return ExpeditionRequirementChecker(clock=lambda: BASE, **kwargs)

    def full_clock(self):
        return FakeClock(
            BASE,
            BASE + timedelta(seconds=2),
            BASE + timedelta(seconds=3),
            BASE + timedelta(seconds=4),
            BASE + timedelta(seconds=5),
        )

    def assert_zero_click_refusal(self, *, controller, ui, reader, events, intents, expected):
        with self.assertRaisesRegex(ContinuousDispatchError, "requirement check"):
            controller.dispatch_one_ready(2)
        self.assertEqual(ui.dispatches, [])
        self.assertEqual(ui.home_calls, [])
        self.assertEqual(reader.read_count, 1)
        self.assertIsNone(intents.get(2))
        names = [name for name, _ in events]
        self.assertNotIn("dispatch_intent_persisted", names)
        self.assertIn("dispatch_requirement_evaluation", names)
        self.assertIn("dispatch_blocked_requirement_check", names)
        evaluation = dict(events[names.index("dispatch_requirement_evaluation")][1])
        blocked = dict(events[names.index("dispatch_blocked_requirement_check")][1])
        self.assertEqual(evaluation["overall_result"], expected)
        self.assertFalse(evaluation["allows_dispatch"])
        self.assertEqual(evaluation["safety"]["game_clicks_sent"], 0)
        self.assertEqual(evaluation["safety"]["game_api_requests_sent"], 0)
        self.assertEqual(blocked["dispatch_clicks_sent"], 0)
        self.assertEqual(blocked["game_api_requests_sent"], 0)
        self.assertFalse(blocked["fleet_composition_changes_proposed"])
        return evaluation

    def test_only_a_pass_verdict_reaches_the_existing_dispatch_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = FakeUi()
            reader = WitnessAwareReader(ui, *self.valid_observations())
            controller, scheduler, intents, events = self.make_controller(
                directory, reader, ui,
                clock=self.full_clock(),
                requirement_checker=self.r3i_checker(),
            )
            result = controller.dispatch_one_ready(2)
            self.assertEqual(result.fleet_id, 2)
            self.assertEqual(ui.dispatches, [(2, 1)])
            names = [name for name, _ in events]
            self.assertIn("dispatch_requirement_evaluation", names)
            self.assertNotIn("dispatch_blocked_requirement_check", names)
            evaluation = dict(events[names.index("dispatch_requirement_evaluation")][1])
            self.assertEqual(evaluation["overall_result"], "PASS")
            self.assertTrue(evaluation["allows_dispatch"])
            self.assertEqual(evaluation["safety"]["game_clicks_sent"], 0)
            self.assertLess(
                names.index("dispatch_requirement_evaluation"),
                names.index("dispatch_intent_persisted"),
            )
            self.assertIn("dispatch_confirmed_by_poi", names)

    def test_a_reject_verdict_blocks_with_zero_clicks_and_no_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = observation(
                BASE - timedelta(seconds=1),
                [snapshot(2, fuel_ready=False), snapshot(3)],
            )
            ui = FakeUi()
            reader = FakeReader(baseline)
            controller, scheduler, intents, events = self.make_controller(
                directory, reader, ui,
                clock=FakeClock(BASE),
                requirement_checker=self.r3i_checker(),
            )
            evaluation = self.assert_zero_click_refusal(
                controller=controller, ui=ui, reader=reader, events=events,
                intents=intents, expected="REJECT",
            )
            self.assertIn("supply", evaluation["failed_checks"])
            self.assertEqual(scheduler.fleet(2).phase, FleetPhase.IDLE)

    def test_an_unknown_verdict_blocks_with_zero_clicks_and_no_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = observation(
                BASE - timedelta(seconds=1),
                [snapshot(2, ship_levels=(), ship_types=()), snapshot(3)],
            )
            ui = FakeUi()
            reader = FakeReader(baseline)
            controller, scheduler, intents, events = self.make_controller(
                directory, reader, ui,
                clock=FakeClock(BASE),
                requirement_checker=self.r3i_checker(),
            )
            evaluation = self.assert_zero_click_refusal(
                controller=controller, ui=ui, reader=reader, events=events,
                intents=intents, expected="UNKNOWN",
            )
            self.assertIn("flagship_level", evaluation["unknown_checks"])
            self.assertIn("flagship_valid", evaluation["unknown_checks"])

    def test_a_missing_static_rule_is_an_unknown_zero_click_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = FakeUi()
            reader = FakeReader(observation(
                BASE - timedelta(seconds=1), [snapshot(2), snapshot(3)]
            ))
            controller, scheduler, intents, events = self.make_controller(
                directory, reader, ui,
                clock=FakeClock(BASE),
                mission_requirements={
                    2: MissionCompositionRequirement(9999, 2, 1),
                    3: MissionCompositionRequirement(3, 3, 3),
                },
                requirement_checker=self.r3i_checker(),
            )
            evaluation = self.assert_zero_click_refusal(
                controller=controller, ui=ui, reader=reader, events=events,
                intents=intents, expected="UNKNOWN",
            )
            self.assertIn("rule_source", evaluation["unknown_checks"])

    def test_mixed_fleet_identity_is_never_used_to_clear_the_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            # Ship 1 appears in BOTH fleet records: the R3-I normalizer must
            # mark the identity ambiguous even though _snapshot_for() succeeds.
            baseline = observation(
                BASE - timedelta(seconds=1),
                [snapshot(2, ship_ids=(1, 2)), snapshot(3, ship_ids=(1, 4, 5))],
            )
            ui = FakeUi()
            reader = FakeReader(baseline)
            controller, scheduler, intents, events = self.make_controller(
                directory, reader, ui,
                clock=FakeClock(BASE),
                requirement_checker=self.r3i_checker(),
            )
            evaluation = self.assert_zero_click_refusal(
                controller=controller, ui=ui, reader=reader, events=events,
                intents=intents, expected="UNKNOWN",
            )
            self.assertIn("fleet_identity", evaluation["unknown_checks"])

    def test_account_unlock_and_dispatch_policy_facts_reach_the_gate(self):
        for label, kwargs in (
            ("locked", {"unlocked_fleet_ids": (3,)}),
            ("out-of-policy", {"dispatchable_fleet_ids": (3,)}),
        ):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                ui = FakeUi()
                reader = FakeReader(observation(
                    BASE - timedelta(seconds=1), [snapshot(2), snapshot(3)]
                ))
                controller, scheduler, intents, events = self.make_controller(
                    directory, reader, ui,
                    clock=FakeClock(BASE),
                    requirement_checker=self.r3i_checker(),
                    **kwargs,
                )
                evaluation = self.assert_zero_click_refusal(
                    controller=controller, ui=ui, reader=reader, events=events,
                    intents=intents, expected="REJECT",
                )
                self.assertIn("fleet_available", evaluation["failed_checks"])

    def test_the_gate_is_opt_in_so_existing_controller_tests_are_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = FakeUi()
            reader = WitnessAwareReader(ui, *self.valid_observations())
            controller, scheduler, intents, events = self.make_controller(
                directory, reader, ui, clock=self.full_clock()
            )
            controller.dispatch_one_ready(2)
            self.assertEqual(ui.dispatches, [(2, 1)])
            self.assertNotIn(
                "dispatch_requirement_evaluation", [name for name, _ in events]
            )

    def test_the_constructor_refuses_a_fake_checker_or_bad_fleet_sets(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = FakeReader(*self.valid_observations())
            with self.assertRaisesRegex(ValueError, "must be an ExpeditionRequirementChecker"):
                self.make_controller(
                    directory, reader, FakeUi(), requirement_checker=object()
                )
            with self.assertRaisesRegex(ValueError, "must be a tuple of positive integers"):
                self.make_controller(
                    directory, reader, FakeUi(), unlocked_fleet_ids=[2]
                )
            with self.assertRaisesRegex(ValueError, "must be a tuple of positive integers"):
                self.make_controller(
                    directory, reader, FakeUi(), dispatchable_fleet_ids=(0,)
                )


class SlowOrFailingReader(FakeReader):
    """A Poi read that stalls, times out, or returns an animation-era snapshot."""

    def __init__(self, observation_or_error):
        super().__init__()
        self.observation_or_error = observation_or_error

    def read_fleets(self):
        self.read_count += 1
        if isinstance(self.observation_or_error, BaseException):
            raise self.observation_or_error
        return self.observation_or_error


class R3IFreshnessAndLatencyGateTests(unittest.TestCase):
    """The dispatch gate must fail closed under latency, animation and stale data.

    These scenarios are the ones a real client produces while a reward/return
    animation is on screen: Poi Redux can already say "idle" while no fresh
    /api_port/port witness exists, or a slow network can age a read past the
    freshness window. In every case the refusal happens during the READ, so no
    requirement evaluation, no intent and no click can occur.
    """

    make_controller = ContinuousDispatchControllerTests.make_controller
    valid_observations = ContinuousDispatchControllerTests.valid_observations

    def r3i_checker(self):
        return ExpeditionRequirementChecker(clock=lambda: BASE)

    def assert_read_stage_refusal(self, *, controller, ui, reader, intents, events):
        self.assertEqual(ui.dispatches, [])
        self.assertEqual(ui.home_calls, [])
        self.assertIsNone(intents.get(2))
        names = [name for name, _ in events]
        self.assertNotIn("dispatch_intent_persisted", names)
        self.assertNotIn("dispatch_requirement_evaluation", names)
        self.assertNotIn("dispatch_blocked_requirement_check", names)
        self.assertEqual(reader.read_count, 1)

    def test_network_latency_that_ages_the_baseline_is_refused_zero_click(self):
        # A read that started fresh but finished after the freshness window.
        stale = observation(
            BASE - timedelta(seconds=95), [snapshot(2), snapshot(3)]
        )
        with tempfile.TemporaryDirectory() as directory:
            ui = FakeUi()
            reader = SlowOrFailingReader(stale)
            controller, _, intents, events = self.make_controller(
                directory, reader, ui,
                clock=FakeClock(BASE),
                requirement_checker=self.r3i_checker(),
            )
            with self.assertRaisesRegex(ContinuousDispatchError, "not trusted"):
                controller.dispatch_one_ready(2)
            self.assert_read_stage_refusal(
                controller=controller, ui=ui, reader=reader,
                intents=intents, events=events,
            )

    def test_a_poi_read_timeout_never_reaches_the_gate_or_the_ui(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = FakeUi()
            reader = SlowOrFailingReader(TimeoutError("Poi read timed out"))
            controller, _, intents, events = self.make_controller(
                directory, reader, ui,
                clock=FakeClock(BASE),
                requirement_checker=self.r3i_checker(),
            )
            with self.assertRaises(TimeoutError):
                controller.dispatch_one_ready(2)
            self.assert_read_stage_refusal(
                controller=controller, ui=ui, reader=reader,
                intents=intents, events=events,
            )

    def test_animation_period_without_a_fresh_witness_is_refused_zero_click(self):
        # This is the real-client state observed on 2026-11-05: an expedition
        # "成功!" result screen is on display while Poi Redux already reports
        # every fleet idle. No natural port response => no freshness witness.
        unverified = observation(
            BASE - timedelta(seconds=1), [snapshot(2), snapshot(3)], fresh=False
        )
        with tempfile.TemporaryDirectory() as directory:
            ui = FakeUi()
            reader = SlowOrFailingReader(unverified)
            controller, _, intents, events = self.make_controller(
                directory, reader, ui,
                clock=FakeClock(BASE),
                requirement_checker=self.r3i_checker(),
            )
            with self.assertRaisesRegex(ContinuousDispatchError, "not trusted"):
                controller.dispatch_one_ready(2)
            self.assert_read_stage_refusal(
                controller=controller, ui=ui, reader=reader,
                intents=intents, events=events,
            )

    def test_a_future_dated_observation_is_refused_zero_click(self):
        future = observation(
            BASE + timedelta(seconds=60), [snapshot(2), snapshot(3)]
        )
        with tempfile.TemporaryDirectory() as directory:
            ui = FakeUi()
            reader = SlowOrFailingReader(future)
            controller, _, intents, events = self.make_controller(
                directory, reader, ui,
                clock=FakeClock(BASE),
                requirement_checker=self.r3i_checker(),
            )
            with self.assertRaisesRegex(ContinuousDispatchError, "not trusted"):
                controller.dispatch_one_ready(2)
            self.assert_read_stage_refusal(
                controller=controller, ui=ui, reader=reader,
                intents=intents, events=events,
            )

    def test_the_gate_and_the_intent_bind_the_same_fresh_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = FakeUi()
            reader = WitnessAwareReader(ui, *self.valid_observations())
            controller, _, intents, events = self.make_controller(
                directory, reader, ui,
                clock=FakeClock(
                    BASE, BASE + timedelta(seconds=2), BASE + timedelta(seconds=3),
                    BASE + timedelta(seconds=4), BASE + timedelta(seconds=5),
                ),
                requirement_checker=self.r3i_checker(),
            )
            controller.dispatch_one_ready(2)
            names = [name for name, _ in events]
            evaluation = dict(events[names.index("dispatch_requirement_evaluation")][1])
            persisted = dict(events[names.index("dispatch_intent_persisted")][1])
            self.assertEqual(
                evaluation["baseline_observed_at"], persisted["baseline_observed_at"]
            )
            self.assertEqual(evaluation["baseline_observed_at"], (
                BASE - timedelta(seconds=1)
            ).isoformat())
            self.assertTrue(evaluation["baseline_source_freshness_verified"])
            self.assertEqual(evaluation["baseline_ship_ids"], [1, 2])
            self.assertEqual(
                evaluation["baseline_source"], "poi-redux:info.fleets+info.ships"
            )
            self.assertLess(
                names.index("dispatch_requirement_evaluation"),
                names.index("dispatch_intent_persisted"),
            )

    def test_equipment_and_stat_evidence_travel_with_the_same_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = FakeUi()
            equipment = {1: (75, 75), 2: ()}
            stats = {"firepower": 50, "anti_air": 70, "asw": 180, "los": 40}
            reader = WitnessAwareReader(ui, *self.valid_observations())
            # Replace the first (pre-dispatch) observation with one carrying
            # evidence on the very same fleet record.
            original = reader.observations[0]
            evidenced = original.__class__(
                (
                    snapshot(2, equipment_by_ship=equipment, total_stats=stats),
                    original.payload[1],
                ),
                original.observed_at,
                original.source,
                source_freshness_verified=original.source_freshness_verified,
            )
            reader.observations[0] = evidenced
            controller, _, _, events = self.make_controller(
                directory, reader, ui,
                clock=FakeClock(
                    BASE, BASE + timedelta(seconds=2), BASE + timedelta(seconds=3),
                    BASE + timedelta(seconds=4), BASE + timedelta(seconds=5),
                ),
                requirement_checker=self.r3i_checker(),
            )
            controller.dispatch_one_ready(2)
            names = [name for name, _ in events]
            evaluation = dict(events[names.index("dispatch_requirement_evaluation")][1])
            self.assertTrue(evaluation["equipment_evidence_present"])
            self.assertTrue(evaluation["fleet_stats_evidence_present"])
            self.assertEqual(evaluation["fleet_stats"], stats)
            self.assertEqual(evaluation["in_dock_ship_ids"], [])
            self.assertEqual(evaluation["overall_result"], "PASS")

    def test_in_dock_evidence_on_the_baseline_blocks_with_zero_clicks(self):
        baseline = observation(
            BASE - timedelta(seconds=1),
            [snapshot(2, in_dock_ship_ids=(1,)), snapshot(3)],
        )
        with tempfile.TemporaryDirectory() as directory:
            ui = FakeUi()
            reader = FakeReader(baseline)
            controller, _, intents, events = self.make_controller(
                directory, reader, ui,
                clock=FakeClock(BASE),
                requirement_checker=self.r3i_checker(),
            )
            with self.assertRaisesRegex(ContinuousDispatchError, "requirement check REJECT"):
                controller.dispatch_one_ready(2)
            self.assertEqual(ui.dispatches, [])
            self.assertEqual(ui.home_calls, [])
            self.assertIsNone(intents.get(2))
            names = [name for name, _ in events]
            evaluation = dict(events[names.index("dispatch_requirement_evaluation")][1])
            self.assertEqual(evaluation["in_dock_ship_ids"], [1])
            self.assertIn("fleet_available", evaluation["failed_checks"])
            blocked = dict(events[names.index("dispatch_blocked_requirement_check")][1])
            self.assertEqual(blocked["dispatch_clicks_sent"], 0)
            self.assertEqual(blocked["game_api_requests_sent"], 0)


if __name__ == "__main__":
    unittest.main()