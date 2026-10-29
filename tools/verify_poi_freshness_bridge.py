"""Read-only end-to-end diagnostic for the optional passive port witness bridge.

This command only observes CDP network events and reads the local Poi MCP
store. It never refreshes the page, sends game input, calls game APIs, or
dispatches an expedition. It may wait up to the configured observer timeout
for a naturally occurring successful port response.
"""
from __future__ import annotations

import argparse
import json
from datetime import timedelta
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from kancollebot.adapters.poi.expedition_reader import PoiExpeditionReader
from kancollebot.adapters.poi.port_response_observer import PassivePortResponseObserver


DEFAULT_OBSERVER_SCRIPT = (
    REPO_ROOT / "tools" / "hand_sandbox" / "poi_port_response_observer_20261029.js"
)


def summarize_state(state: Any, observer_status: str) -> dict[str, Any]:
    """Return safe diagnostic metadata without fleet, ship, or mission details."""
    verified = bool(getattr(state, "source_freshness_verified", False))
    payload = getattr(state, "payload", ())
    observed_at = getattr(state, "observed_at", None)
    source = getattr(state, "source", "unknown")
    return {
        "status": "FRESHNESS_WITNESS_MATCHED" if verified else "FRESHNESS_UNVERIFIED",
        "source_freshness_verified": verified,
        "observed_at": observed_at.isoformat() if observed_at is not None else None,
        "source": source,
        "fleet_count": len(payload) if isinstance(payload, (tuple, list)) else None,
        "observer_status": observer_status,
        "game_actions_enabled": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--observer-script",
        type=Path,
        default=DEFAULT_OBSERVER_SCRIPT,
        help="Path to the reviewed passive CDP observer JavaScript file.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=130.0,
        help="Maximum child-process wait; the observer itself waits up to 120 seconds.",
    )
    parser.add_argument(
        "--max-age-seconds",
        type=float,
        default=30.0,
        help="Maximum age for both response receipt and body capture timestamps.",
    )
    args = parser.parse_args(argv)
    if args.max_age_seconds < 0:
        parser.error("--max-age-seconds must not be negative")

    observer = PassivePortResponseObserver(
        args.observer_script,
        timeout_seconds=args.timeout_seconds,
    )
    reader = PoiExpeditionReader(
        port_snapshot_provider=observer.capture,
        freshness_max_age=timedelta(seconds=args.max_age_seconds),
    )
    try:
        state = reader.read_fleets()
    except Exception as exc:
        print(json.dumps({
            "status": "READ_FAILED",
            "error_type": type(exc).__name__,
            "observer_status": observer.last_status,
            "game_actions_enabled": False,
        }, ensure_ascii=False))
        return 1

    result = summarize_state(state, observer.last_status)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["source_freshness_verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
