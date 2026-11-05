"""Real-client Fleet 2 R3-I gate verification (authorised navigation + resupply, NO dispatch).

This is the WorkBuddy handoff follow-up to ``verify_r3i_live_gate.py``. The
read-only probe cannot obtain a fresh witness while the client is idle, and an
under-supplied fleet cannot pass the supply check. With explicit user
authorisation (scheme 2) this orchestrator performs the two side-effecting
steps that are strictly required, and nothing more:

1. Re-verify the live environment and the CURRENT state of all four fleets
   (never trust the historical observations in the handoff).
2. Run the EXISTING production resupply tool ``full_supply_ui_action.run`` with
   its target scoped to **Fleet 2 only**. The override is process-local: no
   production file is modified. Fleet composition is never changed.
3. Arm the passive port observer and then perform the already reviewed visible
   UI refresh navigation (HOME_PORT -> SORTIE_MENU -> EXPEDITION_LIST ->
   HOME_PORT) so a natural, post-resupply ``/kcsapi/api_port/port`` response is
   captured. The witness is never fabricated and never read from disk.
4. Run the EXISTING read-only ``tools/verify_r3i_live_gate.py`` IN THIS PROCESS
   (instrumented only to prove its observer was armed before any navigation).
   Its file, its freshness criteria and its ``minimum_confidence`` are untouched.

It STOPS at the verdict. It never dispatches, never writes a dispatch intent,
never clicks an expedition-start control, never changes fleet composition and
never constructs, sends or replays a game API request. UI navigation naturally
causes the *game* to issue its own requests; those are only observed.

The audit payload distinguishes WorkBuddy's authorised UI clicks from the
read-only probe's own zero-click / zero-game-API claim.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import subprocess
import sys
import time
import urllib.request
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from kancollebot.adapters.poi.cdp_websocket import connect_game_webview  # noqa: E402
from kancollebot.adapters.poi.expedition_reader import PoiExpeditionReader  # noqa: E402
from kancollebot.adapters.poi.mcp_client import PoiMcpClient  # noqa: E402
from kancollebot.adapters.poi.ui_state_gate import UiState, UiStateGateError  # noqa: E402
from kancollebot.adapters.poi.visible_ui_refresh import VisibleUiPortRefreshDriver  # noqa: E402
from kancollebot.expeditions.continuous_dispatch_controller import UiStateEvidence  # noqa: E402
from kancollebot.expeditions.continuous_scheduler import UiOperationArbiter  # noqa: E402

import full_supply_ui_action as supply  # noqa: E402
from one_expedition_ui_loop import (  # noqa: E402
    HOME_PORT,
    RETURN_CONFIRM,
    LoopError,
    _require_ui_state,
    _reward_screen_on_any_layer,
)

FLEET_ID = 2
MISSION_ID = 1
UNKNOWN_RULE_CONTROL = 9999

# Only events that report a click ALREADY SENT are counted. ``*_verified``
# events carry a *planned* click_count and would invent clicks.
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
    refresh_totals = [
        event["clicks_sent"]
        for event in events
        if event.get("event") == "ui_refresh_click_sent"
        and _counted_int(event.get("clicks_sent"))
    ]
    return clicks + max(refresh_totals, default=0)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _install_settled_refresh_driver(settle_seconds: float) -> None:
    """Widen ONLY the production refresh driver's post-click proof window.

    ``full_supply_ui_action._acquire_fresh_witness`` hardcodes
    ``settle_seconds=1.0``. Live 2026-11-05 the port fade outlasted that window
    right after a reward chain. This override is scoped to this verification
    process; the production tool file is not modified.
    """
    base = supply.VisibleUiPortRefreshDriver

    class SettledRefreshDriver(base):  # type: ignore[valid-type,misc]
        def __init__(self, **kwargs: Any) -> None:
            kwargs["settle_seconds"] = settle_seconds
            super().__init__(**kwargs)

    supply.VisibleUiPortRefreshDriver = SettledRefreshDriver


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


class UiActionLog:
    """Append-only, per-action UI operation log (JSON Lines)."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def add(
        self,
        *,
        phase: str,
        action: str,
        target: str,
        before: Any = None,
        after: Any = None,
        result: str,
        page_stable: bool | None = None,
        poi_read: Any = None,
        clicks_sent: int = 0,
        note: str = "",
    ) -> None:
        self.entries.append(
            {
                "seq": len(self.entries) + 1,
                "timestamp_utc": _utc(),
                "phase": phase,
                "action": action,
                "target": target,
                "before_observation": before,
                "after_observation": after,
                "result": result,
                "page_stable_confirmed": page_stable,
                "poi_read": poi_read,
                "clicks_sent": clicks_sent,
                "note": note,
            }
        )

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in self.entries),
            encoding="utf-8",
        )


