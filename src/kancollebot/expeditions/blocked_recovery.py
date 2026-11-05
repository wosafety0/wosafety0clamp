"""Evidence-based recovery of a BLOCKED fleet that is waiting for its result.

A blocked fleet has no supported way back: ``block()`` clears the Home Port
verification, no transition leaves ``BLOCKED``, and the monitor only holds.
Live 2026-11-05 two fleets were blocked that way - their dispatch clicks had
succeeded, only the post-dispatch confirmation had failed - and neither could
be settled again.

This module is the narrow opposite of a general ``unblock()``. It rebuilds the
facts that ``block()`` could not delete - the dispatch identity Poi confirmed
and the post-dispatch Home Port verification that came after it - from the
durable event log, and it only moves a fleet when every one of those facts is
present *and* the live observation agrees with them. Anything missing keeps
the fleet blocked, because a blocked fleet that needs a human is safer than a
fleet that resumes on a guess.

The evidence is read from the SQLite event log, not from process memory, so a
restarted process reaches the same verdict.

Rejection codes are stable and explainable; see ``REJECTION_CODES``.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from kancollebot.expeditions.continuous_scheduler import FleetPhase, MissionTuple
from kancollebot.expeditions.planner import ExpeditionFleetSnapshot

CONFIRMED_EVENT = "dispatch_confirmed_by_poi"
HOME_VERIFIED_EVENT = "dispatch_home_port_verified"
SETTLEMENT_COMPLETED_EVENTS = (
    "settlement_reward_claimed",
    "settlement_closed_without_reward_screens",
)

# The original two-frame Home Port rule of the dispatch controller. A recovery
# must not invent a looser standard than the dispatch itself was held to.
REQUIRED_HOME_PORT_STATES = ("HOME_PORT", "HOME_PORT")

#: Block reasons that have been reviewed and may be recovered. Every entry is a
#: settlement-side reconciliation failure: the fleet was out, its result came
#: back, and the state machine could not line the two up. Identity conflicts,
#: pending dispatch intents, supply failures and unknown reasons are NOT here.
RECOVERABLE_BLOCK_REASONS: tuple[str, ...] = (
    "settlement phase no longer matches returned mission",
    "continuous settlement stopped after ambiguous observation; "
    "manual reconciliation required",
)

#: Prefixes of reviewed block reasons whose tail is an exception type.
RECOVERABLE_BLOCK_REASON_PREFIXES: tuple[str, ...] = (
    "continuous dispatch stopped after ",
)

REJECTION_CODES: tuple[str, ...] = (
    "blocked_reason_not_recoverable",
    "blocked_mission_identity_missing",
    "dispatch_confirmation_missing",
    "home_port_verification_missing",
    "home_port_verification_order_invalid",
    "poi_snapshot_incomplete",
    "mission_identity_mismatch",
    "observed_ship_ids_mismatch",
    "other_fleets_awaiting_result",
    "reward_screen_not_proven",
    "settlement_completion_not_proven",
    "event_history_conflict",
)


class BlockedRecoveryError(RuntimeError):
    """The durable evidence cannot be read or does not describe a dispatch."""


def is_recoverable_block_reason(reason: str | None) -> bool:
    """True only for the block reasons that were explicitly reviewed."""
    if not isinstance(reason, str) or not reason.strip():
        return False
    if reason in RECOVERABLE_BLOCK_REASONS:
        return True
    return any(reason.startswith(prefix) for prefix in RECOVERABLE_BLOCK_REASON_PREFIXES)


@dataclass(frozen=True, slots=True)
class DispatchEvidence:
    """The dispatch identity and the Home Port proof that followed it."""

    fleet_id: int
    mission_id: int
    final_time_ms: int
    ship_ids: tuple[int, ...]
    confirmed_at: datetime
    home_port_verified_at: datetime
    home_port_evidence: str
    home_port_consecutive_states: tuple[str, ...]
    event_ids: tuple[int, ...] = ()

    def identity(self) -> MissionTuple:
        """The dispatch identity as a mission tuple, at active status."""
        return MissionTuple(
            mission_status=1,
            mission_id=self.mission_id,
            final_time_ms=self.final_time_ms,
            reserved=0,
            ship_ids=self.ship_ids,
        )

    def returned_identity(self) -> MissionTuple:
        """The same identity at returned status, for a settlement phase."""
        return MissionTuple(
            mission_status=2,
            mission_id=self.mission_id,
            final_time_ms=self.final_time_ms,
            reserved=0,
            ship_ids=self.ship_ids,
        )


@dataclass(frozen=True, slots=True)
class EvidenceLookup:
    """The result of reading one fleet's dispatch proof from the event log."""

    evidence: DispatchEvidence | None = None
    rejection: str | None = None
    confirmation_count: int = 0
    verification_count: int = 0


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _fleet_id_of(fields: Mapping[str, object]) -> int | None:
    fleet_id = fields.get("fleet_id")
    if isinstance(fleet_id, bool) or not isinstance(fleet_id, int):
        return None
    return fleet_id


