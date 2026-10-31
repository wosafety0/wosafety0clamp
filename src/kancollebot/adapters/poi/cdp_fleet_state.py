"""Narrow, read-only fleet-state access through the fixed Poi Redux snapshot expression."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from .cdp_websocket import READ_ONLY_FLEET_SNAPSHOT_EXPRESSION


class CdpFleetStateError(RuntimeError):
    """The Poi Redux fleet snapshot is missing, malformed, or ambiguous."""


class CdpReadTransport(Protocol):
    def send(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(frozen=True, slots=True)
class PoiFleetMissionState:
    fleet_id: int
    name: str | None
    mission: tuple[int, int, int, int]
    ship_ids: tuple[int, ...]

    @property
    def mission_status(self) -> int:
        return self.mission[0]

    @property
    def mission_id(self) -> int:
        return self.mission[1]

    @property
    def final_time_ms(self) -> int:
        return self.mission[2]


def _safe_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or abs(value) > 9_007_199_254_740_991:
        raise CdpFleetStateError(f"{label} is not a safe integer")
    return value


def read_poi_fleet_states(transport: CdpReadTransport) -> tuple[PoiFleetMissionState, ...]:
    """Read only fleet IDs, names, mission tuples, and ship membership from Poi's cached Redux state."""
    result = transport.send(
        "Runtime.evaluate",
        {"expression": READ_ONLY_FLEET_SNAPSHOT_EXPRESSION, "returnByValue": True},
    )
    remote = result.get("result") if isinstance(result, dict) else None
    raw = remote.get("value") if isinstance(remote, dict) else None
    if not isinstance(remote, dict) or remote.get("type") != "string" or not isinstance(raw, str):
        raise CdpFleetStateError("CDP did not return a string fleet snapshot")
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CdpFleetStateError("Poi fleet snapshot was not valid JSON") from exc
    if not isinstance(items, list) or not 3 <= len(items) <= 4:
        raise CdpFleetStateError("Poi fleet snapshot must contain three or four fleet records")
    states: list[PoiFleetMissionState] = []
    seen: set[int] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise CdpFleetStateError(f"Fleet record {index} is not an object")
        fleet_id = _safe_int(item.get("id"), f"Fleet {index} ID")
        if not 1 <= fleet_id <= 4 or fleet_id in seen:
            raise CdpFleetStateError("Fleet IDs must be unique integers from 1 through 4")
        seen.add(fleet_id)
        name = item.get("name")
        if name is not None and not isinstance(name, str):
            raise CdpFleetStateError(f"Fleet {fleet_id} name has an invalid type")
        mission = item.get("mission")
        if not isinstance(mission, list) or len(mission) != 4:
            raise CdpFleetStateError(f"Fleet {fleet_id} mission must contain exactly four slots")
        mission_tuple = tuple(_safe_int(value, f"Fleet {fleet_id} mission slot") for value in mission)
        ships = item.get("ships")
        if not isinstance(ships, list) or len(ships) > 6:
            raise CdpFleetStateError(f"Fleet {fleet_id} ship membership is invalid")
        ship_tuple = tuple(_safe_int(value, f"Fleet {fleet_id} ship ID") for value in ships)
        states.append(PoiFleetMissionState(fleet_id, name, mission_tuple, ship_tuple))
    if seen != set(range(1, len(items) + 1)):
        raise CdpFleetStateError("Fleet IDs are not a contiguous set starting at 1")
    return tuple(sorted(states, key=lambda state: state.fleet_id))
