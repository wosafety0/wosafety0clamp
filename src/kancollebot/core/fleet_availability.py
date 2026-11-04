"""Account-specific fleet unlock FACTS, kept free of any task's policy.

``RuntimeConfig.all_fleet_ids`` is a game-level ceiling: KanColle unlocks
fleets 2, 3 and 4 progressively, so a real account may expose only ``(1, 2)``,
``(1, 2, 3)`` or ``(1, 2, 3, 4)``. Which fleets EXIST is an observed account
fact; which fleets a task MAY dispatch is that task's policy. Neither belongs
in this module except the normalization of the fact itself.

This module is at the bottom of the dependency graph: it imports only
``runtime_config`` (a leaf) and never imports ``adapters``, ``expeditions`` or
``task_runtime``. Both the expedition composition roots and the future
multi-task lease layer consume the same normalizer, so "unlocked" cannot mean
two different things in two entry points.
"""
from __future__ import annotations

from typing import Iterable

from kancollebot.runtime_config import RUNTIME_CONFIG, RuntimeConfig


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


def parse_unlocked_fleet_ids(text: str, *, config: RuntimeConfig = RUNTIME_CONFIG) -> tuple[int, ...]:
    """Parse a CLI ``--unlocked-fleet-ids`` value, failing closed on any ambiguity.

    Accepts a comma-separated list of positive integers (for example ``1,2,3``).
    An empty value, non-integer token or duplicate is rejected rather than
    silently interpreted as "all fleets exist".
    """
    if not isinstance(text, str) or not text.strip():
        raise FleetAvailabilityError("unlocked fleet ids must not be empty")
    tokens = [token.strip() for token in text.split(",")]
    if any(not token or not token.lstrip("+").isdigit() for token in tokens):
        raise FleetAvailabilityError(
            "unlocked fleet ids must be a comma-separated list of positive integers"
        )
    return normalize_unlocked_fleet_ids(tuple(int(token) for token in tokens), config=config)


__all__ = [
    "FleetAvailabilityError",
    "normalize_unlocked_fleet_ids",
    "parse_unlocked_fleet_ids",
]
