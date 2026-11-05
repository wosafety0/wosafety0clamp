"""Real-client R3-I PASS gate verification (authorised navigation + resupply, NO dispatch).

This is the authorised follow-up to ``verify_r3i_live_gate.py``. The earlier
probe was strictly read-only and therefore could only ever observe UNKNOWN on a
result/animation screen: no natural ``/kcsapi/api_port/port`` response occurs
while the client sits on a reward screen, and an under-supplied fleet cannot
pass the supply check.

With explicit user authorisation this tool performs the two side-effecting
steps that are strictly required to make a real PASS possible, and nothing
more:

1. Advance any leftover reward screens and return to Home Port.
2. Run the EXISTING production resupply tool (``full_supply_ui_action.run``)
   with its unchanged default target (Fleet 3). Composition is never changed.
3. Arm the passive port observer IN THIS PROCESS, then perform the already
   reviewed visible UI refresh navigation (HOME_PORT -> SORTIE_MENU ->
   EXPEDITION_LIST -> HOME_PORT) so a natural, post-resupply port response is
   captured. The freshness witness is never fabricated and never read from disk.
4. Run the same production read/evaluate chain as a real dispatch:
   ``PoiExpeditionReader.read_fleets()`` -> ``ObservedState`` carrying
   equipment/stat evidence on one baseline -> ``normalize_observed_fleets()``
   -> ``ExpeditionRequirementChecker.evaluate()``.

It STOPS at the verdict. It never calls ``dispatch_one_ready()``, never calls
``prepare_dispatch()``, never writes a dispatch intent, never clicks a
dispatch/ expedition-start control, and never constructs or replays a game API
request. UI navigation naturally causes the *game* to issue its own requests;
those are only observed, never authored by this tool.

The audit payload therefore distinguishes ``game_input_sent`` (true, bounded,
authorised) from ``dispatch_attempted`` (false) and
``game_api_requests_authored_by_tool`` (zero).
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from kancollebot.adapters.poi.background_port_snapshot_provider import (  # noqa: E402
    BackgroundPortSnapshotProvider,
)
from kancollebot.adapters.poi.cdp_websocket import connect_game_webview  # noqa: E402
from kancollebot.adapters.poi.expedition_reader import PoiExpeditionReader  # noqa: E402
from kancollebot.adapters.poi.mcp_client import PoiMcpClient  # noqa: E402
from kancollebot.adapters.poi.port_response_observer import (  # noqa: E402
    PassivePortResponseObserver,
)
from kancollebot.adapters.poi.ui_state_gate import UiState, UiStateGateError  # noqa: E402
from kancollebot.adapters.poi.visible_ui_refresh import (  # noqa: E402
    VisibleUiPortRefreshDriver,
)
from kancollebot.expeditions.continuous_dispatch_controller import (  # noqa: E402
    UiStateEvidence,
)
from kancollebot.expeditions.continuous_scheduler import UiOperationArbiter  # noqa: E402
from kancollebot.expeditions.expedition_requirement_checker import (  # noqa: E402
    ExpeditionRequirementChecker,
    normalize_observed_fleets,
)

import full_supply_ui_action as supply  # noqa: E402
from one_expedition_ui_loop import (  # noqa: E402
    HOME_PORT,
    RETURN_CONFIRM,
    LoopError,
    _require_ui_state,
    _reward_screen_on_any_layer,
)

OBSERVER_SCRIPT = ROOT / "tools" / "hand_sandbox" / "poi_port_response_observer_20261029.js"


class CountingClicks:
    """Proxy the reviewed CDP input so every visible click is auditable."""

    def __init__(self, inner: Any, sink: list[str]) -> None:
        self._inner = inner
        self._sink = sink

    def click(self, point: Any) -> Any:
        self._sink.append(getattr(point, "label", str(point)))
        return self._inner.click(point)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _install_settled_refresh_driver(settle_seconds: float) -> None:
    """Give the production refresh driver a longer post-click proof window.

    ``full_supply_ui_action._acquire_fresh_witness`` constructs the driver with
    ``settle_seconds=1.0``. Immediately after a reward chain the port fade can
    outlast that window, so the driver's final HOME_PORT re-proof fails even
    though the client did reach Home Port (live 2026-11-05). Only the wait is
    widened: the same three clicks, the same six-sample verifier, the same
    no-ambiguous-retry rule and all supply actions stay in production code.
    This is a scoped override inside this verification process, not a change to
    the production tool.
    """
    base = supply.VisibleUiPortRefreshDriver

    class SettledRefreshDriver(base):  # type: ignore[valid-type,misc]
        def __init__(self, **kwargs: Any) -> None:
            kwargs["settle_seconds"] = settle_seconds
            super().__init__(**kwargs)

    supply.VisibleUiPortRefreshDriver = SettledRefreshDriver


def _sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _home_port_clicks(session: Any, clicks: list[str]) -> int:
    """Bounded return to Home Port; only reward screens are advanced by click."""
    sent = 0
    for attempt in range(1, 5):
        try:
            _require_ui_state(session, UiState.HOME_PORT, visual_attempts=6)
            return sent
        except (UiStateGateError, LoopError):
            pass
        reward = _reward_screen_on_any_layer(session)
        point = RETURN_CONFIRM if reward is not None else HOME_PORT
        session.input.click(point)
        clicks.append(f"{getattr(point, 'label', str(point))}@reward={reward}")
        sent += 1
        time.sleep(1.5 if reward is not None else 2.0)
    raise RuntimeError("HOME_PORT was not reached after 4 bounded navigation clicks")


# Only events that report a click ALREADY SENT are counted. ``*_verified``
# events carry a *planned* click_count, and ``*_completed`` events carry an
# aggregate ui_click_count; counting either would double-count or invent clicks.
_SUPPLY_CLICK_EVENT_FIELDS: dict[str, str] = {
    "home_port_navigation_click_sent": "click_count",
    "supply_menu_navigation_click_sent": "click_count",
    "supply_fleet_tab_selected_for_global_button_visibility": "click_count",
    "fleet_wide_supply_click_sent": "click_count",
}


def _counted_int(value: Any) -> bool:
    """bool is an int subclass; a flag is never a click count."""
    return isinstance(value, int) and not isinstance(value, bool)


def count_supply_clicks(events: list[dict[str, Any]]) -> int:
    """Recompute clicks ACTUALLY SENT from append-only resupply audit events."""
    clicks = 0
    for event in events:
        field = _SUPPLY_CLICK_EVENT_FIELDS.get(event.get("event", ""))
        if field is not None and _counted_int(event.get(field)):
            clicks += event[field]
    # The refresh driver reports a running total in ``clicks_sent`` rather than
    # one event per click; take its maximum for this run.
    refresh_totals = [
        event["clicks_sent"]
        for event in events
        if event.get("event") == "ui_refresh_click_sent"
        and _counted_int(event.get("clicks_sent"))
    ]
    clicks += max(refresh_totals, default=0)
    return clicks


def _supply_log_tail(start_line: int) -> tuple[list[dict[str, Any]], int]:
    """Read the resupply tool's append-only audit events from this run only."""
    path = supply.LOG_PATH
    if not path.is_file():
        return [], 0
    lines = path.read_text(encoding="utf-8").splitlines()[start_line:]
    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events, count_supply_clicks(events)


