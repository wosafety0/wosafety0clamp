"""Compose a live ``FleetSupplyObservation`` from Poi Redux plus a port witness.

``ContinuousResupplyController`` refuses to plan a supply action unless the
observation it is given carries ``source_freshness_verified=True``. That flag
must mean something: the payload has to match an actual ``/kcsapi/api_port/port``
response that the trusted in-process observer captured recently.

So this provider joins two independent sources:

* the Redux snapshot (what the game client currently shows), read through the
  fixed read-only CDP expression, and
* the in-memory port witness (what the server actually last said), captured
  passively by the same observer the monitor already uses.

Only when the witness is present, recent, and field-for-field equal to the
Redux payload - and its material inventory matches too - is freshness claimed.
Anything else returns ``source_freshness_verified=False`` so the controller's
preflight fails closed instead of clicking on unverified numbers.

``observed_at`` is deliberately the time the Redux payload was read, not the
witness timestamp: the controller uses it to prove that a post-action read
happened *after* the click, and a stale witness timestamp would make that
comparison meaningless. The witness timestamps are still recorded for evidence,
they just never stand in for the observation time.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from kancollebot.adapters.poi.cdp_port_supply import PoiPortSupplySnapshot
from kancollebot.adapters.poi.port_freshness import port_snapshot_matches_current_state
from kancollebot.expeditions.continuous_resupply_controller import FleetSupplyObservation


class PortSupplyProviderError(RuntimeError):
    """The provider cannot build a coherent observation at all."""


def _witness_timestamp(witness: Any, field: str) -> datetime | None:
    if not isinstance(witness, Mapping):
        return None
    source = witness.get("source")
    if not isinstance(source, Mapping):
        return None
    raw = source.get(field)
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _materials_match(witness: Any, resources: list[int]) -> bool:
    """Require the witness material inventory to equal the Redux inventory."""
    if not isinstance(witness, Mapping):
        return False
    data = witness.get("data")
    materials = data.get("api_material") if isinstance(data, Mapping) else None
    if not isinstance(materials, list) or len(materials) != len(resources):
        return False
    indexed: dict[int, int] = {}
    for item in materials:
        if not isinstance(item, Mapping):
            return False
        material_id = item.get("api_id")
        value = item.get("api_value")
        if (
            isinstance(material_id, bool)
            or not isinstance(material_id, int)
            or material_id < 1
            or isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            or material_id in indexed
        ):
            return False
        indexed[material_id] = value
    return indexed == {index + 1: value for index, value in enumerate(resources)}


class PoiReduxFleetSupplyProvider:
    """Callable ``fleet_data_provider`` for the continuous resupply controller."""

    def __init__(
        self,
        *,
        snapshot_reader: Callable[[], PoiPortSupplySnapshot],
        port_witness_provider: Callable[[], Any] | None,
        max_age: timedelta = timedelta(seconds=30),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not callable(snapshot_reader):
            raise ValueError("snapshot_reader must be callable")
        if port_witness_provider is not None and not callable(port_witness_provider):
            raise ValueError("port_witness_provider must be callable or None")
        if max_age < timedelta(0):
            raise ValueError("max_age must not be negative")
        self.snapshot_reader = snapshot_reader
        self.port_witness_provider = port_witness_provider
        self.max_age = max_age
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        # Evidence only. Never used to decide freshness or the observation time.
        self.last_witness_captured_at: datetime | None = None
        self.last_witness_received_at: datetime | None = None
        self.last_freshness_verified = False
        self.last_reject_reason: str | None = None
        self.read_count = 0

    def __call__(self) -> FleetSupplyObservation:
        snapshot = self.snapshot_reader()
        if not isinstance(snapshot, PoiPortSupplySnapshot):
            raise PortSupplyProviderError(
                "snapshot_reader did not return a PoiPortSupplySnapshot"
            )
        self.read_count += 1

        witness = None
        if self.port_witness_provider is not None:
            witness = self.port_witness_provider()
        self.last_witness_captured_at = _witness_timestamp(witness, "bodyCapturedAt")
        self.last_witness_received_at = _witness_timestamp(witness, "responseReceivedAt")

        # Age is measured after the witness is in hand, so a slow observer call
        # can never make stale evidence look younger than it is.
        now = self.clock()
        verified, reject_reason = self._verify(witness, snapshot, now)
        self.last_freshness_verified = verified
        self.last_reject_reason = reject_reason

        return FleetSupplyObservation(
            fleets=snapshot.fleets,
            ships=snapshot.ships,
            master_ships=snapshot.master_ships,
            resources=snapshot.resources,
            observed_at=snapshot.observed_at,
            source_freshness_verified=verified,
            source="poi-redux:port",
        )

    def _verify(
        self,
        witness: Any,
        snapshot: PoiPortSupplySnapshot,
        now: datetime,
    ) -> tuple[bool, str | None]:
        if witness is None:
            return False, "no in-memory port witness is available"
        if not isinstance(witness, dict):
            return False, "port witness has an invalid shape"
        if not port_snapshot_matches_current_state(
            witness,
            snapshot.fleets,
            snapshot.ships,
            now=now,
            max_age=self.max_age,
        ):
            return False, "port witness does not match the current Redux fleet/ship state"
        if not _materials_match(witness, snapshot.resources):
            return False, "port witness does not match the current Redux material inventory"
        return True, None
