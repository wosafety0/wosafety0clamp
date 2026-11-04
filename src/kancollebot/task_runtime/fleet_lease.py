"""DESIGN REFERENCE — fleet lease (NOT wired into production).

Today ``fleet_runtime`` and ``dispatch_intents`` both key their SQLite rows by
``fleet_id`` alone, so two tasks cannot safely own two different views of the
same fleet. A lease makes ownership explicit BEFORE any such migration.

Invariant: one fleet_id has at most one unreleased lease, regardless of task,
because the game only has one physical Fleet 2. Expired leases still block
acquire() until explicitly reclaimed; silently stealing a fleet from a crashed
run is worse than refusing. This reference implementation is in-memory only.
See docs/DESIGN_multi_task_runtime.md.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

# P0 R1: account fleet facts are task-neutral. Import them from core, not from
# the expedition package, so the future lease layer never depends on one task's
# implementation. Same exception type and normalizer as the expedition roots.
from kancollebot.core.fleet_availability import (
    FleetAvailabilityError,
    normalize_unlocked_fleet_ids,
)
from kancollebot.runtime_config import RUNTIME_CONFIG, RuntimeConfig
from kancollebot.task_scope import (
    TaskKind,
    TaskScopeError,
    coerce_task_kind,
    require_task_enabled,
)

from .registry import TaskRegistry, TaskRegistryError


class FleetLeaseError(RuntimeError):
    """A lease could not be acquired, renewed or released safely."""


class LeaseConflictError(FleetLeaseError):
    """The fleet is already owned by another (or an unreleased) lease."""


class LeaseOwnerError(FleetLeaseError):
    """A run tried to renew or release a lease it does not own."""


@dataclass(frozen=True, slots=True)
class FleetLease:
    lease_id: str
    task_kind: TaskKind
    fleet_id: int
    owner_run_id: str
    purpose: str
    acquired_at: datetime
    expires_at: datetime
    ttl_seconds: float

    def is_expired(self, as_of: datetime | None = None) -> bool:
        moment = as_of or datetime.now(timezone.utc)
        return moment >= self.expires_at

    def is_active(self, as_of: datetime | None = None) -> bool:
        return not self.is_expired(as_of)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FleetLeaseTable:
    """Offline, injectable-clock lease table. No SQLite, no Poi, no clicks."""

    def __init__(
        self,
        *,
        config: RuntimeConfig = RUNTIME_CONFIG,
        registry: TaskRegistry | None = None,
        unlocked_fleet_ids: Iterable[int] | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._config = config
        self._registry = registry
        self._unlocked_fleet_ids = (
            config.all_fleet_ids if unlocked_fleet_ids is None else unlocked_fleet_ids
        )
        self._clock = clock
        self._leases: dict[str, FleetLease] = {}

    def _now(self) -> datetime:
        moment = self._clock()
        if not isinstance(moment, datetime) or moment.tzinfo is None:
            raise FleetLeaseError("lease clock must return a timezone-aware datetime")
        return moment

    def _require_enabled(self, kind: TaskKind) -> None:
        try:
            if self._registry is not None:
                self._registry.require_enabled(kind)
            else:
                require_task_enabled(kind, config=self._config)
        except (TaskScopeError, TaskRegistryError) as exc:
            raise FleetLeaseError(
                f"task {kind.value!r} is not enabled for fleet leasing: {exc}"
            ) from exc

    def acquire(
        self,
        task_kind: TaskKind | str,
        fleet_id: int,
        *,
        owner_run_id: str,
        purpose: str,
        ttl_seconds: float,
        unlocked_fleet_ids: Iterable[int] | None = None,
    ) -> FleetLease:
        kind = coerce_task_kind(task_kind)
        self._require_enabled(kind)
        if not isinstance(owner_run_id, str) or not owner_run_id.strip():
            raise FleetLeaseError("owner_run_id must be a non-empty string")
        if not isinstance(purpose, str) or not purpose.strip():
            raise FleetLeaseError("purpose must be a non-empty string")
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, (int, float))
            or not math.isfinite(ttl_seconds)
            or ttl_seconds <= 0
        ):
            raise FleetLeaseError("ttl_seconds must be finite and positive")
        if isinstance(fleet_id, bool) or not isinstance(fleet_id, int) or fleet_id < 1:
            raise FleetLeaseError("fleet_id must be a positive integer")

        observed = (
            self._unlocked_fleet_ids if unlocked_fleet_ids is None
            else unlocked_fleet_ids
        )
        try:
            unlocked = normalize_unlocked_fleet_ids(observed, config=self._config)
        except FleetAvailabilityError as exc:
            raise FleetLeaseError(f"invalid unlocked fleet set: {exc}") from exc
        if fleet_id not in unlocked:
            raise FleetLeaseError(
                f"fleet {fleet_id} is not unlocked on this account; observed "
                f"{list(unlocked)}. nothing was clicked."
            )

        now = self._now()
        for lease in self._leases.values():
            if lease.fleet_id != fleet_id:
                continue
            if lease.is_expired(now):
                raise LeaseConflictError(
                    f"an EXPIRED lease {lease.lease_id} still holds fleet {fleet_id} "
                    f"(owner {lease.owner_run_id!r}); call reclaim_expired() "
                    "explicitly before re-acquiring"
                )
            raise LeaseConflictError(
                f"fleet {fleet_id} is held by {lease.task_kind.value!r} "
                f"lease {lease.lease_id} (owner {lease.owner_run_id!r}); "
                "one fleet cannot belong to two tasks"
            )

        lease = FleetLease(
            lease_id=uuid.uuid4().hex,
            task_kind=kind,
            fleet_id=fleet_id,
            owner_run_id=owner_run_id,
            purpose=purpose,
            acquired_at=now,
            expires_at=now + timedelta(seconds=float(ttl_seconds)),
            ttl_seconds=float(ttl_seconds),
        )
        self._leases[lease.lease_id] = lease
        return lease

    def renew(
        self, lease_id: str, owner_run_id: str, ttl_seconds: float
    ) -> FleetLease:
        lease = self._leases.get(lease_id)
        if lease is None:
            raise LeaseOwnerError(f"unknown lease {lease_id!r}")
        if lease.owner_run_id != owner_run_id:
            raise LeaseOwnerError(
                f"lease {lease_id!r} is owned by {lease.owner_run_id!r}, not "
                f"{owner_run_id!r}"
            )
        if lease.is_expired(self._now()):
            raise LeaseOwnerError(
                f"lease {lease_id!r} has expired; reclaim_expired() first"
            )
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, (int, float))
            or not math.isfinite(ttl_seconds)
            or ttl_seconds <= 0
        ):
            raise FleetLeaseError("ttl_seconds must be finite and positive")
        now = self._now()
        renewed = FleetLease(
            lease_id=lease.lease_id,
            task_kind=lease.task_kind,
            fleet_id=lease.fleet_id,
            owner_run_id=lease.owner_run_id,
            purpose=lease.purpose,
            acquired_at=now,
            expires_at=now + timedelta(seconds=float(ttl_seconds)),
            ttl_seconds=float(ttl_seconds),
        )
        self._leases[lease_id] = renewed
        return renewed

    def release(self, lease_id: str, owner_run_id: str) -> FleetLease:
        lease = self._leases.get(lease_id)
        if lease is None:
            raise LeaseOwnerError(f"unknown lease {lease_id!r}")
        if lease.owner_run_id != owner_run_id:
            raise LeaseOwnerError(
                f"lease {lease_id!r} is owned by {lease.owner_run_id!r}, not "
                f"{owner_run_id!r}"
            )
        del self._leases[lease_id]
        return lease

    def reclaim_expired(self) -> tuple[str, ...]:
        """Explicit, auditable reclamation. Never called implicitly by acquire()."""
        now = self._now()
        expired = tuple(
            lease_id for lease_id, lease in self._leases.items()
            if lease.is_expired(now)
        )
        for lease_id in expired:
            del self._leases[lease_id]
        return expired

    def holder(self, fleet_id: int) -> FleetLease | None:
        """Any unreleased lease for a fleet, including an expired one."""
        for lease in self._leases.values():
            if lease.fleet_id == fleet_id:
                return lease
        return None

    def active_leases(self) -> tuple[FleetLease, ...]:
        now = self._now()
        return tuple(lease for lease in self._leases.values() if lease.is_active(now))

    def snapshot(self) -> tuple[FleetLease, ...]:
        return tuple(self._leases.values())

    def __len__(self) -> int:
        return len(self._leases)
