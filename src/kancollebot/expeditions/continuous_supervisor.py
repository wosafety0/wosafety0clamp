
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from kancollebot.expeditions.settlement_drain import (
    DrainResult,
    SettlementDrainRequest,
)
from kancollebot.observability.event_envelope import new_dispatch_id
from kancollebot.observability.sink import correlation_scope

#: Everything one fleet can still need from the loop, in the order that decides
#: which single reason label describes the pause. The order is a *reporting*
#: convention only: which fleet gets acted on is now decided by the full list
#: (``_pending_actions``), never by whichever entry came first.
PENDING_KINDS: tuple[str, ...] = (
    "blocked",
    "dispatch_reconciliation",
    "settlement",
    "resupply",
    "home_port_verification",
)

_PENDING_REASONS: dict[str, str] = {
    "blocked": "fleet_{fleet_id}_blocked",
    "dispatch_reconciliation": "fleet_{fleet_id}_dispatch_reconciliation_required",
    "settlement": "fleet_{fleet_id}_settlement_pending",
    "resupply": "fleet_{fleet_id}_resupply_pending",
    "home_port_verification": "fleet_{fleet_id}_home_port_verification_required",
}


@dataclass(frozen=True)
class FleetPendingAction:
    """One fleet's outstanding need, collected from the shared scheduler.

    Exists so the tick can act on **every** fleet that needs something. The old
    shape parsed one pause reason back into one fleet id, which is `FirstOrDefault`
    by another name: with two returned fleets the second was invisible, and a
    fleet that could not be settled stranded every other fleet behind it.
    """

    fleet_id: Any
    phase: str
    kind: str

    def pause_reason(self) -> str:
        """The legacy pause label for this need; reporting only."""
        return _PENDING_REASONS[self.kind].format(fleet_id=self.fleet_id)


