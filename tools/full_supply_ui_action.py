"""Perform guarded Fleet 3-only resupply through the visible Poi UI.

All unlocked fleets are read and validated, but only Fleet 3 may enter the
resource-action queue. Fleets 1 and 2 remain read-only; their supply deficits
are observed but never actioned. A fresh natural port witness is required
between checks and before the Fleet 3 action. An ambiguous click is never retried.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from kancollebot.adapters.poi.cdp_input import RecordedPoint
from kancollebot.adapters.poi.cdp_websocket import connect_game_webview
from kancollebot.adapters.poi.full_supply import (
    FullSupplyPreflightError,
    build_full_supply_plan,
    select_full_supply_targets,
    validate_full_supply_progress,
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
from kancollebot.runtime_config import RUNTIME_CONFIG
from one_expedition_ui_loop import HOME_PORT, LoopError, _require_ui_state


LOG_PATH = ROOT / "logs" / "full_supply_ui_action.jsonl"
OBSERVER_SCRIPT = ROOT / "tools" / "hand_sandbox" / "poi_port_response_observer_20261029.js"
SUPPLY_MENU_POINT = RecordedPoint(103, 340, "supply-menu")
MAX_AGE = timedelta(seconds=30)
TARGET_FLEET_IDS = (3,)
POST_SUPPLY_MAX_POLLS = 10
POST_SUPPLY_POLL_INTERVAL_SECONDS = 0.5


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


# The ship attributes the supply plan and post-click verification read. The
# larger maxima (api_fuel_max/api_bull_max) come from the master lookup, not
# from info.ships.
SHIP_FIELDS = [
    "api_id", "api_ship_id", "api_nowhp", "api_maxhp",
    "api_fuel", "api_bull", "api_cond", "api_lv",
]


def _collection(client: PoiMcpClient, path: str, kind: str) -> Any:
    arguments: dict[str, Any] = {"path": path}
    if path == "info.ships":
        # The Poi MCP caps responses by serialized size, not by item count. This
        # account holds 239 ship instances, so a plain read is truncated and the
        # suite's consistency gates fail. A field projection with a high limit
        # returns the full roster untruncated (verified live: 239/239). The
        # projection is what makes source freshness possible: the port witness
        # carries all 239 ships, and the snapshot must match key-for-key.
        arguments["limit"] = 1000
        arguments["select"] = SHIP_FIELDS
    value = _tool_value(client.call_read_tool("poi_get", arguments), f"poi_get({path})")
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



def _read_stable_state(client: PoiMcpClient) -> tuple[list[Any], dict[str, Any], list[Any], dict[str, Any]]:
    state = read_current_state(client)
    confirmation = read_current_state(client)
    if state != confirmation:
        raise FullSupplyPreflightError("Poi store changed during observation; refusing a mixed snapshot")
    return state


def _fleet_structure_signature(fleets: list[Any]) -> tuple[tuple[int, tuple[int, ...], tuple[int, ...]], ...]:
    signature = []
    for fleet in fleets:
        if not isinstance(fleet, dict):
            raise FullSupplyPreflightError("fleet structure is invalid")
        fleet_id = fleet.get("api_id")
        roster = fleet.get("api_ship")
        mission = fleet.get("api_mission")
        if (
            isinstance(fleet_id, bool) or not isinstance(fleet_id, int)
            or not isinstance(roster, list) or not isinstance(mission, list)
        ):
            raise FullSupplyPreflightError("fleet structure is incomplete")
        signature.append((fleet_id, tuple(roster), tuple(mission)))
    return tuple(sorted(signature))


def _verify_post_supply_state_with_bounded_polling(
    read_state: Callable[[], tuple[list[Any], dict[str, Any], list[Any], dict[str, Any]]],
    before_state: tuple[list[Any], dict[str, Any], list[Any], dict[str, Any]],
    *,
    target_fleet_id: int,
    target_fleet_cost: Any,
    plan: Any,
    max_polls: int = POST_SUPPLY_MAX_POLLS,
    poll_interval_seconds: float = POST_SUPPLY_POLL_INTERVAL_SECONDS,
) -> tuple[list[Any], dict[str, Any], list[Any], dict[str, Any]]:
    """Wait for Poi store propagation using read-only, bounded stable snapshots.

    This function never sends UI input. Every observed state must preserve fleet
    structure, master data, and non-target supply values. Target ship supplies
    may only move toward their master maxima, while materials may only remain
    at their pre-click values or reach the exact preflight delta.
    """
    if isinstance(max_polls, bool) or not isinstance(max_polls, int) or max_polls < 1:
        raise ValueError("max_polls must be a positive integer")
    if poll_interval_seconds < 0:
        raise ValueError("poll_interval_seconds must not be negative")

    before_fleets, before_ships, before_resources, before_masters = before_state
    target_fleet = next(
        (item for item in before_fleets if item.get("api_id") == target_fleet_id),
        None,
    )
    if not isinstance(target_fleet, dict):
        raise FullSupplyPreflightError("target fleet is missing before post-click verification")
    target_ship_ids = {
        ship_id for ship_id in target_fleet.get("api_ship", [])
        if isinstance(ship_id, int) and not isinstance(ship_id, bool) and ship_id > 0
    }
    if not target_ship_ids:
        raise FullSupplyPreflightError("target fleet has no verifiable ships")
    if target_fleet_cost.fleet_id != target_fleet_id:
        raise FullSupplyPreflightError("target fleet cost does not match the selected fleet")
    costs = (target_fleet_cost.fuel_required, target_fleet_cost.ammo_required)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in costs):
        raise FullSupplyPreflightError("target fleet material cost is invalid")
    expected_fuel = before_resources[0] - costs[0]
    expected_ammo = before_resources[1] - costs[1]
    if expected_fuel < 0 or expected_ammo < 0:
        raise FullSupplyPreflightError("preflight material cost exceeds the before-click inventory")

    for attempt in range(1, max_polls + 1):
        if attempt > 1 and poll_interval_seconds:
            time.sleep(poll_interval_seconds)
        post_state = read_state()
        post_fleets, post_ships, post_resources, post_masters = post_state

        if _fleet_structure_signature(post_fleets) != _fleet_structure_signature(before_fleets):
            raise FullSupplyPreflightError(
                "fleet membership or mission state changed after supply; do not repeat the click"
            )
        if post_masters != before_masters:
            raise FullSupplyPreflightError(
                "master ship data changed after supply; do not repeat the click"
            )

        for ship_id in plan.ship_ids:
            before_ship = before_ships.get(str(ship_id))
            after_ship = post_ships.get(str(ship_id))
            if not isinstance(before_ship, dict) or not isinstance(after_ship, dict):
                raise FullSupplyPreflightError(
                    "ship disappeared during post-click verification; do not repeat the click"
                )
            if ship_id not in target_ship_ids:
                if (
                    before_ship.get("api_fuel") != after_ship.get("api_fuel")
                    or before_ship.get("api_bull") != after_ship.get("api_bull")
                ):
                    raise FullSupplyPreflightError(
                        "supply click changed a non-target fleet; do not repeat the click"
                    )
                continue

            master_id = before_ship.get("api_ship_id")
            master = before_masters.get(str(master_id))
            if not isinstance(master, dict):
                raise FullSupplyPreflightError(
                    "target ship master data is missing; do not repeat the click"
                )
            for field, maximum_field in (
                ("api_fuel", "api_fuel_max"),
                ("api_bull", "api_bull_max"),
            ):
                before_value = before_ship.get(field)
                after_value = after_ship.get(field)
                maximum = master.get(maximum_field)
                if any(
                    isinstance(value, bool) or not isinstance(value, int)
                    for value in (before_value, after_value, maximum)
                ) or not (0 <= before_value <= after_value <= maximum):
                    raise FullSupplyPreflightError(
                        f"target ship {ship_id} {field} changed outside the allowed supply range; "
                        "do not repeat the click"
                    )

        observed_fuel, observed_ammo = post_resources[0], post_resources[1]
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (observed_fuel, observed_ammo)
        ):
            raise FullSupplyPreflightError(
                "post-click material inventory contains invalid values; do not repeat the click"
            )
        if not (
            expected_fuel <= observed_fuel <= before_resources[0]
            and expected_ammo <= observed_ammo <= before_resources[1]
        ):
            raise FullSupplyPreflightError(
                "post-click material inventory changed outside the expected supply-cost range; "
                "do not repeat the click"
            )

        target_complete = verify_fleet_supply_complete(
            target_fleet_id, post_fleets, post_ships, post_masters
        )
        materials_complete = (
            observed_fuel == expected_fuel and observed_ammo == expected_ammo
        )
        log(
            "fleet_supply_post_click_verification_poll",
            fleet_id=target_fleet_id,
            attempt=attempt,
            max_attempts=max_polls,
            target_fleet_complete=target_complete,
            expected_material_delta_observed=materials_complete,
            fuel_remaining=observed_fuel,
            ammo_remaining=observed_ammo,
            retry_allowed=False,
        )
        if target_complete and materials_complete:
            log(
                "full_supply_completed_and_verified_after_delayed_store_sync",
                fleet_id=target_fleet_id,
                poll_attempts=attempt,
                max_attempts=max_polls,
                fuel_remaining=observed_fuel,
                ammo_remaining=observed_ammo,
                ui_click_count=1,
                retry_allowed=False,
                game_api_request_initiated=False,
            )
            return post_state

    raise FullSupplyPreflightError(
        f"Poi store did not converge to the verified supply result after {max_polls} "
        "read-only polls; do not repeat the click"
    )


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
    _require_ui_state(session, UiState[expected], visual_attempts=6)
    return UiStateEvidence(expected, (expected, expected), "two_consecutive_in_memory_ooi_fingerprints")


def _require_supply_page(session) -> UiState:
    for expected in (
        UiState.SUPPLY_FLEET_1_SELECTED,
        UiState.SUPPLY_FLEET_2_SELECTED,
        UiState.SUPPLY_FLEET_3_SELECTED,
        UiState.SUPPLY_MENU,
    ):
        try:
            _require_ui_state(session, expected)
            return expected
        except LoopError:
            continue
    raise LoopError("current UI is not a verified supply page")


def _is_supply_page(session) -> bool:
    """Check Home Port first; scan supply-page variants only when necessary."""
    try:
        _require_ui_state(session, UiState.HOME_PORT)
        return False
    except LoopError:
        _require_supply_page(session)
        return True


def _acquire_fresh_witness(session, ui_arbiter: UiOperationArbiter) -> dict[str, Any]:
    observer = PassivePortResponseObserver(OBSERVER_SCRIPT, timeout_seconds=45.0)
    cancel = threading.Event()
    result: dict[str, Any] = {}

    def capture() -> None:
        result["witness"] = observer.capture_cancellable(cancel)
        result["status"] = observer.last_status

    thread = threading.Thread(target=capture, name="passive-port-witness", daemon=True)
    thread.start()
    time.sleep(1.0)
    on_supply_page = _is_supply_page(session)
    if on_supply_page:
        session.input.click(HOME_PORT)
        log("home_port_navigation_click_sent", click_count=1, game_api_request_initiated=False)
        time.sleep(1.0)
        _verify_state(session, UiState.HOME_PORT.name)
    else:
        log("home_port_precondition_already_satisfied", click_count=0)
    thread.join(timeout=5.0)

    if thread.is_alive():
        driver = VisibleUiPortRefreshDriver(
            game_input=session.input,
            verify_state=lambda expected: _verify_state(session, expected),
            ui_arbiter=ui_arbiter,
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


def _acquire_passive_fresh_witness() -> dict[str, Any]:
    """Observe only a naturally occurring port response; never touch the UI."""
    observer = PassivePortResponseObserver(OBSERVER_SCRIPT, timeout_seconds=45.0)
    witness = observer.capture()
    if not isinstance(witness, dict) or observer.last_status != "captured":
        raise FullSupplyPreflightError(
            "dry-run requires a naturally occurring fresh api_port/port response; "
            f"observer status: {observer.last_status}"
        )
    log(
        "fresh_port_witness_captured",
        source="natural_api_port_port_response",
        acquisition_mode="passive_no_ui",
        material_count=len(witness.get("data", {}).get("api_material", [])),
    )
    return witness


def _prepare_full_supply_preflight(
    client: PoiMcpClient,
    witness: dict[str, Any],
    *,
    target_fleet_ids: tuple[int, ...] = TARGET_FLEET_IDS,
) -> tuple[
    tuple[list[Any], dict[str, Any], list[Any], dict[str, Any]],
    datetime,
    Any,
    list[Any],
]:
    """Build a cost plan only from a matching, source-verified fresh witness."""
    initial_state = _read_stable_state(client)
    fleets, ships, resources, masters = initial_state
    now = datetime.now(timezone.utc)
    observed_at = _verify_fresh_witness(witness, fleets, ships, resources, now=now)
    plan = build_full_supply_plan(
        fleets, ships, masters, resources,
        source_freshness_verified=True,
        observed_at=observed_at,
        now=now,
        max_age=MAX_AGE,
        expected_fleet_ids=RUNTIME_CONFIG.all_fleet_ids,
    )
    all_needed_fleets = select_full_supply_targets(plan, supported_fleet_ids=plan.fleet_ids)
    needed_fleets = select_full_supply_targets(
        plan, target_fleet_ids=target_fleet_ids
    )
    target_ship_count = sum(item.ship_count for item in needed_fleets)
    target_fuel_required = sum(item.fuel_required for item in needed_fleets)
    target_ammo_required = sum(item.ammo_required for item in needed_fleets)
    read_only_fleet_deficits = [
        {
            "fleet_id": item.fleet_id,
            "fuel_required": item.fuel_required,
            "ammo_required": item.ammo_required,
        }
        for item in all_needed_fleets
        if item.fleet_id not in target_fleet_ids
    ]
    log(
        "full_supply_preflight_passed",
        observed_fleet_ids=list(plan.fleet_ids),
        observed_fleet_count=len(plan.fleet_ids),
        observed_ship_count=plan.ship_count,
        observed_all_fleet_fuel_required=plan.fuel_required,
        observed_all_fleet_ammo_required=plan.ammo_required,
        target_fleet_ids=list(target_fleet_ids),
        target_fleets=[item.fleet_id for item in needed_fleets],
        target_ship_count=target_ship_count,
        target_fuel_required=target_fuel_required,
        target_ammo_required=target_ammo_required,
        read_only_fleet_deficits=read_only_fleet_deficits,
        planned_fleet_wide_clicks=len(needed_fleets),
        fuel_available=plan.fuel_available,
        ammo_available=plan.ammo_available,
        damaged_ships_present=sum(
            1 for record in ships.values()
            if isinstance(record, dict) and record.get("api_nowhp") is not None
            and record.get("api_nowhp") < record.get("api_maxhp", record.get("api_nowhp"))
            and record.get("api_id") in set(plan.ship_ids)
        ),
    )
    return initial_state, observed_at, plan, needed_fleets


def run(*, dry_run: bool = False) -> int:
    client = PoiMcpClient(timeout=3.0)
    health = client.health()
    if health.get("name") != "poi-nanodesu-mcp" or health.get("status") != "ok" or health.get("storeReady") is not True:
        raise FullSupplyPreflightError("Poi MCP bridge is not healthy")
    client.initialize()
    required_tools = {tool.name for tool in client.list_tools()}
    if not {"poi_get", "poi_lookup"}.issubset(required_tools):
        raise FullSupplyPreflightError("Poi MCP bridge lacks required read-only tools")

    if dry_run:
        witness = _acquire_passive_fresh_witness()
        initial_state, observed_at, plan, needed_fleets = _prepare_full_supply_preflight(
            client, witness, target_fleet_ids=TARGET_FLEET_IDS
        )
        if not needed_fleets:
            log(
                "full_supply_not_needed",
                reason="all configured target fleets are already fully supplied",
                target_fleet_ids=list(TARGET_FLEET_IDS),
            )
            return 0
        log(
            "full_supply_dry_run_passed",
            observed_fleet_count=len(plan.fleet_ids),
            target_fleet_ids=list(TARGET_FLEET_IDS),
            target_ship_count=sum(item.ship_count for item in needed_fleets),
            target_fleets=[item.fleet_id for item in needed_fleets],
            planned_fleet_wide_clicks=len(needed_fleets),
            fuel_required=sum(item.fuel_required for item in needed_fleets),
            ammo_required=sum(item.ammo_required for item in needed_fleets),
            fuel_available=plan.fuel_available,
            ammo_available=plan.ammo_available,
            ui_clicks_sent=0,
            resource_actions_sent=0,
            ui_navigation_sent=0,
            ui_refresh_sent=0,
        )
        return 0

    from kancollebot.adapters.poi.visual_target import require_visual_dependencies
    require_visual_dependencies()

    ui_arbiter = UiOperationArbiter()
    with ui_arbiter.operation("full-supply-batch"), connect_game_webview(timeout=3.0) as session:
        witness = _acquire_fresh_witness(session, ui_arbiter)
        initial_state, observed_at, plan, needed_fleets = _prepare_full_supply_preflight(
            client, witness, target_fleet_ids=TARGET_FLEET_IDS
        )
        if not needed_fleets:
            log(
                "full_supply_not_needed",
                reason="all configured target fleets are already fully supplied",
                target_fleet_ids=list(TARGET_FLEET_IDS),
            )
            return 0

        completed_fleet_ids: list[int] = []
        final_state = None
        for target_index, initial_target_cost in enumerate(needed_fleets):
            if target_index == 0:
                cycle_witness = witness
                cycle_observed_at = observed_at
                cycle_state = initial_state
                cycle_plan = plan
            else:
                cycle_witness = _acquire_fresh_witness(session, ui_arbiter)
                cycle_state = _read_stable_state(client)
                cycle_fleets, cycle_ships, cycle_resources, cycle_masters = cycle_state
                cycle_now = datetime.now(timezone.utc)
                cycle_observed_at = _verify_fresh_witness(
                    cycle_witness, cycle_fleets, cycle_ships, cycle_resources, now=cycle_now
                )
                cycle_plan = build_full_supply_plan(
                    cycle_fleets, cycle_ships, cycle_masters, cycle_resources,
                    source_freshness_verified=True,
                    observed_at=cycle_observed_at,
                    now=cycle_now,
                    max_age=MAX_AGE,
                    expected_fleet_ids=RUNTIME_CONFIG.all_fleet_ids,
                )
                log(
                    "next_fleet_freshness_cycle_verified",
                    completed_fleet_ids=list(completed_fleet_ids),
                    next_fleet_id=initial_target_cost.fleet_id,
                    fuel_available=cycle_plan.fuel_available,
                    ammo_available=cycle_plan.ammo_available,
                    source_freshness_verified=True,
                )

            remaining = validate_full_supply_progress(
                plan, cycle_plan, tuple(completed_fleet_ids),
                target_fleet_ids=TARGET_FLEET_IDS,
            )
            if not remaining or remaining[0].fleet_id != initial_target_cost.fleet_id:
                raise FullSupplyPreflightError("batch progress does not match the next configured fleet")
            target_fleet_cost = remaining[0]
            target_fleet_id = target_fleet_cost.fleet_id
            target_fleet_state = UiState[f"SUPPLY_FLEET_{target_fleet_id}_SELECTED"]

            _require_ui_state(session, UiState.HOME_PORT)
            session.input.click(SUPPLY_MENU_POINT)
            log("supply_menu_navigation_click_sent", fleet_id=target_fleet_id, click_count=1)
            time.sleep(1.0)
            selected_supply_state = _require_supply_page(session)
            if selected_supply_state is target_fleet_state:
                log("supply_fleet_tab_already_selected", fleet_index=target_fleet_id, click_count=0)
            else:
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

            before_state = _read_stable_state(client)
            if before_state != cycle_state:
                raise FullSupplyPreflightError("fleet, ship, or resource state changed before the supply click")
            before_fleets, before_ships, before_resources, before_masters = before_state
            before_now = datetime.now(timezone.utc)
            _verify_fresh_witness(
                cycle_witness, before_fleets, before_ships, before_resources, now=before_now
            )
            before_plan = build_full_supply_plan(
                before_fleets, before_ships, before_masters, before_resources,
                source_freshness_verified=True,
                observed_at=cycle_observed_at,
                now=before_now,
                max_age=MAX_AGE,
                expected_fleet_ids=RUNTIME_CONFIG.all_fleet_ids,
            )
            before_remaining = validate_full_supply_progress(
                plan, before_plan, tuple(completed_fleet_ids),
                target_fleet_ids=TARGET_FLEET_IDS,
            )
            if not before_remaining or before_remaining[0] != target_fleet_cost:
                raise FullSupplyPreflightError("full-supply cost or target changed before the UI click")

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
                fleet_id=target_fleet_id,
                label=target.label,
                bounds=list(target.bounds),
                region=list(target.region),
                click_count=1,
                source_frame_sha256=target.source_frame_sha256,
            )
            session.input.click_visual_target(target, screenshot_base64=frame, button="left")
            log("fleet_wide_supply_click_sent", fleet_id=target_fleet_id, click_count=1, retry_allowed=False)
            post_state = _verify_post_supply_state_with_bounded_polling(
                lambda: _read_stable_state(client),
                before_state,
                target_fleet_id=target_fleet_id,
                target_fleet_cost=target_fleet_cost,
                plan=plan,
            )
            post_fleets, post_ships, post_resources, post_masters = post_state

            completed_fleet_ids.append(target_fleet_id)
            final_state = post_state
            log(
                "fleet_full_supply_completed",
                target_fleet_id=target_fleet_id,
                completed_fleet_ids=list(completed_fleet_ids),
                target_fleet_fuel_consumed=target_fleet_cost.fuel_required,
                target_fleet_ammo_consumed=target_fleet_cost.ammo_required,
                fuel_remaining=post_resources[0],
                ammo_remaining=post_resources[1],
                ui_click_count=1,
                retry_allowed=False,
                game_api_request_initiated=False,
            )

        if final_state is None:
            raise FullSupplyPreflightError("full-supply batch ended without a verified action result")
        final_fleets, final_ships, final_resources, final_masters = final_state
        for fleet_id in completed_fleet_ids:
            if not verify_fleet_supply_complete(
                fleet_id, final_fleets, final_ships, final_masters
            ):
                raise FullSupplyPreflightError(
                    f"target fleet {fleet_id} is not fully supplied"
                )
        target_fuel_required = sum(item.fuel_required for item in needed_fleets)
        target_ammo_required = sum(item.ammo_required for item in needed_fleets)
        target_ship_count = sum(item.ship_count for item in needed_fleets)
        if (
            final_resources[0] != plan.fuel_available - target_fuel_required
            or final_resources[1] != plan.ammo_available - target_ammo_required
        ):
            raise FullSupplyPreflightError("final material delta differs from the target-fleet preflight")
        log(
            "full_supply_completed_and_verified",
            target_fleet_ids=list(completed_fleet_ids),
            observed_fleet_count=len(plan.fleet_ids),
            target_ship_count=target_ship_count,
            fuel_consumed=target_fuel_required,
            ammo_consumed=target_ammo_required,
            fuel_remaining=final_resources[0],
            ammo_remaining=final_resources[1],
            ui_click_count=len(completed_fleet_ids),
            target_fleets_complete=True,
            non_target_fleets_untouched=True,
            game_api_request_initiated=False,
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--authorize-full-supply", action="store_true",
        help="Explicitly authorize the Fleet 3-only supply action; Fleets 1 and 2 remain read-only",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Read-only dry run: use only a natural port response; never click, navigate, or refresh the UI",
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
