"""Injected-port settlement orchestration for the continuous expedition loop.

Drives the reward-collection half of the closed loop:

    RETURNED_PENDING_UI -> RETURN_RESULT -> BONUS_ITEMS -> RESUPPLY_REQUIRED

This module mirrors ``continuous_dispatch_controller``: a production UI driver
must use only visible game UI input and independently verify each page
transition. The controller requires a shared arbiter, one state reader, one UI
driver/session, and a durable event sink. Live UI actions are disabled unless
explicitly enabled.

The settlement controller never opens transports, never creates game requests,
and never retries an ambiguous settlement click. If a re-verified observation
does not match the persisted returned mission identity, the fleet is blocked for
manual reconciliation instead of being silently advanced.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from kancollebot.core.observed_state import ObservedState
from kancollebot.expeditions.continuous_dispatch_controller import (
    PoiFleetStateReader,
    UiStateEvidence,
)
from kancollebot.expeditions.continuous_scheduler import (
    ContinuousExpeditionScheduler,
    FleetPhase,
    MissionTuple,
    UiOperationArbiter,
)
from kancollebot.expeditions.dispatch_attribution import (
    UNATTRIBUTED,
    AttributionVerdict,
    attribute_dispatch,
)
from kancollebot.expeditions.dispatch_registry import (
    DispatchRegistryError,
    SqliteDispatchRegistry,
)
from kancollebot.expeditions.mission_result_witness import (
    DispatchRecord,
    MissionResultWitness,
    WitnessVerdict,
    cross_check_with_dispatch,
    evaluate_witness,
)
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot
from kancollebot.observability.event_envelope import new_correlation_id
from kancollebot.observability.sink import correlation_scope
from kancollebot.expeditions.blocked_recovery import (
    DispatchEvidence,
    evaluate_blocked_recovery,
)
from kancollebot.expeditions.startup_settlement import (
    StartupScreen,
    StartupSettlementRefused,
    evaluate_startup_settlement,
)


class ContinuousSettlementError(RuntimeError):
    """The observed state or UI transaction cannot safely continue."""


@dataclass(frozen=True, slots=True)
class VisibleRewardSettlement:
    """What a startup reward-chain settlement actually proved.

    ``mission`` is None when the durable scheduler held no returned identity for
    the fleet (for example a blocked or forgotten run): the chain is still
    provably closed, but there is no dispatch record to credit, so nothing is
    written back to the scheduler.
    """

    fleet_id: int
    start_screen: str
    mission: MissionTuple | None
    steps: tuple[tuple[str, str], ...]
    ship_ids: tuple[int, ...]
    scheduler_phase_after: str
    recovered_from_blocked: bool = False
    #: The verdict of the expedition-result witness, when one was checked.
    witness_verdict: WitnessVerdict | None = None
    recovery_reason: str | None = None


class ContinuousSettlementUiDriver(Protocol):
    """One shared visible-UI driver that can advance the reward screens.

    Each method must verify every pre/post page checkpoint and must not send
    game API/server requests. ``claim_reward`` must return only after two
    consecutive in-memory verifications of the post-claim idle state.
    """

    def collect_return_result(self, *, fleet_id: int) -> UiStateEvidence:
        """Advance RETURNED_PENDING_UI -> RETURN_RESULT; return RETURN_RESULT evidence."""
        ...

    def claim_bonus_items(self, *, fleet_id: int) -> UiStateEvidence:
        """Advance RETURN_RESULT -> BONUS_ITEMS; return BONUS_ITEMS evidence."""
        ...

    def claim_reward(self, *, fleet_id: int) -> UiStateEvidence:
        """Advance BONUS_ITEMS -> idle Home Port; return HOME_PORT evidence."""
        ...


@dataclass(frozen=True, slots=True)
class SettlementExecution:
    """Evidence returned by a completed settlement transaction for one fleet."""

    fleet_id: int
    mission: MissionTuple
    return_result_evidence: str
    bonus_items_evidence: str
    reward_claimed_evidence: str


class ContinuousSettlementController:
    """Settle one returned fleet through the reward screens, stopping at RESUPPLY_REQUIRED.

    Instantiate exactly one controller per Poi/CDP session and share its
    ``UiOperationArbiter`` with every other UI workflow. Live UI actions are
    disabled unless explicitly enabled.
    """

    def __init__(
        self,
        *,
        scheduler: ContinuousExpeditionScheduler,
        state_reader: PoiFleetStateReader,
        ui_driver: ContinuousSettlementUiDriver,
        ui_arbiter: UiOperationArbiter,
        event_sink: Callable[[str, Mapping[str, object]], None],
        allow_ui_actions: bool = False,
        max_state_age: timedelta = timedelta(seconds=30),
        clock: Callable[[], datetime] | None = None,
        dispatch_registry: SqliteDispatchRegistry | None = None,
        audit_gate: Callable[[], None] | None = None,
    ) -> None:
        if not callable(event_sink):
            raise ValueError("a durable event_sink is required")
        if max_state_age < timedelta(0):
            raise ValueError("max_state_age must not be negative")
        if dispatch_registry is not None and not isinstance(
            dispatch_registry, SqliteDispatchRegistry
        ):
            raise ValueError("dispatch_registry must be a SqliteDispatchRegistry or None")
        if audit_gate is not None and not callable(audit_gate):
            raise ValueError("audit_gate must be callable when given")
        self.audit_gate = audit_gate
        self.dispatch_registry = dispatch_registry
        self.scheduler = scheduler
        self.state_reader = state_reader
        self.ui_driver = ui_driver
        self.ui_arbiter = ui_arbiter
        self.event_sink = event_sink
        self.allow_ui_actions = allow_ui_actions
        self.max_state_age = max_state_age
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def settle_one_returned(
        self, fleet_id: int, *, pages_drained_globally: bool = False
    ) -> SettlementExecution:
        """Settle one returned fleet through the reward screens.

        Fails closed unless ``allow_ui_actions`` is enabled and the fleet is in a
        settlement phase with a returned mission identity that matches the
        scheduler's persisted dispatch. Each UI advance is re-verified against a
        fresh Poi observation; an ambiguous outcome blocks instead of retrying.

        ``pages_drained_globally`` says a global drain has already cleared the
        reward pages for the whole account. It does **not** relax attribution -
        it replaces the fleet-level "only one return may be pending" barrier
        with the stronger question the ledger can answer: is there exactly one
        confirmed, unsettled dispatch instance that this fleet's return closes?
        Without a ledger the barrier stays, because nothing else can tell two
        simultaneously returned fleets apart.
        """
        if not self.allow_ui_actions:
            raise ContinuousSettlementError("UI actions are disabled; explicit enablement is required")
        if fleet_id not in self.scheduler.dispatch_fleet_ids:
            raise ContinuousSettlementError(
                f"Fleet {fleet_id} is not a configured dispatch/settlement fleet"
            )
        # The audit log must be provably writable before the first observation,
        # let alone the first click: a settlement that cannot be recorded leaves
        # a reward chain nobody can reconcile afterwards.
        if self.audit_gate is not None:
            self.audit_gate()

        # A settlement is its own lifecycle, not part of any dispatch. It gets
        # its own ``correlation_id`` and deliberately does not attach the
        # dispatch id: a return can be settled in a process that never saw its
        # dispatch, and the durable link between the two is the dispatch ledger,
        # not a log field.
        lifecycle_id = new_correlation_id()
        with self.ui_arbiter.operation(
            f"continuous-settlement-fleet-{fleet_id}"
        ), correlation_scope(self.event_sink, correlation_id=lifecycle_id):
            self.event_sink("settlement_started", {
                "fleet_id": fleet_id,
                "lifecycle_kind": "settlement",
                "pages_drained_globally": bool(pages_drained_globally),
                "game_api_requests": False,
            })
            baseline, _ = self._read_observation(origin="settlement_baseline")
            snapshot = self._snapshot_for(baseline, fleet_id)
            returned = self._observed_return_state(fleet_id, snapshot)

            current = self.scheduler.fleet(fleet_id)
            if current.phase not in (
                FleetPhase.RETURNED_PENDING_UI,
                FleetPhase.RETURN_RESULT,
                FleetPhase.BONUS_ITEMS,
            ):
                raise ContinuousSettlementError(
                    f"Fleet {fleet_id} is not in a settlement phase: {current.phase.value}"
                )
            if current.mission is None or current.mission.mission_status != 2:
                raise ContinuousSettlementError(
                    f"Fleet {fleet_id} has no returned mission identity to settle"
                )
            persisted = current.mission
            if returned is not None and not returned.same_expedition(persisted):
                self._block_after_ambiguous(fleet_id)
                raise ContinuousSettlementError(
                    f"Fleet {fleet_id} observed returned mission does not match the "
                    f"dispatched identity; settlement blocked"
                )
            # Set when the screens have already been proven gone below, so the
            # same frames are not purchased twice.
            screens_proven_absent = False
            if pages_drained_globally:
                # A drain has already clicked through every settlement page on
                # the account. Poi's ``mission_status`` is not proof that the
                # chain is gone (it advances on the client's own port request),
                # so the *screen* is asked instead - and a chain that is still
                # on display means this drain did not clear it, which is a stop,
                # not a second run of the same clicks.
                probe = getattr(self.ui_driver, "settlement_screen", None)
                if not callable(probe):
                    raise ContinuousSettlementError(
                        f"Fleet {fleet_id} was settled after a global drain, but the "
                        "driver cannot prove the settlement screens are gone; "
                        "refusing to run the reward chain a second time"
                    )
                visible = probe(fleet_id=fleet_id)
                if visible is not None:
                    raise ContinuousSettlementError(
                        f"Fleet {fleet_id} still shows {visible} after a global drain; "
                        "the drain did not clear this chain and settlement is refused "
                        "with zero clicks"
                    )
                self.event_sink("settlement_screens_gone_after_global_drain", {
                    "fleet_id": fleet_id,
                    "phase": current.phase.value,
                    "mission_id": persisted.mission_id,
                    "final_time_ms": persisted.final_time_ms,
                    "game_api_requests": False,
                })
                # Reused below instead of probing the same screens twice: each
                # probe is a fresh composited frame, not a cheap read.
                screens_proven_absent = True
                returned = None
            if returned is None:
                # The server clears api_mission the moment the expedition result
                # is accepted, which happens on the first click of the reward
                # chain (live 2026-11-05). The chain is then pure UI, and the
                # returned identity lives in the scheduler, so an idle reading
                # is the expected shape of "this settlement is already half
                # done" - not a reason to strand the fleet.
                #
                # It is only unambiguous while no OTHER fleet is waiting for its
                # result. The reward screens say which expedition it was, not
                # which fleet, so with a second return pending the chain would be
                # collected for the wrong fleet. Live 2026-11-05: fleet 4's result
                # was accepted by an earlier run, fleet 3 came home two minutes
                # later, and this run's chain settled fleet 3 while the scheduler
                # credited fleet 4.
                other_returns = self._other_fleets_awaiting_result(baseline, fleet_id)
                if other_returns:
                    # P0.2: the barrier is migrated, not deleted. With a global
                    # drain the pages are gone and the ambiguity it protected
                    # against no longer exists - but only the ledger can say
                    # which instance this fleet's return closes, so no ledger
                    # means the old refusal.
                    if not pages_drained_globally:
                        raise ContinuousSettlementError(
                            f"Fleet {fleet_id} reads idle but fleets {other_returns} are "
                            "waiting for their expedition result; the reward screens "
                            "cannot be attributed to one fleet, so settlement is "
                            "refused until only one return is pending"
                        )
                    if self.dispatch_registry is None:
                        raise ContinuousSettlementError(
                            f"Fleet {fleet_id} reads idle but fleets {other_returns} are "
                            "waiting for their expedition result, and no dispatch "
                            "ledger is wired to attribute the result; settlement is "
                            "refused"
                        )
                    self.event_sink("settlement_pages_drained_globally", {
                        "fleet_id": fleet_id,
                        "phase": current.phase.value,
                        "mission_id": persisted.mission_id,
                        "final_time_ms": persisted.final_time_ms,
                        "other_fleets_awaiting_result": list(other_returns),
                        "attribution_source": "dispatch_ledger",
                        "game_api_requests": False,
                    })
                self.event_sink("settlement_resumed_after_result_taken", {
                    "fleet_id": fleet_id,
                    "phase": current.phase.value,
                    "mission_id": persisted.mission_id,
                    "final_time_ms": persisted.final_time_ms,
                })

            # P0-3: the returned tuple names the fleet, not the attempt. The
            # durable dispatch ledger is the only record that can say which
            # dispatch instance this result closes, so it is consulted before
            # the first click and a non-unique answer is a refusal, never a
            # preference order.
            attribution = self._attribute_returned(fleet_id, persisted)
            if attribution is not None and not attribution.allowed:
                self.event_sink("settlement_unattributed", {
                    "fleet_id": fleet_id,
                    "mission_id": persisted.mission_id,
                    "final_time_ms": persisted.final_time_ms,
                    "ship_ids": list(persisted.ship_ids),
                    **attribution.as_event_fields(),
                    "dispatch_clicks_sent": 0,
                    "game_api_requests": False,
                })
                raise ContinuousSettlementError(
                    f"Fleet {fleet_id} return cannot be attributed to exactly one "
                    f"dispatch instance ({attribution.reason}); settlement refused "
                    "with zero clicks"
                )

            if current.phase is FleetPhase.RETURNED_PENDING_UI and returned is None:
                # Nothing left to click is a *result*, not a failure: the reward
                # request is accepted when the result screen opens, so a restart
                # after that point sees an idle fleet and a plain Home Port. Only
                # the driver can prove the absence, so a driver without the probe
                # keeps the old fail-closed behaviour.
                if screens_proven_absent:
                    # Already proven above, on the frames this call purchased.
                    visible = None
                else:
                    probe = getattr(self.ui_driver, "settlement_screen", None)
                    if not callable(probe):
                        # No probe means "cannot prove the chain is over", and an
                        # unprovable absence keeps the old fail-closed answer.
                        raise ContinuousSettlementError(
                            f"Fleet {fleet_id} has no returned mission tuple to settle"
                        )
                    visible = probe(fleet_id=fleet_id)
                if visible is None:
                    updated = self.scheduler.confirm_settlement_already_complete(
                        fleet_id,
                        evidence="home_port_without_reward_screen_and_idle_fleet",
                    )
                    self.event_sink("settlement_closed_without_reward_screens", {
                        "fleet_id": fleet_id,
                        "mission_id": persisted.mission_id,
                        "final_time_ms": persisted.final_time_ms,
                        "evidence": "home_port_without_reward_screen_and_idle_fleet",
                        "current_phase": updated.phase.value,
                        "game_api_requests": False,
                    })
                    self._close_dispatch_record(fleet_id, attribution, persisted)
                    return SettlementExecution(
                        fleet_id=fleet_id,
                        mission=persisted,
                        return_result_evidence="",
                        bonus_items_evidence="",
                        reward_claimed_evidence="home_port_without_reward_screen",
                    )
            return_result_evidence = ""
            bonus_items_evidence = ""
            reward_claimed_evidence = ""

            if current.phase is FleetPhase.RETURNED_PENDING_UI:
                return_result_evidence = self._advance(
                    fleet_id, persisted,
                    ui_call=lambda: self.ui_driver.collect_return_result(fleet_id=fleet_id),
                    expected_state="RETURN_RESULT",
                    confirm_fn=lambda: self.scheduler.confirm_return_result(
                        fleet_id, ui_state="RETURN_RESULT"
                    ),
                    event="settlement_return_result_confirmed",
                )

            current = self.scheduler.fleet(fleet_id)
            if current.phase is FleetPhase.RETURN_RESULT:
                bonus_items_evidence = self._advance(
                    fleet_id, persisted,
                    ui_call=lambda: self.ui_driver.claim_bonus_items(fleet_id=fleet_id),
                    expected_state="BONUS_ITEMS",
                    confirm_fn=lambda: self.scheduler.confirm_bonus_items(
                        fleet_id, ui_state="BONUS_ITEMS"
                    ),
                    event="settlement_bonus_items_confirmed",
                )

            current = self.scheduler.fleet(fleet_id)
            if current.phase is FleetPhase.BONUS_ITEMS:
                reward_claimed_evidence = self._advance(
                    fleet_id, persisted,
                    ui_call=lambda: self.ui_driver.claim_reward(fleet_id=fleet_id),
                    expected_state="HOME_PORT",
                    confirm_fn=lambda: self.scheduler.confirm_reward_claimed(
                        fleet_id, claimed=True
                    ),
                    event="settlement_reward_claimed",
                    idle_after=True,
                )

            self._close_dispatch_record(fleet_id, attribution, persisted)
            return SettlementExecution(
                fleet_id=fleet_id,
                mission=persisted,
                return_result_evidence=return_result_evidence,
                bonus_items_evidence=bonus_items_evidence,
                reward_claimed_evidence=reward_claimed_evidence,
            )

    def settle_visible_reward_chain(
        self,
        *,
        fleet_id: int,
        screen: StartupScreen,
        expected_ship_ids: tuple[int, ...] | None = None,
        expected_ship_ids_source: str | None = None,
        blocked_evidence: DispatchEvidence | None = None,
        blocked_evidence_rejection: str | None = None,
        settlement_completed_at: datetime | None = None,
        mission_result_witness: MissionResultWitness | None = None,
    ) -> VisibleRewardSettlement:
        """Close a reward chain that is already on display at process start.

        This is the one settlement entry that does not need a fresh port
        witness, and it exists because that witness is unobtainable while a
        reward screen is up: the freshness navigation begins at HOME_PORT, and
        every reward screen advances on a click anywhere, so the monitor cannot
        navigate and the fleet can never be settled (live 2026-11-05).

        What replaces the witness gate is narrower, not weaker:

        * the screen is proven on the composited layer by the caller, and
          re-proven read-only here before the first click;
        * the fleet is attributed by its ship-ID set against the composition
          recorded when it was dispatched, and settlement is refused outright
          when that set is missing or different;
        * no other fleet may have an unaccepted result, because the screens
          name the expedition and not the fleet;
        * every step is still two-frame verified by the driver, and a step that
          is already complete is re-proven instead of clicked again.

        Refusals are zero-click: the outcome is raised before the driver is
        asked for anything.
        """
        if not self.allow_ui_actions:
            raise ContinuousSettlementError("UI actions are disabled; explicit enablement is required")
        if fleet_id not in self.scheduler.dispatch_fleet_ids:
            raise ContinuousSettlementError(
                f"Fleet {fleet_id} is not a configured dispatch/settlement fleet"
            )
        if not isinstance(screen, StartupScreen):
            raise ContinuousSettlementError("a proven startup screen is required")

        with self.ui_arbiter.operation(f"startup-settlement-fleet-{fleet_id}"):
            observation = self._read_observation_read_only()
            snapshot = self._snapshot_for(observation, fleet_id)
            others = self._other_fleets_awaiting_result(observation, fleet_id)
            persisted = self.scheduler.fleet(fleet_id).mission

            # P0-3: when the caller has no composition to attribute against,
            # the durable ledger supplies it - but only through the same
            # unique-match rule. This is what lets a real dispatch made by the
            # hand-run entry (no supervisor state, no Home Port event pair)
            # still enter the production settlement chain.
            if expected_ship_ids is None and self.dispatch_registry is not None:
                attribution_mission_id = (
                    persisted.mission_id
                    if persisted is not None
                    else snapshot.mission_id
                )
                if attribution_mission_id is not None:
                    resolved, resolved_source, rejection = (
                        self._expected_ship_ids_from_registry(
                            fleet_id, attribution_mission_id, snapshot.ship_ids
                        )
                    )
                    if rejection is not None:
                        self._refuse_startup(
                            fleet_id,
                            rejection,
                            screen.state,
                            failed_check="dispatch_registry_attribution",
                            mission_id=attribution_mission_id,
                            observed_ship_ids=list(snapshot.ship_ids),
                            game_actions_sent=0,
                        )
                        raise StartupSettlementRefused(rejection)
                    if resolved is not None:
                        expected_ship_ids = resolved
                        expected_ship_ids_source = resolved_source
                        self.event_sink("settlement_composition_from_registry", {
                            "fleet_id": fleet_id,
                            "mission_id": attribution_mission_id,
                            "ship_ids": list(resolved),
                            "expected_ship_ids_source": resolved_source,
                            "game_api_requests": False,
                        })

            if persisted is not None and snapshot.mission_status == 2:
                returned = self._require_returned_snapshot(fleet_id, snapshot)
                if not returned.same_expedition(persisted):
                    self._refuse_startup(
                        fleet_id,
                        "observed_returned_mission_differs_from_persisted",
                        screen.state,
                        failed_check="persisted_mission_identity",
                        game_actions_sent=0,
                    )
                    raise StartupSettlementRefused(
                        "observed_returned_mission_differs_from_persisted"
                    )
                # The fleet was out when this process started and has come home.
                # The start-up snapshot is what says so, and rebuilding the phase
                # from it is the one thing the snapshot may decide: the monitor
                # would have made the same transition had it had a trusted
                # observation. Home Port verification is still required - a
                # return seen before it is exactly what the monitor blocks on.
                current = self.scheduler.fleet(fleet_id)
                if current.phase is FleetPhase.EXPEDITION_ACTIVE:
                    if not current.home_port_verified:
                        self._refuse_startup(
                            fleet_id,
                            "home_port_verification_missing",
                            screen.state,
                            failed_check="post_dispatch_home_port_verification",
                            game_actions_sent=0,
                        )
                        raise StartupSettlementRefused("home_port_verification_missing")
                    self.scheduler.confirm_returned(fleet_id, returned)
                    self.event_sink("startup_settlement_fleet_state_rebuilt", {
                        "fleet_id": fleet_id,
                        "previous_phase": current.phase.value,
                        "current_phase": self.scheduler.fleet(fleet_id).phase.value,
                        "mission_id": returned.mission_id,
                        "final_time_ms": returned.final_time_ms,
                        "game_api_requests": False,
                    })

            # The expedition-result witness is the game's own statement of
            # which fleet completed which expedition. It is checked before
            # the attribution gate and it may only agree: a witness that
            # names another fleet, or a set of ships that is not this
            # fleet's, refuses the chain outright rather than being
            # weighed against the screen. It never stands in for the
            # dispatch confirmation - it appears only when the result is
            # settled, long after the dispatch was accepted.
            witness_verdict: WitnessVerdict | None = None
            if mission_result_witness is not None:
                witness_verdict = evaluate_witness(
                    witness=mission_result_witness,
                    expected_fleet_id=fleet_id,
                    expected_ship_ids=expected_ship_ids,
                    observed_at=self.clock(),
                )
                if not witness_verdict.allowed:
                    self._refuse_startup(
                        fleet_id,
                        witness_verdict.reason,
                        screen.state,
                        failed_check="mission_result_witness",
                        witness_fleet_id=(
                            mission_result_witness.fleet_id
                            if mission_result_witness is not None
                            else None
                        ),
                        witness_ship_ids=list(
                            mission_result_witness.ship_ids
                            if mission_result_witness is not None
                            else ()
                        ),
                        game_actions_sent=0,
                    )
                    raise StartupSettlementRefused(witness_verdict.reason)
                dispatch_record = None
                if blocked_evidence is not None:
                    dispatch_record = DispatchRecord(
                        fleet_id=blocked_evidence.fleet_id,
                        mission_id=blocked_evidence.mission_id,
                        ship_ids=blocked_evidence.ship_ids,
                        observed_at=blocked_evidence.confirmed_at,
                    )
                conflict = cross_check_with_dispatch(
                    witness=mission_result_witness, record=dispatch_record
                )
                if not conflict.allowed:
                    self._refuse_startup(
                        fleet_id,
                        conflict.reason,
                        screen.state,
                        failed_check="mission_result_witness_dispatch_cross_check",
                        witness_fleet_id=mission_result_witness.fleet_id,
                        witness_ship_ids=list(mission_result_witness.ship_ids),
                        witness_quest_name=mission_result_witness.quest_name,
                        game_actions_sent=0,
                    )
                    raise StartupSettlementRefused(conflict.reason)
                witness_verdict = conflict

            # The exclusivity proof is computed from the full Poi snapshot,
            # not from the scheduler's configured fleets: exactly one fleet
            # with an unaccepted result, and this one.
            exclusive_pending_fleet = (not others) and snapshot.mission_status == 2
            decision = evaluate_startup_settlement(
                fleet_id=fleet_id,
                screen=screen,
                snapshot=snapshot,
                expected_ship_ids=expected_ship_ids,
                expected_ship_ids_source=expected_ship_ids_source,
                other_fleets_awaiting_result=others,
                exclusive_pending_fleet=exclusive_pending_fleet,
            )
            if not decision.allowed:
                attribution = decision.attribution
                self._refuse_startup(
                    fleet_id,
                    decision.reason,
                    decision.screen,
                    failed_check="startup_attribution",
                    observed_ship_ids=list(attribution.observed_ship_ids) if attribution else [],
                    expected_ship_ids=list(attribution.expected_ship_ids or ()) if attribution else [],
                    expected_ship_ids_source=expected_ship_ids_source,
                    other_fleets_awaiting_result=list(others),
                    game_actions_sent=0,
                )
                raise StartupSettlementRefused(decision.reason)

            # A blocked fleet may only re-enter the lifecycle with proof: the
            # durable dispatch evidence (Poi's confirmation plus the Home Port
            # verification that followed it) and a live observation that agrees
            # with it. The check runs after the attribution gate, so a screen
            # that cannot be attributed never revives a fleet either.
            recovered = False
            recovery_reason = None
            current = self.scheduler.fleet(fleet_id)
            if current.phase is FleetPhase.BLOCKED:
                recovery = evaluate_blocked_recovery(
                    fleet_id=fleet_id,
                    phase=current.phase,
                    blocked_reason=current.blocked_reason,
                    scheduler_mission=current.mission,
                    evidence=blocked_evidence,
                    snapshot=snapshot,
                    other_fleets_awaiting_result=others,
                    reward_screen_proven=decision.allowed,
                    settlement_completed_at=settlement_completed_at,
                    evidence_rejection=blocked_evidence_rejection,
                )
                if not recovery.allowed or recovery.target_phase is None:
                    self._refuse_startup(
                        fleet_id,
                        recovery.reason,
                        decision.screen,
                        failed_check="blocked_recovery",
                        blocked_reason=current.blocked_reason,
                        dispatch_evidence_present=blocked_evidence is not None,
                        game_actions_sent=0,
                    )
                    raise StartupSettlementRefused(recovery.reason)
                if recovery.target_phase is FleetPhase.RETURNED_PENDING_UI:
                    self.scheduler.recover_blocked_returned(
                        fleet_id,
                        mission=recovery.mission,
                        evidence=recovery.reason,
                    )
                else:
                    self.scheduler.recover_blocked_settled(
                        fleet_id, evidence=recovery.reason
                    )
                recovered = True
                recovery_reason = recovery.reason
                self.event_sink("blocked_fleet_recovered", {
                    "fleet_id": fleet_id,
                    "mission_id": (
                        blocked_evidence.mission_id if blocked_evidence else None
                    ),
                    "original_block_reason": current.blocked_reason,
                    "previous_phase": FleetPhase.BLOCKED.value,
                    "recovered_phase": self.scheduler.fleet(fleet_id).phase.value,
                    "evidence_event_ids": list(
                        blocked_evidence.event_ids if blocked_evidence else ()
                    ),
                    "observed_at": self.clock().isoformat(),
                    "recovery_result": "recovered",
                    "rejection_reason": None,
                    "game_api_requests": False,
                })

            # Rebuild the fleet state from the proven observation when the
            # durable scheduler never learned this dispatch (a fleet sent by
            # hand). The returned mission is Poi's own record and the
            # attribution above has already decided which fleet it belongs to.
            current = self.scheduler.fleet(fleet_id)
            if (
                current.mission is None
                and current.phase in (
                    FleetPhase.IDLE,
                    FleetPhase.READY,
                    FleetPhase.RESUPPLY_REQUIRED,
                )
                and snapshot.mission_status == 2
            ):
                rebuilt = self._require_returned_snapshot(fleet_id, snapshot)
                self.scheduler.adopt_observed_mission(
                    fleet_id, rebuilt, home_port_verified=False
                )
                self.event_sink("startup_settlement_fleet_state_rebuilt", {
                    "fleet_id": fleet_id,
                    "previous_phase": current.phase.value,
                    "current_phase": self.scheduler.fleet(fleet_id).phase.value,
                    "mission_id": rebuilt.mission_id,
                    "final_time_ms": rebuilt.final_time_ms,
                    "attribution_source": decision.attribution_source,
                    "game_api_requests": False,
                })

            probe = getattr(self.ui_driver, "settlement_screen", None)
            if not callable(probe):
                self._refuse_startup(
                    fleet_id,
                    "driver_cannot_prove_displayed_reward_screen",
                    screen.state,
                    failed_check="screen_probe",
                    game_actions_sent=0,
                )
                raise StartupSettlementRefused("driver_cannot_prove_displayed_reward_screen")
            displayed = probe(fleet_id=fleet_id)
            if displayed is None:
                # The proven screen is gone. It is not "already settled" unless
                # something proves that, and a silent step backwards would leave
                # the chain half-open, so nothing is clicked either way.
                self._refuse_startup(
                    fleet_id,
                    "reward_screen_no_longer_displayed",
                    screen.state,
                    failed_check="screen_probe",
                    game_actions_sent=0,
                )
                raise StartupSettlementRefused("reward_screen_no_longer_displayed")

            self.event_sink("startup_settlement_started", {
                "fleet_id": fleet_id,
                "snapshot_screen": screen.state,
                "displayed_screen": displayed,
                "layers_disagree": screen.layers_disagree,
                "default_surface_state": screen.default_surface_state,
                "ship_ids": list(decision.attribution.observed_ship_ids if decision.attribution else ()),
                "expected_ship_ids_source": expected_ship_ids_source,
                "attribution_source": decision.attribution_source,
                "game_api_requests": False,
            })
            # A restart can resume mid-chain: the durable phase may still say
            # RETURNED_PENDING_UI while the proven screen is already the bonus
            # one. The intermediate scheduler phases are pure bookkeeping of
            # what the two-frame probe just proved, so they are recorded before
            # the next click - confirm_reward_claimed would otherwise refuse a
            # chain the game itself has already advanced.
            current = self.scheduler.fleet(fleet_id)
            if (
                displayed in ("RETURN_RESULT", "BONUS_ITEMS")
                and current.phase is FleetPhase.RETURNED_PENDING_UI
            ):
                self.scheduler.confirm_return_result(fleet_id, ui_state="RETURN_RESULT")
                current = self.scheduler.fleet(fleet_id)
            if (
                displayed == "BONUS_ITEMS"
                and current.phase is FleetPhase.RETURN_RESULT
            ):
                self.scheduler.confirm_bonus_items(fleet_id, ui_state="BONUS_ITEMS")

            steps: list[tuple[str, str]] = []
            current_screen = displayed
            if current_screen == "HOME_PORT_RETURN_NOTICE":
                current_screen = self._advance_visible(
                    fleet_id, "RETURN_RESULT",
                    ui_call=lambda: self.ui_driver.collect_return_result(fleet_id=fleet_id),
                    steps=steps,
                    confirm_fn=lambda: self._confirm_phase(
                        fleet_id, FleetPhase.RETURNED_PENDING_UI, "RETURN_RESULT"
                    ),
                )
            if current_screen == "RETURN_RESULT":
                current_screen = self._advance_visible(
                    fleet_id, "BONUS_ITEMS",
                    ui_call=lambda: self.ui_driver.claim_bonus_items(fleet_id=fleet_id),
                    steps=steps,
                    confirm_fn=lambda: self._confirm_phase(
                        fleet_id, FleetPhase.RETURN_RESULT, "BONUS_ITEMS"
                    ),
                )
            if current_screen == "BONUS_ITEMS":
                self._advance_visible(
                    fleet_id, "HOME_PORT",
                    ui_call=lambda: self.ui_driver.claim_reward(fleet_id=fleet_id),
                    steps=steps,
                )

            current = self.scheduler.fleet(fleet_id)
            if current.phase in (
                FleetPhase.RETURNED_PENDING_UI,
                FleetPhase.RETURN_RESULT,
                FleetPhase.BONUS_ITEMS,
            ) and current.mission is not None:
                updated = self.scheduler.confirm_reward_claimed(fleet_id, claimed=True)
                credited = current.mission
            elif current.phase is FleetPhase.EXPEDITION_ACTIVE and current.mission is not None:
                # The expedition is over and the reward chain is closed, but the
                # durable state still says the fleet is at sea: the game
                # clears api_mission when it accepts the result, so the fleet
                # reads idle while the scheduler still holds the active
                # mission. The fleet has nothing left to do but refuel.
                updated = self.scheduler.confirm_active_fleet_settled(
                    fleet_id, evidence="startup_settlement_chain_closed"
                )
                credited = current.mission
            else:
                # No durable returned identity: the chain is provably closed,
                # but there is nothing to credit. Recording a fabricated
                # mission tuple would be worse than leaving the phase alone.
                updated = current
                credited = None
            self.event_sink("startup_settlement_completed", {
                "fleet_id": fleet_id,
                "start_screen": displayed,
                "steps": [state for state, _ in steps],
                "evidence": [evidence for _, evidence in steps],
                "scheduler_phase_after": updated.phase.value,
                "mission_credited": credited is not None,
                "recovered_from_blocked": recovered,
                "recovery_reason": recovery_reason,
                "game_api_requests": False,
            })
            return VisibleRewardSettlement(
                fleet_id=fleet_id,
                start_screen=displayed,
                mission=credited,
                steps=tuple(steps),
                ship_ids=tuple(decision.attribution.observed_ship_ids if decision.attribution else ()),
                scheduler_phase_after=updated.phase.value,
                recovered_from_blocked=recovered,
                recovery_reason=recovery_reason,
                witness_verdict=witness_verdict,
            )

    # -- durable dispatch attribution ---------------------------------------

    def _attribute_returned(
        self, fleet_id: int, mission: MissionTuple
    ) -> AttributionVerdict | None:
        """Attribute one returned mission to one dispatch instance.

        ``None`` means "no registry is wired", which is the pre-P0-3 behaviour
        and must stay available for offline doubles. With a registry there are
        only two answers: one instance, or ``UNATTRIBUTED``.
        """
        if self.dispatch_registry is None:
            return None
        try:
            records = self.dispatch_registry.load_for_fleet(fleet_id)
        except DispatchRegistryError as exc:
            raise ContinuousSettlementError(
                f"durable dispatch ledger is unreadable for Fleet {fleet_id}: {exc}"
            ) from exc
        return attribute_dispatch(
            fleet_id=fleet_id,
            mission_id=mission.mission_id,
            ship_ids=mission.ship_ids,
            records=records,
            observed_at=self.clock(),
        )

    def _close_dispatch_record(
        self,
        fleet_id: int,
        attribution: AttributionVerdict | None,
        mission: MissionTuple,
    ) -> None:
        """Close the attributed instance so it cannot match a later return.

        Best-effort on purpose: the clicks are already done and the reward has
        been claimed, so raising here would report a failure that did not
        happen. A record left open is visible in the ledger; it would make the
        *next* attribution ambiguous, which fails closed rather than crediting
        the wrong fleet.
        """
        if attribution is None or not attribution.allowed or attribution.dispatch_id is None:
            return
        try:
            self.dispatch_registry.mark_settled(  # type: ignore[union-attr]
                attribution.dispatch_id, settled_at=self.clock()
            )
        except Exception as exc:
            try:
                self.event_sink("settlement_dispatch_record_not_closed", {
                    "fleet_id": fleet_id,
                    "dispatch_id": attribution.dispatch_id,
                    "mission_id": mission.mission_id,
                    "error_type": type(exc).__name__,
                    "consequence": "a later attribution may be ambiguous and will refuse",
                })
            except Exception:
                pass

    def _expected_ship_ids_from_registry(
        self, fleet_id: int, mission_id: int, observed_ship_ids: tuple[int, ...]
    ) -> tuple[tuple[int, ...] | None, str | None, str | None]:
        """Resolve the dispatched composition from the durable ledger.

        Returns ``(ship_ids, source, rejection)``. A rejection is a refusal
        reason: the ledger may not be mined for a *plausible* ship set, only
        for the one instance that owns this return.
        """
        if self.dispatch_registry is None:
            return None, None, None
        try:
            records = self.dispatch_registry.load_for_fleet(fleet_id)
        except DispatchRegistryError as exc:
            return None, None, f"dispatch_registry_unreadable:{type(exc).__name__}"
        verdict = attribute_dispatch(
            fleet_id=fleet_id,
            mission_id=mission_id,
            ship_ids=observed_ship_ids,
            records=records,
            observed_at=self.clock(),
        )
        if not verdict.allowed or verdict.record is None:
            return None, None, verdict.reason
        return verdict.record.ship_ids, "dispatch_registry", None

    def _confirm_phase(
        self, fleet_id: int, required_phase: FleetPhase, ui_state: str
    ) -> None:
        """Record a proven screen in the scheduler, when the phase allows it.

        A fleet with no durable mission (blocked, or a state the scheduler never
        learned) has no settlement phase to advance, and inventing one would be
        a fabricated record. The chain still closes; nothing is credited.
        """
        current = self.scheduler.fleet(fleet_id)
        if current.phase is not required_phase:
            return
        if ui_state == "RETURN_RESULT":
            self.scheduler.confirm_return_result(fleet_id, ui_state=ui_state)
        elif ui_state == "BONUS_ITEMS":
            self.scheduler.confirm_bonus_items(fleet_id, ui_state=ui_state)

    def _advance_visible(
        self,
        fleet_id: int,
        expected_state: str,
        *,
        ui_call: Callable[[], UiStateEvidence],
        steps: list[tuple[str, str]],
        confirm_fn: Callable[[], None] | None = None,
    ) -> str:
        """One two-frame proven step of the visible reward chain.

        No Poi observation is required across the boundary here: the port
        witness cannot be produced while a reward screen is up, and the driver
        already proves the resulting screen on two consecutive frames. The
        scheduler is what records the progress, and only after the proof.
        """
        evidence = ui_call()
        self._require_settlement_evidence(evidence, expected_state)
        steps.append((evidence.state, evidence.evidence))
        self.event_sink("startup_settlement_step_confirmed", {
            "fleet_id": fleet_id,
            "state": evidence.state,
            "evidence": evidence.evidence,
        })
        if confirm_fn is not None:
            confirm_fn()
        return evidence.state

    def _refuse_startup(
        self,
        fleet_id: int,
        reason: str,
        screen: str | None,
        *,
        failed_check: str,
        **fields: object,
    ) -> None:
        """Record one refusal with the audit fields the recovery rules require."""
        payload: dict[str, object] = {
            "fleet_id": fleet_id,
            "reason": reason,
            "screen": screen,
            "failed_check": failed_check,
            "recovery_result": "refused",
            "rejection_reason": reason,
            "observed_at": self.clock().isoformat(),
            "automatic_retry": False,
            **fields,
        }
        try:
            self.event_sink("startup_settlement_refused", payload)
        except Exception:
            pass

    def _advance(
        self,
        fleet_id: int,
        persisted: MissionTuple,
        *,
        ui_call: Callable[[], UiStateEvidence],
        expected_state: str,
        confirm_fn: Callable[[], object],
        event: str,
        idle_after: bool = False,
    ) -> str:
        evidence = ui_call()
        self._require_settlement_evidence(evidence, expected_state)
        action_at = self.clock()
        after, _ = self._read_observation(
            newer_than=action_at, origin="settlement_advance"
        )
        after_snapshot = self._snapshot_for(after, fleet_id)
        if idle_after:
            if not self._is_idle(after_snapshot):
                self._block_after_ambiguous(fleet_id)
                raise ContinuousSettlementError(
                    f"Fleet {fleet_id} did not become idle after reward claim"
                )
        else:
            after_returned = self._observed_return_state(fleet_id, after_snapshot)
            if after_returned is not None and not after_returned.same_expedition(persisted):
                self._block_after_ambiguous(fleet_id)
                raise ContinuousSettlementError(
                    f"Fleet {fleet_id} returned mission changed during settlement"
                )
        confirm_fn()
        self.event_sink(event, {"fleet_id": fleet_id, "evidence": evidence.evidence})
        return evidence.evidence

    def _read_observation_read_only(
        self,
    ) -> ObservedState[tuple[ExpeditionFleetSnapshot, ...]]:
        """Read Poi once for the startup gate: no navigation, no witness demand.

        The startup path exists because the freshness navigation cannot run
        while a reward screen is up. It therefore must not call
        :meth:`_read_observation` (which requires ``require_fresh`` and may
        navigate through ``ensure_fresh``). This read is used only to attribute
        the screen to a fleet; the screens themselves are proven by the driver,
        and the normal monitor still enforces the unchanged freshness and
        fail-closed gates once the chain is closed.
        """
        observation = self.state_reader.read_fleets()
        if not observation.source.startswith("poi-redux:"):
            raise StartupSettlementRefused("state observation must originate from Poi Redux")
        return observation

    def _read_observation(
        self,
        *,
        newer_than: datetime | None = None,
        origin: str = "settlement_read",
    ) -> tuple[ObservedState[tuple[ExpeditionFleetSnapshot, ...]], datetime]:
        ensure_fresh = getattr(self.state_reader, "ensure_fresh", None)
        if callable(ensure_fresh):
            observation = ensure_fresh(origin=origin)
        else:
            observation = self.state_reader.read_fleets()
        now = self.clock()
        try:
            observation.require_fresh(now=now, max_age=self.max_state_age)
        except ValueError as exc:
            raise ContinuousSettlementError(f"Poi observation is not trusted: {exc}") from exc
        if not observation.source.startswith("poi-redux:"):
            raise ContinuousSettlementError("state observation must originate from Poi Redux")
        if newer_than is not None and observation.observed_at <= newer_than:
            raise ContinuousSettlementError("Poi observation did not cross the UI action time boundary")
        return observation, now

    @staticmethod
    def _other_fleets_awaiting_result(
        observation: ObservedState[tuple[ExpeditionFleetSnapshot, ...]], fleet_id: int
    ) -> tuple[int, ...]:
        """Fleets other than this one whose result is still waiting on screen.

        A fleet reads status 2 exactly while its result has not been accepted,
        so any other status-2 fleet is a second reward chain that the UI cannot
        tell apart from this one: the reward screens name the expedition, not the
        fleet.
        """
        return tuple(
            sorted(
                item.fleet_id
                for item in observation.payload
                if item.fleet_id != fleet_id and item.mission_status == 2
            )
        )

    @staticmethod
    def _snapshot_for(
        observation: ObservedState[tuple[ExpeditionFleetSnapshot, ...]], fleet_id: int
    ) -> ExpeditionFleetSnapshot:
        matches = [item for item in observation.payload if item.fleet_id == fleet_id]
        if len(matches) != 1:
            raise ContinuousSettlementError(
                f"Poi observation must contain exactly one record for Fleet {fleet_id}"
            )
        return matches[0]

    @staticmethod
    def _require_returned_snapshot(
        fleet_id: int, snapshot: ExpeditionFleetSnapshot
    ) -> MissionTuple:
        if (
            snapshot.mission_status != 2
            or snapshot.mission_id is None
            or snapshot.return_at is None
            or not snapshot.ship_ids
        ):
            raise ContinuousSettlementError(
                f"Fleet {fleet_id} has no returned mission tuple to settle"
            )
        return MissionTuple(
            mission_status=2,
            mission_id=snapshot.mission_id,
            final_time_ms=int(snapshot.return_at.timestamp() * 1000),
            reserved=snapshot.reserved,
            ship_ids=snapshot.ship_ids,
        )

    @classmethod
    def _observed_return_state(
        cls, fleet_id: int, snapshot: ExpeditionFleetSnapshot
    ) -> MissionTuple | None:
        """Returned identity, or None when Poi already reports the fleet idle.

        Both are evidence of the same settled server fact: an idle fleet with a
        returned mission in the scheduler means the reward request was accepted
        and only the reward screens are left. Anything else still has to be a
        status-2 tuple matching the dispatched expedition.
        """
        if cls._is_idle(snapshot):
            return None
        return cls._require_returned_snapshot(fleet_id, snapshot)

    @staticmethod
    def _is_idle(snapshot: ExpeditionFleetSnapshot) -> bool:
        return (
            snapshot.mission_status == 0
            and snapshot.mission_id is None
            and snapshot.return_at is None
            and snapshot.reserved == 0
        )

    @staticmethod
    def _require_settlement_evidence(evidence: UiStateEvidence, expected_state: str) -> None:
        if not isinstance(evidence, UiStateEvidence):
            raise ContinuousSettlementError("UI driver did not return structured evidence")
        if (
            evidence.state != expected_state
            or evidence.consecutive_states != (expected_state, expected_state)
        ):
            raise ContinuousSettlementError(
                f"Expected {expected_state} two consecutive frames; "
                f"observed {evidence.consecutive_states!r}"
            )

    def _block_after_ambiguous(self, fleet_id: int) -> None:
        try:
            self.scheduler.block(
                fleet_id,
                "continuous settlement stopped after ambiguous observation; "
                "manual reconciliation required",
            )
        except Exception:
            pass
        try:
            self.event_sink("settlement_blocked", {
                "fleet_id": fleet_id,
                "reason": "ambiguous observation; automatic retry forbidden",
            })
        except Exception:
            pass
