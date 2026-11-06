from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import one_expedition_ui_loop as entry
import run_continuous_expedition_monitor as monitor_runtime
from kancollebot.calibration import (
    DEFAULT_DETAIL_PATH,
    DEFAULT_MARKS_PATH,
    DEFAULT_POSITION_INDEX_PATH,
    DetailMatch,
    MarkMatch,
    RowLocation,
)
from kancollebot.core.observed_state import ObservedState
from kancollebot.expeditions.continuous_monitor import ContinuousExpeditionMonitor
from kancollebot.expeditions.continuous_scheduler import (
    ContinuousExpeditionScheduler,
    FleetPhase,
)
from kancollebot.expeditions.mission_eligibility import MissionCompositionRequirement
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot
from kancollebot.task_scope import TaskKind
import kancollebot.expeditions.continuous_supervisor as supervisor_module


class FakeReader:
    def __init__(self, snapshots, *, fresh=True):
        self.snapshots = list(snapshots)
        self.fresh = fresh
        self.read_count = 0

    def read_fleets(self):
        self.read_count += 1
        if not self.snapshots:
            raise AssertionError("unexpected extra Poi observation")
        return ObservedState(
            (self.snapshots.pop(0),),
            datetime.now(timezone.utc),
            "poi-redux:production-integration-test",
            source_freshness_verified=self.fresh,
        )


class FakeGameInput:
    """Records the visible clicks, and lets the fleet strip repaint after them.

    This stands in for the game's own rendering: the driver can only continue
    if the screen it reads afterwards agrees with the click it just sent, so the
    fake has to change what it "shows" the way the real screens do.
    """

    def __init__(self, *, marks=None, fleet_after_confirm=2):
        self.clicks = []
        self.marks = marks
        self.fleet_after_confirm = fleet_after_confirm
        self.frames = 0

    def click(self, point):
        self.clicks.append(point.label)
        if self.marks is None:
            return
        if point.label == "mission-confirm":
            self.marks.selected = self.fleet_after_confirm
        elif point.label.startswith("fleet-") and point.label.endswith("-tab"):
            self.marks.selected = int(point.label.split("-")[1])

    def capture_screenshot(self, *, from_surface: bool = True):
        self.frames += 1
        # The start-up probe captures both layers; the fake's "screens" do not
        # differ per layer, so one counter is enough.
        return f"frame-{self.frames}"


class FakeDetails:
    """Name-band evidence, standing in for captions read off a real screen."""

    def __init__(self, known=("03",)):
        self.known = set(known)
        self.asked = []

    def reference(self, expedition_id):
        return object() if expedition_id in self.known else None

    def require(self, expedition_id, screenshot_base64):
        self.asked.append((expedition_id, screenshot_base64))
        return DetailMatch(
            expedition_id=expedition_id,
            score=1.0,
            runner_up="04",
            runner_up_score=0.95,
        )


class FakeMarks:
    """決定 control plus the fleet tabs, with the painted tab as state."""

    def __init__(self, *, fleets=(2, 3, 4), selected=2):
        self._fleets = tuple(fleets)
        self.selected = selected

    def fleets(self):
        return self._fleets

    def require_decision(self, screenshot_base64):
        return MarkMatch(score=1.0, threshold=0.97, detail="決定 control recognised")

    def require_fleet(self, fleet_id, screenshot_base64):
        if fleet_id == self.selected:
            return MarkMatch(score=1.0, threshold=0.95, detail=f"fleet {fleet_id} tab")
        raise RuntimeError(f"fleet {self.selected} is selected, not {fleet_id}")


class FakeListSession:
    """The row locator, standing in for a walked position index."""

    def __init__(self, *, scan, click, index, settle_seconds):
        self.scan = scan
        self.click = click
        self.index = index
        self.settle_seconds = settle_seconds

    def locate_any_tab(self, expedition_id):
        return self.locate(expedition_id)

    def locate(self, expedition_id, *, tab=None):
        return RowLocation(
            expedition_id=expedition_id,
            tab=0,
            position=2,
            offset=0,
            row_index=3,
            point=(350, 351.5),
            scroll_clicks=0,
            tab_clicks=1,
            detail="list position on a page starting at 0",
        )


class FakeProvider:
    """Stands in for BackgroundPortSnapshotProvider, which is callable."""

    def __init__(self, witness=None):
        self.witness = witness
        self.closed = False
        self.reads = 0

    def __call__(self):
        self.reads += 1
        return self.witness

    def close(self, *, join_timeout_seconds):
        self.closed = True


def make_snapshot(*, status=0, mission_id=None, return_at=None):
    return ExpeditionFleetSnapshot(
        fleet_id=3,
        ship_ids=(31, 32, 33),
        mission_id=mission_id,
        return_at=return_at,
        fuel_ready=True,
        ammo_ready=True,
        damaged_ship_ids=(),
        mission_status=status,
        reserved=0,
        ship_levels=(3, 1, 1),
        ship_types=(1, 1, 1),
    )


