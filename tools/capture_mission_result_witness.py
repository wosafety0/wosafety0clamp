"""Capture a real expedition-result exchange and record it durably.

This is the live counterpart of the mission-result observer: it arms the
passive observer, waits for the exchange the game client makes on its own,
and writes what it saw into the append-only event log so a later process can
rebuild it.

It sends nothing. It clicks nothing. The observer only reads a request and a
response the client has already produced.

Usage:
    PYTHONPATH=src python tools/capture_mission_result_witness.py \\
        --database logs/one_expedition_production.sqlite3
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from kancollebot.adapters.poi.mission_result_observer import (  # noqa: E402
    PassiveMissionResultObserver,
)
from kancollebot.expeditions.continuous_dispatch_controller import (  # noqa: E402
    SqliteDispatchEventLog,
)
from kancollebot.expeditions.mission_result_witness import (  # noqa: E402
    MISSION_RESULT_EVENT,
    MissionResultWitness,
    parse_witness,
    witness_fields,
)
from kancollebot.runtime_config import RUNTIME_CONFIG  # noqa: E402

DEFAULT_OBSERVER = (
    ROOT / "tools" / "hand_sandbox" / "poi_mission_result_observer_20261105.js"
)


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture one natural expedition-result exchange."
    )
    parser.add_argument("--database", default=str(RUNTIME_CONFIG.monitor_database))
    parser.add_argument("--observer-script", default=str(DEFAULT_OBSERVER))
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument(
        "--record",
        action="store_true",
        help="write the witness into the durable event log.",
    )
    args = parser.parse_args()

    observer = PassiveMissionResultObserver(
        args.observer_script, timeout_seconds=args.timeout_seconds
    )
    _emit({
        "event": "mission_result_witness_armed",
        "observer_status": observer.last_status,
        "timeout_seconds": args.timeout_seconds,
        "game_api_requests": False,
        "game_input_sent": False,
    })

    witness = observer.capture()
    _emit({
        "event": "mission_result_observer_finished",
        "observer_status": observer.last_status,
        "observer_detail": observer.last_detail,
        "captured": witness is not None,
    })
    if witness is None:
        return 1

    data = witness["data"]
    source = witness.get("source", {})
    raw_receipt = data.get("apiRewardReceipt")
    reward_receipt = None
    if isinstance(raw_receipt, dict):
        raw_items = raw_receipt.get("items")
        items = []
        if isinstance(raw_items, list):
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                items.append({
                    "slot": item.get("slot"),
                    "useitem_id": item.get("useitemId"),
                    "count": item.get("useitemCount"),
                })
        reward_receipt = {
            "status": raw_receipt.get("status"),
            "material": raw_receipt.get("material"),
            "useitem_flags": raw_receipt.get("useitemFlags"),
            "items": items,
        }
    parsed = parse_witness({
        "fleet_id": data["apiDeckId"],
        "quest_name": data["apiQuestName"],
        "clear_result": data["apiClearResult"],
        "ship_ids": data["apiShipId"],
        "reward_receipt": reward_receipt,
        "requested_at": source.get("requestedAt"),
        "response_received_at": source.get("responseReceivedAt"),
    })
    _emit({
        "event": MISSION_RESULT_EVENT,
        "fleet_id": parsed.fleet_id,
        "quest_name": parsed.quest_name,
        "clear_result": parsed.clear_result,
        "ship_ids": list(parsed.ship_ids),
        "reward_receipt_status": (
            parsed.reward_receipt.status if parsed.reward_receipt is not None else None
        ),
        "requested_at": source.get("requestedAt"),
        "response_received_at": source.get("responseReceivedAt"),
        "game_api_requests": False,
        "game_input_sent": False,
    })

    if args.record:
        event_log = SqliteDispatchEventLog(args.database)
        event_log(MISSION_RESULT_EVENT, witness_fields(parsed))
        _emit({
            "event": "mission_result_witness_recorded",
            "database": args.database,
            "fleet_id": parsed.fleet_id,
        })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
