# wosafety0clamp

A local-first Python framework for safety-gated desktop game automation. The game client and its runtime remain on the operator's Windows machine; GitHub Actions runs offline checks only.

> **Handover entry point:** read [docs/HANDOFF.md](docs/HANDOFF.md) before changing code.

## Development branches and integration state (2026-11-06)

The GitHub default branch **main** still contains only `.github`; it is not the application source baseline.

| Role | Branch / PR | Recorded commit | State |
| --- | --- | --- | --- |
| Current MVP base | `dev/dispatch-observer-safety-current-mvp-20261103` | `ba8884466f2bb1d568cc46b76034711df81bb50c` | Current remote base |
| Settlement + run-log review candidate | `dev/run-log-safety-hardening-20261106` / [Draft PR #40](https://github.com/wosafety0/wosafety0clamp/pull/40) | `1bf11856356f9bd3b0fd7a615567a1ac9c4b9fed` (code commit; see PR for current head) | Open, unmerged |
| SQLite event-id readback candidate | `dev/event-id-readback-20261106` / [Draft PR #39](https://github.com/wosafety0/wosafety0clamp/pull/39) | `6048b918632ffea1510e4b038b82cd30c53a4384` | Separate, open, unmerged |
| SQLite/JSONL row-link follow-up | `dev/identity-contract-and-event-link-20261106` / PR pending | `5d514d4a50572966334aaaa9c16b1e700547aa78` | Stacked on #40; CI pending |

PR #40 is a broad staging candidate (42 commits / 105 changed files), not a merge recommendation. Its exact HEAD passed:
- [Push CI #42128797363](https://github.com/wosafety0/wosafety0clamp/actions/runs/42128797363): 1,634 Python tests, 21 optional vision tests skipped; Node 17/17; compile and CLI smoke passed.
- [Feature CI #42128884084](https://github.com/wosafety0/wosafety0clamp/actions/runs/42128884084): all four Python 3.12/3.13 × vision with/without jobs passed; 1,634 tests per matrix (4 skipped with vision, 21 without); Node 19/19; the missing-vision dependency gate failed closed as expected.

The branch contains historical Fleet 2 / Fleet 4 evidence from earlier operator-authorized work. The current scheduled development run performed **no Poi connection, game input, dispatch, settlement, resupply, or account operation**. The one recorded Fleet 4 loop is not proof of unattended multi-cycle operation.

PR #40 keeps run-log production wiring optional; `--run-log-dir` remains explicit and default-off. The follow-up branch returns the existing SQLite integer event ID and writes it into the JSONL envelope as `sqlite_event_id`; the JSONL UUID `event_id` remains distinct. This is a row-level join only, not semantic deduplication. The current `dispatch_id` still has per-attempt semantics in PR #40, while a durable expedition-lifecycle ID remains unresolved and must not be inferred.

The branch `dev/expedition-closed-loop-mvp` remains historical. Re-read the handoff and progress record before choosing a branch; do not infer application state from `main`.

## Current development priorities

1. Review Draft PR #40 as a broad integration candidate; do not merge it without a per-file review.
2. Resolve the semantic difference between the current per-attempt `dispatch_id` and a durable expedition-lifecycle ID. Do not use the attempt ID to attribute later settlement/resupply events.
3. Verify the SQLite-to-JSONL `sqlite_event_id` row link, including partial-write failure behavior; it must not be treated as semantic deduplication.
4. Keep run-log production wiring default-off until a separately authorized live validation proves the full event chain and failure behavior.
5. Continue offline review of multi-fleet settlement/resupply, interruption recovery, scanner boundaries, and the unresolved 7-ship versus 6-slot design constraint.
6. Keep missing, stale, ambiguous, malformed, or conflicting observations fail-closed. A green CI run is not live-client acceptance.

## Automation tasks and scope

This repository is **not expedition-only**. Expedition automation is the first implemented task; combat, sparkle (morale grinding), fleet composition, and quest automation are declared future tasks. `src/kancollebot/task_scope.py` is the single source of truth for task kinds, per-task fleet policy, and shared infrastructure. Run `kancollebot tasks` for the current status.

Only `expedition` has a reviewed visible-UI path. Every other declared task is **fail-closed**: its fleet policy is deliberately empty and uncalibrated, and `require_task_enabled()` must be called before any UI action. Declaring a task is not implementing it.

Two rules protect the roadmap:

- **Fleet policy is per task.** `RuntimeConfig`'s fleet-permission fields (`non_dispatchable_fleet_ids`, `dispatchable_fleet_ids`, `monitor_candidate_fleet_ids`, `resupply_verified_fleet_ids`) belong to the expedition task. Reusing them for combat would wrongly forbid Fleet 1.
- **Shared infrastructure is classified per symbol, not per module.** `task_scope.SHARED_CAPABILITIES` is the single source of truth: 3 `task_neutral` items, 3 `needs_decoupling` items (fleet snapshot, resupply preflight, supply-tab coordinates), and 2 `task_specific` items deliberately excluded from shared infrastructure. `require_task_neutral_capability()` fails closed when another task tries to reuse a non-neutral symbol. Run `kancollebot tasks` for the current list.

Known work before a second task runs alongside expedition: fleet runtime state is keyed by `fleet_id` alone (`fleet_runtime`, `dispatch_intents`), so a shared fleet-lease / `(task_kind, fleet_id)` key is required; `dispatch_events` needs structured task attribution; and each new task needs its own screen registry (UI states, fingerprints, coordinates, click plans). These are documented in `docs/PROJECT_kancollebot_Progress.md` and are intentionally not implemented yet.

## Current status — 2026-11-06

The current MVP base remains `dev/dispatch-observer-safety-current-mvp-20261103`. Draft PR #40 proposes a separate settlement-drain and run-log integration; it has not been merged.

Historical evidence in the repository records:
- Fleet 2 / mission 1 passed the freshness and requirement gate, but that run stopped before dispatch.
- Fleet 4 / mission 1 completed one operator-supervised live loop: supply, fresh witness, gate, dispatch, return, reward settlement, Home Port, and state reconciliation.
- P0.1 `SettlementDrainSession` is covered by deterministic offline Mock tests.
- P0.2 adds a bounded JSONL run log, event envelope, redaction, retention, atomic snapshot, Tee sink, and critical audit gate.

PR #40's previous exact-head matrices passed 1,634 Python tests. The follow-up branch adds a strict positive-integer SQLite receipt and a sink-owned `sqlite_event_id` field to JSONL; its exact-head CI is pending. The run log remains opt-in. The current `dispatch_id` covers one attempt only; it does not survive restart or correlate later settlement/resupply.

## Current state

This is an actively developed prototype, **not an accepted unattended automation release**. One historical Fleet 4 live loop is not proof of repeated cycles, multi-fleet settlement, interruption recovery, or unattended operation. Production run logging remains default-off. The current automation run did not connect to Poi or perform game input.

Read [docs/HANDOFF.md](docs/HANDOFF.md) and [docs/PROJECT_kancollebot_Progress.md](docs/PROJECT_kancollebot_Progress.md) for exact branch, commit, CI, and blocker evidence.

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

The GitHub Actions workflows run offline checks on pushes to main/dev/** and pull requests targeting main/dev/**. The feature matrix tests Python 3.12/3.13 with and without optional vision dependencies.

## Safety boundaries

- Keep credentials, cookies, tokens, local game data, screenshots, and machine-specific paths out of Git.
- CDP remains loopback-only. Do not use Runtime.evaluate, execute page JavaScript, or access page getters in this development workflow. Passive Network observation may only capture naturally occurring traffic.
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
