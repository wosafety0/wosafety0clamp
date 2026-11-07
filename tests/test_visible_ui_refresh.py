from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from kancollebot.adapters.poi.cdp_input import CdpInputError
from kancollebot.adapters.poi.visible_ui_refresh import (
    EXPEDITION_MODE,
    HOME_PORT,
    SORTIE,
    FreshnessRefreshingStateReader,
    VisibleUiPortRefreshDriver,
    VisibleUiRefreshError,
)
from kancollebot.core.observed_state import ObservedState
from kancollebot.expeditions.continuous_dispatch_controller import UiStateEvidence
from kancollebot.expeditions.continuous_scheduler import UiOperationArbiter

try:  # `unittest discover -s tests` puts this directory on sys.path
    from home_port_locator_stub import STUB_HOME_PORT_LOCATOR
except ImportError:  # `-m unittest tests.test_...` imports it as a package
    from tests.home_port_locator_stub import STUB_HOME_PORT_LOCATOR


class FakeInput:
    def __init__(self, fail_at: int | None = None):
        self.clicks = []
        self.fail_at = fail_at

    def click(self, point):
        self.clicks.append(point)
        if self.fail_at == len(self.clicks):
            raise RuntimeError("ambiguous click")


class FakeReader:
    def __init__(self, observations):
        self.observations = list(observations)
        self.calls = 0

    def read_fleets(self):
        self.calls += 1
        if len(self.observations) > 1:
            return self.observations.pop(0)
        return self.observations[0]


def observation(fresh: bool, *, observed_at: datetime | None = None):
    return SimpleNamespace(
        source_freshness_verified=fresh,
        observed_at=observed_at or datetime.now(timezone.utc),
    )