def _event_id_of(row: Mapping[str, object]) -> int | None:
    for key in ("event_id", "id"):
        value = row.get(key)
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
    return None


def _home_port_states(fields: Mapping[str, object]) -> tuple[str, ...]:
    raw = fields.get("consecutive_states")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(item) for item in raw)


def read_dispatch_evidence(
    events: Iterable[Mapping[str, object]], fleet_id: int
) -> EvidenceLookup:
    """Rebuild one fleet's dispatch proof from the durable event log.

    Two events must line up: Poi's confirmation of the dispatch, and the Home
    Port verification that came *after* it for the same mission, with the
    original two-frame evidence. A verification without a confirmation, or one
    that is not later than the confirmation, proves nothing.

    Repeated dispatches are not ignored: when more than one confirmation has a
    matching verification and the identities differ, the history is ambiguous
    and the lookup reports ``event_history_conflict`` instead of guessing.
    """
    if isinstance(fleet_id, bool) or not isinstance(fleet_id, int) or fleet_id < 1:
        raise ValueError("fleet_id must be a positive integer")

    confirmations: list[tuple[int, datetime, int, tuple[int, ...], int | None]] = []
    verifications: list[tuple[int, datetime, str, tuple[str, ...], int | None]] = []
    for row in events:
        if not isinstance(row, Mapping):
            continue
        event = row.get("event")
        fields = row.get("fields")
        if not isinstance(event, str) or not isinstance(fields, Mapping):
            continue
        if _fleet_id_of(fields) != fleet_id:
            continue
        observed_at = _parse_timestamp(fields.get("observed_at"))
        event_id = _event_id_of(row)
        if event == CONFIRMED_EVENT:
            mission_id = fields.get("mission_id")
            final_time_ms = fields.get("final_time_ms")
            raw_ship_ids = fields.get("ship_ids")
            if (
                isinstance(mission_id, bool) or not isinstance(mission_id, int)
                or isinstance(final_time_ms, bool) or not isinstance(final_time_ms, int)
                or not isinstance(raw_ship_ids, (list, tuple))
                or not raw_ship_ids
                or observed_at is None
            ):
                continue
            try:
                ship_ids = tuple(int(ship_id) for ship_id in raw_ship_ids)
            except (TypeError, ValueError):
                continue
            confirmations.append((mission_id, observed_at, final_time_ms, ship_ids, event_id))
        elif event == HOME_VERIFIED_EVENT:
            mission_id = fields.get("mission_id")
            if isinstance(mission_id, bool) or not isinstance(mission_id, int):
                continue
            if observed_at is None:
                continue
            evidence = fields.get("evidence")
            verifications.append((
                mission_id,
                observed_at,
                str(evidence) if isinstance(evidence, str) else "",
                _home_port_states(fields),
                event_id,
            ))

    if not confirmations:
        return EvidenceLookup(rejection="dispatch_confirmation_missing")
    if not verifications:
        return EvidenceLookup(
            rejection="home_port_verification_missing",
            confirmation_count=len(confirmations),
        )

    candidates: list[tuple[tuple[int, datetime, int, tuple[int, ...], int | None], tuple[int, datetime, str, tuple[str, ...], int | None]]] = []
    order_invalid = False
    for confirmation in confirmations:
        mission_id = confirmation[0]
        confirmed_at = confirmation[1]
        matching = [
            verification
            for verification in verifications
            if verification[0] == mission_id and verification[1] > confirmed_at
        ]
        if matching:
            candidates.append((confirmation, matching[-1]))
        elif any(
            verification[0] == mission_id and verification[1] <= confirmed_at
            for verification in verifications
        ):
            # A verification for this mission exists, but not after this
            # confirmation: the ordering is broken, not merely absent.
            order_invalid = True

    if not candidates:
        return EvidenceLookup(
            rejection=(
                "home_port_verification_order_invalid"
                if order_invalid
                else "home_port_verification_missing"
            ),
            confirmation_count=len(confirmations),
            verification_count=len(verifications),
        )

    identities = {
        (item[0][0], item[0][2], item[0][3]) for item in candidates
    }
    if len(identities) > 1:
        # Two different dispatches each with their own verification: the
        # history cannot say which one this fleet is waiting for.
        return EvidenceLookup(
            rejection="event_history_conflict",
            confirmation_count=len(confirmations),
            verification_count=len(verifications),
        )

    confirmation, verification = candidates[-1]
    mission_id, confirmed_at, final_time_ms, ship_ids, confirm_id = confirmation
    _, verified_at, evidence_text, states, verify_id = verification
    if states and states != REQUIRED_HOME_PORT_STATES:
        # The dispatch controller's own rule: two consecutive HOME_PORT frames.
        return EvidenceLookup(
            rejection="home_port_verification_missing",
            confirmation_count=len(confirmations),
            verification_count=len(verifications),
        )
    if not evidence_text:
        return EvidenceLookup(
            rejection="home_port_verification_missing",
            confirmation_count=len(confirmations),
            verification_count=len(verifications),
        )
    return EvidenceLookup(
        evidence=DispatchEvidence(
            fleet_id=fleet_id,
            mission_id=mission_id,
            final_time_ms=final_time_ms,
            ship_ids=ship_ids,
            confirmed_at=confirmed_at,
            home_port_verified_at=verified_at,
            home_port_evidence=evidence_text,
            home_port_consecutive_states=states,
            event_ids=tuple(
                item for item in (confirm_id, verify_id) if item is not None
            ),
        ),
        confirmation_count=len(confirmations),
        verification_count=len(verifications),
    )


