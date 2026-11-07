"""Requirement A: the expedition-timer wait is ONE blocking sleep, not a poll.

Every assertion here is behavioural: injected ``sleep``/``monotonic`` callables
record what the sleeper asked for, and the process-wide probe counters record
what the rest of the process did.  Nothing in this file trusts a log line.
"""
from __future__ import annotations

import ast
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import one_expedition_ui_loop as entry  # noqa: E402
from kancollebot.expeditions.expedition_timer_wait import (  # noqa: E402
    DEFAULT_POST_EXPIRY_GRACE_SECONDS,
    ExpeditionTimerSleeper,
    FleetTimer,
    ProbeDuringSleepError,
    plan_timer_sleep,
)
from kancollebot.observability.probe_counter import (  # noqa: E402
    PROBE_COUNTER,
    PROBE_KINDS,
    ProbeCounter,
    mark_probe,
    total as probe_total,
)

NOW = 1_700_000_000_000


class TimerSleepPlanTests(unittest.TestCase):
    def test_the_earliest_unexpired_deadline_is_the_one_waited_for(self):
        plan = plan_timer_sleep(
            timers={2: NOW + 60_000, 3: NOW + 10_000, 4: NOW - 5_000},
            now_epoch_ms=NOW,
        )
        self.assertTrue(plan.should_sleep)
        self.assertEqual(plan.fleet_id, 3)
        self.assertEqual(plan.deadline_epoch_ms, NOW + 10_000)

    def test_the_grace_is_added_to_the_remaining_time(self):
        plan = plan_timer_sleep(
            timers={2: NOW + 10_000},
            now_epoch_ms=NOW,
            grace_seconds=3.0,
        )
        self.assertAlmostEqual(plan.sleep_seconds, 13.0)
        self.assertEqual(plan.grace_seconds, 3.0)
        self.assertEqual(DEFAULT_POST_EXPIRY_GRACE_SECONDS, 3.0)

    def test_an_expired_timer_does_not_produce_a_sleep(self):
        plan = plan_timer_sleep(timers={2: NOW - 1}, now_epoch_ms=NOW)
        self.assertFalse(plan.should_sleep)
        self.assertEqual(plan.reason, "timer_expired_refresh_may_start")

    def test_executable_pending_work_is_never_slept_past(self):
        plan = plan_timer_sleep(
            timers={2: NOW + 3_600_000},
            now_epoch_ms=NOW,
            pending_fleet_ids=(2,),
        )
        self.assertFalse(plan.should_sleep)
        self.assertEqual(plan.reason, "pending_work")
        self.assertEqual(plan.pending_fleet_ids, (2,))

    def test_the_cap_only_shortens_the_sleep(self):
        plan = plan_timer_sleep(
            timers={2: NOW + 3_600_000},
            now_epoch_ms=NOW,
            max_sleep_seconds=30.0,
        )
        self.assertTrue(plan.should_sleep)
        self.assertAlmostEqual(plan.sleep_seconds, 30.0)
        self.assertEqual(plan.reason, "timer_pending_bounded_by_max_sleep")

    def test_a_cap_longer_than_the_timer_changes_nothing(self):
        plan = plan_timer_sleep(
            timers={2: NOW + 5_000},
            now_epoch_ms=NOW,
            grace_seconds=3.0,
            max_sleep_seconds=600.0,
        )
        self.assertAlmostEqual(plan.sleep_seconds, 8.0)
        self.assertEqual(plan.reason, "timer_pending")

    def test_a_boolean_or_negative_grace_is_refused(self):
        for bad in (True, -1.0, float("nan")):
            with self.assertRaises(ValueError):
                plan_timer_sleep(timers={2: NOW + 1}, now_epoch_ms=NOW, grace_seconds=bad)


