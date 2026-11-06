# Sortie and Map Rules

**Status:** NOT_STARTED — no game-rule assumptions are authorized from this file  
**Last reviewed:** 2026-11-06

## Research scope

- Sortie eligibility and map unlock prerequisites.
- Map node types, route branching, random routing, and route-control conditions.
- Normal sortie, event sortie, Combined Fleet, and other special sortie modes.
- Continue/retreat decisions, large-damage safeguards, and map completion.
- Air-raid, radar-shooting, night-battle, transport, and other special nodes.
- Map progress, gauge, boss, and event-lock behavior.

## Required evidence

Record each map and node condition separately. Distinguish fixed map rules, event-specific rules, random outcomes, and player-side configuration. Preserve the current event/map version and the exact UI state used for verification.

## Prohibited assumptions

- Do not infer route probability from one or two runs.
- Do not assume a map's node sequence is fixed unless the current map rule proves it.
- Do not treat a battle result as map completion without the relevant gauge/clear evidence.
- Do not automate advance/retreat until the damage, battle-result, and node-transition rules are documented.
