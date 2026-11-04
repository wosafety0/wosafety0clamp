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

    if not isinstance(raw, dict):
        raise ValueError(f"mission catalogue at {path} must be a JSON object")

    source = raw.get("items", raw)
    catalogue: dict[int, int] = {}
    for key, entry in source.items():
        mission_id = int(key)
        deck_num = entry.get("api_deck_num") if isinstance(entry, dict) else int(entry)
        if isinstance(deck_num, bool) or not isinstance(deck_num, int) or not 1 <= deck_num <= 6:
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
