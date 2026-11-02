"""Perform one guarded fleet-wide resupply through the visible Poi UI.

No game API request is initiated by this tool. It observes a natural port response,
validates the current Poi store, and sends at most one click to the allowlisted
fleet-wide resupply button. An ambiguous click is never retried.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from kancollebot.adapters.poi.cdp_input import RecordedPoint
from kancollebot.adapters.poi.cdp_websocket import connect_game_webview
from kancollebot.adapters.poi.full_supply import (
    FullSupplyPreflightError,
    build_full_supply_plan,
    verify_full_supply_complete,
    verify_fleet_supply_complete,
)
from kancollebot.adapters.poi.mcp_client import PoiMcpClient, PoiMcpError
from kancollebot.adapters.poi.port_freshness import port_snapshot_matches_current_state
from kancollebot.adapters.poi.port_response_observer import PassivePortResponseObserver
from kancollebot.adapters.poi.ui_state_gate import UiState
from kancollebot.adapters.poi.visible_ui_refresh import VisibleUiPortRefreshDriver
from kancollebot.adapters.poi.visual_target import locate_fleet_supply_all_button, locate_supply_fleet_tab
from kancollebot.expeditions.continuous_dispatch_controller import UiStateEvidence
from kancollebot.expeditions.continuous_scheduler import UiOperationArbiter
from one_expedition_ui_loop import HOME_PORT, LoopError, _require_ui_state


LOG_PATH = ROOT / "logs" / "full_supply_ui_action.jsonl"
OBSERVER_SCRIPT = ROOT / "tools" / "hand_sandbox" / "poi_port_response_observer_20261029.js"
SUPPLY_MENU_POINT = RecordedPoint(103, 340, "supply-menu")
MAX_AGE = timedelta(seconds=30)


def log(event: str, **fields: Any) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    line = json.dumps(payload, ensure_ascii=True)
    with LOG_PATH.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(line + "\n")
    print(line, flush=True)


def _tool_value(result: dict[str, Any], name: str) -> Any:
    if result.get("isError") is True:
        raise PoiMcpError(f"{name} returned a tool-level error")
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    content = result.get("content")
    texts = [
        item.get("text") for item in content
        if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
    ] if isinstance(content, list) else []
    if len(texts) != 1:
        raise PoiMcpError(f"{name} returned an ambiguous payload")
    try:
        return json.loads(texts[0])
    except json.JSONDecodeError as exc:
        raise PoiMcpError(f"{name} returned invalid JSON") from exc


def _collection(client: PoiMcpClient, path: str, kind: str) -> Any:
    value = _tool_value(client.call_read_tool("poi_get", {"path": path}), f"poi_get({path})")
    if (
        not isinstance(value, dict) or value.get("kind") != kind
        or value.get("truncated") is not False
        or value.get("returned") != value.get("total")
        or not isinstance(value.get("items"), list if kind == "array" else dict)
        or value.get("returned") != len(value["items"])
    ):
        raise PoiMcpError(f"{path} is incomplete or has an unknown shape")
    return value["items"]


def read_current_state(client: PoiMcpClient) -> tuple[list[Any], dict[str, Any], list[Any], dict[str, Any]]:
    fleets = _collection(client, "info.fleets", "array")
    ships = _collection(client, "info.ships", "object-map")
    resources = _collection(client, "info.resources", "array")
    if len(resources) != 8:
        raise PoiMcpError("resource inventory must contain exactly eight material slots")
    master_ids: set[int] = set()
    for fleet in fleets:
        if not isinstance(fleet, dict) or not isinstance(fleet.get("api_ship"), list):
            raise PoiMcpError("fleet roster is incomplete")
        for ship_id in fleet["api_ship"]:
            if ship_id == -1:
                continue
            if isinstance(ship_id, bool) or not isinstance(ship_id, int) or ship_id < 1:
                raise PoiMcpError("fleet roster contains an invalid ship ID")
            ship = ships.get(str(ship_id))
            if not isinstance(ship, dict):
                raise PoiMcpError(f"fleet references missing ship {ship_id}")
            master_id = ship.get("api_ship_id")
            if isinstance(master_id, bool) or not isinstance(master_id, int) or master_id < 1:
                raise PoiMcpError(f"ship {ship_id} has an invalid master ID")
            master_ids.add(master_id)
    lookup = _tool_value(
        client.call_read_tool(
            "poi_lookup",
            {"kind": "ships", "ids": sorted(master_ids), "select": ["api_fuel_max", "api_bull_max"]},
        ),
        "poi_lookup(ships)",
    )
    if not isinstance(lookup, dict) or set(lookup) != {str(item) for item in master_ids}:
        raise PoiMcpError("master ship lookup is incomplete")
    return fleets, ships, resources, lookup


def _materials_match(witness: dict[str, Any], resources: list[Any]) -> bool:
    data = witness.get("data")
    materials = data.get("api_material") if isinstance(data, dict) else None
    if not isinstance(materials, list) or len(materials) != len(resources):
        return False
    indexed: dict[int, int] = {}
    for item in materials:
        if not isinstance(item, dict):
            return False
        material_id, value = item.get("api_id"), item.get("api_value")
        if (
            isinstance(material_id, bool) or not isinstance(material_id, int)
            or isinstance(value, bool) or not isinstance(value, int) or value < 0
            or material_id in indexed
        ):
            return False
        indexed[material_id] = value
    return indexed == {index + 1: value for index, value in enumerate(resources)}


def _verify_fresh_witness(
    witness: dict[str, Any],
    fleets: list[Any],
    ships: dict[str, Any],
    resources: list[Any],
    *,
    now: datetime,
) -> datetime:
    if not port_snapshot_matches_current_state(
        witness, fleets, ships, now=now, max_age=MAX_AGE
    ):
        raise FullSupplyPreflightError("fresh port witness does not match current fleet/ship state")
    if not _materials_match(witness, resources):
        raise FullSupplyPreflightError("fresh port witness does not match current resource inventory")
    source = witness.get("source")
    captured = source.get("bodyCapturedAt") if isinstance(source, dict) else None
    if not isinstance(captured, str):
        raise FullSupplyPreflightError("fresh port witness has no capture timestamp")
    try:
        observed_at = datetime.fromisoformat(captured.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FullSupplyPreflightError("fresh port witness timestamp is invalid") from exc
    return observed_at


def _verify_state(session, expected: str) -> UiStateEvidence:
    _require_ui_state(session, UiState[expected])
    return UiStateEvidence(expected, (expected, expected), "two_consecutive_in_memory_ooi_fingerprints")


def _acquire_fresh_witness(session) -> dict[str, Any]:
    observer = PassivePortResponseObserver(OBSERVER_SCRIPT, timeout_seconds=45.0)
    cancel = threading.Event()
    result: dict[str, Any] = {}

    def capture() -> None:
        result["witness"] = observer.capture_cancellable(cancel)
        result["status"] = observer.last_status

    thread = threading.Thread(target=capture, name="passive-port-witness", daemon=True)
    thread.start()
    time.sleep(1.0)
    on_supply_page = False
    for expected in (
        UiState.SUPPLY_FLEET_2_SELECTED,
        UiState.SUPPLY_FLEET_3_SELECTED,
        UiState.SUPPLY_MENU,
    ):
        try:
            _require_ui_state(session, expected)
            on_supply_page = True
            break
        except LoopError:
            continue
    if on_supply_page:
        session.input.click(HOME_PORT)
        log("home_port_navigation_click_sent", click_count=1, game_api_request_initiated=False)
        time.sleep(1.0)
        _require_ui_state(session, UiState.HOME_PORT)
    else:
        _require_ui_state(session, UiState.HOME_PORT)
        log("home_port_precondition_already_satisfied", click_count=0)
    thread.join(timeout=5.0)

    if thread.is_alive():
        driver = VisibleUiPortRefreshDriver(
            game_input=session.input,
            verify_state=lambda expected: _verify_state(session, expected),
            ui_arbiter=UiOperationArbiter(),
            event_sink=lambda event, payload: log(event, **dict(payload)),
            settle_seconds=1.0,
        )
        driver.refresh_port_state()
        thread.join(timeout=20.0)

    if thread.is_alive():
        cancel.set()
        thread.join(timeout=2.0)
        raise FullSupplyPreflightError("no natural api_port/port response was captured after visible navigation")
    witness = result.get("witness")
    if not isinstance(witness, dict) or result.get("status") != "captured":
        raise FullSupplyPreflightError(f"passive port observer did not capture a witness: {result.get('status')}")
    log("fresh_port_witness_captured", source="natural_api_port_port_response", material_count=len(witness.get("data", {}).get("api_material", [])))
    return witness


def run(*, dry_run: bool = False) -> int:
    client = PoiMcpClient(timeout=3.0)
    health = client.health()
    if health.get("name") != "poi-nanodesu-mcp" or health.get("status") != "ok" or health.get("storeReady") is not True:
        raise FullSupplyPreflightError("Poi MCP bridge is not healthy")
    client.initialize()
    required_tools = {tool.name for tool in client.list_tools()}
    if not {"poi_get", "poi_lookup"}.issubset(required_tools):
        raise FullSupplyPreflightError("Poi MCP bridge lacks required read-only tools")

    with connect_game_webview(timeout=3.0) as session:
        witness = _acquire_fresh_witness(session)
        fleets, ships, resources, masters = read_current_state(client)
        confirm_fleets, confirm_ships, confirm_resources, confirm_masters = read_current_state(client)
        if (
            fleets != confirm_fleets or ships != confirm_ships
            or resources != confirm_resources or masters != confirm_masters
        ):
            raise FullSupplyPreflightError("Poi store changed during preflight; refusing a mixed snapshot")
        now = datetime.now(timezone.utc)
        observed_at = _verify_fresh_witness(witness, fleets, ships, resources, now=now)
        plan = build_full_supply_plan(
            fleets, ships, masters, resources,
            source_freshness_verified=True,
            observed_at=observed_at,
            now=now,
            max_age=MAX_AGE,
            expected_fleet_ids=(1, 2, 3),
        )
        needed_fleets = [
            item for item in plan.fleet_costs
            if item.fuel_required > 0 or item.ammo_required > 0
        ]
        log(
            "full_supply_preflight_passed",
            fleet_ids=list(plan.fleet_ids),
            fleet_count=len(plan.fleet_ids),
            ship_count=plan.ship_count,
            fuel_required=plan.fuel_required,
            ammo_required=plan.ammo_required,
            target_fleets=[item.fleet_id for item in needed_fleets],
            fuel_available=plan.fuel_available,
            ammo_available=plan.ammo_available,
            damaged_ships_present=sum(
                1 for record in ships.values()
                if isinstance(record, dict) and record.get("api_nowhp") is not None
                and record.get("api_nowhp") < record.get("api_maxhp", record.get("api_nowhp"))
                and record.get("api_id") in set(plan.ship_ids)
            ),
        )
        if not needed_fleets:
            log("full_supply_not_needed", reason="all ships are already fully supplied")
            return 0
        if len(needed_fleets) != 1:
            raise FullSupplyPreflightError(
                "more than one fleet needs supply; process fleets in separate fresh-witness cycles"
            )
        target_fleet_cost = needed_fleets[0]
        target_fleet_id = target_fleet_cost.fleet_id
        if target_fleet_id not in (2, 3):
            raise FullSupplyPreflightError(
                f"fleet {target_fleet_id} needs supply but has no independently verified supply-tab gate"
            )
        target_fleet_state = UiState[f"SUPPLY_FLEET_{target_fleet_id}_SELECTED"]
        if dry_run:
            log(
                "full_supply_dry_run_passed",
                fleet_count=len(plan.fleet_ids),
                ship_count=plan.ship_count,
                fuel_required=plan.fuel_required,
                ammo_required=plan.ammo_required,
                fuel_available=plan.fuel_available,
                ammo_available=plan.ammo_available,
                ui_clicks_sent=0,
                resource_actions_sent=0,
            )
            return 0

        session.input.click(SUPPLY_MENU_POINT)
        log("supply_menu_navigation_click_sent", click_count=1)
        time.sleep(1.0)
        _require_ui_state(session, UiState.SUPPLY_MENU)
        tab_frame = session.input.capture_screenshot()
        fleet_tab = locate_supply_fleet_tab(tab_frame, fleet_index=target_fleet_id)
        session.input.click_visual_target(fleet_tab, screenshot_base64=tab_frame, button="left")
        log(
            "supply_fleet_tab_selected_for_global_button_visibility",
            fleet_index=target_fleet_id,
            bounds=list(fleet_tab.bounds),
            click_count=1,
        )
        time.sleep(0.5)
        _require_ui_state(session, target_fleet_state)

        current_fleets, current_ships, current_resources, current_masters = read_current_state(client)
        if (
            current_fleets != fleets or current_ships != ships
            or current_resources != resources or current_masters != masters
        ):
            raise FullSupplyPreflightError("fleet, ship, or resource state changed before the supply click")
        now = datetime.now(timezone.utc)
        _verify_fresh_witness(witness, current_fleets, current_ships, current_resources, now=now)
        current_plan = build_full_supply_plan(
            current_fleets, current_ships, current_masters, current_resources,
            source_freshness_verified=True,
            observed_at=observed_at,
            now=now,
            max_age=MAX_AGE,
            expected_fleet_ids=(1, 2, 3),
        )
        if current_plan != plan:
            raise FullSupplyPreflightError("full-supply cost changed after the initial preflight")

        targets = []
        frame = ""
        for _ in range(3):
            _require_ui_state(session, target_fleet_state)
            frame = session.input.capture_screenshot()
            targets.append(locate_fleet_supply_all_button(frame))
            time.sleep(0.25)
        bounds = {target.bounds for target in targets}
        if len(bounds) != 1:
            raise FullSupplyPreflightError("fleet-wide supply button location drifted across frames")
        target = targets[-1]
        log(
            "fleet_wide_supply_button_verified",
            label=target.label,
            bounds=list(target.bounds),
            region=list(target.region),
            click_count=1,
            source_frame_sha256=target.source_frame_sha256,
        )
        session.input.click_visual_target(target, screenshot_base64=frame, button="left")
        log("fleet_wide_supply_click_sent", click_count=1, retry_allowed=False)
        time.sleep(2.0)

        post_fleets, post_ships, post_resources, post_masters = read_current_state(client)
        confirm_post_fleets, confirm_post_ships, confirm_post_resources, confirm_post_masters = read_current_state(client)
        if (
            post_fleets != confirm_post_fleets or post_ships != confirm_post_ships
            or post_resources != confirm_post_resources or post_masters != confirm_post_masters
        ):
            raise FullSupplyPreflightError("post-click Poi state is unstable; do not repeat the click")
        if not verify_fleet_supply_complete(target_fleet_id, post_fleets, post_ships, post_masters):
            raise FullSupplyPreflightError(
                f"fleet {target_fleet_id} was not fully supplied; do not repeat the click"
            )
        expected_fuel = resources[0] - target_fleet_cost.fuel_required
        expected_ammo = resources[1] - target_fleet_cost.ammo_required
        if post_resources[0] != expected_fuel or post_resources[1] != expected_ammo:
            raise FullSupplyPreflightError("post-click resource delta differs from the target-fleet cost; do not repeat the click")
        if verify_full_supply_complete(plan, post_fleets, post_ships, post_masters):
            event = "full_supply_completed_and_verified"
        else:
            event = "fleet_full_supply_completed_remaining_fleets_pending"
        log(
            event,
            target_fleet_id=target_fleet_id,
            fleet_count=len(plan.fleet_ids),
            ship_count=plan.ship_count,
            target_fleet_fuel_consumed=target_fleet_cost.fuel_required,
            target_fleet_ammo_consumed=target_fleet_cost.ammo_required,
            fuel_remaining=post_resources[0],
            ammo_remaining=post_resources[1],
            all_fleets_complete=event == "full_supply_completed_and_verified",
            ui_click_count=1,
            game_api_request_initiated=False,
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--authorize-full-supply", action="store_true",
        help="Explicitly authorize one fleet-wide resupply UI click for the currently unlocked fleets",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate freshness, fleet state, material costs, and UI navigation without resupplying",
    )
    args = parser.parse_args()
    if not args.dry_run and not args.authorize_full_supply:
        parser.error("--authorize-full-supply is required unless --dry-run is specified")
    try:
        return run(dry_run=args.dry_run)
    except Exception as exc:
        try:
            log(
                "STOPPED_FAIL_CLOSED",
                error_type=type(exc).__name__,
                error=str(exc),
                retry_allowed=False,
            )
        except OSError:
            print(json.dumps({"event": "STOPPED_FAIL_CLOSED", "error_type": type(exc).__name__}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
