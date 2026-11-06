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
