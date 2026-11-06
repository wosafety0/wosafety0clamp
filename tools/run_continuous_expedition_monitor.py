"""Run a read-only continuous monitor over the configured expedition fleets.

This runtime wires the passive Poi freshness provider into the durable fleet
state store and continuous monitor. By default it is read-only. An explicit
UI-refresh option may navigate only HOME_PORT -> SORTIE_MENU -> EXPEDITION_LIST
-> HOME_PORT to obtain a natural port witness; dispatch, reward collection, and
resupply remain disabled.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from kancollebot.adapters.poi.background_port_snapshot_provider import (  # noqa: E402
    BackgroundPortSnapshotProvider,
)
from kancollebot.adapters.poi.expedition_reader import PoiExpeditionReader  # noqa: E402
from kancollebot.adapters.poi.mcp_client import PoiMcpClient  # noqa: E402
from kancollebot.adapters.poi.port_response_observer import (  # noqa: E402
    PassivePortResponseObserver,
)
from kancollebot.expeditions.continuous_dispatch_controller import (  # noqa: E402
    SqliteDispatchEventLog,
    UiStateEvidence,
)
from kancollebot.expeditions.continuous_scheduler import (  # noqa: E402
    ContinuousExpeditionScheduler,
    UiOperationArbiter,
)
from kancollebot.adapters.poi.visible_ui_refresh import (  # noqa: E402
    FreshnessRefreshingStateReader,
    VisibleUiPortRefreshDriver,
)
from kancollebot.expeditions.continuous_monitor import (  # noqa: E402
    ContinuousExpeditionMonitor,
    ContinuousMonitorError,
)
from kancollebot.expeditions.dispatch_recovery import (  # noqa: E402
    DurableDispatchCoordinator,
    SqliteDispatchIntentStore,
)
from kancollebot.expeditions.fleet_state_store import (  # noqa: E402
    SqliteFleetStateStore,
)
from kancollebot.expeditions.mission_eligibility import (  # noqa: E402
    MissionCompositionRequirement,
)
from kancollebot.expeditions.expedition_rule_data import (  # noqa: E402
    DEFAULT_RULE_SET,
    requirements_for_assignments,
)
from kancollebot.expeditions.fleet_availability import (  # noqa: E402
    resolve_fleet_availability,
)
from kancollebot.expeditions.recovery_classification import (  # noqa: E402
    RecoveryPauseGate,
    RecoveryPausedError,
)
# P0 R4: the SAME task-neutral normalizer that dispatch-once probes with. The
# monitor cannot open a CDP game session in its default read-only mode, so the
# operator may supply the observed account fact explicitly; ambiguity fails
# closed instead of being interpreted as "all fleets exist".
from kancollebot.core.fleet_availability import (  # noqa: E402
    FleetAvailabilityError,
    parse_unlocked_fleet_ids,
)
from kancollebot.runtime_config import RUNTIME_CONFIG  # noqa: E402
from kancollebot.observability.run_log import RunLogPolicy  # noqa: E402
from kancollebot.observability.sink import (  # noqa: E402
    RunLogSink,
    TeeEventSink,
    build_run_log_sink,
    correlation_capability,
)
from kancollebot.task_scope import (  # noqa: E402
    TaskKind,
    require_task_enabled,
)


DEFAULT_OBSERVER_SCRIPT = RUNTIME_CONFIG.observer_script
DEFAULT_DATABASE = RUNTIME_CONFIG.monitor_database
DEFAULT_POI_ENDPOINT = RUNTIME_CONFIG.poi_endpoint


@dataclass
class MonitorRuntime:
    monitor: ContinuousExpeditionMonitor
    provider: BackgroundPortSnapshotProvider
    scheduler: ContinuousExpeditionScheduler
    unresolved_dispatch_fleets: tuple[int, ...]
    ui_refresh_enabled: bool = False
    ui_arbiter: UiOperationArbiter | None = None
    ui_refresh_driver: VisibleUiPortRefreshDriver | None = None
    # None means the caller did not supply an observed account fleet set. The
    # monitor's own per-tick observation check then remains the fail-closed
    # backstop; this flag must never be reported as "verified" in that case.
    unlocked_fleet_ids: tuple[int, ...] | None = None
    fleet_availability_verified: bool = False
    # Which automation task this runtime was composed for. The monitor and its
    # stores are expedition-specific today; recording the task makes mixed
    # event logs attributable and stops a future task from silently reusing it.
    task_kind: str = TaskKind.EXPEDITION.value
    # Identity of the unified run log for this process, when one is enabled.
    # Empty means "no run log": the SQLite ledger is still the durable record.
    run_id: str = ""
    run_log_sink: RunLogSink | None = None
    # The object every component was actually wired with. The run's first row
    # describes *its* correlation capability, not the run log writer's, because
    # that is the sink whose rows a reader will be looking at.
    event_sink: object | None = None
    recovery_gate: RecoveryPauseGate | None = None


def build_runtime(
    *,
    database_path: str | Path = DEFAULT_DATABASE,
    poi_endpoint: str = DEFAULT_POI_ENDPOINT,
    observer_script: str | Path = DEFAULT_OBSERVER_SCRIPT,
    observer_timeout_seconds: float = 130.0,
    max_state_age_seconds: float = 30.0,
    # How close a scheduled return has to be before a stale observation is
    # worth a navigation. This is what keeps an idle tick from turning into
    # game input every cooldown period.
    refresh_lead_seconds: float = 60.0,
    client: PoiMcpClient | None = None,
    observer: PassivePortResponseObserver | None = None,
    dispatch_fleet_ids: tuple[int, ...] = RUNTIME_CONFIG.monitor_candidate_fleet_ids,
    mission_requirements: Mapping[int, MissionCompositionRequirement] | None = None,
    # The observed set of fleets THIS account has unlocked. Fleet 4 (or 3) may
    # not exist. None means "not observed": the monitor's own per-tick check is
    # then the fail-closed backstop and the run is marked unverified.
    unlocked_fleet_ids: tuple[int, ...] | None = None,
    # True for an explicitly named --fleet-id: an un-unlocked target is a hard
    # error instead of being dropped from a candidate set.
    fleet_availability_strict: bool = False,
    enable_ui_refresh: bool = False,
    ui_input: Any | None = None,
    ui_state_verifier: Callable[[str], UiStateEvidence] | None = None,
    refresh_wait_seconds: float = RUNTIME_CONFIG.refresh_wait_seconds,
    refresh_poll_seconds: float = RUNTIME_CONFIG.refresh_poll_seconds,
    refresh_cooldown_seconds: float = RUNTIME_CONFIG.refresh_cooldown_seconds,
    # This composition root is expedition-specific (its stores, scheduler and
    # mission_requirements are). A declared-but-unimplemented task is rejected
    # here instead of silently inheriting expedition fleet policy.
    task_kind: TaskKind | str = TaskKind.EXPEDITION,
    # Opt-in recovery for a fleet that is already out (restart, or an expedition
    # started by hand). Off by default: BLOCKED is the conservative answer when
    # the durable state and the game disagree. Turning it on asserts that the
    # caller confirmed HOME_PORT in the same observation window, and the monitor
    # still requires the observed mission identity and composition to match.
    adopt_unplanned_missions: bool = False,
    # Opt-in unified run log. When a directory is given, every event that used to
    # land only in SQLite is ALSO written to the three-day rolling JSONL there,
    # through one tee object so the supervisor's "shared sink" identity check
    # still holds. None keeps the historical behaviour exactly as it was.
    run_log_dir: str | Path | None = None,
    run_log_run_id: str | None = None,
    run_log_policy: RunLogPolicy | None = None,
    recovery_gate: RecoveryPauseGate | None = None,
) -> MonitorRuntime:
    """Compose one read-only monitor with shared durable stores and witness source."""
    if recovery_gate is None:
        recovery_gate = RecoveryPauseGate()
    elif not isinstance(recovery_gate, RecoveryPauseGate):
        raise ValueError("recovery_gate must be a RecoveryPauseGate or None")
    policy = require_task_enabled(task_kind)
    if policy.task_kind is not TaskKind.EXPEDITION:
        raise ValueError(
            "build_runtime is the expedition composition root; task "
            f"{policy.task_kind.value!r} needs its own runtime"
        )
    if not math.isfinite(observer_timeout_seconds) or observer_timeout_seconds <= 0:
        raise ValueError("observer_timeout_seconds must be finite and positive")
    if not math.isfinite(max_state_age_seconds) or max_state_age_seconds < 0:
        raise ValueError("max_state_age_seconds must be finite and non-negative")
    if not math.isfinite(refresh_lead_seconds) or refresh_lead_seconds < 0:
        raise ValueError("refresh_lead_seconds must be finite and non-negative")
    if not dispatch_fleet_ids or len(set(dispatch_fleet_ids)) != len(dispatch_fleet_ids):
        raise ValueError("dispatch_fleet_ids must be a non-empty unique sequence")
    dispatchable = set(policy.dispatchable_fleet_ids)
    if not dispatchable.issuperset(dispatch_fleet_ids):
        raise ValueError(
            "dispatch_fleet_ids must be a subset of the configured dispatchable "
            f"fleets {sorted(dispatchable)}; reserved or out-of-policy fleets "
            f"{sorted(set(dispatch_fleet_ids) - dispatchable)} are not dispatchable"
        )

    # Policy says which fleets MAY be dispatched; the account says which ones
    # EXIST. Resolve the effective set from the observed unlocked fleets so an
    # account without Fleet 3/4 never has a phantom dispatch target.
    if unlocked_fleet_ids is None:
        effective_dispatch_fleet_ids = tuple(dispatch_fleet_ids)
        observed_unlocked: tuple[int, ...] | None = None
        account_fleet_ids = RUNTIME_CONFIG.all_fleet_ids
        # Unverified path: never assert that a not-requested dispatchable
        # fleet EXISTS. Mark those as deferred ("unconfirmed on this account")
        # rather than read-only ("exists but must not be touched"). The
        # monitor's per-tick observation check remains the fail-closed backstop.
        deferred_fleet_ids = tuple(
            fleet_id
            for fleet_id in RUNTIME_CONFIG.dispatchable_fleet_ids
            if fleet_id not in effective_dispatch_fleet_ids
        )
        availability_verified = False
    else:
        availability = resolve_fleet_availability(
            unlocked_fleet_ids,
            dispatch_fleet_ids,
            strict=fleet_availability_strict,
            task_kind=policy.task_kind,
        )
        effective_dispatch_fleet_ids = availability.effective_dispatch_fleet_ids
        observed_unlocked = availability.unlocked_fleet_ids
        account_fleet_ids = observed_unlocked
        deferred_fleet_ids = availability.locked_fleet_ids
        availability_verified = True

    if enable_ui_refresh:
        if ui_input is None or ui_state_verifier is None:
            raise ValueError("UI refresh requires an open game input session and state verifier")
    elif ui_input is not None or ui_state_verifier is not None:
        raise ValueError("UI input and state verifier require enable_ui_refresh=True")

    database = Path(database_path).expanduser()
    database.parent.mkdir(parents=True, exist_ok=True)

    event_sink = SqliteDispatchEventLog(
        str(database), task_kind=policy.task_kind.value
    )
    run_log_sink: RunLogSink | None = None
    if run_log_dir is not None or run_log_policy is not None:
        # One tee, not a second scheduler: the SQLite append-only ledger stays
        # the authoritative durable record and the run log is added beside it.
        run_log_sink = build_run_log_sink(
            directory=run_log_dir,
            policy=run_log_policy,
            task_kind=policy.task_kind.value,
            run_id=run_log_run_id,
        )
        event_sink = TeeEventSink(event_sink, run_log_sink)  # type: ignore[assignment]
    state_store = SqliteFleetStateStore(database)
    intent_store = SqliteDispatchIntentStore(database)
    # The three scheduler roles must be disjoint. Fleets the account has not
    # unlocked go to the deferred group for THIS run; read-only covers only
    # fleets that really exist and are not dispatch targets.
    read_only_fleet_ids = tuple(
        fleet_id
        for fleet_id in account_fleet_ids
        if fleet_id not in effective_dispatch_fleet_ids
        and fleet_id not in deferred_fleet_ids
    )
    scheduler = ContinuousExpeditionScheduler(
        dispatch_fleet_ids=effective_dispatch_fleet_ids,
        read_only_fleet_ids=read_only_fleet_ids,
        deferred_fleet_ids=deferred_fleet_ids,
        state_store=state_store,
    )
    recovery = DurableDispatchCoordinator(scheduler, intent_store)
    unresolved = recovery.recover_pending_intents()

    active_observer = observer or PassivePortResponseObserver(
        observer_script,
        timeout_seconds=observer_timeout_seconds,
    )
    provider = BackgroundPortSnapshotProvider(active_observer)
    reader = PoiExpeditionReader(
        client or PoiMcpClient(poi_endpoint),
        port_snapshot_provider=provider,
        freshness_max_age=timedelta(seconds=max_state_age_seconds),
    )
    # R3-I: which fleet runs which mission is still a scheduling decision, but
    # the NUMBERS for each mission now come from the single versioned static
    # rule table rather than three copies of hardcoded literals. Fleet 4 keeps
    # the conservative mission-1 default; this is not a claim that dispatching
    # Fleet 4 was verified on a real client.
    all_requirements = requirements_for_assignments(
        {2: 1, 3: 3, 4: 1}, rule_set=DEFAULT_RULE_SET
    )
    requirements = {
        fleet_id: all_requirements[fleet_id]
        for fleet_id in effective_dispatch_fleet_ids
    }
    if mission_requirements is not None:
        # A caller that dispatches a mission other than the ones configured
        # here has to say which one. The R3-I evaluator remains authoritative
        # for the full condition set; this legacy mapping is only a blunt
        # pre-check used by the monitor.
        if set(mission_requirements) != set(effective_dispatch_fleet_ids):
            raise ValueError(
                "mission_requirements must configure every and only effective "
                "dispatchable fleet"
            )
        requirements = dict(mission_requirements)
    ui_arbiter = (
        UiOperationArbiter(action_guard=recovery_gate.require_actions_allowed)
        if enable_ui_refresh else None
    )
    ui_refresh_driver = None
    state_reader = reader
    if enable_ui_refresh:
        ui_refresh_driver = VisibleUiPortRefreshDriver(
            game_input=ui_input,
            verify_state=ui_state_verifier,
            ui_arbiter=ui_arbiter,
            event_sink=event_sink,
        )
        state_reader = FreshnessRefreshingStateReader(
            reader,
            ui_refresh_driver,
            max_wait_seconds=refresh_wait_seconds,
            poll_interval_seconds=refresh_poll_seconds,
            refresh_cooldown_seconds=refresh_cooldown_seconds,
            # Every refresh is attributed to the caller that asked for it, so
            # the audit trail says why the client was navigated.
            event_sink=event_sink,
        )

    monitor = ContinuousExpeditionMonitor(
        scheduler=scheduler,
        state_reader=state_reader,
        mission_requirements=requirements,
        event_sink=event_sink,
        max_state_age=timedelta(seconds=max_state_age_seconds),
        refresh_lead_time=timedelta(seconds=refresh_lead_seconds),
        adopt_unplanned_missions=adopt_unplanned_missions,
    )
    return MonitorRuntime(
        monitor=monitor,
        provider=provider,
        scheduler=scheduler,
        unresolved_dispatch_fleets=unresolved,
        ui_refresh_enabled=enable_ui_refresh,
        ui_arbiter=ui_arbiter,
        ui_refresh_driver=ui_refresh_driver,
        unlocked_fleet_ids=observed_unlocked,
        fleet_availability_verified=availability_verified,
        task_kind=policy.task_kind.value,
        run_id=run_log_sink.run_id if run_log_sink is not None else "",
        run_log_sink=run_log_sink,
        event_sink=event_sink,
        recovery_gate=recovery_gate,
    )


def _emit(payload: dict[str, Any], output: Callable[[str], None]) -> None:
    output(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _stdout(line: str) -> None:
    print(line, flush=True)


def _observer_status(runtime: MonitorRuntime) -> dict[str, Any]:
    return {
        "observer_status": getattr(runtime.provider, "last_status", "unknown"),
        "observer_running": bool(getattr(runtime.provider, "is_running", False)),
    }


def run_monitor(
    runtime: MonitorRuntime,
    *,
    poll_seconds: float = 5.0,
    max_ticks: int | None = None,
    once: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    output: Callable[[str], None] = _stdout,
    recover: Callable[[], bool] | None = None,
) -> int:
    """Poll until stopped; freshness failures never change scheduler readiness.

    ``recover`` is offered every failed tick and returns True when the fault was
    repaired, in which case the tick is retried immediately instead of waiting
    out the poll interval. It is what lets a blocking dialog that appears
    *during* a run be cleared; the caller owns the allowance that stops it from
    retrying forever.
    """
    if not math.isfinite(poll_seconds) or poll_seconds <= 0:
        raise ValueError("poll_seconds must be finite and positive")
    if max_ticks is not None and (
        isinstance(max_ticks, bool) or not isinstance(max_ticks, int) or max_ticks < 1
    ):
        raise ValueError("max_ticks must be a positive integer")
    if once and max_ticks is not None and max_ticks != 1:
        raise ValueError("once cannot be combined with max_ticks other than 1")

    tick_count = 0
    while True:
        tick_count += 1
        try:
            recovery_gate = getattr(runtime, "recovery_gate", None)
            if recovery_gate is not None:
                recovery_gate.require_actions_allowed()
            report = runtime.monitor.tick()
        except RecoveryPausedError as exc:
            _emit({
                "status": "RECOVERY_PAUSED",
                "tick": tick_count,
                "category": exc.category.value,
                "generation": exc.generation,
                "reason": exc.reason,
                "game_actions_enabled": False,
                "ui_navigation_enabled": runtime.ui_refresh_enabled,
            }, output)
            return 3
        except ContinuousMonitorError as exc:
            message = str(exc)
            status = (
                "FRESHNESS_UNVERIFIED"
                if "not trusted" in message
                else "MONITOR_BLOCKED"
            )
            _emit(
                {
                    "status": status,
                    "tick": tick_count,
                    "error_type": type(exc).__name__,
                    "reason": message,
                    "source_freshness_verified": False,
                    "unresolved_dispatch_fleets": list(
                        runtime.unresolved_dispatch_fleets
                    ),
                    **_observer_status(runtime),
                    "game_actions_enabled": False,
                    "ui_navigation_enabled": runtime.ui_refresh_enabled,
                },
                output,
            )
            if recover is not None and recover():
                continue
            if once or (max_ticks is not None and tick_count >= max_ticks):
                return 2
        except Exception as exc:
            _emit(
                {
                    "status": "MONITOR_TICK_FAILED",
                    "tick": tick_count,
                    "error_type": type(exc).__name__,
                    **_observer_status(runtime),
                    "game_actions_enabled": False,
                    "ui_navigation_enabled": runtime.ui_refresh_enabled,
                },
                output,
            )
            # A parked renderer surfaces here as a bare timeout rather than a
            # scheduler error, so this path needs the recovery offer too.
            if recover is not None and recover():
                continue
            return 2
        else:
            _emit(
                {
                    "status": "MONITOR_TICK_OK",
                    "tick": tick_count,
                    "observed_at": report.observed_at.isoformat(),
                    "source": report.source,
                    "source_freshness_verified": report.source_freshness_verified,
                    **_observer_status(runtime),
                    "fleets": [
                        {
                            "fleet_id": item.fleet_id,
                            "previous_phase": item.previous_phase.value,
                            "current_phase": item.current_phase.value,
                            "action": item.action,
                            "reason": item.reason,
                        }
                        for item in report.fleets
                    ],
                    "unresolved_dispatch_fleets": list(
                        runtime.unresolved_dispatch_fleets
                    ),
                    "game_actions_enabled": False,
                    "ui_navigation_enabled": runtime.ui_refresh_enabled,
                },
                output,
            )
            if once or (max_ticks is not None and tick_count >= max_ticks):
                return 0

        sleep(poll_seconds)


def _modal_budget(args: Any) -> Any | None:
    """One press allowance for the whole process, or None when auto-dismiss is off."""
    if not getattr(args, "auto_dismiss_modal", False):
        return None
    from kancollebot.adapters.poi.modal_window import DismissBudget

    return DismissBudget(limit=args.modal_dismiss_limit)


def _dismiss_modal_if_needed(
    budget: Any,
    *,
    probe: Any | None = None,
    emit: Callable[[dict[str, Any]], None] | None = None,
) -> Any | None:
    """Probe the surface, and press Cancel only when CDP blames a modal.

    Returns the verdict in hand afterwards (never None when one was passed in),
    so the caller can decide from ``input_allowed`` whether to carry on. Every
    press is drawn from ``budget``; refusals cost nothing. Nothing here guesses
    at a coordinate or at which window to close.
    """
    from kancollebot.adapters.poi.modal_window import (
        RecoveryOutcome,
        Win32Backend,
        dismiss_blocking_modal,
    )
    from kancollebot.adapters.poi.renderer_block import live_probe

    emitter = emit if emit is not None else (lambda event: _emit(event, _stdout))
    calls = {"n": 0}

    def fresh() -> Any:
        # Reuse a verdict already in hand for the "before" state: probing a
        # parked renderer costs a whole timeout, and it was just paid for.
        calls["n"] = calls["n"] + 1
        if calls["n"] == 1 and probe is not None:
            return probe
        return live_probe()

    try:
        backend = Win32Backend()
    except Exception as exc:  # noqa: BLE001 - window access can be unavailable
        emitter(
            {
                "status": "MODAL_RECOVERY_UNAVAILABLE",
                "error_type": type(exc).__name__,
                "reason": str(exc),
                "game_actions_enabled": False,
            }
        )
        return probe

    try:
        recovery = dismiss_blocking_modal(
            allow_window_input=True, backend=backend, probe=fresh, budget=budget
        )
    except Exception as exc:  # noqa: BLE001 - recovery is best effort, never fatal
        emitter(
            {
                "status": "MODAL_RECOVERY_FAILED",
                "error_type": type(exc).__name__,
                "reason": str(exc),
                "game_actions_enabled": False,
            }
        )
        return probe

    event: dict[str, Any] = {
        "status": "MODAL_CANCEL_ATTEMPTED",
        # Outcomes that mean "a human should look at this now". The content
        # verdicts are here because they say the automation could not prove the
        # dialog was the known prompt, so it stopped on purpose.
        "escalate_to_human": recovery.outcome
        in {
            RecoveryOutcome.EXHAUSTED,
            RecoveryOutcome.STILL_BLOCKED,
            RecoveryOutcome.CONTENT_UNVERIFIED,
            RecoveryOutcome.UNRECOGNISED_DIALOG,
        },
    }
    event.update(recovery.as_event())
    emitter(event)
    return recovery.probe_after


def _build_ui_state_verifier(
    game_session: Any, capture_dir: str | None = None
) -> Callable[[str], UiStateEvidence]:
    """Adapt the production two-frame Ooi checkpoint gate to the refresh driver."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from tools.one_expedition_ui_loop import _require_ui_state
    from kancollebot.adapters.poi.ui_state_gate import UiState

    def verify(expected: str) -> UiStateEvidence:
        state = UiState(expected)
        # Keep the default call shape identical: a diagnostic capture is opt-in.
        if capture_dir is None:
            evidence = _require_ui_state(game_session, state, visual_attempts=6)
        else:
            evidence = _require_ui_state(
                game_session, state, visual_attempts=6, capture_dir=capture_dir
            )
        return UiStateEvidence(
            state=state.value,
            consecutive_states=(state.value, state.value),
            evidence=evidence,
        )

    return verify


