from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from kancollebot.core.observation_contracts import ObservedFleetSnapshot
from kancollebot.core.observed_state import ObservedState
from kancollebot.expeditions.continuous_scheduler import FleetPhase, MissionTuple
from kancollebot.expeditions.recovery_classification import (
    RecoveryPauseGate,
    classify_recovery_signals,
)
from kancollebot.expeditions.recovery_reconciliation import (
    RecoveryReconciliationCoordinator,
)


class SnapshotProvider:
    def __init__(self, snapshot):
        self.latest_snapshot = snapshot


class StateReader:
    def __init__(self, observation):
        self.observation = observation
        self.calls = 0

    def read_fleets(self):
        self.calls += 1
        return self.observation


class Scheduler:
    def __init__(self, states):
        self._states = tuple(states)

    @property
    def states(self):
        return self._states


class DispatchRegistry:
    def __init__(self, records=()):
        self.records = tuple(records)

    def load_open(self):
        return self.records


class IntentStore:
    def __init__(self, intents=()):
        self.intents = tuple(intents)

    def load_all(self):
        return self.intents


class EventLog:
    def __init__(self, events=()):
        self.events = tuple(events)

    def load_all(self):
        return self.events


class ActionJournal:
    def __init__(self, unresolved=()):
        self.unresolved = tuple(unresolved)

    def load_unresolved(self):
        return self.unresolved


class RecoveryReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.gate = RecoveryPauseGate()
        self.gate.pause(
            classify_recovery_signals(observer_status="cdp_connection_closed")
        )
        self.paused_at = self.gate.paused_at_utc
        self.witness_at = self.paused_at + timedelta(seconds=1)
        self.observed_at = self.paused_at + timedelta(seconds=2)
        self.now = self.paused_at + timedelta(seconds=3)
        self.fleet = ObservedFleetSnapshot(
            fleet_id=2,
            ship_ids=(100,),
            mission_status=0,
            mission_id=None,
            return_at=None,
            fuel_ready=True,
            ammo_ready=True,
            reserved=0,
        )
        self.observation = ObservedState(
            payload=(self.fleet,),
            observed_at=self.observed_at,
            source="poi-redux:port",
            source_freshness_verified=True,
        )
        self.snapshot = {
            "source": {
                "responseReceivedAt": self.witness_at.isoformat(),
            }
        }
        self.scheduler_state = SimpleNamespace(
            fleet_id=2,
            phase=FleetPhase.READY,
            mission=None,
            home_port_verified=False,
        )

    def make_coordinator(
        self,
        *,
        snapshot=None,
        observation=None,
        scheduler_states=None,
        dispatches=(),
        intents=(),
        unresolved_actions=(),
        lifecycle_events=(),
        action_journal=True,
        event_log=True,
    ):
        reader = StateReader(
            self.observation if observation is None else observation
        )
        coordinator = RecoveryReconciliationCoordinator(
            recovery_gate=self.gate,
            snapshot_provider=SnapshotProvider(
                self.snapshot if snapshot is None else snapshot
            ),
            state_reader=reader,
            scheduler=Scheduler(
                (self.scheduler_state,)
                if scheduler_states is None
                else scheduler_states
            ),
            dispatch_registry=DispatchRegistry(dispatches),
            intent_store=IntentStore(intents),
            event_log=EventLog(lifecycle_events) if event_log else None,
            action_journal=(ActionJournal(unresolved_actions) if action_journal else None),
            max_state_age=timedelta(seconds=30),
            clock=lambda: self.now,
        )
        return coordinator, reader

    def test_idle_ready_fleet_reconciles_using_scheduler_states_property(self):
        coordinator, reader = self.make_coordinator()

        report = coordinator.evaluate()

        self.assertEqual(report.status, "RECONCILED")
        self.assertTrue(report.business_reconciled)
        self.assertFalse(report.resume_allowed)
        self.assertEqual(reader.calls, 1)
        self.assertEqual(report.fleets[0].outcome, "READY")
        self.assertTrue(self.gate.is_paused)

    def test_witness_not_newer_than_pause_waits_without_reading_poi(self):
        snapshot = {
            "source": {
                "responseReceivedAt": self.paused_at.isoformat(),
            }
        }
        coordinator, reader = self.make_coordinator(snapshot=snapshot)

        report = coordinator.evaluate()

        self.assertEqual(report.status, "WAITING_FOR_WITNESS")
        self.assertEqual(report.reason_codes, ("awaiting_natural_witness_after_pause",))
        self.assertEqual(reader.calls, 0)
        self.assertTrue(self.gate.is_paused)

    def test_unverified_poi_source_freshness_requires_manual_review(self):
        observation = ObservedState(
            payload=(self.fleet,),
            observed_at=self.observed_at,
            source="poi-redux:port",
            source_freshness_verified=False,
        )
        coordinator, _ = self.make_coordinator(observation=observation)

        report = coordinator.evaluate()

        self.assertEqual(report.status, "MANUAL_REQUIRED")
        self.assertTrue(any(code.startswith("poi_observation_untrusted:") for code in report.reason_codes))
        self.assertFalse(report.resume_allowed)

    def test_unresolved_action_journal_blocks_reconciliation(self):
        coordinator, _ = self.make_coordinator(
            unresolved_actions=({"fleet_id": 2, "state": "unknown"},)
        )

        report = coordinator.evaluate()

        self.assertEqual(report.status, "MANUAL_REQUIRED")
        self.assertIn("unresolved_action_journal", report.reason_codes)

    def test_pending_dispatch_intent_blocks_reconciliation(self):
        coordinator, _ = self.make_coordinator(intents=({"fleet_id": 2},))

        report = coordinator.evaluate()

        self.assertEqual(report.status, "MANUAL_REQUIRED")
        self.assertIn("pending_dispatch_intents", report.reason_codes)

    def test_active_mission_requires_exact_scheduler_poi_and_dispatch_identity(self):
        final_time_ms = 1_793_349_189_460
        mission = MissionTuple(
            mission_status=1,
            mission_id=3,
            final_time_ms=final_time_ms,
            reserved=0,
            ship_ids=(100, 101),
        )
        active_fleet = ObservedFleetSnapshot(
            fleet_id=2,
            ship_ids=(100, 101),
            mission_status=1,
            mission_id=3,
            return_at=datetime.fromtimestamp(final_time_ms / 1000, timezone.utc),
            reserved=0,
        )
        scheduler_state = SimpleNamespace(
            fleet_id=2,
            phase=FleetPhase.EXPEDITION_ACTIVE,
            mission=mission,
            home_port_verified=True,
        )
        record = SimpleNamespace(
            dispatch_id="dispatch-20261106T010000Z-abcdef123456",
            fleet_id=2,
            mission_id=3,
            ship_ids=(100, 101),
            final_time_ms=final_time_ms,
            status="confirmed",
        )
        observation = ObservedState(
            payload=(active_fleet,),
            observed_at=self.observed_at,
            source="poi-redux:port",
            source_freshness_verified=True,
        )
        coordinator, _ = self.make_coordinator(
            observation=observation,
            scheduler_states=(scheduler_state,),
            dispatches=(record,),
        )

        report = coordinator.evaluate()

        self.assertEqual(report.status, "RECONCILED")
        self.assertEqual(report.fleets[0].outcome, "EXPEDITION_ACTIVE")
        self.assertEqual(report.fleets[0].dispatch_id, record.dispatch_id)

    def test_dispatch_identity_conflict_fails_closed(self):
        final_time_ms = 1_793_349_189_460
        mission = MissionTuple(
            mission_status=1,
            mission_id=3,
            final_time_ms=final_time_ms,
            reserved=0,
            ship_ids=(100, 101),
        )
        active_fleet = ObservedFleetSnapshot(
            fleet_id=2,
            ship_ids=(100, 101),
            mission_status=1,
            mission_id=3,
            return_at=datetime.fromtimestamp(final_time_ms / 1000, timezone.utc),
            reserved=0,
        )
        scheduler_state = SimpleNamespace(
            fleet_id=2,
            phase=FleetPhase.EXPEDITION_ACTIVE,
            mission=mission,
            home_port_verified=True,
        )
        record = SimpleNamespace(
            dispatch_id="dispatch-20261106T010000Z-abcdef123456",
            fleet_id=2,
            mission_id=4,
            ship_ids=(100, 101),
            final_time_ms=final_time_ms,
            status="confirmed",
        )
        observation = ObservedState(
            payload=(active_fleet,),
            observed_at=self.observed_at,
            source="poi-redux:port",
            source_freshness_verified=True,
        )
        coordinator, _ = self.make_coordinator(
            observation=observation,
            scheduler_states=(scheduler_state,),
            dispatches=(record,),
        )

        report = coordinator.evaluate()

        self.assertEqual(report.status, "MANUAL_REQUIRED")
        self.assertIn(
            "fleet_2:scheduler_poi_and_dispatch_identity_conflict",
            report.reason_codes,
        )

    def test_future_witness_timestamp_is_rejected(self):
        snapshot = {
            "source": {
                "responseReceivedAt": (self.now + timedelta(seconds=1)).isoformat(),
            }
        }
        coordinator, _ = self.make_coordinator(snapshot=snapshot)

        report = coordinator.evaluate()

        self.assertEqual(report.status, "MANUAL_REQUIRED")
        self.assertIn("witness_timestamp_is_future_dated", report.reason_codes)


    def test_missing_action_journal_or_lifecycle_log_fails_closed(self):
        no_journal, _ = self.make_coordinator(action_journal=False)
        no_event_log, _ = self.make_coordinator(event_log=False)

        journal_report = no_journal.evaluate()
        event_report = no_event_log.evaluate()

        self.assertIn("action_journal_unavailable", journal_report.reason_codes)
        self.assertIn("lifecycle_event_log_unavailable", event_report.reason_codes)
        self.assertEqual(journal_report.status, "MANUAL_REQUIRED")
        self.assertEqual(event_report.status, "MANUAL_REQUIRED")

    def test_empty_scheduler_state_cannot_be_reported_as_reconciled(self):
        coordinator, _ = self.make_coordinator(scheduler_states=())

        report = coordinator.evaluate()

        self.assertEqual(report.status, "MANUAL_REQUIRED")
        self.assertIn("scheduler_has_no_fleet_states", report.reason_codes)

    def test_incomplete_settlement_lifecycle_blocks_reconciliation(self):
        events = (
            {
                "created_at": (self.paused_at - timedelta(seconds=1)).isoformat(),
                "event": "settlement_started",
                "fields": {"fleet_id": 2},
            },
        )
        coordinator, _ = self.make_coordinator(lifecycle_events=events)

        report = coordinator.evaluate()

        self.assertEqual(report.status, "MANUAL_REQUIRED")
        self.assertIn(
            "lifecycle_incomplete:settlement:fleet_2:settlement_started",
            report.reason_codes,
        )

    def test_completed_settlement_lifecycle_is_accepted(self):
        events = (
            {
                "created_at": (self.paused_at - timedelta(seconds=2)).isoformat(),
                "event": "settlement_started",
                "fields": {"fleet_id": 2},
            },
            {
                "created_at": (self.paused_at - timedelta(seconds=1)).isoformat(),
                "event": "settlement_reward_claimed",
                "fields": {"fleet_id": 2},
            },
        )
        coordinator, _ = self.make_coordinator(lifecycle_events=events)

        report = coordinator.evaluate()

        self.assertEqual(report.status, "RECONCILED")

    def test_incomplete_resupply_lifecycle_blocks_reconciliation(self):
        events = (
            {
                "created_at": (self.paused_at - timedelta(seconds=1)).isoformat(),
                "event": "resupply_action_started",
                "fields": {"fleet_id": 2, "lifecycle_kind": "resupply"},
            },
        )
        coordinator, _ = self.make_coordinator(lifecycle_events=events)

        report = coordinator.evaluate()

        self.assertEqual(report.status, "MANUAL_REQUIRED")
        self.assertIn(
            "lifecycle_incomplete:resupply:fleet_2:resupply_action_started",
            report.reason_codes,
        )

    def test_confirmed_resupply_lifecycle_is_accepted(self):
        events = (
            {
                "created_at": (self.paused_at - timedelta(seconds=2)).isoformat(),
                "event": "resupply_action_started",
                "fields": {"fleet_id": 2, "lifecycle_kind": "resupply"},
            },
            {
                "created_at": (self.paused_at - timedelta(seconds=1)).isoformat(),
                "event": "resupply_confirmed",
                "fields": {"fleet_id": 2},
            },
        )
        coordinator, _ = self.make_coordinator(lifecycle_events=events)

        report = coordinator.evaluate()

        self.assertEqual(report.status, "RECONCILED")

    def test_post_pause_settlement_blocker_requires_manual_review(self):
        events = (
            {
                "created_at": (self.paused_at + timedelta(milliseconds=500)).isoformat(),
                "event": "settlement_blocked",
                "fields": {"fleet_id": 2, "reason": "ambiguous"},
            },
        )
        coordinator, _ = self.make_coordinator(lifecycle_events=events)

        report = coordinator.evaluate()

        self.assertEqual(report.status, "MANUAL_REQUIRED")
        self.assertTrue(any(code.startswith("lifecycle_blocked:settlement:fleet_2") for code in report.reason_codes))

    def test_unpaused_gate_never_produces_reconciled_report(self):
        gate = RecoveryPauseGate()
        coordinator = RecoveryReconciliationCoordinator(
            recovery_gate=gate,
            snapshot_provider=SnapshotProvider(self.snapshot),
            state_reader=StateReader(self.observation),
            scheduler=Scheduler((self.scheduler_state,)),
            dispatch_registry=DispatchRegistry(),
            intent_store=IntentStore(),
            clock=lambda: self.now,
        )

        report = coordinator.evaluate()

        self.assertEqual(report.status, "MANUAL_REQUIRED")
        self.assertIn("recovery_gate_not_paused", report.reason_codes)
        self.assertFalse(report.resume_allowed)


if __name__ == "__main__":
    unittest.main()
