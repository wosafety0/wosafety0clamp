# Sparkle-Farming Module Plan

Status: OFFLINE_PLANNER_IMPLEMENTED; POI_RUNTIME_PENDING
Branch: `dev/sparkling-planner-mvp`

## Why this work can proceed independently

The expedition module's live integration is blocked on validating the user's local Poi runtime. Sparkle farming can make progress without that runtime by implementing and testing a pure planner against explicit morale snapshots. This does not unblock or replace the Poi integration work.

## Scope of this MVP

- Accept observed ship morale values and an explicit target (default 50).
- Mark unknown morale and caller-designated ineligible ships as blocked.
- Prioritize eligible ships with the lowest observed morale.
- Estimate a bounded number of sorties using a caller-supplied morale-gain estimate.
- Require refreshes between bounded batches; do not treat estimated gains as guaranteed.

## Deliberate non-goals

- No Poi requests, UI clicks, battle commands, map selection, sortie launch, or live game actions.
- No hard-coded claim that each sortie grants a fixed morale amount.
- No assumption that cached morale values represent the current logged-in game session.
- No real-game validation claim.

## Next implementation stages

1. Add a sanitized read-only morale field to the Poi normalization layer after a real payload fixture is captured.
2. Verify whether the target Poi/game state exposes morale directly and establish freshness/session evidence.
3. Add a policy-driven sortie strategy only after source evidence and offline tests establish its constraints.
4. Add bounded action orchestration with cancellation, per-action state refresh, duplicate prevention, and post-action verification.
5. Test locally in read-only mode before enabling any live gameplay action.

## Current evidence

The offline planner and unit tests are source-controlled. Its estimated sortie counts are planning aids, not game-mechanics guarantees. Poi runtime compatibility, live morale normalization, and all actual gameplay actions remain unverified and disabled.
