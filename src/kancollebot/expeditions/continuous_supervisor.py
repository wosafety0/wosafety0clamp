
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class SupervisorTickResult:
    monitor_result: Any
    dispatches: tuple[Any, ...]
    paused_reason: Optional[str] = None
    settlements: tuple[Any, ...] = ()
    resupplies: tuple[Any, ...] = ()


class ContinuousExpeditionSupervisor:
    """Coordinate fleet monitoring and sequential expedition dispatch."""

    SETTLEMENT_PHASES = {
        "RETURNED_PENDING_UI",
        "RETURN_RESULT",
        "BONUS_ITEMS",
    }

    RESUPPLY_PHASES = {
        "RESUPPLY_REQUIRED",
    }

    ACTIVE_PHASES = {
        "EXPEDITION_ACTIVE",
        "ACTIVE",
    }

    def __init__(
        self,
        monitor: Any,
        dispatch_controller: Any,
        settlement_controller: Any = None,
        resupply_controller: Any = None,
    ) -> None:
        self.monitor = monitor
        self.dispatch_controller = dispatch_controller
        self.settlement_controller = settlement_controller
        self.resupply_controller = resupply_controller

        monitor_scheduler = getattr(monitor, "scheduler", None)
        controller_scheduler = getattr(
            dispatch_controller, "scheduler", None
        )
        if monitor_scheduler is not controller_scheduler:
            raise ValueError(
                "monitor and dispatch controller must share one scheduler"
            )

        monitor_reader = getattr(monitor, "state_reader", None)
        controller_reader = getattr(
            dispatch_controller, "state_reader", None
        )
        if monitor_reader is not controller_reader:
            raise ValueError(
                "monitor and dispatch controller must share one Poi state reader"
            )

        monitor_event_sink = getattr(monitor, "event_sink", None)
        controller_event_sink = getattr(
            dispatch_controller, "event_sink", None
        )
        if monitor_event_sink is not controller_event_sink:
            raise ValueError(
                "monitor and dispatch controller must share one event sink"
            )

        monitor_requirements = getattr(
            monitor, "mission_requirements", None
        )
        controller_requirements = getattr(
            dispatch_controller, "mission_requirements", None
        )

        if (monitor_requirements is None) != (
            controller_requirements is None
        ):
            raise ValueError(
                "monitor and dispatch controller must both define mission requirements"
            )

        if (
            monitor_requirements is not None
            and dict(monitor_requirements)
            != dict(controller_requirements)
        ):
            raise ValueError(
                "monitor and dispatch controller must use identical mission requirements"
            )

        if settlement_controller is not None:
            settlement_scheduler = getattr(
                settlement_controller, "scheduler", None
            )
            if settlement_scheduler is not monitor_scheduler:
                raise ValueError(
                    "settlement controller must share one scheduler"
                )
            if (
                getattr(settlement_controller, "state_reader", None)
                is not monitor_reader
            ):
                raise ValueError(
                    "settlement controller must share one Poi state reader"
                )
            if (
                getattr(settlement_controller, "event_sink", None)
                is not monitor_event_sink
            ):
                raise ValueError(
                    "settlement controller must share one event sink"
                )

        if resupply_controller is not None:
            resupply_scheduler = getattr(
                resupply_controller, "scheduler", None
            )
            if resupply_scheduler is not monitor_scheduler:
                raise ValueError(
                    "resupply controller must share one scheduler"
                )
            # The resupply controller reads supply data from an injected
            # fleet_data_provider rather than the shared PoiFleetStateReader,
            # so it intentionally exposes no state_reader to compare against.
            if (
                getattr(resupply_controller, "event_sink", None)
                is not monitor_event_sink
            ):
                raise ValueError(
                    "resupply controller must share one event sink"
                )

    @staticmethod
    def _phase_name(state: Any) -> str:
        phase = getattr(state, "phase", None)
        if phase is None:
            return ""

        value = getattr(phase, "value", phase)
        return str(value).upper()

    @staticmethod
    def _fleet_id(state: Any, fallback: Any = None) -> Any:
        return getattr(state, "fleet_id", fallback)

    def _fleet_states(self) -> list[tuple[Any, Any]]:
        scheduler = self.monitor.scheduler
        states = getattr(scheduler, "states", None)

        if isinstance(states, dict):
            return list(states.items())

        fleet_ids = getattr(scheduler, "dispatch_fleet_ids", ())
        result = []

        for fleet_id in fleet_ids:
            try:
                result.append((fleet_id, scheduler.fleet(fleet_id)))
            except (KeyError, LookupError):
                continue

        return result

    def _pause_reason(self) -> Optional[str]:
        for fleet_id, state in self._fleet_states():
            phase = self._phase_name(state)

            if phase == "BLOCKED":
                return f"fleet_{fleet_id}_blocked"

            if phase == "DISPATCHING":
                return (
                    f"fleet_{fleet_id}_dispatch_reconciliation_required"
                )

            if phase in self.SETTLEMENT_PHASES:
                return f"fleet_{fleet_id}_settlement_pending"

            if phase in self.RESUPPLY_PHASES:
                return f"fleet_{fleet_id}_resupply_pending"

            if phase in self.ACTIVE_PHASES:
                if not bool(
                    getattr(state, "home_port_verified", False)
                ):
                    return (
                        f"fleet_{fleet_id}_home_port_verification_required"
                    )

        return None

    @staticmethod
    def _settlement_fleet_id(pause_reason: str) -> Optional[int]:
        """Parse ``fleet_<id>_settlement_pending`` into the fleet id, else None."""
        prefix = "fleet_"
        suffix = "_settlement_pending"
        if not pause_reason.startswith(prefix) or not pause_reason.endswith(suffix):
            return None
        middle = pause_reason[len(prefix): -len(suffix)]
        try:
            return int(middle)
        except ValueError:
            return None

    @staticmethod
    def _resupply_fleet_id(pause_reason: str) -> Optional[int]:
        """Parse ``fleet_<id>_resupply_pending`` into the fleet id, else None."""
        prefix = "fleet_"
        suffix = "_resupply_pending"
        if not pause_reason.startswith(prefix) or not pause_reason.endswith(suffix):
            return None
        middle = pause_reason[len(prefix): -len(suffix)]
        try:
            return int(middle)
        except ValueError:
            return None

    @staticmethod
    def _paused(
        monitor_result: Any,
        pause_reason: str,
        settlements: tuple[Any, ...],
        resupplies: tuple[Any, ...],
        dispatches: tuple[Any, ...] = (),
    ) -> SupervisorTickResult:
        return SupervisorTickResult(
            monitor_result=monitor_result,
            dispatches=tuple(dispatches),
            paused_reason=pause_reason,
            settlements=settlements,
            resupplies=resupplies,
        )

    def tick(self) -> SupervisorTickResult:
        """Run one scheduling decision and record what it decided.

        The supervisor used to make the single most important decision of the
        loop - "may anything be dispatched this tick, and if not, why" - without
        emitting a single event. The decision is now written through the shared
        sink (the same object every component already had to share), so a run
        can be reconstructed after the fact. A monitor double that exposes no
        callable sink is left alone rather than crashed on.
        """
        result = self._tick_once()
        self._record_decision(result)
        return result

    def _tick_once(self) -> SupervisorTickResult:
        monitor_result = self.monitor.tick()

        settlements: tuple[Any, ...] = ()
        resupplies: tuple[Any, ...] = ()
        pause_reason = self._pause_reason()

        if pause_reason is not None:
            settlement_fleet_id = (
                self._settlement_fleet_id(pause_reason)
                if self.settlement_controller is not None
                else None
            )
            if settlement_fleet_id is not None:
                if not bool(
                    getattr(
                        self.settlement_controller,
                        "allow_ui_actions",
                        False,
                    )
                ):
                    # Settlement is required but not available: fail closed
                    # rather than dispatching around an unresolved fleet.
                    return self._paused(
                        monitor_result, pause_reason, settlements, resupplies
                    )
                settlement = self.settlement_controller.settle_one_returned(
                    settlement_fleet_id
                )
                settlements = (settlement,)
                # A fleet that just finished claiming its reward becomes
                # RESUPPLY_REQUIRED; advance the pause reason so the resupply
                # branch below can close the loop in the same tick.
                pause_reason = self._pause_reason()

            resupply_fleet_id = (
                self._resupply_fleet_id(pause_reason)
                if self.resupply_controller is not None
                else None
            )
            if resupply_fleet_id is not None:
                if not bool(
                    getattr(
                        self.resupply_controller,
                        "allow_ui_actions",
                        False,
                    )
                ):
                    # Resupply is required but not available: fail closed.
                    # A fleet stuck in RESUPPLY_REQUIRED can never reach READY,
                    # so the loop cannot re-dispatch it; pausing is the safe
                    # stance rather than dispatching the other fleets and
                    # leaving this one stranded.
                    return self._paused(
                        monitor_result, pause_reason, settlements, resupplies
                    )
                resupply = self.resupply_controller.resupply_one_needed(
                    resupply_fleet_id
                )
                resupplies = (resupply,)
                pause_reason = self._pause_reason()

        # Still paused after the one settlement + following resupply we allow
        # per tick (e.g. a second fleet also needs an action): report and stop.
        if pause_reason is not None:
            return self._paused(
                monitor_result, pause_reason, settlements, resupplies
            )

        if not bool(
            getattr(
                self.dispatch_controller,
                "allow_ui_actions",
                False,
            )
        ):
            return self._paused(
                monitor_result, "ui_actions_disabled", settlements, resupplies
            )

        scheduler = self.monitor.scheduler
        dispatch_order = tuple(
            getattr(scheduler, "dispatch_fleet_ids", ())
        )

        dispatches = []

        for fleet_id in dispatch_order:
            try:
                state = scheduler.fleet(fleet_id)
            except (KeyError, LookupError):
                continue

            if self._phase_name(state) != "READY":
                continue

            result = self.dispatch_controller.dispatch_one_ready(
                fleet_id
            )
            dispatches.append(result)

            updated_state = scheduler.fleet(fleet_id)

            if self._phase_name(updated_state) not in self.ACTIVE_PHASES:
                return self._paused(
                    monitor_result,
                    f"fleet_{fleet_id}_post_dispatch_not_active",
                    settlements,
                    resupplies,
                    tuple(dispatches),
                )

            if not bool(
                getattr(updated_state, "home_port_verified", False)
            ):
                return self._paused(
                    monitor_result,
                    f"fleet_{fleet_id}_post_dispatch_home_port_not_verified",
                    settlements,
                    resupplies,
                    tuple(dispatches),
                )

        return SupervisorTickResult(
            monitor_result=monitor_result,
            dispatches=tuple(dispatches),
            paused_reason=None,
            settlements=settlements,
            resupplies=resupplies,
        )

    # -- run-log integration ------------------------------------------------

    def _record_decision(self, result: SupervisorTickResult) -> None:
        """Emit one row describing this tick's scheduling decision.

        ``supervisor_paused`` is a safety-critical event: a deliberate stop is
        the one thing a recovery reader must be able to find. Emitting it before
        the caller acts on the result is what keeps the log honest when the
        process dies right afterwards.
        """
        sink = getattr(self.monitor, "event_sink", None)
        if not callable(sink):
            # Offline/test monitors may expose the attribute without a callable
            # (the supervisor contract only requires the *identity* to match).
            return
        fields = {
            "paused_reason": result.paused_reason,
            "dispatch_fleet_ids": [
                getattr(item, "fleet_id", None) for item in result.dispatches
            ],
            "dispatch_count": len(result.dispatches),
            "settlement_fleet_ids": [
                getattr(item, "fleet_id", None) for item in result.settlements
            ],
            "settlement_count": len(result.settlements),
            "resupply_fleet_ids": [
                getattr(item, "fleet_id", None) for item in result.resupplies
            ],
            "resupply_count": len(result.resupplies),
            "fleet_phases": self._fleet_phases(),
        }
        event = "supervisor_paused" if result.paused_reason else "supervisor_tick"
        sink(event, fields)

    def _fleet_phases(self) -> dict[str, str]:
        phases: dict[str, str] = {}
        for fleet_id, state in self._fleet_states():
            phases[str(fleet_id)] = self._phase_name(state)
        return phases
