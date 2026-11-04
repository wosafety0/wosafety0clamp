"""Read-only expedition master-data table for the minimum fleet size.

This module is the single, narrow place that reads ``api_deck_num`` from the
api_start2 / api_mission master data. ``api_deck_num`` is the number of ships a
mission requires, which is exactly ``minimum_ships`` in
``MissionCompositionRequirement``.

Nothing else from the master data is consumed here:

* ``api_sample_fleet`` (the game's suggested composition) is deliberately NOT
  used. It is a hint, not a rule, and must never become a hard requirement.
* No ship-type / ``api_stype`` data is read, so this module cannot infer
  ``allowed_ship_types``. That field stays None unless the caller supplies it.

The table is a plain ``{mission_id: minimum_ships}`` mapping. The shipped file
(``data/expedition_min_ships.json``) is derived from a captured api_start2 probe
of the public mission list; it contains only integer id/count pairs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

DEFAULT_CATALOGUE_PATH = Path(__file__).resolve().parent / "data" / "expedition_min_ships.json"


def load_mission_catalogue(path: str | Path) -> dict[int, int]:
    """Load ``mission_id -> minimum_ships`` from a master-data table.

    Accepts either the captured api_start2 object-map shape
    (``{"items": {"1": {"api_deck_num": 2, ...}}}``) or the shipped flat
    ``{id: count}`` shape.

    Raises on a missing file, a non-integer id, a non-integer ``api_deck_num``,
    or an ``api_deck_num`` outside 1..6. The upper bound is the fleet-size
    ceiling enforced by ``MissionCompositionRequirement``; a value of 0 or 7+
    means the table is wrong, not that the mission needs nothing, so it is
    rejected rather than silently passed through.
    """
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"mission catalogue not found: {path}") from exc

    if type(raw) is not dict:
        raise ValueError(f"mission catalogue at {path} must be a JSON object")

    is_api_start2 = "items" in raw
    source = raw["items"] if is_api_start2 else raw
    if type(source) is not dict:
        raise ValueError(f"mission catalogue entries at {path} must be a JSON object")

    catalogue: dict[int, int] = {}
    for key, entry in source.items():
        if type(key) is not str or not key.isascii() or not key.isdecimal():
            raise ValueError(f"mission catalogue key must be a positive ASCII integer, got {key!r}")
        mission_id = int(key)
        if mission_id < 1 or mission_id in catalogue:
            raise ValueError(f"mission catalogue contains an invalid or duplicate mission id: {key!r}")

        if is_api_start2:
            if type(entry) is not dict:
                raise ValueError(f"mission {mission_id}: api_start2 item must be a JSON object")
            deck_num = entry.get("api_deck_num")
        else:
            # The flat format is already a typed JSON mapping. Do not coerce
            # strings, floats, or booleans into integers before validation.
            deck_num = entry

        if type(deck_num) is not int or not 1 <= deck_num <= 6:
            raise ValueError(
                f"mission {mission_id}: api_deck_num must be an integer 1..6, got {deck_num!r}"
            )
        catalogue[mission_id] = deck_num
    return catalogue


def minimum_ships_for_mission(
    catalogue: Mapping[int, int] | None, mission_id: int
) -> int | None:
    """Return the minimum ship count for ``mission_id``, or None if absent.

    A ``None`` catalogue is tolerated and yields None; the caller decides whether
    absence is fatal. This function only reports what the table says.
    """
    if catalogue is None:
        return None
    return catalogue.get(mission_id)