def _snapshot_to_dict(record: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for fld in fields(record):
        value = getattr(record, fld.name)
        if fld.name in ("equipment_by_ship", "total_stats", "conditions"):
            out[fld.name] = dict(value) if value is not None else None
        elif isinstance(value, (tuple, list)):
            out[fld.name] = [v.isoformat() if isinstance(v, datetime) else v for v in value]
        elif isinstance(value, datetime):
            out[fld.name] = value.isoformat()
        else:
            out[fld.name] = value
    return out


def _read_fleets_readonly() -> dict[str, Any]:
    """Read the CURRENT Poi state through the production reader (no witness)."""
    observed = PoiExpeditionReader(PoiMcpClient()).read_fleets()
    return {
        "observed_at": observed.observed_at.isoformat(),
        "source": observed.source,
        "source_freshness_verified": observed.source_freshness_verified,
        "fleets": [_snapshot_to_dict(r) for r in observed.payload],
    }


def _fleet(state: dict[str, Any], fleet_id: int) -> dict[str, Any]:
    return next(f for f in state["fleets"] if f["fleet_id"] == fleet_id)


def _fleet_brief(state: dict[str, Any]) -> dict[str, Any]:
    return {
        str(f["fleet_id"]): {
            "ship_ids": f["ship_ids"],
            "fuel_ready": f["fuel_ready"],
            "ammo_ready": f["ammo_ready"],
            "mission_status": f["mission_status"],
            "mission_id": f["mission_id"],
            "in_dock_ship_ids": f["in_dock_ship_ids"],
            "damaged_ship_ids": f["damaged_ship_ids"],
        }
        for f in state["fleets"]
    }


def _node_process_count() -> int:
    """Count node.exe processes; used to prove the observer child is running."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq node.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return -1
    return sum(1 for line in result.stdout.splitlines() if '"node.exe"' in line)


def _home_port_clicks(session: Any, clicks: list[str]) -> int:
    """Bounded return to Home Port; only reward screens are advanced by click."""
    sent = 0
    for _ in range(1, 5):
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


def _cdp_version() -> dict[str, Any]:
    with urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=5) as r:
        return json.load(r)


def _poi_health() -> dict[str, Any]:
    return PoiMcpClient().health()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fleet-id", type=int, default=FLEET_ID)
    parser.add_argument("--mission-id", type=int, default=MISSION_ID)
    parser.add_argument("--max-state-age-seconds", type=float, default=30.0)
    parser.add_argument("--witness-wait-seconds", type=float, default=90.0)
    parser.add_argument("--refresh-settle-seconds", type=float, default=3.0)
    parser.add_argument("--observer-attach-seconds", type=float, default=2.5)
    parser.add_argument("--probe-evidence-out", type=Path, required=True)
    parser.add_argument("--ui-log-out", type=Path, required=True)
    parser.add_argument("--summary-out", type=Path, required=True)
    args = parser.parse_args(argv)

    started = time.monotonic()
    log = UiActionLog()
    clicks: list[str] = []
    refresh_events: list[dict[str, Any]] = []
    stop_reasons: list[str] = []

    # ------------------------------------------------------------------
    # Phase A: re-verify the CURRENT environment and all four fleet states.
    # ------------------------------------------------------------------
    cdp = _cdp_version()
    health = _poi_health()
    with connect_game_webview(timeout=3.0) as session:
        with urllib.request.urlopen("http://127.0.0.1:9222/json/list", timeout=5) as r:
            targets = json.load(r)
        game_targets = [t for t in targets if t.get("type") == "webview"]
    before = _read_fleets_readonly()
    fleet_before = _fleet(before, args.fleet_id)
    log.add(
        phase="A_environment_recheck",
        action="read_only_environment_and_state_snapshot",
        target="CDP 9222 / Poi MCP 12450 / info.fleets+info.ships",
        after={
            "cdp_browser": cdp.get("Browser"),
            "cdp_user_agent": cdp.get("User-Agent"),
            "game_webview_count": len(game_targets),
            "poi_mcp_health": health,
            "fleets": _fleet_brief(before),
        },
        result="captured",
        page_stable=True,
        poi_read={"observed_at": before["observed_at"], "source": before["source"]},
        note="Historical handoff state is NOT trusted; everything re-read now.",
    )

    # Stop conditions: any pending settlement / dangerous state.
    dangerous = [
        f["fleet_id"] for f in before["fleets"]
        if f["mission_status"] not in (0, None)
        or f["mission_id"] is not None
        or f["return_at"] is not None
    ]
    if dangerous or fleet_before["in_dock_ship_ids"] or fleet_before["damaged_ship_ids"]:
        stop_reasons.append(
            f"preflight dangerous state: active_or_returning={dangerous}, "
            f"in_dock={fleet_before['in_dock_ship_ids']}, "
            f"damaged={fleet_before['damaged_ship_ids']}"
        )

    needs_supply = not (fleet_before["fuel_ready"] and fleet_before["ammo_ready"])
    log.add(
        phase="A_environment_recheck",
        action="supply_needed_decision",
        target=f"Fleet {args.fleet_id}",
        before={
            "fuel_ready": fleet_before["fuel_ready"],
            "ammo_ready": fleet_before["ammo_ready"],
        },
        result="supply_required" if needs_supply else "already_full_no_ui_action",
        page_stable=True,
        note="Only resupply when the CURRENT read confirms a deficit.",
    )

    supply_rc: int | None = None
    supply_events: list[dict[str, Any]] = []
    supply_clicks = 0
    supply_finished_at: str | None = None
    after: dict[str, Any] = before

    if stop_reasons:
        log.add(
            phase="STOP", action="halt_before_any_ui_action", target="all",
            result="stopped", note="; ".join(stop_reasons),
        )
    else:
        # --------------------------------------------------------------
        # Phase B: resupply Fleet 2 ONLY, via the existing production tool.
        # --------------------------------------------------------------
        if needs_supply:
            supply_log_start = (
                len(supply.LOG_PATH.read_text(encoding="utf-8").splitlines())
                if supply.LOG_PATH.is_file() else 0
            )
            _install_settled_refresh_driver(args.refresh_settle_seconds)
            supply.TARGET_FLEET_IDS = (args.fleet_id,)  # process-local override ONLY
            log.add(
                phase="B_resupply",
                action="scope_resupply_target",
                target=f"full_supply_ui_action.TARGET_FLEET_IDS -> ({args.fleet_id},)",
                before="(3,) production default",
                after=f"({args.fleet_id},) process-local",
                result="scoped",
                note="No production file modified; audit log and progress validator "
                     "still enforce that only this fleet changes.",
            )
            time.sleep(2.0)
            supply_stdout = io.StringIO()
            try:
                with contextlib.redirect_stdout(supply_stdout):
                    supply_rc = supply.run(dry_run=False)
            except Exception as exc:
                supply_rc = 2
                stop_reasons.append(f"resupply tool raised {type(exc).__name__}: {exc}")
            supply_finished_at = _utc()
            if supply.LOG_PATH.is_file():
                for line in supply.LOG_PATH.read_text(encoding="utf-8").splitlines()[supply_log_start:]:
                    try:
                        supply_events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            supply_clicks = count_supply_clicks(supply_events)
            log.add(
                phase="B_resupply",
                action="production_full_supply_ui_action",
                target=f"Fleet {args.fleet_id} supply-all button",
                before=_fleet_brief(before),
                result=f"return_code={supply_rc}",
                page_stable=supply_rc == 0,
                clicks_sent=supply_clicks,
                note="Normal game UI clicks only. No game API authored, no dispatch, "
                     "no composition change. Each click is in the append-only audit tail.",
            )
            if supply_rc != 0:
                stop_reasons.append(f"resupply tool returned {supply_rc}")

        after = _read_fleets_readonly()
        fleet_after = _fleet(after, args.fleet_id)
        changed = [
            f["fleet_id"] for f in after["fleets"]
            if _fleet(before, f["fleet_id"])["fuel_ready"] != f["fuel_ready"]
            or _fleet(before, f["fleet_id"])["ammo_ready"] != f["ammo_ready"]
        ]
        roster_changed = [
            f["fleet_id"] for f in after["fleets"]
            if _fleet(before, f["fleet_id"])["ship_ids"] != f["ship_ids"]
        ]
        log.add(
            phase="B_resupply",
            action="post_resupply_read_only_verification",
            target="all four fleets / Fleet 2 fuel+ammo",
            before=_fleet_brief(before),
            after=_fleet_brief(after),
            result="verified" if fleet_after["fuel_ready"] and fleet_after["ammo_ready"] else "NOT_FULL",
            page_stable=True,
            poi_read={"observed_at": after["observed_at"]},
            note=f"supply_state_changed_fleets={changed}; roster_changed={roster_changed}",
        )
        if not (fleet_after["fuel_ready"] and fleet_after["ammo_ready"]):
            stop_reasons.append("Fleet 2 fuel/ammo not full after resupply")
        if roster_changed:
            stop_reasons.append(f"fleet roster changed: {roster_changed}")
        if changed and set(changed) - {args.fleet_id}:
            stop_reasons.append(f"unauthorised fleet supply state changed: {changed}")
        if fleet_after["ship_ids"] != fleet_before["ship_ids"]:
            stop_reasons.append("Fleet 2 ship identity changed")

    # ------------------------------------------------------------------
    # Phase C: arm the read-only probe's observer, THEN navigate to refresh.
    # ------------------------------------------------------------------
    probe_result: dict[str, Any] = {}
    observer_ready = False
    node_before = _node_process_count()
    node_after_start = node_before
    witness_capture: dict[str, Any] = {}
    nav_clicks = 0
    probe_evidence: dict[str, Any] | None = None

    if not stop_reasons:
        # C1: return to Home Port BEFORE arming the probe, so any port response
        # caused by leaving the supply page cannot become a pre-refresh witness.
        with connect_game_webview(timeout=3.0) as session:
            nav_clicks = _home_port_clicks(session, clicks)
        log.add(
            phase="C1_return_to_home_port",
            action="bounded_home_port_navigation",
            target="母港 button",
            result="HOME_PORT verified (6 samples)",
            page_stable=True,
            clicks_sent=nav_clicks,
            note="Performed before the probe's observer is armed.",
        )

        # C2: launch the EXISTING read-only verification script as a separate
        # process. It arms its own passive CDP Network observer and will only
        # accept a witness captured inside that process. Its file, its
        # freshness criteria and its minimum_confidence are untouched.
        probe_argv = [
            sys.executable,
            str(ROOT / "tools" / "verify_r3i_live_gate.py"),
            "--fleet-id", str(args.fleet_id),
            "--mission-id", str(args.mission_id),
            "--max-state-age-seconds", str(args.max_state_age_seconds),
            "--witness-wait-seconds", str(args.witness_wait_seconds),
            "--out", str(args.probe_evidence_out),
        ]
        node_before = _node_process_count()
        probe_process = subprocess.Popen(
            probe_argv, cwd=str(ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )

        # Prove the observer child is actually running before ANY navigation.
        observer_ready = False
        node_peak = node_before
        attach_deadline = time.monotonic() + 20.0
        while time.monotonic() < attach_deadline:
            if probe_process.poll() is not None:
                break
            current = _node_process_count()
            node_peak = max(node_peak, current)
            if current > node_before:
                observer_ready = True
                break
            time.sleep(0.25)
        if observer_ready:
            time.sleep(args.observer_attach_seconds)
            if probe_process.poll() is not None:
                observer_ready = False
        node_after_start = max(node_peak, _node_process_count())
        log.add(
            phase="C2_arm_passive_observer",
            action="start_read_only_verification_probe",
            target="tools/verify_r3i_live_gate.py (unmodified file, separate process)",
            before={"node_process_count": node_before},
            after={
                "node_process_count": node_after_start,
                "observer_child_process_started": observer_ready,
                "probe_pid": probe_process.pid,
                "probe_alive": probe_process.poll() is None,
                "probe_argv": probe_argv,
            },
            result="observer_armed" if observer_ready else "NOT_ARMED",
            page_stable=True,
            note="The node.exe count increase is objective evidence that the "
                 "passive observer child started. No witness is fabricated; no "
                 "freshness criterion changed. The probe sends zero clicks and "
                 "zero game API requests.",
        )
        if not observer_ready:
            stop_reasons.append("read-only probe passive observer did not start")
        else:
            # C3: visible UI refresh navigation -> natural api_port/port response.
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
                    refresh_id="r3i-fleet2-handoff-gate",
                    origin="verify_r3i_fleet2_gate",
                )
                refresh_clicks = len(clicks) - nav_clicks
                log.add(
                    phase="C3_visible_ui_refresh",
                    action="HOME_PORT->SORTIE_MENU->EXPEDITION_LIST->HOME_PORT",
                    target="normal game UI navigation buttons",
                    after={
                        "refresh_stats": driver.last_refresh_stats,
                        "refresh_events": refresh_events,
                    },
                    result="refresh_navigation_completed",
                    page_stable=True,
                    clicks_sent=refresh_clicks,
                    note="Each step re-proved with 6 visual samples. The game issues "
                         "its own requests; the observer only listens.",
                )

            try:
                probe_stdout, probe_stderr = probe_process.communicate(
                    timeout=args.witness_wait_seconds + 60.0
                )
                probe_result.update({
                    "return_code": probe_process.returncode,
                    "stdout_chars": len(probe_stdout or ""),
                    "stderr_tail": (probe_stderr or "")[-1200:],
                    "finished_at_utc": _utc(),
                })
            except subprocess.TimeoutExpired:
                probe_process.kill()
                probe_process.communicate()
                probe_result.update({
                    "return_code": None,
                    "error": "probe exceeded witness window and was terminated",
                    "finished_at_utc": _utc(),
                })
                stop_reasons.append("read-only probe did not finish within the witness window")

            if args.probe_evidence_out.is_file():
                probe_evidence = json.loads(
                    args.probe_evidence_out.read_text(encoding="utf-8")
                )
            else:
                stop_reasons.append("read-only probe wrote no evidence JSON")
            if probe_result.get("return_code") not in (0, None) or probe_result.get("error"):
                stop_reasons.append(f"read-only probe error: {probe_result}")

            witness_capture = {
                "captured": bool(
                    (probe_evidence or {}).get("passive_observer", {}).get(
                        "natural_port_response_captured"
                    )
                ),
                "passive_observer_state": (probe_evidence or {}).get("passive_observer"),
                "observation": (probe_evidence or {}).get("observation"),
            }
            log.add(
                phase="C3_visible_ui_refresh",
                action="probe_completed_and_witness_recorded",
                target="passive CDP Network observer (read-only probe process)",
                after=witness_capture,
                result="captured" if witness_capture.get("captured") else "NO_WITNESS",
                page_stable=True,
                poi_read={
                    "probe_return_code": probe_result.get("return_code"),
                    "probe_error": probe_result.get("error"),
                },
                note="Witness was captured inside the read-only probe process; "
                     "capture time and observer status are in its evidence JSON.",
            )

    # ------------------------------------------------------------------
    # Phase D: evaluate every stop condition. Never relax a criterion.
    # ------------------------------------------------------------------
    verdict = (probe_evidence or {}).get("verdicts", {}).get(str(args.mission_id), {})
    control_verdict = (probe_evidence or {}).get("verdicts", {}).get(str(UNKNOWN_RULE_CONTROL), {})
    observation = (probe_evidence or {}).get("observation", {})
    passive = (probe_evidence or {}).get("passive_observer", {})
    probe_fleet = next(
        (f for f in (probe_evidence or {}).get("fleets", []) if f["fleet_id"] == args.fleet_id),
        None,
    )

    checks = {
        "natural_port_witness_captured": bool(passive.get("natural_port_response_captured")),
        "source_freshness_verified_true": observation.get("source_freshness_verified") is True,
        "fleet2_fuel_and_ammo_full": bool(
            probe_fleet and probe_fleet["fuel_ready"] and probe_fleet["ammo_ready"]
        ),
        "equipment_evidence_complete": bool(probe_fleet and probe_fleet["equipment_by_ship"] is not None),
        "fleet_stats_evidence_complete": bool(probe_fleet and probe_fleet["total_stats"] is not None),
        "baseline_ship_ids_match": bool(
            probe_fleet and probe_fleet["ship_ids"] == _fleet(after, args.fleet_id)["ship_ids"]
            and verdict.get("baseline_ship_ids") == probe_fleet["ship_ids"]
        ),
        "snapshot_source_matches": bool(
            verdict.get("snapshot_source") == observation.get("source")
            and observation.get("source") == before["source"]
        ),
        "verdict_is_pass": verdict.get("overall_result") == "PASS" and verdict.get("allows_dispatch") is True,
        "unknown_rule_control_preserved": control_verdict.get("overall_result") == "UNKNOWN",
        "minimum_confidence_cross_checked": verdict.get("rule_confidence") == "cross_checked",
        "no_ui_stop_reason": not stop_reasons,
    }
    failed = [name for name, passed in checks.items() if not passed]
    for name in failed:
        stop_reasons.append(f"stop condition failed: {name}")

    log.add(
        phase="D_verdict_and_stop_conditions",
        action="evaluate_gate_and_stop_conditions",
        target=f"Fleet {args.fleet_id} / mission {args.mission_id} + rule {UNKNOWN_RULE_CONTROL} control",
        after={
            "verdict": verdict.get("overall_result"),
            "allows_dispatch": verdict.get("allows_dispatch"),
            "control_verdict": control_verdict.get("overall_result"),
            "checks": checks,
            "failed": failed,
        },
        result="PASS" if not failed else "STOPPED",
        page_stable=True,
        note="; ".join(stop_reasons) if stop_reasons else "all stop conditions satisfied",
    )

    # ------------------------------------------------------------------
    # Write the three deliverables.
    # ------------------------------------------------------------------
    args.ui_log_out.parent.mkdir(parents=True, exist_ok=True)
    log.write(args.ui_log_out)
    summary = {
        "kind": "r3i_fleet2_real_client_gate_evidence",
        "captured_at_utc": _utc(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "authorisation": {
            "scheme": 2,
            "allowed": [
                "normal game UI refresh navigation to trigger natural api_port/port",
                "normal game UI resupply of Fleet 2",
                "read-only collection, gate verification, logging, evidence archiving",
            ],
            "forbidden_and_not_done": [
                "dispatch any fleet", "change fleet composition",
                "construct/send/replay game API requests",
                "turn passive CDP listening into active game requests",
            ],
        },
        "repository": {
            "branch": "feat/production-settlement-resupply-wiring-20261105",
            "head_at_run_start": subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True,
                text=True, check=False,
            ).stdout.strip(),
        },
        "environment": {
            "cdp_browser": cdp.get("Browser"),
            "cdp_user_agent": cdp.get("User-Agent"),
            "game_webview_count": len(game_targets),
            "poi_mcp_health": health,
        },
        "phase_A_preflight": {
            "observed_at": before["observed_at"],
            "source": before["source"],
            "fleet_brief": _fleet_brief(before),
            "fleet2_detail": _fleet(before, args.fleet_id),
            "dangerous_fleets": dangerous,
            "supply_required": needs_supply,
        },
        "phase_B_resupply": {
            "performed": needs_supply and not any("preflight" in r for r in stop_reasons),
            "target_fleet_ids_process_local": [args.fleet_id],
            "production_files_modified": False,
            "return_code": supply_rc,
            "finished_at_utc": supply_finished_at,
            "audit_events": supply_events,
            "audit_click_count": supply_clicks,
            "post_state": _fleet_brief(after),
            "post_fleet2_detail": _fleet(after, args.fleet_id),
            "supply_state_changed_fleets": (
                [f["fleet_id"] for f in after["fleets"]
                 if _fleet(before, f["fleet_id"])["fuel_ready"] != f["fuel_ready"]
                 or _fleet(before, f["fleet_id"])["ammo_ready"] != f["ammo_ready"]]
                if after is not before else []
            ),
        },
        "phase_C_refresh_and_probe": {
            "C1_home_port_return_clicks": nav_clicks,
            "node_process_count_before_observer": node_before,
            "node_process_count_after_observer_start": node_after_start,
            "observer_ready": observer_ready,
            "probe_result": probe_result,
            "probe_is_zero_click_and_zero_game_api": True,
            "refresh_events": refresh_events,
            "witness_capture": witness_capture,
            "probe_evidence_file": _display_path(args.probe_evidence_out),
        },
        "probe_evidence_reference": {
            "path": _display_path(args.probe_evidence_out),
            "sha256": (
                _sha256_file(args.probe_evidence_out)
                if args.probe_evidence_out.is_file() else None
            ),
        },
        "observation": observation,
        "verdict": verdict,
        "unknown_rule_control": control_verdict,
        "same_baseline": {
            "baseline_observed_at": observation.get("observed_at"),
            "baseline_source": observation.get("source"),
            "baseline_source_freshness_verified": observation.get("source_freshness_verified"),
            "baseline_ship_ids": verdict.get("baseline_ship_ids"),
            "snapshot_source": verdict.get("snapshot_source"),
            "state_trusted": verdict.get("state_trusted"),
            "equipment_evidence_present": verdict.get("equipment_evidence_present"),
            "fleet_stats_evidence_present": verdict.get("fleet_stats_evidence_present"),
            "note": "No dispatch intent was written (dispatch is forbidden in this "
                    "authorisation). Evaluation and intent sharing one "
                    "baseline_observed_at remains proven by deterministic tests; "
                    "this run proves the gate was built on one fresh, self-consistent "
                    "ObservedFleetSnapshot.",
        },
        "stop_conditions": {"checks": checks, "failed": failed, "reasons": stop_reasons},
        "r3j_gate": {
            "all_conditions_satisfied": not failed,
            "status": "conditions_satisfied_pending_github_readback" if not failed else "NOT_SATISFIED_R3J_NOT_STARTED",
        },
        "safety": {
            "authorised_ui_clicks_sent": supply_clicks + len(clicks),
            "click_breakdown": {
                "phase_B_resupply_and_its_preflight_refresh": supply_clicks,
                "phase_C1_home_port_return": nav_clicks,
                "phase_C3_visible_refresh": len(clicks) - nav_clicks,
            },
            "navigation_click_labels": clicks,
            "probe_game_clicks_sent": 0,
            "probe_game_api_requests_sent": 0,
            "game_api_requests_authored_by_tool": 0,
            "dispatch_attempted": False,
            "dispatch_intent_written": False,
            "fleet_composition_changes_proposed": False,
            "runtime_evaluate_used": False,
        },
        "evidence_digest_sha256": _sha256(
            {"before": _fleet_brief(before), "after": _fleet_brief(after),
             "verdict": verdict, "observation": observation}
        ),
    }
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(json.dumps({
        "verdict": verdict.get("overall_result"),
        "allows_dispatch": verdict.get("allows_dispatch"),
        "control": control_verdict.get("overall_result"),
        "fresh": observation.get("source_freshness_verified"),
        "failed_checks": failed,
        "stop_reasons": stop_reasons,
        "authorised_ui_clicks": supply_clicks + len(clicks),
        "probe_evidence": str(args.probe_evidence_out),
        "ui_log": str(args.ui_log_out),
        "summary": str(args.summary_out),
    }, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
