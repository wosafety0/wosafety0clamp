"""Pure fail-closed expedition fleet composition planner; no game I/O."""

from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class CandidateShip:
    ship_id: int
    ship_type: str
    level: int
    locked: bool
    heavily_damaged: bool
    repairing: bool


@dataclass(frozen=True)
class FleetBuildResult:
    eligible: bool
    ship_ids: tuple[int, ...]
    reason: str


def build_expedition_fleet(
    ships: Sequence[CandidateShip],
    required_types: Mapping[str, int],
    *,
    min_flagship_level: int = 1,
    min_total_level: int = 1,
    max_ships: int = 6,
    reserved_ship_ids: Sequence[int] = (),
) -> FleetBuildResult:
    """Find a valid fleet without assuming unobserved equipment or game state.

    required_types specify required ship-type slots; NA is any eligible ship type.\n    This planner does not
    certify drum, landing-craft, supply, morale, or mission-specific constraints.
    """
    invalid = FleetBuildResult(False, (), "Invalid or incomplete composition input.")
    if (
        isinstance(max_ships, bool) or not isinstance(max_ships, int)
        or not 1 <= max_ships <= 6
        or any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in required_types.values())
        or any(not isinstance(t, str) or not t for t in required_types)
        or not required_types or sum(required_types.values()) < 1
        or sum(required_types.values()) > max_ships
        or any(isinstance(v, bool) or not isinstance(v, int) or v < 1
               for v in (min_flagship_level, min_total_level))
        or any(not isinstance(s, CandidateShip) for s in ships)
        or any(isinstance(i, bool) or not isinstance(i, int) or i <= 0 for i in reserved_ship_ids)
    ):
        return invalid
    ids = [s.ship_id for s in ships]
    if any(isinstance(i, bool) or not isinstance(i, int) or i <= 0 for i in ids) or len(ids) != len(set(ids)):
        return invalid
    reserved = set(reserved_ship_ids)
    pool = sorted(
        (s for s in ships if s.locked and not s.heavily_damaged
         and not s.repairing and s.ship_id not in reserved
         and isinstance(s.ship_type, str) and s.ship_type
         and isinstance(s.level, int) and not isinstance(s.level, bool) and s.level > 0),
        key=lambda s: (-s.level, s.ship_id),
    )
    slots = sorted((t for t, count in required_types.items() for _ in range(count)), key=lambda t: (t == "NA", t))
    best = None

    def search(index, chosen, used):
        nonlocal best
        if index == len(slots):
            if sum(s.level for s in chosen) < min_total_level:
                return
            leaders = [s for s in chosen if s.level >= min_flagship_level]
            if not leaders:
                return
            leader = min(leaders, key=lambda s: (s.level, s.ship_id))
            rest = sorted((s for s in chosen if s.ship_id != leader.ship_id),
                          key=lambda s: (s.level, s.ship_id))
            ordered = (leader,) + tuple(rest)
            score = (sum(s.level for s in chosen), tuple(s.ship_id for s in ordered))
            if best is None or score < best[0]:
                best = (score, ordered)
            return
        kind = slots[index]
        for ship in pool:
            if (kind == "NA" or ship.ship_type == kind) and ship.ship_id not in used:
                search(index + 1, chosen + [ship], used | {ship.ship_id})

    search(0, [], set())
    if best is None:
        return FleetBuildResult(False, (), "No fleet meets ship-type and level constraints.")
    return FleetBuildResult(True, tuple(s.ship_id for s in best[1]),
                            "Composition candidate only; remaining expedition gates required.")
