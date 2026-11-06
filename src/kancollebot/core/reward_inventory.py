from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping


REWARD_INVENTORY_BASELINE_EVENT = "mission_reward_inventory_baseline"


class RewardInventoryError(ValueError):
    """A Poi reward-inventory snapshot is malformed or incomplete."""


@dataclass(frozen=True, slots=True)
class RewardInventorySnapshot:
    observed_at: datetime
    resources: tuple[int, ...]
    useitems: tuple[tuple[int, int], ...]
    source: str = "poi-nanodesu-mcp:info.resources+info.useitems"

    def __post_init__(self) -> None:
        if not isinstance(self.observed_at, datetime) or self.observed_at.tzinfo is None:
            raise RewardInventoryError("inventory observation time must be timezone-aware")
        if len(self.resources) != 8 or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in self.resources
        ):
            raise RewardInventoryError("inventory resources must contain eight non-negative integers")
        ids: set[int] = set()
        for item_id, count in self.useitems:
            if (
                isinstance(item_id, bool) or not isinstance(item_id, int) or item_id < 1
                or isinstance(count, bool) or not isinstance(count, int) or count < 0
                or item_id in ids
            ):
                raise RewardInventoryError("inventory useitems must have unique positive IDs and non-negative counts")
            ids.add(item_id)
        if not isinstance(self.source, str) or not self.source.strip():
            raise RewardInventoryError("inventory source must be a non-empty string")


@dataclass(frozen=True, slots=True)
class RewardInventoryVerdict:
    allowed: bool
    reason: str
    resource_deltas: tuple[int, ...] = ()
    useitem_deltas: tuple[tuple[int, int], ...] = ()


def reward_inventory_snapshot_fields(
    snapshot: RewardInventorySnapshot, *, fleet_id: int, dispatch_id: str
) -> dict[str, object]:
    if isinstance(fleet_id, bool) or not isinstance(fleet_id, int) or fleet_id < 1:
        raise ValueError("fleet_id must be a positive integer")
    if not isinstance(dispatch_id, str) or not dispatch_id.strip():
        raise ValueError("dispatch_id must be a non-empty string")
    return {
        "fleet_id": fleet_id,
        "dispatch_id": dispatch_id.strip(),
        "observed_at": snapshot.observed_at.isoformat(),
        "resources": list(snapshot.resources),
        "useitems": [
            {"api_id": item_id, "api_count": count}
            for item_id, count in snapshot.useitems
        ],
        "source": snapshot.source,
    }


def parse_reward_inventory_snapshot(value: object) -> RewardInventorySnapshot:
    if not isinstance(value, Mapping):
        raise RewardInventoryError("inventory snapshot fields must be a mapping")
    observed_at_value = value.get("observed_at")
    if not isinstance(observed_at_value, str):
        raise RewardInventoryError("inventory snapshot time is missing")
    try:
        observed_at = datetime.fromisoformat(observed_at_value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RewardInventoryError("inventory snapshot time is invalid") from exc
    raw_resources = value.get("resources")
    if not isinstance(raw_resources, (list, tuple)):
        raise RewardInventoryError("inventory resources are missing")
    resources = tuple(raw_resources)
    raw_useitems = value.get("useitems")
    if not isinstance(raw_useitems, (list, tuple)):
        raise RewardInventoryError("inventory useitems are missing")
    useitems: list[tuple[int, int]] = []
    for item in raw_useitems:
        if not isinstance(item, Mapping):
            raise RewardInventoryError("inventory useitem row is malformed")
        item_id = item.get("api_id")
        count = item.get("api_count")
        useitems.append((item_id, count))
    return RewardInventorySnapshot(
        observed_at=observed_at,
        resources=resources,
        useitems=tuple(sorted(useitems)),
        source=value.get("source", "poi-nanodesu-mcp:info.resources+info.useitems"),
    )


def latest_reward_inventory_baseline_for_fleet(
    events: Iterable[Mapping[str, object]], fleet_id: int, dispatch_id: str
) -> RewardInventorySnapshot | None:
    if isinstance(fleet_id, bool) or not isinstance(fleet_id, int) or fleet_id < 1:
        raise ValueError("fleet_id must be a positive integer")
    if not isinstance(dispatch_id, str) or not dispatch_id.strip():
        raise ValueError("dispatch_id must be a non-empty string")
    latest_fields: Mapping[str, object] | None = None
    for row in events:
        if not isinstance(row, Mapping) or row.get("event") != REWARD_INVENTORY_BASELINE_EVENT:
            continue
        fields = row.get("fields")
        if (
            not isinstance(fields, Mapping)
            or fields.get("fleet_id") != fleet_id
            or fields.get("dispatch_id") != dispatch_id
        ):
            continue
        latest_fields = fields
    if latest_fields is None:
        return None
    try:
        return parse_reward_inventory_snapshot(latest_fields)
    except RewardInventoryError:
        return None


def verify_reward_inventory_delta(
    *,
    before: RewardInventorySnapshot,
    after: RewardInventorySnapshot,
    expected_material: tuple[int, ...],
    expected_items: tuple[tuple[int, int, int], ...],
    requested_at: datetime,
    response_received_at: datetime,
) -> RewardInventoryVerdict:
    """Compare the Poi Redux inventory with the reward receipt for one result."""
    if before.observed_at >= requested_at:
        return RewardInventoryVerdict(False, "inventory_baseline_not_before_result_request")
    if after.observed_at < response_received_at:
        return RewardInventoryVerdict(False, "inventory_read_predates_result_response")
    if after.observed_at <= before.observed_at:
        return RewardInventoryVerdict(False, "inventory_read_not_newer_than_baseline")
    if len(expected_material) not in (4, 8):
        return RewardInventoryVerdict(False, "reward_material_receipt_has_invalid_length")

    resource_deltas = tuple(
        after.resources[index] - before.resources[index] for index in range(8)
    )
    for index in range(8):
        expected = expected_material[index] if index < len(expected_material) else 0
        if resource_deltas[index] != expected:
            return RewardInventoryVerdict(
                False,
                f"reward_resource_delta_mismatch_at_index_{index}",
                resource_deltas=resource_deltas,
            )

    expected_useitems: dict[int, int] = {}
    for _, item_id, count in expected_items:
        expected_useitems[item_id] = expected_useitems.get(item_id, 0) + count
    before_items = dict(before.useitems)
    after_items = dict(after.useitems)
    all_ids = sorted(set(before_items) | set(after_items) | set(expected_useitems))
    useitem_deltas = tuple(
        (item_id, after_items.get(item_id, 0) - before_items.get(item_id, 0))
        for item_id in all_ids
    )
    for item_id, delta in useitem_deltas:
        if delta != expected_useitems.get(item_id, 0):
            return RewardInventoryVerdict(
                False,
                f"reward_useitem_delta_mismatch_for_id_{item_id}",
                resource_deltas=resource_deltas,
                useitem_deltas=useitem_deltas,
            )
    return RewardInventoryVerdict(
        True,
        "reward_inventory_matches_result_receipt",
        resource_deltas=resource_deltas,
        useitem_deltas=useitem_deltas,
    )