class FakeWitnessObserver:
    """Stand-in for the passive node child; no process is ever started."""

    def __init__(self, script, *, timeout_seconds=0.0):
        self.script = script
        self.timeout_seconds = timeout_seconds


class FakeWitnessProvider:
    instances: list["FakeWitnessProvider"] = []
    trace: list[str] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.armed = False
        self.cancelled = False
        self.status = "armed"
        self.witnesses: tuple[object, ...] = ()
        FakeWitnessProvider.instances.append(self)

    def arm(self):
        self.armed = True
        FakeWitnessProvider.trace.append("arm")

    def cancel(self):
        self.cancelled = True

    def wait(self, timeout_seconds=None):
        return True

    def wait_for_fleet(self, fleet_id, timeout_seconds):
        return False

    def verdict_for(self, **kwargs):
        from kancollebot.expeditions.mission_result_witness import WitnessVerdict

        return WitnessVerdict(False, "witness_not_yet_observed")

    def receipt_actionable_for(self, fleet_id):
        return False


class OneExpeditionProductionIntegrationTests(unittest.TestCase):
    def make_runtime(self, snapshots, *, fresh=True):
        scheduler = ContinuousExpeditionScheduler(
            dispatch_fleet_ids=(3,),
            read_only_fleet_ids=(1, 2),
            deferred_fleet_ids=(4,),
        )
        reader = FakeReader(snapshots, fresh=fresh)
        event_sink = lambda _name, _fields: None
        monitor = ContinuousExpeditionMonitor(
            scheduler=scheduler,
            state_reader=reader,
            mission_requirements={
                3: MissionCompositionRequirement(
                    mission_id=3,
                    minimum_ships=3,
                    minimum_flagship_level=3,
                )
            },
            event_sink=event_sink,
            max_state_age=timedelta(seconds=30),
        )
        provider = FakeProvider()
        runtime = SimpleNamespace(
            scheduler=scheduler,
            monitor=monitor,
            provider=provider,
            ui_arbiter=None,
            unlocked_fleet_ids=(1, 2, 3, 4),
            fleet_availability_verified=True,
        )
        return runtime, reader, provider

    def make_args(self, database):
        args = MagicMock()
        args.authorize_ui_actions = True
        args.poll_seconds = 5.0
        args.timeout_seconds = 1800.0
        args.grace_delay_seconds = 10.0
        args.database = database
        args.observer_script = ROOT / "tools" / "observer.js"
        args.observer_timeout_seconds = 5.0
        args.max_state_age_seconds = 30.0
        args.enable_ui_refresh = True
        # Real values, not MagicMock truthiness: the wait-mode bounds are
        # validated before composition.
        args.wait_for_return = False
        args.max_ticks = 60
        args.tick_interval_seconds = 10.0
        args.adopt_observed_mission = False
        args.settle_only = False
        # The dispatch target is now an argument rather than a constant, so the
        # stub has to carry a real master-data id: an unset MagicMock attribute
        # is deliberately refused rather than coerced.
        args.fleet_id = 3
        args.mission_id = 3
        args.position_index = DEFAULT_POSITION_INDEX_PATH
        args.detail_index = DEFAULT_DETAIL_PATH
        args.marks = DEFAULT_MARKS_PATH
        # The composition gate has no default; unset means "use the verified
        # pair for this fleet and mission".
        args.minimum_ships = None
        args.flagship_level = None
        return args

    def patch_evidence_sources(self, stack, *, marks):
        """Stand in for the pixels the entry would otherwise read off the screen.

        The entry loads its calibration assets from disk and screenshots the
        live game; a unit test has neither, so the row locator and the visual
        references are replaced while the driver, controller, supervisor and
        monitor under test stay real.
        """
        stack.enter_context(
            patch.object(entry, "ExpeditionListSession", FakeListSession)
        )
        stack.enter_context(
            patch.object(entry, "load_detail_index", lambda _path: FakeDetails())
        )
        stack.enter_context(
            patch.object(entry, "load_marks", lambda _path: marks)
        )
        stack.enter_context(
            patch.object(entry, "load_position_index", lambda _path: object())
        )
        # The fake frames are not decodable PNGs, and the start-up probe fails
        # closed on an unreadable classifier. Stand in for a verified plain
        # port reading so the tests exercise everything after the snapshot.
        stack.enter_context(
            patch.object(
                entry,
                "classify_screenshot_fingerprints",
                return_value=("HOME_PORT", {}),
            )
        )

    def test_entry_wires_real_monitor_controller_supervisor_and_cdp_driver(self):
        return_at = datetime.now(timezone.utc) + timedelta(minutes=20)
        idle = make_snapshot()
        active = make_snapshot(status=1, mission_id=3, return_at=return_at)
        runtime, reader, provider = self.make_runtime([idle, idle, active, active])
        marks = FakeMarks(selected=2)

        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "production" / "state.sqlite3"
            database.parent.mkdir(parents=True)
            args = self.make_args(database)
            game_session = MagicMock()
            game_session.input = FakeGameInput(marks=marks, fleet_after_confirm=2)
            game_session.__enter__.return_value = game_session
            game_session.__exit__.return_value = False

            state_checks = []

            def fake_require_state(_session, state, **kwargs):
                state_checks.append((state.value, kwargs.get("visual_attempts", 3)))
                return "two_consecutive_in_memory_ooi_fingerprints"

            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(entry, "connect_game_webview", return_value=game_session)
                )
                stack.enter_context(patch.object(entry, "_capture_home_reference"))
                stack.enter_context(
                    patch.object(entry, "_require_ui_state", side_effect=fake_require_state)
                )
                stack.enter_context(patch.object(entry, "_print_event"))
                stack.enter_context(
                    patch("kancollebot.adapters.poi.visual_target.require_visual_dependencies")
                )
                stack.enter_context(
                    patch.object(
                        entry, "_probe_unlocked_fleet_ids",
                        return_value=(1, 2, 3, 4),
                    )
                )
                self.patch_evidence_sources(stack, marks=marks)
                # HOME_REFERENCE_SETTLE_SECONDS is deliberately left real: it is
                # the pause the production frame capture uses, and the click
                # order below must not depend on it being zero.
                built = {}

                def fake_build(**kwargs):
                    built.update(kwargs)
                    return runtime

                with patch.object(monitor_runtime, "build_runtime",
                                  side_effect=fake_build) as build:
                    self.assertEqual(entry.run(args), 0)
                    # Dispatch checkpoints keep the stricter three-sample gate,
                    # while the freshness navigation gets the live-verified six.
                    self.assertTrue(state_checks)
                    self.assertEqual({attempts for _, attempts in state_checks}, {3})
                    state_checks.clear()
                    built["ui_state_verifier"]("HOME_PORT")
                    self.assertEqual(state_checks, [("HOME_PORT", 6)])

            build.assert_called_once()
            self.assertEqual(build.call_args.kwargs["dispatch_fleet_ids"], (3,))
            self.assertTrue(build.call_args.kwargs["enable_ui_refresh"])
            self.assertIs(build.call_args.kwargs["ui_input"], game_session.input)
            refresh_verifier = build.call_args.kwargs["ui_state_verifier"]
            self.assertIsNotNone(refresh_verifier)
            self.assertEqual(
                game_session.input.clicks,
                [
                    "sortie",
                    "expedition-mode",
                    "expedition-row-03-pos2",
                    "mission-confirm",
                    "fleet-3-tab",
                    "expedition-start",
                    "home-port",
                ],
            )
            self.assertEqual(reader.read_count, 4)
            self.assertIs(runtime.scheduler.fleet(3).phase, FleetPhase.EXPEDITION_ACTIVE)
            self.assertTrue(runtime.scheduler.fleet(3).home_port_verified)
            self.assertTrue(provider.closed)

    def test_production_entry_passes_verified_account_fleet_kwargs(self):
        """R4: the REAL dispatch-once entry must hand the runtime the observed
        account fact plus an explicit strictness/task decision (not just a
        class-level helper being called correctly).
        """
        return_at = datetime.now(timezone.utc) + timedelta(minutes=20)
        idle = make_snapshot()
        active = make_snapshot(status=1, mission_id=3, return_at=return_at)
        runtime, _, _ = self.make_runtime([idle, idle, active, active])
        marks = FakeMarks(selected=2)

        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "production" / "state.sqlite3"
            database.parent.mkdir(parents=True)
            args = self.make_args(database)
            game_session = MagicMock()
            game_session.input = FakeGameInput(marks=marks, fleet_after_confirm=2)
            game_session.__enter__.return_value = game_session
            game_session.__exit__.return_value = False

            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(entry, "connect_game_webview", return_value=game_session)
                )
                stack.enter_context(patch.object(entry, "_capture_home_reference"))
                stack.enter_context(
                    patch.object(
                        entry, "_require_ui_state",
                        return_value="two_consecutive_in_memory_ooi_fingerprints",
                    )
                )
                stack.enter_context(patch.object(entry, "_print_event"))
                stack.enter_context(
                    patch("kancollebot.adapters.poi.visual_target.require_visual_dependencies")
                )
                stack.enter_context(
                    patch.object(
                        entry, "_probe_unlocked_fleet_ids",
                        return_value=(1, 2, 3, 4),
                    )
                )
                self.patch_evidence_sources(stack, marks=marks)
                with patch.object(monitor_runtime, "build_runtime",
                                  return_value=runtime) as build:
                    self.assertEqual(entry.run(args), 0)

            build.assert_called_once()
            kwargs = build.call_args.kwargs
            self.assertEqual(kwargs["unlocked_fleet_ids"], (1, 2, 3, 4))
            self.assertIs(kwargs["fleet_availability_strict"], True)
            self.assertIs(kwargs["task_kind"], TaskKind.EXPEDITION)
            self.assertEqual(kwargs["dispatch_fleet_ids"], (3,))
            self.assertEqual(set(kwargs["mission_requirements"]), {3})
            self.assertIsNotNone(kwargs["ui_input"])

    def test_production_supervisor_injects_every_controller_the_driver_can_execute(self):
        """R3: the composition root must wire the whole closed loop it can run.

        Both branches are now executable by the production driver: the supply
        click chain and, since 2026-11-05, the three reward-screen transitions
        (each reward screen advances on a click anywhere, so every step is one
        proven click). The supervisor therefore receives a real settlement
        controller, and the "unavailable" report must disappear rather than
        linger as a stale excuse.
        """
        return_at = datetime.now(timezone.utc) + timedelta(minutes=20)
        idle = make_snapshot()
        active = make_snapshot(status=1, mission_id=3, return_at=return_at)
        runtime, _, _ = self.make_runtime([idle, idle, active, active])
        marks = FakeMarks(selected=2)
        real_supervisor = supervisor_module.ContinuousExpeditionSupervisor
        built_supervisors: list[object] = []

        def capture_real_supervisor(*pos, **kw):
            instance = real_supervisor(*pos, **kw)
            built_supervisors.append(instance)
            return instance

        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "production" / "state.sqlite3"
            database.parent.mkdir(parents=True)
            args = self.make_args(database)
            game_session = MagicMock()
            game_session.input = FakeGameInput(marks=marks, fleet_after_confirm=2)
            game_session.__enter__.return_value = game_session
            game_session.__exit__.return_value = False

            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(entry, "connect_game_webview", return_value=game_session)
                )
                stack.enter_context(patch.object(entry, "_capture_home_reference"))
                stack.enter_context(
                    patch.object(
                        entry, "_require_ui_state",
                        return_value="two_consecutive_in_memory_ooi_fingerprints",
                    )
                )
                print_event = stack.enter_context(patch.object(entry, "_print_event"))
                stack.enter_context(
                    patch("kancollebot.adapters.poi.visual_target.require_visual_dependencies")
                )
                stack.enter_context(
                    patch.object(
                        entry, "_probe_unlocked_fleet_ids",
                        return_value=(1, 2, 3, 4),
                    )
                )
                self.patch_evidence_sources(stack, marks=marks)
                stack.enter_context(
                    patch.object(monitor_runtime, "build_runtime", return_value=runtime)
                )
                supervisor_cls = stack.enter_context(
                    patch.object(
                        supervisor_module, "ContinuousExpeditionSupervisor",
                        side_effect=capture_real_supervisor,
                    )
                )
                self.assertEqual(entry.run(args), 0)

            supervisor_cls.assert_called_once()
            assembly = supervisor_cls.call_args.kwargs
            self.assertEqual(
                set(assembly),
                {"monitor", "dispatch_controller", "settlement_controller", "resupply_controller"},
            )
            self.assertIs(assembly["monitor"], runtime.monitor)
            self.assertEqual(len(built_supervisors), 1)
            instance = built_supervisors[0]
            self.assertIsNotNone(instance.settlement_controller)
            self.assertIsNotNone(instance.resupply_controller)

            # Nothing is reported as missing, because nothing is.
            unavailable = [
                call for call in print_event.call_args_list
                if call.args and call.args[0] == "settlement_controller_unavailable"
            ]
            self.assertEqual(unavailable, [])

            # Every visible click workflow that IS wired must exclude the others.
            arbiter = instance.resupply_controller.ui_arbiter
            self.assertIs(instance.dispatch_controller.ui_arbiter, arbiter)
            self.assertIs(instance.settlement_controller.ui_arbiter, arbiter)
            self.assertIs(arbiter, assembly["dispatch_controller"].ui_arbiter)

            # The supervisor enforces these shares at construction time; assert
            # them here so a future refactor cannot quietly drop one.
            self.assertIs(instance.resupply_controller.scheduler, runtime.scheduler)
            self.assertIs(instance.settlement_controller.scheduler, runtime.scheduler)
            self.assertIs(
                instance.resupply_controller.event_sink, runtime.monitor.event_sink
            )
            self.assertIs(
                instance.settlement_controller.event_sink, runtime.monitor.event_sink
            )
            self.assertIs(instance.resupply_controller.allow_ui_actions, True)
            self.assertIs(instance.settlement_controller.allow_ui_actions, True)

            # The supply cost source stays the Redux-read snapshot joined with
            # the monitor's passive port witness, not a fabricated payload.
            provider = instance.resupply_controller.fleet_data_provider
            self.assertIsNotNone(provider.port_witness_provider)
            self.assertIs(provider.port_witness_provider, runtime.provider)

    def test_in_process_witness_is_armed_before_clicks_and_cancelled_on_exit(self):
        """#134: production owns the passive observer, not an external helper.

        The provider must be armed while the durable sink exists and BEFORE the
        first visible click, injected into the real settlement controller, and
        cancelled in the finally block. The node child is patched out, so this
        stays a purely offline composition proof.
        """
        return_at = datetime.now(timezone.utc) + timedelta(minutes=20)
        idle = make_snapshot()
        active = make_snapshot(status=1, mission_id=3, return_at=return_at)
        runtime, _, _ = self.make_runtime([idle, idle, active, active])
        marks = FakeMarks(selected=2)
        real_supervisor = supervisor_module.ContinuousExpeditionSupervisor
        built_supervisors: list[object] = []
        FakeWitnessProvider.instances = []
        FakeWitnessProvider.trace = []
        trace = FakeWitnessProvider.trace

        def capture_real_supervisor(*pos, **kw):
            instance = real_supervisor(*pos, **kw)
            built_supervisors.append(instance)
            return instance

        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "production" / "state.sqlite3"
            database.parent.mkdir(parents=True)
            args = self.make_args(database)
            args.in_process_witness = True
            game_session = MagicMock()
            game_input = FakeGameInput(marks=marks, fleet_after_confirm=2)
            real_click = game_input.click

            def traced_click(point):
                trace.append("click")
                return real_click(point)

            game_input.click = traced_click
            game_session.input = game_input
            game_session.__enter__.return_value = game_session
            game_session.__exit__.return_value = False

            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(entry, "connect_game_webview", return_value=game_session)
                )
                stack.enter_context(patch.object(entry, "_capture_home_reference"))
                stack.enter_context(
                    patch.object(
                        entry, "_require_ui_state",
                        return_value="two_consecutive_in_memory_ooi_fingerprints",
                    )
                )
                print_event = stack.enter_context(patch.object(entry, "_print_event"))
                stack.enter_context(
                    patch("kancollebot.adapters.poi.visual_target.require_visual_dependencies")
                )
                stack.enter_context(
                    patch.object(
                        entry, "_probe_unlocked_fleet_ids",
                        return_value=(1, 2, 3, 4),
                    )
                )
                self.patch_evidence_sources(stack, marks=marks)
                stack.enter_context(
                    patch.object(monitor_runtime, "build_runtime", return_value=runtime)
                )
                stack.enter_context(
                    patch.object(
                        supervisor_module, "ContinuousExpeditionSupervisor",
                        side_effect=capture_real_supervisor,
                    )
                )
                stack.enter_context(
                    patch(
                        "kancollebot.adapters.poi.mission_result_observer."
                        "PassiveMissionResultObserver",
                        FakeWitnessObserver,
                    )
                )
                stack.enter_context(
                    patch(
                        "kancollebot.expeditions.mission_result_witness_provider."
                        "MissionResultWitnessProvider",
                        FakeWitnessProvider,
                    )
                )
                self.assertEqual(entry.run(args), 0)

            names = [call.args[0] for call in print_event.call_args_list
                     if call.args]
            self.assertIn("in_process_witness_wired", names)
            self.assertIn("in_process_witness_stopped", names)
            # Arming must precede the first visible click of the run.
            self.assertIn("arm", trace)
            self.assertIn("click", trace)
            self.assertLess(trace.index("arm"), trace.index("click"))
            self.assertEqual(len(FakeWitnessProvider.instances), 1)
            provider = FakeWitnessProvider.instances[0]
            self.assertTrue(provider.armed)
            self.assertTrue(provider.cancelled)
            self.assertIs(provider.kwargs["event_log"], runtime.monitor.event_sink)
            self.assertIs(provider.kwargs["event_sink"], runtime.monitor.event_sink)
            self.assertEqual(provider.kwargs["expected_captures"], 1)
            # The REAL controller received it, not just a local variable.
            self.assertEqual(len(built_supervisors), 1)
            self.assertIs(
                built_supervisors[0].settlement_controller.witness_provider,
                provider,
            )

    def test_unfresh_monitor_observation_stops_before_any_ui_click(self):
        runtime, reader, provider = self.make_runtime([make_snapshot()], fresh=False)
        marks = FakeMarks(selected=2)
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "production" / "state.sqlite3"
            database.parent.mkdir(parents=True)
            args = self.make_args(database)
            game_session = MagicMock()
            game_session.input = FakeGameInput(marks=marks)
            game_session.__enter__.return_value = game_session
            game_session.__exit__.return_value = False

            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(entry, "connect_game_webview", return_value=game_session)
                )
                stack.enter_context(patch.object(entry, "_capture_home_reference"))
                stack.enter_context(
                    patch.object(
                        entry, "_require_ui_state",
                        return_value="two_consecutive_in_memory_ooi_fingerprints",
                    )
                )
                stack.enter_context(patch.object(entry, "_print_event"))
                stack.enter_context(
                    patch("kancollebot.adapters.poi.visual_target.require_visual_dependencies")
                )
                stack.enter_context(
                    patch.object(
                        entry, "_probe_unlocked_fleet_ids",
                        return_value=(1, 2, 3, 4),
                    )
                )
                stack.enter_context(
                    patch.object(monitor_runtime, "build_runtime", return_value=runtime)
                )
                self.patch_evidence_sources(stack, marks=marks)
                with self.assertRaisesRegex(entry.LoopError, "Poi observation is not trusted"):
                    entry.run(args)

            self.assertEqual(game_session.input.clicks, [])
            self.assertEqual(reader.read_count, 1)
            self.assertTrue(provider.closed)


    def test_an_ununlocked_fleet_4_is_refused_before_any_game_connection(self):
        """An account with only fleets 1/2/3 must fail closed on --fleet-id 4."""
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "production" / "state.sqlite3"
            database.parent.mkdir(parents=True)
            args = self.make_args(database)
            args.fleet_id = 4
            game_session = MagicMock()
            game_session.input = FakeGameInput()
            game_session.__enter__.return_value = game_session
            game_session.__exit__.return_value = False

            with (
                patch.object(entry, "connect_game_webview", return_value=game_session) as connect,
                patch.object(entry, "_print_event"),
                patch("kancollebot.adapters.poi.visual_target.require_visual_dependencies"),
                patch.object(entry, "_probe_unlocked_fleet_ids", return_value=(1, 2, 3)),
                patch.object(monitor_runtime, "build_runtime") as build,
            ):
                with self.assertRaisesRegex(entry.LoopError, "not unlocked on this account"):
                    entry.run(args)

            connect.assert_not_called()
            build.assert_not_called()
            self.assertEqual(game_session.input.clicks, [])

    def test_fleet_4_is_no_longer_statically_deferred(self):
        """On a four-fleet account, Fleet 4 reaches the composition gate.

        R3-I replaced the entry's private two-pair table with one versioned
        static rule table. A mission WITH a rule (3) is therefore no longer a
        Fleet-4 dead end, but a catalogue mission WITHOUT a rule (41) must
        still fail closed at the composition gate with zero clicks. The old
        static 'deferred' rejection is gone either way.
        """
        # The pure adapter now resolves Fleet 4 / mission 3 from the static
        # table; no live-verified Fleet-4 pair is needed any more.
        args = self.make_args(Path(tempfile.gettempdir()) / "unused.sqlite3")
        requirement = entry._mission_requirement(args, 4, 3)
        self.assertEqual((requirement.minimum_ships, requirement.minimum_flagship_level), (3, 3))

        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "production" / "state.sqlite3"
            database.parent.mkdir(parents=True)
            args = self.make_args(database)
            args.fleet_id = 4
            args.mission_id = 41  # in the catalogue, but has no static rule
            game_session = MagicMock()
            game_session.input = FakeGameInput()
            game_session.__enter__.return_value = game_session
            game_session.__exit__.return_value = False

            with (
                patch.object(entry, "connect_game_webview", return_value=game_session),
                patch.object(entry, "_print_event"),
                patch("kancollebot.adapters.poi.visual_target.require_visual_dependencies"),
                patch.object(entry, "_probe_unlocked_fleet_ids", return_value=(1, 2, 3, 4)),
                patch.object(monitor_runtime, "build_runtime") as build,
            ):
                with self.assertRaisesRegex(entry.LoopError, "no static requirement rule"):
                    entry.run(args)

            build.assert_not_called()
            self.assertEqual(game_session.input.clicks, [])


    def test_cli_enables_ui_refresh_by_default_and_can_disable_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "loop.jsonl"
            common = [
                "--authorize-ui-actions", "--fleet-id", "3", "--mission-id", "3",
                "--log-path", str(log),
            ]
            with (
                # _main_impl rebinds these module globals; restore them so a
                # later test never writes to a temporary directory that is gone.
                patch.object(entry, "LOG_PATH", entry.LOG_PATH),
                patch.object(entry, "RUN_ID", entry.RUN_ID),
                patch.object(entry, "HOME_REFERENCE_BASE64", entry.HOME_REFERENCE_BASE64),
                patch.object(entry, "_print_event"),
                patch.object(entry, "run", return_value=0) as run,
            ):
                self.assertEqual(entry._main_impl(common), 0)
                self.assertTrue(run.call_args.args[0].enable_ui_refresh)
                self.assertEqual(entry._main_impl([*common, "--no-ui-refresh"]), 0)
                self.assertFalse(run.call_args.args[0].enable_ui_refresh)


