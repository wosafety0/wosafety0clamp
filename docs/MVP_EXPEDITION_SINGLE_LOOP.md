# Single Expedition MVP — Execution Contract

Status: ACTIVE (2026-10-26)
Branch: dev/expedition-closed-loop-mvp

## One acceptance criterion
One fixed fleet performs one fixed expedition, with observed transitions:
ready -> dispatched -> completed -> reward collected -> ready.

## Strict scope
1. Read the fixed fleet's expedition state from Poi.
2. Identify readiness and expedition-in-progress/completed states using observed fields.
3. Identify the shortest feasible action mechanism for one dispatch and one reward collection.
4. Implement guarded single-dispatch and reward collection only after separate user authorization for live game actions.
5. Observe return to ready; stop. No continuous re-dispatch is required.

## Stop conditions
Unknown or stale state, mismatched fleet/expedition, ambiguous completion, unavailable action interface, or any unexpected response -> STOP without game actions.

## Current evidence and blockers
- Historical Poi read-only evidence: info.fleets (3 fleets) and info.ships (91 ships), positive fleet-ship references matched.
- Existing Python MCP client is read-only; poi_get is allowlisted; poi_hensei_save is explicitly excluded.
- Fleet/ship reference consistency is not expedition-state recognition.
- Real dispatch and reward-collection action interfaces: NOT_VERIFIED.
- End-to-end live expedition loop: NOT_VERIFIED.
- Test execution in this work round: NOT_RUN.
- No game actions authorized or executed.

## Immediate next implementation task
Find the exact existing Poi expedition-state fields and response envelope in archived observed evidence; implement only the minimal read-only fixed-fleet state classifier grounded in those fields. If evidence is insufficient, request one passive capture during a manually operated expedition. Do not guess field semantics.

## Explicitly out of scope
AI model comparisons, generalized Bot reverse engineering, multi-fleet scheduling, optimal expeditions, battle automation, generic frameworks, extensive security investigations, or GitHub Actions.

## Workflow
Cloud GitHub first; no local checkout required. Read before write. Record verified commits and unresolved blockers; never claim unrun tests passed.

## 2026-10-26 — Read-only evidence checkpoint
- GitHub code search on the repository default-branch index for `api_mission`, `expedition`, and `poi_get` returned no matches. This search is not exhaustive for this development branch.
- Mainline MCP client reviewed directly: `poi_get` is allowlisted; no dispatch or reward-collection tool is implemented.
- No field-level observed before/after expedition evidence was established in this round.
- Decision: do not infer expedition state from unverified field semantics. Next obtain minimal passive observations of the same fixed fleet before dispatch, after manual dispatch, and after manual reward collection. Capture only relevant status fields, redact identifiers/secrets, and do not issue game actions.
- Test execution: NOT_RUN. Game actions: DISABLED. No local checkout required.

## 2026-10-26 — Batched local verification workflow
- User reports Poi is logged in.
- Default workflow: continue cloud-side development and progress logging on `dev/expedition-closed-loop-mvp`; do not require a local repository unless a test specifically needs the Windows/Poi runtime.
- Batch local checks only when they are necessary and ready; prefer passive/read-only observation. After the batch, return to the GitHub mainline, record actual outcomes, and verify remote readback.
- Keep local test results, GitHub commits, and real-game validation as separate statuses. Never report a test as passed without execution evidence.
- MVP scope remains unchanged: one fixed fleet, one fixed expedition, one complete loop. Poi login alone does not authorize game actions.
