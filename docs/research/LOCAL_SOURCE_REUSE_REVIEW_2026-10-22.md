# Local Source Reuse Review — 2026-10-22

## Scope and provenance

This review is based only on the nine source/document files pasted by the user from the independent workspace `D:\\Project_KancolleBot`. No local filesystem access was performed by this cloud-side review, no files were uploaded from the local disk, and no original POI/KCA files or caches were inspected here. The legacy source bundle is not copied into this repository.

## Findings

### Reusable concepts

- `03_Integrations/state_fetcher-PoiCDP.py` shows the intended read-only observation fields: `info.fleets`, `info.ships`, `const.$ships`, and `info.materials`. The current cloud implementation already reads fleets, roster ships, and master ship records through the Poi MCP read bridge. The materials fields are not yet part of the expedition reader contract and should be added only when a concrete feature needs them.
- `01_Product/state_parser.py` documents a fleet-level view of `api_mission`. Its sample `[1, 2, 1792155961666, 0]` supports treating index 1 as the candidate mission ID, which matches the current normalizer. The timestamp slot and units are not sufficiently verified by this pasted source alone, so the current reader intentionally leaves `return_at` unknown instead of guessing.
- `01_Product/guard.py` illustrates a manual authorization gate and a consecutive-failure lockout. The current repository already has a centralized `SafetyPolicy` and `ActionPreflight`; duplicating the legacy guard would create two independent sources of safety state. Preserve the ideas, not the duplicate implementation.
- `05_Docs/DEVELOPMENT_PLAN.md` reinforces the required acceptance discipline: test simulated eligible/ineligible/error cases, keep live actions separately authorized, and do not mark unverified behavior as PASS.

### Do not reuse directly

- `01_Product/scheduler.py` and the duplicated scheduler embedded in `03_Integrations/action_utils.py` use a direct CDP WebSocket path and swallow state-read errors as `None`. The current architecture uses the allowlisted Poi MCP read bridge; replacing it with this older path would bypass the current interface contract and lose useful error details.
- The legacy scheduler hardcodes expedition mission ID `1` and dispatches based on a minimal mission-state check. This is not suitable for target selection or closed-loop verification.
- `ActCollect`, `ActExpedition`, and `ActSupply` in the pasted integration bundle rely on fixed screen coordinates, screenshot templates, and click sequences. Their source contains no evidence that those coordinates are valid for the current Poi build, window size, or game state. They must not be wired into a live executor without a separately reviewed, recorded interaction contract and explicit authorization.
- `03_Integrations/state_fetcher.py` duplicates most of `state_fetcher-PoiCDP.py` and includes additional unrelated code excerpts. Neither file should be copied wholesale.
- `03_Integrations/template_matcher.py` and `action_utils.py` hardcode paths under the prohibited legacy root `D:\\KancolleBot`. Those paths must not be restored or used by the new project.
- The pasted `state_parser.py` is a mixed explanatory document/code excerpt, not a clean importable Python module. Its rules need focused unit tests before any extracted implementation is adopted.

## Current cloud implementation comparison

- Existing modules: `src/kancollebot/adapters/poi/expedition_reader.py`, `src/kancollebot/adapters/poi/expedition_state_v2.py`, `src/kancollebot/expeditions/planner.py`, `src/kancollebot/safety/policy.py`, and `src/kancollebot/safety/action_preflight.py`.
- The reader is read-only and normalizes data into planner snapshots. The planner is deterministic and does not perform network or gameplay actions.
- Existing normalization is deliberately conservative: incomplete ship/master records are rejected; readiness is not inferred from unknown fields; the mission return timestamp is left unknown until its slot and units are verified.
- The installed Poi/MCP bridge was previously checked by user-run diagnostics, but the Python reader has not yet been executed against the user's local Poi process. That runtime compatibility remains unverified.

## Decision

1. Keep the MCP read bridge as the only current state-reader integration.
2. Reuse the legacy files as research evidence, not as drop-in runtime modules.
3. Prioritize offline contract tests for mission-array interpretation, empty ship slots, missing ship records, supply readiness, and conservative failure behavior.
4. Keep live collection, resupply, expedition dispatch, and battle actions disabled until interaction evidence and closed-loop post-action verification exist.
5. Do not upload the full reverse-engineering directory, third-party application trees, or cache bundles as part of this code reuse step.

## Verification status

- Review document: added to the cloud repository branch.
- Runtime code behavior: unchanged by this documentation-only commit.
- Local workspace: not modified.
- Local-to-GitHub upload: not performed.
- Live game operations: not performed.