class VisibleUiPortRefreshDriverTests(unittest.TestCase):
    def make_driver(self, states, *, fail_click_at=None):
        game_input = FakeInput(fail_at=fail_click_at)
        events = []
        sleeps = []

        def verify(expected):
            actual = states.pop(0)
            if isinstance(actual, Exception):
                raise actual
            return UiStateEvidence(
                state=actual,
                consecutive_states=(actual, actual),
                evidence="two_consecutive_test_frames",
            )

        driver = VisibleUiPortRefreshDriver(
            home_port_locator=STUB_HOME_PORT_LOCATOR,
            game_input=game_input,
            verify_state=verify,
            ui_arbiter=UiOperationArbiter(),
            event_sink=lambda event, fields: events.append((event, dict(fields))),
            sleep=sleeps.append,
            settle_seconds=1.0,
        )
        return driver, game_input, events, sleeps

    def test_refresh_uses_only_the_verified_navigation_sequence(self):
        driver, game_input, events, sleeps = self.make_driver(
            ["HOME_PORT", "SORTIE_MENU", "EXPEDITION_LIST", "HOME_PORT"]
        )

        result = driver.refresh_port_state()

        self.assertEqual(
            game_input.clicks,
            [SORTIE, EXPEDITION_MODE, HOME_PORT],
        )
        self.assertEqual(sleeps, [1.0, 1.0, 1.0])
        self.assertEqual(result.state, "HOME_PORT")
        self.assertEqual(result.consecutive_states, ("HOME_PORT", "HOME_PORT"))
        self.assertEqual(events[-1][0], "ui_refresh_completed")
        self.assertEqual(events[-1][1]["clicks_sent"], 3)

    def test_initial_state_failure_sends_no_click(self):
        driver, game_input, events, _ = self.make_driver(["UNKNOWN"])

        with self.assertRaises(VisibleUiRefreshError):
            driver.refresh_port_state()

        self.assertEqual(game_input.clicks, [])
        self.assertEqual(events[-1][0], "ui_refresh_blocked")
        self.assertEqual(events[-1][1]["clicks_attempted"], 0)
        self.assertEqual(events[-1][1]["clicks_completed"], 0)

    def test_initial_cdp_error_records_only_safe_method_and_code(self):
        diagnostic_error = CdpInputError(
            "CDP command failed: Accessibility.getFullAXTree (code=-32000)"
        )
        driver, game_input, events, _ = self.make_driver([diagnostic_error])

        with self.assertRaises(VisibleUiRefreshError):
            driver.refresh_port_state()

        fields = events[-1][1]
        self.assertEqual(fields["diagnostic_category"], "cdp_error_response")
        self.assertEqual(fields["cdp_method"], "Accessibility.getFullAXTree")
        self.assertEqual(fields["cdp_error_code"], -32000)
        self.assertEqual(game_input.clicks, [])
        self.assertNotIn("message", fields)
        self.assertNotIn("sensitive", str(fields))

    def test_failed_post_click_checkpoint_stops_without_retry(self):
        driver, game_input, events, _ = self.make_driver(
            ["HOME_PORT", "UNKNOWN"]
        )

        with self.assertRaises(VisibleUiRefreshError):
            driver.refresh_port_state()

        self.assertEqual(game_input.clicks, [SORTIE])
        self.assertEqual(events[-1][0], "ui_refresh_blocked")
        self.assertEqual(events[-1][1]["clicks_attempted"], 1)
        self.assertEqual(events[-1][1]["clicks_completed"], 1)
        self.assertFalse(events[-1][1]["outcome_ambiguous"])

    def test_a_click_that_provably_did_nothing_is_repeated_once(self):
        # Live 2026-11-05: a 出撃 click landed during a transition and the client
        # stayed on HOME_PORT. The precondition being re-proven afterwards makes
        # that a *known* no-op, not an ambiguous outcome, so one repeat is safe.
        driver, game_input, events, _ = self.make_driver(
            [
                "HOME_PORT",       # initial precondition
                "HOME_PORT",       # after the click: nothing happened
                "HOME_PORT",       # precondition re-proven
                "SORTIE_MENU",     # after the repeat
                "EXPEDITION_LIST",
                "HOME_PORT",
            ]
        )

        result = driver.refresh_port_state()

        self.assertEqual(
            game_input.clicks, [SORTIE, SORTIE, EXPEDITION_MODE, HOME_PORT]
        )
        self.assertEqual(result.state, "HOME_PORT")
        names = [name for name, _ in events]
        self.assertIn("ui_refresh_click_repeated", names)
        repeated = next(fields for name, fields in events if name == "ui_refresh_click_repeated")
        self.assertEqual(repeated["target"], SORTIE.label)
        self.assertEqual(repeated["state_after_first_click"], "HOME_PORT")
        self.assertEqual(events[-1][0], "ui_refresh_completed")
        self.assertEqual(events[-1][1]["clicks_sent"], 4)

    def test_a_click_that_changed_the_screen_is_never_repeated(self):
        # The screen moved, so the outcome is ambiguous: no second click, by
        # design. This is the line the repeat must not cross.
        driver, game_input, events, _ = self.make_driver(
            ["HOME_PORT", "UNKNOWN", "EXPEDITION_LIST"]
        )

        with self.assertRaises(VisibleUiRefreshError):
            driver.refresh_port_state()

        self.assertEqual(game_input.clicks, [SORTIE])
        self.assertEqual(events[-1][0], "ui_refresh_blocked")
        self.assertNotIn("ui_refresh_click_repeated", [name for name, _ in events])

    def test_ambiguous_click_is_not_replayed(self):
        driver, game_input, events, _ = self.make_driver(
            ["HOME_PORT"], fail_click_at=1
        )

        with self.assertRaises(VisibleUiRefreshError):
            driver.refresh_port_state()

        self.assertEqual(game_input.clicks, [SORTIE])
        self.assertEqual(events[-1][1]["clicks_attempted"], 1)
        self.assertEqual(events[-1][1]["clicks_completed"], 0)
        self.assertTrue(events[-1][1]["outcome_ambiguous"])


