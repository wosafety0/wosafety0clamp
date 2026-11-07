
from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic as _monotonic
from typing import Any, Optional

from kancollebot.expeditions.page_intent import (
    ACTION_DISPATCH,
    ACTION_RESUPPLY,
    ACTION_SETTLEMENT,
    PageEvidence,
    check_page_evidence,
)
from kancollebot.expeditions.page_navigation import (
    PageFallback,
    plan_page_fallback,
)
from kancollebot.expeditions.settlement_drain import (
    DrainErrorType,
    DrainResult,
    SettlementDrainRequest,
)
from kancollebot.observability.event_envelope import new_dispatch_id
from kancollebot.observability.sink import (
    correlation_capability,
    correlation_scope,
)

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


@dataclass
class _TickProgress:
    """What this tick had already finished when it stopped.

    The tick used to record its decision only after ``_tick_once()`` returned,
    so any raise - and the most common one in production is a fresh-baseline
    refusal from the monitor - left no row at all, and a tick that dispatched
    the first fleet before failing on the second lost the first fleet's result.

    This carrier is written *as the tick advances*, so the failure row can say
    "fleet 2 was dispatched, fleet 3 failed" instead of "something failed".
    It holds no identity of its own: no ids are minted here.
    """

    phase: str = "monitor"
    fleet_id: Any = None
    settlements: list[Any] = field(default_factory=list)
    resupplies: list[Any] = field(default_factory=list)
    dispatches: list[Any] = field(default_factory=list)

    def note(self, phase: str, fleet_id: Any = None) -> None:
        self.phase = phase
        self.fleet_id = fleet_id

    @property
    def completed_anything(self) -> bool:
        return bool(self.settlements or self.resupplies or self.dispatches)


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
    #: The phase-4 route for the page that withheld this tick, if any. ``None``
    #: for every tick that was not paused by page evidence. The supervisor does
    #: not execute the route - it cannot even click - so this field is a
    #: decision the loop acts on, never an action already taken.
    page_fallback: Optional[PageFallback] = None


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

    def pending_actions(self) -> tuple[FleetPendingAction, ...]:
        """Public view of outstanding work, used by the timer sleep gate.

        A fleet with a pending settlement, resupply, blocked state or missing
        Home Port verification has executable work *now*, so the loop must not
        sleep past its timer.
        """
        return self._pending_actions()

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
        page_fallback: Optional[PageFallback] = None,
    ) -> SupervisorTickResult:
        return SupervisorTickResult(
            monitor_result=monitor_result,
            dispatches=tuple(dispatches),
            paused_reason=pause_reason,
            settlements=settlements,
            resupplies=resupplies,
            drain=drain,
            page_fallback=page_fallback,
        )

    def tick(self, *, page_evidence: PageEvidence | None = None) -> SupervisorTickResult:
        """Run one scheduling decision and record what it decided.

        The supervisor used to make the single most important decision of the
        loop - "may anything be dispatched this tick, and if not, why" - without
        emitting a single event. The decision is now written through the shared
        sink (the same object every component already had to share), so a run
        can be reconstructed after the fact. A monitor double that exposes no
        callable sink is left alone rather than crashed on.

        The row is written on the **failure** path too, as ``supervisor_paused``
        with a ``*_failed:<ErrorType>`` reason: a fresh-baseline refusal is the
        most common production outcome and it must be as findable as a deliberate
        stop. The partial progress of the tick travels with it, so "fleet 2 was
        dispatched, fleet 3 failed" is one readable row instead of a guess.
        """
        progress = _TickProgress()
        try:
            result = self._tick_once(progress, page_evidence)
        except Exception as exc:
            self._record_failure(exc, progress)
            raise
        self._record_decision(result)
        return result

    def _page_intent(self) -> Optional[str]:
        """The action this tick intends, derived from the existing phases only.

        This is a projection of ``_pending_actions`` (the one interpreter of
        FleetPhase) plus the same READY scan the dispatch step uses. It never
        invents work: a page class cannot reach this method, and an empty
        scheduler returns ``None`` rather than a synthetic intent.
        """
        kinds = {action.kind for action in self._pending_actions()}
        if "settlement" in kinds:
            return ACTION_SETTLEMENT
        if "resupply" in kinds:
            return ACTION_RESUPPLY
        if kinds:
            # Blocked / dispatch_reconciliation / home_port_verification keep
            # their existing handling; page evidence does not veto them.
            return None
        if self._ready_fleet_ids():
            return ACTION_DISPATCH
        return None

    def _tick_once(
        self, progress: _TickProgress, page_evidence: PageEvidence | None = None
    ) -> SupervisorTickResult:
        progress.note("monitor")
        monitor_result = self.monitor.tick()

        # Page evidence is veto-only (see ``page_intent``): the phases above
        # decide what must happen, and a fresh, accepted, incompatible page
        # class can only stop it from happening this tick. UNKNOWN is not
        # evidence and is never read as Home Port.
        intent = self._page_intent()
        if page_evidence is not None:
            check = check_page_evidence(intent, page_evidence)
            self._emit("supervisor_page_evidence", {
                "intent": check.intent,
                "page": check.page,
                "detail_state": page_evidence.detail_state,
                "allowed": check.allowed,
                "reason": check.reason,
                "evidence_age_seconds": round(
                    max(0.0, _monotonic() - page_evidence.observed_at), 3
                ),
            })
            if not check.allowed:
                reason = f"page_mismatch:{check.intent}:{check.page}"
                # Phase 4: name the smallest route back, but do not take it.
                # The supervisor still sends nothing; the loop owns the game
                # session, so it executes the route and this tick stops with
                # the same ``page_mismatch`` label it always had.
                fallback = plan_page_fallback(check.page, check.intent)
                self._emit("supervisor_page_mismatch", {
                    "intent": check.intent,
                    "page": check.page,
                    "detail_state": page_evidence.detail_state,
                    "paused_reason": reason,
                    "clicks_sent_this_tick": 0,
                    "next_step": "hold_for_next_observation",
                })
                self._emit(
                    "supervisor_page_fallback",
                    {"paused_reason": reason, **fallback.as_log_fields()},
                )
                return self._paused(
                    monitor_result, reason, (), (), page_fallback=fallback
                )

        # Two ways to resolve a returned fleet, chosen by whether the loop owns
        # a global drain session:
        #
        # * with one, every pending fleet is settled after a single page-driven
        #   drain, because the drain does not care which fleet owns a page;
        # * without one, the legacy path runs unchanged, one fleet per tick, and
        #   keeps the ``other_returns`` barrier (the reward screens cannot be
        #   attributed to one fleet, so the second return must wait).
        if self.drain_session is not None and self.pending_settlement_fleets():
            return self._tick_with_global_drain(monitor_result, progress)
        return self._tick_per_fleet(monitor_result, progress)

    def _tick_per_fleet(
        self, monitor_result: Any, progress: _TickProgress
    ) -> SupervisorTickResult:
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
                progress.note("settlement", settlement_fleet_id)
                settlement = self.settlement_controller.settle_one_returned(
                    settlement_fleet_id
                )
                settlements = (settlement,)
                progress.settlements.append(settlement)
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
                progress.note("resupply", resupply_fleet_id)
                resupply = self.resupply_controller.resupply_one_needed(
                    resupply_fleet_id
                )
                resupplies = (resupply,)
                progress.resupplies.append(resupply)
                pause_reason = self._pause_reason()

        # Still paused after the one settlement + following resupply we allow
        # per tick (e.g. a second fleet also needs an action): report and stop.
        if pause_reason is not None:
            return self._paused(
                monitor_result, pause_reason, settlements, resupplies
            )

        return self._dispatch_ready_fleets(
            monitor_result, settlements, resupplies, progress
        )

    def _tick_with_global_drain(
        self, monitor_result: Any, progress: _TickProgress
    ) -> SupervisorTickResult:
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

        progress.note("drain")
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
            if drain.error_encountered in (
                DrainErrorType.SETTLED_UI_PENDING,
                DrainErrorType.OBSERVATION_BUDGET_EXHAUSTED,
            ):
                # UI-only catch-up: the server owes nothing more (or the client
                # is simply still animating). No settlement click may be sent
                # from here, and this is not a failure that needs a human - the
                # next tick re-reads the server state before anything else.
                self._emit("supervisor_settlement_ui_pending", {
                    "pending_fleets": list(pending),
                    "error": error,
                    "final_page": drain.final_page.value,
                    "actions_performed": drain.actions_performed,
                    "clicks_sent_this_tick": 0,
                    "next_step": "wait_for_ui_then_reread_server_state",
                })
                return SupervisorTickResult(
                    monitor_result=monitor_result,
                    dispatches=(),
                    paused_reason=None,
                    settlements=(),
                    resupplies=(),
                    drain=drain,
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
            progress.note("settlement", fleet_id)
            settlement = self.settlement_controller.settle_one_returned(
                fleet_id, pages_drained_globally=True
            )
            progress.settlements.append(settlement)
            results.append(settlement)
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
                self._resupply_one(fleet_id, progress)
                for fleet_id in resupply_fleet_ids
            )

        result = self._dispatch_ready_fleets(
            monitor_result, settlements, resupplies, progress
        )
        return SupervisorTickResult(
            monitor_result=result.monitor_result,
            dispatches=result.dispatches,
            paused_reason=result.paused_reason,
            settlements=result.settlements,
            resupplies=result.resupplies,
            drain=drain,
        )

    def _resupply_one(self, fleet_id: Any, progress: _TickProgress) -> Any:
        """Resupply one fleet, noting the phase so a failure is attributable."""
        progress.note("resupply", fleet_id)
        result = self.resupply_controller.resupply_one_needed(fleet_id)
        progress.resupplies.append(result)
        return result

    def _ready_fleet_ids(self) -> list[Any]:
        """READY fleets in configured dispatch order.

        The single READY scan: the dispatch step and the page-intent projection
        must agree on what "ready to sail" means, so they share this.
        """
        scheduler = self.monitor.scheduler
        ready: list[Any] = []
        for fleet_id in tuple(getattr(scheduler, "dispatch_fleet_ids", ())):
            try:
                state = scheduler.fleet(fleet_id)
            except (KeyError, LookupError):
                continue

            if self._phase_name(state) == "READY":
                ready.append(fleet_id)
        return ready

    def _dispatch_ready_fleets(
        self,
        monitor_result: Any,
        settlements: tuple[Any, ...],
        resupplies: tuple[Any, ...],
        progress: _TickProgress,
    ) -> SupervisorTickResult:
        """Dispatch every READY fleet in scheduler order, stopping on surprise."""
        scheduler = self.monitor.scheduler
        ready = self._ready_fleet_ids()

        if not bool(
            getattr(
                self.dispatch_controller,
                "allow_ui_actions",
                False,
            )
        ):
            if not ready:
                # Nothing is waiting to sail, so a controller that may not click
                # has not refused anything. Reporting the refusal here labelled a
                # settle-only pass over an empty dispatch queue as a blocked
                # click (2026-11-07: ``game_actions_sent=0`` next to
                # ``ui_actions_disabled``, which reads as a failure and is not
                # one). The gap between settlements and dispatch is simply empty.
                return SupervisorTickResult(
                    monitor_result=monitor_result,
                    dispatches=(),
                    paused_reason=None,
                    settlements=settlements,
                    resupplies=resupplies,
                )
            return self._paused(
                monitor_result, "ui_actions_disabled", settlements, resupplies
            )

        dispatches = []

        for fleet_id in ready:
            try:
                state = scheduler.fleet(fleet_id)
            except (KeyError, LookupError):
                continue

            if self._phase_name(state) != "READY":
                continue

            progress.note("dispatch", fleet_id)
            result = self._dispatch_with_identity(fleet_id)
            dispatches.append(result)
            progress.dispatches.append(result)

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

        **The id is minted only if the sink can actually carry it.** An id that
        no row can hold is an identity claimed nowhere: it would make the
        supervisor look like it correlated an attempt while the log says
        nothing, and a reader comparing the two would find no trace. So when the
        real capability (not the mere presence of a ``correlation`` attribute -
        see :func:`correlation_capability`) is missing, the attempt runs
        uncorrelated, no id is minted here, and
        ``dispatch_correlation_unavailable`` states the degradation before the
        attempt starts. The dispatch itself is never blocked by this: the
        durable ledger mints its own attempt id inside the controller, which is
        a separate claim about a separate artifact.

        The same id is handed to the controller, which writes it into the
        durable dispatch ledger before the irreversible click. The scope and
        the ledger therefore name one attempt, and the id is minted once rather
        than at two call sites.
        """
        sink = self._sink()
        capable = sink is not None and correlation_capability(sink).capable
        if not capable:
            if sink is not None:
                self._note_uncorrelated_dispatch(fleet_id, sink)
            return self.dispatch_controller.dispatch_one_ready(fleet_id)
        dispatch_id = new_dispatch_id()
        with correlation_scope(sink, dispatch_id=dispatch_id, fleet_id=fleet_id):
            return self.dispatch_controller.dispatch_one_ready(
                fleet_id, dispatch_id=dispatch_id
            )

    def _note_uncorrelated_dispatch(self, fleet_id: int, sink: Any) -> None:
        """Say, before the attempt, that its rows will carry no identity.

        A missing capability must be visible where the attempt starts, not
        inferred later from a column full of nulls: a null ``dispatch_id`` is
        also what an uncorrelated emitter and a dropped scope look like.
        """
        capability = correlation_capability(sink)
        self._emit(
            "dispatch_correlation_unavailable",
            {
                "fleet_id": fleet_id,
                "correlation_capable": capability.capable,
                "correlation_sources": list(capability.sources),
                "reason": capability.reason,
                "consequence": "no dispatch_id is minted for this attempt; its rows carry null",
                "dispatch_allowed": True,
            },
        )

    def _correlation_capable(self) -> bool | None:
        """Whether this process can correlate at all, or ``None`` without a sink."""
        sink = self._sink()
        if sink is None:
            return None
        return correlation_capability(sink).capable

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
            # ``fleets_2_not_ready`` is a verdict, not a cause. The monitor
            # already produced the cause (the planner's per-fleet reason), so
            # the decision row carries it rather than dropping it.
            "fleet_decisions": self._fleet_decisions(result.monitor_result),
            # Stated on every decision row, not only at startup: a null in any
            # identity column below is only interpretable next to the fact that
            # this process could (or could not) bind one at all.
            "correlation_capable": self._correlation_capable(),
        }
        event = "supervisor_paused" if result.paused_reason else "supervisor_tick"
        sink(event, fields)

    def _record_failure(self, error: Exception, progress: _TickProgress) -> None:
        """Record the tick that stopped, as the pause it really was.

        A raise from the monitor or from a controller ends the tick, so the row
        this writes **is** the decision row: ``supervisor_paused`` with a
        ``*_failed:<ErrorType>`` reason, the phase and fleet that failed, and
        the work that had already completed before it. The audit called
        ``supervisor_paused`` one of the 26 critical events and then pointed out
        that the most common production stop - a fresh-baseline refusal - never
        reached it; this closes that.

        Two rules, both about not lying in either direction:

        * the original failure is never converted into a result or a reason
          string. The reason names the error *type* and the phase; the message
          is carried verbatim in ``error``. Nothing is invented;
        * a failure to write this row is swallowed. The caller is about to
          receive the original exception, and replacing it with
          ``AuditLogUnavailable`` would hide the cause of the stop. Fail-closed
          is already satisfied here: the loop stopped. On the *success* path the
          same event is deliberately not swallowed (see ``_record_decision``),
          because there the write failure is the only thing that can stop a
          paused tick from proceeding silently.
        """
        sink = self._sink()
        if sink is None:
            return
        try:
            sink("supervisor_paused", {
                "paused_reason": self._failure_reason(error, progress),
                "error_type": type(error).__name__,
                "error": str(error)[:400],
                "failed_phase": progress.phase,
                "failed_fleet_id": progress.fleet_id,
                "dispatch_fleet_ids": [
                    getattr(item, "fleet_id", None) for item in progress.dispatches
                ],
                "dispatch_count": len(progress.dispatches),
                "settlement_fleet_ids": [
                    getattr(item, "fleet_id", None) for item in progress.settlements
                ],
                "settlement_count": len(progress.settlements),
                "resupply_fleet_ids": [
                    getattr(item, "fleet_id", None) for item in progress.resupplies
                ],
                "resupply_count": len(progress.resupplies),
                "fleet_phases": self._fleet_phases(),
                "partial_result": progress.completed_anything,
                "correlation_capable": self._correlation_capable(),
            })
        except Exception:
            # The original failure is what the caller must see.
            pass

    @staticmethod
    def _failure_reason(error: Exception, progress: _TickProgress) -> str:
        """A reason label a reader can filter on, derived from facts only."""
        error_type = type(error).__name__
        if progress.fleet_id is None:
            return f"{progress.phase}_failed:{error_type}"
        return f"fleet_{progress.fleet_id}_{progress.phase}_failed:{error_type}"

    def _fleet_phases(self) -> dict[str, str]:
        phases: dict[str, str] = {}
        for fleet_id, state in self._fleet_states():
            phases[str(fleet_id)] = self._phase_name(state)
        return phases

    @staticmethod
    def _fleet_decisions(monitor_result: Any) -> list[dict[str, Any]]:
        """Every fleet's monitor verdict with the reason that produced it.

        The pause label summarises the tick; it is not a cause. ``fleets_2_
        not_ready`` cannot distinguish "no supply" from "critically damaged"
        from "already on a mission", and those need different operator action.
        A monitor double without ``fleets`` yields ``[]`` instead of raising.
        """
        fleets = getattr(monitor_result, "fleets", None)
        if not isinstance(fleets, (list, tuple)):
            return []
        rows: list[dict[str, Any]] = []
        for item in fleets:
            previous = getattr(item, "previous_phase", None)
            current = getattr(item, "current_phase", None)
            rows.append(
                {
                    "fleet_id": getattr(item, "fleet_id", None),
                    "previous_phase": getattr(previous, "value", previous),
                    "current_phase": getattr(current, "value", current),
                    "action": getattr(item, "action", None),
                    "reason": getattr(item, "reason", None),
                }
            )
        return rows
