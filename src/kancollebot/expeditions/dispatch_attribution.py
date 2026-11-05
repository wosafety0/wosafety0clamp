"""Attribute one returned expedition to exactly one durable dispatch instance.

The reward screens name the expedition, not the fleet, and Poi's status-2 tuple
names the fleet but not which *attempt* it belongs to. When a fleet is
dispatched twice on the same mission with the same composition, screen
evidence and fleet id are both insufficient: only the dispatch instance
(``dispatch_registry``) can say which attempt this return closes.

This module is pure. It reads records and a live observation and returns a
verdict; it clicks nothing, writes nothing, and never falls back to "the first
candidate". ``UNATTRIBUTED`` covers *both* failure directions - no candidate
and more than one - because guessing in either direction is the same mistake.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from kancollebot.expeditions.dispatch_registry import DispatchIdentity

__all__ = [
    "AttributionVerdict",
    "UNATTRIBUTED",
    "attribute_dispatch",
]

#: The reason code used whenever no single dispatch instance owns the result.
UNATTRIBUTED = "UNATTRIBUTED"


@dataclass(frozen=True, slots=True)
class AttributionVerdict:
    """Whether one returned mission may be credited to one dispatch instance."""

    allowed: bool
    reason: str
    dispatch_id: str | None = None
    record: DispatchIdentity | None = None
    candidate_count: int = 0
    considered_dispatch_ids: tuple[str, ...] = ()

    @property
    def unattributed(self) -> bool:
        return not self.allowed and self.reason == UNATTRIBUTED

    def as_event_fields(self) -> dict[str, object]:
        """A log-friendly view; never includes a fabricated candidate."""
        return {
            "attribution_result": "attributed" if self.allowed else UNATTRIBUTED,
            "attribution_reason": self.reason,
            "dispatch_id": self.dispatch_id,
            "candidate_count": self.candidate_count,
            "considered_dispatch_ids": list(self.considered_dispatch_ids),
        }


def _refuse(
    reason: str,
    *,
    considered: tuple[str, ...] = (),
    candidate_count: int = 0,
) -> AttributionVerdict:
    return AttributionVerdict(
        allowed=False,
        reason=reason,
        dispatch_id=None,
        record=None,
        candidate_count=candidate_count,
        considered_dispatch_ids=considered,
    )


def attribute_dispatch(
    *,
    fleet_id: int,
    mission_id: int,
    ship_ids: Iterable[int],
    records: Iterable[DispatchIdentity],
    observed_at: datetime | None = None,
) -> AttributionVerdict:
    """Find the one dispatch instance that this return can belong to.

    Matching is conjunctive and deliberately narrow:

    * same fleet;
    * same mission id;
    * same ship *set* (order is not part of a composition's identity);
    * Poi-confirmed (a ``pending`` or ``unknown`` instance proves no dispatch,
      so it may never be credited with a result);
    * not already settled, and - when both times are known - confirmed no
      later than the observation that reported the return.

    ``observed_at`` is optional because the returned mission tuple carries its
    own ``final_time_ms``; callers that have a fresh observation should pass
    it so a future-dated or stale record cannot be preferred.
    """
    if isinstance(fleet_id, bool) or not isinstance(fleet_id, int) or fleet_id < 1:
        raise ValueError("fleet_id must be a positive integer")
    if isinstance(mission_id, bool) or not isinstance(mission_id, int) or mission_id < 1:
        raise ValueError("mission_id must be a positive integer")
    expected_ships = frozenset(int(ship_id) for ship_id in ship_ids)
    if not expected_ships:
        # No observed composition means the return cannot be identified at
        # all; that is a refusal, not "match anything".
        return _refuse(UNATTRIBUTED)

    candidates: list[DispatchIdentity] = []
    considered: list[str] = []
    for record in records:
        if record.fleet_id != fleet_id:
            continue
        considered.append(record.dispatch_id)
        if record.mission_id != mission_id:
            continue
        if frozenset(record.ship_ids) != expected_ships:
            continue
        if record.status != "confirmed":
            continue
        if observed_at is not None and record.confirmed_at is not None:
            if record.confirmed_at > observed_at:
                continue
        candidates.append(record)

    if not candidates:
        return _refuse(UNATTRIBUTED, considered=tuple(considered), candidate_count=0)
    if len(candidates) > 1:
        # Two confirmed instances with the same fleet, mission and ships. The
        # reward screen cannot distinguish them, so neither may be credited.
        return _refuse(
            UNATTRIBUTED,
            considered=tuple(item.dispatch_id for item in candidates),
            candidate_count=len(candidates),
        )

    record = candidates[0]
    return AttributionVerdict(
        allowed=True,
        reason="single_matching_dispatch_instance",
        dispatch_id=record.dispatch_id,
        record=record,
        candidate_count=1,
        considered_dispatch_ids=tuple(considered),
    )
