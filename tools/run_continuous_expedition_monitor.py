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


DEFAULT_OBSERVER_SCRIPT = (
    ROOT / "tools" / "hand_sandbox" / "poi_port_response_observer_20261029.js"
)
DEFAULT_DATABASE = ROOT / "logs" / "continuous_expedition.sqlite3"


@dataclass
class MonitorRuntime:
    monitor: ContinuousExpeditionMonitor
    provider: BackgroundPortSnapshotProvider
    scheduler: ContinuousExpeditionScheduler
    unresolved_dispatch_fleets: tuple[int, ...]
    ui_refresh_enabled: bool = False
    ui_arbiter: UiOperationArbiter | None = None
    ui_refresh_driver: VisibleUiPortRefreshDriver | None = None


def build_runtime(
    *,
    database_path: str | Path = DEFAULT_DATABASE,
    poi_endpoint: str = "http://127.0.0.1:12450/mcp",
    observer_script: str | Path = DEFAULT_OBSERVER_SCRIPT,
    observer_timeout_seconds: float = 130.0,
    max_state_age_seconds: float = 30.0,
    client: PoiMcpClient | None = None,
    observer: PassivePortResponseObserver | None = None,
    dispatch_fleet_ids: tuple[int, ...] = (2, 3),
    enable_ui_refresh: bool = False,
    ui_input: Any | None = None,
    ui_state_verifier: Callable[[str], UiStateEvidence] | None = None,
    refresh_wait_seconds: float = 45.0,
    refresh_poll_seconds: float = 0.5,
    refresh_cooldown_seconds: float = 30.0,
) -> MonitorRuntime:
    """Compose one read-only monitor with shared durable stores and witness source."""
    if not math.isfinite(observer_timeout_seconds) or observer_timeout_seconds <= 0:
        raise ValueError("observer_timeout_seconds must be finite and positive")
    if not math.isfinite(max_state_age_seconds) or max_state_age_seconds < 0:
        raise ValueError("max_state_age_seconds must be finite and non-negative")
    if (
        not dispatch_fleet_ids
        or len(set(dispatch_fleet_ids)) != len(dispatch_fleet_ids)
        or any(fleet_id not in (2, 3) for fleet_id in dispatch_fleet_ids)
    ):
        raise ValueError("dispatch_fleet_ids must be a non-empty unique subset of (2, 3)")
    if enable_ui_refresh:
        if ui_input is None or ui_state_verifier is None:
            raise ValueError("UI refresh requires an open game input session and state verifier")
    elif ui_input is not None or ui_state_verifier is not None:
        raise ValueError("UI input and state verifier require enable_ui_refresh=True")

    database = Path(database_path).expanduser()
    database.parent.mkdir(parents=True, exist_ok=True)

    event_sink = SqliteDispatchEventLog(str(database))
    state_store = SqliteFleetStateStore(database)
    intent_store = SqliteDispatchIntentStore(database)
    read_only_fleet_ids = tuple(
        fleet_id for fleet_id in (1, 2, 3) if fleet_id not in dispatch_fleet_ids
    )
    scheduler = ContinuousExpeditionScheduler(
        dispatch_fleet_ids=dispatch_fleet_ids,
        read_only_fleet_ids=read_only_fleet_ids,
        deferred_fleet_ids=(4,),
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
    all_requirements = {
        2: MissionCompositionRequirement(
            mission_id=1,
            minimum_ships=2,
            minimum_flagship_level=1,
        ),
        3: MissionCompositionRequirement(
            mission_id=3,
            minimum_ships=3,
            minimum_flagship_level=3,
        ),
    }
    requirements = {
        fleet_id: all_requirements[fleet_id] for fleet_id in dispatch_fleet_ids
    }
    ui_arbiter = UiOperationArbiter() if enable_ui_refresh else None
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
        )

    monitor = ContinuousExpeditionMonitor(
        scheduler=scheduler,
        state_reader=state_reader,
        mission_requirements=requirements,
        event_sink=event_sink,
        max_state_age=timedelta(seconds=max_state_age_seconds),
    )
    return MonitorRuntime(
        monitor=monitor,
        provider=provider,
        scheduler=scheduler,
        unresolved_dispatch_fleets=unresolved,
        ui_refresh_enabled=enable_ui_refresh,
        ui_arbiter=ui_arbiter,
        ui_refresh_driver=ui_refresh_driver,
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
) -> int:
    """Poll until stopped; freshness failures never change scheduler readiness."""
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
            report = runtime.monitor.tick()
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
                    "unresolved_dispatch_fleets": list(
                        runtime.unresolved_dispatch_fleets
                    ),
                    **_observer_status(runtime),
                    "game_actions_enabled": False,
                    "ui_navigation_enabled": runtime.ui_refresh_enabled,
                },
                output,
            )
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
            return 2
        else:
            _emit(
                {
                    "status": "MONITOR_TICK_OK",
                    "tick": tick_count,
                    "observed_at": report.observed_at.isoformat(),
                    "source": report.source,
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


def _build_ui_state_verifier(game_session: Any) -> Callable[[str], UiStateEvidence]:
    """Adapt the production two-frame Ooi checkpoint gate to the refresh driver."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from tools.one_expedition_ui_loop import _require_ui_state
    from kancollebot.adapters.poi.ui_state_gate import UiState

    def verify(expected: str) -> UiStateEvidence:
        state = UiState(expected)
        evidence = _require_ui_state(game_session, state)
        return UiStateEvidence(
            state=state.value,
            consecutive_states=(state.value, state.value),
            evidence=evidence,
        )

    return verify


def _run_monitor_with_session(args: Any, game_session: Any | None = None) -> int:
    enable_ui_refresh = game_session is not None
    try:
        runtime = build_runtime(
            database_path=args.database,
            poi_endpoint=args.poi_endpoint,
            observer_script=args.observer_script,
            observer_timeout_seconds=args.observer_timeout_seconds,
            max_state_age_seconds=args.max_state_age_seconds,
            enable_ui_refresh=enable_ui_refresh,
            ui_input=game_session.input if game_session is not None else None,
            ui_state_verifier=(
                _build_ui_state_verifier(game_session)
                if game_session is not None
                else None
            ),
            refresh_wait_seconds=args.refresh_wait_seconds,
            refresh_poll_seconds=args.refresh_poll_seconds,
            refresh_cooldown_seconds=args.refresh_cooldown_seconds,
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
            "dispatch_fleets": [2, 3],
            "read_only_fleets": [1],
            "deferred_fleets": [4],
            "game_actions_enabled": False,
            "ui_navigation_enabled": runtime.ui_refresh_enabled,
        },
        _stdout,
    )
    try:
        return run_monitor(
            runtime,
            poll_seconds=args.poll_seconds,
            max_ticks=args.max_ticks,
            once=args.once,
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
        runtime.provider.close(join_timeout_seconds=1.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--poi-endpoint", default="http://127.0.0.1:12450/mcp")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--observer-script", type=Path, default=DEFAULT_OBSERVER_SCRIPT)
    parser.add_argument("--observer-timeout-seconds", type=float, default=130.0)
    parser.add_argument("--max-state-age-seconds", type=float, default=30.0)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--max-ticks", type=int)
    parser.add_argument(
        "--enable-ui-refresh",
        action="store_true",
        help=(
            "Allow only the verified visible HOME_PORT -> SORTIE_MENU -> "
            "EXPEDITION_LIST -> HOME_PORT navigation to obtain a natural Poi "
            "freshness witness. Dispatch, settlement, and resupply remain disabled."
        ),
    )
    parser.add_argument("--refresh-wait-seconds", type=float, default=45.0)
    parser.add_argument("--refresh-poll-seconds", type=float, default=0.5)
    parser.add_argument("--refresh-cooldown-seconds", type=float, default=30.0)
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one monitor tick and exit; unverified freshness remains blocked.",
    )
    args = parser.parse_args(argv)
    if not math.isfinite(args.poll_seconds) or args.poll_seconds <= 0:
        parser.error("--poll-seconds must be finite and positive")
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

            with connect_game_webview() as game_session:
                return _run_monitor_with_session(args, game_session)
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


if __name__ == "__main__":
    raise SystemExit(main())