class RunLogStartPayloadTests(unittest.TestCase):
    """The closed-loop entry must write a durable run boundary (live 2026-11-06).

    Live evidence: the 02:02Z/02:08Z runs carried ``run_started`` only on
    stdout - the ledger had neither a run delimiter nor the
    ``correlation_capable`` field, while the monitor entry had both.
    """

    def test_the_payload_states_capability_and_joins_the_two_run_id_domains(self):
        payload = entry._run_log_start_payload(
            dispatch_fleet_ids=(3,),
            database=Path("prod.sqlite3"),
            ui_navigation_enabled=True,
            game_actions_enabled=True,
            tool_run_id="20261106T020811.000000Z",
            emitting_sink=lambda row: None,
        )
        self.assertEqual(payload["database"], str(Path("prod.sqlite3")))
        self.assertEqual(payload["dispatch_fleets"], [3])
        self.assertTrue(payload["ui_navigation_enabled"])
        self.assertTrue(payload["game_actions_enabled"])
        self.assertEqual(payload["tool_run_id"], "20261106T020811.000000Z")
        # A plain function sink cannot bind correlation: the payload says so
        # explicitly instead of leaving the fields absent.
        self.assertFalse(payload["correlation_capable"])
        self.assertEqual(payload["correlation_sources"], [])

    def test_a_correlation_capable_sink_reports_itself(self):
        class TeeWithCorrelation:
            def __call__(self, row: dict) -> None:
                return None

            def correlation(self, **updates: object) -> object:
                raise AssertionError("the payload must not enter the scope")

        payload = entry._run_log_start_payload(
            dispatch_fleet_ids=(),
            database="x.sqlite3",
            ui_navigation_enabled=False,
            game_actions_enabled=False,
            tool_run_id="t",
            emitting_sink=TeeWithCorrelation(),
        )
        self.assertTrue(payload["correlation_capable"])
        self.assertEqual(payload["correlation_sources"], ["TeeWithCorrelation"])

    def test_supply_settle_covers_the_live_measured_animation(self):
        # Live 2026-11-06 02:03Z: a ~1 s gap let the supply animation swallow
        # the return-home-port click entirely - the ledger recorded it "sent"
        # while the page never changed.
        from kancollebot.adapters.poi.continuous_expedition_ui import (
            SUPPLY_SETTLE_SECONDS,
        )

        self.assertGreaterEqual(SUPPLY_SETTLE_SECONDS, 4.0)



