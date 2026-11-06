"""P0.2c-6: what a correlation scope gives back, and what it cannot reach.

Four claims, each one a place where "the log has a null" could otherwise mean
two different things:

**1. The scope reports itself.** ``correlation_scope`` yields the context that
actually took effect, so a caller does not have to re-derive it - and gets
``None`` when the sink could not bind one. Nesting inherits, an exception still
releases the scope.

**2. A tee hands back a real member's identity.** It owns none of its own: it
fans the scope out and reports the first member that took it, and reads back the
same way.

**3. ``contextvars`` do not cross a thread boundary - and that is kept.** This
module *proves* the limitation rather than papering over it: a row emitted on a
worker thread carries no ``dispatch_id`` even inside a scope. Carrying the
identity over is an explicit, opt-in act (``capture_correlation`` ->
``CorrelationSnapshot.rebind``), used by nobody in production, because the
background threads never share the ``event_sink`` in the first place. A scan
test keeps it that way.

**4. The supervisor mints a ``dispatch_id`` only when the sink can carry it.**
An id no row can hold is an identity claimed nowhere. When the capability is
missing the attempt still runs, ``dispatch_correlation_unavailable`` states the
degradation first, and the decision row records ``correlation_capable`` so every
later null has a stated premise.

Everything here is offline: temporary directories, fake scheduler, fake
controller, no Poi, no CDP, no click.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from kancollebot.expeditions.continuous_scheduler import FleetPhase  # noqa: E402
from kancollebot.expeditions.continuous_supervisor import (  # noqa: E402
    ContinuousExpeditionSupervisor,
)
from kancollebot.observability.event_envelope import (  # noqa: E402
    new_action_id,
    new_dispatch_id,
)
from kancollebot.observability.sink import (  # noqa: E402
    CorrelationSnapshot,
    TeeEventSink,
    build_run_log_sink,
    capture_correlation,
    correlation_capability,
    correlation_scope,
)
from tests.test_continuous_supervisor import (  # noqa: E402
    FakeMonitor,
    FakeScheduler,
)


def read_all_records(directory: Path) -> list[dict]:
    records: list[dict] = []
    for path in sorted(directory.glob("run-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


def plain_sink(rows: list[tuple[str, dict]]):
    """A callable with no correlation scope - the shape most tools and tests use."""

    def sink(event: str, fields) -> None:
        rows.append((event, dict(fields)))

    return sink


class ScopeReportsItselfTests(unittest.TestCase):
    """1. The context in force is handed back, or explicitly ``None``."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = Path(self._tmp.name)
        self.sink = build_run_log_sink(
            directory=self.directory, task_kind="expedition"
        )

    def test_the_scope_yields_the_context_it_bound(self):
        dispatch_id = new_dispatch_id()
        with correlation_scope(self.sink, dispatch_id=dispatch_id, fleet_id=4) as bound:
            self.assertEqual(bound.dispatch_id, dispatch_id)
            self.assertEqual(bound.fleet_id, 4)
            self.assertEqual(bound.run_id, self.sink.run_id)

    def test_a_nested_scope_inherits_and_only_replaces_its_own_slot(self):
        dispatch_id = new_dispatch_id()
        action_id = new_action_id()
        with correlation_scope(self.sink, dispatch_id=dispatch_id, fleet_id=4):
            with correlation_scope(self.sink, action_id=action_id) as inner:
                self.assertEqual(inner.dispatch_id, dispatch_id)
                self.assertEqual(inner.action_id, action_id)
                self.assertEqual(inner.fleet_id, 4)
            # Leaving the inner scope restores the outer one, not the base.
            self.assertIsNone(self.sink.context.action_id)
            self.assertEqual(self.sink.context.dispatch_id, dispatch_id)

    def test_the_scope_is_released_after_an_exception(self):
        dispatch_id = new_dispatch_id()
        with self.assertRaises(RuntimeError):
            with correlation_scope(self.sink, dispatch_id=dispatch_id):
                raise RuntimeError("boom")
        self.assertIsNone(self.sink.context.dispatch_id)

    def test_a_sink_without_a_scope_yields_none_instead_of_raising(self):
        rows: list[tuple[str, dict]] = []
        with correlation_scope(plain_sink(rows), dispatch_id=new_dispatch_id()) as bound:
            self.assertIsNone(bound)
        self.assertEqual(rows, [])

    def test_the_scope_still_works_on_a_sink_whose_scope_publishes_nothing(self):
        # Binding happened; the sink simply has no way to hand it back. The
        # caller gets None, which is why capability and readability are two
        # separate questions (see `capture_correlation`).
        class Quiet:
            def __init__(self) -> None:
                self.entered = 0

            def __call__(self, event, fields) -> None:
                pass

            def correlation(self, **updates):
                from contextlib import nullcontext

                self.entered += 1
                return nullcontext()

        sink = Quiet()
        self.assertTrue(correlation_capability(sink).capable)
        with correlation_scope(sink, fleet_id=4) as bound:
            self.assertIsNone(bound)
        self.assertEqual(sink.entered, 1)


