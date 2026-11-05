"""Read-only, real-client verification of the R3-I expedition requirement gate.

This tool is deliberately SIDE-EFFECT FREE with respect to the game:

* it only calls the loopback Poi MCP read tools (``poi_get`` / ``poi_lookup`` /
  ``poi_describe``) and, optionally, the passive CDP Network observer;
* it never sends game input, never constructs or replays a game API request,
  never clicks, never dispatches and never writes to the game/database;
* the only file it writes is the evidence JSON named by ``--out``.

It runs the SAME production path as a real dispatch:
``PoiExpeditionReader.read_fleets()`` -> ``ObservedState`` carrying equipment /
stat evidence on each fleet record -> ``normalize_observed_fleets()`` ->
``ExpeditionRequirementChecker.evaluate()``. Nothing after the verdict is
executed, so a PASS proves the gate would allow dispatch, not that a dispatch
was sent.

Freshness is never fabricated. A fresh baseline requires a natural
``/kcsapi/api_port/port`` response captured IN THIS PROCESS by the passive
observer. If the client is idle on a result/animation screen, no such response
occurs and ``source_freshness_verified`` stays False: the gate must then answer
UNKNOWN. That is a real, useful safety observation, not a failure of the tool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kancollebot.adapters.poi.background_port_snapshot_provider import (  # noqa: E402
    BackgroundPortSnapshotProvider,
)
from kancollebot.adapters.poi.expedition_reader import PoiExpeditionReader  # noqa: E402
from kancollebot.adapters.poi.mcp_client import PoiMcpClient  # noqa: E402
from kancollebot.adapters.poi.port_response_observer import (  # noqa: E402
    PassivePortResponseObserver,
)
from kancollebot.expeditions.expedition_requirement_checker import (  # noqa: E402
    ExpeditionRequirementChecker,
    normalize_observed_fleets,
)

DRUM_MASTER_ID = 75
OBSERVER_SCRIPT = (
    Path(__file__).resolve().parent
    / "hand_sandbox"
    / "poi_port_response_observer_20261029.js"
)


def _sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _fleet_evidence(record) -> dict:
    equipment = (
        {str(k): list(v) for k, v in sorted(record.equipment_by_ship.items())}
        if record.equipment_by_ship is not None
        else None
    )
    drums = (
        sum(1 for items in (record.equipment_by_ship or {}).values() for i in items if i == DRUM_MASTER_ID)
        if record.equipment_by_ship is not None
        else None
    )
    carriers = (
        sum(1 for items in (record.equipment_by_ship or {}).values() if any(i == DRUM_MASTER_ID for i in items))
        if record.equipment_by_ship is not None
        else None
    )
    return {
        "fleet_id": record.fleet_id,
        "ship_ids": list(record.ship_ids),
        "ship_levels": list(record.ship_levels),
        "ship_types": list(record.ship_types),
        "mission_status": record.mission_status,
        "mission_id": record.mission_id,
        "fuel_ready": record.fuel_ready,
        "ammo_ready": record.ammo_ready,
        "equipment_by_ship": equipment,
        "total_stats": dict(record.total_stats) if record.total_stats is not None else None,
        "conditions": (
            {str(k): v for k, v in sorted(record.conditions.items())}
            if record.conditions is not None else None
        ),
        "in_dock_ship_ids": list(record.in_dock_ship_ids),
        "drum_count": drums,
        "drum_carriers": carriers,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fleet-id", type=int, default=2)
    parser.add_argument("--mission-id", type=int, default=1)
    parser.add_argument("--max-state-age-seconds", type=float, default=30.0)
    parser.add_argument(
        "--witness-wait-seconds", type=float, default=0.0,
        help="Bound the wait for a NATURAL port response; 0 disables it. "
             "This tool never navigates or clicks to force one.",
    )
    parser.add_argument("--poi-endpoint", default="http://127.0.0.1:12450/mcp")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    provider = None
    if args.witness_wait_seconds > 0:
        provider = BackgroundPortSnapshotProvider(
            PassivePortResponseObserver(
                OBSERVER_SCRIPT,
                timeout_seconds=max(5.0, args.witness_wait_seconds),
            )
        )
        provider()  # start the passive, read-only observer thread

    reader = PoiExpeditionReader(
        PoiMcpClient(args.poi_endpoint),
        port_snapshot_provider=provider,
        freshness_max_age=timedelta(seconds=args.max_state_age_seconds),
    )
    started = time.monotonic()
    if args.witness_wait_seconds > 0:
        deadline = time.monotonic() + args.witness_wait_seconds
        while time.monotonic() < deadline and provider.latest_snapshot is None:
            time.sleep(0.25)

    observed = reader.read_fleets()
    now = datetime.now(timezone.utc)
    checker = ExpeditionRequirementChecker(
        clock=lambda: now, minimum_confidence="cross_checked"
    )

    verdicts = {}
    for mission_id in sorted({args.mission_id, 9999}):
        normalized = normalize_observed_fleets(
            observed, args.fleet_id,
            now=now,
            max_state_age=timedelta(seconds=args.max_state_age_seconds),
        )
        verdict = checker.evaluate(normalized, mission_id, fleet_id=args.fleet_id)
        verdicts[str(mission_id)] = {
            "overall_result": verdict.overall_result.value,
            "allows_dispatch": verdict.allows_dispatch,
            "rule_source": verdict.rule_source,
            "rule_version": verdict.rule_version,
            "rule_confidence": verdict.rule_confidence,
            "passed_checks": list(verdict.passed_checks),
            "failed_checks": list(verdict.failed_checks),
            "unknown_checks": list(verdict.unknown_checks),
            "rejection_reasons": list(verdict.rejection_reasons),
            "checks": [
                {"name": c.name, "status": c.status.value, "detail": c.detail}
                for c in verdict.checks
            ],
            "snapshot_source": normalized.snapshot_source,
            "state_trusted": normalized.state_trusted,
            "state_untrusted_reasons": list(normalized.state_untrusted_reasons),
            "equipment_evidence_present": normalized.equipment_by_ship is not None,
            "fleet_stats_evidence_present": normalized.total_stats is not None,
            "baseline_ship_ids": list(normalized.ship_ids),
        }

    observer_state = {
        "started": provider is not None,
        "wait_seconds": args.witness_wait_seconds,
        "thread_alive_at_capture": provider.is_running if provider else False,
        "last_status": provider.last_status if provider else "disabled",
        "natural_port_response_captured": (
            provider.latest_snapshot is not None if provider else False
        ),
    }
    evidence = {
        "kind": "r3i_live_readonly_gate_verification",
        "tool": "tools/verify_r3i_live_gate.py",
        "captured_at_utc": now.isoformat(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "poi_endpoint": args.poi_endpoint,
        "fleet_id": args.fleet_id,
        "requested_mission_id": args.mission_id,
        "max_state_age_seconds": args.max_state_age_seconds,
        "witness_wait_seconds": args.witness_wait_seconds,
        "observation": {
            "observed_at": observed.observed_at.isoformat(),
            "source": observed.source,
            "source_freshness_verified": observed.source_freshness_verified,
        },
        "passive_observer": observer_state,
        "fleets": [_fleet_evidence(record) for record in observed.payload],
        "evidence_digest_sha256": _sha256(
            [_fleet_evidence(record) for record in observed.payload]
        ),
        "verdicts": verdicts,
        "safety": {
            "game_clicks_sent": 0,
            "game_api_requests_sent": 0,
            "game_input_sent": False,
            "dispatch_attempted": False,
            "fleet_composition_changes_proposed": False,
            "ship_state_changes_proposed": False,
            "runtime_evaluate_used": False,
            "files_written_by_probe": ["evidence JSON at --out"],
        },
    }
    text = json.dumps(evidence, ensure_ascii=False, indent=2)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    if provider is not None:
        provider.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
