# Game Rules Knowledge Base

**Status:** ACTIVE — rule research and evidence registry  
**Scope:** Game mechanics that constrain KancolleBot planning, decisions, and UI automation  
**Last reviewed:** 2026-11-06  
**Current baseline:** Community-maintained Japanese wiki research; current Poi runtime cross-check is incomplete.

## Mandatory development gate

**Every feature that makes a game-state decision MUST be based on the applicable rule records in this directory. Do not infer game mechanics from UI appearance, field names, intuition, or an earlier implementation.**

Before designing or changing code:

1. Identify every affected game domain below.
2. Read its rule file and check each relevant rule's status, source date, conditions, and unresolved conflicts.
3. If a required rule is UNKNOWN, INFERRED, stale, or contradictory, stop that decision path. Gather evidence first; do not silently choose a plausible interpretation.
4. Separate the actual game rule from the automation safety policy. A conservative automation policy must never be described as a game mechanic.
5. Add or update rule records and evidence before relying on newly discovered mechanics in implementation.
6. Add tests for the implementation contract, but do not treat synthetic tests as proof that the game itself follows the rule.
7. After real Poi/UI verification, record the observation scope, game/client version, source signal, timestamp, and limitations.

This gate applies to new code, bug fixes, refactors that alter game decisions, configuration defaults, and production planning.

## Evidence and status vocabulary

| Label | Meaning | May drive production decisions? |
|---|---|---|
| OFFICIAL | Current official game notice/help/UI explicitly establishes the rule | Yes, within stated scope |
| LIVE_VERIFIED | Reproduced against the current game through normal UI and/or read-only Poi evidence, with reproducible record | Yes, within verified scope |
| CROSS_VERIFIED | Official/current source plus independent live verification agree | Yes |
| COMMUNITY_REPORTED | Player-maintained wiki, guide, or community test report | Research input only; conservative blocking may be used if clearly identified as policy |
| INFERRED | Derived from indirect observations or code behavior | No |
| UNKNOWN | Not established | No |
| CONFLICTED | Reliable sources or observations disagree | No, fail closed |

Rule maturity is separate from implementation status. A rule can be well documented but not implemented; code tests do not upgrade a game rule's evidence status.

## Rule record requirements

Each rule record must include:
- Stable ID and concise statement.
- Scope and preconditions (game mode, fleet, phase, ship type, event-only conditions, etc.).
- Evidence status and confidence limitations.
- Source URL, source type, last-modified date if available, and access date.
- Poi/UI reproduction details or explicit statement that no live verification exists.
- Automation consequence, explicitly separated from the game rule.
- Tests or future verification needed.

When a source itself says "under investigation", "estimated", or "not fully verified", preserve that uncertainty. Do not convert it into a deterministic threshold.

## Domain map

| Domain | File | Coverage | Current status |
|---|---|---|---|
| Expeditions | [EXPEDITIONS.md](EXPEDITIONS.md) | Fleet eligibility, mission uniqueness, selected mission catalogue, stat gates, great success, monthly prerequisites and engagement expeditions | Partial; community source researched, live verification pending |
| Battle | [BATTLE.md](BATTLE.md) | Battle node types, phase order, day/night transitions, battle result | Partial; community source researched, live verification pending |
| Formation and battle formations | [FORMATIONS.md](FORMATIONS.md) | Standard formations, selection constraints, combined-fleet formations | Partial; community source researched, live verification pending |
| Fleet composition | [FLEET_COMPOSITION.md](FLEET_COMPOSITION.md) | Fleet slots, expedition eligibility, combined-fleet composition and restrictions | Partial; community source researched, live verification pending |
| Ship condition, supply and fatigue | [SHIP_STATE_SUPPLY_FATIGUE.md](SHIP_STATE_SUPPLY_FATIGUE.md) | Fuel/ammunition, damage, fatigue, condition update timing | Partial; community source researched, live verification pending |
| Equipment and attributes | [EQUIPMENT_AND_ATTRIBUTES.md](EQUIPMENT_AND_ATTRIBUTES.md) | Equipment categories, bonuses, stat aggregation, aircraft and drum-can requirements | Not yet researched; no assumptions permitted |
| Sortie, maps and routing | [SORTIE_AND_MAPS.md](SORTIE_AND_MAPS.md) | Sortie eligibility, map nodes, route branching, retreat/advance decisions | Not yet researched; no assumptions permitted |
| Repairs, recovery and ship loss | [REPAIR_AND_RECOVERY.md](REPAIR_AND_RECOVERY.md) | Repair, damage recovery, retreat, sinking/loss boundaries | Not yet researched; no assumptions permitted |
| Resources, rewards and quests | [RESOURCES_REWARDS_QUESTS.md](RESOURCES_REWARDS_QUESTS.md) | Resource costs, reward settlement, quest prerequisites and reset rules | Partial only where cross-referenced by expedition source; otherwise not researched |

## Source policy

Preferred order:
1. Current official in-game help, notices, and observable game UI.
2. Current live Poi state and passive network evidence, captured without initiating or replaying game-server requests.
3. Reproducible controlled observations through normal game UI.
4. Maintained community wiki and documented player tests.
5. Third-party guides, old forum posts, and inference.

