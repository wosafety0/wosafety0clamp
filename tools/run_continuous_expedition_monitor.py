"""Run a read-only continuous monitor over the configured expedition fleets.

This runtime wires the passive Poi freshness provider into the durable fleet
state store and continuous monitor. It never creates a UI driver, opens a game
CDP input session, clicks, dispatches, collects rewards, or resupplies.
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
)
from kancollebot.expeditions.continuous_monitor import (  # noqa: E402
    ContinuousExpeditionMonitor,
    ContinuousMonitorError,
)
from kancollebot.expeditions.continuous_scheduler import (  # noqa: E402
    ContinuousExpeditionScheduler,
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


def build_runtime(
    *,
    database_path: str | Path = DEFAULT_DATABASE,
    poi_endpoint: str = "http://127.0.0.1:12450/mcp",
    observer_script: str | Path = DEFAULT_OBSERVER_SCRIPT,
    observer_timeout_seconds: float = 130.0,
    max_state_age_seconds: float = 30.0,
    client: PoiMcpClient | None = None,
    observer: PassivePortResponseObserver | None = None,
) -> MonitorRuntime:
    """Compose one read-only monitor with shared durable stores and witness source."""
    if not math.isfinite(observer_timeout_seconds) or observer_timeout_seconds <= 0:
        raise ValueError("observer_timeout_seconds must be finite and positive")
    if not math.isfinite(max_state_age_seconds) or max_state_age_seconds < 0:
        raise ValueError("max_state_age_seconds must be finite and non-negative")

    database = Path(database_path).expanduser()
    database.parent.mkdir(parents=True, exist_ok=True)

    event_sink = SqliteDispatchEventLog(str(database))
    state_store = SqliteFleetStateStore(database)
    intent_store = SqliteDispatchIntentStore(database)
    scheduler = ContinuousExpeditionScheduler(
        dispatch_fleet_ids=(2, 3),
        read_only_fleet_ids=(1,),
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
    requirements = {
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
    monitor = ContinuousExpeditionMonitor(
        scheduler=scheduler,
        state_reader=reader,
        mission_requirements=requirements,
        event_sink=event_sink,
        max_state_age=timedelta(seconds=max_state_age_seconds),
    )
    return MonitorRuntime(
        monitor=monitor,
        provider=provider,
        scheduler=scheduler,
        unresolved_dispatch_fleets=unresolved,
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
                },
                output,
            )
            if once or (max_ticks is not None and tick_count >= max_ticks):
                return 0

        sleep(poll_seconds)


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
        "--once",
        action="store_true",
        help="Run one read-only tick and exit; no freshness is inferred if no witness exists.",
    )
    args = parser.parse_args(argv)
    if not math.isfinite(args.poll_seconds) or args.poll_seconds <= 0:
        parser.error("--poll-seconds must be finite and positive")
    if args.max_ticks is not None and args.max_ticks < 1:
        parser.error("--max-ticks must be a positive integer")
    if args.once and args.max_ticks is not None and args.max_ticks != 1:
        parser.error("--once cannot be combined with --max-ticks other than 1")

    try:
        runtime = build_runtime(
            database_path=args.database,
            poi_endpoint=args.poi_endpoint,
            observer_script=args.observer_script,
            observer_timeout_seconds=args.observer_timeout_seconds,
            max_state_age_seconds=args.max_state_age_seconds,
        )
    except (OSError, ValueError) as exc:
        _emit(
            {
                "status": "STARTUP_FAILED",
                "error_type": type(exc).__name__,
                "game_actions_enabled": False,
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
            },
            _stdout,
        )
        return 130
    finally:
        runtime.provider.close(join_timeout_seconds=1.0)


if __name__ == "__main__":
    raise SystemExit(main())
