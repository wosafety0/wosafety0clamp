# Battle Rules

**Status:** PARTIAL — community-source research; not yet live-verified against the current Poi session  
**Last reviewed:** 2026-11-06  
**Primary source:** Japanese Kancolle Wiki, [戦闘について](https://wikiwiki.jp/kancolle/戦闘について), accessed 2026-11-06. The source explicitly warns that some values and mechanics remain under investigation.

## Rule records

### BTL-001 — Battle node types are not interchangeable
- **Rule:** The source distinguishes normal battle nodes, opening-night-battle nodes, dawn-battle nodes, air-battle nodes, air-raid nodes, radar-shooting nodes, and other special forms. Their phase sequences and available actions differ.
- **Evidence:** COMMUNITY_REPORTED.
- **Source:** [戦闘について — 戦闘マスと戦闘形態](https://wikiwiki.jp/kancolle/戦闘について), lines 19–77.
- **Automation consequence:** A battle controller must identify the current node type before interpreting the battle screen or selecting an action. Unknown node type means no automated tactical choice.

### BTL-002 — Normal battle phase order
- **Rule:** For a normal battle, the source describes a phase sequence including scouting, possible jet/base-aircraft attacks, air battle, support, opening anti-submarine attack, opening torpedo, engagement form, shelling (possibly two rounds), torpedo phase, night-battle decision, friendly fleet, and night battle.
- **Evidence:** COMMUNITY_REPORTED; exceptions are explicitly documented.
- **Source:** [戦闘について — 戦闘の流れ](https://wikiwiki.jp/kancolle/戦闘について), accessed 2026-11-06.
- **Automation consequence:** Do not implement a single linear phase model for all nodes. The phase machine must be conditional on node type, fleet type, and available systems.

### BTL-003 — Special node phase exceptions
- **Rule:** The source states that opening-night nodes omit several normal daytime phases; air-battle nodes omit shelling and torpedo phases; air-raid nodes are defensive and end without the ordinary battle sequence; radar-shooting nodes enter combat without a formation-selection screen.
- **Evidence:** COMMUNITY_REPORTED.
- **Source:** [戦闘について](https://wikiwiki.jp/kancolle/戦闘について), lines 29–77 and 43–70.
- **Automation consequence:** UI navigation and state detection must not wait for a phase that does not exist on the current node. Do not infer a missing screen is a UI failure until node type is known.

### BTL-004 — Battle result is separate from phase completion
- **Rule:** The source treats victory rank and battle outcome as separate result concepts, and details battle-specific result conditions elsewhere.
- **Evidence:** COMMUNITY_REPORTED; detailed victory conditions not yet researched in this dossier.
- **Source:** [戦闘について](https://wikiwiki.jp/kancolle/戦闘について).
- **Automation consequence:** Reaching the end of the attack animation is not proof of a particular victory rank, map clear, or safe advance condition. Exact result rules require a dedicated record before implementing decisions.

## Unresolved battle rules — implementation prohibited until researched

- Exact victory-rank conditions (S/A/B/C/D/E), including enemy fleet types and special battle modes.
- Day-to-night transition and the night-battle continue/retreat choice.
- Damage calculation, hit/evasion, critical, armor, and damage-state thresholds.
- Opening air battle, air superiority, anti-air cut-in, support fleets, friendly fleets, and base air groups.
- Submarine targeting and anti-submarine eligibility.
- Combined-fleet phase order and special attacks.
- Large-damage retreat and escort-retreat rules.
- Current event-only mechanics and version changes.

## Implementation gate

Battle automation must be a state machine based on the actual node type and current visible/observed state. It must not infer battle success from animation completion, enemy disappearance alone, a timer, or an assumed phase order. Until the relevant rules are researched and live-verified, battle decision-making remains out of scope.
