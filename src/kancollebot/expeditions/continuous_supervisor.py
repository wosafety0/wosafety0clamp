
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class SupervisorTickResult:
    monitor_result: Any
    dispatches: tuple[Any, ...]
    paused_reason: Optional[str] = None


class ContinuousExpeditionSupervisor:
    """Coordinate fleet monitoring and sequential expedition dispatch."""

    SETTLEMENT_PHASES = {
        "RETURNED_PENDING_UI",
        "RETURN_RESULT",
        "BONUS_ITEMS",
    }

    ACTIVE_PHASES = {
        "EXPEDITION_ACTIVE",
        "ACTIVE",
    }

    def __init__(
        self,
        monitor: Any,
        dispatch_controller: Any,
    ) -> None:
        self.monitor = monitor
        self.dispatch_controller = dispatch_controller

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

            if phase in self.ACTIVE_PHASES:
                if not bool(
                    getattr(state, "home_port_verified", False)
                ):
                    return (
                        f"fleet_{fleet_id}_home_port_verification_required"
                    )

        return None

    def tick(self) -> SupervisorTickResult:
        monitor_result = self.monitor.tick()

        pause_reason = self._pause_reason()
        if pause_reason is not None:
            return SupervisorTickResult(
                monitor_result=monitor_result,
                dispatches=(),
                paused_reason=pause_reason,
            )

        if not bool(
            getattr(
                self.dispatch_controller,
                "allow_ui_actions",
                False,
            )
        ):
            return SupervisorTickResult(
                monitor_result=monitor_result,
                dispatches=(),
                paused_reason="ui_actions_disabled",
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
                return SupervisorTickResult(
                    monitor_result=monitor_result,
                    dispatches=tuple(dispatches),
                    paused_reason=(
                        f"fleet_{fleet_id}_post_dispatch_not_active"
                    ),
                )

            if not bool(
                getattr(updated_state, "home_port_verified", False)
            ):
                return SupervisorTickResult(
                    monitor_result=monitor_result,
                    dispatches=tuple(dispatches),
                    paused_reason=(
                        f"fleet_{fleet_id}_post_dispatch_home_port_not_verified"
                    ),
                )

        return SupervisorTickResult(
            monitor_result=monitor_result,
            dispatches=tuple(dispatches),
            paused_reason=None,
        )