class TimerSleeperTests(unittest.TestCase):
    def test_a_clean_sleep_makes_exactly_one_blocking_call_and_no_probe(self):
        counter = ProbeCounter()
        slept: list[float] = []
        clock = iter([100.0, 112.5])

        sleeper = ExpeditionTimerSleeper(
            sleep=slept.append,
            monotonic=lambda: next(clock),
            probe_counter=counter,
        )
        plan = plan_timer_sleep(
            timers={2: NOW + 10_000}, now_epoch_ms=NOW, grace_seconds=2.0
        )
        result = sleeper.wait(plan)

        self.assertEqual(slept, [12.0])
        self.assertTrue(result.slept)
        self.assertEqual(probe_total(result.probe_calls_during_sleep), 0)
        self.assertEqual(result.end_reason, "timer_expired")
        self.assertFalse(result.timer_is_return_evidence)
        self.assertFalse(result.returned)
        self.assertEqual(result.dispatches_sent, 0)

    def test_a_probe_during_the_sleep_is_a_hard_error(self):
        """The invariant is enforced by counting, not by trusting the log."""
        counter = ProbeCounter()

        def probe_then_sleep(_seconds: float) -> None:
            counter.mark("poi")
            counter.mark("screenshot")

        sleeper = ExpeditionTimerSleeper(
            sleep=probe_then_sleep,
            monotonic=lambda: 0.0,
            probe_counter=counter,
        )
        plan = plan_timer_sleep(timers={2: NOW + 1_000}, now_epoch_ms=NOW)
        with self.assertRaises(ProbeDuringSleepError) as raised:
            sleeper.wait(plan)
        self.assertIn("poi", str(raised.exception))
        self.assertIn("screenshot", str(raised.exception))

    def test_every_probe_kind_is_detected(self):
        for kind in PROBE_KINDS:
            counter = ProbeCounter()
            sleeper = ExpeditionTimerSleeper(
                sleep=lambda _s, kind=kind: counter.mark(kind),
                monotonic=lambda: 0.0,
                probe_counter=counter,
            )
            plan = plan_timer_sleep(timers={2: NOW + 1_000}, now_epoch_ms=NOW)
            with self.assertRaises(ProbeDuringSleepError, msg=kind):
                sleeper.wait(plan)

    def test_the_cancel_event_wakes_the_sleep_without_any_probe(self):
        counter = ProbeCounter()
        cancel = threading.Event()
        cancel.set()
        waited: list[float] = []

        class _RecordingEvent:
            def wait(self, timeout=None):
                waited.append(timeout)
                return True

        sleeper = ExpeditionTimerSleeper(
            sleep=lambda _s: self.fail("the interval sleep must not be used when a cancel channel exists"),
            monotonic=lambda: 0.0,
            cancel_event=_RecordingEvent(),
            probe_counter=counter,
        )
        plan = plan_timer_sleep(timers={2: NOW + 60_000}, now_epoch_ms=NOW)
        result = sleeper.wait(plan)

        self.assertEqual(waited, [plan.sleep_seconds])
        self.assertEqual(result.end_reason, "shutdown_cancelled")
        self.assertEqual(probe_total(result.probe_calls_during_sleep), 0)
        self.assertFalse(result.timer_is_return_evidence)

    def test_the_run_log_fields_can_reconstruct_the_whole_sleep(self):
        rows: list[tuple[str, dict]] = []
        sleeper = ExpeditionTimerSleeper(
            sleep=lambda _s: None,
            monotonic=lambda: 0.0,
            probe_counter=ProbeCounter(),
            event_sink=lambda event, fields: rows.append((event, fields)),
        )
        plan = plan_timer_sleep(
            timers={2: NOW + 30_000, 3: NOW + 90_000},
            now_epoch_ms=NOW,
            grace_seconds=3.0,
            pending_fleet_ids=(),
        )
        sleeper.wait(plan)

        events = [event for event, _ in rows]
        self.assertEqual(events, [
            "expedition_timer_sleep_started",
            "expedition_timer_sleep_finished",
        ])
        started = rows[0][1]
        finished = rows[1][1]
        for field in (
            "fleet_id", "deadline_epoch_ms", "deadline_iso",
            "requested_sleep_seconds", "grace_seconds", "start_reason",
            "fleet_timers", "probe_calls_before_sleep",
            "timer_is_return_evidence", "dispatches_sent",
        ):
            self.assertIn(field, started)
        self.assertEqual(started["fleet_id"], 2)
        self.assertEqual(started["deadline_epoch_ms"], NOW + 30_000)
        self.assertEqual([item["fleet_id"] for item in started["fleet_timers"]], [2, 3])
        self.assertFalse(started["timer_is_return_evidence"])
        self.assertEqual(started["dispatches_sent"], 0)
        for field in (
            "actual_sleep_seconds", "end_reason", "probe_calls_during_sleep",
            "probe_calls_total_during_sleep", "probe_violation", "next_step",
        ):
            self.assertIn(field, finished)
        self.assertAlmostEqual(finished["requested_sleep_seconds"], 33.0)
        self.assertEqual(finished["probe_calls_total_during_sleep"], 0)
        self.assertFalse(finished["probe_violation"])
        self.assertEqual(
            finished["next_step"], "visible_sortie_home_refresh_then_fresh_poi_read"
        )

    def test_a_disabled_plan_reports_without_sleeping(self):
        slept: list[float] = []
        sleeper = ExpeditionTimerSleeper(
            sleep=slept.append,
            monotonic=lambda: 0.0,
            probe_counter=ProbeCounter(),
        )
        plan = plan_timer_sleep(
            timers={2: NOW + 10_000}, now_epoch_ms=NOW, enabled=False
        )
        result = sleeper.wait(plan)
        self.assertEqual(slept, [])
        self.assertFalse(result.slept)
        self.assertEqual(result.end_reason, "timer_wait_disabled")


