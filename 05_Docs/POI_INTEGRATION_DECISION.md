# POI Integration Decision Record

**Status:** Selected architecture; implementation gate remains open  
**Date:** 2026-10-23  
**Branch:** research/poi-plugin-bridge-20261023  
**Scope:** Read-only game-state integration. No gameplay actions are authorized by this record.

## Decision

Use a **Poi plugin as the primary state adapter** and a **versioned, atomically written JSON snapshot file** as the initial plugin-to-Python boundary. Keep Python as the planner and automation core. Treat Chrome DevTools Protocol (CDP, currently observed at 127.0.0.1:9222) as a development diagnostic/fallback only, not as the production contract.

This is a selected design direction, not a claim that the plugin bridge already works. The plugin runtime's supported access to Poi's state store and filesystem must be verified in a minimal read-only prototype before implementation is considered integrated.

## Why this direction

1. **Fits the user's host preference:** Poi remains the running game host; no second game client is introduced.
2. **Decouples Python from Chromium internals:** Python reads a documented file schema instead of discovering CDP targets, handling WebSocket details, or evaluating JavaScript in a renderer.
3. **Keeps the boundary read-only:** the first adapter exports observations only. No commands, clicks, API requests, cookies, tokens, or authentication state cross the boundary.
4. **Testable away from the game:** Python parsing, validation, stale-data checks, planning, and safety logic can use fixtures and standard-library unit tests in GitHub Actions.
5. **Supports later replacement:** the Python adapter consumes a normalized snapshot contract; the producer can be changed without rewriting planner logic.

## Evidence and limits

### Existing local evidence supplied by the user

The local preflight found these files under D:\Project_KancolleBot\03_Integrations\:

- state_fetcher-PoiCDP.py: reads window.getStore() fields for fleets, ships, ship constants, and materials via CDP.
- state_fetcher.py: contains a similar CDP state reader, mixed with explanatory text and unrelated SafetyGuard code.
- action_utils.py: contains CDP screenshot/click helpers and hard-coded references into the prohibited legacy path D:\KancolleBot\templates\....

The local Poi plugin directory showed installed third-party plugins, including poi-plugin-noro6-exporter and poi-plugin-forwarder. This establishes that Poi has a plugin ecosystem on the user's machine; it does **not** establish a supported API for this new plugin or prove filesystem/IPC access.

### Current remote repository evidence

- src/kancollebot/models.py defines immutable ShipState, FleetState, and GameSnapshot models.
- tests/test_models.py covers basic model validation.
- .github/workflows/tests.yml runs standard-library unit tests on Python 3.12.
- README.md states that the game client remains local and that mock-first testing is preferred.

The local project and remote repository are not structurally identical. This decision record is stored in the remote repository; it does not assert that the local files were changed or synchronized.

## Proposed data flow

    Poi plugin (read-only state access)
            |
            | versioned JSON snapshot; atomic replace
            v
    User-local state file (not committed or uploaded)
            |
            v
    Python PoiSnapshotAdapter
            |
            v
    Validated immutable GameSnapshot
            |
            v
    Planner / simulation / safety checks

The initial transport is a file because it avoids introducing an always-listening local HTTP service or a new network protocol. This choice is conditional on confirming that the Poi plugin environment supports a safe, supported write path. If it does not, evaluate a loopback-only IPC/HTTP bridge as the alternative; do not silently fall back to CDP as the permanent interface.

## Snapshot contract (version 1 proposal)

The producer should emit one complete JSON document and replace the previous snapshot atomically. It must not expose partial writes.

    {
      "schema_version": 1,
      "source": "poi-plugin",
      "observed_at": "2026-10-23T01:00:00Z",
      "fleets": [
        {
          "fleet_id": 1,
          "name": "第1艦隊",
          "ship_ids": [1],
          "expedition_id": null,
          "in_battle": false
        }
      ],
      "ships": [
        {
          "ship_id": 1,
          "name": "吹雪",
          "hp": 15,
          "max_hp": 15,
          "condition": 49,
          "fuel": 15,
          "ammo": 20
        }
      ],
      "materials": {
        "fuel": 100,
        "ammo": 100,
        "steel": 100,
        "bauxite": 100
      }
    }

The numbers and values above are illustrative fixtures only, not live game data. Preserve Japanese fleet and ship names.

### Contract requirements

- Require schema_version, timezone-aware observed_at, fleets, and ships.
- Reject unknown major schema versions, malformed IDs, duplicate IDs, impossible HP ranges, and invalid numeric values.
- Treat absent optional values as unknown (null), not as zero.
- Reject snapshots older than a configurable freshness limit before any future planner may rely on them.
- The snapshot contains state only; never include cookies, tokens, request headers, credentials, or action commands.
- Write to a temporary file in the same directory, flush/close it, then atomically replace the target file. The reader must handle a missing file, parse failure, and stale snapshot as explicit errors.
- The state file stays outside Git-tracked source paths. Never commit real player state or reference-app caches.

## Implementation stages

1. **Remote-only now:** define the contract and implement/test the Python parser against synthetic fixtures. No Poi runtime is needed for this stage.
2. **Minimal Poi plugin prototype:** inspect the documented plugin entry point and supported store/file APIs; export a synthetic or read-only snapshot only. Do not add gameplay controls.
3. **Local acceptance test:** launch the plugin in the user's existing Poi instance, verify plugin loading and schema correctness, and confirm that no game action is issued.
4. **Planner integration:** connect validated snapshots to planning/simulation only after freshness and failure behavior are tested.
5. **Execution adapter:** keep disabled until separately reviewed and explicitly enabled with preconditions, cancellation, audit logs, and safety checks.

## Acceptance gates

- [ ] Plugin API and state-store access are supported by source/documentation or verified prototype evidence.
- [ ] File output mechanism is verified in the actual Poi plugin runtime.
- [ ] Synthetic fixture parser tests pass in GitHub Actions or a local standard-library test run.
- [ ] Missing, stale, malformed, duplicate-ID, and unsupported-version cases fail closed.
- [ ] A real Poi read-only smoke test confirms the schema without clicks or game API actions.
- [ ] No secrets, real player-state snapshots, or third-party source trees enter Git history.

## Explicit non-goals

- No use of D:\KancolleBot\Source.
- No modification of original Poi/KCA installation or cache data.
- No game actions, click automation, dispatch, battle decisions, or API command transmission in this integration phase.
- No claim that a plugin is installed, loaded, or connected until a real test confirms it.
