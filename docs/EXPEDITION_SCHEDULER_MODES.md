# Expedition Scheduler Modes

**Status:** USER-DIRECTED PRODUCT REQUIREMENTS / DESIGN CONTRACT; documented, not implemented or runtime-tested.  
**Priority:** Fixed per-fleet assignment is the first scheduler mode to deliver. Resource-adaptive planning is a later mode.

## 1. Goals and mode separation

The scheduler shall support two explicit operating modes. They must not be blended into one implicit policy:

1. **Fixed Assignment Mode (P0):** The operator explicitly assigns a mission to each fleet. Example:
   - Fleet 2 → Expedition A
   - Fleet 3 → Expedition B
   - Fleet 4 → Expedition C

   The scheduler's job is to execute and maintain those assignments safely. It must not replace the selected mission with another mission merely because another mission has better resource yield.

2. **Resource-Adaptive Mode (P1):** The planner evaluates the current resource inventory and configured resource targets/weights, then chooses suitable missions to address the current resource balance. This mode is developed only after Fixed Assignment Mode is accepted.

Mode selection must be explicit and persisted as configuration. No silent fallback from one mode to the other is allowed.

## 2. Fixed Assignment Mode — P0

### 2.1 Assignment configuration

- Store assignments by stable fleet identity and stable mission ID, not display-name text alone.
- Support independent mappings for Fleet 2, Fleet 3 and Fleet 4; the example above is illustrative, not a hard-coded default.
- Allow an operator to leave a fleet unassigned/disabled.
- Validate that the target fleet is unlocked, exists, and has a current, complete identity snapshot.
- Validate that the selected mission exists, is currently available, and is not hidden behind an unmet prerequisite.
- Validate the mission's exact fleet composition, flagship, level, equipment, aggregate-stat, supply and other researched requirements before dispatch.
- Preserve the assignment across scheduler restarts. Configuration changes must be explicit and auditable.
- A fleet's assignment is stable: temporary ineligibility pauses that fleet; it does not cause the scheduler to silently choose a substitute mission.

### 2.2 Runtime behavior

- Each fleet has its own desired mission assignment and lifecycle state.
- The scheduler may track multiple fleets concurrently, but all game UI operations remain serialized through the single UI arbiter. It must never assume that three fleets can interact with the shared game screen simultaneously.
- Register all due/pending fleet intents before the serial drain phase. The arbiter then handles one UI transaction at a time.
- A fleet with an active expedition is observed until its exact assigned mission return is verified. Only then may its settlement, resupply and next dispatch lifecycle proceed.
- A fleet is not considered dispatched merely because a navigation or button click occurred. Verify the exact mission tuple and ordered ship roster from fresh game state.
- Timer expiry alone does not establish return. Require fresh post-deadline state and exact mission/return verification.
- One fleet's blocked or failed transaction must not corrupt another fleet's durable state. Shared UI contention must be handled by the arbiter, not concurrent clicks.
- Keep per-fleet lifecycle and action journals, plus a shared serialized UI action journal.

### 2.3 Safety filters still apply

Fixed assignment means the user chooses the mission; it does not bypass safety or game-rule validation.

- Combat/engagement expeditions (including Type I and Type II) remain excluded by default. They must not be started merely because a mission ID appears in a stored mapping. Any combat mission requires the separate explicit opt-in and authorization workflow defined in EXP-025.
- Unknown, stale, incomplete or conflicting fleet/mission data fails closed.
- No dispatch with unresolved fleet identity, unverified mission classification, missing requirements, unsafe damage state, insufficient supplies, or stale state.
- Do not send, construct or replay game-server requests. Use the reviewed visible UI path only.
- No page-reload loop or blind retries.
- A failed/ambiguous dispatch must be reconciled against fresh game state before any retry.

## 3. Resource-Adaptive Mode — P1

This mode chooses missions dynamically based on the current resource inventory rather than a fixed fleet-to-mission mapping.

