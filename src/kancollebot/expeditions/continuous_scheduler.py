"""Offline state machine for continuous multi-fleet expedition scheduling.

This module owns fleet lifecycle invariants and optional durable state-store
integration. It performs no Poi I/O, screen recognition, game input, or waiting.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol

# P0 R1: ``UiOperationArbiter`` is a task-neutral mutex wrapper. Its canonical
# definition now lives in the adapters layer (where the mutex itself lives);
# the re-export below keeps the same class object for every legacy import and
# isinstance check. No expedition rule is embedded in it.
from kancollebot.adapters.poi.ui_operation_lock import UiOperationArbiter
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

    def adopt_observed_mission(
        self,
        fleet_id: int,
        mission: MissionTuple,
        *,
        home_port_verified: bool,
    ) -> FleetRuntime:
        """Adopt a mission this scheduler did not dispatch, from one observation.

        A restart, or an expedition started by hand, leaves the durable state at
        IDLE while the game says a fleet is out. Blocking is the fail-closed
        answer and stays the default; this method is the narrow opposite: the
        caller has already matched the observed mission identity against the
        configured mission and the composition gate, so the only missing fact is
        *which* phase the fleet occupies.

        Two targets, because the phase invariants are what keep settlement
        honest: an active mission (status 1) becomes EXPEDITION_ACTIVE and needs
        the caller to have confirmed Home Port in the same observation window;
        an already-returned mission (status 2) becomes RETURNED_PENDING_UI, which
        is exactly the state that waits for the reward screens.
        """
        current = self.fleet(fleet_id)
        if current.phase not in (
            FleetPhase.IDLE, FleetPhase.READY, FleetPhase.RESUPPLY_REQUIRED
        ):
            raise SchedulerInvariantError(
                "only an idle, ready or resupply-pending fleet can adopt an observed mission"
            )
        if not isinstance(mission, MissionTuple):
            raise SchedulerInvariantError("adoption requires a mission tuple")
        if mission.mission_status == 1:
            if not home_port_verified:
                raise SchedulerInvariantError(
                    "adopting an active mission requires a confirmed Home Port"
                )
            return self._set(replace(
                current, phase=FleetPhase.EXPEDITION_ACTIVE, mission=mission,
                home_port_verified=True,
            ))
        if mission.mission_status == 2:
            return self._set(replace(
                current, phase=FleetPhase.RETURNED_PENDING_UI, mission=mission,
                home_port_verified=False,
            ))
        raise SchedulerInvariantError("adoption requires mission status 1 or 2")

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

    def confirm_settlement_already_complete(
        self, fleet_id: int, *, evidence: str
    ) -> FleetRuntime:
        """Close a settlement whose reward screens are no longer on display.

        The reward request is accepted the moment the result screen opens, so a
        process that restarts after that point finds an idle fleet and a plain
        Home Port instead of a reward chain (live 2026-11-05). Waiting for a
        screen that will never come strands the fleet forever; this transition
        records the UI evidence the caller actually has and moves to resupply,
        which is the phase the fleet genuinely needs.
        """
        current = self.fleet(fleet_id)
        if current.phase not in (
            FleetPhase.RETURNED_PENDING_UI, FleetPhase.RETURN_RESULT, FleetPhase.BONUS_ITEMS
        ):
            raise SchedulerInvariantError(
                "only a settlement-phase fleet can be closed as already settled"
            )
        if not isinstance(evidence, str) or not evidence.strip():
            raise SchedulerInvariantError("closing a settlement requires UI evidence")
        return self._set(replace(current, phase=FleetPhase.RESUPPLY_REQUIRED,
                                 mission=None, home_port_verified=False))

    def confirm_active_fleet_settled(
        self, fleet_id: int, *, evidence: str
    ) -> FleetRuntime:
        """Move an active fleet whose expedition is over to ``RESUPPLY_REQUIRED``.

        The game clears ``api_mission`` when the expedition result is accepted,
        so a fleet that was out on an expedition reads idle from then on - even
        while the reward screens are still open. Once those screens are closed,
        the fleet has nothing left to do but refuel, and this transition says so.

        It is deliberately narrow: the phase must be an active expedition (or
        an idle fleet with a mission), the fleet must still carry that mission,
        and the caller must name the evidence that closed the chain.
        """
        current = self.fleet(fleet_id)
        if current.phase not in (FleetPhase.EXPEDITION_ACTIVE, FleetPhase.IDLE):
            raise SchedulerInvariantError(
                "only an active or idle fleet can be recorded as settled"
            )
        if current.mission is None:
            raise SchedulerInvariantError(
                "a settled fleet must still carry the expedition it was on"
            )
        if not isinstance(evidence, str) or not evidence.strip():
            raise SchedulerInvariantError("recording a settlement requires named evidence")
        return self._set(replace(
            current, phase=FleetPhase.RESUPPLY_REQUIRED, mission=None,
            home_port_verified=False, blocked_reason=None,
        ))

    def require_resupply(self, fleet_id: int) -> FleetRuntime:
        """Queue an idle fleet for supply, because dispatch preconditions failed.

        Core logic Step 1: a fleet that is idle but not fully supplied must have
        "the normal in-game resupply action" performed before dispatch, not be
        reported as unready forever. Without this transition a cold-start fleet
        sits in IDLE with ``NEEDS_RESUPPLY``: the monitor says ``not_ready``,
        ``_pending_kind`` sees no outstanding work, and no controller is ever
        asked to supply it -- a permanent dead end.

        Only IDLE may enter. A READY fleet that lost supply is a contradiction
        with the verification that produced READY, so the monitor blocks it
        instead of silently re-entering supply.
        """
        current = self.fleet(fleet_id)
        if current.phase is not FleetPhase.IDLE:
            raise SchedulerInvariantError(
                "only an IDLE fleet may be queued for resupply"
            )
        return self._set(replace(current, phase=FleetPhase.RESUPPLY_REQUIRED))

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

    def recover_blocked_returned(
        self, fleet_id: int, *, mission: MissionTuple, evidence: str
    ) -> FleetRuntime:
        """Move a blocked fleet back into a settlement phase, with proof.

        This is deliberately not an ``unblock()``. A blocked fleet may only
        re-enter the lifecycle as a *returned* fleet whose identity the caller
        has proven from the durable dispatch evidence - the dispatch Poi
        confirmed and the Home Port verification that followed it - and whose
        returned mission matches that proof. The only permitted target is
        ``RETURNED_PENDING_UI``, the phase that waits for the reward screens.

        Anything else keeps the fleet blocked: a blocked fleet that needs a
        human is safer than a fleet that resumes on a guess.
        """
        current = self.fleet(fleet_id)
        if current.phase is not FleetPhase.BLOCKED:
            raise SchedulerInvariantError("only a blocked fleet can be recovered")
        if not isinstance(mission, MissionTuple) or mission.mission_status != 2:
            raise SchedulerInvariantError(
                "a recovered fleet needs a returned mission tuple (status 2)"
            )
        if current.mission is not None and not current.mission.same_expedition(mission):
            raise SchedulerInvariantError(
                "the recovered mission must be the blocked fleet's own dispatch"
            )
        if not isinstance(evidence, str) or not evidence.strip():
            raise SchedulerInvariantError("recovery requires named evidence")
        return self._set(replace(
            current, phase=FleetPhase.RETURNED_PENDING_UI, mission=mission,
            home_port_verified=False, blocked_reason=None,
        ))

    def recover_blocked_settled(
        self, fleet_id: int, *, evidence: str
    ) -> FleetRuntime:
        """Move a blocked fleet straight to ``RESUPPLY_REQUIRED``.

        The game already accepted the expedition result, so the fleet reads
        idle and only the supply is left. The caller must have proven that
        with the same durable evidence the returned path requires; the
        difference is only that no reward screen is outstanding any more.
        """
        current = self.fleet(fleet_id)
        if current.phase is not FleetPhase.BLOCKED:
            raise SchedulerInvariantError("only a blocked fleet can be recovered")
        if not isinstance(evidence, str) or not evidence.strip():
            raise SchedulerInvariantError("recovery requires named evidence")
        return self._set(replace(
            current, phase=FleetPhase.RESUPPLY_REQUIRED, mission=None,
            home_port_verified=False, blocked_reason=None,
        ))

    def _set(self, state: FleetRuntime) -> FleetRuntime:
        if self._state_store is not None:
            self._state_store.save(state)
        self._fleets[state.fleet_id] = state
        return state


# ``UiOperationArbiter`` is re-exported from the adapters layer (import above).
