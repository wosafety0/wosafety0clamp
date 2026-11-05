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
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot
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
    ) -> None:
        if not callable(event_sink):
            raise ValueError("a durable event_sink is required")
        if max_state_age < timedelta(0):
            raise ValueError("max_state_age must not be negative")
        self.scheduler = scheduler
        self.state_reader = state_reader
        self.ui_driver = ui_driver
        self.ui_arbiter = ui_arbiter
        self.event_sink = event_sink
        self.allow_ui_actions = allow_ui_actions
        self.max_state_age = max_state_age
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def settle_one_returned(self, fleet_id: int) -> SettlementExecution:
        """Settle one returned fleet through the reward screens.

        Fails closed unless ``allow_ui_actions`` is enabled and the fleet is in a
        settlement phase with a returned mission identity that matches the
        scheduler's persisted dispatch. Each UI advance is re-verified against a
        fresh Poi observation; an ambiguous outcome blocks instead of retrying.
        """
        if not self.allow_ui_actions:
            raise ContinuousSettlementError("UI actions are disabled; explicit enablement is required")
        if fleet_id not in self.scheduler.dispatch_fleet_ids:
            raise ContinuousSettlementError(
                f"Fleet {fleet_id} is not a configured dispatch/settlement fleet"
            )

        with self.ui_arbiter.operation(f"continuous-settlement-fleet-{fleet_id}"):
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
                    raise ContinuousSettlementError(
                        f"Fleet {fleet_id} reads idle but fleets {other_returns} are "
                        "waiting for their expedition result; the reward screens "
                        "cannot be attributed to one fleet, so settlement is "
                        "refused until only one return is pending"
                    )
                self.event_sink("settlement_resumed_after_result_taken", {
                    "fleet_id": fleet_id,
                    "phase": current.phase.value,
                    "mission_id": persisted.mission_id,
                    "final_time_ms": persisted.final_time_ms,
                })

            if current.phase is FleetPhase.RETURNED_PENDING_UI and returned is None:
                # Nothing left to click is a *result*, not a failure: the reward
                # request is accepted when the result screen opens, so a restart
                # after that point sees an idle fleet and a plain Home Port. Only
                # the driver can prove the absence, so a driver without the probe
                # keeps the old fail-closed behaviour.
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

            decision = evaluate_startup_settlement(
                fleet_id=fleet_id,
                screen=screen,
                snapshot=snapshot,
                expected_ship_ids=expected_ship_ids,
                expected_ship_ids_source=expected_ship_ids_source,
                other_fleets_awaiting_result=others,
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
                "game_api_requests": False,
            })
            return VisibleRewardSettlement(
                fleet_id=fleet_id,
                start_screen=displayed,
                mission=credited,
                steps=tuple(steps),
                ship_ids=tuple(decision.attribution.observed_ship_ids if decision.attribution else ()),
                scheduler_phase_after=updated.phase.value,
            )

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
        payload: dict[str, object] = {
            "fleet_id": fleet_id,
            "reason": reason,
            "screen": screen,
            "failed_check": failed_check,
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
