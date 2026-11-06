"""P0.2 run log: what happens when the log itself fails.

Three separate promises, each tested against the real object that makes it:

* an ordinary diagnostic that cannot be written is reported and the run goes on;
* a critical audit row that cannot be written stops the action it describes -
  proven on the real dispatch controller and on the real UI click primitive;
* a snapshot that cannot be replaced raises, and the previous snapshot survives.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from kancollebot.adapters.poi.continuous_expedition_ui import (  # noqa: E402
    CdpContinuousExpeditionUiDriver,
)
from kancollebot.expeditions.continuous_dispatch_controller import (  # noqa: E402
    ContinuousDispatchController,
)
from kancollebot.expeditions.continuous_scheduler import (  # noqa: E402
    ContinuousExpeditionScheduler,
    UiOperationArbiter,
)
from kancollebot.expeditions.dispatch_recovery import (  # noqa: E402
    DurableDispatchCoordinator,
    SqliteDispatchIntentStore,
)
from kancollebot.expeditions.fleet_state_store import SqliteFleetStateStore  # noqa: E402
from kancollebot.expeditions.mission_eligibility import (  # noqa: E402
    MissionCompositionRequirement,
)
from kancollebot.expeditions.settlement_drain import (  # noqa: E402
    DrainPageState,
    SettlementDrainRequest,
    SettlementDrainSession,
)
from kancollebot.observability.audit_policy import AuditLogUnavailable  # noqa: E402
from kancollebot.observability.run_log import (  # noqa: E402
    RunLogPolicy,
    RunLogStore,
    SnapshotWriteError,
)
from kancollebot.observability.sink import (  # noqa: E402
    TeeEventSink,
    build_run_log_sink,
)
from tests.test_continuous_dispatch_controller import (  # noqa: E402
    BASE,
    FakeClock,
    FakeUi,
    WitnessAwareReader,
    observation,
    snapshot,
)


class BrokenStore(RunLogStore):
    """A store whose every append fails, standing in for a full disk."""

    def __init__(self, *args, fail_on: str | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fail_on = fail_on
        self.attempts = 0

    def append(self, record, *, durable: bool = False):
        self.attempts += 1
        if self.fail_on is None or record.get("event") == self.fail_on:
            raise OSError(28, "No space left on device")
        return super().append(record, durable=durable)


class SinkFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make_sink(self, store: RunLogStore, **kwargs):
        errors: list[tuple[str, str]] = []

        def on_error(event, exc):
            errors.append((event, f"{type(exc).__name__}: {exc}"))

        sink = build_run_log_sink(
            directory=self.directory,
            task_kind="expedition",
            run_id="run-20261106T120000Z-0000000000aa",
            on_error=on_error,
            prune_interval_seconds=0,
        )
        sink._store = store  # noqa: SLF001 - injecting the failing store
        return sink, errors

    def test_a_diagnostic_write_failure_warns_and_the_run_continues(self):
        store = BrokenStore(RunLogPolicy(directory=self.directory))
        sink, errors = self.make_sink(store)
        sink("fleet_monitor_transition", {"fleet_id": 3})  # must not raise
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0][0], "fleet_monitor_transition")
        self.assertIn("No space left on device", errors[0][1])
        self.assertIsNotNone(sink.degraded_reason)

    def test_a_later_successful_diagnostic_write_does_not_clear_the_gap(self):
        store = RunLogStore(RunLogPolicy(directory=self.directory))
        sink, _ = self.make_sink(store)
        original_append = store.append
        failed = False

        def fail_once(record, *, durable=False):
            nonlocal failed
            if not failed:
                failed = True
                raise OSError(28, "No space left on device")
            return original_append(record, durable=durable)

        with patch.object(store, "append", side_effect=fail_once):
            sink("fleet_monitor_transition", {"fleet_id": 3})
            sink("supervisor_tick", {"dispatch_count": 0})

        self.assertIn("No space left on device", sink.degraded_reason or "")
        with self.assertRaises(AuditLogUnavailable):
            sink.ensure_writable()

    def test_a_degraded_sink_refuses_later_critical_events_after_storage_recovers(self):
        store = BrokenStore(
            RunLogPolicy(directory=self.directory),
            fail_on="fleet_monitor_transition",
        )
        sink, _ = self.make_sink(store)
        sink("fleet_monitor_transition", {"fleet_id": 3})
        store.fail_on = None
        attempts_before_critical = store.attempts

        with self.assertRaises(AuditLogUnavailable):
            sink("continuous_ui_click_attempted", {"action": "settlement"})

        self.assertEqual(store.attempts, attempts_before_critical)
        self.assertIsNotNone(sink.degraded_reason)

    def test_a_successful_stop_record_does_not_clear_a_critical_gap(self):
        store = BrokenStore(
            RunLogPolicy(directory=self.directory),
            fail_on="continuous_ui_click_attempted",
        )
        sink, _ = self.make_sink(store)
        with self.assertRaises(AuditLogUnavailable):
            sink("continuous_ui_click_attempted", {"action": "dispatch"})

        sink._store = RunLogStore(RunLogPolicy(directory=self.directory))  # noqa: SLF001
        sink.stop("audit_failed")
        with self.assertRaises(AuditLogUnavailable):
            sink.ensure_writable()

    def test_invalid_critical_event_name_still_fails_closed(self):
        sink, _ = self.make_sink(RunLogStore(RunLogPolicy(directory=self.directory)))
        with self.assertRaises(AuditLogUnavailable):
            sink("dispatch_intent_persisted ", {"fleet_id": 4})

    def test_a_critical_write_failure_raises_instead_of_pretending(self):
        store = BrokenStore(RunLogPolicy(directory=self.directory))
        sink, errors = self.make_sink(store)
        with self.assertRaises(AuditLogUnavailable):
            sink("continuous_ui_click_attempted", {"action": "dispatch"})
        self.assertEqual(len(errors), 1)
        self.assertIsNotNone(sink.degraded_reason)

    def test_the_gate_stays_closed_until_a_new_run_starts(self):
        store = BrokenStore(RunLogPolicy(directory=self.directory))
        sink, _ = self.make_sink(store)
        with self.assertRaises(AuditLogUnavailable):
            sink("dispatch_intent_persisted", {"fleet_id": 4})
        with self.assertRaises(AuditLogUnavailable):
            sink.ensure_writable()

        # A repair does not silently re-open the same sink: the run that lost a
        # row must not keep acting as if its log were complete.
        healthy = RunLogStore(RunLogPolicy(directory=self.directory))
        sink._store = healthy  # noqa: SLF001
        with self.assertRaises(AuditLogUnavailable):
            sink.ensure_writable()

        # A fresh run (new process, new run_id) starts clean.
        replacement = build_run_log_sink(
            directory=self.directory,
            task_kind="expedition",
            run_id="run-20261106T120000Z-0000000000ac",
            prune_interval_seconds=0,
        )
        replacement.ensure_writable()
        replacement("supervisor_tick", {"dispatch_count": 0})
        self.assertIsNone(replacement.degraded_reason)
        self.assertEqual(len(healthy.read_events()), 1)

    def test_the_gate_refuses_when_the_directory_is_not_writable(self):
        store = BrokenStore(RunLogPolicy(directory=self.directory))
        sink, _ = self.make_sink(store)
        with patch.object(RunLogStore, "probe", side_effect=PermissionError("denied")):
            with self.assertRaises(AuditLogUnavailable):
                sink.ensure_writable()
        self.assertIn("denied", sink.degraded_reason or "")

    def test_a_snapshot_failure_keeps_the_previous_snapshot_and_raises(self):
        sink = build_run_log_sink(
            directory=self.directory,
            task_kind="expedition",
            run_id="run-20261106T120000Z-0000000000ab",
            prune_interval_seconds=0,
        )
        sink.write_snapshot({"phase": "IDLE"})
        before = (self.directory / "current_state.json").read_text(encoding="utf-8")

        with patch("kancollebot.observability.run_log.os.replace", side_effect=OSError(5, "I/O error")):
            with self.assertRaises(SnapshotWriteError):
                sink.write_snapshot({"phase": "DISPATCHING"})

        after = (self.directory / "current_state.json").read_text(encoding="utf-8")
        self.assertEqual(before, after)
        self.assertEqual(json.loads(after)["phase"], "IDLE")
        self.assertEqual(list(self.directory.glob("*.tmp")), [])
        self.assertIn("I/O error", sink.last_snapshot_error or "")


class DispatchGateTests(unittest.TestCase):
    def make_controller(self, directory, reader, ui, gate):
        events: list[str] = []
        db_path = Path(directory) / "state.sqlite3"
        scheduler = ContinuousExpeditionScheduler(state_store=SqliteFleetStateStore(db_path))
        intent_store = SqliteDispatchIntentStore(db_path)
        coordinator = DurableDispatchCoordinator(scheduler, intent_store)
        controller = ContinuousDispatchController(
            scheduler=scheduler,
            dispatch_coordinator=coordinator,
            state_reader=reader,
            ui_driver=ui,
            ui_arbiter=UiOperationArbiter(),
            mission_requirements={
                2: MissionCompositionRequirement(1, 2, 1),
                3: MissionCompositionRequirement(3, 3, 3),
            },
            event_sink=lambda name, fields: events.append(name),
            allow_ui_actions=True,
            max_state_age=timedelta(seconds=30),
            clock=FakeClock(
                BASE,
                BASE + timedelta(seconds=2),
                BASE + timedelta(seconds=3),
                BASE + timedelta(seconds=4),
                BASE + timedelta(seconds=5),
            ),
            passive_witness_wait_seconds=0,
            audit_gate=gate,
        )
        return controller, intent_store, events

    @staticmethod
    def observations():
        ships = (1, 2)
        return_at = BASE + timedelta(minutes=20)
        return (
            observation(BASE - timedelta(seconds=1), [snapshot(2, ship_ids=ships), snapshot(3)]),
            observation(BASE + timedelta(seconds=3), [
                snapshot(2, status=1, mission_id=1, return_at=return_at, ship_ids=ships),
                snapshot(3),
            ]),
            observation(BASE + timedelta(seconds=5), [
                snapshot(2, status=1, mission_id=1, return_at=return_at, ship_ids=ships),
                snapshot(3),
            ]),
        )

    def test_a_broken_audit_log_blocks_the_dispatch_before_any_state_is_written(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = FakeUi()
            reader = WitnessAwareReader(ui, *self.observations())

            def gate():
                raise AuditLogUnavailable("run log is degraded: disk full")

            controller, _intents, events = self.make_controller(directory, reader, ui, gate)

            with self.assertRaises(AuditLogUnavailable):
                controller.dispatch_one_ready(2)

            self.assertEqual(ui.dispatches, [], "no UI input may be sent")
            self.assertEqual(ui.home_calls, [])
            self.assertEqual(reader.read_count, 0, "no Poi read either")
            self.assertEqual(events, [], "the gate ran before anything was evaluated")
            self.assertNotIn("dispatch_intent_persisted", events)

    def test_a_configuration_without_a_gate_behaves_exactly_as_before(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = FakeUi()
            reader = WitnessAwareReader(ui, *self.observations())
            controller, _intents, events = self.make_controller(directory, reader, ui, None)

            result = controller.dispatch_one_ready(2)

            self.assertEqual(result.fleet_id, 2)
            self.assertEqual(ui.dispatches, [(2, 1)])
            self.assertIn("dispatch_intent_persisted", events)
            self.assertIn("dispatch_confirmed_by_poi", events)

    def test_the_gate_must_be_callable_when_supplied(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "state.sqlite3"
            scheduler = ContinuousExpeditionScheduler(state_store=SqliteFleetStateStore(db_path))
            with self.assertRaises(ValueError):
                ContinuousDispatchController(
                    scheduler=scheduler,
                    dispatch_coordinator=DurableDispatchCoordinator(
                        scheduler, SqliteDispatchIntentStore(db_path)
                    ),
                    state_reader=WitnessAwareReader(FakeUi()),
                    ui_driver=FakeUi(),
                    ui_arbiter=UiOperationArbiter(),
                    mission_requirements={2: MissionCompositionRequirement(1, 2, 1)},
                    event_sink=lambda name, fields: None,
                    audit_gate="not-callable",
                )


class RealClickPathTests(unittest.TestCase):
    """The click primitive must abort when its audit row cannot be written."""

    def make_driver(self, event_sink):
        class Input:
            def __init__(self):
                self.clicks = []

            def click(self, point):
                self.clicks.append(point)

        game_input = Input()
        driver = CdpContinuousExpeditionUiDriver(
            game_input=game_input,
            verify_state=lambda state: None,
            event_sink=event_sink,
            details=SimpleNamespace(reference=lambda *a: None, require=lambda *a: None),
            marks=SimpleNamespace(
                require_decision=lambda *a: None,
                require_fleet=lambda *a: None,
                fleets=lambda: (2, 3),
            ),
            capture=lambda: "",
            sleep=lambda seconds: None,
            settle_seconds=0.0,
        )
        return driver, game_input

    def test_a_failing_critical_row_prevents_the_click_and_is_not_retried(self):
        calls: list[str] = []

        def sink(event, fields):
            calls.append(event)
            if event == "continuous_ui_click_attempted":
                raise AuditLogUnavailable("run log is degraded: disk full")

        driver, game_input = self.make_driver(sink)
        point = SimpleNamespace(label="dispatch-button")

        with self.assertRaises(AuditLogUnavailable):
            driver._click(point, action="expedition_start", step=1)  # noqa: SLF001

        self.assertEqual(game_input.clicks, [])
        self.assertEqual(calls, ["continuous_ui_click_attempted"])

    def test_a_degraded_tee_blocks_click_before_primary_event_append(self):
        store = BrokenStore(
            RunLogPolicy(directory=self.directory),
            fail_on="fleet_monitor_transition",
        )
        run_sink, _ = SinkFailureTests().make_sink(store)
        primary_events: list[str] = []

        def primary(event, fields):
            primary_events.append(event)
            return len(primary_events)

        tee = TeeEventSink(primary, run_sink)
        tee("fleet_monitor_transition", {"fleet_id": 4})
        store.fail_on = None

        driver, game_input = self.make_driver(tee)
        with self.assertRaises(AuditLogUnavailable):
            driver._click(  # noqa: SLF001
                SimpleNamespace(label="settlement-advance"),
                action="advance_settlement",
                step=1,
            )

        self.assertEqual(primary_events, ["fleet_monitor_transition"])
        self.assertEqual(game_input.clicks, [])

    def test_a_healthy_sink_still_emits_attempted_then_sent(self):
        calls: list[str] = []

        def sink(event, fields):
            calls.append(event)

        driver, game_input = self.make_driver(sink)
        driver._click(SimpleNamespace(label="dispatch-button"), action="expedition_start", step=1)  # noqa: SLF001

        self.assertEqual(len(game_input.clicks), 1)
        self.assertEqual(calls, ["continuous_ui_click_attempted", "continuous_ui_click_sent"])


class DrainFailureTests(unittest.TestCase):
    class ScriptedUi:
        def __init__(self, sequence):
            self.sequence = list(sequence)
            self.index = 0
            self.actions = []

        def observe(self):
            return self.sequence[self.index]

        def advance(self, page):
            self.actions.append(page)
            if self.index + 1 >= len(self.sequence):
                raise AssertionError("advance past the end of the script")
            self.index += 1

    class NoWaitClock:
        def __init__(self):
            self.now = 0.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    def test_a_failing_action_row_stops_the_drain_before_the_click(self):
        ui = self.ScriptedUi([DrainPageState.RETURN_NOTICE, DrainPageState.HOME_PORT])
        events: list[str] = []

        def sink(event, fields):
            events.append(event)
            if event == "settlement_drain_action":
                raise AuditLogUnavailable("run log is degraded")

        session = SettlementDrainSession(ui, ui, clock=self.NoWaitClock(), event_sink=sink)
        request = SettlementDrainRequest(
            max_actions=3,
            timeout_seconds=30.0,
            action_interval_seconds=5.0,
            stable_frame_count=1,
        )
        with self.assertRaises(AuditLogUnavailable):
            session.drain(request)
        self.assertEqual(ui.actions, [])
        self.assertEqual(events, ["settlement_drain_started", "settlement_drain_action"])

    def test_a_healthy_sink_records_the_whole_drain_on_one_action_per_page(self):
        ui = self.ScriptedUi([
            DrainPageState.RETURN_NOTICE,
            DrainPageState.EXPEDITION_RESULT,
            DrainPageState.REWARD_PANEL,
            DrainPageState.HOME_PORT,
        ])
        events: list[tuple[str, dict]] = []
        session = SettlementDrainSession(
            ui, ui, clock=self.NoWaitClock(),
            event_sink=lambda event, fields: events.append((event, fields)),
        )
        result = session.drain(SettlementDrainRequest(
            max_actions=5,
            timeout_seconds=30.0,
            action_interval_seconds=5.0,
            stable_frame_count=1,
        ))

        names = [name for name, _ in events]
        self.assertEqual(names.count("settlement_drain_action"), 3)
        self.assertEqual(names.count("settlement_drain_page_changed"), 3)
        self.assertEqual(names[0], "settlement_drain_started")
        self.assertEqual(names[-1], "settlement_drain_finished")
        self.assertEqual(events[-1][1]["reached_home"], True)
        self.assertEqual(events[-1][1]["completed_pages"], 3)
        self.assertEqual(result.actions_performed, 3)
        self.assertEqual(
            [fields["page"] for name, fields in events if name == "settlement_drain_action"],
            ["return_notice", "expedition_result", "reward_panel"],
        )


if __name__ == "__main__":
    unittest.main()
