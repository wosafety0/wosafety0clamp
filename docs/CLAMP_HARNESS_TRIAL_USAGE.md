# Clamp Harness Trial Usage

## Purpose

The Harness acceptance workflow is an auxiliary acceptance/reporting layer for clamp. It does not replace the repository's existing checks, change runtime behavior, or authorize game actions.

## When to use it

1. **During normal implementation:** keep using the project's existing development and test loop. Do not trigger the Harness after every small edit; use it when a coherent change is ready for an acceptance checkpoint.
2. **At a checkpoint on the trial branch:** push a commit to a branch matching `feat/harness-trial-gate-*`. The Harness trial workflow runs automatically.
3. **Before proposing integration into a development branch:** open/update a PR targeting `dev/**`. The workflow runs in the PR context. Keep the trial PR as Draft until the changes and check evidence have been reviewed.
4. **For a manual spot check:** use Actions → Harness Trial Acceptance → Run workflow. This is optional; normal matching pushes and PRs already trigger the workflow.

## What the workflow checks

The reusable workflow is pinned to immutable commit `982a84782db825689bd3b84c13cc168934375e44`, whose Push and PR CI passed in the Harness repository before this consumer pin was updated. It runs the consumer-owned command:

- Node static tests: `tests/static_decoder_extract.test.cjs` and `tests/verify_cdp_snapshot.test.cjs`
- Python compile check: `python -m compileall -q src tests`
- Offline Python unit tests: `PYTHONPATH=src python -m unittest discover -s tests -v`

The command uses `&&`, so a failed stage prevents later command segments from running. The reusable workflow creates a redacted acceptance report, verifies the report's consumer commit SHA and caller workflow ref/SHA against trusted GitHub context, uploads the artifact, and closes the final gate unless runtime validation, the consumer command, report generation, artifact upload, and report verification all succeed.

## How to read the result

- **Green run:** the configured static/compile/offline checks passed for that commit. This is not proof of live POI integration, live game behavior, or semantic correctness of every change.
- **Red run:** open the failed job and identify the first failing step. Download `consumer-acceptance-report` from the run's Artifacts section for the redacted stage summary. Do not infer the root cause from the final gate alone.
- **Expected-negative test:** an intentionally failing smoke test should live on a clearly named isolated test branch and must not be confused with normal clamp acceptance.
- **No report artifact:** treat the run as incomplete/failed; do not infer acceptance from an earlier green run.

## Existing CI trigger coverage

The existing `.github/workflows/ci.yml` is unchanged, but its triggers are narrower than this trial: pushes to `main` and `dev/**`, pull requests targeting `main`, and manual dispatch. It does **not** run for pushes to `feat/harness-trial-gate-*` or for PR #14 targeting `dev/expedition-closed-loop-mvp`. Therefore, the Harness Trial Acceptance run is the only automated workflow check currently observed on this trial PR head. Treat `existing CI remains unchanged` as configuration independence, not as proof that existing CI ran on the same head. Before integration, use the normal development-branch CI path or separately run the equivalent offline checks; do not promote this trial based on Harness-only evidence.

## Safety and integration policy

- Existing project CI remains authoritative and unchanged by this trial.
- The workflow has `contents: read` permission and runs offline project checks only.
- It does not call model providers, operate the game, deploy, merge PRs, or change machine-wide settings.
- Do not make this trial a required branch-protection check until the team/user separately decides to promote it.
- Keep PR #14 Draft/unmerged pending review and explicit merge authorization.

## Current evidence

