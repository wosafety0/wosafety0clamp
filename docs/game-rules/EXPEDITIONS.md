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
