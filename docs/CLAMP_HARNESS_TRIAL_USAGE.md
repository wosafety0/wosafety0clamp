# Clamp Harness Trial Usage

## Purpose

The Harness acceptance workflow is an auxiliary acceptance/reporting layer for clamp. It does not replace the repository's existing checks, change runtime behavior, or authorize game actions.

## When to use it

1. **During normal implementation:** keep using the project's existing development and test loop. Do not trigger the Harness after every small edit; use it when a coherent change is ready for an acceptance checkpoint.
2. **At a checkpoint on the trial branch:** push a commit to a branch matching `feat/harness-trial-gate-*`. The Harness trial workflow runs automatically.
3. **Before proposing integration into a development branch:** open/update a PR targeting `dev/**`. The workflow runs in the PR context. Keep the trial PR as Draft until the changes and check evidence have been reviewed.
4. **For a manual spot check:** use Actions → Harness Trial Acceptance → Run workflow. This is optional; normal matching pushes and PRs already trigger the workflow.

## What the workflow checks

The reusable workflow is pinned to immutable commit `65544e290fd875bb6c4e30f6525b238200037bba`. It runs the consumer-owned command:

- Node static tests: `tests/static_decoder_extract.test.cjs` and `tests/verify_cdp_snapshot.test.cjs`
- Python compile check: `python -m compileall -q src tests`
- Offline Python unit tests: `PYTHONPATH=src python -m unittest discover -s tests -v`

The command uses `&&`, so a failed stage prevents later command segments from running. The reusable workflow creates a redacted acceptance report, uploads it as an artifact, and closes the final gate unless all required stages succeed.

## How to read the result

- **Green run:** the configured static/compile/offline checks passed for that commit. This is not proof of live POI integration, live game behavior, or semantic correctness of every change.
- **Red run:** open the failed job and identify the first failing step. Download `consumer-acceptance-report` from the run's Artifacts section for the redacted stage summary. Do not infer the root cause from the final gate alone.
- **Expected-negative test:** an intentionally failing smoke test should live on a clearly named isolated test branch and must not be confused with normal clamp acceptance.
- **No report artifact:** treat the run as incomplete/failed; do not infer acceptance from an earlier green run.

## Safety and integration policy

- Existing project CI remains authoritative and unchanged by this trial.
- The workflow has `contents: read` permission and runs offline project checks only.
- It does not call model providers, operate the game, deploy, merge PRs, or change machine-wide settings.
- Do not make this trial a required branch-protection check until the team/user separately decides to promote it.
- Keep PR #14 Draft/unmerged pending review and explicit merge authorization.

## Current evidence

- Normal clamp acceptance: [run 40801580252](https://github.com/wosafety0/wosafety0clamp/actions/runs/40801580252).
- Latest archived hosted empty-command negative-path test: [run 40802071906](https://github.com/wosafety0/wosafety0clamp/actions/runs/40802071906). Its expected failure confirms the pinned reusable workflow rejects blank consumer commands, uploads the failed report, and closes the gate. This negative test is separate from normal acceptance.
- Consumer trial PR: [#14](https://github.com/wosafety0/wosafety0clamp/pull/14).

## Document responsibility

This guide documents only the clamp Harness trial. Update it when the trial workflow's triggers, pinned reusable-workflow SHA, acceptance command, report behavior, or integration policy changes.