- Latest normal clamp acceptance at the time of this update: [run 40805100294](https://github.com/wosafety0/wosafety0clamp/actions/runs/40805100294), exact head `cb9113354c3058595a421f97f704acc7f488b2a2`; 12 Node tests and 122 Python tests passed, report artifact uploaded, final gate passed.
- Latest archived hosted empty-command negative-path test: [run 40802071906](https://github.com/wosafety0/wosafety0clamp/actions/runs/40802071906). Its expected failure confirms the pinned reusable workflow rejects blank consumer commands, uploads the failed report, and closes the gate. This negative test is separate from normal acceptance.
- Latest exact-head consumer acceptance at `8e703cb56c81208a634cd9dcdf7a4e42c83f95c4`: Push CI [40852682505](https://github.com/wosafety0/wosafety0clamp/actions/runs/40852682505) and PR CI [40852688621](https://github.com/wosafety0/wosafety0clamp/actions/runs/40852688621) both passed. The PR run completed runtime validation, consumer command, redacted report generation, artifact upload, report identity verification, and final gate; `consumer-acceptance-report` artifact ID `12888110966` is available (SHA-256 `8b6d70076e306acc2d33ef51f3ac1f8f33bb9a2cf7088e153966ba6809d6d52b`).
- Latest exact-head consumer acceptance at `722cd1e70669003f0fc2a5e51c9555fca588a562`: Push CI [40851991948](https://github.com/wosafety0/wosafety0clamp/actions/runs/40851991948) and PR CI [40852000842](https://github.com/wosafety0/wosafety0clamp/actions/runs/40852000842) both passed. The PR run confirms runtime validation, consumer command, redacted report generation, artifact upload, report identity verification, and final gate all succeeded; `consumer-acceptance-report` artifact ID `12888045215` was uploaded.
- Consumer trial PR: [#14](https://github.com/wosafety0/wosafety0clamp/pull/14).

## Document responsibility

This guide documents only the clamp Harness trial. Update it when the trial workflow's triggers, pinned reusable-workflow SHA, acceptance command, report behavior, or integration policy changes.


## Report-verifier pin update

- Updated the trial from `0173c597e82364a91e50f9817a9c8d40876dc04c` to `982a84782db825689bd3b84c13cc168934375e44`, preserving the inline consumer report verifier and Python/Node runtime contract. The earlier candidate `07b8000ab8c2a1dad0f91bd29e2d84028bec2c93` was rejected after consumer CI showed its verifier script was not present in the consumer checkout.
- The Harness repository's Push CI [40841134517](https://github.com/wosafety0/dev-workflow-kit/actions/runs/40841134517) and PR CI [40841140380](https://github.com/wosafety0/dev-workflow-kit/actions/runs/40841140380) passed for that exact SHA before pinning it here.
- Consumer integration verification is complete for Clamp head `a841627963a77701091bba69da74f09390b81c50`: offline Node tests (13/13), Python compilation, Python unit tests (122/122), and `git diff --check` passed. Exact-head Clamp Push CI [40851075661](https://github.com/wosafety0/wosafety0clamp/actions/runs/40851075661) and PR CI [40851082838](https://github.com/wosafety0/wosafety0clamp/actions/runs/40851082838) both completed successfully, including report generation, artifact upload, report identity verification, and final gate. PR #14 remains Draft/open/unmerged; CI success does not authorize merge or promotion to required branch protection.

### Runtime-validation negative path (hosted evidence)

- Isolated negative-path run [40821913417](https://github.com/wosafety0/wosafety0clamp/actions/runs/40821913417) on commit `ebacf6cad0ecdea1e6a2051068bf60864c585287` supplied an unsupported `runtime` value. The validator reported `runtime must be either python or node.` and exited 2; runtime setup and the consumer acceptance command were skipped.
- The reusable workflow still generated and uploaded `consumer-acceptance-report` (artifact ID `12875055823`), with runtime validation failed and the consumer command not run. Report verification and the final gate then failed closed; the final gate listed `runtime,tests,verification` because skipped required stages are not successful. In this case `tests` means **skipped**, not that the consumer test suite ran and failed.
- Diagnosis rule: inspect the first failed step to find the root failure, then use the report artifact to confirm the stage summary. Do not treat the final-gate list as a substitute for the first failing step, and do not count this intentional negative run as normal acceptance.