# Offline Expedition Planner MVP

Date: 2026-10-21
Status: IMPLEMENTED_ON_BRANCH; CI_PENDING
Branch: `dev/expedition-planner-mvp`
Implementation: Python standard library only

## Scope

This increment adds a deterministic, side-effect-free readiness evaluator for one observed fleet. It is a planning primitive, not a POI integration and not a live expedition executor.

## Decisions

- `WAITING_FOR_RETURN`: observed return time is in the future.
- `RETURNED_AWAITING_REFRESH`: return time elapsed but the snapshot still reports an active mission. A fresh game-state observation is required before planning again.
- `BLOCKED_UNKNOWN_RETURN_TIME`: mission is active but no return time is known.
- `BLOCKED_EMPTY_FLEET`: no ships are observed.
- `BLOCKED_DAMAGED_SHIPS`: one or more fleet ships are marked damaged.
- `NEEDS_RESUPPLY`: fuel or ammunition readiness is false.
- `READY_TO_DISPATCH`: the observed fleet is not on a mission, has ships, has no marked damage, and reports fuel and ammunition ready.

## Safety properties

- The evaluator performs no I/O, network access, POI calls, or game actions.
- An elapsed timer does not imply the server/client state has updated.
- Unknown mission return time blocks progression.
- All supplied timestamps must be timezone-aware.
- `READY_TO_DISPATCH` is a recommendation only; a future executor must apply the separate safety policy and verify fresh state immediately before any action.
- The planner does not infer repair, resupply, ship rotation, or mission-selection policy.

## Tests

`tests/test_expedition_planner.py` covers the decision branches, timestamp validation, and invalid snapshot combinations using only `unittest`.

## Evidence classification

- `IMPLEMENTED`: source and unit tests are present on this branch.
- `OFFLINE_TEST_PASSED`: pending CI result for the branch head.
- `RUNTIME_VERIFIED`: not performed.
- `END_TO_END_VERIFIED`: not performed.
- POI state integration and KCV expedition call-chain research remain pending.
