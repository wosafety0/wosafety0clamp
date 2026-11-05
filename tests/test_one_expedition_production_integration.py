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

    def capture_screenshot(self):
        self.frames += 1
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

        It must still fail closed there: no Fleet-4 composition requirement
        has been verified live, and inventing one would be a dispatch decision
        made from a guess. What matters here is that the old static 'deferred'
        rejection is gone and the account-fact gate admitted the fleet.
        """
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
                patch.object(entry, "connect_game_webview", return_value=game_session),
                patch.object(entry, "_print_event"),
                patch("kancollebot.adapters.poi.visual_target.require_visual_dependencies"),
                patch.object(entry, "_probe_unlocked_fleet_ids", return_value=(1, 2, 3, 4)),
                patch.object(monitor_runtime, "build_runtime") as build,
            ):
                with self.assertRaisesRegex(entry.LoopError, "no verified composition requirement"):
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


if __name__ == "__main__":
    unittest.main()
