"""Validate minimal Poi collection boundaries without inferring game semantics.

This module performs no network access and never issues game actions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class PoiStateShapeError(ValueError):
    """Raised when a Poi collection does not match the observed shape."""


@dataclass(frozen=True)
class PoiCollectionSnapshot:
    fleets: tuple[Mapping[str, Any], ...]
    ships: Mapping[str, Mapping[str, Any]]


def validate_poi_collections(fleets: Any, ships: Any) -> PoiCollectionSnapshot:
    """Validate collection containers and references without decoding game meaning."""
    if not isinstance(fleets, list) or not isinstance(ships, dict):
        raise PoiStateShapeError("expected fleet array and ship object map")
    if not all(isinstance(fleet, dict) for fleet in fleets):
        raise PoiStateShapeError("fleet entries must be objects")
    if not all(isinstance(key, str) and isinstance(ship, dict) for key, ship in ships.items()):
        raise PoiStateShapeError("ship map keys must be strings and values must be objects")
    for ship in ships.values():
        if "api_id" not in ship:
            raise PoiStateShapeError("ship entry is missing api_id")
    for fleet in fleets:
        references = fleet.get("api_ship")
        if not isinstance(references, list):
            raise PoiStateShapeError("fleet api_ship must be an array")
        for reference in references:
            if isinstance(reference, bool) or not isinstance(reference, int):
                raise PoiStateShapeError("fleet api_ship references must be integers")
            if reference > 0:
                ship = ships.get(str(reference))
                if ship is None or ship.get("api_id") != reference:
                    raise PoiStateShapeError("positive ship reference has no matching ship entry")
    return PoiCollectionSnapshot(tuple(fleets), ships)