A community wiki is not an official source. Its revision date and its own uncertainty notes must be retained. Old rules must be rechecked against the current game version before they become implementation requirements.

## Global safety boundaries

- Game API traffic may be observed passively only. Do not construct, send, replay, or trigger game-server requests.
- Use normal visible UI for permitted game actions; do not use page reload loops.
- Unknown or stale state must fail closed.
- Never infer a successful dispatch, battle result, return, or reward settlement from a timer or a navigation click alone.
- Do not run a live experiment that risks ship loss, unwanted dispatch, resource consumption, or irreversible state change merely to fill a documentation gap. Prefer passive observation during normal play.

## Update history

### 2026-11-06 — Initial domain registry

- Established this directory as the game-mechanics reference entry point.
- Added research dossiers for expedition, battle, formations, fleet composition, and ship condition/supply/fatigue.
- Current records are explicitly marked as community-source research; current Poi live verification is incomplete.
- Added empty-scope dossiers for equipment/attributes, sortie/maps, repair/recovery, and resources/rewards/quests so missing knowledge is visible rather than silently guessed.

## External structured-data and research reference

The upstream [Yukikaze030/KanColle-Agent](https://github.com/Yukikaze030/KanColle-Agent) repository has been reviewed as a **research architecture and source-discovery reference**, not as a runtime dependency.

- Read [KAN_COLLE_AGENT_REFERENCE.md](KAN_COLLE_AGENT_REFERENCE.md) before using its data-source model.
- It separates current player state (Poi), static master data (Data MCP), and gameplay explanations (Wiki Researcher). These evidence classes must not substitute for one another.
- Its project instructions restrict data and mechanics to **二期 (post-May-2023 server migration)**. KancolleBot must reject一期-only or era-unknown claims as current production assumptions until cross-checked.
- Static data sources are mixed: pinned game master data, community-maintained ship/equipment/quest datasets, and third-party implementation logic. Preserve per-field provenance; do not label the entire dataset "official".
- Third-party schemas, normalizer helpers, tests, and implementation behavior are implementation references, not game rules. In particular, do not copy HP-ratio normalization thresholds as game damage rules.
- Static dataset completeness does not prove live behavior. Poi freshness is per domain; online/health status or a newly generated snapshot timestamp is not sufficient proof of current game state.
- This integration is documentation-only. It does not authorize installing or connecting upstream MCP services or changing the current KancolleBot runtime.


### 2026-11-06 — Expedition Wiki second-pass research

- Expanded `EXPEDITIONS.md` with EXP-015–EXP-018 covering per-ship fuel/ammunition consumption, Fleet 2 and mission unlock prerequisites, reward-item display semantics, and monthly completion cutoff.
- Retained COMMUNITY_REPORTED status; the source is a community wiki and several underlying calculations/tables cite 2020/2021 or 2023 material.
- No current Poi live verification or game operation was performed. No game code may rely on these records as production-grade mechanics until independently verified.


### 2026-11-06 — Expedition mission catalogue

- Added a selected 12-mission catalogue (IDs 2, 3, 5, 6, 9, 11, 13, 17, 20, 21, 24, 41) to `EXPEDITIONS.md`.
- Captured mission duration, supply-consumption rates, flagship/fleet levels, fleet composition, special equipment/stat requirements, and success reward baselines.
- All entries remain COMMUNITY_REPORTED. This is a transcription of a community wiki table, not a current live game verification or an official rules dataset.


### 2026-11-06 — Expedition stat aggregation caveat

- Added EXP-019 to `EXPEDITIONS.md`: selected expedition checks use equipment-inclusive aggregate stats; improvement arithmetic differs from ordinary battle/stat display calculations; aircraft contributions have additional modifiers and unresolved details.
- Production use requires a dedicated calculator with fully specified inputs. Unsupported equipment/aircraft cases remain UNKNOWN and fail closed.
- Evidence remains COMMUNITY_REPORTED; no current Poi or in-game validation was performed.


### 2026-11-06 — Expedition great-success rule classes

- Added EXP-020 to EXPEDITIONS.md, separating ordinary morale-based, drum-can, and flagship-level great-success models.
- Recorded the reported six-sparkled-ship guarantee for ordinary missions, mission-specific drum-can thresholds, and the older flagship-level probability model without promoting any of them to verified production rules.
- Preserved a source conflict for ID 24: the current overview lists 2 drums for the reported guaranteed great-success condition, while an older duplicated page lists 4. The dossier follows the current overview and flags the discrepancy.
- All conditions remain COMMUNITY_REPORTED; no Poi state, current game UI, or live expedition result was checked. No game code changed.


### 2026-11-06 — Additional logistics expedition records

- Added EXP-021 with mission records for IDs 37, 38, 40, 44 and E2: duration, per-ship supply cost, flagship/fleet levels, exact fleet composition, drum-can and aggregate-stat success requirements, and success reward baselines.
- Kept great-success thresholds separate from success requirements and cross-referenced EXP-020.
- Flagged ID 44's source-marked composition ambiguity, the EXP-019 stat-calculation dependency, and E2's engagement-type damage risk. All entries remain COMMUNITY_REPORTED and unavailable as production dispatch authority.
