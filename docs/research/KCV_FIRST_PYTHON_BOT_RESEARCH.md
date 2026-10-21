# KCV-First Python Bot Research

Date: 2026-10-21
Status: INITIAL_SOURCE_REVIEW
Implementation language: Python
Runtime target: Local Windows + POI
Primary research direction: KCV-related automation logic
Secondary comparison: KCA/kcauto_custom

## Research question

The user wants the new Bot to behave more like the KCV-related automation project, while implementing our own business logic in Python. Do not assume the whole Bot must be written in C#.

## Important identity distinction

The public upstream repository `Grabacr07/KanColleViewer` describes itself as a KanColle browser/viewer built with C# and WPF. Its README describes capturing traffic for display and explicitly says it does not modify traffic or send game-server information as macro/cheat behavior. Its documented features include fleet/ship views, fleet readiness notifications, quest and expedition status, and return notifications. This public upstream repository is therefore useful for state modeling and event/update design, but it is not evidence by itself of the separate Assistant automation behavior present in the user's local reference materials.

Source:
- Repository: https://github.com/Grabacr07/KanColleViewer
- README: https://github.com/Grabacr07/KanColleViewer/blob/develop/README.md

The local path names previously surfaced in the user's preflight include:
- `02_ReverseEngineering\KCA\OriginalReferences_Deobfuscation_Test\Grabacr07.KanColleViewer.Models\Assistant.cs`
- `02_ReverseEngineering\KCA\OriginalReferences_Deobfuscation_Test\Grabacr07.KanColleViewer.Views.Assistant\AutoSortie.xaml.cs`

The directory label `KCA` appears inconsistent with these KCV-related namespaces. Do not rename or move local files automatically. Treat them as `KCV_REFERENCE_REVIEW` until the source project identity and call chain are confirmed.

## Initial source observations

### Fleet model

In `source/Grabacr07.KanColleWrapper/Models/Fleet.cs`, the Fleet model:
- stores fleet identity, name, ship list, sortie flag, fleet state, and expedition state;
- updates identity/name/mission/ships from a raw deck model;
- recomputes fleet state after ship membership changes;
- tracks sortie/homecoming as explicit state transitions.

Interpretation: model state changes are centralized, and dependent fleet state is recalculated when inputs change. This is a useful design pattern for Python domain models; it does not authorize copying the C# implementation.

### Expedition model

In `source/Grabacr07.KanColleWrapper/Models/Expedition.cs`, expedition state:
- treats empty/all-zero mission data as no active expedition;
- derives mission ID and return time from incoming state data;
- exposes remaining time and execution state as derived properties;
- emits a return event based on the timer and configured notification threshold.

Interpretation: keep raw observed data, normalized state, and time-derived values separate. Use injected/fake clocks in Python tests to avoid waiting in real time.

### Fleet state calculation

In `source/Grabacr07.KanColleWrapper/Models/FleetState.cs`, fleet state calculation derives aggregate values such as levels, air superiority potential, speed, and view range. Readiness is updated from fleet composition and activity flags.

Interpretation: calculations should be pure where possible, and readiness should be a decision based on explicit state rather than scattered UI conditions.

## Proposed Python architecture

1. `domain/models.py`: typed, normalized ship/fleet/expedition snapshots.
2. `domain/fleet_state.py`: pure readiness and derived-stat functions.
3. `engine/state_machine.py`: explicit states and allowed transitions; unknown/stale state blocks actions.
4. `engine/expedition_policy.py`: choose an intended expedition and eligible fleet from configuration and observed state.
5. `adapters/poi/`: read-only state adapter first; no unverified protocol or direct game-request replay.
6. `adapters/actions/`: later action interface with live actions disabled by default.
7. `tests/fixtures/`: sanitized synthetic fixtures and deterministic tests.
8. `docs/research/`: evidence table with source, symbol, behavior, Python design implication, and validation status.

## Evidence levels

- `SOURCE_CONFIRMED`: visible in the cited public source.
- `OFFLINE_TEST_PASSED`: tested against deterministic fixtures.
- `RUNTIME_VERIFIED`: observed in the user's local POI/runtime.
- `END_TO_END_VERIFIED`: full local workflow verified with an explicit safe test.
- `NEEDS_VALIDATION`: hypothesis or incomplete evidence.
- `BLOCKED`: missing source/access or unsafe to proceed.

## Next source review tasks

1. Identify the exact project/repository corresponding to local `Assistant.cs` and `AutoSortie.xaml.cs`.
2. Trace the automation entry point, timer/event loop, readiness checks, sortie decision, retry/error paths, and stop conditions.
3. Compare those behaviors with the public KCV fleet/expedition state model.
4. Read KCA expedition/combat flow only as a comparison, not as the primary design.
5. Validate POI data access independently; never infer a POI action API from KCV or KCA code.

## Non-goals for this phase

- No live game action.
- No direct HTTP/WebSocket construction or replay.
- No upload of third-party source trees or local game/session data.
- No claim that source inspection equals runtime validation.
