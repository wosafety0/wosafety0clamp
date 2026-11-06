# Expedition Rules

**Status:** PARTIAL — community-source research; not yet live-verified against the current Poi session  
**Last reviewed:** 2026-11-06  
**Primary source:** Japanese Kancolle Wiki, [遠征](https://wikiwiki.jp/kancolle/遠征), accessed 2026-11-06. The page contains historical research notes and is not an official game specification.

## Rule records

### EXP-001 — Eligible fleet numbers
- **Rule:** Fleet 1 cannot be sent on expeditions. Fleets 2–4 can be used, subject to their unlock state.
- **Evidence:** COMMUNITY_REPORTED.
- **Source:** [遠征 — 仕様](https://wikiwiki.jp/kancolle/遠征), lines 62–65.
- **Automation consequence:** Never schedule Fleet 1 for an expedition. Do not assume Fleet 3 or Fleet 4 is unlocked; read current game state.
- **Live verification:** Not performed for this rule in this archive revision.

### EXP-002 — A mission ID is globally exclusive while assigned
- **Rule:** The same expedition cannot be assigned to multiple fleets simultaneously.
- **Evidence:** COMMUNITY_REPORTED; explicit statement on the source page.
- **Source:** [遠征 — 仕様](https://wikiwiki.jp/kancolle/遠征), lines 63–66.
- **Automation consequence:** A multi-fleet plan must assign distinct mission IDs. Reject duplicate mission IDs before any UI action. Do not invent a result-attribution problem to work around a game rule.
- **Live verification:** Not performed in this archive revision.

### EXP-003 — Flagship damage restriction
- **Rule:** A fleet with a heavily damaged (大破) flagship cannot depart on an expedition. The source states that a heavily damaged escort ship does not itself prevent a normal expedition.
- **Evidence:** COMMUNITY_REPORTED.
- **Source:** [遠征 — 仕様](https://wikiwiki.jp/kancolle/遠征), lines 65–66; [success conditions](https://wikiwiki.jp/kancolle/遠征), lines 222–228.
- **Automation consequence:** The game rule is not the same as the project's risk policy. The automation may apply a stricter rule (for example, block any damaged ship), but must label it as an automation safety policy rather than a game requirement.
- **Live verification:** Not performed in this archive revision.

### EXP-004 — Event fleet-state restrictions
- **Rule:** During an event, Fleet 2 may be unavailable for expeditions while configured as part of a Combined Fleet; Fleet 3 may be unavailable while configured as a Surface Task Force / 遊撃部隊.
- **Evidence:** COMMUNITY_REPORTED.
- **Source:** [遠征 — 仕様](https://wikiwiki.jp/kancolle/遠征), lines 63–67.
- **Automation consequence:** Check current fleet mode and event state; do not treat fleet number alone as sufficient eligibility.
- **Live verification:** Not performed in this archive revision.

### EXP-005 — Basic success requirements
- **Rule:** Basic requirements include flagship level, fleet size, and required ship types. Some missions additionally require total fleet level.
- **Evidence:** COMMUNITY_REPORTED.
- **Source:** [遠征 — 成功条件](https://wikiwiki.jp/kancolle/遠征), lines 195–204.
- **Automation consequence:** Requirements must be mission-specific and evaluated against the fleet at dispatch time. Do not use a single generic minimum-ship-count or flagship-only check.
- **Live verification:** Not performed. Exact current requirements for each mission must be sourced from the mission catalogue and cross-checked.

### EXP-006 — Ship-type categories are not interchangeable
- **Rule:** The source distinguishes categories such as light cruiser vs. torpedo cruiser/training cruiser, heavy cruiser vs. aviation cruiser, battleship vs. aviation battleship, and submarine aircraft carrier vs. submarine tender. "Carrier" and specific carrier subclasses also have distinctions.
- **Evidence:** COMMUNITY_REPORTED; the source includes historical notes and some category definitions may be stale.
- **Source:** [遠征 — 成功条件](https://wikiwiki.jp/kancolle/遠征), lines 201–204.
- **Automation consequence:** Do not infer category equivalence from labels or visual similarity. Use a current authoritative ship-type mapping and mission-specific rule.
- **Live verification:** Not performed.

### EXP-007 — Equipment requirements for selected missions
- **Rule:** Some missions require a minimum number of drum-can-equipped ships and a minimum total number of drum cans. The source lists selected transport missions.
- **Evidence:** COMMUNITY_REPORTED.
- **Source:** [遠征 — 成功条件](https://wikiwiki.jp/kancolle/遠征), lines 205–208.
- **Automation consequence:** Requirements must be attached to the individual mission. No universal drum-can rule may be inferred.
- **Live verification:** Not performed.

### EXP-008 — Aggregate ship attributes for selected missions
- **Rule:** Some missions require aggregate equipment-included firepower, anti-air, anti-submarine, or scouting values. The source notes that equipment bonuses, improvement values, reinforcement expansion equipment, and aircraft stat handling can affect the calculation.
- **Evidence:** COMMUNITY_REPORTED; the source references dated research and special calculation handling.
- **Source:** [遠征 — 成功条件](https://wikiwiki.jp/kancolle/遠征), lines 209–216.
- **Automation consequence:** Do not calculate mission eligibility using bare ship base stats or a generic "total combat power" chart. The exact stat aggregation formula and current master data must be independently validated before coding.
- **Live verification:** Not performed.

### EXP-009 — Fuel and ammunition readiness
- **Rule:** Incomplete fuel or ammunition can contribute to expedition failure. The source explicitly says the exact threshold is not established and reports some successful under-supplied cases.
- **Evidence:** COMMUNITY_REPORTED with source-declared uncertainty.
- **Source:** [遠征 — 成功条件](https://wikiwiki.jp/kancolle/遠征), lines 217–221.
- **Automation consequence:** Full fuel and ammunition before dispatch is a conservative automation policy. Do not claim the game requires 100% supply unless separately verified.
- **Live verification:** Not performed.

### EXP-010 — Fatigue at result confirmation
- **Rule:** The source states that if even one participating ship is orange- or red-fatigued when the expedition result is confirmed, the expedition fails. It also states fatigue can recover during the expedition, so departure-time fatigue alone does not determine the result.
- **Evidence:** COMMUNITY_REPORTED.
- **Source:** [遠征 — 成功条件](https://wikiwiki.jp/kancolle/遠征), lines 222–226.
- **Automation consequence:** Departure-time fatigue and result-time fatigue are separate observations. A result-time fatigue check must use current, refreshed state; do not project the result from the departure snapshot.
- **Live verification:** Not performed. The Poi update timing and exact signal used for result-time condition must be tested.

### EXP-011 — Escort damage and engagement-type expeditions
- **Rule:** The source says escort damage does not prevent normal expedition success if the flagship restriction is satisfied. Engagement-type expeditions are exceptions to the normal no-damage assumption; ships can be damaged, but the source says they do not sink during those expeditions.
- **Evidence:** COMMUNITY_REPORTED.
- **Source:** [遠征 — success conditions and engagement expeditions](https://wikiwiki.jp/kancolle/遠征), lines 222–228 and 307–310.
- **Automation consequence:** Do not treat all expeditions as damage-free. Engagement-type expeditions require a separate risk policy and must not enter a normal unattended loop until independently validated.
- **Live verification:** Not performed.

### EXP-012 — Success, great success, failure, and cancellation rewards
- **Rule:** The source describes different outcomes: success grants mission rewards; great success grants 1.5× resources and 2× Admiral/ship experience plus great-success-only items; failure gives zero resource rewards and reduced Admiral experience while ship experience remains; cancellation gives no resources or experience.
- **Evidence:** COMMUNITY_REPORTED.
- **Source:** [遠征 — success conditions](https://wikiwiki.jp/kancolle/遠征), lines 186–193.
- **Automation consequence:** Result labels must be observed and recorded distinctly. Do not calculate rewards from a generic "completed" flag.
- **Live verification:** Not performed.

### EXP-013 — Great-success conditions differ by expedition class
- **Rule:** The source describes different great-success conditions for ordinary expeditions, drum-can missions, and certain flagship-level / sparkle-count missions. For ordinary missions, six sparkling ships at departure are stated as a guaranteed great success. Special mission classes have their own conditions and tables.
- **Evidence:** COMMUNITY_REPORTED; parts of the source are explicitly based on 2020 research.
- **Source:** [遠征 — 大成功条件](https://wikiwiki.jp/kancolle/遠征), lines 230–279.
- **Automation consequence:** Never apply a universal sparkle-count threshold to every mission. Exact mission class and current formula must be known. Until verified, automation must not promise great success.
- **Live verification:** Not performed.

### EXP-014 — Monthly expedition reset and prerequisites
- **Rule:** The source states monthly expeditions reset at 12:00 on the 15th and some require prerequisite monthly expeditions. It also describes a completion-time cutoff.
- **Evidence:** COMMUNITY_REPORTED; requires current-version verification.
- **Source:** [遠征 — マンスリー遠征](https://wikiwiki.jp/kancolle/遠征), lines 281–305.
- **Automation consequence:** Do not schedule monthly missions based only on a calendar assumption. Read the in-game availability/completion state.
- **Live verification:** Not performed.

## Lifecycle facts that still require live confirmation

1. Which current Poi fields represent expedition status, mission ID, start time, and return time, and which are refreshed by a natural port response?
2. At what exact UI transition does the fleet become available after the timer ends?
3. Can multiple returning fleets produce sequential result pages, and how is each page tied to its fleet?
4. Which signal proves result acceptance, bonus-item completion, supply completion, and final Home Port state?
5. How does the current game client expose result-time fatigue and ship state after a return?
6. How are cancellations, timeouts, event restrictions, and engagement-type expeditions represented?

## Implementation gate

- A unique mission ID per active expedition is mandatory.
- A timer reaching zero is not proof of return or settlement.
- A navigation click is not proof of result acceptance.
- A result page must be uniquely attributed to the correct fleet; ambiguity means UNKNOWN and fail closed.
- Mission-specific ship count, ship types, level, stat, and equipment requirements must be represented explicitly.
- No production use of unverified formulas or stale mission tables.

### EXP-015 — Expedition fuel and ammunition consumption
- **Rule:** The detailed table states that fuel and ammunition consumption are calculated as a percentage of each participating ship's amount at departure, with fractional amounts rounded down. The percentage differs by expedition and ship; steel and bauxite are not listed as expedition operating costs. The page warns that deliberately departing with low fuel/ammunition can reduce consumption but is likely to cause failure.
- **Evidence:** COMMUNITY_REPORTED; the wiki notes display-meter edge cases and historical observations.
- **Source:** [遠征 — overview](https://wikiwiki.jp/kancolle/遠征), lines 32–35; [遠征/詳細一覧表](https://wikiwiki.jp/kancolle/遠征/詳細一覧表), lines 33–35.
- **Automation consequence:** Cost estimation must use the actual per-ship departure supply and mission consumption rate, not a fleet-wide fixed amount. Do not intentionally under-supply to optimize costs. Preserve the existing full-supply pre-dispatch safety policy.
- **Live verification:** Not performed; compare Poi supply values before and after a naturally completed expedition before implementing accounting.

### EXP-016 — Fleet 2 unlock and expedition discovery chain
- **Rule:** The wiki states Fleet 2 is unlocked by completing quest A4 (「6隻編成の艦隊を編成せよ！」), after which expeditions become available. Most missions appear by completing lower-numbered prerequisite missions, but the page lists exceptions involving sortie-area clears and monthly prerequisites.
- **Evidence:** COMMUNITY_REPORTED. The detailed prerequisite table cites the English wiki as accessed 2023-05-16; the current Japanese page contains the summary and table.
- **Source:** [遠征 — 開放要件](https://wikiwiki.jp/kancolle/遠征), lines 73–80 and 81–155.
- **Automation consequence:** Fleet unlock and mission availability must be read from current game state. A locally reconstructed prerequisite graph may explain availability but must not override the in-game mission list. Do not assume all missions unlock through simple numeric sequence.
- **Live verification:** Not performed; verify only by observing current UI state during normal play.

### EXP-017 — Expedition reward-item display semantics
- **Rule:** The wiki reports that an item displayed alone or on the left side of a multi-item reward display is obtained randomly regardless of success/great-success; an item displayed on the right side of a multi-item display is guaranteed only on great success. The exact number of items can itself be random.
- **Evidence:** COMMUNITY_REPORTED.
- **Source:** [遠征 — 仕様](https://wikiwiki.jp/kancolle/遠征), lines 68–71.
- **Automation consequence:** Reward prediction must distinguish guaranteed base rewards, random bonus drops, and great-success-only rewards. Do not treat every icon on the expedition detail screen as guaranteed.
- **Live verification:** Not performed; settlement logs should record the actual result label and actual received items separately from predicted rewards.

### EXP-018 — Monthly expedition completion cutoff
- **Rule:** The wiki reports monthly expeditions reset at 12:00 on the 15th, and that the expedition's completion time (not merely dispatch time or reward-claim time) must fall within the monthly window. Some monthly missions require prerequisite monthly missions to be completed again after reset.
- **Evidence:** COMMUNITY_REPORTED; the page dates the monthly expedition feature to 2019 and includes historical reset notes.
- **Source:** [遠征 — マンスリー遠征](https://wikiwiki.jp/kancolle/遠征), lines 281–305.
- **Automation consequence:** A monthly mission must not be scheduled near the cutoff based solely on dispatch eligibility. Use the current in-game monthly state and leave a safety margin; do not infer a reset from local calendar time alone.
- **Live verification:** Not performed; current server reset behavior and UI status remain to be confirmed.

## Mission catalogue — selected expedition records

**Source:** [Japanese Kancolle Wiki — 遠征/詳細一覧表](https://wikiwiki.jp/kancolle/遠征/詳細一覧表), accessed 2026-11-06. The entries below transcribe selected rows for planning and research. The page is community-maintained; table freshness and underlying formulas are not independently verified. All records below are **COMMUNITY_REPORTED**, not production-authoritative.

| Mission ID | Name | Duration | Fuel / ammo cost | Flagship Lv / fleet total Lv | Fleet composition | Rewards on success |
|---|---|---:|---|---|---|---|
| 2 | 長距離練習航海 | 00:30 | 50% / 0% | 2 / — | At least 4 ships; any types | Ammo 100, steel 30, repair bucket 0–1; 20 admiral EXP |
| 3 | 警備任務 | 00:20 | 30% / 20% | 3 / — | At least 3 ships; any types | Fuel 30, ammo 30, steel 40; 30 admiral EXP |
| 5 | 海上護衛任務 | 01:30 | 50% / 0% | 3 / — | At least 4; light cruiser 1, destroyer/escort 2, other 1 | Fuel 200, ammo 200, steel 20, bauxite 20; 40 admiral EXP |
| 6 | 防空射撃演習 | 00:40 | 30% / 20% | 4 / — | At least 4 ships; any types | Bauxite 80, furniture box (small) 0–1; 30 admiral EXP |
| 9 | タンカー護衛任務 | 04:00 | 50% / 0% | 3 / — | At least 4; light cruiser 1, destroyer/escort 2, other 1 | Fuel 350, furniture box (small) 0–1, repair bucket 0–2; 60 admiral EXP |
| 11 | ボーキサイト輸送任務 | 05:00 | 50% / 0% | 6 / — | At least 4; destroyer/escort 2, other 2 | Bauxite 250, furniture box (small) 0–1, repair bucket 0–1; 40 admiral EXP |
| 13 | 鼠輸送作戦 | 04:00 | 50% / 40% | 5 / — | Exactly 6; light cruiser 1, destroyer 4, other 1 | Fuel 240, ammo 300, repair bucket 0–2, furniture box (small) 0–1; 70 admiral EXP |
| 17 | 敵地偵察作戦 | 00:45 | 30% / 40% | 20 / — | Exactly 6; light cruiser 1, destroyer 3, other 2 | Fuel 70, ammo 90, steel 50; 30 admiral EXP |
| 20 | 潜水艦哨戒任務 | 02:00 | 50% / 40% | 1 / — | At least 2; submarine 1, light cruiser 1 | Steel 150, development material 0–1, furniture box (medium) 0–1; 40 admiral EXP |
| 21 | 北方鼠輸送作戦 | 02:20 | 80% / 70% | 15 / 30 | At least 5; light cruiser 1, destroyer 4; at least 3 ships carry at least 3 drum cans total | Fuel 320, ammo 270, furniture box (small) 0–1; 45 admiral EXP |
| 24 | 北方航路海上護衛 | 08:20 | 90% / 60% | 50 / 200 | Exactly 6; light cruiser must be flagship, destroyer/escort 4, other 1; at least 2 drum cans total | Fuel 500, bauxite 150, development material 0–2, repair bucket 0–1; 65 admiral EXP |
| 41 | ブルネイ泊地沖哨戒 | 01:00 | 50% / 50% | 30 / 100 | At least 3; destroyer/escort 3; aggregate firepower ≥60, AA ≥80, ASW ≥210 | Fuel 100, bauxite 20, development material 0–1, repair bucket 0–1; 30 admiral EXP |

**Interpretation and limitations**

1. Cost percentages are applied per participating ship to its departure-time remaining fuel/ammunition, with fractional amounts rounded down (the wiki notes exceptions for support-fleet expeditions). Do not estimate costs by applying the percentage once to a fleet total.
2. “Other” means an unrestricted slot only where the row says so. It does not permit replacing a specifically required ship class with a similar class.
3. “Destroyer/escort” follows the wiki's stated combined category for the relevant mission. Do not generalize this to all expedition requirements.
4. Mission 21 and 24 have drum-can conditions in addition to ship composition. Mission 41 has equipment-included aggregate stat thresholds; aircraft stats have special modifiers described elsewhere on the source page.
5. Resource rewards above are success baselines, not great-success predictions. Random item drops and great-success-only items must be tracked separately (see EXP-017).
6. The table includes entries whose source notes cite older research and historical updates. Before any production use, compare the mission row against current in-game mission details and current Poi master/player data. A table transcription test can verify our parser, not prove game mechanics.

**Automation gate:** These rows are planning references only. Do not dispatch based solely on this table. A production candidate must additionally pass current mission availability, fleet eligibility, live ship identity/type mapping, per-domain Poi freshness, damage/fatigue/supply policy, and exact equipment/stat calculation. Unknown or stale inputs must block the action.


### EXP-019 — Equipment-inclusive expedition stat aggregation
- **Rule:** For selected expeditions, the success check uses fleet aggregate firepower, anti-air, anti-submarine, and/or scouting values with equipment included. The wiki states that equipment improvement bonuses contribute, but expedition improvement arithmetic differs from ordinary battle/stat display arithmetic. Aircraft-derived values can receive special modifiers involving aircraft type, slot aircraft count, and proficiency; the wiki says parts of this handling remain unclear or under investigation.
- **Evidence:** COMMUNITY_REPORTED; the source's detailed explanation contains explicit investigation caveats and dated research notes.
- **Source:** [遠征 — 成功条件 and stat calculation notes](https://wikiwiki.jp/kancolle/遠征), [detailed mission table](https://wikiwiki.jp/kancolle/遠征/詳細一覧表), accessed 2026-11-06. The page lists the stat-gated mission set and gives examples such as A2/A3/A4/A5/A6, B1–B6, 41, 43–46, D1–D3, E1–E2.
- **Reported improvement handling:** The page describes per-equipment improvement contributions, summed across the fleet, with fractional precision; examples include firepower improvements from small/medium guns and anti-submarine improvements from sonar/depth-charge equipment. Do not assume the standard combat formula applies.
- **Reported aircraft handling:** Aircraft stat contributions may be affected by slot aircraft count and proficiency. The wiki notes that aircraft-derived ASW has partially described special handling and that aircraft are not recommended as a way to fine-tune a fleet just above a threshold while the formula remains unclear.
- **Automation consequence:** A stat-gated mission must be represented as a mission-specific set of thresholds and a separately versioned expedition-stat calculator. Do not reuse battle stat aggregation, raw ship master stats, UI-visible sums, or the shared KanColle-Agent HP normalizer as substitutes. If an aircraft/equipment contribution cannot be calculated with a fully specified supported rule, mark the result UNKNOWN and block dispatch.
- **Live verification:** Not performed. No current Poi equipment slots, improvement values, aircraft proficiency, or mission result were inspected in this pass. The formula must be independently validated against the current client before production use.


### EXP-020 — Great-success determination classes
- **Rule class A — ordinary expeditions:** The maintained Japanese wiki reports that a fleet meeting the expedition success conditions and departing with all six ships in the sparkled / high morale state achieves great success with 100% probability. It estimates approximately 95% with five sparkled ships and approximately 80% with four; these reduced-fleet probabilities are community estimates, not deterministic guarantees. The relevant state is the departure-time condition: sparkle disappearing by return does not invalidate a great-success result.
- **Rule class B — drum-can expeditions:** The wiki describes a separate model for IDs 21, 24, 37, 38, 40, 44 and E2. Its current table lists the following reported guaranteed combinations, in addition to each mission's normal success conditions:

| Mission ID | Name | Sparkled ships | Ships carrying drums | Total drums |
|---|---|---:|---:|---:|
| 21 | 北方鼠輸送作戦 | 4 | 3 | 4 |
| 24 | 北方航路海上護衛 | 4 | 1 | 2 |
| 37 | 東京急行 | 4 | 3 | 5 |
| 38 | 東京急行(弐) | 4 | 4 | 10 |
| 40 | 水上機前線輸送 | 4 | 1 | 4 |
| 44 | 航空装備輸送任務 | 4 | 3 | 8 |
| E2 | 強行鼠輸送作戦 | 4 | 3 | 6 |

  The source's theoretical probability table says the higher drum threshold plus four or more sparkled ships reaches at least 100%; extra drums beyond that threshold do not increase the reported probability. It also reports a lower, non-guaranteed great-success chance when drums exceed the mission's success minimum without meeting the higher great-success threshold. Treat the probability model as community research, not a production-grade formula.
- **Rule class C — flagship-level expeditions:** The wiki reports a separate probability model for a subset of missions: 16 + 15 × sparkled_ship_count + (sqrt(flagship_level) + flagship_level / 10) percent. It states that, when normal success conditions are met, flagship Lv128 with at least four sparkled ships, or flagship Lv33 with at least five sparkled ships, reaches a reported 100% great-success threshold even if the remaining ships are not sparkled. The candidate mission list includes A2–A6, B3–B5, 41, 43, 45, 46, D1, D2 and E1; the source marks some entries with question marks and dates this applicability list to 2020-09-17. Do not assume this model applies to every listed mission without newer corroboration.
- **Reward effect:** The same page reports great success gives 1.5× resource rewards, 2× admiral/ship experience, and guarantees the right-side item in a multi-item reward display. This is separate from random item drops and base success rewards (see EXP-017).
- **Evidence:** COMMUNITY_REPORTED. The page is community-maintained. Its drum-can probability model cites a 2020 investigation; the flagship-level formula cites 2019 community research and the applicability list is explicitly dated 2020-09-17. A separate older duplicated page has a conflicting ID 24 drum count (4 rather than 2); this dossier follows the current maintained overview's table (2) and records the conflict rather than silently merging the values.
- **Sources:** [遠征 — success and great-success conditions](https://wikiwiki.jp/kancolle/遠征), especially lines 230–278; [older duplicated page](https://wikiwiki.jp/kancolle/遠征/複製) for the conflicting historical ID 24 value. Accessed 2026-11-06.
- **Automation consequence:** Great-success prediction must be a separate, mission-class-aware calculation from expedition success eligibility. Inputs include the exact fleet roster, departure-time morale per ship, flagship level, per-ship drum-can count, and number of ships carrying drums. Store the source rule/version and predicted probability alongside the decision. Do not promise a guaranteed great success for a candidate with an uncertain class, stale morale, ambiguous drum placement, or conflicting mission data. A conservative policy may require six verified sparkled ships for ordinary missions; this is an automation policy, not a universal game requirement.
- **Live verification:** Not performed. No current Poi morale, equipment layout, mission-specific result, or controlled great-success outcome was inspected. No dispatch or game-state change was performed.


### EXP-021 — Additional logistics and engagement mission records
**Evidence:** COMMUNITY_REPORTED; transcribed from the maintained Japanese detailed mission table accessed 2026-11-06. These records are not live-verified.

| Mission ID | Name | Duration | Fuel / ammo cost | Flagship Lv / fleet total Lv | Success composition and special conditions | Success baseline rewards |
|---|---|---:|---|---|---|---|
| 37 | 東京急行 | 02:45 | 80% / 80% | 50 / 200 | Exactly 6: light cruiser 1 + destroyer 5; no free slot. At least 3 ships carry at least 4 drums total. | Ammo 380, steel 270 |
| 38 | 東京急行(弐) | 02:55 | 80% / 80% | 65 / 240 | Exactly 6: destroyer 5 + other 1. At least 4 ships carry at least 8 drums total. | Fuel 420, steel 200 |
| 40 | 水上機前線輸送 | 06:50 | 80% / 70% | 25 / 150 | Exactly 6: light cruiser fixed as flagship 1, seaplane tender 2, destroyer 2, other 1. At least 4 drums total. | Fuel 300, ammo 300, bauxite 100 |
| 44 | 航空装備輸送任務 (monthly) | 10:00 | 80% / 40% | 35 / 210 | Exactly 6: carrier-category 1 (source says carrier / seaplane tender / escort carrier can fill this slot), seaplane tender 1, light cruiser 1, destroyer/escort 2, other 1. At least 3 ships carry at least 6 drums total; aggregate AA ≥200, ASW ≥200, scouting ≥150. | Ammo 200, bauxite 800 |
| E2 | 強行鼠輸送作戦 (monthly, engagement type II) | 03:05 | 90% / 95% | 70 / 320 | At least 5, all 5 required destroyers. At least 3 ships carry at least 4 drums total; aggregate firepower ≥280, AA ≥240, ASW ≥200, scouting ≥160. | Ammo 480; repair bucket 0–2; improvement material 0–1 |

**Separate great-success thresholds (not success requirements):** ID 37: at least 4 sparkled ships and at least 5 drums total on at least 3 ships; ID 38: at least 4 sparkled ships and at least 10 drums total on at least 4 ships; ID 40: at least 4 sparkled ships and at least 4 drums total (the great-success summary table specifies at least 1 drum-carrying ship); ID 44: at least 4 sparkled ships and at least 8 drums total on at least 3 ships; E2: at least 4 sparkled ships and at least 6 drums total on at least 3 ships. These are community-reported combinations and inherit EXP-020's source-age and probability caveats.

**Important qualifications:**
1. ID 37 has no free fleet slot; do not treat its fifth destroyer as an interchangeable class.
2. ID 40 requires a light cruiser flagship and two seaplane tenders; a similar carrier class is not a substitute.
3. ID 44's source itself labels part of the carrier/seaplane-tender slot interpretation as requiring verification. Keep that composition branch UNKNOWN until current UI or controlled evidence confirms it.
4. ID 44 is monthly and the source lists A4 completion as its appearance prerequisite. ID E2 is monthly and engagement type II; the overview says engagement expeditions can cause ship damage (but not sinking). Neither should enter unattended dispatch until damage, repair, fatigue and monthly cutoff handling are separately verified.
5. Stat-gated missions require the EXP-019 calculator. Aircraft modifiers and improvement arithmetic remain unresolved; the aggregate thresholds above are not sufficient to authorize dispatch.

**Sources:** [遠征/詳細一覧表](https://wikiwiki.jp/kancolle/遠征/詳細一覧表), ID 37/38/40 lines 300–314, ID 44 lines 218–220, ID E2 lines 320–322; [遠征 — great-success and engagement rules](https://wikiwiki.jp/kancolle/遠征), lines 235–278 and 307–319. Accessed 2026-11-06.
**Live verification:** Not performed. No current mission UI, Poi fleet/equipment state, or expedition result was inspected. No game state or runtime code changed.


### EXP-022 — Advanced stat-gated, monthly, and engagement expedition records

**Status:** COMMUNITY_REPORTED; transcribed from the maintained Japanese wiki on 2026-11-06; not live-verified. This record expands the mission catalogue for stat-gated expeditions and missions with restrictive flagship/fleet compositions. Values below are source transcriptions, not production authorization.

| Mission ID | Name | Duration | Fuel / ammo | Flagship Lv / fleet total Lv | Composition and success conditions | Special status |
|---|---|---:|---:|---:|---|---|
| A2 | 海峡警備行動 | 00:55 | 50% / 20% | 20 / — | At least 4; destroyer/escort 4; firepower ≥50, AA ≥70, ASW ≥180 | — |
| A3 | 長時間対潜警戒 | 02:15 | 60% / 30% | 35 / 185 | At least 5; light cruiser 1, destroyer/escort 3, other 1; ASW ≥280, scouting ≥60 | — |
| A4 | 南西方面連絡線哨戒 | 01:50 | 75% / 60% | 40 / 200 | At least 5; light cruiser 1, destroyer 2, other 2; firepower ≥300, AA ≥200, ASW ≥200, scouting ≥120 | Monthly |
| A5 | 小笠原沖哨戒線 | 03:00 | 80% / 75% | 45 / 230 | At least 5; light cruiser 1, destroyer 3, other 1; firepower ≥280, AA ≥220, ASW ≥240, scouting ≥150 | Monthly, engagement type I |
| A6 | 小笠原沖戦闘哨戒 | 03:30 | 90% / 95% | 55 / 290 | Exactly 6; light cruiser 1, destroyer 3, other 2; firepower ≥330, AA ≥300, ASW ≥270, scouting ≥180 | Monthly, engagement type II |
| B3 | 南西諸島離島哨戒作戦 | 02:50 | 65% / 80% | 50 / 250 | Exactly 6; seaplane tender 1, light cruiser 1, destroyer/escort 4; firepower ≥400, AA ≥220, ASW ≥220, scouting ≥190 | Monthly |
| B4 | 南西諸島離島防衛作戦 | 07:30 | 85% / 85% | 55 / 300 | Exactly 6; heavy cruiser 2, light cruiser 1, destroyer 2, submarine 1; firepower ≥500, AA ≥280, ASW ≥280, scouting ≥170 | Monthly, engagement type I; no free slot |
| B5 | 南西諸島捜索撃滅戦 | 06:30 | 90% / 95% | 60 / 330 | Exactly 6; seaplane tender 1, light cruiser 1, destroyer 2, other 2; firepower ≥510, AA ≥400, ASW ≥285, scouting ≥385 | Monthly, engagement type II |
| B6 | 精鋭水雷戦隊夜襲 | 05:50 | 95% / 95% | 75 / 400 | Exactly 6; light cruiser fixed as flagship 1, destroyer 5; firepower ≥410, AA ≥390, ASW ≥410, scouting ≥340 | Monthly, engagement type II; no free slot |
| 42 | ミ船団護衛(一号船団) | 08:00 | 80% / 65% | 45 / 200 | At least 4; light cruiser 1, destroyer 2, other 1; source also lists alternative escort compositions | Monthly |
| 43 | ミ船団護衛(二号船団) | 12:00 | 85% / 90% | 55 / 300 | Exactly 6; escort carrier fixed as flagship with 2 destroyers or 2 escort ships plus 3 others; alternatively light carrier fixed as flagship, light cruiser 1, destroyer 4; firepower ≥500, AA ≥280, ASW ≥280, scouting ≥170 | Monthly, engagement type I |
| 45 | ボーキサイト船団護衛 | 03:20 | 60% / 40% | 50 / 240 | At least 5; escort carrier or light carrier fixed as flagship 1, destroyer/escort 4; AA ≥240, ASW ≥300, scouting ≥180 | — |
| 46 | 南西海域戦闘哨戒 | 03:30 | 75% / 95% | 60 / 300 | At least 5; heavy cruiser 2, light cruiser 1, destroyer 2; firepower ≥350, AA ≥250, ASW ≥220, scouting ≥190 | Monthly, engagement type II |
| D1 | 西方海域偵察作戦 | 02:00 | 75% / 50% | 50 / 200 | At least 5; seaplane tender fixed as flagship 1, destroyer 3, other 1; AA ≥240, ASW ≥240, scouting ≥300 | Source marks part of composition as requiring verification |
| D2 | 西方潜水艦作戦 | 10:00 | 90% / 90% | 55 / 270 | At least 5; submarine tender (not submarine aircraft carrier) fixed as flagship 1, submarine 3, other 1; firepower ≥60, AA ≥80, ASW ≥50, scouting ≥70 | Monthly, engagement type I |
| D3 | 欧州方面友軍との接触 | 12:00 | 90% / 95% | 65 / 350 | At least 5; submarine tender (not submarine aircraft carrier) fixed as flagship 1, submarine 3, other 1; firepower ≥115, AA ≥90, ASW ≥70, scouting ≥95 | Monthly, engagement type II; great-success note is marked uncertain by source |
| E1 | ラバウル方面艦隊進出 | 07:30 | 95% / 85% | 55 / 290 | Exactly 6; heavy cruiser fixed as flagship 1, light cruiser 1, destroyer 3, other 1; firepower ≥450, AA ≥350, ASW ≥330, scouting ≥250 | Monthly, engagement type II |

**Prerequisite and reset observations (community-reported):**
- A2 requires ID 8 and A1; A3 requires A2 and ID 24; A4 requires A3.
- A5 requires A4 and B3 to be completed in the current monthly cycle; A6 requires A5 and B4 in the current monthly cycle.
- B3 follows B2 monthly; B4 follows B3 monthly; B5 follows B4 monthly. B6 requires A4 and B4 in the current monthly cycle.
- ID 43 follows ID 42 in the monthly cycle. ID 44 requires IDs 40 and 41 once, plus A4 in the current monthly cycle. ID 45 requires IDs 32 and 41. ID 46 requires D1.
- D1 follows ID 32; D2 follows D1; D3 follows D2 in the current monthly cycle. E1 follows ID 40.
- The wiki describes monthly reset at the 15th day, 12:00. Do not derive eligibility from the local clock: use the current mission list and in-game completion state.

**Critical interpretation and safety constraints:**
1. Every aggregate stat threshold is equipment-inclusive. EXP-019 remains unresolved: improvement contribution arithmetic differs from ordinary battle stats, and aircraft contributions may depend on slot count/proficiency. A threshold printed here is not computable until a validated mission-aware calculator exists.
2. Ship categories must remain exact. In particular, light cruiser excludes torpedo cruiser/training cruiser; heavy cruiser excludes aviation cruiser; submarine tender is not submarine aircraft carrier; escort carrier and light carrier are not interchangeable unless the mission row explicitly permits either.
3. Fixed flagship restrictions and no-free-slot compositions are hard constraints in the source table. Do not fill a slot by approximate class matching.
4. A5/A6/B4/B5/B6/43/46/D2/D3/E1 are engagement-type or high-risk missions according to the source. Engagement expeditions may damage ships; exclude them from unattended dispatch until damage, post-return inspection, repair/recovery, and monthly-cycle handling are independently verified.
5. D1's composition and D3's great-success note retain source uncertainty. Unknown branches must stay UNKNOWN.
6. These records are research references only. Current mission visibility, monthly completion status, actual fleet roster, per-domain Poi freshness, ship master types, equipment, morale and damage must be independently checked before any decision.

**Sources:** [Maintained Japanese expedition overview](https://wikiwiki.jp/kancolle/遠征), accessed 2026-11-06, especially the prerequisite table and monthly/engagement rules; [detailed expedition table](https://wikiwiki.jp/kancolle/遠征/詳細一覧表), accessed 2026-11-06, rows A2–A6, B3–B6, 42–46, D1–D3 and E1. The overview's prerequisite table itself cites English-wiki material accessed 2023-05-16; retain that age limitation for the unlock graph. All claims remain COMMUNITY_REPORTED.

**Live verification:** Not performed. No current Poi mission list, fleet/equipment state, monthly completion flags, or expedition outcome was inspected. No game state or runtime code changed.

### EXP-023 — Stat aggregation and equipment-improvement calculation boundaries

- **Rule:** For stat-gated expeditions, the wiki says to sum each ship's equipment-inclusive firepower, anti-air, anti-submarine and/or scouting stat. It explicitly warns that the expedition selection screen's overall-strength chart is not the value to use. Equipment bonuses and reinforcement-expansion equipment are included.
- **Timing caveat:** The wiki reports that reinforcement-expansion equipment can be installed or changed while a fleet is away, potentially changing a near-threshold expedition to success; removing relevant equipment can cause failure on return. The exact server sampling point is not specified. Do not assume the departure-time equipment snapshot alone is sufficient, and do not mutate equipment during an automated expedition.
- **Improvement handling reported by the source:** improvement contributions are accumulated per equipped item, retained to one decimal place (the source table says truncate below the second decimal), and added to the relevant stat. Examples and the partially investigated table include:
  - Firepower: small-calibre main guns, secondary guns, small radars and several other categories use reported coefficients such as 0.5 × sqrt(stars); medium/large main guns and large radars use sqrt(stars).
  - Anti-air: high-angle guns and anti-air machine guns are reported as sqrt(stars); some equipment categories are explicitly listed as having no contribution, while other rows remain blank/unresearched.
  - Anti-submarine: sonar, depth-charge projectors and depth charges are reported as sqrt(stars). Aircraft contributions depend on the aircraft's base ASW and are also subject to aircraft-slot modifiers.
  - Scouting: water reconnaissance aircraft and radar have reported improvement contributions, but aircraft-slot/proficiency handling remains more complex.
- **Aircraft handling:** The wiki says aircraft stat contributions can depend on aircraft type, slot count and proficiency; firepower is described as normally added, while scouting receives slot/proficiency modifiers. For aircraft ASW with internal proficiency 0, the source reports floor(base_ASW × 0.65) for a one-aircraft slot and floor(base_ASW × (0.65 + sqrt(slot_count - 2) × 0.1)) for slots of two or more aircraft. It does not provide a complete proficiency-inclusive formula. A visible maximum proficiency mark (>>) does not reveal the exact internal value; the source reports a range of 100–120, so threshold-edge estimates are unsafe.
- **Evidence:** COMMUNITY_REPORTED; the wiki labels this entire calculation section as under investigation. Its tables contain blank/unresearched cells and aircraft formulas cite community tests and spreadsheets. Do not promote the partial coefficient table into a complete formula.
- **Automation consequence:** A production stat calculator needs per-equipment-type rules, improvement level, equipment bonuses, reinforcement slot, aircraft type, aircraft slot count and the best available proficiency evidence. It must preserve decimal arithmetic and the source's stated truncation behavior. Unsupported or ambiguous rows return UNKNOWN, not zero. Until validated, use a conservative margin or block stat-gated dispatch; never rely on the expedition UI aggregate chart.
- **Mutation policy:** Freeze fleet equipment for the entire expedition lifecycle. If external/manual equipment changes are detected while a stat-gated fleet is away, mark the predicted outcome UNKNOWN and require the actual settlement result; do not auto-correct by changing gear mid-expedition.
- **Sources:** [遠征 — stat-gated expedition improvement and aircraft handling](https://wikiwiki.jp/kancolle/遠征), accessed 2026-11-06, lines 209–215 and 339–425. The source itself labels the calculation as under investigation.
- **Live verification:** Not performed. No Poi equipment inventory, improvement values, reinforcement slots, aircraft slot counts/proficiency, or controlled threshold experiment was inspected. No game code or game state changed.

### EXP-024 — Profitability ranking and combat expedition risk

**Evidence:** COMMUNITY_REPORTED; research only; no Poi/live verification.

**Routine resource farming — headline gross hourly rates**
| Resource | Mission | Gross rate | Main constraints |
|---|---|---:|---|
| Fuel | 38 東京急行(弐) | 144/h | Flagship Lv65, total Lv240, 5 destroyers, 8 drums on 4+ ships |
| Fuel alternative | 21 北方鼠輸送作戦 | 137/h | Flagship Lv15, total Lv30, light cruiser + 4 destroyers, drum requirements |
| Fuel accessible | 5 海上護衛任務 | 133/h | Flagship Lv3, accepted 4-ship composition |
| Ammunition | 2 長距離練習航海 | 200/h | 30 min, 4 ships |
| Steel | 3 警備任務 | 120/h | 20 min, 3 ships |
| Bauxite | 6 防空射撃演習 | 120/h | 40 min, 4 ships |

These are gross rates, not net profit. A weighted community score uses fuel + ammo + steel + 3×bauxite per hour; the 1:1:1:3 weighting is a convenience, not an official value. The scheduler must prioritize actual resource deficits and subtract fleet supply, UI turnaround, and any other explicit costs.

**Net-value model:** keep a resource vector: rewards minus departure supply/replenishment minus expected repair costs and other explicit costs. Only calculate a scalar score after applying user-selected resource weights. Keep ordinary success and great-success scenarios separate if probability is unknown. Do not double-count mission consumption and replenishment.

**Combat damage**
- Type I: maintained wiki reports 0–3 ships may return with up to moderate-damage-class damage.
- Type II: multiple heavily damaged ships are not uncommon; sixth-slot damage is reportedly lighter, but no reliable per-ship probability distribution or guaranteed safe slot exists.
- Damage can reach 1 HP, but the wiki reports combat expeditions do not sink ships.
- Higher stats reportedly reduce damage. The 2025 player report documents damage at 217%, 218%, and 219.1% of requirements, then no damage in 17 Type II runs at 220%. This is anecdotal, not a guarantee; older 217% claims conflict with it. Mark the safe threshold CONFLICTED.
- A player's 2025 monthly route was resource-positive with its own ships and assumptions, but its screenshots were described as old. Do not generalize its aggregate result.

**Fatigue**
- Ordinary expedition return: reported -3 condition, independent of duration.
- Damaged Type I ships: additional random -5 to -10; damaged Type II ships: additional random -10 to -18. Source ranges contain uncertainty markers.
- Natural recovery: reported +3 every 3 minutes, capped at 49, including while away or in repair. Repair completion reportedly sets condition to 40.
- Orange/red fatigue at result confirmation can cause expedition failure. Read actual condition; never infer it only from elapsed time.
- Damage and extra fatigue are reported to occur even on combat-expedition failure. Abort reportedly avoids both, but gives no reward and is not a routine recovery mechanism.

**Repair economics**
Repair material cost is ship-specific coefficient × lost HP, rounded down; ship level does not affect material cost. Repair time separately scales with level, class multiplier and lost HP. Representative steel/fuel coefficients per HP: destroyer 0.9/0.48; light cruiser 1.5/0.80; heavy cruiser 2.4/1.28; aviation cruiser 3.0/1.60; submarine 0.6/0.32; Yamato-class 15.0/8.0; Musashi Kai Ni 16.5/8.8. These are examples, not class-wide defaults. Use the exact ship-specific row. Track steel, fuel, dock occupancy and buckets as separate costs.

**Automation policy**
1. Default unattended allowlist: non-combat expeditions selected by resource deficit and net value; consider IDs 2, 3, 5, 6, 21, 37 and 38 when all exact requirements pass.
2. Keep every Type I/II combat expedition blocked from unattended dispatch. They may be presented for user-reviewed monthly execution only.
3. The 220% Type II stat target is a supervised candidate filter, never a no-damage guarantee.
4. Do not automatically sacrifice ships or assign expensive ships to slot six based only on community tendencies.
5. After every return, re-read HP, condition, supplies, repair status and fleet membership. Quarantine damaged ships until repair/recovery is verified.
6. Combat missions cannot be declared profitable while damage distribution and expected repair cost are UNKNOWN.

**Sources:** [KouryakuLab requirements](https://kouryakulab.com/ja/games/kancolle/expedition-requirements/), [efficiency calculator](https://kouryakulab.com/ja/games/kancolle/expeditions/), [Japanese expedition wiki](https://wikiwiki.jp/kancolle/遠征), [fatigue wiki](https://wikiwiki.jp/kancolle/疲労度), [repair wiki](https://wikiwiki.jp/kancolle/入渠), [2025 monthly expedition report](https://senka.hatenadiary.jp/entry/2025/07/21/002334), [secondary combat guide](https://gameranbu.jp/kancolle/dc96d7cc9fa3e0ea09e3). Accessed 2026-11-06 except the player report (updated 2025-07-20) and secondary guide (updated 2021-02-07). All are community sources.

**Live verification:** Not performed. No current Poi data, damage test, repair-dock state, or game operation was inspected.
