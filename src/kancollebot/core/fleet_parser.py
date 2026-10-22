"""Parse observed POI fleet payloads without performing I/O or game actions.

Unknown or missing expedition state is preserved as unknown; it is never
silently interpreted as an idle fleet.
"""

from dataclasses import dataclass
from typing import Any


MISSION_STATE_TEXT = {
    0: "未派遣",
    1: "遠征中",
    2: "已完成",
}


@dataclass(frozen=True, slots=True)
class FleetObservation:
    """Validated, read-only subset of one POI fleet observation."""

    fleet_id: int
    fleet_name: str
    ship_slots: tuple[int, ...]
    mission_state_code: int | None
    mission_task_id: int | None
    mission_completion_time: int | None

    @property
    def occupied_ship_ids(self) -> tuple[int, ...]:
        """Return ship IDs without compacting or changing the original slot map."""
        return tuple(ship_id for ship_id in self.ship_slots if ship_id > 0)

    @property
    def mission_state_text(self) -> str:
        if self.mission_state_code is None:
            return "未知"
        return MISSION_STATE_TEXT.get(self.mission_state_code, "未知")


def _integer(value: Any, field_name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field_name} must be an integer >= {minimum}")
    return value


def _extract_fleet_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get("data")
    if not isinstance(payload, list):
        raise ValueError("fleet payload must be a list or an object containing a list in 'data'")
    if any(not isinstance(item, dict) for item in payload):
        raise ValueError("every fleet entry must be an object")
    return payload


def parse_fleet_payload(payload: Any) -> tuple[FleetObservation, ...]:
    """Parse a POI /fleets response or an object whose 'data' field is that list.

    The top-level list is intentionally handled directly. Missing or malformed
    mission fields become unknown, which prevents the planner from assuming
    that the fleet is idle.
    """
    observations: list[FleetObservation] = []
    seen_fleet_ids: set[int] = set()

    for item in _extract_fleet_list(payload):
        fleet_id = _integer(item.get("api_id"), "api_id", minimum=1)
        if fleet_id in seen_fleet_ids:
            raise ValueError(f"duplicate fleet id: {fleet_id}")
        seen_fleet_ids.add(fleet_id)

        raw_slots = item.get("api_ship")
        if not isinstance(raw_slots, list) or len(raw_slots) > 6:
            raise ValueError(f"fleet {fleet_id}: api_ship must be a list of at most six slots")

        slots: list[int] = []
        empty_seen = False
        occupied_seen: set[int] = set()
        for raw_ship_id in raw_slots:
            if isinstance(raw_ship_id, bool) or not isinstance(raw_ship_id, int):
                raise ValueError(f"fleet {fleet_id}: ship slot values must be integers")
            if raw_ship_id == -1:
                empty_seen = True
            elif raw_ship_id <= 0:
                raise ValueError(f"fleet {fleet_id}: unsupported empty/invalid ship id {raw_ship_id}")
            else:
                if empty_seen:
                    raise ValueError(f"fleet {fleet_id}: occupied ship slot follows an empty slot")
                if raw_ship_id in occupied_seen:
                    raise ValueError(f"fleet {fleet_id}: duplicate ship id {raw_ship_id}")
                occupied_seen.add(raw_ship_id)
            slots.append(raw_ship_id)

        mission = item.get("api_mission")
        state_code: int | None = None
        task_id: int | None = None
        completion_time: int | None = None
        if isinstance(mission, list) and mission:
            first = mission[0]
            if isinstance(first, int) and not isinstance(first, bool) and first >= 0:
                state_code = first
            if len(mission) > 1:
                value = mission[1]
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    task_id = value
            if len(mission) > 2:
                value = mission[2]
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    completion_time = value

        name = item.get("api_name", "")
        if not isinstance(name, str):
            raise ValueError(f"fleet {fleet_id}: api_name must be text")

        observations.append(
            FleetObservation(
                fleet_id=fleet_id,
                fleet_name=name,
                ship_slots=tuple(slots),
                mission_state_code=state_code,
                mission_task_id=task_id,
                mission_completion_time=completion_time,
            )
        )

    return tuple(observations)
