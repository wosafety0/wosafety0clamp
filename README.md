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
2. Keep Fleet 1 read-only, Fleet 2/Fleet 3 as the only dispatch candidates, and Fleet 4 deferred until it is unlocked.
3. Continue mocked/offline tests and static CI; missing, stale, ambiguous, or malformed observations must remain fail-closed.
4. Treat any future live operation as a separately authorized, operator-supervised activity. CI and this repository workflow do not authorize unattended game actions.

## Current status — 2026-11-05

The repository is beyond the initial bootstrap stage. The current MVP includes an offline-tested fleet lifecycle state machine, shared-UI arbiter, SQLite fleet-state and dispatch-intent stores/recovery, an injected-port dispatch controller, a read-only continuous monitor, and a single-tick supervisor.

The Poi integration includes a passive Network observer and an in-memory freshness-witness path. One natural successful port response was matched against a contemporaneous Poi MCP read on 2026-11-02; this is a bounded observation, not proof of long-term freshness or atomic consistency across future reads.

The recorded operator-supervised UI evidence covers return-result/bonus/home navigation and fleet-wide supply for the unlocked Fleets 1–3. A final audit recorded 13/13 ships at master fuel/ammunition levels. This historical evidence does not establish unattended scheduler readiness.

Production dispatch/supervisor composition, mission/fleet selection, reward progression, automatic resupply integration into the continuous scheduler, and unattended operation remain pending. The production freshness/dispatch gate remains fail-closed.

The current read-only scanner/safety candidate stack is **PR #28 → PR #29 → PR #30 → PR #31 → PR #32 → PR #34**; all remain open, Draft, and unmerged. PR #30 refuses to infer list completion from row count; PR #31 blocks relocation when the active area tab is unknown; PR #32 adds a pure offline multi-page scan session requiring independent top/bottom evidence bound to the exact first/latest pages and labels for every row. PR #32 exact-head CI [run 41750517004](https://github.com/wosafety0/wosafety0clamp/actions/runs/41750517004) passed 778 Python tests (20 skipped), Node static 17/17, observer syntax, package/CLI smoke checks, and compileall. PR #34 adds exact immutable-integer validation for RowSignature inputs; exact-head Push CI [run 41886869791](https://github.com/wosafety0/wosafety0clamp/actions/runs/41886869791) passed 780 Python tests (20 skipped), Node static 17/17, observer syntax, package/CLI smoke checks, and compileall. Its documentation archive head `e1eae5c6a7ecb14bf542180eb0f266e019aaa69b` also passed Push CI [run 41936761069](https://github.com/wosafety0/wosafety0clamp/actions/runs/41936761069). PR #33 is the earlier same change based on PR #30 and is superseded by #34; do not merge it independently. All evidence is offline only: no live scrolling or clicking is authorized, and the visual top/bottom edge detectors remain unimplemented.

A separate M1 composition-data candidate is maintained in [Draft PR #35](https://github.com/wosafety0/wosafety0clamp/pull/35), stacked directly on the PR #27 safety baseline rather than the scanner stack. It reads only `api_deck_num` to supply `minimum_ships`; it does not infer ship types or flagship level. Its strict-loader code head passed 1,113 Python tests (21 skipped), Node 17/17, and compile/CLI checks; the archive-head Push and four-matrix PR-context CI both passed 1,113 tests per matrix job. See `docs/PROJECT_kancollebot_Progress.md` for exact commit/run evidence.

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
