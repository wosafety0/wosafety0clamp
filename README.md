# wosafety0clamp

A local-first Python framework for safety-gated desktop game automation. The game client and its runtime remain on the operator's Windows machine; GitHub Actions runs offline checks only.

> **Handover entry point:** read [docs/HANDOFF.md](docs/HANDOFF.md) before changing code.

## Active development branch

The application source and current MVP work are on:

**dev/expedition-closed-loop-mvp**

At the time of this update, the GitHub default branch **main** contains only the **.github** directory. Do not assume that **main** is the current development baseline.

    git clone https://github.com/wosafety0/wosafety0clamp.git
    Set-Location wosafety0clamp
    git fetch origin
    git switch --track origin/dev/expedition-closed-loop-mvp

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
- **Shared infrastructure stays task-neutral.** The cross-process UI mutex, read-only Poi fleet snapshot, supply preflight/verification, composition snapshot shapes, the append-only event sink, and the visual dependency gate are shared by all tasks; no task-specific game logic belongs inside them.

Known work before a second task runs alongside expedition: fleet runtime state is keyed by `fleet_id` alone (`fleet_runtime`, `dispatch_intents`), so a shared fleet-lease / `(task_kind, fleet_id)` key is required; `dispatch_events` needs structured task attribution; and each new task needs its own screen registry (UI states, fingerprints, coordinates, click plans). These are documented in `docs/PROJECT_kancollebot_Progress.md` and are intentionally not implemented yet.

## Current status — 2026-11-03

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