def _run_monitor_with_session(
    args: Any, game_session: Any | None = None, budget: Any | None = None
) -> int:
    enable_ui_refresh = game_session is not None
    # Pressing a window is a GUI action, so it stays tied to the UI-refresh
    # path: a read-only run must not touch a window even to clear a dialog.
    if not enable_ui_refresh:
        budget = None
    elif budget is None:
        budget = _modal_budget(args)
    budget_exhausted_announced = {"done": False}

    def recover() -> bool:
        """Clear a blocking dialog that appeared mid-run, within the allowance."""
        if budget is None:
            return False
        if not budget.available():
            # Announce the escalation once, not on every failing tick.
            if not budget_exhausted_announced["done"]:
                budget_exhausted_announced["done"] = True
                _emit(
                    {
                        "status": "MODAL_CANCEL_EXHAUSTED",
                        "reason": (
                            "the prompt keeps coming back after "
                            f"{budget.describe()} presses; the network is still down"
                        ),
                        "dismiss_budget": budget.describe(),
                        "manual_action": "检查网络后手动处理，不要继续自动监控",
                        "game_actions_enabled": False,
                    },
                    _stdout,
                )
            return False
        verdict = _dismiss_modal_if_needed(budget, emit=_stdout)
        return bool(getattr(verdict, "input_allowed", False))

    try:
        runtime = build_runtime(
            database_path=args.database,
            poi_endpoint=args.poi_endpoint,
            observer_script=args.observer_script,
            observer_timeout_seconds=args.observer_timeout_seconds,
            max_state_age_seconds=args.max_state_age_seconds,
            refresh_lead_seconds=args.refresh_lead_seconds,
            enable_ui_refresh=enable_ui_refresh,
            ui_input=game_session.input if game_session is not None else None,
            ui_state_verifier=(
                _build_ui_state_verifier(game_session, args.diagnostic_capture_dir)
                if game_session is not None
                else None
            ),
            refresh_wait_seconds=args.refresh_wait_seconds,
            refresh_poll_seconds=args.refresh_poll_seconds,
            refresh_cooldown_seconds=args.refresh_cooldown_seconds,
            # P0 R4: both production entries now pass the observed account
            # fleet set and an explicit strictness decision. dispatch-once
            # probes it (strict=True for a named target); the monitor takes an
            # operator-supplied set (None = unverified, backstop stays active).
            unlocked_fleet_ids=getattr(args, "unlocked_fleet_ids", None),
            fleet_availability_strict=bool(
                getattr(args, "fleet_availability_strict", False)
            ),
            task_kind=TaskKind.EXPEDITION,
            run_log_dir=getattr(args, "run_log_dir", None),
        )
    except (OSError, ValueError) as exc:
        _emit(
            {
                "status": "STARTUP_FAILED",
                "error_type": type(exc).__name__,
                "reason": str(exc),
                "game_actions_enabled": False,
                "ui_navigation_enabled": enable_ui_refresh,
            },
            print,
        )
        return 2

    _emit(
        {
            "status": "READ_ONLY_MONITOR_STARTED",
            "database": str(args.database),
            "unresolved_dispatch_fleets": list(
                runtime.unresolved_dispatch_fleets
            ),
            "dispatch_fleets": list(runtime.scheduler.dispatch_fleet_ids),
            "dispatch_candidate_ceiling": list(
                RUNTIME_CONFIG.monitor_candidate_fleet_ids
            ),
            "read_only_fleets": list(runtime.scheduler.read_only_fleet_ids),
            "deferred_fleets": list(runtime.scheduler.deferred_fleet_ids),
            "unlocked_fleet_ids": (
                list(runtime.unlocked_fleet_ids)
                if runtime.unlocked_fleet_ids is not None
                else None
            ),
            "fleet_availability_verified": runtime.fleet_availability_verified,
            "game_actions_enabled": False,
            "ui_navigation_enabled": runtime.ui_refresh_enabled,
            "run_id": getattr(runtime, "run_id", "") or None,
            "run_log_directory": (
                str(getattr(runtime, "run_log_sink").directory)
                if getattr(runtime, "run_log_sink", None) is not None
                else None
            ),
        },
        _stdout,
    )
    run_log_sink = getattr(runtime, "run_log_sink", None)
    if run_log_sink is not None:
        # The capability belongs to the sink the components emit through. A tee
        # is capable when any member is, which is computed from real attributes
        # and never from "the object has a correlation field".
        run_log_sink.start({
            "database": str(args.database),
            "dispatch_fleets": list(runtime.scheduler.dispatch_fleet_ids),
            "ui_navigation_enabled": runtime.ui_refresh_enabled,
            "game_actions_enabled": False,
            **correlation_capability(runtime.event_sink).as_fields(),
        })
    try:
        return run_monitor(
            runtime,
            poll_seconds=args.poll_seconds,
            max_ticks=args.max_ticks,
            once=args.once,
            recover=recover,
        )
    except KeyboardInterrupt:
        _emit(
            {
                "status": "MONITOR_STOPPED",
                "reason": "keyboard_interrupt",
                "game_actions_enabled": False,
                "ui_navigation_enabled": runtime.ui_refresh_enabled,
            },
            _stdout,
        )
        return 130
    finally:
        # Lifecycle rows are diagnostics, not a gate: a stop record that fails to
        # write must never replace the exception that actually ended the run.
        if run_log_sink is not None:
            run_log_sink.stop("monitor_exited")
        runtime.provider.close(join_timeout_seconds=1.0)


