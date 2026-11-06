# wosafety0clamp

A local-first Python framework for safety-gated desktop game automation. The game client and its runtime remain on the operator's Windows machine; GitHub Actions runs offline checks only.

> **Handover entry point:** read [docs/HANDOFF.md](docs/HANDOFF.md) before changing code.

## Current remote state — 2026-11-07

The current application source line is `feat/production-settlement-resupply-wiring-20261105` at `cdac285d2bc30ac32ceaa2e87cd56f9414ccc0a2`. The default `main` branch still contains only `.github`; it is not the application source baseline.

The current narrow run-log safety candidate is Draft PR [#43](https://github.com/wosafety0/wosafety0clamp/pull/43), branch `feat/run-log-critical-ui-gate-current-base-20261107`. Its code/test head `5f9dc881a7a442721c5123117b18e1065985a0de` passed repository CI (1,936 Python tests, 22 optional vision tests skipped, Node 19/19) and the four-way Python 3.12/3.13 × vision with/without Feature CI matrix. The PR remains open and unmerged.

The 2026-11-06 live evidence is bounded: one Fleet 2 closed-loop cycle completed; the next stability attempt stopped fail-closed at `unknown_page`. This is not three-cycle stability or multi-fleet acceptance. The drain observation-window change remains subject to a separate user decision. Run-log production wiring remains opt-in/default-off; no default-on decision is implied.


## Historical integration snapshot (2026-11-04)

The GitHub default branch **main** contains only the `.github` directory. It is not, and never was, the application source baseline.

The integration performed on 2026-11-04 is **local only**. Nothing has been pushed, so `3bd949b` is a local-only commit and **must not be described as an integrated remote version**.

| Role | Branch | Local HEAD | Remote HEAD |
| --- | --- | --- | --- |
| Integration target | `dev/dispatch-observer-safety-current-mvp-20261103` | `3bd949b` | `86f294c` |
| Preservation / active work line | `feat/closed-loop-settlement-20261104` | `3bd949b` | `caa3e5a` |
| Verified fast-forward snapshot (local, temporary) | `temp/integration-ff-20261104` | `caa3e5a` | n/a |

The target branch was advanced locally by a pure fast-forward of 81 commits (zero conflicts, no new merge commit). These SHAs are the integration snapshot; later local commits advance the local HEAD, so re-check with `git rev-parse` and `git ls-remote origin <branch>` before relying on them. Until a separately authorized push happens, a fresh clone can obtain only `caa3e5a` from `origin/feat/closed-loop-settlement-20261104` and `86f294c` from the target dev branch.

`dev/expedition-closed-loop-mvp` is a **historical** MVP branch. Older handoff notes and PR descriptions still call it the active baseline; that statement is superseded. It has diverged substantially from the current line (56 / 141 commits from the merge base) and cannot be fast-forwarded.

    git clone https://github.com/wosafety0/wosafety0clamp.git
    Set-Location wosafety0clamp
    git fetch origin
    git switch --track origin/feat/closed-loop-settlement-20261104

The remote branch above is behind the local integration head until the pending push is authorized.

## Current development priorities

1. Complete production dispatch/supervisor composition only after freshness, UI-state gates, durable intent recovery, exact post-action verification, reward settlement, and fleet-wide resupply are integrated.
2. Keep Fleet 1 read-only **for expeditions**. Fleets 2/3/4 are the *expedition* dispatch policy ceiling, but the effective set is always resolved from the fleets this account has actually unlocked (2, 3 and 4 unlock progressively); a missing fleet fails closed rather than being assumed. Fleet 4 may be dispatched but has no verified supply visual gate yet, so its closed-loop resupply remains blocked until real-client calibration. Fleet 1 is the flagship **sortie/combat** fleet: this reservation is a per-task policy, not a global fact, and combat automation must declare its own fleet policy.
3. Continue mocked/offline tests and static CI; missing, stale, ambiguous, or malformed observations must remain fail-closed.
4. Treat any future live operation as a separately authorized, operator-supervised activity. CI and this repository workflow do not authorize unattended game actions.

## Automation tasks and scope

This repository is **not expedition-only**. Expedition automation is the first implemented task; combat, sparkle (morale grinding), fleet composition, and quest automation are declared future tasks. `src/kancollebot/task_scope.py` is the single source of truth for task kinds, per-task fleet policy, and shared infrastructure. Run `kancollebot tasks` for the current status.

Only `expedition` has a reviewed visible-UI path. Every other declared task is **fail-closed**: its fleet policy is deliberately empty and uncalibrated, and `require_task_enabled()` must be called before any UI action. Declaring a task is not implementing it.

Two rules protect the roadmap:

- **Fleet policy is per task.** `RuntimeConfig`'s fleet-permission fields (`non_dispatchable_fleet_ids`, `dispatchable_fleet_ids`, `monitor_candidate_fleet_ids`, `resupply_verified_fleet_ids`) belong to the expedition task. Reusing them for combat would wrongly forbid Fleet 1.
- **Shared infrastructure is classified per symbol, not per module.** `task_scope.SHARED_CAPABILITIES` is the single source of truth: 3 `task_neutral` items, 3 `needs_decoupling` items (fleet snapshot, resupply preflight, supply-tab coordinates), and 2 `task_specific` items deliberately excluded from shared infrastructure. `require_task_neutral_capability()` fails closed when another task tries to reuse a non-neutral symbol. Run `kancollebot tasks` for the current list.

Known work before a second task runs alongside expedition: fleet runtime state is keyed by `fleet_id` alone (`fleet_runtime`, `dispatch_intents`), so a shared fleet-lease / `(task_kind, fleet_id)` key is required; `dispatch_events` needs structured task attribution; and each new task needs its own screen registry (UI states, fingerprints, coordinates, click plans). These are documented in `docs/PROJECT_kancollebot_Progress.md` and are intentionally not implemented yet.

## Historical status — 2026-11-03

The repository is beyond the initial bootstrap stage. The current MVP includes an offline-tested fleet lifecycle state machine, shared-UI arbiter, SQLite fleet-state and dispatch-intent stores/recovery, an injected-port dispatch controller, a read-only continuous monitor, and a single-tick supervisor.

The Poi integration includes a passive Network observer and an in-memory freshness-witness path. One natural successful port response was matched against a contemporaneous Poi MCP read on 2026-11-02; this is a bounded observation, not proof of long-term freshness or atomic consistency across future reads.

The recorded operator-supervised UI evidence covers return-result/bonus/home navigation and fleet-wide supply for the unlocked Fleets 1–3. A final audit recorded 13/13 ships at master fuel/ammunition levels. This historical evidence does not establish unattended scheduler readiness.

Production dispatch/supervisor composition, mission/fleet selection, reward progression, automatic resupply integration into the continuous scheduler, and unattended operation remain pending. The production freshness/dispatch gate remains fail-closed. Current safety candidate PR #27 is based directly on the current MVP branch and adds strict dispatch-intent, freshness, and passive-observer validation; its exact-head offline CI passed 460 Python tests, 17 Node static tests, and compile/syntax checks, with 17 optional vision tests skipped because those dependencies were unavailable.

## Current state

This is an actively developed prototype, **not an accepted unattended automation release**.

Verified components include read-only Poi state observation, passive freshness witnessing, guarded fleet-wide resupply, visual UI-state gates, and offline-tested dispatch preconditions. The full live dispatch → return → reward settlement → resupply loop remains unverified. Read the handoff document for exact evidence and limitations.

## Development setup

- Python 3.11 or newer.
- Node.js is required by the repository's JavaScript static tests.
- Runtime dependencies are intentionally minimal.
- OpenCV and NumPy are optional and installed through the vision extra when visual localization is needed.

    py -3.12 -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install --upgrade pip
    python -m pip install -e .

Optional visual dependencies:

    python -m pip install -e ".[vision]"

## Unified runtime entry

All production runtime commands use the installed `kancollebot` command (or
`python -m kancollebot.cli` from the repository root):

    # Read-only monitor, one observation cycle
    kancollebot monitor --once

    # Read-only monitor, continuous observation
    kancollebot monitor

    # One supervised Fleet 3 -> mission 3 dispatch transaction.
    # This sends normal visible-UI game input and requires explicit authorization.
    kancollebot dispatch-once --authorize-ui-actions

The legacy `tools/one_expedition_ui_loop.py` and
`tools/run_continuous_expedition_monitor.py` script paths remain compatibility
shims and forward to the same CLI. Shared runtime defaults and the production
Fleet 3 / mission 3 target are defined in
`src/kancollebot/runtime_config.py`. Fleet 2-specific helper functions retained
inside the one-shot module are test/compatibility helpers, not the production
dispatch target.

`dispatch-once` performs a real in-game dispatch attempt when all safety gates
pass. Do not use it as an unattended scheduler or assume that the full return,
reward settlement, and repeat loop has been accepted.

## Offline validation

Run from the repository root in PowerShell:

    $env:PYTHONPATH = "src"
    python -m unittest discover -s tests -v
    node --test tests/static_decoder_extract.test.cjs tests/verify_cdp_snapshot.test.cjs
    node --check tools/hand_sandbox/poi_port_response_observer_20261029.js
    python -m compileall -q src tests tools
    git diff --check

The GitHub Actions workflow runs the corresponding offline checks on pushes to main and dev/**, and on pull requests targeting main.

## Safety boundaries

- Keep credentials, cookies, tokens, local game data, screenshots, and machine-specific paths out of Git.
- CDP is loopback-only. Runtime.evaluate is restricted to one fixed read-only fleet snapshot expression; arbitrary JavaScript evaluation is prohibited.
- Do not send, replay, or construct game-server requests.
- Game actions must use the reviewed visible UI path and fail closed on stale, ambiguous, or conflicting evidence.
- Offline tests and a green CI run do not establish live gameplay acceptance.
- Do not enable dispatch or unattended operation merely because a command-line authorization flag exists.

## Documentation map

- [Developer handoff and current status](docs/HANDOFF.md)
- [Single-loop design contract and chronological implementation notes](docs/MVP_EXPEDITION_SINGLE_LOOP.md)
- [Passive port-response observer contract](docs/POI_PORT_RESPONSE_OBSERVER.md)
- [Research notes](docs/research/)
- [Authoritative global collaboration rules](https://github.com/wosafety0/Agents-global/blob/main/AGENTS.md)

The global AGENTS.md is the authoritative collaboration rule source. Do not create a competing project copy of those global rules.
