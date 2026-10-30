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

Use `scripts/dc_clamp_batch_check.ps1` when a local project-state check is actually necessary. It is read-only and designed for Windows PowerShell 5.1.

The script intentionally checks only known CLAMP worktree locations and tracked project paths; it does not recursively scan the machine.

## Measurement policy

DC usage reports must distinguish:

- **DC overall usage:** the service/account usage statistic returned by DC.
- **CLAMP-specific usage:** only calls that can be reliably attributed to this project from complete, queryable call history.

If complete project-attribution data is unavailable, report that fact rather than deriving a savings percentage from estimates.

## Current baseline

The current DC service snapshot observed during this optimization reported 1548 total calls, 99% success, and 83% monthly calls remaining. This is an overall DC baseline, **not** a CLAMP-only count. No CLAMP savings percentage is claimed from it.

## Verification boundary

This optimization changes only the collaboration workflow and adds a read-only local batch helper. It does not authorize game input, game API requests, refreshes, dispatch, or reward collection.
