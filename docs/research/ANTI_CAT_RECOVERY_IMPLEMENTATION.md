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