def _fleet_evidence(record: Any) -> dict[str, Any]:
    return {
        "fleet_id": record.fleet_id,
        "ship_ids": list(record.ship_ids),
        "ship_levels": list(record.ship_levels),
        "ship_types": list(record.ship_types),
        "mission_status": record.mission_status,
        "mission_id": record.mission_id,
        "fuel_ready": record.fuel_ready,
        "ammo_ready": record.ammo_ready,
        "equipment_by_ship": (
            {str(k): list(v) for k, v in sorted(record.equipment_by_ship.items())}
            if record.equipment_by_ship is not None else None
        ),
        "total_stats": (
            dict(record.total_stats) if record.total_stats is not None else None
        ),
        "in_dock_ship_ids": list(record.in_dock_ship_ids),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fleet-id", type=int, default=3)
    parser.add_argument("--mission-id", type=int, default=1)
    parser.add_argument("--max-state-age-seconds", type=float, default=30.0)
    parser.add_argument("--witness-wait-seconds", type=float, default=30.0)
    parser.add_argument("--observer-timeout-seconds", type=float, default=90.0)
    parser.add_argument("--poi-endpoint", default="http://127.0.0.1:12450/mcp")
    parser.add_argument(
        "--refresh-settle-seconds", type=float, default=3.0,
        help="Wait after each visible navigation click before re-proving the "
             "screen. Live 2026-11-05 the port fade outlasted the proof window; "
             "1.0s is not enough right after a reward chain.",
    )
    parser.add_argument("--settle-before-supply-seconds", type=float, default=3.0)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    clicks: list[str] = []
    refresh_events: list[dict[str, Any]] = []
    started = time.monotonic()
    supply_log_start = (
        len(supply.LOG_PATH.read_text(encoding="utf-8").splitlines())
        if supply.LOG_PATH.is_file() else 0
    )

    # --- Phase A: leave any leftover reward screen, reach HOME_PORT. ---
    with connect_game_webview(timeout=3.0) as session:
        settle_clicks = _home_port_clicks(session, clicks)
    settle_trace = {"home_port_navigation_clicks": settle_clicks}

    # --- Phase B: production resupply tool, unchanged defaults (Fleet 3). ---
    time.sleep(args.settle_before_supply_seconds)
    _install_settled_refresh_driver(args.refresh_settle_seconds)
    supply_stdout = io.StringIO()
    with contextlib.redirect_stdout(supply_stdout):
        supply_rc = supply.run(dry_run=False)
    if supply_rc != 0:
        raise RuntimeError(f"production resupply tool returned {supply_rc}")
    supply_events, supply_clicks = _supply_log_tail(supply_log_start)
    supply_finished_at = datetime.now(timezone.utc)

    # --- Phase C: post-resupply witness, in this process, then the gate. ---
    provider = BackgroundPortSnapshotProvider(
        PassivePortResponseObserver(
            OBSERVER_SCRIPT, timeout_seconds=args.observer_timeout_seconds
        )
    )
    provider()
    time.sleep(1.0)  # let the passive observer attach before any navigation

    arbiter = UiOperationArbiter()
    with connect_game_webview(timeout=3.0) as session:
        def verify_state(expected: str) -> UiStateEvidence:
            _require_ui_state(
                session, UiState(expected), visual_attempts=6,
                from_surface=expected != "HOME_PORT_RETURN_NOTICE",
            )
            return UiStateEvidence(
                expected, (expected, expected),
                "two_consecutive_in_memory_ooi_fingerprints",
            )

        # Resupply leaves the client on the supply page; return to the port first.
        home_clicks = _home_port_clicks(session, clicks)

        driver = VisibleUiPortRefreshDriver(
            game_input=CountingClicks(session.input, clicks),
            verify_state=verify_state,
            ui_arbiter=arbiter,
            event_sink=lambda event, payload: refresh_events.append(
                {"event": event, **dict(payload)}
            ),
            settle_seconds=args.refresh_settle_seconds,
        )
        driver.refresh_port_state(
            refresh_id="r3i-live-pass-gate",
            origin="verify_r3i_live_pass_gate",
        )

        deadline = time.monotonic() + args.witness_wait_seconds
        witness: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            candidate = provider.latest_snapshot
            received = _timestamp(
                (candidate or {}).get("source", {}).get("responseReceivedAt")
            )
            if candidate is not None and received is not None and received >= supply_finished_at:
                witness = candidate
                break
            time.sleep(0.25)

        if witness is None:
            raise RuntimeError(
                "no post-resupply natural api_port/port witness was captured in this process"
            )

        reader = PoiExpeditionReader(
            PoiMcpClient(args.poi_endpoint),
            port_snapshot_provider=provider,
            freshness_max_age=timedelta(seconds=args.max_state_age_seconds),
        )
        observed = reader.read_fleets()
        now = datetime.now(timezone.utc)
        checker = ExpeditionRequirementChecker(
            clock=lambda: now, minimum_confidence="cross_checked"
        )
        normalized = normalize_observed_fleets(
            observed, args.fleet_id,
            now=now,
            max_state_age=timedelta(seconds=args.max_state_age_seconds),
        )
        verdict = checker.evaluate(
            normalized, args.mission_id, fleet_id=args.fleet_id
        )

    snapshot = next(
        (record for record in observed.payload if record.fleet_id == args.fleet_id),
        None,
    )
    if snapshot is None:
        raise RuntimeError(f"no observed record for fleet {args.fleet_id}")

    # The SAME two assertions the production controller makes before it ever
    # writes an intent or clicks (continuous_dispatch_controller.py). Reproduced
    # here on live data because dispatch_one_ready() must not be called.
    binding = {
        "baseline_observed_at": observed.observed_at.isoformat(),
        "baseline_source": observed.source,
        "baseline_source_freshness_verified": observed.source_freshness_verified,
        "baseline_ship_ids": list(snapshot.ship_ids),
        "normalized_snapshot_source": normalized.snapshot_source,
        "normalized_ship_ids": list(normalized.ship_ids),
        "source_matches": normalized.snapshot_source == observed.source,
        "ship_ids_match": normalized.ship_ids == snapshot.ship_ids,
        "equipment_evidence_present": normalized.equipment_by_ship is not None,
        "fleet_stats_evidence_present": normalized.total_stats is not None,
        "state_trusted": normalized.state_trusted,
        "state_untrusted_reasons": list(normalized.state_untrusted_reasons),
    }
    binding["same_fresh_baseline"] = (
        binding["source_matches"]
        and binding["ship_ids_match"]
        and binding["baseline_source_freshness_verified"]
        and binding["state_trusted"]
    )

    fleets = [_fleet_evidence(record) for record in observed.payload]
    evidence = {
        "kind": "r3i_live_pass_gate_verification",
        "tool": "tools/verify_r3i_live_pass_gate.py",
        "captured_at_utc": now.isoformat(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "fleet_id": args.fleet_id,
        "mission_id": args.mission_id,
        "max_state_age_seconds": args.max_state_age_seconds,
        "phases": {
            "A_settle_to_home_port": settle_trace,
            "B_production_resupply": {
                "tool": "tools/full_supply_ui_action.py (run, default target Fleet 3)",
                "return_code": supply_rc,
                "finished_at_utc": supply_finished_at.isoformat(),
                "audit_events": supply_events,
                "audit_click_count": supply_clicks,
                "stdout_line_count": len(supply_stdout.getvalue().splitlines()),
                "process_local_refresh_settle_seconds": args.refresh_settle_seconds,
                "settle_before_supply_seconds": args.settle_before_supply_seconds,
                "production_files_modified": False,
            },
            "C_post_resupply_refresh": {
                "home_port_return_clicks": home_clicks,
                "refresh_stats": driver.last_refresh_stats,
                "refresh_events": refresh_events,
                "passive_observer_last_status": provider.last_status,
                "witness_captured_in_this_process": True,
                "witness_source": witness.get("source"),
            },
        },
        "observation": {
            "observed_at": observed.observed_at.isoformat(),
            "source": observed.source,
            "source_freshness_verified": observed.source_freshness_verified,
        },
        "same_baseline_binding": binding,
        "fleets": fleets,
        "evidence_digest_sha256": _sha256(fleets),
        "verdict": {
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
        },
        "safety": {
            "authorised_ui_clicks_sent": len(clicks) + supply_clicks,
            "click_breakdown": {
                "phase_A_settle": settle_clicks,
                "phase_B_resupply_and_its_refresh": supply_clicks,
                "phase_C_post_resupply_refresh": len(clicks) - settle_clicks,
            },
            "navigation_click_labels": clicks,
            "game_api_requests_authored_by_tool": 0,
            "game_api_requests_replayed_or_constructed": 0,
            "dispatch_attempted": False,
            "dispatch_intent_written": False,
            "fleet_composition_changes_proposed": False,
            "runtime_evaluate_used": False,
            "files_written_by_probe": ["evidence JSON at --out"],
        },
    }

    text = json.dumps(evidence, ensure_ascii=False, indent=2)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    provider.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
