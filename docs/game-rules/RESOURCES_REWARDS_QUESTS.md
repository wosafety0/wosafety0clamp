# Resources, Rewards and Quest Rules

**Status:** PARTIAL — expedition reward behavior is cross-referenced; other mechanics not yet researched  
**Last reviewed:** 2026-11-06

## Current coverage

See [EXPEDITIONS.md](EXPEDITIONS.md), rules EXP-012 and EXP-014, for community-reported expedition outcome rewards and monthly expedition timing.

## Research scope

- Resource and item costs for supply, repair, construction, development, and sortie.
- Reward-screen ordering, item acquisition, result confirmation, and resource propagation.
- Quest prerequisites, counters, completion timing, daily/weekly/monthly reset rules.
- Resource caps, overflow, and reward loss conditions.
- Poi data paths and refresh timing for resources, items, and quest state.

## Required evidence

Keep game rules, displayed reward text, and Poi resource readback as separate evidence. Record exact before/after values and any delayed propagation. Do not attribute unexplained resource deltas to a preceding UI action.

## Prohibited assumptions

- Do not infer successful reward collection from closing a popup.
- Do not infer a resource delta from an animation or expected reward table.
- Do not infer quest completion from a counter change unless the completion state is observed.