Inputs must include:
- Current fuel, ammunition, steel and bauxite inventory with per-domain freshness and completeness.
- Operator-configured target inventory, reserve floor and/or deficit weights for each resource.
- Mission reward vector and duration, with ordinary-success and great-success scenarios kept separate.
- Per-ship departure supply cost and the actual supply-restoration cost needed for the configured next-cycle baseline.
- Expected UI/settlement/resupply turnaround time, fleet availability, mission eligibility and prerequisite state.
- Repair, fatigue, bucket and other costs only where supported by sufficiently reliable data. Unknown costs must remain UNKNOWN, not zero.
- Explicit constraints such as disabled missions, fleet-to-mission restrictions, and the combat-expedition exclusion policy.

Selection behavior:
- Rank eligible missions by their marginal contribution to the configured resource deficits and net value, not by a fixed universal gross-yield leaderboard.
- Do not overproduce a resource already above its configured target while leaving a critical resource below reserve, unless the operator explicitly configures that priority.
- Consider the whole fleet assignment set so multiple fleets do not all choose the same resource when another deficit is more urgent.
- Keep deterministic tie-breaking and record the inputs, rule version, candidate rejections and reason for the chosen mission.
- Recalculate only from fresh, complete inventory and fleet state. If a required input is stale or UNKNOWN, pause adaptive selection rather than guessing.
- The adaptive planner may recommend a mission, but it cannot bypass the same dispatch safety gates used by Fixed Assignment Mode.

## 4. Shared scheduler invariants

- The two modes share the same mission catalogue, fleet eligibility evaluator, freshness gates, durable intent registry, lifecycle state machine, UI arbiter, action journal and settlement verification.
- Mode-specific planning must not duplicate or bypass the dispatch controller's safety gates.
- Every dispatch intent records the mode, fleet ID, selected mission ID, assignment/config revision, decision reason and relevant state snapshot/freshness evidence.
- Every UI action and state transition is logged with timestamp, reason, observed precondition, action result and postcondition.
- Startup recovery must reconcile persisted intents against fresh live game state before resuming. Never blindly replay a previously persisted click.
- The first acceptance target is Fixed Assignment Mode for multiple fleets, with no assumption that all three fleets can be dispatched in parallel through the UI.

## 5. Delivery order and acceptance

### P0 — Fixed Assignment Mode

1. Define and validate a versioned configuration schema for per-fleet mission assignments.
2. Implement per-fleet independent desired-state planning and due-intent registration.
3. Reuse the shared serial UI arbiter and existing durable lifecycle/dispatch/return/settlement gates.
4. Add offline tests for multiple assignments, disabled/unassigned fleets, invalid mission IDs, mission requirement failures, combat mission rejection, stale data, restart recovery, UI serialization and one fleet failing while another remains intact.
5. Run CI and review the handoff/operational configuration.
6. Only after offline acceptance, perform separately authorized supervised live tests. Do not claim unattended acceptance from offline tests.

### P1 — Resource-Adaptive Mode

1. Define configurable resource targets/reserves/weights.
2. Implement resource-vector net-value scoring with explicit UNKNOWN handling.
3. Add multi-fleet joint allocation and deterministic tie-breaking.
4. Test changing inventory, competing deficits, overstock, insufficient fleet eligibility, stale inputs, and mission/repair cost uncertainty.
5. Compare recommendations with observed inventory deltas during supervised operation before enabling automatic adaptive dispatch.

## 6. Current implementation status

- Requirements captured from the operator on 2026-11-06.
- Existing codebase already contains a scheduler/supervisor, durable fleet lifecycle components, shared UI arbitration and a production Fleet 3 / mission 3 default. These existing components must be inspected and reused; this document does not assert that multi-fleet fixed assignment is already implemented.
- Fixed Assignment Mode: **PLANNED / NOT IMPLEMENTED / NOT TESTED**.
- Resource-Adaptive Mode: **PLANNED / NOT IMPLEMENTED / NOT TESTED**.
- No runtime configuration or game code was changed by this design record.
