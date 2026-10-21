# Offline Expedition Planner MVP

Date: 2026-10-21
Status: IMPLEMENTED_ON_BRANCH; CI_STATUS_MUST_MATCH_LATEST_HEAD
Branch: `dev/expedition-planner-mvp`
Implementation: Python standard library only

## Scope

This increment adds deterministic, side-effect-free readiness evaluation and fleet rotation selection. It is a planning primitive, not a POI integration and not a live expedition executor.

## Readiness decisions

- `WAITING_FOR_RETURN`: observed return time is in the future.
- `RETURNED_AWAITING_REFRESH`: return time elapsed but the snapshot still reports an active mission. A fresh game-state observation is required before planning again.
- `BLOCKED_UNKNOWN_RETURN_TIME`: mission is active but no return time is known.
- `BLOCKED_EMPTY_FLEET`: no ships are observed.
- `BLOCKED_DAMAGED_SHIPS`: one or more fleet ships are marked damaged.
- `NEEDS_RESUPPLY`: fuel or ammunition readiness is false.
- `READY_TO_DISPATCH`: the observed fleet is not on a mission, has ships, has no marked damage, and reports fuel and ammunition ready.

## Fleet rotation

`select_next_expedition_fleet` evaluates all supplied snapshots and selects only from ready fleets. Optional preferred fleet IDs provide deterministic priority; otherwise the lowest ready fleet ID wins. Duplicate fleet IDs and duplicate preferences are rejected. When no fleet is ready, the selection is `None`.

## Safety properties

- The planner performs no I/O, network access, POI calls, or game actions.
- An elapsed timer does not imply the server/client state has updated.
- Unknown mission return time blocks progression.
- All supplied timestamps must be timezone-aware.
- `READY_TO_DISPATCH` and the selected fleet are recommendations only; a future executor must apply the separate safety policy and verify fresh state immediately before any action.
- The planner does not infer repair, resupply, or mission-selection policy.
- Fleet rotation does not mutate snapshots or persist scheduling history.

## Tests

- `tests/test_expedition_planner.py` covers readiness decisions and invalid snapshot combinations.
- `tests/test_expedition_rotation.py` covers deterministic selection, preference handling, blocked fleets, duplicate input rejection, and empty input.
- Tests use Python's standard-library `unittest`.

## Evidence classification

- `IMPLEMENTED`: source, tests, and this document are present on the branch.
- `CI_PASSED`: only claim after checking a successful workflow run for the exact latest branch head SHA.
- `RUNTIME_VERIFIED`: not performed.
- `END_TO_END_VERIFIED`: not performed.
- POI state integration and KCV expedition call-chain research remain pending.
