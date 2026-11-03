# CLAMP DC Call Cost Optimization

Status: ACTIVE
Scope: `wosafety0/wosafety0clamp` formal development branch
Branch: `dev/expedition-closed-loop-mvp`

## Purpose

Reduce unnecessary Desktop Commander (DC) calls without weakening safety checks, tests, write verification, or the project's fail-closed behavior.

## Operating policy

1. **GitHub first.** Read repository source, rules, progress, commits, branches, and remote status through the GitHub connector whenever the authoritative information already exists remotely.
2. **DC only for local-only facts.** Use DC for Windows/Poi runtime state, local uncommitted changes, local files that are not represented remotely, and tests that specifically require the local environment.
3. **Batch the local phase.** Before a change, use one bounded read-only batch to check repository identity, branch/HEAD, worktree status, target paths, and overwrite risk. After a change, use one bounded batch to inspect the diff/exit status and run only the necessary tests.
4. **Reuse sessions and results.** Reuse an existing process/session when it is still running. Do not restart a task until its prior state is known. Reuse earlier command output when it still answers the question.
5. **Targeted expansion only.** When a check fails, inspect the specific failure path. Do not perform broad recursive or whole-disk scans merely because a result is missing.
6. **No cost-driven safety shortcuts.** Required safety checks, tests, and post-write remote readback remain mandatory.
7. **Keep status layers separate.** Local modified/saved, committed, pushed, remote-read-back, and tested are separate states.

## Standard local batch entry point

For the current cross-project workflow, use the local shared helper at `D:\AI_Project\Project_KancolleBot\diagnostics\shared-execution\Invoke-SharedProjectBatch.ps1` from the persistent PowerShell session. It batches KancolleBot and Harness audits/tests and supports bounded repository-relative file reads. See the helper's adjacent `README.md` for the exact commands and safety limits.

`scripts/dc_clamp_batch_check.ps1` is a legacy, read-only diagnostic. Its embedded worktree paths are stale and it is **not** the standard entry point; do not use it for current project-state checks. The shared helper is local-only and is not part of the repository or CI.

## Measurement policy

DC usage reports must distinguish:

- **DC overall usage:** the service/account usage statistic returned by DC.
- **CLAMP-specific usage:** only calls that can be reliably attributed to this project from complete, queryable call history.

If complete project-attribution data is unavailable, report that fact rather than deriving a savings percentage from estimates.

## Current baseline

The earlier historical DC service snapshot reported 1548 total calls, 99% success, and 83% monthly calls remaining. On 2026-11-03, a later account-wide snapshot reported 7126 total calls and 71% monthly calls remaining (the preceding observed snapshot was 7090). These are overall service counters, **not** CLAMP- or Harness-attributed counts; the +36 change cannot be attributed to this workflow. No project-specific savings percentage is claimed. Future comparisons must use attributable per-task call histories or a controlled same-task before/after measurement.

## Verification boundary

This optimization changes only the collaboration workflow and adds a read-only local batch helper. It does not authorize game input, game API requests, refreshes, dispatch, or reward collection.

## Runtime file-read deduplication

- The tools/one_expedition_ui_loop.py controller caches Ooi and supply-checkbox fingerprint JSON documents within the process, keyed by resolved path, modification timestamp, and file size. A changed file version is reloaded; the cache is bounded to four entries.
- This removes repeated reads during consecutive visual fallback checkpoints without caching screenshots, Poi state, network responses, or UI evidence. Accessibility checks, fresh screenshot captures, two-consecutive-match requirements, and fail-closed behavior remain unchanged.
- The Harness audit found no proven same-execution duplicate file read or safely mergeable subprocess; config loading, acceptance-contract hashing, Git metadata, and independently timed tasks serve distinct purposes.

## Visual template image cache

- The KancolleBot visual state classifier and two-frame target locator share a bounded in-process grayscale-template cache.
- Cache identity includes resolved path, modification timestamp, file size, and the injected OpenCV/NumPy module identities. A changed file or module context triggers a fresh read and decode; stale versions for the same path are evicted.
- The cache is capped at 32 entries. Screenshot payloads, Poi state, accessibility results, network responses, and UI evidence are never cached. Matching, two-frame confirmation, ambiguity checks, and fail-closed behavior remain per screenshot.
- Unit tests verify one read for an unchanged template and invalidation after file modification. No live-game performance or DC-credit savings are claimed.

## Monitor MCP setup reuse

- PoiExpeditionReader validates the static info.fleets description once per reader instance. Every monitor read still performs health validation and fresh fleet/ship reads, including the before/after consistency confirmation and targeted master lookup.
- PoiMcpClient constructs one no-redirect urllib opener per client and reuses it for MCP and health requests, avoiding repeated handler/opener construction. HTTP responses are still read and closed per request; no game data or HTTP response is cached.
- Unit tests verify one static description request across repeated reads, live state re-reads on every cycle, health checks on every cycle, and one opener construction across MCP and health calls.
## Remaining monitor read-path audit (2026-11-03)

- Each `PoiExpeditionReader.read_fleets()` observation performs at most one `poi_lookup` request for the sorted, deduplicated master IDs referenced by the current fleet/ship snapshot; it is not one lookup per ship or per fleet. The request is already batched.
- The selected master fields (`api_fuel_max`, `api_bull_max`) are metadata, but the bridge exposes no master-data revision or Poi-session generation token. Health checks establish bridge readiness, not master-dataset identity. Cross-observation caching therefore has no reliable invalidation condition and could retain stale data after a Poi reload or dataset change; keep the lookup live.
- Keep the before/after fleet and ship reads: they detect visible state changes while composing one observation. Keep per-observation bridge health checks: they verify current liveness/readiness. Neither is a redundant read with a safe replacement established by this audit.
- The background port observer is intentionally one-shot and waits for a natural response. Its owner re-arms it after each capture/timeout with a 250 ms delay. Converting it to a persistent listener would require a separately designed re-arm/lifecycle protocol and dedicated integration tests; no low-risk process merge is justified.
- `ContinuousExpeditionSupervisor.tick()` is caller-driven and contains no polling loop or sleep interval. Poll cadence is configured outside this class, so changing it here would be speculative.
- Result: no additional runtime optimization applied in this audit; retain freshness, consistency, and fail-closed gates.