def read_settlement_completion(
    events: Iterable[Mapping[str, object]], fleet_id: int
) -> datetime | None:
    """When this fleet's reward chain was last proven closed, if ever.

    An idle fleet is not proof of a collected reward: the game clears
    ``api_mission`` the moment the result is accepted, and a human may have
    clicked it. Only a settlement event that recorded the chain as closed
    proves it.
    """
    if isinstance(fleet_id, bool) or not isinstance(fleet_id, int) or fleet_id < 1:
        raise ValueError("fleet_id must be a positive integer")
    latest: datetime | None = None
    for row in events:
        if not isinstance(row, Mapping):
            continue
        event = row.get("event")
        fields = row.get("fields")
        if not isinstance(event, str) or event not in SETTLEMENT_COMPLETED_EVENTS:
            continue
        if not isinstance(fields, Mapping) or _fleet_id_of(fields) != fleet_id:
            continue
        observed_at = _parse_timestamp(fields.get("observed_at"))
        if observed_at is None:
            observed_at = _parse_timestamp(row.get("created_at"))
        if observed_at is None:
            continue
        if latest is None or observed_at > latest:
            latest = observed_at
    return latest


@dataclass(frozen=True, slots=True)
class BlockedRecoveryDecision:
    """Whether a blocked fleet may re-enter a settlement phase."""

    allowed: bool
    reason: str
    target_phase: FleetPhase | None = None
    mission: MissionTuple | None = None
    evidence: DispatchEvidence | None = None


