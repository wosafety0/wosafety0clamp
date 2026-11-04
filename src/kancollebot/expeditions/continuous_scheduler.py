"""Offline state machine for continuous multi-fleet expedition scheduling.

This module owns fleet lifecycle invariants and optional durable state-store
integration. It performs no Poi I/O, screen recognition, game input, or waiting.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import Enum
from typing import Iterator, Protocol

from kancollebot.adapters.poi.ui_operation_lock import poi_ui_operation
from kancollebot.runtime_config import RUNTIME_CONFIG


class FleetPhase(str, Enum):
    IDLE = "IDLE"
    DISPATCHING = "DISPATCHING"
    EXPEDITION_ACTIVE = "EXPEDITION_ACTIVE"
    RETURNED_PENDING_UI = "RETURNED_PENDING_UI"
    RETURN_RESULT = "RETURN_RESULT"
    BONUS_ITEMS = "BONUS_ITEMS"
    RESUPPLY_REQUIRED = "RESUPPLY_REQUIRED"
    READY = "READY"
    BLOCKED = "BLOCKED"


class SchedulerInvariantError(RuntimeError):
    """A fleet transition violates the continuous scheduler contract."""


@dataclass(frozen=True, slots=True)
class MissionTuple:
    mission_status: int
    mission_id: int
    final_time_ms: int
    reserved: int
    ship_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        numeric_fields = (self.mission_status, self.mission_id, self.final_time_ms, self.reserved)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in numeric_fields):
            raise ValueError("mission tuple fields must be integers")
        if self.mission_status not in (1, 2):
            raise ValueError("mission_status must be active (1) or returned (2)")
        if self.mission_id < 1 or self.final_time_ms < 1 or self.reserved < 0:
            raise ValueError("mission tuple numeric fields are invalid")
        if not isinstance(self.ship_ids, tuple) or not self.ship_ids:
            raise ValueError("mission ship_ids must be a non-empty tuple")
        if any(isinstance(ship_id, bool) or not isinstance(ship_id, int) or ship_id < 1
               for ship_id in self.ship_ids):
            raise ValueError("mission ship_ids must be positive integers")
        if len(set(self.ship_ids)) != len(self.ship_ids):
            raise ValueError("mission ship_ids must be unique")

    def same_expedition(self, other: "MissionTuple") -> bool:
        """Compare expedition identity while allowing status 1 -> 2 on return."""
        return (
            isinstance(other, MissionTuple)
            and self.mission_id == other.mission_id
            and self.final_time_ms == other.final_time_ms
            and self.reserved == other.reserved
            and self.ship_ids == other.ship_ids
        )


@dataclass(frozen=True, slots=True)
class FleetRuntime:
    fleet_id: int
    phase: FleetPhase = FleetPhase.IDLE
    mission: MissionTuple | None = None
    home_port_verified: bool = False
    blocked_reason: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.fleet_id, bool) or not isinstance(self.fleet_id, int) or self.fleet_id < 1:
            raise ValueError("fleet_id must be a positive integer")
        if not isinstance(self.phase, FleetPhase):
            raise ValueError("phase must be a FleetPhase")
        if self.mission is not None and not isinstance(self.mission, MissionTuple):
            raise ValueError("mission must be a MissionTuple or None")
        if not isinstance(self.home_port_verified, bool):
            raise ValueError("home_port_verified must be a bool")
        if self.phase is FleetPhase.EXPEDITION_ACTIVE and (
            self.mission is None or self.mission.mission_status != 1
        ):
            raise ValueError("active fleet requires an active mission tuple with status 1")
        if self.phase in (
            FleetPhase.RETURNED_PENDING_UI, FleetPhase.RETURN_RESULT, FleetPhase.BONUS_ITEMS
        ) and (self.mission is None or self.mission.mission_status != 2):
            raise ValueError("returned/settlement phases require a returned mission tuple with status 2")
        if self.home_port_verified and self.phase is not FleetPhase.EXPEDITION_ACTIVE:
            raise ValueError("Home Port verification is valid only for an active expedition")
        if self.phase is FleetPhase.BLOCKED:
            if not isinstance(self.blocked_reason, str) or not self.blocked_reason.strip():
                raise ValueError("blocked fleet requires a stop reason")
        elif self.blocked_reason is not None:
            raise ValueError("blocked_reason is valid only for BLOCKED phase")


class FleetStateStore(Protocol):
    def load_all(self) -> tuple[FleetRuntime, ...]:
        ...

    def save(self, state: FleetRuntime) -> None:
        ...


class ContinuousExpeditionScheduler:
    """Pure fleet lifecycle coordinator; only configured fleets are dispatchable."""

    def __init__(
        self,
        *,
        dispatch_fleet_ids: tuple[int, ...] = (2, 3),
        read_only_fleet_ids: tuple[int, ...] = (1,),
        # No fleet is statically deferred any more. Whether Fleet 4 (or 3)
        # exists is an ACCOUNT fact resolved by the caller from the observed
        # Poi fleet set, then passed in per run. See fleet_availability.
        deferred_fleet_ids: tuple[int, ...] = RUNTIME_CONFIG.deferred_fleet_ids,
        state_store: FleetStateStore | None = None,
    ) -> None:
        groups = (dispatch_fleet_ids, read_only_fleet_ids, deferred_fleet_ids)
        flattened = tuple(item for group in groups for item in group)
        if any(item < 1 for item in flattened):
            raise ValueError("fleet IDs must be positive")
        if len(set(flattened)) != len(flattened):
            raise ValueError("fleet roles must be disjoint")
        if not dispatch_fleet_ids or len(set(dispatch_fleet_ids)) != len(dispatch_fleet_ids):
            raise ValueError("dispatch fleet IDs must be non-empty and unique")
        self.dispatch_fleet_ids = dispatch_fleet_ids
        self.read_only_fleet_ids = read_only_fleet_ids
        self.deferred_fleet_ids = deferred_fleet_ids
        self._fleets = {fleet_id: FleetRuntime(fleet_id) for fleet_id in dispatch_fleet_ids}
        self._state_store = state_store
        if state_store is not None:
            restored_ids = set()
            for restored in state_store.load_all():
                if restored.fleet_id not in self._fleets:
                    raise SchedulerInvariantError(
                        f"persisted fleet {restored.fleet_id} is outside the configured dispatch scope"
                    )
                if restored.fleet_id in restored_ids:
                    raise SchedulerInvariantError(
                        f"duplicate persisted state for fleet {restored.fleet_id}"
                    )
                restored_ids.add(restored.fleet_id)
                self._fleets[restored.fleet_id] = restored

    def fleet(self, fleet_id: int) -> FleetRuntime:
        try:
            return self._fleets[fleet_id]
        except KeyError as exc:
            if fleet_id in self.read_only_fleet_ids:
                raise SchedulerInvariantError(f"Fleet {fleet_id} is read-only") from exc
            if fleet_id in self.deferred_fleet_ids:
                raise SchedulerInvariantError(f"Fleet {fleet_id} is deferred until unlocked") from exc
            raise SchedulerInvariantError(f"Fleet {fleet_id} is not configured for dispatch") from exc

    def states(self) -> tuple[FleetRuntime, ...]:
        return tuple(self._fleets[fleet_id] for fleet_id in self.dispatch_fleet_ids)

    def mark_ready(self, fleet_id: int) -> FleetRuntime:
        current = self.fleet(fleet_id)
        if current.phase not in (FleetPhase.IDLE, FleetPhase.RESUPPLY_REQUIRED):
            raise SchedulerInvariantError("fleet can become READY only from IDLE or RESUPPLY_REQUIRED")
        return self._set(replace(current, phase=FleetPhase.READY, mission=None,
                                 home_port_verified=False, blocked_reason=None))

    def begin_dispatch(self, fleet_id: int) -> FleetRuntime:
        current = self.fleet(fleet_id)
        if current.phase is not FleetPhase.READY:
            raise SchedulerInvariantError("dispatch requires a READY fleet")
        return self._set(replace(current, phase=FleetPhase.DISPATCHING))

    def confirm_dispatch(self, fleet_id: int, mission: MissionTuple) -> FleetRuntime:
        current = self.fleet(fleet_id)
        if current.phase is not FleetPhase.DISPATCHING:
            raise SchedulerInvariantError("dispatch confirmation requires DISPATCHING")
        if mission.mission_status != 1:
            raise SchedulerInvariantError("dispatch confirmation requires active mission status 1")
        return self._set(replace(current, phase=FleetPhase.EXPEDITION_ACTIVE,
                                 mission=mission, home_port_verified=False))

    def confirm_home_port_after_dispatch(
        self, fleet_id: int, *, ui_state: str, observed_mission: MissionTuple
    ) -> FleetRuntime:
        current = self.fleet(fleet_id)
        if current.phase is not FleetPhase.EXPEDITION_ACTIVE or current.mission is None:
            raise SchedulerInvariantError("Home Port verification requires an active dispatch")
        if ui_state != "HOME_PORT":
            raise SchedulerInvariantError("dispatch must return to HOME_PORT before waiting")
        if observed_mission != current.mission or observed_mission.mission_status != 1:
            raise SchedulerInvariantError("mission tuple changed during Home Port navigation")
        return self._set(replace(current, home_port_verified=True))

    def may_wait_for_return(self, fleet_id: int) -> bool:
        current = self.fleet(fleet_id)
        return current.phase is FleetPhase.EXPEDITION_ACTIVE and current.home_port_verified

    def confirm_returned(self, fleet_id: int, observed_mission: MissionTuple) -> FleetRuntime:
        current = self.fleet(fleet_id)
        if not self.may_wait_for_return(fleet_id):
            raise SchedulerInvariantError("return monitoring is forbidden before Home Port verification")
        if observed_mission.mission_status != 2 or current.mission is None:
            raise SchedulerInvariantError("return confirmation requires returned mission status 2")
        if not observed_mission.same_expedition(current.mission):
            raise SchedulerInvariantError("returned mission identity does not match dispatched mission")
        return self._set(replace(
            current, phase=FleetPhase.RETURNED_PENDING_UI, mission=observed_mission,
            home_port_verified=False
        ))

    def confirm_return_result(self, fleet_id: int, *, ui_state: str) -> FleetRuntime:
        current = self.fleet(fleet_id)
        if current.phase is not FleetPhase.RETURNED_PENDING_UI or ui_state != "RETURN_RESULT":
            raise SchedulerInvariantError("RETURN_RESULT requires a returned fleet and matching UI state")
        return self._set(replace(current, phase=FleetPhase.RETURN_RESULT))

    def confirm_bonus_items(self, fleet_id: int, *, ui_state: str) -> FleetRuntime:
        current = self.fleet(fleet_id)
        if current.phase is not FleetPhase.RETURN_RESULT or ui_state != "BONUS_ITEMS":
            raise SchedulerInvariantError("BONUS_ITEMS requires RETURN_RESULT and matching UI state")
        return self._set(replace(current, phase=FleetPhase.BONUS_ITEMS))

    def confirm_reward_claimed(self, fleet_id: int, *, claimed: bool) -> FleetRuntime:
        current = self.fleet(fleet_id)
        if current.phase is not FleetPhase.BONUS_ITEMS or not claimed:
            raise SchedulerInvariantError("reward completion requires a confirmed claim from BONUS_ITEMS")
        return self._set(replace(current, phase=FleetPhase.RESUPPLY_REQUIRED,
                                 mission=None, home_port_verified=False))

    def confirm_resupply(self, fleet_id: int, *, fully_supplied: bool) -> FleetRuntime:
        current = self.fleet(fleet_id)
        if current.phase is not FleetPhase.RESUPPLY_REQUIRED or not fully_supplied:
            raise SchedulerInvariantError("READY requires verified resupply")
        return self._set(replace(current, phase=FleetPhase.READY))

    def block(self, fleet_id: int, reason: str) -> FleetRuntime:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("blocked reason must not be empty")
        current = self.fleet(fleet_id)
        return self._set(replace(
            current, phase=FleetPhase.BLOCKED, blocked_reason=reason,
            home_port_verified=False,
        ))

    def _set(self, state: FleetRuntime) -> FleetRuntime:
        if self._state_store is not None:
            self._state_store.save(state)
        self._fleets[state.fleet_id] = state
        return state


class UiOperationArbiter:
    """Cross-process mutex for all operations against the shared Poi window."""

    @contextmanager
    def operation(self, label: str) -> Iterator[None]:
        with poi_ui_operation(label):
            yield