def _runtime_with(timers: dict[int, int], phases: dict[int, str] | None = None):
    """A scheduler stub: only ``fleet(id)`` and the phase/mission it returns."""
    phases = phases or {}

    class _Scheduler:
        def fleet(self, fleet_id):
            if fleet_id not in timers:
                from kancollebot.expeditions.continuous_scheduler import (
                    SchedulerInvariantError,
                )

                raise SchedulerInvariantError(f"fleet {fleet_id} is not configured")
            return SimpleNamespace(
                phase=SimpleNamespace(value=phases.get(fleet_id, "EXPEDITION_ACTIVE")),
                mission=SimpleNamespace(final_time_ms=timers[fleet_id]),
            )

    return SimpleNamespace(scheduler=_Scheduler())


class ProductionWaitWiringTests(unittest.TestCase):
    """The sleep must be reachable from the real loop, not only from a unit test."""

    @staticmethod
    def _supervisor(pending: tuple[int, ...] = ()):
        return SimpleNamespace(
            ACTIVE_PHASES={"EXPEDITION_ACTIVE", "ACTIVE"},
            pending_actions=lambda: tuple(
                SimpleNamespace(fleet_id=fleet_id) for fleet_id in pending
            ),
        )

    def test_the_production_helper_sleeps_once_to_the_deadline_plus_grace(self):
        runtime = _runtime_with({2: NOW + 60_000})
        slept: list[float] = []
        with (
            patch.object(entry.time, "time", return_value=NOW / 1000.0),
            patch.object(entry.time, "sleep", side_effect=slept.append),
            patch.object(entry, "_print_event"),
        ):
            slept_kind = entry._wait_before_next_observation(
                SimpleNamespace(
                    timer_sleep=True,
                    post_expiry_grace_seconds=3.0,
                    max_timer_sleep_seconds=None,
                    tick_interval_seconds=10.0,
                ),
                ExpeditionTimerSleeper(
                    sleep=slept.append,
                    monotonic=lambda: 0.0,
                    probe_counter=ProbeCounter(),
                ),
                runtime=runtime,
                supervisor=self._supervisor(),
                target_fleet_ids=(2,),
                tick_index=1,
                tick_budget=60,
                wake_reason="return_not_observed",
            )
        self.assertEqual(slept_kind, "timer_sleep")
        self.assertEqual(len(slept), 1, slept)
        self.assertAlmostEqual(slept[0], 63.0, places=3)

    def test_pending_work_falls_back_to_the_interval_instead_of_sleeping_past_it(self):
        runtime = _runtime_with({2: NOW + 3_600_000})
        slept: list[float] = []
        with (
            patch.object(entry.time, "time", return_value=NOW / 1000.0),
            patch.object(entry.time, "sleep", side_effect=slept.append),
            patch.object(entry, "_print_event"),
        ):
            kind = entry._wait_before_next_observation(
                SimpleNamespace(
                    timer_sleep=True,
                    post_expiry_grace_seconds=3.0,
                    max_timer_sleep_seconds=None,
                    tick_interval_seconds=7.0,
                ),
                ExpeditionTimerSleeper(
                    sleep=slept.append,
                    monotonic=lambda: 0.0,
                    probe_counter=ProbeCounter(),
                ),
                runtime=runtime,
                supervisor=self._supervisor(pending=(2,)),
                target_fleet_ids=(2,),
                tick_index=2,
                tick_budget=60,
                wake_reason="no_trusted_observation",
            )
        self.assertEqual(kind, "interval_fallback")
        self.assertEqual(slept, [7.0])

    def test_an_expired_timer_only_applies_the_grace(self):
        runtime = _runtime_with({2: NOW - 1_000})
        slept: list[float] = []
        with (
            patch.object(entry.time, "time", return_value=NOW / 1000.0),
            patch.object(entry.time, "sleep", side_effect=slept.append),
            patch.object(entry, "_print_event"),
        ):
            kind = entry._wait_before_next_observation(
                SimpleNamespace(
                    timer_sleep=True,
                    post_expiry_grace_seconds=3.0,
                    max_timer_sleep_seconds=None,
                    tick_interval_seconds=10.0,
                ),
                ExpeditionTimerSleeper(
                    sleep=slept.append,
                    monotonic=lambda: 0.0,
                    probe_counter=ProbeCounter(),
                ),
                runtime=runtime,
                supervisor=self._supervisor(),
                target_fleet_ids=(2,),
                tick_index=1,
                tick_budget=60,
                wake_reason="return_not_observed",
            )
        self.assertEqual(kind, "post_expiry_grace")
        self.assertEqual(slept, [3.0])

    def test_the_settings_resolver_survives_a_mocked_namespace(self):
        from unittest.mock import MagicMock

        enabled, grace, cap, interval = entry._timer_wait_settings(MagicMock())
        self.assertTrue(enabled)
        self.assertEqual(grace, 3.0)
        self.assertIsNone(cap)
        self.assertEqual(interval, 10.0)

    def test_the_real_loop_no_longer_polls_with_the_tick_interval(self):
        """AST guard: the wait sites call the blocking helper, not time.sleep."""
        source = (ROOT / "tools" / "one_expedition_ui_loop.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        loop_fn = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_run_production_supervisor_once"
        )
        calls = [
            node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            for node in ast.walk(loop_fn)
            if isinstance(node, ast.Call)
        ]
        self.assertIn("_wait_before_next_observation", calls)
        # The only remaining bespoke sleeps in the production loop are the 3 s
        # grace inside the helper; the tick-interval poll is gone.
        direct_sleeps = [
            node
            for node in ast.walk(loop_fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "sleep"
            and node.args
            and isinstance(node.args[0], ast.Attribute)
            and node.args[0].attr == "tick_interval_seconds"
        ]
        self.assertEqual(direct_sleeps, [])

    def test_the_sleeper_is_the_real_one_used_by_the_loop(self):
        source = (ROOT / "tools" / "one_expedition_ui_loop.py").read_text(encoding="utf-8")
        self.assertIn("ExpeditionTimerSleeper(", source)
        self.assertIn("plan_timer_sleep(", source)
        self.assertIn("event_sink_from_stdout(_print_event)", source)


class ProbeCounterContractTests(unittest.TestCase):
    def test_snapshot_and_delta_only_report_new_calls(self):
        counter = ProbeCounter()
        counter.mark("poi")
        before = counter.snapshot()
        counter.mark("poi")
        counter.mark("cdp")
        delta = counter.delta(before)
        self.assertEqual(delta["poi"], 1)
        self.assertEqual(delta["cdp"], 1)
        self.assertEqual(probe_total(delta), 2)

    def test_the_process_default_counter_is_the_one_adapters_mark(self):
        before = PROBE_COUNTER.snapshot()
        mark_probe("poi")
        delta = PROBE_COUNTER.delta(before)
        self.assertEqual(delta["poi"], 1)

    def test_an_unknown_probe_name_is_ignored_not_counted(self):
        counter = ProbeCounter()
        counter.mark("not-a-probe")
        self.assertEqual(probe_total(counter.delta({})), 0)


if __name__ == "__main__":
    unittest.main()
