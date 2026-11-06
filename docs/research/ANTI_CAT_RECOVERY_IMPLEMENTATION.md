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