class FreshnessRefreshingStateReaderTests(unittest.TestCase):
    def test_already_fresh_observation_does_not_navigate(self):
        reader = FakeReader([observation(True)])
        driver = SimpleNamespace(refresh_port_state=lambda: self.fail("unexpected refresh"))
        wrapper = FreshnessRefreshingStateReader(reader, driver)

        result = wrapper.ensure_fresh(origin="test_reader")

        self.assertTrue(result.source_freshness_verified)
        self.assertEqual(reader.calls, 1)

    def test_unverified_observation_refreshes_then_waits_for_matching_witness(self):
        reader = FakeReader([
            observation(False),
            observation(False),
            observation(True),
        ])
        refresh_calls = []
        driver = SimpleNamespace(refresh_port_state=lambda: refresh_calls.append("refresh"))
        clock = [0.0]
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds

        wrapper = FreshnessRefreshingStateReader(
            reader,
            driver,
            max_wait_seconds=2.0,
            poll_interval_seconds=0.5,
            refresh_cooldown_seconds=10.0,
            monotonic=lambda: clock[0],
            sleep=sleep,
        )

        result = wrapper.ensure_fresh(origin="test_reader")

        self.assertTrue(result.source_freshness_verified)
        self.assertEqual(refresh_calls, ["refresh"])
        self.assertEqual(reader.calls, 3)
        self.assertEqual(sleeps, [0.5])

    def test_timeout_returns_unverified_observation_without_claiming_success(self):
        reader = FakeReader([observation(False)])
        refresh_calls = []
        driver = SimpleNamespace(refresh_port_state=lambda: refresh_calls.append("refresh"))
        clock = [0.0]

        def sleep(seconds):
            clock[0] += seconds

        wrapper = FreshnessRefreshingStateReader(
            reader,
            driver,
            max_wait_seconds=1.0,
            poll_interval_seconds=0.5,
            rebuild_max_attempts=1,
            monotonic=lambda: clock[0],
            sleep=sleep,
        )

        result = wrapper.ensure_fresh(origin="test_reader")

        self.assertFalse(result.source_freshness_verified)
        self.assertEqual(refresh_calls, ["refresh"])
        self.assertEqual(reader.calls, 3)

    def test_cooldown_prevents_repeating_an_ambiguous_navigation(self):
        """The cooldown is waited out, never skipped or replayed immediately."""
        reader = FakeReader([observation(False)])
        refresh_calls = []
        refresh_at = []
        clock = [0.0]

        def refresh():
            refresh_calls.append("refresh")
            refresh_at.append(clock[0])

        driver = SimpleNamespace(refresh_port_state=refresh)
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds

        wrapper = FreshnessRefreshingStateReader(
            reader,
            driver,
            max_wait_seconds=0.5,
            poll_interval_seconds=0.5,
            refresh_cooldown_seconds=10.0,
            rebuild_max_attempts=1,
            monotonic=lambda: clock[0],
            sleep=sleep,
        )

        first = wrapper.ensure_fresh(origin="test_reader")
        second = wrapper.ensure_fresh(origin="test_reader")

        self.assertFalse(first.source_freshness_verified)
        self.assertFalse(second.source_freshness_verified)
        # A second navigation is allowed only after the remaining cooldown has
        # actually elapsed; it is never replayed inside the window.
        self.assertEqual(refresh_calls, ["refresh", "refresh"])
        self.assertEqual(refresh_at, [0.0, 10.5])
        self.assertEqual(sleeps, [0.5, 10.0, 0.5])

    def test_cooldown_starts_after_the_witness_wait_finishes(self):
        """The cooldown clock starts at the end of the witness wait, not its start."""
        reader = FakeReader([observation(False)])
        refresh_calls = []
        refresh_at = []
        clock = [0.0]

        def refresh():
            refresh_calls.append("refresh")
            refresh_at.append(clock[0])

        driver = SimpleNamespace(refresh_port_state=refresh)
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds

        wrapper = FreshnessRefreshingStateReader(
            reader,
            driver,
            max_wait_seconds=1.0,
            poll_interval_seconds=0.5,
            refresh_cooldown_seconds=0.5,
            rebuild_max_attempts=1,
            monotonic=lambda: clock[0],
            sleep=sleep,
        )

        first = wrapper.ensure_fresh(origin="test_reader")
        self.assertFalse(first.source_freshness_verified)
        self.assertEqual(clock[0], 1.0)
        self.assertEqual(refresh_at, [0.0])

        # With no reusable witness the second navigation must wait out the
        # cooldown that began at clock=1.0, so it happens at 1.5, not 1.0.
        second = wrapper.ensure_fresh(origin="test_reader")
        self.assertFalse(second.source_freshness_verified)
        self.assertEqual(refresh_calls, ["refresh", "refresh"])
        self.assertEqual(refresh_at, [0.0, 1.5])
        self.assertEqual(sleeps[2], 0.5)


