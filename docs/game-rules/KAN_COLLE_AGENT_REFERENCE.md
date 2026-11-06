# KanColle-Agent Reference for Game-Mechanics Research

**Status:** RESEARCH WORKFLOW REFERENCE — upstream documentation inspected; no runtime integration performed  
**Reviewed:** 2026-11-06  
**Upstream:** [Yukikaze030/KanColle-Agent](https://github.com/Yukikaze030/KanColle-Agent) — `main`  
**Purpose:** Define how KancolleBot may use the upstream project's data and research architecture without confusing player state, static master data, community mechanics, and automation policy.

## 1. What this upstream project contributes

Its documented separation is useful and should be adopted as a research discipline:

| Layer | Answers | KancolleBot use | Does not prove |
|---|---|---|---|
| Poi MCP | What does this player currently have / what state is Poi observing? | Read-only player snapshot: ships, equipment, fleets, quests, resources, operations, freshness | That every domain is fresh merely because Poi is online |
| KanColle Data MCP | What is present in the static game-data dataset? | Resolve stable ship/equipment/item/quest/expedition/map refs; inspect master attributes, remodel transitions, equipment rules and structured prerequisites | That every dataset field is a complete game rule or current live behavior |
| Wiki Researcher | What are the documented mechanics / how is a system played? | Research exact mechanics, edge cases, formulas, current maps and changes | Official truth; wiki claims remain source-attributed and may be stale |
| KancolleBot policy | What is safe for this automation to do with current evidence? | Conservative gates, state machine, action authorization and fail-closed handling | A game rule; policy must be labelled separately |

**Adopt the separation, not the upstream runtime wholesale.** KanColle-Agent documents a read-only Poi MCP and a separate local static Data MCP. KancolleBot's existing Poi/CDP adapter and normal visible-UI action boundary remain authoritative for this project. This reference does not authorize installing or connecting the upstream MCP services, changing the current adapter, or importing their code.

## 2. Era/version gate — 二期 only

The upstream repository's `AGENTS.md`, main Skill, and Wiki Researcher instructions explicitly restrict decisions to **二期**, defined there as after the May 2023 server migration.

Rules for this project:

1. A mechanic or data row must identify its applicable era. Use 二期 as the default target for current KancolleBot work.
2. Do not use一期 data, old formulas, pre-migration route tables, or legacy quest conditions as current decision inputs.
3. If a source mixes eras or does not state its era, isolate the relevant passage and mark it `UNKNOWN` / requiring cross-check; do not silently assume 二期.
4. Record game data version, source revision/commit, source URL, and access date whenever available.
5. A dataset generated today from a pinned old source is still an old snapshot. Generation time is not upstream freshness.

## 3. Data-source provenance reported by upstream

The upstream `docs/DATA_MCP.md` describes these inputs:

| Data area | Reported source | How to use it |
|---|---|---|
| Ship master records, remodel links, items, selected remodel costs | Pinned `api/api_start2.json` | Structural/static facts; retain the exact pinned source revision |
| Equipment and supplemental ship data | `kcwiki/kancolle-data` JSON data | Community-maintained structured data; not automatically official |
| Quests | `kcwikizh/kcwiki-quest-data` npm data | Quest identity, prerequisite and reward data; verify source version and missing fields |
| Ship types, equipment eligibility, expeditions and maps | Pinned `api_start2.json` | Static master data; does not alone establish every operational success condition |
| Remodel calculation rules | Pinned KC3Kai `RemodelDb.js` calculation functions | Third-party implementation evidence; preserve license, source pin and hash; independently cross-check |
| Improvement schedules | `fleet.diablohu.com/arsenal/` cached data | Third-party, time-dependent data; preserve date/timezone and source status |
| Normal map guide slices | Upstream skill reference Markdown | Community guide content; use only for the matching map/version |

The upstream documentation reports one snapshot containing 862 ship forms, 555 remodel transitions, 105 items, 741 equipment records, 446 quests, 65 expeditions and 42 maps. These are **reported snapshot counts**, not independently fetched or verified by KancolleBot. Query the upstream `kc_data_status` in its own environment before relying on current counts or capabilities.

### Provenance requirements

- Prefer exact master IDs / refs over name matching.
- Distinguish `instance_id` (a player's owned instance) from `master_id` (a static game entity).
- Preserve source-level `coverage`, `missing`, `warnings`, `provenance`, and partial/ambiguous result semantics.
- A missing field, unsupported type, ambiguous name, or absent source record is not zero and is not proof that the game entity/rule does not exist.
- `coverage=complete` means only that the fields modelled by that dataset are present. It is not live verification, proof of completeness of the game itself, or a guarantee that the pinned source is current.
- For directed remodels, costs belong to a specific `from → to` transition. Do not infer costs from a destination form, a flattened chain, or a minimum remodel level.
- Do not collapse items and equipment into one namespace. Preserve exact item/equipment refs and names.
- For equipment eligibility, distinguish normal slots from expansion slots; if the static rule is partial or level-dependent, retain the unknown and require the appropriate player-state evidence.

## 4. Poi state and freshness requirements

The upstream Poi MCP design separates player snapshots from static master data. It documents domain freshness, coverage, snapshot versions and explicit unknown states.

Apply these principles to KancolleBot:

- Player-owned state must come from the current Poi observation, not static master data or model memory.
- Static master data must not be used to fabricate player ownership, fleet membership, current equipment, resources, or quest progress.
- Check freshness per affected domain (fleets, ships, equipment, resources, quests, operations); do not infer all-domain freshness from a single online/health flag.
- Preserve `unknown`, `not_loaded`, `partial`, `stale`, and `not_ready` as distinct states. They are not equivalent to empty, zero, false, absent, or incomplete in-game.
- A snapshot timestamp generated by a reader is not proof that the underlying Redux/API data changed or refreshed. Require a verified refresh boundary and source signal.
- Keep stable player-instance identity separate from master identity. A ship name or master ID alone cannot uniquely attribute a player-owned ship.
- Use current Poi values for current HP, supply, fatigue, fleet assignment, expedition state, sortie state and quest progress; verify the actual field mapping and refresh semantics against this project's current Poi version before production use.

### Important anti-pattern: do not copy normalizer heuristics as game rules

The upstream shared type source contains a helper that maps `hp / max_hp` ratios into labels such as healthy, shouha, chuha and taiha. That is a **software normalization helper**, not by itself an authoritative statement of the game's damage thresholds or UI semantics. Do not copy its numeric boundaries into KancolleBot rule dossiers unless independently established by current game evidence and reliable sources.

Likewise, a type definition or example snapshot establishes a software contract, not necessarily the game's full mechanics.

## 5. Mechanism research workflow

For every mechanic that can affect a game-state decision:

1. **Name the domain and decision.** Example: expedition eligibility, mission success, result settlement, battle phase, formation availability, fleet composition, equipment compatibility, repair, or quest unlock.
2. **Read this project's domain dossier** under `docs/game-rules/`. Identify rule IDs and evidence gaps.
3. **Resolve entities through static data** where applicable: exact ship/equipment/item/quest/expedition/map IDs and source revision. Do not use display-name guesses.
4. **Read current player state through Poi** where applicable. Check domain freshness, coverage, missing fields, and identity attribution.
5. **Research the mechanic** in current 二期 sources. Prefer official current notices/UI; then independent live observation; then maintained community wiki and reproducible player tests. Compare Chinese, Japanese and English sources when ambiguity or conflict warrants it.
6. **Separate evidence classes.** Static master data, community wiki claims, live Poi observations, synthetic tests and automation policy are different evidence types.
7. **Record before implementation.** Update the applicable dossier with a stable rule ID, era, preconditions, source revision/date, evidence status, observed signal, limitations, automation consequence and tests needed.
8. **Block unresolved decisions.** UNKNOWN, INFERRED, stale, partial or CONFLICTED data cannot silently drive a production action. Use a conservative policy only when explicitly labelled as policy.
9. **Test the software contract.** Unit/integration tests prove our code handles known inputs; they do not prove the game mechanic.
10. **Recheck after game/data updates.** A source revision change, game update, new ship/equipment, event rule, or Poi adapter change can invalidate earlier assumptions.

## 6. Evidence labels and their correct use

Use the evidence vocabulary defined in `docs/game-rules/README.md`. Add these source descriptors to each record where useful:

- `OFFICIAL`: current official notice/help/UI directly supports the exact claim.
- `LIVE_VERIFIED`: current normal UI and/or passive Poi evidence reproduces the claim, with timestamp, version and reproducible signal.
- `CROSS_VERIFIED`: independent current sources and live evidence agree.
- `COMMUNITY_REPORTED`: community wiki/guide/player test; research input only.
- `IMPLEMENTATION_REFERENCE`: a third-party software implementation or data schema describes how that project models something. This is **not** a game-rule evidence level and cannot independently authorize a game decision.
- `INFERRED`, `UNKNOWN`, `CONFLICTED`: do not use as production game-rule assumptions.

## 7. Security and operational boundaries

- The upstream documentation describes Poi MCP as read-only. Preserve KancolleBot's own rule: game API observation is passive only; do not construct, send, replay or trigger game-server requests.
- Do not connect an unreviewed service to the live game or expose local MCP endpoints beyond loopback.
- Do not copy access tokens, cookies, DMM sessions, or credentials into source, logs, issues or progress records.
- Do not enable upstream agent write/game-action capabilities; the upstream README explicitly says V1 does not implement automatic game actions.
- No live experiment may risk ship loss, unwanted dispatch, resource consumption or irreversible state changes merely to fill a documentation gap.

## 8. Upstream references inspected

All were read from the upstream `main` branch on 2026-11-06:

- [README.md](https://github.com/Yukikaze030/KanColle-Agent/blob/main/README.md)
- [AGENTS.md](https://github.com/Yukikaze030/KanColle-Agent/blob/main/AGENTS.md)
- [System design](https://github.com/Yukikaze030/KanColle-Agent/blob/main/KanColle%20AI%20Agent%20完整系统设计文档.md)
- [Data MCP](https://github.com/Yukikaze030/KanColle-Agent/blob/main/docs/DATA_MCP.md)
- [Poi MCP](https://github.com/Yukikaze030/KanColle-Agent/blob/main/docs/POI_MCP.md)
- [MCP tools](https://github.com/Yukikaze030/KanColle-Agent/blob/main/docs/MCP_TOOLS.md)
- [Skills](https://github.com/Yukikaze030/KanColle-Agent/blob/main/docs/SKILLS.md)
- [Main Skill](https://github.com/Yukikaze030/KanColle-Agent/blob/main/.opencode/skills/kancolle-main/SKILL.md)
- [Wiki Researcher](https://github.com/Yukikaze030/KanColle-Agent/blob/main/.opencode/agents/kcwiki-researcher.md)
- [Shared game/player data types](https://github.com/Yukikaze030/KanColle-Agent/blob/main/packages/shared/src/types.ts)

**Inspection limits:** this was a remote documentation/source inspection. KancolleBot did not install, run, connect to, or independently validate KanColle-Agent's MCP servers or static dataset in this task. No current Poi state was queried and no game action was performed.