class RunStopLifecycleTests(unittest.TestCase):
    """Every exit of the closed-loop entry leaves a durable stop row.

    Live 2026-11-06: the entry wrote ``run_started`` (after the earlier fix)
    but never ``run_stopped``, so a reader could not tell a run that finished
    from one that died mid-tick. The monitor entry already stops its sink in
    a ``finally``; the closed-loop entry must do the same on all three exits.
    """

    COMMON = ["--authorize-ui-actions", "--fleet-id", "3", "--mission-id", "3"]

    def tearDown(self):
        # The stop helpers mutate a module-level one-slot holder; leave it
        # empty so no other test stops a sink that does not exist.
        entry._remember_run_log_sink(None)

    def test_the_stop_helper_is_a_noop_without_a_live_sink(self):
        entry._remember_run_log_sink(None)
        entry._stop_run_log("loop_completed")

    def test_the_stop_helper_hands_reason_and_fields_to_the_sink(self):
        recorded: list[tuple[str, dict]] = []

        class FakeSink:
            def stop(self, reason, fields=None):
                recorded.append((reason, dict(fields or {})))

        entry._remember_run_log_sink(FakeSink())
        entry._stop_run_log("loop_completed", tool_run_id="t")
        self.assertEqual(recorded, [("loop_completed", {"tool_run_id": "t"})])

    def test_a_broken_sink_never_masks_the_outcome(self):
        class BrokenSink:
            def stop(self, reason, fields=None):
                raise RuntimeError("disk gone")

        entry._remember_run_log_sink(BrokenSink())
        entry._stop_run_log("loop_crashed", error_type="RuntimeError")

    def _exits(self, run_patch, expect_code=None):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "loop.jsonl"
            with (
                patch.object(entry, "LOG_PATH", entry.LOG_PATH),
                patch.object(entry, "RUN_ID", entry.RUN_ID),
                patch.object(entry, "HOME_REFERENCE_BASE64", entry.HOME_REFERENCE_BASE64),
                patch.object(entry, "_print_event"),
                patch.object(entry, "_stop_run_log") as stop,
                patch.object(entry, "run", run_patch) as run,
            ):
                code = entry._main_impl([*self.COMMON, "--log-path", str(log)])
                if expect_code is not None:
                    self.assertEqual(code, expect_code)
            return stop, run

    def test_a_normal_return_stops_the_run_log_as_completed(self):
        stop, _ = self._exits(MagicMock(return_value=0), expect_code=0)
        (call,) = stop.call_args_list
        self.assertEqual(call.args[0], "loop_completed")
        self.assertTrue(call.kwargs["tool_run_id"])

    def test_a_fail_closed_exit_stops_the_run_log_with_the_error_type(self):
        stop, _ = self._exits(
            MagicMock(side_effect=entry.LoopError("gate refused")), expect_code=2
        )
        (call,) = stop.call_args_list
        self.assertEqual(call.args[0], "loop_failed_closed")
        self.assertEqual(call.kwargs["error_type"], "LoopError")
        self.assertTrue(call.kwargs["tool_run_id"])

    def test_an_uncontrolled_crash_stops_the_run_log_then_propagates(self):
        captured: list[object] = []
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "loop.jsonl"
            with (
                patch.object(entry, "LOG_PATH", entry.LOG_PATH),
                patch.object(entry, "RUN_ID", entry.RUN_ID),
                patch.object(entry, "HOME_REFERENCE_BASE64", entry.HOME_REFERENCE_BASE64),
                patch.object(entry, "_print_event"),
                patch.object(
                    entry, "_stop_run_log",
                    side_effect=lambda *a, **k: captured.append((a, k)),
                ),
                patch.object(entry, "run", MagicMock(side_effect=RuntimeError("boom"))),
            ):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    entry._main_impl([*self.COMMON, "--log-path", str(log)])
        ((args, kwargs),) = captured
        self.assertEqual(args[0], "loop_crashed")
        self.assertEqual(kwargs["error_type"], "RuntimeError")
        self.assertTrue(kwargs["tool_run_id"])



