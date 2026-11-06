"""Single source of truth for local KancolleBot runtime defaults."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    all_fleet_ids: tuple[int, ...] = (1, 2, 3)
    monitor_candidate_fleet_ids: tuple[int, ...] = (2, 3)
    production_dispatch_fleet_id: int = 3
    production_mission_id: int = 3
    deferred_fleet_ids: tuple[int, ...] = (4,)
    poi_endpoint: str = "http://127.0.0.1:12450/mcp"
    observer_timeout_seconds: float = 130.0
    max_state_age_seconds: float = 30.0
    monitor_poll_seconds: float = 5.0
    refresh_wait_seconds: float = 45.0
    refresh_poll_seconds: float = 0.5
    refresh_cooldown_seconds: float = 30.0

    @property
    def observer_script(self) -> Path:
        return REPOSITORY_ROOT / "tools" / "hand_sandbox" / "poi_port_response_observer_20261029.js"

    @property
    def monitor_database(self) -> Path:
        return REPOSITORY_ROOT / "logs" / "continuous_expedition.sqlite3"

    @property
    def dispatch_database(self) -> Path:
        return REPOSITORY_ROOT / "logs" / "one_expedition_production.sqlite3"

    @property
    def dispatch_log_path(self) -> Path:
        return REPOSITORY_ROOT / "logs" / "one_expedition_ui_loop.jsonl"


RUNTIME_CONFIG = RuntimeConfig()

if RUNTIME_CONFIG.production_dispatch_fleet_id not in RUNTIME_CONFIG.all_fleet_ids:
    raise RuntimeError("Production dispatch fleet must exist in all_fleet_ids")
if RUNTIME_CONFIG.production_dispatch_fleet_id not in RUNTIME_CONFIG.monitor_candidate_fleet_ids:
    raise RuntimeError("Production dispatch fleet must be included in monitor candidates")
if len(set(RUNTIME_CONFIG.all_fleet_ids)) != len(RUNTIME_CONFIG.all_fleet_ids):
    raise RuntimeError("all_fleet_ids must be unique")
if len(set(RUNTIME_CONFIG.monitor_candidate_fleet_ids)) != len(RUNTIME_CONFIG.monitor_candidate_fleet_ids):
    raise RuntimeError("monitor_candidate_fleet_ids must be unique")