class FreshnessRebuildTests(unittest.TestCase):
    """Defect 4: after a long timer sleep the client's first natural port
    response may not land inside one wait window, so a single navigation can
    fail to recover freshness. ``ensure_fresh`` now rebuilds up to
    ``rebuild_max_attempts`` times with a backoff, and still fails closed when
    every rebuild times out."""

    @staticmethod
    def _make(observations, *, rebuild_max_attempts=2, rebuild_backoff_seconds=0.5):
        reader = FakeReader(observations)
        refresh_calls = []
        events = []
        driver = SimpleNamespace(
            refresh_port_state=lambda **kwargs: refresh_calls.append("refresh")
        )
        clock = [0.0]

        def sleep(seconds):
            clock[0] += seconds

        wrapper = FreshnessRefreshingStateReader(
            reader,
            driver,
            max_wait_seconds=0.5,
            poll_interval_seconds=0.5,
            refresh_cooldown_seconds=0.0,
            rebuild_max_attempts=rebuild_max_attempts,
            rebuild_backoff_seconds=rebuild_backoff_seconds,
            event_sink=lambda event, fields: events.append((event, dict(fields))),
            monotonic=lambda: clock[0],
            sleep=sleep,
        )
        return wrapper, reader, refresh_calls, events

    @staticmethod
    def _audits(events):
        return [fields for name, fields in events if name == "ui_refresh_audit"]

    def test_a_rebuild_retries_and_succeeds_when_the_witness_arrives_late(self):
        wrapper, _, refresh_calls, events = self._make(
            [observation(False), observation(False), observation(True)]
        )

        result = wrapper.ensure_fresh(origin="monitor_tick")

        self.assertTrue(result.source_freshness_verified)
        self.assertEqual(refresh_calls, ["refresh", "refresh"])
        audits = self._audits(events)
        self.assertEqual(audits[-1]["outcome"], "refreshed")
        self.assertEqual(audits[-1]["rebuild_attempts"], 2)

    def test_a_rebuild_that_never_finds_a_witness_stays_fail_closed(self):
        wrapper, _, refresh_calls, events = self._make([observation(False)])

        result = wrapper.ensure_fresh(origin="monitor_tick")

        self.assertFalse(result.source_freshness_verified)
        self.assertEqual(refresh_calls, ["refresh", "refresh"])
        audits = self._audits(events)
        self.assertEqual(audits[-1]["outcome"], "witness_timeout")
        self.assertEqual(audits[-1]["rebuild_attempts"], 2)

    def test_rebuild_is_bounded_to_the_configured_attempts(self):
        wrapper, _, refresh_calls, _ = self._make(
            [observation(False)], rebuild_max_attempts=3
        )

        result = wrapper.ensure_fresh(origin="monitor_tick")

        self.assertFalse(result.source_freshness_verified)
        self.assertEqual(refresh_calls, ["refresh", "refresh", "refresh"])

    def test_repeated_cached_reads_do_not_count_as_a_rebuild_success(self):
        # The reader keeps handing back the same cached, unverified object; the
        # wait polling it again and again must never look like a witness.
        cached = observation(False)
        reader = FakeReader([cached])
        refresh_calls = []
        events = []
        driver = SimpleNamespace(
            refresh_port_state=lambda **kwargs: refresh_calls.append("refresh")
        )
        clock = [0.0]

        def sleep(seconds):
            clock[0] += seconds

        wrapper = FreshnessRefreshingStateReader(
            reader,
            driver,
            max_wait_seconds=2.0,
            poll_interval_seconds=0.5,
            refresh_cooldown_seconds=0.0,
            rebuild_max_attempts=1,
            event_sink=lambda event, fields: events.append((event, dict(fields))),
            monotonic=lambda: clock[0],
            sleep=sleep,
        )

        result = wrapper.ensure_fresh(origin="monitor_tick")

        self.assertFalse(result.source_freshness_verified)
        self.assertEqual(refresh_calls, ["refresh"])
        audits = self._audits(events)
        self.assertEqual(audits[-1]["outcome"], "witness_timeout")


