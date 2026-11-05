"""Offline tests for the evidence-based recovery of a blocked fleet (R3-G).

A blocked fleet has no supported way back, so the recovery is deliberately not
an ``unblock()``: it rebuilds the dispatch proof from the durable event log
and only moves the fleet when every fact is present and the live observation
agrees with it. Every refusal below must leave the fleet blocked and send no
game action.

The matrix follows the R3 handoff: evidence completeness, identity matching,
the other-fleet protection, the block-reason whitelist, settlement-completion
proof, event-history conflicts, restart recovery and the "no general unblock,
no automatic re-dispatch" boundary.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from kancollebot.core.observed_state import ObservedState
from kancollebot.expeditions.blocked_recovery import (
    RECOVERABLE_BLOCK_REASONS,
    DispatchEvidence,
    evaluate_blocked_recovery,
    is_recoverable_block_reason,
    read_dispatch_evidence,
    read_settlement_completion,
)
from kancollebot.expeditions.continuous_dispatch_controller import (
    SqliteDispatchEventLog,
    UiStateEvidence,
)
from kancollebot.expeditions.continuous_scheduler import (
    ContinuousExpeditionScheduler,
    FleetPhase,
    MissionTuple,
    UiOperationArbiter,
)
from kancollebot.expeditions.continuous_settlement_controller import (
    ContinuousSettlementController,
)
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot
from kancollebot.expeditions.startup_settlement import (
    StartupSettlementRefused,
    select_startup_screen,
)

BASE = datetime(2026, 11, 5, 7, 0, 0, tzinfo=timezone.utc)
RETURN_AT = BASE + timedelta(minutes=20)
FINAL_MS = int(RETURN_AT.timestamp() * 1000)
FLEET = 3
OTHER = 2
SHIPS = (16374, 13350, 6, 5747)
CONFIRMED_AT = BASE.isoformat()
VERIFIED_AT = (BASE + timedelta(seconds=30)).isoformat()
SETTLED_AT = (BASE + timedelta(minutes=25)).isoformat()

#: The block reason a dispatch leaves behind when the home-port confirmation
#: failed after the start click had already landed.
DISPATCH_BLOCK_REASON = (
    "continuous dispatch stopped after VisibleUiRefreshError; "
    "manual reconciliation required"
)


def fleet_snapshot(fleet_id=FLEET, ship_ids=SHIPS, mission_status=0, return_at=RETURN_AT):
    returned = mission_status != 0
    return ExpeditionFleetSnapshot(
        fleet_id=fleet_id,
        ship_ids=ship_ids,
        mission_id=3 if returned else None,
        return_at=return_at if returned else None,
        mission_status=mission_status,
        reserved=0,
        ship_levels=(1,) * len(ship_ids),
        ship_types=(1,) * len(ship_ids),
    )


def observation(fleets):
    return ObservedState(
        tuple(fleets), BASE, "poi-redux:info.fleets+info.ships",
        source_freshness_verified=True,
    )


def evidence(
    fleet_id=FLEET, mission_id=3, final_time_ms=FINAL_MS, ship_ids=SHIPS
):
    return DispatchEvidence(
        fleet_id=fleet_id,
        mission_id=mission_id,
        final_time_ms=final_time_ms,
        ship_ids=tuple(ship_ids),
        confirmed_at=BASE,
        home_port_verified_at=BASE + timedelta(seconds=30),
        home_port_evidence="two_consecutive_in_memory_ooi_fingerprints",
        home_port_consecutive_states=("HOME_PORT", "HOME_PORT"),
        event_ids=(11, 12),
    )


def confirmed_event(
    fleet_id=FLEET, mission_id=3, final_time_ms=FINAL_MS, ship_ids=SHIPS,
    observed_at=CONFIRMED_AT,
):
    return {
        "event_id": 11,
        "created_at": observed_at,
        "event": "dispatch_confirmed_by_poi",
        "fields": {
            "fleet_id": fleet_id,
            "mission_id": mission_id,
            "final_time_ms": final_time_ms,
            "ship_ids": list(ship_ids),
            "observed_at": observed_at,
        },
    }


def verified_event(
    fleet_id=FLEET, mission_id=3, observed_at=VERIFIED_AT,
    consecutive_states=("HOME_PORT", "HOME_PORT"),
):
    return {
        "event_id": 12,
        "created_at": observed_at,
        "event": "dispatch_home_port_verified",
        "fields": {
            "fleet_id": fleet_id,
            "mission_id": mission_id,
            "evidence": "two_consecutive_in_memory_ooi_fingerprints",
            "consecutive_states": list(consecutive_states),
            "observed_at": observed_at,
        },
    }


def settled_event(fleet_id=FLEET, observed_at=SETTLED_AT):
    return {
        "event_id": 13,
        "created_at": observed_at,
        "event": "settlement_reward_claimed",
        "fields": {
            "fleet_id": fleet_id,
            "evidence": "two_consecutive_frames",
            "observed_at": observed_at,
        },
    }


class ReadDispatchEvidenceTests(unittest.TestCase):
    def test_a_complete_pair_rebuilds_the_dispatch_proof(self):
        lookup = read_dispatch_evidence([confirmed_event(), verified_event()], FLEET)
        found = lookup.evidence
        self.assertIsNotNone(found)
        self.assertEqual(lookup.rejection, None)
        self.assertEqual(found.mission_id, 3)
        self.assertEqual(found.final_time_ms, FINAL_MS)
        self.assertEqual(found.ship_ids, SHIPS)
        self.assertGreater(found.home_port_verified_at, found.confirmed_at)
        self.assertEqual(found.home_port_consecutive_states, ("HOME_PORT", "HOME_PORT"))
        self.assertEqual(found.event_ids, (11, 12))

    def test_a_missing_dispatch_confirmation_is_reported(self):
        lookup = read_dispatch_evidence([verified_event()], FLEET)
        self.assertIsNone(lookup.evidence)
        self.assertEqual(lookup.rejection, "dispatch_confirmation_missing")

    def test_a_missing_home_port_verification_is_reported(self):
        lookup = read_dispatch_evidence([confirmed_event()], FLEET)
        self.assertIsNone(lookup.evidence)
        self.assertEqual(lookup.rejection, "home_port_verification_missing")

    def test_a_home_port_verification_that_is_not_later_is_reported(self):
        lookup = read_dispatch_evidence(
            [confirmed_event(), verified_event(observed_at=CONFIRMED_AT)], FLEET
        )
        self.assertIsNone(lookup.evidence)
        self.assertEqual(lookup.rejection, "home_port_verification_order_invalid")

    def test_a_verification_for_another_mission_proves_nothing(self):
        lookup = read_dispatch_evidence(
            [confirmed_event(), verified_event(mission_id=9)], FLEET
        )
        self.assertIsNone(lookup.evidence)
        self.assertEqual(lookup.rejection, "home_port_verification_missing")

    def test_a_verification_without_two_home_port_frames_proves_nothing(self):
        lookup = read_dispatch_evidence(
            [confirmed_event(), verified_event(consecutive_states=("HOME_PORT",))], FLEET
        )
        self.assertIsNone(lookup.evidence)
        self.assertEqual(lookup.rejection, "home_port_verification_missing")

    def test_another_fleets_evidence_is_not_ours(self):
        lookup = read_dispatch_evidence(
            [confirmed_event(fleet_id=OTHER), verified_event(fleet_id=OTHER)], FLEET
        )
        self.assertIsNone(lookup.evidence)
        self.assertEqual(lookup.rejection, "dispatch_confirmation_missing")

    def test_malformed_payloads_are_ignored(self):
        broken = {
            "event_id": 14,
            "created_at": CONFIRMED_AT,
            "event": "dispatch_confirmed_by_poi",
            "fields": {"fleet_id": FLEET, "mission_id": "three"},
        }
        lookup = read_dispatch_evidence([broken], FLEET)
        self.assertIsNone(lookup.evidence)
        self.assertEqual(lookup.rejection, "dispatch_confirmation_missing")

    def test_repeated_dispatches_with_different_identities_conflict(self):
        # Two dispatches, each with its own Home Port proof: the history
        # cannot say which one this fleet is waiting for.
        lookup = read_dispatch_evidence(
            [
                confirmed_event(mission_id=3),
                verified_event(mission_id=3),
                confirmed_event(
                    mission_id=4,
                    final_time_ms=FINAL_MS + 60000,
                    observed_at=(BASE + timedelta(minutes=1)).isoformat(),
                ),
                verified_event(
                    mission_id=4,
                    observed_at=(BASE + timedelta(minutes=2)).isoformat(),
                ),
            ],
            FLEET,
        )
        self.assertIsNone(lookup.evidence)
        self.assertEqual(lookup.rejection, "event_history_conflict")

    def test_a_repeated_dispatch_with_the_same_identity_is_not_a_conflict(self):
        lookup = read_dispatch_evidence(
            [
                confirmed_event(),
                verified_event(),
                confirmed_event(observed_at=(BASE + timedelta(minutes=1)).isoformat()),
                verified_event(observed_at=(BASE + timedelta(minutes=2)).isoformat()),
            ],
            FLEET,
        )
        self.assertIsNotNone(lookup.evidence)

    def test_the_evidence_survives_a_restart(self):
        # The proof must come from the durable log, not from a running process.
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite3"
            sink = SqliteDispatchEventLog(str(database))
            sink("dispatch_confirmed_by_poi", confirmed_event()["fields"])
            sink("dispatch_home_port_verified", verified_event()["fields"])
            rows = SqliteDispatchEventLog(str(database)).load_all()
            lookup = read_dispatch_evidence(rows, FLEET)
        self.assertIsNotNone(lookup.evidence)
        self.assertEqual(lookup.evidence.ship_ids, SHIPS)


class SettlementCompletionTests(unittest.TestCase):
    def test_a_claimed_reward_proves_the_chain_closed(self):
        found = read_settlement_completion([settled_event()], FLEET)
        self.assertIsNotNone(found)

    def test_no_settlement_event_proves_nothing(self):
        self.assertIsNone(
            read_settlement_completion([confirmed_event(), verified_event()], FLEET)
        )

    def test_another_fleets_settlement_proves_nothing(self):
        self.assertIsNone(read_settlement_completion([settled_event(fleet_id=OTHER)], FLEET))


class BlockedReasonWhitelistTests(unittest.TestCase):
    def test_the_reviewed_reasons_are_recoverable(self):
        for reason in RECOVERABLE_BLOCK_REASONS:
            with self.subTest(reason=reason):
                self.assertTrue(is_recoverable_block_reason(reason))

    def test_the_dispatch_block_reason_is_recoverable(self):
        self.assertTrue(is_recoverable_block_reason(DISPATCH_BLOCK_REASON))

    def test_unknown_and_operator_reasons_are_not_recoverable(self):
        for reason in (
            "unknown BLOCKED reason",
            "an earlier dispatch intent is still pending; recovery is required before retry",
            "persisted active mission conflicts with pending dispatch intent",
            "fresh Poi state remains idle after interrupted dispatch; automatic retry is forbidden",
            "orphaned dispatch intent found outside DISPATCHING; automatic retry is forbidden",
            "continuous resupply stopped after ambiguous observation; manual reconciliation required",
            "active mission tuple is missing or mismatched",
            "",
            None,
        ):
            with self.subTest(reason=reason):
                self.assertFalse(is_recoverable_block_reason(reason))


class EvaluateBlockedRecoveryTests(unittest.TestCase):
    def evaluate(self, **overrides):
        kwargs = dict(
            fleet_id=FLEET,
            phase=FleetPhase.BLOCKED,
            blocked_reason=DISPATCH_BLOCK_REASON,
            scheduler_mission=None,
            evidence=evidence(),
            snapshot=fleet_snapshot(mission_status=2),
            other_fleets_awaiting_result=(),
            reward_screen_proven=True,
            settlement_completed_at=None,
        )
        kwargs.update(overrides)
        return evaluate_blocked_recovery(**kwargs)

    def test_complete_evidence_and_a_matching_return_allow_the_recovery(self):
        decision = self.evaluate()
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.target_phase, FleetPhase.RETURNED_PENDING_UI)
        self.assertEqual(decision.mission.mission_status, 2)
        self.assertEqual(decision.mission.ship_ids, SHIPS)

    def test_a_block_reason_outside_the_whitelist_keeps_the_fleet_blocked(self):
        decision = self.evaluate(blocked_reason="unknown BLOCKED reason")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "blocked_reason_not_recoverable")

    def test_without_the_dispatch_evidence_the_fleet_stays_blocked(self):
        decision = self.evaluate(evidence=None, evidence_rejection="dispatch_confirmation_missing")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "dispatch_confirmation_missing")

    def test_a_mismatched_mission_id_keeps_the_fleet_blocked(self):
        decision = self.evaluate(evidence=evidence(mission_id=9))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "mission_identity_mismatch")

    def test_a_mismatched_return_time_keeps_the_fleet_blocked(self):
        decision = self.evaluate(evidence=evidence(final_time_ms=FINAL_MS + 1000))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "mission_identity_mismatch")

    def test_a_missing_return_time_keeps_the_fleet_blocked(self):
        decision = self.evaluate(
            snapshot=ExpeditionFleetSnapshot(
                fleet_id=FLEET, ship_ids=SHIPS, mission_id=3, return_at=None,
                mission_status=2, reserved=0,
                ship_levels=(1,) * len(SHIPS), ship_types=(1,) * len(SHIPS),
            )
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "mission_identity_mismatch")

    def test_a_mismatched_ship_set_keeps_the_fleet_blocked(self):
        decision = self.evaluate(
            evidence=evidence(ship_ids=(1, 2, 3, 4)),
            snapshot=fleet_snapshot(mission_status=2),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "observed_ship_ids_mismatch")

    def test_a_second_returning_fleet_keeps_the_fleet_blocked(self):
        decision = self.evaluate(other_fleets_awaiting_result=(OTHER,))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "other_fleets_awaiting_result")

    def test_an_incomplete_snapshot_keeps_the_fleet_blocked(self):
        decision = self.evaluate(snapshot=None)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "poi_snapshot_incomplete")

    def test_an_idle_fleet_without_settlement_proof_keeps_the_fleet_blocked(self):
        decision = self.evaluate(snapshot=fleet_snapshot(mission_status=0))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "settlement_completion_not_proven")

    def test_an_idle_fleet_without_a_proven_reward_screen_keeps_the_fleet_blocked(self):
        decision = self.evaluate(
            snapshot=fleet_snapshot(mission_status=0),
            settlement_completed_at=BASE + timedelta(minutes=25),
            reward_screen_proven=False,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "reward_screen_not_proven")

    def test_an_idle_fleet_with_full_proof_goes_to_resupply(self):
        decision = self.evaluate(
            snapshot=fleet_snapshot(mission_status=0),
            settlement_completed_at=BASE + timedelta(minutes=25),
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.target_phase, FleetPhase.RESUPPLY_REQUIRED)
        self.assertIsNone(decision.mission)

    def test_a_fleet_still_on_expedition_keeps_the_fleet_blocked(self):
        decision = self.evaluate(snapshot=fleet_snapshot(mission_status=1))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "mission_identity_mismatch")

    def test_a_fleet_that_is_not_blocked_is_not_recovered(self):
        decision = self.evaluate(phase=FleetPhase.IDLE)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "blocked_reason_not_recoverable")

    def test_a_conflicting_scheduler_mission_keeps_the_fleet_blocked(self):
        conflicting = MissionTuple(
            mission_status=1, mission_id=7, final_time_ms=FINAL_MS + 5000,
            reserved=0, ship_ids=SHIPS,
        )
        decision = self.evaluate(scheduler_mission=conflicting)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "mission_identity_mismatch")

    def test_a_matching_scheduler_mission_does_not_block_the_recovery(self):
        matching = MissionTuple(
            mission_status=1, mission_id=3, final_time_ms=FINAL_MS,
            reserved=0, ship_ids=SHIPS,
        )
        decision = self.evaluate(scheduler_mission=matching)
        self.assertTrue(decision.allowed)


class SchedulerRecoveryTests(unittest.TestCase):
    def make_blocked(self, *, with_mission=True, reason=DISPATCH_BLOCK_REASON):
        scheduler = ContinuousExpeditionScheduler(dispatch_fleet_ids=(FLEET,))
        scheduler.mark_ready(FLEET)
        if with_mission:
            scheduler.begin_dispatch(FLEET)
            scheduler.confirm_dispatch(
                FLEET,
                MissionTuple(
                    mission_status=1, mission_id=3, final_time_ms=FINAL_MS,
                    reserved=0, ship_ids=SHIPS,
                ),
            )
        scheduler.block(FLEET, reason)
        return scheduler

    def test_a_blocked_fleet_returns_to_the_settlement_phase(self):
        scheduler = self.make_blocked()
        returned = MissionTuple(
            mission_status=2, mission_id=3, final_time_ms=FINAL_MS,
            reserved=0, ship_ids=SHIPS,
        )
        updated = scheduler.recover_blocked_returned(
            FLEET, mission=returned, evidence="dispatch_evidence"
        )
        self.assertIs(updated.phase, FleetPhase.RETURNED_PENDING_UI)
        self.assertEqual(updated.mission, returned)
        self.assertIsNone(updated.blocked_reason)
        self.assertFalse(updated.home_port_verified)

    def test_a_settled_blocked_fleet_goes_straight_to_resupply(self):
        scheduler = self.make_blocked()
        updated = scheduler.recover_blocked_settled(FLEET, evidence="attribution")
        self.assertIs(updated.phase, FleetPhase.RESUPPLY_REQUIRED)
        self.assertIsNone(updated.mission)
        self.assertIsNone(updated.blocked_reason)

    def test_a_fleet_that_is_not_blocked_cannot_be_recovered(self):
        scheduler = ContinuousExpeditionScheduler(dispatch_fleet_ids=(FLEET,))
        returned = MissionTuple(
            mission_status=2, mission_id=3, final_time_ms=FINAL_MS,
            reserved=0, ship_ids=SHIPS,
        )
        with self.assertRaisesRegex(RuntimeError, "only a blocked fleet"):
            scheduler.recover_blocked_returned(
                FLEET, mission=returned, evidence="dispatch_evidence"
            )

    def test_recovery_without_evidence_is_refused(self):
        scheduler = self.make_blocked()
        returned = MissionTuple(
            mission_status=2, mission_id=3, final_time_ms=FINAL_MS,
            reserved=0, ship_ids=SHIPS,
        )
        with self.assertRaisesRegex(RuntimeError, "named evidence"):
            scheduler.recover_blocked_returned(FLEET, mission=returned, evidence="  ")

    def test_an_active_mission_cannot_be_used_for_a_recovery(self):
        scheduler = self.make_blocked()
        active = MissionTuple(
            mission_status=1, mission_id=3, final_time_ms=FINAL_MS,
            reserved=0, ship_ids=SHIPS,
        )
        with self.assertRaisesRegex(RuntimeError, "returned mission tuple"):
            scheduler.recover_blocked_returned(
                FLEET, mission=active, evidence="dispatch_evidence"
            )

    def test_another_fleets_mission_cannot_be_used_for_a_recovery(self):
        scheduler = self.make_blocked()
        foreign = MissionTuple(
            mission_status=2, mission_id=9, final_time_ms=FINAL_MS + 1000,
            reserved=0, ship_ids=SHIPS,
        )
        with self.assertRaisesRegex(RuntimeError, "own dispatch"):
            scheduler.recover_blocked_returned(
                FLEET, mission=foreign, evidence="dispatch_evidence"
            )

    def test_the_supply_path_continues_from_resupply_required(self):
        scheduler = self.make_blocked()
        scheduler.recover_blocked_settled(FLEET, evidence="attribution")
        ready = scheduler.confirm_resupply(FLEET, fully_supplied=True)
        self.assertIs(ready.phase, FleetPhase.READY)

    def test_there_is_no_general_unblock(self):
        # The boundary the handoff draws: no unblock(), no automatic
        # re-dispatch escape hatch on the scheduler.
        scheduler = self.make_blocked()
        self.assertFalse(hasattr(scheduler, "unblock"))
        self.assertFalse(hasattr(scheduler, "unblock_fleet"))


class NoNavigationReader:
    def __init__(self, observation):
        self._observation = observation
        self.read_calls = 0

    def read_fleets(self):
        self.read_calls += 1
        return self._observation

    def ensure_fresh(self, *, origin):
        raise AssertionError("the startup path must not navigate for a witness")


class SpySettlementUi:
    def __init__(self, displayed_screen="BONUS_ITEMS"):
        self.calls = []
        self.displayed_screen = displayed_screen

    @staticmethod
    def _evidence(state):
        return UiStateEvidence(state, (state, state), "two_consecutive_frames")

    def settlement_screen(self, *, fleet_id):
        self.calls.append(("settlement_screen", fleet_id))
        return self.displayed_screen

    def collect_return_result(self, *, fleet_id):
        self.calls.append(("collect_return_result", fleet_id))
        return self._evidence("RETURN_RESULT")

    def claim_bonus_items(self, *, fleet_id):
        self.calls.append(("claim_bonus_items", fleet_id))
        return self._evidence("BONUS_ITEMS")

    def claim_reward(self, *, fleet_id):
        self.calls.append(("claim_reward", fleet_id))
        return self._evidence("HOME_PORT")


class StartupChainRecoveryTests(unittest.TestCase):
    """The startup chain is where a blocked fleet may be recovered."""

    def make(self, observation, ui, *, reason=DISPATCH_BLOCK_REASON):
        scheduler = ContinuousExpeditionScheduler(dispatch_fleet_ids=(FLEET,))
        scheduler.mark_ready(FLEET)
        scheduler.begin_dispatch(FLEET)
        scheduler.confirm_dispatch(
            FLEET,
            MissionTuple(
                mission_status=1, mission_id=3, final_time_ms=FINAL_MS,
                reserved=0, ship_ids=SHIPS,
            ),
        )
        scheduler.block(FLEET, reason)
        controller = ContinuousSettlementController(
            scheduler=scheduler,
            state_reader=NoNavigationReader(observation),
            ui_driver=ui,
            ui_arbiter=UiOperationArbiter(),
            event_sink=lambda event, fields: None,
            allow_ui_actions=True,
        )
        return controller, scheduler

    def run_chain(
        self, controller, blocked_evidence, *, screen="HOME_PORT_RETURN_NOTICE",
        settled_at=None,
    ):
        return controller.settle_visible_reward_chain(
            fleet_id=FLEET,
            screen=select_startup_screen(composited_state=screen),
            expected_ship_ids=SHIPS,
            expected_ship_ids_source="dispatch_confirmed_by_poi",
            blocked_evidence=blocked_evidence,
            blocked_evidence_rejection=None if blocked_evidence else "dispatch_confirmation_missing",
            settlement_completed_at=settled_at,
        )

    def test_a_proven_blocked_fleet_is_recovered_and_settled(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = SpySettlementUi(displayed_screen="HOME_PORT_RETURN_NOTICE")
            poi = observation([fleet_snapshot(mission_status=2), fleet_snapshot(OTHER)])
            controller, scheduler = self.make(poi, ui)
            result = self.run_chain(controller, evidence())
            state = scheduler.fleet(FLEET)
        self.assertTrue(result.recovered_from_blocked)
        self.assertEqual(
            [name for name, _ in ui.calls],
            ["settlement_screen", "collect_return_result", "claim_bonus_items", "claim_reward"],
        )
        self.assertIs(state.phase, FleetPhase.RESUPPLY_REQUIRED)
        self.assertIsNone(state.blocked_reason)

    def test_a_blocked_fleet_without_evidence_is_refused_with_zero_clicks(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = SpySettlementUi(displayed_screen="HOME_PORT_RETURN_NOTICE")
            poi = observation([fleet_snapshot(mission_status=2), fleet_snapshot(OTHER)])
            controller, scheduler = self.make(poi, ui)
            with self.assertRaises(StartupSettlementRefused) as caught:
                self.run_chain(controller, None)
            state = scheduler.fleet(FLEET)
        self.assertEqual(caught.exception.reason, "dispatch_confirmation_missing")
        self.assertEqual([call for call in ui.calls if call[0] != "settlement_screen"], [])
        self.assertIs(state.phase, FleetPhase.BLOCKED)
        self.assertIsNotNone(state.blocked_reason)

    def test_a_block_reason_outside_the_whitelist_keeps_the_fleet_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = SpySettlementUi(displayed_screen="HOME_PORT_RETURN_NOTICE")
            poi = observation([fleet_snapshot(mission_status=2), fleet_snapshot(OTHER)])
            controller, scheduler = self.make(
                poi, ui, reason="unknown BLOCKED reason"
            )
            with self.assertRaises(StartupSettlementRefused) as caught:
                self.run_chain(controller, evidence())
            state = scheduler.fleet(FLEET)
        self.assertEqual(caught.exception.reason, "blocked_reason_not_recoverable")
        self.assertEqual([call for call in ui.calls if call[0] != "settlement_screen"], [])
        self.assertIs(state.phase, FleetPhase.BLOCKED)

    def test_a_second_returning_fleet_keeps_the_blocked_fleet_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = SpySettlementUi(displayed_screen="HOME_PORT_RETURN_NOTICE")
            poi = observation([
                fleet_snapshot(mission_status=2),
                fleet_snapshot(OTHER, mission_status=2),
            ])
            controller, scheduler = self.make(poi, ui)
            with self.assertRaises(StartupSettlementRefused) as caught:
                self.run_chain(controller, evidence())
            state = scheduler.fleet(FLEET)
        self.assertEqual(caught.exception.reason, "other_fleets_awaiting_result")
        self.assertEqual([call for call in ui.calls if call[0] != "settlement_screen"], [])
        self.assertIs(state.phase, FleetPhase.BLOCKED)

    def test_an_idle_fleet_without_settlement_proof_keeps_the_fleet_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = SpySettlementUi(displayed_screen="BONUS_ITEMS")
            poi = observation([fleet_snapshot(mission_status=0), fleet_snapshot(OTHER)])
            controller, scheduler = self.make(poi, ui)
            with self.assertRaises(StartupSettlementRefused) as caught:
                self.run_chain(controller, evidence(), screen="BONUS_ITEMS")
            state = scheduler.fleet(FLEET)
        self.assertEqual(caught.exception.reason, "settlement_completion_not_proven")
        self.assertEqual([call for call in ui.calls if call[0] != "settlement_screen"], [])
        self.assertIs(state.phase, FleetPhase.BLOCKED)

    def test_an_idle_fleet_with_full_proof_ends_in_resupply(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = SpySettlementUi(displayed_screen="BONUS_ITEMS")
            poi = observation([fleet_snapshot(mission_status=0), fleet_snapshot(OTHER)])
            controller, scheduler = self.make(poi, ui)
            result = self.run_chain(
                controller, evidence(), screen="BONUS_ITEMS",
                settled_at=BASE + timedelta(minutes=25),
            )
            state = scheduler.fleet(FLEET)
        self.assertTrue(result.recovered_from_blocked)
        self.assertIs(state.phase, FleetPhase.RESUPPLY_REQUIRED)
        self.assertEqual(
            [call for call in ui.calls if call[0] != "settlement_screen"],
            [("claim_reward", FLEET)],
        )

    def test_a_screen_that_vanished_before_the_first_click_refuses(self):
        # The recovery passed, but the page changed before the first click:
        # the chain must not be walked on a screen nobody proved.
        with tempfile.TemporaryDirectory() as directory:
            ui = SpySettlementUi(displayed_screen=None)
            poi = observation([fleet_snapshot(mission_status=2), fleet_snapshot(OTHER)])
            controller, scheduler = self.make(poi, ui)
            with self.assertRaises(StartupSettlementRefused) as caught:
                self.run_chain(controller, evidence())
            state = scheduler.fleet(FLEET)
        self.assertEqual(caught.exception.reason, "reward_screen_no_longer_displayed")
        self.assertEqual([call for call in ui.calls if call[0] != "settlement_screen"], [])
        self.assertIs(state.phase, FleetPhase.RETURNED_PENDING_UI)

    def test_the_chain_never_asks_the_reader_to_navigate(self):
        with tempfile.TemporaryDirectory() as directory:
            ui = SpySettlementUi(displayed_screen="HOME_PORT_RETURN_NOTICE")
            poi = observation([fleet_snapshot(mission_status=2), fleet_snapshot(OTHER)])
            controller, _ = self.make(poi, ui)
            self.run_chain(controller, evidence())
        self.assertEqual(controller.state_reader.read_calls, 1)

    def test_a_refusal_is_audited_with_zero_game_actions(self):
        events = []
        with tempfile.TemporaryDirectory() as directory:
            ui = SpySettlementUi(displayed_screen="HOME_PORT_RETURN_NOTICE")
            poi = observation([fleet_snapshot(mission_status=2), fleet_snapshot(OTHER)])
            controller, scheduler = self.make(poi, ui)
            controller.event_sink = lambda event, fields: events.append((event, fields))
            with self.assertRaises(StartupSettlementRefused):
                self.run_chain(controller, None)
        refusals = [fields for name, fields in events if name == "startup_settlement_refused"]
        self.assertTrue(refusals)
        for fields in refusals:
            self.assertEqual(fields["game_actions_sent"], 0)
            self.assertEqual(fields["recovery_result"], "refused")
            self.assertTrue(fields["rejection_reason"])
        self.assertIs(scheduler.fleet(FLEET).phase, FleetPhase.BLOCKED)


class MonitorRulesUnchangedTests(unittest.TestCase):
    """The recovery must not weaken what the monitor already refuses to do."""

    def test_a_blocked_fleet_still_makes_the_monitor_refuse_to_navigate(self):
        from kancollebot.expeditions.continuous_monitor import (
            ContinuousExpeditionMonitor,
            ContinuousMonitorError,
        )

        from kancollebot.expeditions.mission_eligibility import (
            MissionCompositionRequirement,
        )

        scheduler = ContinuousExpeditionScheduler(dispatch_fleet_ids=(FLEET,))
        scheduler.block(FLEET, DISPATCH_BLOCK_REASON)
        reader = NoNavigationReader(
            observation([fleet_snapshot(mission_status=2), fleet_snapshot(OTHER)])
        )
        monitor = ContinuousExpeditionMonitor(
            scheduler=scheduler,
            state_reader=reader,
            mission_requirements={
                FLEET: MissionCompositionRequirement(
                    mission_id=3, minimum_ships=3, minimum_flagship_level=1
                )
            },
            event_sink=lambda event, fields: None,
        )
        with self.assertRaisesRegex(ContinuousMonitorError, "will not navigate"):
            monitor.tick()
        self.assertEqual(reader.read_calls, 1)

    def test_the_refresh_cooldown_and_witness_rules_are_untouched(self):
        from kancollebot.adapters.poi.visible_ui_refresh import (
            FreshnessRefreshingStateReader,
        )

        import inspect

        parameters = inspect.signature(
            FreshnessRefreshingStateReader.__init__
        ).parameters
        self.assertEqual(parameters["max_wait_seconds"].default, 45.0)
        self.assertEqual(parameters["refresh_cooldown_seconds"].default, 30.0)
        self.assertEqual(parameters["poll_interval_seconds"].default, 0.5)


if __name__ == "__main__":
    unittest.main()
