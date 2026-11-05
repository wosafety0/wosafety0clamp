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


class ContinuousSettlementError(RuntimeError):
    """The observed state or UI transaction cannot safely continue."""


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
            baseline, _ = self._read_observation()
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
        after, _ = self._read_observation(newer_than=action_at)
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

    def _read_observation(
        self, *, newer_than: datetime | None = None
    ) -> tuple[ObservedState[tuple[ExpeditionFleetSnapshot, ...]], datetime]:
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