class CooldownWitnessReuseTests(unittest.TestCase):
    """A monitor refresh must not be lost to the navigation cooldown.

    Live 2026-11-07: monitor_tick refreshed at 12:54:05, the settlement
    baseline at 12:54:25 was handed ``cooldown_suppressed`` (unverified) and
    the supervisor paused. A still-valid witness is now reused with zero
    clicks; when none is valid the cooldown is waited out and the existing
    refresh path obtains a new one; suppression is never reported as success.
    """

    NOW = datetime(2026, 11, 7, 12, 54, 5, tzinfo=timezone.utc)

    @classmethod
    def _obs(cls, fresh: bool, *, age_seconds: float = 0.0):
        return observation(
            fresh,
            observed_at=cls.NOW - timedelta(seconds=age_seconds),
        )

    @classmethod
    def _build(cls, observations, *, cooldown=30.0):
        refresh_calls = []
        sleeps = []
        events = []
        mono = [0.0]

        def sleep(seconds):
            sleeps.append(seconds)
            mono[0] += seconds

        driver = SimpleNamespace(
            refresh_port_state=lambda **kwargs: refresh_calls.append(
                kwargs.get("origin")
            ),
            last_refresh_stats={
                "clicks_completed": 3,
                "steps": [
                    "open_sortie",
                    "open_expedition_list",
                    "return_home_port",
                ],
            },
        )
        wrapper = FreshnessRefreshingStateReader(
            FakeReader(observations),
            driver,
            max_wait_seconds=1.0,
            poll_interval_seconds=0.5,
            refresh_cooldown_seconds=cooldown,
            rebuild_max_attempts=1,
            event_sink=lambda event, fields: events.append((event, dict(fields))),
            refresh_id_factory=lambda: "rfr-fixed",
            clock=lambda: cls.NOW,
            monotonic=lambda: mono[0],
            sleep=sleep,
        )
        return wrapper, refresh_calls, sleeps, events

    @staticmethod
    def _audits(events):
        return [fields for name, fields in events if name == "ui_refresh_audit"]

    def test_monitor_refresh_then_settlement_read_reuses_the_witness(self):
        witness = self._obs(True)
        wrapper, refreshes, _, events = self._build([
            self._obs(False), self._obs(False), witness, self._obs(False),
        ])

        monitor = wrapper.ensure_fresh(origin="monitor_tick")
        self.assertTrue(monitor.source_freshness_verified)
        self.assertEqual(refreshes, ["monitor_tick"])

        baseline = wrapper.ensure_fresh(origin="settlement_baseline")
        self.assertIs(baseline, witness)
        self.assertEqual(refreshes, ["monitor_tick"])
        audit = self._audits(events)[-1]
        self.assertEqual(audit["outcome"], "witness_reused")
        self.assertTrue(audit["witness_verified"])
        self.assertEqual(audit["clicks_sent"], 0)
        self.assertEqual(audit["navigation_steps"], [])

    def test_reused_witness_passes_the_callers_require_fresh_gate(self):
        witness = ObservedState(
            payload=(),
            observed_at=self.NOW,
            source="poi-redux:test",
            source_freshness_verified=True,
        )
        wrapper, _, _, _ = self._build([
            self._obs(False), self._obs(False), witness, self._obs(False),
        ])

        wrapper.ensure_fresh(origin="monitor_tick")
        baseline = wrapper.ensure_fresh(origin="settlement_baseline")

        self.assertIs(baseline, witness)
        baseline.require_fresh(now=self.NOW, max_age=timedelta(seconds=30))

    def test_an_expired_witness_is_reobtained_after_the_cooldown(self):
        stale = self._obs(True, age_seconds=60.0)
        fresh = self._obs(True)
        wrapper, refreshes, sleeps, events = self._build([
            self._obs(False), self._obs(False), stale,
            self._obs(False), self._obs(False), fresh,
        ])

        wrapper.ensure_fresh(origin="monitor_tick")
        baseline = wrapper.ensure_fresh(origin="settlement_baseline")

        self.assertIs(baseline, fresh)
        self.assertEqual(refreshes, ["monitor_tick", "settlement_baseline"])
        self.assertIn(30.0, sleeps)
        audits = self._audits(events)
        self.assertEqual(audits[1]["outcome"], "cooldown_suppressed")
        self.assertFalse(audits[1]["witness_verified"])
        self.assertEqual(audits[-1]["outcome"], "refreshed")

    def test_no_witness_waits_out_the_cooldown_and_stays_fail_closed(self):
        wrapper, refreshes, sleeps, events = self._build([self._obs(False)])

        wrapper.ensure_fresh(origin="monitor_tick")
        baseline = wrapper.ensure_fresh(origin="settlement_baseline")

        self.assertFalse(baseline.source_freshness_verified)
        self.assertEqual(refreshes, ["monitor_tick", "settlement_baseline"])
        self.assertIn(30.0, sleeps)
        audits = self._audits(events)
        self.assertEqual(audits[1]["outcome"], "cooldown_suppressed")
        self.assertFalse(audits[1]["witness_verified"])
        self.assertEqual(audits[-1]["outcome"], "witness_timeout")
        self.assertFalse(audits[-1]["witness_verified"])

    def test_reuse_sends_no_extra_ui_action(self):
        witness = self._obs(True)
        wrapper, refreshes, _, events = self._build([
            self._obs(False), self._obs(False), witness, self._obs(False),
        ])

        wrapper.ensure_fresh(origin="monitor_tick")
        wrapper.ensure_fresh(origin="settlement_baseline")

        self.assertEqual(refreshes, ["monitor_tick"])
        audits = self._audits(events)
        self.assertEqual([a["clicks_sent"] for a in audits], [3, 0])
        self.assertEqual(audits[-1]["navigation_steps"], [])

    def test_cooldown_suppression_is_never_reported_as_verification(self):
        wrapper, _, _, events = self._build([self._obs(False)])

        wrapper.ensure_fresh(origin="monitor_tick")
        wrapper.ensure_fresh(origin="settlement_baseline")

        audits = self._audits(events)
        suppressed = [a for a in audits if a["outcome"] == "cooldown_suppressed"]
        self.assertEqual(len(suppressed), 1)
        for audit in suppressed:
            self.assertIs(audit["witness_verified"], False)
            self.assertEqual(audit["clicks_sent"], 0)
            self.assertEqual(audit["navigation_steps"], [])
        self.assertFalse(
            any(a["outcome"] in {"refreshed", "witness_reused"} for a in audits)
        )


