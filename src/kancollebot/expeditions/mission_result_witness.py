"""Read the expedition-result exchange the game client already made.

When a fleet finishes an expedition, the client asks the server for the
result and tells the server which fleet it is asking about. That exchange
states, in the game's own words, which fleet completed which expedition. It
is the first attribution evidence in this project that does not come from
looking at a screen.

The boundary matters, and this module is where it is enforced. The exchange
appears only when the *result* is settled, so it can say "this fleet
completed this expedition" and it can identify the fleet far better than a
screen can. It cannot say "this dispatch was accepted by the server at the
moment the button was pressed" - by the time the result exists, the dispatch
is long over. A mission-result witness therefore never stands in for
``dispatch_confirmed_by_poi``; it corroborates an identity, and a conflict
between the two is a refusal, not a preference.

The module is pure: it reads witnesses and durable dispatch records and
returns verdicts. It performs no I/O, opens no transports and sends nothing.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

#: The event name a mission-result witness is recorded under.
MISSION_RESULT_EVENT = "mission_result_observed"

#: Reasons a witness is refused. They are stable and explainable.
REJECTION_CODES: tuple[str, ...] = (
    "witness_malformed",
    "witness_fleet_mismatch",
    "witness_ship_set_mismatch",
    "witness_quest_name_mismatch",
    "witness_not_yet_observed",
    "witness_conflicts_with_dispatch_record",
    "no_dispatch_record_to_cross_check",
)


class MissionResultWitnessError(RuntimeError):
    """The witness cannot be read or does not describe a result."""


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


@dataclass(frozen=True, slots=True)
class MissionResultWitness:
    """One expedition-result exchange, as the game stated it."""

    fleet_id: int
    quest_name: str
    clear_result: int
    ship_ids: tuple[int, ...]
    requested_at: datetime | None
    response_received_at: datetime | None
    event_id: int | None = None

    def identity_key(self) -> tuple[str, int, int, tuple[int, ...]]:
        """What makes two witnesses the same observation."""
        return (self.quest_name, self.fleet_id, self.clear_result, self.ship_ids)


def _int_of(fields: Mapping[str, object], key: str) -> int | None:
    value = fields.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _ship_ids_of(fields: Mapping[str, object]) -> tuple[int, ...] | None:
    raw = fields.get("ship_ids")
    if not isinstance(raw, (list, tuple)) or not raw:
        return None
    ship_ids: list[int] = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            # The -1 placeholder is not a ship.
            continue
        if value not in ship_ids:
            ship_ids.append(value)
    if not ship_ids:
        return None
    return tuple(ship_ids)


def parse_witness(fields: Mapping[str, object]) -> MissionResultWitness:
    """Read one recorded witness, refusing anything that is not a result.

    The ship list is filtered here rather than at comparison time: the game
    puts a ``-1`` placeholder in front of the expedition's ships, and a
    placeholder must never be counted as one of the fleet's own ships.
    """
    if not isinstance(fields, Mapping):
        raise MissionResultWitnessError("witness fields must be a mapping")
    fleet_id = _int_of(fields, "fleet_id")
    quest_name = fields.get("quest_name")
    clear_result = _int_of(fields, "clear_result")
    ship_ids = _ship_ids_of(fields)
    if fleet_id is None or fleet_id < 1:
        raise MissionResultWitnessError("witness has no usable fleet id")
    if not isinstance(quest_name, str) or not quest_name.strip():
        raise MissionResultWitnessError("witness has no expedition name")
    if clear_result is None:
        raise MissionResultWitnessError("witness has no clear result")
    if ship_ids is None:
        raise MissionResultWitnessError("witness has no expedition ship")
    requested_at = _parse_timestamp(fields.get("requested_at"))
    response_received_at = _parse_timestamp(fields.get("response_received_at"))
    event_id = fields.get("event_id")
    if isinstance(event_id, bool) or not isinstance(event_id, int):
        event_id = None
    return MissionResultWitness(
        fleet_id=fleet_id,
        quest_name=quest_name.strip(),
        clear_result=clear_result,
        ship_ids=ship_ids,
        requested_at=requested_at,
        response_received_at=response_received_at,
        event_id=event_id,
    )


def read_witnesses(
    events: Iterable[Mapping[str, object]],
) -> tuple[MissionResultWitness, ...]:
    """Rebuild every recorded witness from the durable event log.

    Duplicate responses are recorded once: two rows describing the same
    exchange are one observation, and the first one wins so the audit trail
    keeps the order the events were written in.
    """
    witnesses: list[MissionResultWitness] = []
    seen: set[tuple[str, int, int, tuple[int, ...]]] = set()
    for row in events:
        if not isinstance(row, Mapping):
            continue
        if row.get("event") != MISSION_RESULT_EVENT:
            continue
        fields = row.get("fields")
        if not isinstance(fields, Mapping):
            continue
        try:
            witness = parse_witness(fields)
        except MissionResultWitnessError:
            continue
        key = witness.identity_key()
        if key in seen:
            continue
        seen.add(key)
        witnesses.append(witness)
    return tuple(witnesses)


@dataclass(frozen=True, slots=True)
class WitnessVerdict:
    """Whether a witness may be adopted as attribution evidence."""

    allowed: bool
    reason: str
    witness: MissionResultWitness | None = None

    @property
    def fleet_id(self) -> int | None:
        return self.witness.fleet_id if self.witness else None


def _refuse(reason: str, witness: MissionResultWitness | None = None) -> WitnessVerdict:
    return WitnessVerdict(allowed=False, reason=reason, witness=witness)


def evaluate_witness(
    *,
    witness: MissionResultWitness | None,
    expected_fleet_id: int,
    expected_ship_ids: tuple[int, ...] | None,
    expected_quest_name: str | None = None,
    observed_at: datetime | None = None,
) -> WitnessVerdict:
    """Decide whether one witness may be adopted for one fleet.

    A witness is adopted only when it agrees with the fleet it is claimed for
    on every point that can be checked. The expedition name is checked only
    when the caller knows which expedition to expect - two fleets may run the
    same expedition, and the name alone then says nothing.
    """
    if witness is None:
        return _refuse("witness_not_yet_observed")
    if witness.fleet_id != expected_fleet_id:
        # The game named a different fleet. That is a conflict, not a
        # mismatch to be resolved by preference.
        return _refuse("witness_fleet_mismatch", witness)
    if observed_at is not None and witness.response_received_at is not None:
        if witness.response_received_at > observed_at:
            # A witness from the future cannot be evidence about the past.
            return _refuse("witness_not_yet_observed", witness)
    if expected_ship_ids:
        if set(witness.ship_ids) != set(expected_ship_ids):
            return _refuse("witness_ship_set_mismatch", witness)
    if expected_quest_name:
        if witness.quest_name != expected_quest_name:
            return _refuse("witness_quest_name_mismatch", witness)
    return WitnessVerdict(
        allowed=True,
        reason="witness_agrees_with_fleet_identity",
        witness=witness,
    )


@dataclass(frozen=True, slots=True)
class DispatchRecord:
    """The dispatch identity a witness is cross-checked against."""

    fleet_id: int
    mission_id: int
    ship_ids: tuple[int, ...]
    quest_name: str | None = None
    observed_at: datetime | None = None


def cross_check_with_dispatch(
    *,
    witness: MissionResultWitness,
    record: DispatchRecord | None,
) -> WitnessVerdict:
    """Check a witness against the durable dispatch it supposedly belongs to.

    The witness says which fleet completed which expedition; the dispatch
    record says which fleet was sent where with which ships. When both exist
    they must agree. A disagreement is refused outright - it means one of the
    two records is about a different expedition, and picking either one would
    be a guess.
    """
    if record is None:
        # Without a dispatch record there is nothing to cross-check. The
        # witness may still be adopted as a plain observation of the fleet,
        # but it proves nothing about a dispatch.
        return WitnessVerdict(
            allowed=True,
            reason="witness_observed_without_dispatch_record",
            witness=witness,
        )
    if witness.fleet_id != record.fleet_id:
        return _refuse("witness_conflicts_with_dispatch_record", witness)
    if set(witness.ship_ids) != set(record.ship_ids):
        return _refuse("witness_conflicts_with_dispatch_record", witness)
    if record.quest_name is not None and witness.quest_name != record.quest_name:
        return _refuse("witness_conflicts_with_dispatch_record", witness)
    return WitnessVerdict(
        allowed=True,
        reason="witness_agrees_with_dispatch_record",
        witness=witness,
    )


def latest_witness_for_fleet(
    events: Iterable[Mapping[str, object]],
    fleet_id: int,
) -> MissionResultWitness | None:
    """The most recent witness recorded for one fleet, if any.

    Read from the durable log rather than from process memory, so a
    restarted process reaches the same conclusion about what was observed.
    """
    if isinstance(fleet_id, bool) or not isinstance(fleet_id, int) or fleet_id < 1:
        raise ValueError("fleet_id must be a positive integer")
    latest: MissionResultWitness | None = None
    for witness in read_witnesses(events):
        if witness.fleet_id != fleet_id:
            continue
        if latest is None:
            latest = witness
            continue
        # The log is append-only and ordered, so the last one wins. Ties are
        # broken by the exchange time when it is known.
        if latest.response_received_at is None:
            latest = witness
        elif witness.response_received_at is not None and (
            witness.response_received_at >= latest.response_received_at
        ):
            latest = witness
    return latest


def witness_fields(
    witness: MissionResultWitness,
) -> dict[str, object]:
    """The durable payload for one recorded witness."""
    return {
        "fleet_id": witness.fleet_id,
        "quest_name": witness.quest_name,
        "clear_result": witness.clear_result,
        "ship_ids": list(witness.ship_ids),
        "requested_at": (
            witness.requested_at.isoformat() if witness.requested_at else None
        ),
        "response_received_at": (
            witness.response_received_at.isoformat()
            if witness.response_received_at
            else None
        ),
        "game_api_requests": False,
        "game_input_sent": False,
    }
