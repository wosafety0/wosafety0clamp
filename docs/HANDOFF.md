# Developer Handoff

- **Updated:** 2026-11-06
- **Repository:** wosafety0/wosafety0clamp
- **Authority:** `wosafety0/Agents-global/main/AGENTS.md` (2.1.0-rc1)

## Current remote state

The GitHub default branch `main` (`a4f377764d94c8f0960c931bf94c27bb7e9b5a8e`) contains only `.github`; it is not the application baseline.

| Role | Branch / PR | Recorded commit | State |
|---|---|---|---|
| Current MVP base | `dev/dispatch-observer-safety-current-mvp-20261103` | `ba8884466f2bb1d568cc46b76034711df81bb50c` | Current remote base |
| Settlement + run-log candidate | `dev/run-log-safety-hardening-20261106` / [PR #40](https://github.com/wosafety0/wosafety0clamp/pull/40) | code commit `1bf11856356f9bd3b0fd7a615567a1ac9c4b9fed` | Draft, unmerged |
| SQLite event ID candidate | `dev/event-id-readback-20261106` / [PR #39](https://github.com/wosafety0/wosafety0clamp/pull/39) | `6048b918632ffea1510e4b038b82cd30c53a4384` | Draft, unmerged |
| SQLite/JSONL row-link follow-up | `dev/identity-contract-and-event-link-20261106` / [PR #41](https://github.com/wosafety0/wosafety0clamp/pull/41) | `fae250edba687adb47d04d072e91383204fc18c4` | Draft, unmerged |
| Critical audit-gap guard | `dev/run-log-critical-gap-gate-20261106` / [PR #42](https://github.com/wosafety0/wosafety0clamp/pull/42) | `e462ac9dca213366781108aaf0dc58416ae814c9` | Draft, stacked on #41 |

PR #40 is a broad staging candidate (42 commits / 105 changed files), not a merge recommendation. Exact code-head CI:
- [Push #42128797363](https://github.com/wosafety0/wosafety0clamp/actions/runs/42128797363): 1,634 Python tests, 21 optional vision tests skipped; Node 17/17; compile and CLI smoke passed.
- [Feature matrix #42128884084](https://github.com/wosafety0/wosafety0clamp/actions/runs/42128884084): 4/4 jobs passed (Python 3.12/3.13 × vision with/without); 1,634 Python tests per job, 4 skipped with vision and 21 without; Node 19/19; dependency gate behaved as expected in both directions.

## Verified achievements

### Historical live evidence (earlier operator-authorized sessions)

- Fleet 2 / mission 1 passed the freshness and requirement gate, but that run stopped before dispatch.
- Fleet 4 / mission 1 completed one operator-supervised live loop: supply, fresh witness, gate PASS, dispatch, return refresh, reward settlement, Home Port, and post-settlement state reconciliation. Evidence is under `docs/research/P0_FLEET4_*`.
- This is one fleet / one mission / one recorded loop, not unattended multi-cycle acceptance.

### Offline implementation and CI

- P0.1 adds a bounded `SettlementDrainSession` contract with deterministic Mock tests.
- P0.2 adds an event envelope, JSONL run log, bounded rotation/retention, redaction, atomic state snapshot, Tee integration, and a critical audit gate while retaining the SQLite Action Journal.
- P0.2b restricts emitter-forged correlation IDs and binds a runtime-minted `dispatch_id` around one supervisor dispatch attempt.
- P0.2c adds sticky degraded-state handling, oversized-event rejection, symlink-safe log/snapshot paths, unique snapshot temp files, fail-closed redaction fallback, provenance-field redaction, and malformed critical-event rejection.
- P0.2d adds a strict SQLite row-ID receipt and links it to the JSONL envelope through sink-owned `sqlite_event_id`; the JSONL UUID `event_id` remains a separate namespace. Exact-head CI passed 1,637 Python tests with 21 optional vision tests skipped.
- P0.2e (Draft PR #42) closes the audit-gap bypass across visible UI paths: a degraded RunLogSink rejects every later critical event; TeeEventSink preflights all writable sinks before the primary SQLite append; supply-tab/supply-all clicks now emit critical pre-click events; visible port-refresh click attempts/outcomes are critical and the permitted retry records its own attempt/sent pair. Exact-head Push [42135969359](https://github.com/wosafety0/wosafety0clamp/actions/runs/42135969359) and Feature CI [42135976481](https://github.com/wosafety0/wosafety0clamp/actions/runs/42135976481) passed 1,644 Python tests; all four Python 3.12/3.13 × vision with/without matrices passed, Node 19/19.

## Explicitly not verified / blocked

- **Identity semantics:** PR #40's `dispatch_id` means one dispatch attempt; it is not persisted across restarts and does not propagate to settlement/resupply. PR #39's design defines `dispatch_id` as the complete expedition lifecycle. These contracts conflict.
- **Event ID namespaces:** PR #40's JSONL `event_id` is a UUID string; SQLite `event_id` is an integer primary key. PR #41 adds `sqlite_event_id` to join the same event row across both logs; the IDs remain distinct and cannot be substituted.
- **Action identity:** `action_id` is reserved but not bound per UI click.
- **Run-log live validation:** `--run-log-dir` remains opt-in. No live log was produced or validated by this scheduled run.
- **Lifecycle coverage:** settlement and resupply events lack a persisted lifecycle ID. An attempt ID is not proof that dispatch occurred; require intent, Poi confirmation, and subsequent state evidence.
- **Operational acceptance:** one historical Fleet 4 loop does not prove multiple fleets pending settlement, concurrent fleet behavior, crash recovery, or interruption recovery.
- **Fleet 4 supply:** historical visual calibration has not been promoted into the production supply gate.
- **Scanner:** multi-page end-of-list detection, cross-page row identity, and post-scroll coordinate recalibration still need independent validation.
- **Fleet-size contract:** the 7-ship planning requirement versus the current six-slot fleet model remains unresolved.
- This scheduled run did not connect to Poi, send game input, click, scroll, dispatch, settle, resupply, or modify the system.

## Next priorities

1. Review PR #40's 42-commit / 105-file diff by subsystem; split it if the scopes cannot be reviewed together.
2. Resolve per-attempt versus full-lifecycle `dispatch_id` semantics. Preserve legacy rows as unknown; do not infer identity from fleet, mission, timestamps, or adjacency.
3. Review PR #41's SQLite-to-JSONL row link, including primary-success/secondary-failure behavior; do not treat it as semantic deduplication.
4. Design durable lifecycle recovery for settlement/reward/resupply and READY confirmation. Do not choose a schema migration or resolver without a compatibility design.
5. Add per-action `action_id` only after the lifecycle contract is settled.
6. Keep run-log wiring default-off until a separately authorized live validation is approved.
7. Continue offline review of scanner boundaries, interrupted settlement recovery, and the 7-ship versus 6-slot constraint.

## Validation commands

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
node --test tests/static_decoder_extract.test.cjs tests/verify_cdp_snapshot.test.cjs
node --check tools/hand_sandbox/poi_port_response_observer_20261029.js
python -m compileall -q src tests tools
```

The feature matrix additionally tests Python 3.12/3.13 with and without optional vision dependencies. CI success proves only offline checks; it does not connect to Poi or verify live expedition behavior.

## Safety contract

- CDP endpoints and WebSockets remain credential-free loopback.
- Do not use `Runtime.evaluate`, execute page JavaScript, or access page getters in this development workflow.
- Passive Network observation may capture naturally occurring responses only; it must not initiate, construct, or replay game requests.
- This scheduled automation performs no game input. Any future live action requires explicit user authorization and a separately reviewed scope.
- Stale, incomplete, conflicting, malformed, or ambiguous evidence blocks actions.
- Never infer a successful dispatch from `dispatch_id` alone.
- Keep run-log wiring default-off until identity semantics and live validation are approved.
- Do not modify Windows, proxy, or system settings; do not merge PRs, change `main`, publish, deploy, or expand permissions.

## Open PR inventory

The current remote inventory has 31 open PRs. No PR was merged or closed in this run.

- [PR #40](https://github.com/wosafety0/wosafety0clamp/pull/40): 42 commits / 105 files, Draft, broad settlement/run-log integration candidate.
- [PR #39](https://github.com/wosafety0/wosafety0clamp/pull/39): SQLite event ID readback source candidate, Draft.
- [PR #41](https://github.com/wosafety0/wosafety0clamp/pull/41): SQLite-to-JSONL row link, Draft, stacked on #40; final-head CI passed 1,637 tests / 21 optional skips.
- [PR #42](https://github.com/wosafety0/wosafety0clamp/pull/42): sticky audit-gap and pre-click event gate, Draft, stacked on #41; exact-head Push and four-matrix Feature CI passed 1,644 tests per matrix.
- [PR #27](https://github.com/wosafety0/wosafety0clamp/pull/27): current MVP safety base.
- PR #34 / #35 and older scanner/M1 drafts are historical or stale; re-check each head/base comparison before reuse.
- Do not bulk-close, rebase, merge, or force-move historical drafts without per-PR dependency and diff review.
