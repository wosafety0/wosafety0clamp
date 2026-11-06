# Equipment and Attribute Rules

**Status:** NOT_STARTED — no game-rule assumptions are authorized from this file  
**Last reviewed:** 2026-11-06

## Research scope

- Equipment categories and ship-slot compatibility.
- Equipment bonuses, remodel-specific bonuses, and improvement contributions.
- Firepower, torpedo, anti-air, anti-submarine, scouting, armor, evasion, accuracy, range, speed, and air power.
- Aircraft slot count, proficiency, plane losses, and resupply behavior.
- Drum cans, landing craft, special equipment, and mission-specific requirements.
- Poi master-data fields versus ship-instance fields and their update behavior.

## Required evidence

For every rule, capture the exact ship/equipment class, remodel, equipment slot, game mode, and calculation context. Cross-check current master data and at least one current game/UI observation where the mechanic affects an automated decision.

## Prohibited assumptions

- Do not assume displayed equipment bonuses are added identically in every calculation.
- Do not infer stat formulas from a single displayed total.
- Do not assume equipment category equivalence from a name or icon.
- Do not implement mission or battle eligibility using unverified master-data field names.
