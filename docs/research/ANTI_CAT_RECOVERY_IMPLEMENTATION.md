# Anti-Cat Recovery Implementation

## P1 — Passive signal classification

- Branch: \`dev/anti-cat-recovery-20261106\`
- Base: \`2ae3da364310ccd38d2fd1f43f94c1bfa1b1956c\`
- Implementation: \`src/kancollebot/expeditions/recovery_classification.py\`
- Tests: \`tests/test_recovery_classification.py\`

### Implemented

A pure classifier maps observed signals to conservative recovery categories:
- natural port witness captured;
- no natural request observed;
- cat screen;
- CDP unavailable / target crashed;
- observer unavailable;
- server rejection, including API result 201;
- login required;
- maintenance;
- unknown/untrusted signal;
- observer inactive.

The classifier performs no network operation, CDP reconnect, UI input, page refresh, game API request, request replay, or state mutation.

### Safety contract

1. A natural-response timeout alone is not evidence of a network outage.
2. Explicit CDP disconnection is distinct from a missing natural response.
3. API result 201 and other non-success API results require manual handling; they are not retried.
4. A cat screen requests a pause and observation; it does not trigger automatic refresh.
5. Unknown or malformed signals fail closed.
6. The classification result always has \`resume_allowed == False\`. Business resumption requires a later, separate state-reconciliation gate.

### Verification

- GitHub Actions: [run 42140324828](https://github.com/wosafety0/wosafety0clamp/actions/runs/42140324828)
- Result: success.
- Full Python suite: 1798 tests OK, 22 skipped because optional vision dependencies were unavailable.
- Python compileall, CLI smoke checks, and offline JavaScript checks passed.
- All 13 new recovery-classification tests passed.

### Scope not yet implemented

- No Supervisor/Monitor integration.
- No global pause gate.
- No observer reconnect coordinator or retry/backoff.
- No Action Journal / Dispatch Registry reconciliation flow.
- No automatic resume.
- No Poi/CDP session, UI click, page refresh, dispatch, settlement, or resupply was performed.
- No live anti-cat verification.

### Next phase

Implement P2 global pause gating before \`monitor.tick()\` and at visible UI action boundaries. Offline fault-injection tests must prove zero UI actions while paused, stale observation-generation rejection, and fail-closed handling of unknown state.


## P2 — Global recovery pause gate

### Implemented

- \`RecoveryPauseGate\` is a thread-safe, latched pause gate. P2 intentionally has no resume/reset API; a later phase must add release only after explicit business reconciliation.
- \`ContinuousExpeditionSupervisor.tick()\` checks the gate before \`monitor.tick()\`; a paused tick records a \`supervisor_paused\` decision and performs no monitor, settlement, resupply, or dispatch work.
- \`MonitorRuntime\` owns one shared gate. \`run_monitor()\` stops before the next tick and reports \`RECOVERY_PAUSED\`.
- The shared \`UiOperationArbiter\` checks the gate at operation entry and exposes a per-input permit.
- The visible port-refresh path checks the permit before each click, including its one permitted no-effect repeat.
- The expedition UI driver checks the permit for every dispatch, settlement, and resupply click. The production composition root also guards list-scroll clicks and pointer movement.
- Each input permit holds the gate lock across one synchronous input and its click receipt. A concurrent pause waits for an already-authorized input to finish, then blocks the next input; it does not interrupt an in-flight click.
- The monitor's modal-recovery callback refuses to start modal dismissal after the gate is paused.

### Verification

- GitHub Actions: [run 42142219592](https://github.com/wosafety0/wosafety0clamp/actions/runs/42142219592)
- Exact tested HEAD and branch HEAD: \`d283b8a18e48f2a8def98133948871e56d1a01ba\`.
- Result: success; full Python suite **1812 tests OK / 22 skipped**.
- New/updated tests cover: supervisor pre-monitor pause, monitor runner pause, runtime/arbiter shared gate, first-click refusal, mid-refresh pause with no second click, CDP expedition click refusal, arbiter refusal, and thread-safe pause waiting for an in-flight input permit.
- Python compileall, CLI smoke checks, and offline JavaScript checks passed.

### Remaining boundary

- The gate is now wired into production action paths, but no production observer currently calls \`RecoveryPauseGate.pause()\`; the gate remains open unless an upstream caller explicitly pauses it. P3 must connect the passive observer/classifier to this shared gate and preserve the evidence that caused the pause.
- There is still no automatic CDP reconnect, retry/backoff, witness reacquisition, business ledger reconciliation, or automatic resume.
- No Poi/CDP session or game UI action was performed. No live anti-cat verification.


## P3 — Passive observer drives the pause gate

### Implemented

- \`BackgroundPortSnapshotProvider\` accepts an optional status-transition callback and invokes it only when the observer status changes. It invokes the callback outside its own lock.
- The callback is deliberately limited to the thread-safe in-memory recovery gate. It does not share the event sink or background-thread correlation context.
- \`build_runtime()\` connects the existing passive Poi observer to \`classify_recovery_signals()\`; any classification requiring a pause latches the shared \`RecoveryPauseGate\`.
- CDP target discovery/connection failures, connection closure, observer process/protocol failures and untrusted/unknown statuses now pause the run automatically.
- A natural-response timeout alone remains non-fatal: it means no natural request was observed, not proof of a network outage.
- Observer status claiming \`captured\` without an actual witness payload is downgraded to \`invalid_status_without_snapshot\`, which fails closed.
- Repeated identical observer failures do not repeatedly invoke the callback. A later healthy witness does not clear the latched gate.

### Verification

- GitHub Actions: [run 42142935940](https://github.com/wosafety0/wosafety0clamp/actions/runs/42142935940)
- Exact tested HEAD and branch HEAD: \`423a623f98ff5a596711455480d6d21904c5c00f\`.
- Result: success; full Python suite **1815 tests OK / 22 skipped**.
- New tests cover observer status transition deduplication, runtime observer-fault pause propagation, and false captured status without a witness payload.
- Python compileall, CLI smoke checks, and offline JavaScript checks passed.

### Remaining boundary

- This phase consumes the existing passive observer's status vocabulary. It does not yet classify visual cat frames, extract API result 201 from game responses, or observe a CDP target-crash event not already surfaced by the current observer.
- The observer still uses the existing fixed retry delay; bounded exponential backoff and a retry budget are not implemented.
- A healthy observer status or newly captured witness cannot release the pause. There is no automatic reconnect orchestration, witness-after-pause proof, Action Journal / Dispatch Registry reconciliation, or automatic resume.
- No Poi/CDP session or game UI action was performed. No live anti-cat verification.


## P4 — Bounded observer retry and backoff

### Implemented

- The passive snapshot provider now uses exponential retry backoff for repeated observer/transport failures: base delay, doubling per consecutive failure, capped by \`max_retry_delay_seconds\`.
- The default retry budget is six consecutive failures; after exhaustion the provider stops and publishes \`retry_budget_exhausted:<last_status>\`. The recovery gate remains latched.
- A successful natural witness resets the consecutive-failure count.
- \`natural_response_timeout\` is neutral and does not consume the failure budget: no observed request is not evidence of a broken connection.
- Retry base delay, cap, and failure budget are configurable and validated. The default cap preserves compatibility with a caller-supplied base delay greater than eight seconds.
- Status callbacks remain transition-only and never share the event sink from the background thread.

### Verification

- GitHub Actions: [run 42143413592](https://github.com/wosafety0/wosafety0clamp/actions/runs/42143413592)
- Exact tested HEAD and branch HEAD: \`77fe29ff1f7b069fdc7d46ed126d1c35812b25c2\`.
- Result: success; full Python suite **1817 tests OK / 22 skipped**.
- New tests cover bounded retry exhaustion, natural timeout neutrality, status callback behavior, and malformed captured-without-witness signals.
- Python compileall, CLI smoke checks, and offline JavaScript checks passed.

### Remaining boundary

- Retry exhaustion stops the observer thread; the process remains paused and requires operator intervention/restart. No request replay or page refresh is attempted.
- A successful new witness still does not release the gate. P5 must prove the witness was captured after the pause, then reconcile Poi fleet mission tuples and durable dispatch/settlement/resupply ledgers before any explicit release.
- No Poi/CDP session or game UI action was performed. No live anti-cat verification.


## P5 — Read-only post-pause evidence and business reconciliation

### Implemented

- Added `RecoveryReconciliationCoordinator` in `src/kancollebot/expeditions/recovery_reconciliation.py`.
- The coordinator reads the latest in-memory natural port witness, a read-only Poi fleet observation, scheduler fleet states, open Dispatch Registry records, pending dispatch intents, unresolved Action Journal records, and the append-only SQLite dispatch event log.
- A witness must have a valid `source.responseReceivedAt` strictly later than `RecoveryPauseGate.paused_at_utc` and no later than the reconciliation clock. No post-pause witness returns `WAITING_FOR_WITNESS`.
- Poi state must pass `ObservedState.require_fresh()`, carry `source_freshness_verified=True`, use the `poi-redux:` source namespace, and have an observation timestamp later than the pause.
- Each scheduler fleet is checked against Poi mission status, mission id, ordered ship ids, reserved slot, return timestamp, and the unique confirmed Dispatch Registry identity. Idle/ready/resupply phases require an idle Poi mission and no open dispatch. Active/returned phases require an exact mission and dispatch match; active fleets also require the persisted Home Port verification.
- Pending dispatch intents, unresolved Action Journal rows, empty/invalid fleet state, untracked active Poi fleets, open dispatch conflicts, and scheduler/Poi disagreement all fail closed.
- Settlement and resupply lifecycle events are checked from the append-only SQLite event log. Settlement start events must close with a recognized settlement terminal; resupply start events must close with `resupply_confirmed`. Duplicate starts, incomplete lifecycles, post-pause blocker events, and missing lifecycle evidence require manual review. The event log is paired by fleet and lifecycle kind in append order because the SQLite-only sink does not necessarily publish correlation context.
- Missing Action Journal or lifecycle event log is not interpreted as an empty ledger; it yields `MANUAL_REQUIRED`.
- The report has `WAITING_FOR_WITNESS`, `MANUAL_REQUIRED`, or `RECONCILED` status. Its `resume_allowed` property is hard-coded to false.

### Safety boundary

1. The coordinator is read-only: it does not mutate scheduler state or any ledger.
2. It performs no CDP reconnect, page refresh, request replay, UI click, dispatch, settlement, or resupply.
3. It never releases or resets `RecoveryPauseGate`.
4. A `RECONCILED` report means the supplied business evidence agrees; it is not permission to resume. Visible UI verification and a separate, explicitly authorized release design remain future work.
5. No live Poi/CDP or real-game recovery was performed.

### Verification

- Latest implementation commit: `87f71bc8fd4ddc8f965ad74ce13999374b1db799`.
- GitHub Actions: [run 42148382256](https://github.com/wosafety0/wosafety0clamp/actions/runs/42148382256), success.
- Added `tests/test_recovery_reconciliation.py`: 16 focused tests covering post-pause witness timing, Poi freshness, scheduler property access, active mission/dispatch identity, pending intents, Action Journal, settlement/resupply lifecycle pairing, missing evidence, and the no-auto-resume contract.
- The full CI workflow also completed Python compilation, CLI smoke checks, offline JavaScript checks, and the full Python unit suite.

### Remaining boundary

- P5 is a tested read-only component but is not yet wired into the production recovery entry point.
- No visual cat-frame recognition, independent CDP target-crash signal, or API result 201 extraction from natural responses.
- No operator-visible UI checkpoint or explicit gate-release API.
- No real network interruption, Poi/CDP reconnect, game UI action, or live anti-cat verification.

### Next phase

P6 should expose the read-only report in a controlled recovery entry point and verify the visible UI state. Any gate release must be a separate action requiring explicit operator approval; a healthy network or a reconciled report alone must never release the gate.