class TeeHandsBackAMemberTests(unittest.TestCase):
    """2. A tee owns no identity; it reports the member that took it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = Path(self._tmp.name)
        self.run_log = build_run_log_sink(
            directory=self.directory, task_kind="expedition"
        )

    def test_the_tee_yields_the_identity_of_the_member_that_took_it(self):
        rows: list[tuple[str, dict]] = []
        tee = TeeEventSink(plain_sink(rows), self.run_log)
        dispatch_id = new_dispatch_id()
        with tee.correlation(dispatch_id=dispatch_id) as bound:
            self.assertEqual(bound.dispatch_id, dispatch_id)

    def test_the_tee_reads_back_what_it_would_report(self):
        rows: list[tuple[str, dict]] = []
        tee = TeeEventSink(plain_sink(rows), self.run_log)
        self.assertEqual(tee.context.run_id, self.run_log.run_id)
        dispatch_id = new_dispatch_id()
        with correlation_scope(tee, dispatch_id=dispatch_id):
            self.assertEqual(tee.context.dispatch_id, dispatch_id)
        self.assertIsNone(tee.context.dispatch_id)

    def test_a_tee_of_plain_functions_binds_nothing_and_says_so(self):
        rows: list[tuple[str, dict]] = []
        tee = TeeEventSink(plain_sink(rows), plain_sink(rows))
        self.assertFalse(correlation_capability(tee).capable)
        self.assertIsNone(tee.context)
        with correlation_scope(tee, dispatch_id=new_dispatch_id()) as bound:
            self.assertIsNone(bound)


class ContextDoesNotCrossAThreadTests(unittest.TestCase):
    """3. The limitation is a fact to be tested, not a bug to be hidden."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = Path(self._tmp.name)
        self.sink = build_run_log_sink(
            directory=self.directory, task_kind="expedition"
        )

    def run_on_a_thread(self, target) -> None:
        thread = threading.Thread(target=target)
        thread.start()
        thread.join()

    def test_a_worker_thread_does_not_inherit_the_scope(self):
        dispatch_id = new_dispatch_id()
        seen: dict[str, object] = {}

        def worker() -> None:
            seen["dispatch_id"] = self.sink.context.dispatch_id
            self.sink("worker_row", {"fleet_id": 4})

        with correlation_scope(self.sink, dispatch_id=dispatch_id):
            self.run_on_a_thread(worker)

        self.assertIsNone(seen["dispatch_id"], "contextvars must not leak across threads")
        row = [
            record
            for record in read_all_records(self.directory)
            if record["event"] == "worker_row"
        ][0]
        self.assertNotIn("dispatch_id", row)
        # The base identity still reaches it: run_id/task_kind are per sink,
        # not per thread, and a worker row is still attributable to the run.
        self.assertEqual(row["run_id"], self.sink.run_id)

    def test_capturing_the_identity_is_a_read_not_a_scope(self):
        dispatch_id = new_dispatch_id()
        with correlation_scope(self.sink, dispatch_id=dispatch_id, fleet_id=4):
            snapshot = capture_correlation(self.sink)
        # The scope has closed, but the snapshot still holds the attempt.
        self.assertIsInstance(snapshot, CorrelationSnapshot)
        self.assertTrue(snapshot.capable)
        self.assertEqual(snapshot.updates()["dispatch_id"], dispatch_id)
        self.assertEqual(snapshot.updates()["fleet_id"], 4)
        self.assertIsNone(self.sink.context.dispatch_id)

    def test_an_explicit_rebind_carries_it_and_closes_again(self):
        dispatch_id = new_dispatch_id()
        seen: dict[str, object] = {}

        with correlation_scope(self.sink, dispatch_id=dispatch_id, fleet_id=4):
            snapshot = capture_correlation(self.sink)

        def worker() -> None:
            with snapshot.rebind(self.sink) as bound:
                seen["bound"] = bound
                self.sink("worker_row", {"fleet_id": 4})
            seen["after"] = self.sink.context.dispatch_id

        self.run_on_a_thread(worker)

        self.assertEqual(seen["bound"].dispatch_id, dispatch_id)
        self.assertIsNone(seen["after"], "the rebind must close with its block")
        row = [
            record
            for record in read_all_records(self.directory)
            if record["event"] == "worker_row"
        ][0]
        self.assertEqual(row["dispatch_id"], dispatch_id)
        self.assertEqual(row["fleet_id"], 4)

    def test_an_empty_snapshot_rebinds_nothing_and_invents_nothing(self):
        snapshot = capture_correlation(self.sink)
        self.assertEqual(snapshot.updates(), {})
        seen: dict[str, object] = {}

        def worker() -> None:
            with snapshot.rebind(self.sink) as bound:
                seen["bound"] = bound
                self.sink("worker_row", {})

        self.run_on_a_thread(worker)

        self.assertIsNone(seen["bound"])
        row = [
            record
            for record in read_all_records(self.directory)
            if record["event"] == "worker_row"
        ][0]
        self.assertNotIn("dispatch_id", row)

    def test_capability_and_readability_are_answered_separately(self):
        rows: list[tuple[str, dict]] = []
        blind = capture_correlation(plain_sink(rows))
        self.assertFalse(blind.capable)
        self.assertIsNone(blind.context)
        self.assertEqual(blind.updates(), {})
        self.assertIn("no callable correlation scope", blind.reason)

        class CannotPublish:
            def __call__(self, event, fields) -> None:
                pass

            def correlation(self, **updates):
                from contextlib import nullcontext

                return nullcontext()

        unreadable = capture_correlation(CannotPublish())
        self.assertTrue(unreadable.capable, "it can bind...")
        self.assertIsNone(unreadable.context, "...but it cannot be read back")
        self.assertIn("publishes no readable context", unreadable.reason)

    def test_nothing_in_production_propagates_correlation_implicitly(self):
        # The opt-in helper must stay opt-in. If this list grows, someone
        # started copying one thread's identity onto another thread's rows.
        carriers = sorted(
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "src").rglob("*.py")
            if "capture_correlation" in path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            carriers,
            [
                "src/kancollebot/observability/__init__.py",
                "src/kancollebot/observability/sink.py",
            ],
        )


