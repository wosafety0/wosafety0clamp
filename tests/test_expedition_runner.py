"""Offline tests for the expedition cycle coordinator."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone

from kancollebot.core.observed_state import ObservedState
from kancollebot.expeditions.action_journal import ExpeditionActionJournal
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot
from kancollebot.expeditions.runner import ExpeditionRunner
from kancollebot.safety.policy import SafetyPolicy


NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def fleet(fleet_id: int = 2) -> ExpeditionFleetSnapshot:
    return ExpeditionFleetSnapshot(
        fleet_id=fleet_id,
        ship_ids=(1, 2, 3, 4, 5, 6),
        fuel_ready=True,
        ammo_ready=True,
    )


class FakeAdapter:
    def __init__(
        self, *, observed_at=NOW, snapshots=(fleet(),), fail_dispatch=False,
        confirm_dispatch=False, source_freshness_verified=True,
    ):
        self.state = ObservedState(
            tuple(snapshots), observed_at, "fake-poi",
            source_freshness_verified=source_freshness_verified,
        )
        self.dispatches = []
        self.fail_dispatch = fail_dispatch
        self.confirm_dispatch = confirm_dispatch

    def read_fleets(self):
        return self.state

    def dispatch_expedition(self, *, fleet_id, mission_id):
        self.dispatches.append((fleet_id, mission_id))
        if self.fail_dispatch:
            raise TimeoutError("simulated ambiguous timeout")
        if self.confirm_dispatch:
            updated = tuple(
                ExpeditionFleetSnapshot(
                    fleet_id=item.fleet_id, ship_ids=item.ship_ids,
                    mission_id=mission_id, fuel_ready=item.fuel_ready,
                    ammo_ready=item.ammo_ready,
                ) if item.fleet_id == fleet_id else item
                for item in self.state.payload
            )
            self.state = ObservedState(
                updated, self.state.observed_at + timedelta(seconds=1), "fake-poi",
                source_freshness_verified=self.state.source_freshness_verified,
            )


class ExpeditionRunnerTests(unittest.TestCase):
    def policy(self):
        return SafetyPolicy(allow_live_actions=True, require_explicit_confirmation=True)

    def test_default_policy_blocks_dispatch(self):
        adapter = FakeAdapter()
        result = ExpeditionRunner(adapter).run_once(
            mission_id=1, now=NOW, max_state_age=timedelta(seconds=30),
            policy=SafetyPolicy(),
        )
        self.assertEqual(result.status, "blocked_by_preflight")
        self.assertEqual(adapter.dispatches, [])

    def test_confirmed_fresh_state_dispatches_once(self):
        adapter = FakeAdapter(confirm_dispatch=True)
        with tempfile.TemporaryDirectory() as directory:
            journal = ExpeditionActionJournal(Path(directory) / "actions.sqlite")
            runner = ExpeditionRunner(adapter, journal=journal)
            kwargs = dict(
                mission_id=1, now=NOW, max_state_age=timedelta(seconds=30),
                policy=self.policy(), confirmed=True,
            )
            first = runner.run_once(**kwargs)
            second = runner.run_once(**kwargs)
        self.assertEqual(first.status, "dispatch_confirmed")
        self.assertEqual(second.status, "no_ready_fleet")
        self.assertEqual(adapter.dispatches, [(2, 1)])

    def test_live_dispatch_requires_durable_journal(self):
        adapter = FakeAdapter(confirm_dispatch=True)
        result = ExpeditionRunner(adapter).run_once(
            mission_id=1, now=NOW, max_state_age=timedelta(seconds=30),
            policy=self.policy(), confirmed=True,
        )
        self.assertEqual(result.status, "journal_unavailable")
        self.assertEqual(adapter.dispatches, [])

    def test_sqlite_reservation_error_blocks_dispatch(self):
        class BrokenJournal:
            def reserve(self, fleet_id, mission_id, observed_at):
                raise sqlite3.OperationalError("database is locked")

        adapter = FakeAdapter()
        result = ExpeditionRunner(adapter, journal=BrokenJournal()).run_once(
            mission_id=1, now=NOW, max_state_age=timedelta(seconds=30),
            policy=self.policy(), confirmed=True,
        )
        self.assertEqual(result.status, "journal_unavailable")
        self.assertEqual(adapter.dispatches, [])

    def test_unverified_source_freshness_blocks_dispatch(self):
        adapter = FakeAdapter(source_freshness_verified=False)
        result = ExpeditionRunner(adapter).run_once(
            mission_id=1, now=NOW, max_state_age=timedelta(seconds=30),
            policy=self.policy(), confirmed=True,
        )
        self.assertEqual(result.status, "blocked_by_preflight")
        self.assertIn("Source data freshness has not been verified", result.detail)
        self.assertEqual(adapter.dispatches, [])

    def test_stale_state_blocks(self):
        adapter = FakeAdapter(observed_at=NOW - timedelta(minutes=2))
        result = ExpeditionRunner(adapter).run_once(
            mission_id=1, now=NOW, max_state_age=timedelta(seconds=30),
            policy=self.policy(), confirmed=True,
        )
        self.assertEqual(result.status, "blocked_by_preflight")
        self.assertEqual(adapter.dispatches, [])

    def test_stop_blocks_without_reading_or_dispatching(self):
        adapter = FakeAdapter()
        runner = ExpeditionRunner(adapter)
        runner.stop()
        result = runner.run_once(
            mission_id=1, now=NOW, max_state_age=timedelta(seconds=30),
            policy=self.policy(), confirmed=True,
        )
        self.assertEqual(result.status, "stopped")
        self.assertEqual(adapter.dispatches, [])

    def test_no_ready_fleet_does_not_dispatch(self):
        adapter = FakeAdapter(snapshots=())
        result = ExpeditionRunner(adapter).run_once(
            mission_id=1, now=NOW, max_state_age=timedelta(seconds=30),
            policy=self.policy(), confirmed=True,
        )
        self.assertEqual(result.status, "no_ready_fleet")
        self.assertEqual(adapter.dispatches, [])

    def test_ambiguous_adapter_failure_blocks_same_observation_retry(self):
        adapter = FakeAdapter(fail_dispatch=True)
        with tempfile.TemporaryDirectory() as directory:
            journal = ExpeditionActionJournal(Path(directory) / "actions.sqlite")
            runner = ExpeditionRunner(adapter, journal=journal)
            kwargs = dict(
                mission_id=1, now=NOW, max_state_age=timedelta(seconds=30),
                policy=self.policy(), confirmed=True,
            )
            first = runner.run_once(**kwargs)
            second = runner.run_once(**kwargs)
        self.assertEqual(first.status, "dispatch_outcome_unknown")
        self.assertEqual(second.status, "duplicate_blocked")
        self.assertEqual(len(adapter.dispatches), 1)


if __name__ == "__main__":
    unittest.main()