class PassiveWitnessWaitTests(unittest.TestCase):
    """The wait that keeps a post-action read from navigating for its witness."""

    @staticmethod
    def _reader(observations, clock, refresh_calls):
        driver = SimpleNamespace(
            refresh_port_state=lambda: refresh_calls.append("refresh")
        )

        def sleep(seconds):
            clock[0] += seconds

        reader = FakeReader(observations)
        wrapper = FreshnessRefreshingStateReader(
            reader,
            driver,
            max_wait_seconds=5.0,
            poll_interval_seconds=0.5,
            refresh_cooldown_seconds=0.0,
            monotonic=lambda: clock[0],
            sleep=sleep,
        )
        return wrapper, reader

    def test_a_witness_that_arrives_on_its_own_sends_no_click(self):
        clock = [0.0]
        refresh_calls = []
        wrapper, reader = self._reader(
            [observation(False), observation(False), observation(True)],
            clock,
            refresh_calls,
        )

        result = wrapper.await_passive_witness(timeout_seconds=5.0)

        self.assertTrue(result.source_freshness_verified)
        self.assertEqual(refresh_calls, [])
        self.assertEqual(reader.calls, 3)

    def test_a_witness_that_never_arrives_still_sends_no_click(self):
        clock = [0.0]
        refresh_calls = []
        wrapper, _ = self._reader([observation(False)], clock, refresh_calls)

        result = wrapper.await_passive_witness(timeout_seconds=1.0)

        self.assertFalse(result.source_freshness_verified)
        self.assertEqual(refresh_calls, [])
        self.assertEqual(clock[0], 1.0)

    def test_the_wait_respects_the_ui_action_time_boundary(self):
        clock = [0.0]
        refresh_calls = []
        boundary = datetime(2026, 11, 5, 4, 0, tzinfo=timezone.utc)
        stale = SimpleNamespace(
            source_freshness_verified=True,
            observed_at=boundary,
        )
        fresh = SimpleNamespace(
            source_freshness_verified=True,
            observed_at=boundary + timedelta(seconds=1),
        )
        wrapper, _ = self._reader([stale, fresh], clock, refresh_calls)

        result = wrapper.await_passive_witness(
            timeout_seconds=5.0, newer_than=boundary
        )

        self.assertEqual(result.observed_at, boundary + timedelta(seconds=1))
        self.assertEqual(refresh_calls, [])

    def test_a_negative_timeout_is_refused(self):
        clock = [0.0]
        wrapper, _ = self._reader([observation(False)], clock, [])

        with self.assertRaisesRegex(ValueError, "finite and non-negative"):
            wrapper.await_passive_witness(timeout_seconds=-1.0)


