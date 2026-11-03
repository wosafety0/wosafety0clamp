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
