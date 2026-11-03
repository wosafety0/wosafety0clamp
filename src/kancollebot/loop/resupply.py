"""Offline resupply readiness for the continuous expedition loop.

Wraps the fail-closed full-supply preflight/verification so the loop can decide
``RESUPPLY_REQUIRED -> READY`` only from explicit, repeatable offline predicates.
This module never sends UI input; the live supply click stays gated elsewhere.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from kancollebot.adapters.poi.full_supply import (
    FullSupplyPlan,
    FullSupplyPreflightError,
    build_full_supply_plan,
    verify_full_supply_complete,
)


class ResupplyReadinessError(RuntimeError):
    """Offline resupply readiness cannot be determined safely."""


@dataclass(frozen=True, slots=True)
class ResupplyReadiness:
    """Explicit, offline predicate result for a resupply decision."""

    feasible: bool
    plan: FullSupplyPlan | None
    reason: str


def evaluate_resupply_readiness(
    fleets: Any,
    ships: Any,
    master_ships: Any,
    resources: Any,
    *,
    observed_at: datetime,
    now: datetime,
    max_age: timedelta = timedelta(seconds=30),
    expected_fleet_ids: tuple[int, ...] = (1, 2, 3),
) -> ResupplyReadiness:
    """Build a full-fleet supply plan from a fresh, verified observation.

    Returns ``feasible=False`` with a reason when the preflight rejects the plan
    (non-idle fleet, stale witness, insufficient resources, or bad shape)
    instead of raising, so the loop can log and wait without throwing.
    """
    try:
        plan = build_full_supply_plan(
            fleets,
            ships,
            master_ships,
            resources,
            source_freshness_verified=True,
            observed_at=observed_at,
            now=now,
            max_age=max_age,
            expected_fleet_ids=expected_fleet_ids,
        )
    except FullSupplyPreflightError as exc:
        return ResupplyReadiness(feasible=False, plan=None, reason=str(exc))
    except ValueError as exc:
        raise ResupplyReadinessError(f"resupply readiness inputs invalid: {exc}") from exc
    return ResupplyReadiness(
        feasible=True, plan=plan, reason="full-fleet resupply plan built"
    )


def verify_resupply_complete(
    plan: FullSupplyPlan,
    fleets: Any,
    ships: Any,
    master_ships: Any,
) -> bool:
    """Return true only when every ship in the plan is fully supplied."""
    return verify_full_supply_complete(plan, fleets, ships, master_ships)
