"""Account-specific fleet unlock facts, kept separate from dispatch policy.

``RuntimeConfig.all_fleet_ids`` is a game-level ceiling; the EXPEDITION policy
(``dispatchable_fleet_ids`` via ``task_scope.fleet_policy_for``) says which
fleets THAT task may dispatch. Neither is a statement about a particular game
account, and neither is a global law about a fleet for other tasks. KanColle
unlocks fleets 2, 3 and 4 progressively, so a real account may expose only
``(1, 2)``, ``(1, 2, 3)`` or
``(1, 2, 3, 4)`` in ``info.fleets`` / the live Poi CDP fleet snapshot.

Treating the policy ceiling as an account fact is what makes a promoted Fleet 4
(or even Fleet 3) unsafe: the monitor would demand an observation for a fleet
that does not exist, or worse a dispatch target would be accepted with no
evidence that the game has that fleet at all.

The only trustworthy source is the observed fleet id set. This module
normalizes that fact and resolves an *effective* dispatch set from it. A
missing fleet always fails closed; it is never silently assumed to exist.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from kancollebot.runtime_config import RUNTIME_CONFIG, RuntimeConfig
from kancollebot.task_scope import TaskKind, TaskScopeError, require_task_enabled


class FleetAvailabilityError(ValueError):
    """The account's unlocked fleet set cannot support the requested dispatch."""


def normalize_unlocked_fleet_ids(
    values: Iterable[int], *, config: RuntimeConfig = RUNTIME_CONFIG
) -> tuple[int, ...]:
    """Validate an observed fleet id set and return it in canonical order.

    Poi exposes fleet ids as a contiguous sequence starting at 1. Rejecting a
    non-contiguous or out-of-policy set is deliberate: guessing what a partial
    observation means would turn a read error into a dispatch decision.
    """
    if isinstance(values, (str, bytes)):
        raise FleetAvailabilityError("unlocked fleet ids must be a sequence of integers")
    try:
        ids = tuple(values)
    except TypeError as exc:
        raise FleetAvailabilityError(
            "unlocked fleet ids must be a sequence of integers"
        ) from exc
    if not ids:
        raise FleetAvailabilityError("unlocked fleet ids must not be empty")
    for item in ids:
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            raise FleetAvailabilityError(
                f"unlocked fleet id {item!r} must be a positive integer"
            )
    if len(set(ids)) != len(ids):
        raise FleetAvailabilityError("unlocked fleet ids must be unique")
    canonical = tuple(sorted(ids))
    if canonical != tuple(range(1, len(canonical) + 1)):
        raise FleetAvailabilityError(
            f"unlocked fleet ids {list(canonical)} must be contiguous starting at 1"
        )
    unknown = tuple(fleet_id for fleet_id in canonical if fleet_id not in config.all_fleet_ids)
    if unknown:
        raise FleetAvailabilityError(
            f"observed fleets {list(unknown)} are outside the configured policy ceiling "
            f"{list(config.all_fleet_ids)}"
        )
    return canonical


@dataclass(frozen=True, slots=True)
class FleetAvailability:
    """The resolved account fleet picture for one run of one task."""

    unlocked_fleet_ids: tuple[int, ...]
    effective_dispatch_fleet_ids: tuple[int, ...]
    requested_but_not_unlocked: tuple[int, ...]
    locked_fleet_ids: tuple[int, ...]
    task_kind: str = TaskKind.EXPEDITION.value


def resolve_fleet_availability(
    unlocked_fleet_ids: Iterable[int],
    requested_fleet_ids: Sequence[int],
    *,
    config: RuntimeConfig = RUNTIME_CONFIG,
    strict: bool = False,
    task_kind: TaskKind | str = TaskKind.EXPEDITION,
) -> FleetAvailability:
    """Resolve which of the requested fleets may actually be dispatched.

    ``strict=True`` is for an explicitly named target (``--fleet-id``): a fleet
    that is not unlocked is a hard error, because silently substituting another
    fleet would dispatch a different fleet than the operator authorized.

    ``strict=False`` is for a candidate set (the continuous monitor): fleets the
    account has not unlocked are dropped from the candidate set. An empty
    result is always an error.

    ``task_kind`` selects the fleet POLICY. It defaults to expedition for
    backward compatibility, but a task that is declared and not yet implemented
    fails closed here instead of inheriting the expedition policy (which would,
    for example, wrongly forbid Fleet 1 for combat automation).
    """
    try:
        policy = require_task_enabled(task_kind, config=config)
    except TaskScopeError as exc:
        raise FleetAvailabilityError(f"task scope refused: {exc}") from exc
    unlocked = normalize_unlocked_fleet_ids(unlocked_fleet_ids, config=config)
    requested = tuple(requested_fleet_ids)
    if not requested:
        raise FleetAvailabilityError("requested dispatch fleets must not be empty")
    if len(set(requested)) != len(requested):
        raise FleetAvailabilityError("requested dispatch fleets must be unique")
    for fleet_id in requested:
        if isinstance(fleet_id, bool) or not isinstance(fleet_id, int):
            raise FleetAvailabilityError(
                f"requested fleet id {fleet_id!r} must be an integer"
            )

    allowed = policy.dispatchable_fleet_ids
    forbidden = tuple(fleet_id for fleet_id in requested if fleet_id not in allowed)
    if forbidden:
        reserved_hint = ""
        if any(fleet_id in policy.reserved_fleet_ids for fleet_id in forbidden):
            reserved_hint = (
                " (reserved for this task only; other task kinds declare their "
                "own policy)"
            )
        raise FleetAvailabilityError(
            f"Fleet(s) {list(forbidden)} are not permitted by policy; the "
            f"{policy.task_kind.value} dispatchable ceiling is {list(allowed)}"
            f"{reserved_hint}. Refusing to dispatch."
        )

    not_unlocked = tuple(
        fleet_id for fleet_id in requested if fleet_id not in unlocked
    )
    if strict and not_unlocked:
        raise FleetAvailabilityError(
            f"Fleet(s) {list(not_unlocked)} are not unlocked on this account; the "
            f"observed unlocked fleets are {list(unlocked)}. Refusing to dispatch; "
            "nothing was clicked."
        )

    effective = tuple(fleet_id for fleet_id in requested if fleet_id in unlocked)
    if not effective:
        raise FleetAvailabilityError(
            "none of the requested dispatch fleets are unlocked on this account; "
            "refusing to dispatch"
        )
    locked = tuple(
        fleet_id for fleet_id in config.all_fleet_ids if fleet_id not in unlocked
    )
    return FleetAvailability(
        unlocked_fleet_ids=unlocked,
        effective_dispatch_fleet_ids=effective,
        requested_but_not_unlocked=not_unlocked,
        locked_fleet_ids=locked,
        task_kind=policy.task_kind.value,
    )
