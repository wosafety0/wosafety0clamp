# Expedition Planning and Action Safety MVP

Date: 2026-10-21
Status: IMPLEMENTED_ON_BRANCH; CI_STATUS_MUST_MATCH_LATEST_HEAD
Branch: `dev/expedition-planner-mvp`
Implementation: Python standard library only

## Scope

This increment adds deterministic, side-effect-free fleet readiness evaluation, fleet rotation selection, and a preflight gate for future action adapters. It is not a POI integration and does not execute game actions.

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

## Action preflight

`authorize_planned_action` validates a non-empty action name, requires fresh timezone-aware observed state, and then checks the explicit `SafetyPolicy`. The default policy blocks live actions. If live actions are enabled, explicit confirmation is still required by default. A successful preflight returns evidence metadata only; it does not call POI or execute the action.

## Safety properties

- Planning and preflight perform no I/O, network access, POI calls, or game actions.
- An elapsed timer does not imply the server/client state has updated.
- Unknown mission return time blocks progression.
- Stale and future-dated observations are rejected.
- Fleet readiness and selected fleet are recommendations only.
- Fleet rotation does not mutate snapshots or persist scheduling history.
- A future executor must re-check state at the point of action and provide audit logging and a reliable stop mechanism.

## Tests

- `tests/test_expedition_planner.py`: readiness decisions and invalid snapshot combinations.
- `tests/test_expedition_rotation.py`: deterministic selection, preference handling, blocked fleets, duplicate input rejection, and empty input.
- `tests/test_action_preflight.py`: default-deny policy, explicit confirmation, stale/future observations, and preflight evidence.
- Tests use Python's standard-library `unittest`.

## Evidence classification

- `IMPLEMENTED`: source, tests, and this document are present on the branch.
- `CI_PASSED`: only claim after checking a successful workflow run for the exact latest branch head SHA.
- `RUNTIME_VERIFIED`: not performed.
- `END_TO_END_VERIFIED`: not performed.
- POI state integration and KCV expedition call-chain research remain pending.