class RecordingRecoverySink:
    """A sink that records rows, and can be told to fail on chosen names."""

    def __init__(self, fail_on=()):
        self.rows: list[tuple[str, dict]] = []
        self.fail_on = frozenset(fail_on)

    def __call__(self, event, fields=None):
        if event in self.fail_on:
            raise RuntimeError("audit unavailable")
        self.rows.append((event, dict(fields or {})))


class HomePortRecoveryObservabilityTests(unittest.TestCase):
    """The recovery must be visible in the durable run log, and fail closed.

    Live 2026-11-06 02:08Z: a recovery ran (or did not - the log could not
    tell) because every row went to stdout only. With an event_sink the
    recovery leaves rows; a failed gate row must stop the click, like the
    driver's ``_click``.
    """

    def _sink_and_session(self):
        sink = RecordingRecoverySink()
        session = SimpleNamespace(input=FakeGameInput())
        return sink, session

    def _run_recovery(self, sink, session, require_side_effects):
        with (
            patch.object(entry, "_require_ui_state", side_effect=require_side_effects),
            patch.object(entry, "_reward_screen_on_any_layer", return_value=None),
            patch.object(
                entry, "home_port_candidates",
                return_value=[entry.RecordedPoint(50, 50, "home-port")],
            ),
            patch.object(entry.time, "sleep", lambda seconds: None),
            patch.object(entry, "_home_port_ui_layer_is_hidden", return_value=False),
            patch.object(entry, "_recover_home_port_by_observation") as observed,
        ):
            entry._recover_home_port(session, event_sink=sink)
        return observed

    def test_an_already_home_port_confirms_durably_without_clicks(self):
        sink, session = self._sink_and_session()
        self._run_recovery(sink, session, require_side_effects=[None])
        self.assertEqual(session.input.clicks, [])
        self.assertEqual(
            sink.rows, [("home_port_recovery_confirmed", {"attempt": 0})]
        )

    def test_a_reward_screen_refuses_without_any_click_row(self):
        sink, session = self._sink_and_session()
        with (
            patch.object(
                entry, "_require_ui_state",
                side_effect=entry.UiStateGateError("not home"),
            ),
            patch.object(
                entry, "_reward_screen_on_any_layer", return_value="BONUS_ITEMS"
            ),
        ):
            with self.assertRaisesRegex(entry.LoopError, "reward screen"):
                entry._recover_home_port(session, event_sink=sink)
        self.assertEqual(session.input.clicks, [])
        events = [event for event, _ in sink.rows]
        self.assertNotIn("home_port_recovery_click_attempted", events)

    def test_one_click_recovery_emits_attempted_before_sent(self):
        sink, session = self._sink_and_session()
        self._run_recovery(
            sink, session,
            require_side_effects=[entry.UiStateGateError("not home"), None],
        )
        self.assertEqual(len(session.input.clicks), 1)
        events = [event for event, _ in sink.rows]
        self.assertEqual(events, [
            "home_port_state_not_confirmed",
            "home_port_recovery_click_attempted",
            "home_port_recovery_click_sent",
            "home_port_recovery_confirmed",
        ])
        attempted = sink.rows[1][1]
        sent = sink.rows[2][1]
        self.assertEqual(attempted["attempt"], sent["attempt"])
        self.assertEqual(attempted["point"], sent["point"])

    def test_a_failed_gate_row_sends_no_click_at_all(self):
        sink, session = self._sink_and_session()
        sink.fail_on = frozenset({"home_port_recovery_click_attempted"})
        with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
            self._run_recovery(
                sink, session,
                require_side_effects=[entry.UiStateGateError("not home")],
            )
        self.assertEqual(session.input.clicks, [])

    def test_a_failed_diagnostic_row_does_not_break_recovery(self):
        sink, session = self._sink_and_session()
        sink.fail_on = frozenset({"home_port_recovery_confirmed"})
        self._run_recovery(
            sink, session,
            require_side_effects=[entry.UiStateGateError("not home"), None],
        )
        self.assertEqual(len(session.input.clicks), 1)
        events = [event for event, _ in sink.rows]
        self.assertNotIn("home_port_recovery_confirmed", events)
        self.assertIn("home_port_recovery_click_sent", events)

    def test_exhausted_attempts_fall_through_to_observed_recovery_with_the_sink(self):
        sink, session = self._sink_and_session()
        observed = self._run_recovery(
            sink, session,
            require_side_effects=entry.UiStateGateError("never home"),
        )
        self.assertEqual(observed.call_args.kwargs.get("event_sink"), sink)
        events = [event for event, _ in sink.rows]
        self.assertEqual(events.count("home_port_recovery_retry"), 3)



if __name__ == "__main__":
    unittest.main()