@dataclass(frozen=True)
class SupervisorTickResult:
    monitor_result: Any
    dispatches: tuple[Any, ...]
    paused_reason: Optional[str] = None
    settlements: tuple[Any, ...] = ()
    resupplies: tuple[Any, ...] = ()
    #: The one global drain that ran this tick, if any. ``None`` means the tick
    #: used the per-fleet settlement path (no drain session wired) or had
    #: nothing to drain.
    drain: Optional[DrainResult] = None


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
        *,
        drain_session: Any = None,
        drain_request: SettlementDrainRequest | None = None,
    ) -> None:
        self.monitor = monitor
        self.dispatch_controller = dispatch_controller
        self.settlement_controller = settlement_controller
        self.resupply_controller = resupply_controller

        # A drain session is the only thing that can clear reward pages for more
        # than one fleet at a time, because the pages name the expedition and
        # not the fleet. It is optional: without it the loop keeps the per-fleet
        # barrier, which is safer but cannot resolve two simultaneous returns.
        if drain_session is not None:
            if not callable(getattr(drain_session, "drain", None)):
                raise ValueError("drain_session must expose a callable drain(request)")
            if not isinstance(drain_request, SettlementDrainRequest):
                # Every limit is explicit. A default here would be a silent
                # click budget, which is exactly what this project refuses.
                raise ValueError(
                    "drain_request must be a SettlementDrainRequest whenever a "
                    "drain_session is given"
                )
        elif drain_request is not None:
            raise ValueError("drain_request requires a drain_session")
        self.drain_session = drain_session
        self.drain_request = drain_request

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

    def _pending_kind(self, phase: str, state: Any) -> Optional[str]:
        """Classify one fleet's phase into one outstanding need, if any."""
        if phase == "BLOCKED":
            return "blocked"

        if phase == "DISPATCHING":
            return "dispatch_reconciliation"

        if phase in self.SETTLEMENT_PHASES:
            return "settlement"

        if phase in self.RESUPPLY_PHASES:
            return "resupply"

        if phase in self.ACTIVE_PHASES:
            if not bool(getattr(state, "home_port_verified", False)):
                return "home_port_verification"

        return None

    def _pending_actions(self) -> tuple[FleetPendingAction, ...]:
        """Every fleet that still needs something, in scheduler order.

        This is the single place that interprets fleet phases. Everything else
        (the pause label, which fleets to settle, which to resupply) reads this
        list, so there is exactly one answer to "what is outstanding" instead of
        one per call site.
        """
        actions: list[FleetPendingAction] = []
        for fleet_id, state in self._fleet_states():
            phase = self._phase_name(state)
            kind = self._pending_kind(phase, state)
            if kind is None:
                continue
            actions.append(
                FleetPendingAction(
                    fleet_id=self._fleet_id(state, fleet_id),
                    phase=phase,
                    kind=kind,
                )
            )
        return tuple(actions)

    def pending_settlement_fleets(self) -> tuple[Any, ...]:
        """Every fleet waiting on a result, not just the first one found."""
        return tuple(
            action.fleet_id
            for action in self._pending_actions()
            if action.kind == "settlement"
        )

    def pending_resupply_fleets(self) -> tuple[Any, ...]:
        return tuple(
            action.fleet_id
            for action in self._pending_actions()
            if action.kind == "resupply"
        )

    def _pause_reason(self) -> Optional[str]:
        """The one label that summarises why the loop cannot dispatch.

        Derived from ``_pending_actions`` so the label can never disagree with
        the work the tick actually does, and kept in scheduler order so the
        label stays byte-identical to the pre-P0.2 behaviour. It is a
        *description*, not a selection: the tick reads the whole list.
        """
        actions = self._pending_actions()
        if not actions:
            return None
        return actions[0].pause_reason()

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
        *,
        drain: Any = None,
    ) -> SupervisorTickResult:
        return SupervisorTickResult(
            monitor_result=monitor_result,
            dispatches=tuple(dispatches),
            paused_reason=pause_reason,
            settlements=settlements,
            resupplies=resupplies,
            drain=drain,
        )

    def tick(self) -> SupervisorTickResult:
        """Run one scheduling decision and record what it decided.

        The supervisor used to make the single most important decision of the
        loop - "may anything be dispatched this tick, and if not, why" - without
        emitting a single event. The decision is now written through the shared
        sink (the same object every component already had to share), so a run
        can be reconstructed after the fact. A monitor double that exposes no
        callable sink is left alone rather than crashed on.

        The row is written on the **failure** path too. A fresh-baseline
        refusal is the most common production outcome, and it leaves no
        ``supervisor_tick`` behind; without this, the log ends at
        ``run_started`` exactly when the operator most needs to know why the
        loop stopped. A partially completed tick (first fleet dispatched,
        second refused) therefore also keeps its summary row.
        """
        try:
            result = self._tick_once()
        except Exception as exc:
            self._record_failure(exc)
            raise
        self._record_decision(result)
        return result

    def _tick_once(self) -> SupervisorTickResult:
        monitor_result = self.monitor.tick()

        # Two ways to resolve a returned fleet, chosen by whether the loop owns
        # a global drain session:
        #
        # * with one, every pending fleet is settled after a single page-driven
        #   drain, because the drain does not care which fleet owns a page;
        # * without one, the legacy path runs unchanged, one fleet per tick, and
        #   keeps the ``other_returns`` barrier (the reward screens cannot be
        #   attributed to one fleet, so the second return must wait).
        if self.drain_session is not None and self.pending_settlement_fleets():
            return self._tick_with_global_drain(monitor_result)
        return self._tick_per_fleet(monitor_result)

    def _tick_per_fleet(self, monitor_result: Any) -> SupervisorTickResult:
        """Legacy path: one settlement + one resupply per tick, then dispatch.

        Unchanged on purpose. It is safe but cannot resolve two simultaneous
        returns; the drain path exists for that, and this stays the default so
        an unwired loop behaves exactly as it did before P0.2.
        """
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

        return self._dispatch_ready_fleets(monitor_result, settlements, resupplies)

    def _tick_with_global_drain(self, monitor_result: Any) -> SupervisorTickResult:
        """Settle every returned fleet behind one drain, then serve the rest.

        The order matters and is the point of this path:

        1. drain the visible settlement pages **once** - no fleet id is passed,
           because the pages belong to the account and not to a fleet;
        2. only if a stable Home Port was proven, close every pending fleet's
           settlement (each one attributed through the dispatch ledger, never by
           arrival order);
        3. resupply every fleet that needs it, then dispatch every READY fleet.

        A drain that stops early ends the tick with zero settlement clicks and
        zero dispatches: the pages are in an unknown state, so nothing else may
        be touched until a human looks.
        """
        settlements: tuple[Any, ...] = ()
        resupplies: tuple[Any, ...] = ()

        pending = self.pending_settlement_fleets()
        if self.settlement_controller is None or not bool(
            getattr(self.settlement_controller, "allow_ui_actions", False)
        ):
            return self._paused(
                monitor_result,
                FleetPendingAction(pending[0], "settlement", "settlement").pause_reason(),
                settlements,
                resupplies,
            )

        drain = self.drain_session.drain(self.drain_request)
        self._emit("supervisor_settlement_drain", {
            "pending_fleets": list(pending),
            "reached_home": drain.reached_home,
            "completed_pages": drain.completed_pages,
            "actions_performed": drain.actions_performed,
            "final_page": drain.final_page.value,
            "error": (
                drain.error_encountered.value
                if drain.error_encountered is not None
                else None
            ),
            "requires_manual_intervention": drain.requires_manual_intervention,
        })
        if not drain.reached_home:
            error = (
                drain.error_encountered.value
                if drain.error_encountered is not None
                else "unknown"
            )
            return SupervisorTickResult(
                monitor_result=monitor_result,
                dispatches=(),
                paused_reason=f"settlement_drain_stopped:{error}",
                settlements=(),
                resupplies=(),
                drain=drain,
            )

        results = []
        for fleet_id in self.pending_settlement_fleets():
            # ``pages_drained_globally`` replaces the fleet-level "only one
            # return may be pending" barrier with ledger attribution: the pages
            # are gone, and the ledger says which instance this fleet's return
            # closes. A non-unique answer still refuses with zero clicks.
            results.append(
                self.settlement_controller.settle_one_returned(
                    fleet_id, pages_drained_globally=True
                )
            )
        settlements = tuple(results)

        resupply_fleet_ids = self.pending_resupply_fleets()
        if resupply_fleet_ids:
            if self.resupply_controller is None or not bool(
                getattr(self.resupply_controller, "allow_ui_actions", False)
            ):
                return self._paused(
                    monitor_result,
                    FleetPendingAction(
                        resupply_fleet_ids[0], "RESUPPLY_REQUIRED", "resupply"
                    ).pause_reason(),
                    settlements,
                    resupplies,
                    drain=drain,
                )
            resupplies = tuple(
                self.resupply_controller.resupply_one_needed(fleet_id)
                for fleet_id in resupply_fleet_ids
            )

        result = self._dispatch_ready_fleets(monitor_result, settlements, resupplies)
        return SupervisorTickResult(
            monitor_result=result.monitor_result,
            dispatches=result.dispatches,
            paused_reason=result.paused_reason,
            settlements=result.settlements,
            resupplies=result.resupplies,
            drain=drain,
        )

    def _dispatch_ready_fleets(
        self,
        monitor_result: Any,
        settlements: tuple[Any, ...],
        resupplies: tuple[Any, ...],
    ) -> SupervisorTickResult:
        """Dispatch every READY fleet in scheduler order, stopping on surprise."""
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

            result = self._dispatch_with_identity(fleet_id)
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

    def _sink(self) -> Any:
        """The one sink every component of this process shares, or ``None``."""
        sink = getattr(self.monitor, "event_sink", None)
        return sink if callable(sink) else None

    def _emit(self, event: str, fields: dict[str, Any]) -> None:
        """Log one supervisor-level fact; a monitor double may have no sink."""
        sink = self._sink()
        if sink is None:
            return
        sink(event, dict(fields))

    def _dispatch_with_identity(self, fleet_id: int) -> Any:
        """Run one dispatch attempt under one freshly minted dispatch identity.

        The **attempt** is the unit of identity, not its outcome: the id is
        minted before the controller reads a baseline, so a fleet refused by the
        R3-I requirement gate still yields an identifiable attempt whose
        ``dispatch_requirement_evaluation`` / ``dispatch_blocked_*`` rows carry
        the same ``dispatch_id`` the click rows would have carried.

        A reader must therefore not treat "``dispatch_id`` is present" as
        "a dispatch happened". Only ``dispatch_intent_persisted`` followed by
        ``dispatch_confirmed_by_poi`` says that.

        Binding happens on the shared sink the constructor already forces every
        component to use, so the UI driver's ``continuous_ui_click_*`` rows (same
        sink, emitted from inside the controller call) inherit the identity too.
        A sink that cannot express correlation leaves ``dispatch_id`` null
        rather than failing the dispatch: offline doubles keep working, and the
        gap stays visible in the log instead of being papered over.

        The same id is handed to the controller, which writes it into the
        durable dispatch ledger before the irreversible click. The scope and
        the ledger therefore name one attempt, and the id is minted once rather
        than at two call sites.
        """
        dispatch_id = new_dispatch_id()
        with correlation_scope(
            self._sink(), dispatch_id=dispatch_id, fleet_id=fleet_id
        ):
            return self.dispatch_controller.dispatch_one_ready(
                fleet_id, dispatch_id=dispatch_id
            )

    def _record_decision(self, result: SupervisorTickResult) -> None:
        """Emit one row describing this tick's scheduling decision.

        ``supervisor_paused`` is a safety-critical event: a deliberate stop is
        the one thing a recovery reader must be able to find. Emitting it before
        the caller acts on the result is what keeps the log honest when the
        process dies right afterwards.
        """
        sink = self._sink()
        if sink is None:
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

    def _record_failure(self, error: Exception) -> None:
        """Emit the summary row for a tick that raised before deciding.

        The exception a monitor or controller raises is never converted into a
        result - it still propagates - but a run that stops here must not leave
        only ``run_started`` on disk. No reason is invented: the error type and
        the fleet phases are the facts.
        """
        sink = self._sink()
        if sink is None:
            return
        try:
            sink("supervisor_tick_failed", {
                "error_type": type(error).__name__,
                "paused_reason": None,
                "fleet_phases": self._fleet_phases(),
                "dispatch_count": 0,
            })
        except Exception:
            # The original failure is what the caller must see.
            pass

    def _fleet_phases(self) -> dict[str, str]:
        phases: dict[str, str] = {}
        for fleet_id, state in self._fleet_states():
            phases[str(fleet_id)] = self._phase_name(state)
        return phases