def _main_impl(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--poi-endpoint", default=DEFAULT_POI_ENDPOINT)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument(
        "--run-log-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Also write the unified three-day rolling JSONL run log to DIR "
            "(default off). The SQLite append-only ledger is always written; "
            "this adds the correlation envelope used to rebuild a run."
        ),
    )
    parser.add_argument("--observer-script", type=Path, default=DEFAULT_OBSERVER_SCRIPT)
    parser.add_argument("--observer-timeout-seconds", type=float, default=RUNTIME_CONFIG.observer_timeout_seconds)
    parser.add_argument("--max-state-age-seconds", type=float, default=RUNTIME_CONFIG.max_state_age_seconds)
    parser.add_argument(
        "--refresh-lead-seconds",
        type=float,
        default=60.0,
        help="how close a return must be before a stale observation justifies navigation",
    )
    parser.add_argument("--poll-seconds", type=float, default=RUNTIME_CONFIG.monitor_poll_seconds)
    parser.add_argument("--max-ticks", type=int)
    parser.add_argument(
        "--unlocked-fleet-ids",
        default=None,
        metavar="IDS",
        help=(
            "Observed account fleet ids as a comma-separated contiguous list "
            "starting at 1 (for example 1,2,3). This is an ACCOUNT FACT, not a "
            "dispatch policy: fleets not in the list are dropped from the "
            "candidate set. Omitting it keeps the read-only unverified path, "
            "where the per-tick observation check stays the fail-closed "
            "backstop and the run reports fleet_availability_verified=false."
        ),
    )
    parser.add_argument(
        "--fleet-availability-strict",
        action="store_true",
        help=(
            "With --unlocked-fleet-ids, refuse to start if any requested "
            "candidate fleet is not unlocked instead of trimming the set. "
            "dispatch-once always uses this strict mode for its named target."
        ),
    )
    parser.add_argument(
        "--require-verified-fleet-availability",
        action="store_true",
        help=(
            "Fail closed unless an observed --unlocked-fleet-ids set was "
            "supplied. Use this to demand the same verified account fact that "
            "dispatch-once obtains from its zero-click CDP probe."
        ),
    )
    parser.add_argument(
        "--enable-ui-refresh",
        action="store_true",
        help=(
            "Allow only the verified visible HOME_PORT -> SORTIE_MENU -> "
            "EXPEDITION_LIST -> HOME_PORT navigation to obtain a natural Poi "
            "freshness witness. Dispatch, settlement, and resupply remain disabled."
        ),
    )
    parser.add_argument("--refresh-wait-seconds", type=float, default=RUNTIME_CONFIG.refresh_wait_seconds)
    parser.add_argument("--refresh-poll-seconds", type=float, default=RUNTIME_CONFIG.refresh_poll_seconds)
    parser.add_argument("--refresh-cooldown-seconds", type=float, default=RUNTIME_CONFIG.refresh_cooldown_seconds)
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one monitor tick and exit; unverified freshness remains blocked.",
    )
    dismiss_group = parser.add_mutually_exclusive_group()
    dismiss_group.add_argument(
        "--auto-dismiss-modal",
        dest="auto_dismiss_modal",
        action="store_true",
        default=None,
        help=(
            "Press the Cancel button of a blocking refresh dialog before "
            "navigating, and again if one appears mid-run. On by default; only "
            "the button labelled 取消/キャンセル/Cancel is addressable, and only "
            "while --enable-ui-refresh is on. Presses stop after "
            "--modal-dismiss-limit so a dead network escalates instead of being "
            "cancelled forever."
        ),
    )
    dismiss_group.add_argument(
        "--no-auto-dismiss-modal",
        dest="auto_dismiss_modal",
        action="store_false",
        help="Never touch a window; report the block and stop instead.",
    )
    parser.add_argument(
        "--modal-dismiss-limit",
        type=int,
        default=3,
        help=(
            "How many times one process may press Cancel before escalating to a "
            "human (default 3). Counts presses, not attempts."
        ),
    )
    parser.add_argument(
        "--diagnostic-capture-dir",
        default=None,
        metavar="DIR",
        help=(
            "Write the screenshot behind each confirmed UI state to DIR (one PNG "
            "per state). Off by default: screenshots otherwise stay in memory. "
            "Intended for an explicit calibration pass whose result is analysed "
            "offline; put it somewhere ignored by git."
        ),
    )
    args = parser.parse_args(argv)
    # P0 R4: normalize the account fact ONCE, before any session is opened.
    # Empty, non-integer, duplicate, non-contiguous or out-of-policy sets all
    # fail closed here rather than being silently widened.
    try:
        args.unlocked_fleet_ids = (
            parse_unlocked_fleet_ids(args.unlocked_fleet_ids)
            if args.unlocked_fleet_ids is not None
            else None
        )
    except FleetAvailabilityError as exc:
        parser.error(f"--unlocked-fleet-ids rejected: {exc}")
    if args.fleet_availability_strict and args.unlocked_fleet_ids is None:
        parser.error("--fleet-availability-strict requires --unlocked-fleet-ids")
    if args.require_verified_fleet_availability and args.unlocked_fleet_ids is None:
        parser.error(
            "--require-verified-fleet-availability requires an observed "
            "--unlocked-fleet-ids set; the monitor will not claim a verified "
            "account fact it does not have"
        )
    # None means the flag was not mentioned at all, which now means "on".
    if args.auto_dismiss_modal is None:
        args.auto_dismiss_modal = True
    if args.auto_dismiss_modal and not args.enable_ui_refresh:
        # Only worth complaining about when it was asked for explicitly.
        if "--auto-dismiss-modal" in (argv if argv is not None else sys.argv[1:]):
            parser.error(
                "--auto-dismiss-modal only applies together with --enable-ui-refresh"
            )
    if args.modal_dismiss_limit < 1:
        parser.error("--modal-dismiss-limit must be a positive integer")
    if not math.isfinite(args.poll_seconds) or args.poll_seconds <= 0:
        parser.error("--poll-seconds must be finite and positive")
    if not math.isfinite(args.refresh_lead_seconds) or args.refresh_lead_seconds < 0:
        parser.error("--refresh-lead-seconds must be finite and non-negative")
    if args.max_ticks is not None and args.max_ticks < 1:
        parser.error("--max-ticks must be a positive integer")
    if args.once and args.max_ticks is not None and args.max_ticks != 1:
        parser.error("--once cannot be combined with --max-ticks other than 1")
    if args.enable_ui_refresh:
        if (
            not math.isfinite(args.refresh_wait_seconds)
            or args.refresh_wait_seconds <= 0
            or not math.isfinite(args.refresh_poll_seconds)
            or args.refresh_poll_seconds <= 0
            or not math.isfinite(args.refresh_cooldown_seconds)
            or args.refresh_cooldown_seconds < 0
        ):
            parser.error("UI refresh wait/poll must be positive and cooldown non-negative")

        try:
            from kancollebot.adapters.poi.cdp_websocket import connect_game_webview
            from kancollebot.adapters.poi.renderer_block import MANUAL_ACTION, live_probe

            # A blocking refresh confirm parks the game renderer, so every later
            # command would burn its own timeout and fail with an unrelated
            # message. Probe once up front and name the real cause instead.
            probe = live_probe()
            budget = _modal_budget(args)
            if not probe.input_allowed and budget is not None and probe.dialog_suspected:
                recovered = _dismiss_modal_if_needed(budget, probe=probe, emit=_stdout)
                if recovered is not None:
                    probe = recovered

            if not probe.input_allowed:
                _emit(
                    {
                        "status": "BLOCKED_GAME_RENDERER",
                        "reason": probe.reason.value,
                        "detail": probe.detail,
                        "dialog_suspected": probe.dialog_suspected,
                        "manual_action": MANUAL_ACTION if probe.dialog_suspected else None,
                        "game_actions_enabled": False,
                        "ui_navigation_enabled": True,
                    },
                    print,
                )
                return 2

            with connect_game_webview() as game_session:
                return _run_monitor_with_session(args, game_session, budget)
        except Exception as exc:
            _emit(
                {
                    "status": "STARTUP_FAILED",
                    "error_type": type(exc).__name__,
                    "reason": str(exc),
                    "game_actions_enabled": False,
                    "ui_navigation_enabled": True,
                },
                print,
            )
            return 2

    return _run_monitor_with_session(args)


def main(argv: list[str] | None = None) -> int:
    """Compatibility shim; production commands route through the unified CLI."""
    from kancollebot.cli import main as unified_main

    forwarded = sys.argv[1:] if argv is None else argv
    return unified_main(["monitor", *forwarded])


if __name__ == "__main__":
    raise SystemExit(main())
