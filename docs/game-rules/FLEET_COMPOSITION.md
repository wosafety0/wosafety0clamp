# Fleet Composition Rules

**Status:** PARTIAL — community-source research; not yet live-verified against the current Poi session  
**Last reviewed:** 2026-11-06  
**Primary sources:** Japanese Kancolle Wiki, [遠征](https://wikiwiki.jp/kancolle/遠征) and [連合艦隊](https://wikiwiki.jp/kancolle/連合艦隊), accessed 2026-11-06.

## Rule records

### FLEET-001 — Four numbered fleets have different roles
- **Rule:** The expedition source states that Fleet 1 cannot be sent on expeditions and Fleets 2–4 can be expedition fleets, subject to unlock and event-state restrictions.
- **Evidence:** COMMUNITY_REPORTED.
- **Source:** [遠征](https://wikiwiki.jp/kancolle/遠征), lines 62–67.
- **Automation consequence:** Fleet number and current role are distinct facts. Never assume that every fleet can perform every operation.

### FLEET-002 — Combined Fleet uses Fleet 1 and Fleet 2
- **Rule:** The source describes a Combined Fleet as up to 12 ships across Fleet 1 and Fleet 2. Only Fleets 1 and 2 can form it; Fleet 3 and Fleet 4 cannot be included. It is available only for eligible maps and cannot be used for expeditions or exercises.
- **Evidence:** COMMUNITY_REPORTED; the source links official staff tutorial posts for the basic assembly.
- **Source:** [連合艦隊](https://wikiwiki.jp/kancolle/連合艦隊), lines 1–11 and 27–39.
- **Automation consequence:** Combined Fleet mode changes fleet eligibility and battle behavior. Detect it explicitly; do not interpret Fleet 1 and Fleet 2 as independent normal fleets while the Combined Fleet state is active.

### FLEET-003 — Combined Fleet has multiple composition classes
- **Rule:** The source distinguishes Carrier Task Force (空母機動部隊), Surface Task Force (水上打撃部隊), and Transport Escort (輸送護衛部隊), each with different ship-type and fleet-slot restrictions.
- **Evidence:** COMMUNITY_REPORTED.
- **Source:** [連合艦隊](https://wikiwiki.jp/kancolle/連合艦隊), lines 8–11 and composition sections.
- **Automation consequence:** Composition validation must be keyed by Combined Fleet type and map requirements. Do not build one generic 12-ship validator.

### FLEET-004 — Combined Fleet roles are asymmetric
- **Rule:** The source assigns different combat responsibilities to Fleet 1 and Fleet 2; attack phases and order differ by Combined Fleet type.
- **Evidence:** COMMUNITY_REPORTED.
- **Source:** [連合艦隊](https://wikiwiki.jp/kancolle/連合艦隊), lines 13–26.
- **Automation consequence:** The fleet slots are not interchangeable. Tactical logic must use the Combined Fleet type and the correct fleet role.

## Unresolved composition rules — implementation prohibited until researched

- Current unlock conditions for Fleet 3 and Fleet 4.
- Complete normal-fleet composition and ship-slot constraints for each sortie mode.
- Event-specific fleet tags, map lock rules, and restrictions.
- Exact ship-type identity mapping, including remodels and special subclasses.
- Fleet preset behavior, ship duplication constraints, and composition-change side effects.
- How Poi represents Combined Fleet mode and whether its snapshot is fresh.

## Implementation gate

A composition validator must consume explicit, current ship-type metadata and a mode-specific requirement set. Never infer a ship class from a display name, portrait, or approximate category. If fleet mode or ship type is unknown, block the action.