def _refuse(reason: str, evidence: DispatchEvidence | None = None) -> BlockedRecoveryDecision:
    return BlockedRecoveryDecision(allowed=False, reason=reason, evidence=evidence)


def evaluate_blocked_recovery(
    *,
    fleet_id: int,
    phase: FleetPhase,
    blocked_reason: str | None,
    scheduler_mission: MissionTuple | None,
    evidence: DispatchEvidence | None,
    snapshot: ExpeditionFleetSnapshot | None,
    other_fleets_awaiting_result: tuple[int, ...] = (),
    reward_screen_proven: bool = False,
    settlement_completed_at: datetime | None = None,
    evidence_rejection: str | None = None,
) -> BlockedRecoveryDecision:
    """Decide whether one blocked fleet may be recovered, and to what phase.

    The only permitted targets are ``RETURNED_PENDING_UI`` (its result is still
    waiting on screen) and ``RESUPPLY_REQUIRED`` (the game already accepted the
    result and only the reward screens were left). Every missing fact keeps
    the fleet blocked.
    """
    if phase is not FleetPhase.BLOCKED:
        return _refuse("blocked_reason_not_recoverable")
    if not is_recoverable_block_reason(blocked_reason):
        return _refuse("blocked_reason_not_recoverable")
    if evidence is None:
        return _refuse(evidence_rejection or "dispatch_confirmation_missing")
    if scheduler_mission is not None and not scheduler_mission.same_expedition(
        evidence.identity()
    ):
        return _refuse("mission_identity_mismatch", evidence)
    if other_fleets_awaiting_result:
        # Two unaccepted results: the reward screens name the expedition and
        # not the fleet, so one fleet's chain could be credited to the other.
        return _refuse("other_fleets_awaiting_result", evidence)
    if snapshot is None:
        return _refuse("poi_snapshot_incomplete", evidence)

    observed_ship_ids = tuple(snapshot.ship_ids)
    if not observed_ship_ids:
        return _refuse("observed_ship_ids_mismatch", evidence)
    if set(observed_ship_ids) != set(evidence.ship_ids):
        return _refuse("observed_ship_ids_mismatch", evidence)

    if snapshot.mission_status == 1:
        return _refuse("mission_identity_mismatch", evidence)

    if snapshot.mission_status == 2:
        if snapshot.mission_id != evidence.mission_id:
            return _refuse("mission_identity_mismatch", evidence)
        if snapshot.return_at is None:
            return _refuse("mission_identity_mismatch", evidence)
        final_time_ms = int(snapshot.return_at.timestamp() * 1000)
        if final_time_ms != evidence.final_time_ms:
            return _refuse("mission_identity_mismatch", evidence)
        return BlockedRecoveryDecision(
            allowed=True,
            reason="dispatch_evidence_and_returned_mission_agree",
            target_phase=FleetPhase.RETURNED_PENDING_UI,
            mission=evidence.returned_identity(),
            evidence=evidence,
        )

    if snapshot.mission_status == 0:
        # The game already accepted the result, so the fleet reads idle. An
        # idle fleet is not proof that the reward was collected: the chain
        # must be proven closed, and the reward page and its ship set proven
        # attributable.
        if settlement_completed_at is None:
            return _refuse("settlement_completion_not_proven", evidence)
        if not reward_screen_proven:
            return _refuse("reward_screen_not_proven", evidence)
        return BlockedRecoveryDecision(
            allowed=True,
            reason="dispatch_evidence_and_proven_settlement_agree",
            target_phase=FleetPhase.RESUPPLY_REQUIRED,
            mission=None,
            evidence=evidence,
        )

    return _refuse("mission_identity_mismatch", evidence)
