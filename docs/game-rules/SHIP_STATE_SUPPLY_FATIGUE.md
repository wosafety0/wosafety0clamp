# Ship State, Supply and Fatigue Rules

**Status:** PARTIAL — community-source research; not yet live-verified against the current Poi session  
**Last reviewed:** 2026-11-06  
**Primary sources:** Japanese Kancolle Wiki, [疲労度](https://wikiwiki.jp/kancolle/疲労度) and [遠征](https://wikiwiki.jp/kancolle/遠征), accessed 2026-11-06.

## Rule records

### SHIP-001 — Fatigue is represented by condition state
- **Rule:** The source describes condition values and visible states including sparkling / morale-high, normal, light fatigue, orange fatigue, and red fatigue. Exact numeric boundaries are community-derived estimates, not directly exposed as an official game value.
- **Evidence:** COMMUNITY_REPORTED; numeric mapping explicitly marked as estimated by the source.
- **Source:** [疲労度](https://wikiwiki.jp/kancolle/疲労度), lines 10–38.
- **Automation consequence:** Use visible state only where its current semantics are validated. Do not manufacture exact condition values from an icon unless the mapping is independently established.

### SHIP-002 — Home Port display refresh affects fatigue visibility
- **Rule:** The source states fatigue status is updated for display when the Home Port screen is shown; to inspect the latest state, navigate to Home Port and then revisit Formation or Modernization.
- **Evidence:** COMMUNITY_REPORTED.
- **Source:** [疲労度](https://wikiwiki.jp/kancolle/疲労度), lines 14–20.
- **Automation consequence:** A Poi snapshot captured before the relevant refresh cannot automatically be treated as the latest displayed fatigue. Record observation time and refresh boundary.

### SHIP-003 — Natural fatigue recovery is time-based but not immediately reflected
- **Rule:** The source describes natural recovery over time and states that the visible update is associated with returning to Home Port. The precise condition-value calculations are community research.
- **Evidence:** COMMUNITY_REPORTED.
- **Source:** [疲労度](https://wikiwiki.jp/kancolle/疲労度), accessed 2026-11-06.
- **Automation consequence:** Do not assume that waiting a fixed interval alone proves the refreshed fatigue state. Read the current game state after the relevant UI refresh.

### SHIP-004 — Expedition fuel and ammunition
- **Rule:** The expedition source says reduced fuel/ammunition can contribute to failure, but its exact threshold is unknown; it recommends supplying before departure. Reduced aircraft count is stated not to prevent success.
- **Evidence:** COMMUNITY_REPORTED with explicit uncertainty.
- **Source:** [遠征](https://wikiwiki.jp/kancolle/遠征), lines 217–221.
- **Automation consequence:** Full supply before expedition is a conservative automation policy, not a confirmed universal game threshold.

### SHIP-005 — Expedition fatigue is evaluated at result confirmation
- **Rule:** The expedition source states that one orange- or red-fatigued participating ship at result confirmation causes failure. Fatigue may recover during the expedition, so dispatch-time and result-time fatigue must not be conflated.
- **Evidence:** COMMUNITY_REPORTED.
- **Source:** [遠征](https://wikiwiki.jp/kancolle/遠征), lines 222–226.
- **Automation consequence:** Use a fresh result-time observation; do not predict success from the dispatch snapshot.

### SHIP-006 — Damage and expedition eligibility
- **Rule:** The expedition source states a heavily damaged flagship blocks expedition dispatch, while a heavily damaged escort does not block a normal expedition. Engagement-type expeditions can cause damage.
- **Evidence:** COMMUNITY_REPORTED.
- **Source:** [遠征](https://wikiwiki.jp/kancolle/遠征), lines 65–66 and 222–228; lines 307–310.
- **Automation consequence:** Keep game eligibility separate from the bot's stricter safety policy. Engagement-type missions require a separate policy.

## Unresolved state rules — implementation prohibited until researched

- Exact Poi fields and update triggers for ship fuel, ammunition, HP, fatigue, morale, and repair state.
- Whether info.ships values update immediately, only after a port response, or through another event.
- Exact damage thresholds and their current UI representation.
- How repair completion, repair buckets, and fatigue interact.
- Whether all fatigue-related display updates require the same Home Port transition in the current client.
- How ship state changes are represented during event/engagement expeditions.

## Implementation gate

State used for dispatch, battle, supply, or recovery must have explicit freshness evidence. A timestamp created by the reader is not itself proof that the underlying game store was refreshed. Unknown state must fail closed.