class EmittingController:
    """Emits one row inside the attempt and keeps the id it was handed."""

    def __init__(self, scheduler, state_reader, event_sink) -> None:
        self.scheduler = scheduler
        self.state_reader = state_reader
        self.event_sink = event_sink
        self.allow_ui_actions = True
        self.calls: list[int] = []
        self.dispatch_ids: list[str | None] = []

    def dispatch_one_ready(self, fleet_id, *, dispatch_id=None):
        self.calls.append(fleet_id)
        self.dispatch_ids.append(dispatch_id)
        if callable(self.event_sink):
            self.event_sink("dispatch_intent_persisted", {"fleet_id": fleet_id})
        state = self.scheduler.states[fleet_id]
        state.phase = FleetPhase.EXPEDITION_ACTIVE
        state.home_port_verified = True
        return SimpleNamespace(fleet_id=fleet_id)


class DispatchIdentityRequiresCapabilityTests(unittest.TestCase):
    """4. No id is minted for a sink that cannot carry one."""

    def build(self, sink):
        scheduler = FakeScheduler(
            {2: FleetPhase.READY, 3: FleetPhase.EXPEDITION_ACTIVE},
            {2: True, 3: True},
        )
        reader = object()
        monitor = FakeMonitor(scheduler, reader, sink)
        controller = EmittingController(scheduler, reader, sink)
        supervisor = ContinuousExpeditionSupervisor(
            monitor=monitor, dispatch_controller=controller
        )
        return supervisor, controller

    def test_a_capable_sink_gets_one_id_naming_one_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            sink = build_run_log_sink(directory=Path(tmp), task_kind="expedition")
            supervisor, controller = self.build(sink)
            supervisor.tick()

            (handed,) = controller.dispatch_ids
            self.assertIsNotNone(handed, "a capable sink earns an identity")
            records = read_all_records(Path(tmp))
            names = [record["event"] for record in records]
            self.assertNotIn("dispatch_correlation_unavailable", names)
            intent = [
                record for record in records if record["event"] == "dispatch_intent_persisted"
            ][0]
            self.assertEqual(intent["dispatch_id"], handed)
            tick = [record for record in records if record["event"] == "supervisor_tick"][0]
            self.assertIs(tick["correlation_capable"], True)
            self.assertNotIn("dispatch_id", tick, "the scope must not leak into the tick row")

    def test_an_incapable_sink_gets_no_id_and_the_dispatch_still_runs(self):
        rows: list[tuple[str, dict]] = []
        supervisor, controller = self.build(plain_sink(rows))
        result = supervisor.tick()

        self.assertIsNone(result.paused_reason)
        self.assertEqual(controller.calls, [2], "the dispatch is never blocked for this")
        self.assertEqual(controller.dispatch_ids, [None], "no id is minted for no sink")

        names = [name for name, _ in rows]
        self.assertIn("dispatch_intent_persisted", names)
        self.assertIn("dispatch_correlation_unavailable", names)
        unavailable = [fields for name, fields in rows if name == "dispatch_correlation_unavailable"][0]
        self.assertIs(unavailable["correlation_capable"], False)
        self.assertIs(unavailable["dispatch_allowed"], True)
        self.assertIn("no callable correlation scope", unavailable["reason"])
        # The degradation is stated before the attempt, not reconstructed later.
        self.assertLess(
            names.index("dispatch_correlation_unavailable"),
            names.index("dispatch_intent_persisted"),
        )
        tick = [fields for name, fields in rows if name == "supervisor_tick"][0]
        self.assertIs(tick["correlation_capable"], False)

    def test_a_monitor_without_a_sink_neither_mints_nor_complains(self):
        supervisor, controller = self.build(object())
        self.assertIsNone(supervisor.tick().paused_reason)
        self.assertEqual(controller.dispatch_ids, [None])

    def test_the_decision_row_states_capability_even_when_it_pauses(self):
        rows: list[tuple[str, dict]] = []
        sink = plain_sink(rows)
        scheduler = FakeScheduler({2: FleetPhase.READY, 3: FleetPhase.READY})
        reader = object()
        monitor = FakeMonitor(scheduler, reader, sink)
        controller = EmittingController(scheduler, reader, sink)
        controller.allow_ui_actions = False
        supervisor = ContinuousExpeditionSupervisor(
            monitor=monitor, dispatch_controller=controller
        )
        result = supervisor.tick()

        self.assertEqual(result.paused_reason, "ui_actions_disabled")
        paused = [fields for name, fields in rows if name == "supervisor_paused"][0]
        self.assertIs(paused["correlation_capable"], False)
        self.assertEqual(controller.calls, [], "a disabled controller is never called")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