class RefreshAttributionTests(unittest.TestCase):
    """Every refresh must be joinable to the caller that asked for it."""

    def test_the_driver_stamps_every_step_with_the_refresh_identity(self):
        driver, _, events, _ = VisibleUiPortRefreshDriverTests.make_driver(
            self, ["HOME_PORT", "SORTIE_MENU", "EXPEDITION_LIST", "HOME_PORT"]
        )

        driver.refresh_port_state(refresh_id="rfr-test", origin="dispatch_preflight")

        stamped = [fields for _, fields in events if "refresh_id" in fields]
        self.assertTrue(stamped)
        for fields in stamped:
            self.assertEqual(fields["refresh_id"], "rfr-test")
            self.assertEqual(fields["refresh_origin"], "dispatch_preflight")
        self.assertEqual(driver.last_refresh_stats["clicks_completed"], 3)
        self.assertEqual(driver.last_refresh_stats["outcome"], "completed")
        self.assertEqual(
            driver.last_refresh_stats["steps"],
            ["open_sortie", "open_expedition_list", "return_home_port"],
        )

    def test_the_driver_refuses_an_empty_identity(self):
        driver, _, _, _ = VisibleUiPortRefreshDriverTests.make_driver(self, [])

        with self.assertRaisesRegex(ValueError, "non-empty string"):
            driver.refresh_port_state(refresh_id="  ")
        with self.assertRaisesRegex(ValueError, "non-empty string"):
            driver.refresh_port_state(origin="")

    def _reader(self, observations, *, events, driver, cooldown=30.0):
        clock = [0.0]

        def sleep(seconds):
            clock[0] += seconds

        return (
            FreshnessRefreshingStateReader(
                FakeReader(observations),
                driver,
                max_wait_seconds=2.0,
                poll_interval_seconds=0.5,
                refresh_cooldown_seconds=cooldown,
                event_sink=lambda event, fields: events.append((event, dict(fields))),
                refresh_id_factory=lambda: "rfr-fixed",
                monotonic=lambda: clock[0],
                sleep=sleep,
            ),
            clock,
        )

    def test_a_refresh_records_origin_clicks_and_outcome(self):
        events = []
        driver = SimpleNamespace(
            refresh_port_state=lambda **kwargs: driver.calls.append(kwargs),
            calls=[],
            last_refresh_stats={
                "clicks_completed": 3,
                "steps": ["open_sortie", "open_expedition_list", "return_home_port"],
            },
        )
        wrapper, _ = self._reader(
            [observation(False), observation(True)], events=events, driver=driver
        )

        wrapper.ensure_fresh(origin="dispatch_preflight")

        audits = [fields for name, fields in events if name == "ui_refresh_audit"]
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0]["origin"], "dispatch_preflight")
        self.assertEqual(audits[0]["refresh_id"], "rfr-fixed")
        self.assertEqual(audits[0]["outcome"], "refreshed")
        self.assertTrue(audits[0]["witness_verified"])
        self.assertEqual(audits[0]["clicks_sent"], 3)
        self.assertEqual(
            audits[0]["navigation_steps"],
            ["open_sortie", "open_expedition_list", "return_home_port"],
        )
        self.assertEqual(driver.calls[0]["origin"], "dispatch_preflight")
        self.assertEqual(driver.calls[0]["refresh_id"], "rfr-fixed")

    def test_an_already_fresh_read_records_no_navigation(self):
        events = []
        # Stale stats on purpose: this is what a driver holds after an earlier
        # navigation. Re-reporting them would claim three clicks this call never
        # sent (seen live 2026-11-07: three "already_fresh" audits each claiming
        # clicks_sent=3, all copied from one bootstrap refresh).
        driver = SimpleNamespace(
            refresh_port_state=lambda **kwargs: self.fail("unexpected refresh"),
            last_refresh_stats={
                "clicks_completed": 3,
                "steps": ["open_sortie", "open_expedition_list", "return_home_port"],
            },
        )
        wrapper, _ = self._reader([observation(True)], events=events, driver=driver)

        wrapper.ensure_fresh(origin="monitor_tick")

        audits = [fields for name, fields in events if name == "ui_refresh_audit"]
        self.assertEqual(audits[0]["outcome"], "already_fresh")
        self.assertIsNone(audits[0]["refresh_id"])
        self.assertEqual(audits[0]["clicks_sent"], 0)
        self.assertEqual(audits[0]["navigation_steps"], [])

    def test_a_suppressed_refresh_says_so_instead_of_looking_like_a_navigation(self):
        events = []
        refresh_calls = []
        driver = SimpleNamespace(
            refresh_port_state=lambda **kwargs: refresh_calls.append(
                kwargs.get("origin")
            ),
            last_refresh_stats={"clicks_completed": 3, "steps": ["open_sortie"]},
        )
        wrapper, clock = self._reader(
            [observation(False)], events=events, driver=driver
        )
        wrapper._last_refresh_at = 0.0
        clock[0] = 1.0

        wrapper.ensure_fresh(origin="dispatch_post_action")

        audits = [fields for name, fields in events if name == "ui_refresh_audit"]
        self.assertEqual(audits[0]["outcome"], "cooldown_suppressed")
        self.assertEqual(audits[0]["clicks_sent"], 0)
        self.assertEqual(audits[0]["navigation_steps"], [])
        self.assertFalse(audits[0]["witness_verified"])
        # With no reusable witness the cooldown is waited out (29s of the 30s
        # window) and the existing refresh path is then used. The two calls are
        # the two bounded rebuild attempts of this single ensure_fresh, not a
        # second refresh request.
        self.assertEqual(refresh_calls, ["dispatch_post_action"] * 2)
        self.assertGreaterEqual(clock[0], 30.0)

    def test_origin_is_required(self):
        events = []
        driver = SimpleNamespace(refresh_port_state=lambda **kwargs: None)
        wrapper, _ = self._reader([observation(True)], events=events, driver=driver)

        with self.assertRaisesRegex(ValueError, "name the caller"):
            wrapper.ensure_fresh(origin="")
        with self.assertRaisesRegex(ValueError, "name the caller"):
            wrapper.ensure_fresh(origin="   ")

    def test_reading_a_stale_state_navigates_for_nothing(self):
        """R3-D: read_fleets is observation, not a refresh request.

        A read that finds no witness used to fire a full navigation, which is
        how 27 navigations ended up behind two dispatch intents.
        """
        events = []
        driver = SimpleNamespace(
            refresh_port_state=lambda **kwargs: self.fail("read_fleets must not navigate")
        )
        wrapper, _ = self._reader([observation(False)], events=events, driver=driver)

        result = wrapper.read_fleets()

        self.assertFalse(result.source_freshness_verified)
        self.assertEqual(
            [name for name, _ in events if name == "ui_refresh_audit"], []
        )
        self.assertIsNone(wrapper._last_refresh_at)


if __name__ == "__main__":
    unittest.main()
