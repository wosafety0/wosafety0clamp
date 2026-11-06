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
from kancollebot.core.reward_inventory import (
    REWARD_INVENTORY_BASELINE_EVENT,
    RewardInventoryError,
    RewardInventorySnapshot,
    RewardInventoryVerdict,
    reward_inventory_snapshot_fields,
    verify_reward_inventory_delta,
)
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
    MISSION_RESULT_EVENT,
    DispatchRecord,
    MissionResultWitness,
    MissionResultWitnessError,
    WitnessVerdict,
    cross_check_with_dispatch,
    evaluate_witness,
    parse_observer_witness,
    reward_receipt_rejection_reason,
    witness_fields,
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
    inventory_verdict: RewardInventoryVerdict | None = None


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
    witness_verdict: WitnessVerdict | None = None
    inventory_verdict: RewardInventoryVerdict | None = None


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
        mission_result_observer: object | None = None,
        mission_result_witness_loader: Callable[[int], MissionResultWitness | None] | None = None,
        require_mission_result_witness: bool = False,
        mission_name_resolver: Callable[[int], str] | None = None,
        reward_inventory_provider: Callable[[], RewardInventorySnapshot] | None = None,
        reward_inventory_baseline_loader: Callable[[int, str], RewardInventorySnapshot | None] | None = None,
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
        if type(require_mission_result_witness) is not bool:
            raise ValueError("require_mission_result_witness must be a bool")
        if mission_result_witness_loader is not None and not callable(mission_result_witness_loader):
            raise ValueError("mission_result_witness_loader must be callable or None")
        if mission_name_resolver is not None and not callable(mission_name_resolver):
            raise ValueError("mission_name_resolver must be callable or None")
        if reward_inventory_provider is not None and not callable(reward_inventory_provider):
            raise ValueError("reward_inventory_provider must be callable or None")
        if reward_inventory_baseline_loader is not None and not callable(reward_inventory_baseline_loader):
            raise ValueError("reward_inventory_baseline_loader must be callable or None")
        if require_mission_result_witness:
            if not callable(getattr(mission_result_observer, "arm", None)):
                raise ValueError("required mission-result witness needs an armable observer")
            if not callable(mission_result_witness_loader):
                raise ValueError("required mission-result witness needs a durable witness loader")
            if not callable(mission_name_resolver):
                raise ValueError("required mission-result witness needs a Poi mission-name resolver")
            if not callable(reward_inventory_provider):
                raise ValueError("required reward verification needs a Poi inventory reader")
            if not callable(reward_inventory_baseline_loader):
                raise ValueError("required reward verification needs a durable inventory baseline loader")
        self.audit_gate = audit_gate
        self.dispatch_registry = dispatch_registry
        self.mission_result_observer = mission_result_observer
        self.mission_result_witness_loader = mission_result_witness_loader
        self.require_mission_result_witness = require_mission_result_witness
        self.mission_name_resolver = mission_name_resolver
        self.reward_inventory_provider = reward_inventory_provider
        self.reward_inventory_baseline_loader = reward_inventory_baseline_loader
        self.scheduler = scheduler
        self.state_reader = state_reader
        self.ui_driver = ui_driver
        self.ui_arbiter = ui_arbiter
        self.event_sink = event_sink
        self.allow_ui_actions = allow_ui_actions
        self.max_state_age = max_state_age
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _dispatch_record_from_identity(self, identity: object, *, fleet_id: int) -> DispatchRecord:
        if getattr(identity, "fleet_id", None) != fleet_id:
            raise ContinuousSettlementError("dispatch registry returned a different fleet")
        if getattr(identity, "status", None) != "confirmed":
            raise ContinuousSettlementError("result witness requires one confirmed dispatch record")
        mission_id = getattr(identity, "mission_id", None)
        ship_ids = getattr(identity, "ship_ids", None)
        quest_name = getattr(identity, "quest_name", None)
        confirmed_at = getattr(identity, "confirmed_at", None)
        if (not isinstance(quest_name, str) or not quest_name.strip()) and self.mission_name_resolver is not None:
            try:
                quest_name = self.mission_name_resolver(mission_id)
            except Exception as exc:
                raise ContinuousSettlementError(
                    "Poi master-data mission name lookup failed during settlement"
                ) from exc
        if (
            isinstance(mission_id, bool) or not isinstance(mission_id, int) or mission_id < 1
            or not isinstance(ship_ids, tuple) or not ship_ids
            or not isinstance(quest_name, str) or not quest_name.strip()
            or not isinstance(confirmed_at, datetime)
            or confirmed_at.tzinfo is None or confirmed_at.utcoffset() is None
        ):
            raise ContinuousSettlementError("confirmed dispatch record is incomplete for witness attribution")
        return DispatchRecord(
            fleet_id=fleet_id, mission_id=mission_id, ship_ids=ship_ids,
            quest_name=quest_name.strip(), observed_at=confirmed_at,
        )

    def _single_open_dispatch_identity(self, fleet_id: int, snapshot: object) -> object:
        if self.dispatch_registry is None:
            raise ContinuousSettlementError("mission-result witness requires the durable dispatch registry")
        try:
            records = self.dispatch_registry.load_for_fleet(fleet_id)
        except DispatchRegistryError as exc:
            raise ContinuousSettlementError(f"dispatch registry is unreadable: {exc}") from exc
        open_records = [record for record in records if getattr(record, "is_open", False)]
        if len(open_records) != 1:
            raise ContinuousSettlementError(
                f"expected exactly one open dispatch for Fleet {fleet_id}; found {len(open_records)}"
            )
        identity = open_records[0]
        self._dispatch_record_from_identity(identity, fleet_id=fleet_id)
        snapshot_mission = getattr(snapshot, "mission_id", None)
        if snapshot_mission is not None and snapshot_mission != identity.mission_id:
            raise ContinuousSettlementError("Poi mission id conflicts with the open dispatch record")
        snapshot_ships = getattr(snapshot, "ship_ids", None)
        if isinstance(snapshot_ships, tuple) and snapshot_ships:
            if set(snapshot_ships) != set(identity.ship_ids):
                raise ContinuousSettlementError("Poi ship roster conflicts with the open dispatch record")
        return identity

    def _load_mission_result_witness(self, fleet_id: int) -> MissionResultWitness | None:
        if self.mission_result_witness_loader is None:
            return None
        try:
            witness = self.mission_result_witness_loader(fleet_id)
        except Exception as exc:
            raise ContinuousSettlementError(
                f"durable mission-result witness log is unreadable: {type(exc).__name__}"
            ) from exc
        if witness is not None and not isinstance(witness, MissionResultWitness):
            raise ContinuousSettlementError("durable mission-result witness loader returned an invalid object")
        return witness

    def _validate_mission_result_witness(
        self, *, fleet_id: int, witness: MissionResultWitness, identity: object
    ) -> WitnessVerdict:
        record = self._dispatch_record_from_identity(identity, fleet_id=fleet_id)
        verdict = evaluate_witness(
            witness=witness, expected_fleet_id=fleet_id,
            expected_ship_ids=record.ship_ids, expected_quest_name=record.quest_name,
            observed_at=self.clock(),
        )
        if not verdict.allowed:
            raise ContinuousSettlementError(verdict.reason)
        cross_check = cross_check_with_dispatch(witness=witness, record=record)
        if not cross_check.allowed:
            raise ContinuousSettlementError(cross_check.reason)
        receipt_rejection = reward_receipt_rejection_reason(witness)
        if receipt_rejection is not None:
            raise ContinuousSettlementError(receipt_rejection)
        self.event_sink("mission_result_witness_verified", {
            "fleet_id": fleet_id, "mission_id": record.mission_id,
            "quest_name": witness.quest_name, "ship_ids": list(witness.ship_ids),
            "result_kind": witness.result_kind,
            "reward_receipt_status": (
                witness.reward_receipt.status if witness.reward_receipt is not None else None
            ),
            "requested_at": witness.requested_at.isoformat() if witness.requested_at else None,
            "response_received_at": (
                witness.response_received_at.isoformat() if witness.response_received_at else None
            ),
            "dispatch_witness_verdict": cross_check.reason,
            "game_api_requests": False, "game_input_sent": False,
        })
        return cross_check

    def _arm_mission_result_observer(self, fleet_id: int):
        if not self.require_mission_result_witness or self.mission_result_observer is None:
            return None
        try:
            session = self.mission_result_observer.arm()
        except Exception as exc:
            self.event_sink("mission_result_observer_arm_failed", {
                "fleet_id": fleet_id,
                "status": getattr(self.mission_result_observer, "last_status", "unknown"),
                "error_type": type(exc).__name__,
                "game_api_requests": False, "game_input_sent": False,
            })
            raise ContinuousSettlementError(
                "passive mission-result observer did not prove it was armed"
            ) from exc
        try:
            self.event_sink("mission_result_observer_armed", {
                "fleet_id": fleet_id,
                "status": getattr(self.mission_result_observer, "last_status", "armed"),
                "armed_before_result_click": True,
                "game_api_requests": False, "game_input_sent": False,
            })
        except Exception:
            session.cancel()
            raise
        return session

    def _capture_and_validate_mission_result(
        self, *, fleet_id: int, identity: object, session: object
    ) -> WitnessVerdict:
        try:
            raw = session.wait()
        except Exception as exc:
            self.event_sink("mission_result_witness_capture_failed", {
                "fleet_id": fleet_id,
                "status": getattr(self.mission_result_observer, "last_status", "unknown"),
                "error_type": type(exc).__name__,
                "game_api_requests": False, "game_input_sent": False,
            })
            raise ContinuousSettlementError("mission-result witness capture failed") from exc
        if raw is None:
            status = getattr(self.mission_result_observer, "last_status", "no_capture")
            self.event_sink("mission_result_witness_capture_failed", {
                "fleet_id": fleet_id, "status": status,
                "game_api_requests": False, "game_input_sent": False,
            })
            raise ContinuousSettlementError(
                f"no natural mission-result response was captured ({status})"
            )
        try:
            witness = parse_observer_witness(raw)
        except MissionResultWitnessError as exc:
            self.event_sink("mission_result_witness_rejected", {
                "fleet_id": fleet_id, "reason": str(exc),
                "game_api_requests": False, "game_input_sent": False,
            })
            raise ContinuousSettlementError(f"mission-result witness rejected: {exc}") from exc
        self.event_sink(MISSION_RESULT_EVENT, witness_fields(witness))
        return self._validate_mission_result_witness(
            fleet_id=fleet_id, witness=witness, identity=identity
        )

    def _capture_reward_inventory_baseline(
        self, *, fleet_id: int, identity: object
    ) -> RewardInventorySnapshot:
        if self.reward_inventory_provider is None:
            raise ContinuousSettlementError("Poi reward-inventory reader is unavailable")
        dispatch_id = getattr(identity, "dispatch_id", None)
        if not isinstance(dispatch_id, str) or not dispatch_id.strip():
            raise ContinuousSettlementError("confirmed dispatch has no durable dispatch id")
        try:
            snapshot = self.reward_inventory_provider()
        except Exception as exc:
            raise ContinuousSettlementError(
                f"pre-result Poi inventory read failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(snapshot, RewardInventorySnapshot):
            raise ContinuousSettlementError("Poi inventory reader returned an invalid snapshot")
        if snapshot.source != "poi-nanodesu-mcp:info.resources+info.useitems":
            raise ContinuousSettlementError("reward baseline did not come from the approved Poi MCP inventory paths")
        fields = reward_inventory_snapshot_fields(
            snapshot, fleet_id=fleet_id, dispatch_id=dispatch_id
        )
        self.event_sink(REWARD_INVENTORY_BASELINE_EVENT, fields)
        self.event_sink("mission_reward_inventory_baseline_captured", {
            "fleet_id": fleet_id,
            "dispatch_id": dispatch_id,
            "observed_at": snapshot.observed_at.isoformat(),
            "resource_slots": len(snapshot.resources),
            "useitem_count": len(snapshot.useitems),
            "source": snapshot.source,
            "game_api_requests": False,
            "game_input_sent": False,
        })
        return snapshot

    def _load_reward_inventory_baseline(
        self, *, fleet_id: int, identity: object
    ) -> RewardInventorySnapshot:
        if self.reward_inventory_baseline_loader is None:
            raise ContinuousSettlementError("durable reward-inventory baseline loader is unavailable")
        dispatch_id = getattr(identity, "dispatch_id", None)
        if not isinstance(dispatch_id, str) or not dispatch_id.strip():
            raise ContinuousSettlementError("confirmed dispatch has no durable dispatch id")
        try:
            snapshot = self.reward_inventory_baseline_loader(fleet_id, dispatch_id)
        except Exception as exc:
            raise ContinuousSettlementError(
                f"durable reward-inventory baseline is unreadable: {type(exc).__name__}"
            ) from exc
        if not isinstance(snapshot, RewardInventorySnapshot):
            raise ContinuousSettlementError(
                "no reward-inventory baseline exists for this exact dispatch"
            )
        if snapshot.source != "poi-nanodesu-mcp:info.resources+info.useitems":
            raise ContinuousSettlementError("persisted reward baseline has an unapproved source")
        return snapshot

    def _verify_reward_inventory(
        self, *, fleet_id: int, identity: object,
        before: RewardInventorySnapshot, witness: MissionResultWitness,
    ) -> RewardInventoryVerdict:
        if self.reward_inventory_provider is None:
            raise ContinuousSettlementError("Poi reward-inventory reader is unavailable")
        receipt = witness.reward_receipt
        if (
            receipt is None or receipt.material is None
            or witness.requested_at is None or witness.response_received_at is None
        ):
            raise ContinuousSettlementError("reward receipt is incomplete for inventory verification")
        try:
            after = self.reward_inventory_provider()
        except Exception as exc:
            raise ContinuousSettlementError(
                f"post-result Poi inventory read failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(after, RewardInventorySnapshot):
            raise ContinuousSettlementError("post-result Poi inventory reader returned an invalid snapshot")
        if after.source != "poi-nanodesu-mcp:info.resources+info.useitems":
            raise ContinuousSettlementError("post-result inventory did not come from the approved Poi MCP paths")
        verdict = verify_reward_inventory_delta(
            before=before,
            after=after,
            expected_material=receipt.material,
            expected_items=receipt.items,
            requested_at=witness.requested_at,
            response_received_at=witness.response_received_at,
        )
        self.event_sink(
            "mission_reward_inventory_verified" if verdict.allowed
            else "mission_reward_inventory_rejected",
            {
                "fleet_id": fleet_id,
                "dispatch_id": getattr(identity, "dispatch_id", None),
                "reason": verdict.reason,
                "before_observed_at": before.observed_at.isoformat(),
                "after_observed_at": after.observed_at.isoformat(),
                "resource_deltas": list(verdict.resource_deltas),
                "useitem_deltas": [
                    {"api_id": item_id, "delta": delta}
                    for item_id, delta in verdict.useitem_deltas
                ],
                "game_api_requests": False,
                "game_input_sent": False,
            },
        )
        if not verdict.allowed:
            raise ContinuousSettlementError(verdict.reason)
        return verdict

    def _require_persisted_mission_result_witness(
        self, *, fleet_id: int, identity: object
    ) -> WitnessVerdict:
        witness = self._load_mission_result_witness(fleet_id)
        if witness is None:
            raise ContinuousSettlementError("no durable mission-result witness exists for this settlement")
        return self._validate_mission_result_witness(
            fleet_id=fleet_id, witness=witness, identity=identity
        )

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
                # P0.2 red line: a global drain has already clicked through every
                # settlement page on the account, so this fleet's reward chain is
                # physically gone. An UNATTRIBUTED verdict (the ledger cannot pin
                # the return to exactly one dispatch instance - e.g. the dispatch
                # event was never recorded, or two instances tie) must never block
                # the bookkeeping that advances the fleet to RESUPPLY_REQUIRED. We
                # tag it and continue; the dispatch record stays open for manual
                # reconciliation instead of stranding the fleet. The legacy
                # per-fleet path keeps its fail-closed refusal, because there the
                # clicks have not happened yet and guessing is worse than waiting.
                if not pages_drained_globally:
                    raise ContinuousSettlementError(
                        f"Fleet {fleet_id} return cannot be attributed to exactly one "
                        f"dispatch instance ({attribution.reason}); settlement refused "
                        "with zero clicks"
                    )

            mission_result_capture_session = None
            mission_result_witness_verdict: WitnessVerdict | None = None
            mission_result_inventory_baseline: RewardInventorySnapshot | None = None
            mission_result_inventory_verdict: RewardInventoryVerdict | None = None
            if self.require_mission_result_witness:
                if attribution is None or not attribution.allowed or attribution.record is None:
                    raise ContinuousSettlementError(
                        "mission-result witness requires exactly one attributed confirmed dispatch"
                    )
                if (
                    current.phase is FleetPhase.RETURNED_PENDING_UI
                    and returned is not None
                    and not pages_drained_globally
                ):
                    mission_result_inventory_baseline = self._capture_reward_inventory_baseline(
                        fleet_id=fleet_id, identity=attribution.record
                    )
                    mission_result_capture_session = self._arm_mission_result_observer(fleet_id)
                else:
                    mission_result_inventory_baseline = self._load_reward_inventory_baseline(
                        fleet_id=fleet_id, identity=attribution.record
                    )
                    mission_result_witness_verdict = self._require_persisted_mission_result_witness(
                        fleet_id=fleet_id, identity=attribution.record
                    )
                    if mission_result_witness_verdict.witness is None:
                        raise ContinuousSettlementError("persisted mission-result witness has no payload")
                    mission_result_inventory_verdict = self._verify_reward_inventory(
                        fleet_id=fleet_id,
                        identity=attribution.record,
                        before=mission_result_inventory_baseline,
                        witness=mission_result_witness_verdict.witness,
                    )

            if (
                current.phase in (
                    FleetPhase.RETURNED_PENDING_UI,
                    FleetPhase.RETURN_RESULT,
                    FleetPhase.BONUS_ITEMS,
                )
                and returned is None
            ):
                # Nothing left to click is a *result*, not a failure: the reward
                # request is accepted when the result screen opens, so a restart
                # after that point sees an idle fleet and a plain Home Port.
                # Live 2026-11-06: this also covers a chain interrupted mid-way
                # (durable phase RETURN_RESULT or BONUS_ITEMS) whose screens
                # were advanced past while the process was down - the game
                # clears api_mission on the first click either way, and only
                # the scheduler knows which step it had recorded. Only the
                # driver can prove the absence, so a driver without the probe
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
                try:
                    return_result_evidence = self._advance(
                        fleet_id, persisted,
                        ui_call=lambda: self.ui_driver.collect_return_result(fleet_id=fleet_id),
                        expected_state="RETURN_RESULT",
                        confirm_fn=lambda: self.scheduler.confirm_return_result(
                            fleet_id, ui_state="RETURN_RESULT"
                        ),
                        event="settlement_return_result_confirmed",
                    )
                    if mission_result_capture_session is not None:
                        mission_result_witness_verdict = self._capture_and_validate_mission_result(
                            fleet_id=fleet_id,
                            identity=attribution.record,
                            session=mission_result_capture_session,
                        )
                        mission_result_capture_session = None
                        if (
                            mission_result_witness_verdict.witness is None
                            or mission_result_inventory_baseline is None
                        ):
                            raise ContinuousSettlementError(
                                "mission-result witness or inventory baseline is missing"
                            )
                        mission_result_inventory_verdict = self._verify_reward_inventory(
                            fleet_id=fleet_id,
                            identity=attribution.record,
                            before=mission_result_inventory_baseline,
                            witness=mission_result_witness_verdict.witness,
                        )
                except Exception:
                    if mission_result_capture_session is not None:
                        mission_result_capture_session.cancel()
                    raise

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
                witness_verdict=mission_result_witness_verdict,
                inventory_verdict=mission_result_inventory_verdict,
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
            reason = "UI actions are disabled; explicit enablement is required"
            self._refuse_startup(
                fleet_id,
                reason,
                self._screen_name(screen),
                failed_check="ui_actions_disabled",
            )
            raise ContinuousSettlementError(reason)
        if fleet_id not in self.scheduler.dispatch_fleet_ids:
            reason = f"Fleet {fleet_id} is not a configured dispatch/settlement fleet"
            self._refuse_startup(
                fleet_id,
                reason,
                self._screen_name(screen),
                failed_check="fleet_not_configured",
            )
            raise ContinuousSettlementError(reason)
        if not isinstance(screen, StartupScreen):
            reason = "a proven startup screen is required"
            raise ContinuousSettlementError(reason)

        # One startup settlement is its own lifecycle, exactly like
        # ``settle_one_returned``: every row it emits - including the refusals
        # below and the later ``startup_settlement_started`` that marks the first
        # click - carries one ``correlation_id``. ``startup_settlement_entry`` is
        # a separate fact from that start row on purpose: this one says "the
        # entry was invoked, with this screen", and it is the only boundary a
        # refused chain has, because a refusal must not claim a chain began.
        #
        # Deliberately no ``dispatch_id``: this entry exists for a process that
        # never saw the dispatch, and the durable link between the two is the
        # dispatch ledger, not a log field.
        lifecycle_id = new_correlation_id()
        with correlation_scope(self.event_sink, correlation_id=lifecycle_id):
            self.event_sink("startup_settlement_entry", {
                "fleet_id": fleet_id,
                "lifecycle_kind": "settlement",
                "entry": "visible_reward_chain",
                "screen": self._screen_name(screen),
                "game_api_requests": False,
                "automatic_retry": False,
            })
            return self._settle_visible_reward_chain(
                fleet_id=fleet_id,
                screen=screen,
                expected_ship_ids=expected_ship_ids,
                expected_ship_ids_source=expected_ship_ids_source,
                blocked_evidence=blocked_evidence,
                blocked_evidence_rejection=blocked_evidence_rejection,
                settlement_completed_at=settlement_completed_at,
                mission_result_witness=mission_result_witness,
            )

    @staticmethod
    def _screen_name(screen: object) -> str | None:
        """The screen's own label, whatever enum/string the caller passed.

        ``StartupScreen`` carries it as ``state``; an enum carries it as
        ``value``; a plain string is already the label. Never the repr: a
        refusal row has to be greppable.
        """
        if screen is None:
            return None
        for attribute in ("state", "value"):
            label = getattr(screen, attribute, None)
            if isinstance(label, str) and label:
                return label
        return str(screen)

    def _settle_visible_reward_chain(
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
            if mission_result_witness is not None and not self.require_mission_result_witness:
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

            mission_result_capture_session = None
            dispatch_identity = None
            mission_result_inventory_baseline = None
            inventory_verdict = None
            if self.require_mission_result_witness:
                try:
                    dispatch_identity = self._single_open_dispatch_identity(fleet_id, snapshot)
                    if displayed == "HOME_PORT_RETURN_NOTICE":
                        mission_result_inventory_baseline = self._capture_reward_inventory_baseline(
                            fleet_id=fleet_id, identity=dispatch_identity
                        )
                        # The natural result request follows this click. The
                        # observer must be armed before the click, not after it.
                        mission_result_capture_session = self._arm_mission_result_observer(fleet_id)
                    else:
                        mission_result_inventory_baseline = self._load_reward_inventory_baseline(
                            fleet_id=fleet_id, identity=dispatch_identity
                        )
                        if mission_result_witness is None:
                            mission_result_witness = self._load_mission_result_witness(fleet_id)
                        if mission_result_witness is None:
                            raise ContinuousSettlementError(
                                "startup result/bonus screen has no durable mission-result witness"
                            )
                        witness_verdict = self._validate_mission_result_witness(
                            fleet_id=fleet_id,
                            witness=mission_result_witness,
                            identity=dispatch_identity,
                        )
                        inventory_verdict = self._verify_reward_inventory(
                            fleet_id=fleet_id,
                            identity=dispatch_identity,
                            before=mission_result_inventory_baseline,
                            witness=mission_result_witness,
                        )
                except ContinuousSettlementError as exc:
                    self._refuse_startup(
                        fleet_id,
                        str(exc),
                        screen.state,
                        failed_check="mission_result_witness_and_reward_receipt",
                        game_actions_sent=0,
                    )
                    raise StartupSettlementRefused(str(exc)) from exc

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
                try:
                    current_screen = self._advance_visible(
                        fleet_id, "RETURN_RESULT",
                        ui_call=lambda: self.ui_driver.collect_return_result(fleet_id=fleet_id),
                        steps=steps,
                        confirm_fn=lambda: self._confirm_phase(
                            fleet_id, FleetPhase.RETURNED_PENDING_UI, "RETURN_RESULT"
                        ),
                    )
                    if mission_result_capture_session is not None:
                        witness_verdict = self._capture_and_validate_mission_result(
                            fleet_id=fleet_id,
                            identity=dispatch_identity,
                            session=mission_result_capture_session,
                        )
                        mission_result_capture_session = None
                        if witness_verdict.witness is None or mission_result_inventory_baseline is None:
                            raise ContinuousSettlementError(
                                "mission-result witness or inventory baseline is missing"
                            )
                        inventory_verdict = self._verify_reward_inventory(
                            fleet_id=fleet_id,
                            identity=dispatch_identity,
                            before=mission_result_inventory_baseline,
                            witness=witness_verdict.witness,
                        )
                except Exception as exc:
                    if mission_result_capture_session is not None:
                        mission_result_capture_session.cancel()
                    if isinstance(exc, StartupSettlementRefused):
                        raise
                    raise StartupSettlementRefused(str(exc)) from exc
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
            if credited is not None:
                # P0-3 alignment (live 2026-11-06): this startup path used to
                # close the scheduler phase but not the durable dispatch
                # instance, so the ledger kept two confirmed instances of one
                # identical dispatch and the fleet's *next* return was refused
                # as UNATTRIBUTED. Best-effort exactly like
                # ``settle_one_returned``: the clicks are done and the reward
                # is claimed, and an attribution that cannot answer uniquely
                # leaves the record open for the next, fail-closed refusal -
                # it never raises over a settlement that already happened.
                try:
                    attribution = self._attribute_returned(fleet_id, credited)
                except ContinuousSettlementError:
                    attribution = None
                self._close_dispatch_record(fleet_id, attribution, credited)
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
                inventory_verdict=inventory_verdict,
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
