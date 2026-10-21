# Expedition Cycle Coordinator

Date: 2026-10-21
Status: OFFLINE_COORDINATOR_IMPLEMENTED; POI_ADAPTER_AND_END_TO_END_PENDING
Branch: `dev/expedition-planner-mvp`

## Responsibility boundary

The expedition subsystem is split into:
- `planner.py`: pure readiness evaluation.
- `rotation.py`: deterministic ready-fleet selection.
- `runner.py`: one-cycle coordination behind a narrow adapter protocol.
- `safety/action_preflight.py`: timestamp freshness and live-action policy gate.
- `adapters/poi`: future concrete POI implementation; it must be based on verified interfaces.

## One-cycle behavior

1. Read timestamped fleet snapshots through the injected adapter.
2. Select a ready fleet using the pure planner and rotation module.
3. Block if there is no ready fleet.
4. Check state freshness and live-action policy immediately before dispatch.
5. Require explicit confirmation when the policy requires it.
6. Record an in-memory duplicate guard before calling the adapter.
7. Treat adapter exceptions as ambiguous outcomes; do not automatically retry against the same observation.
8. Require a fresh state read to confirm whether the game accepted the request.

## Status meanings

- `no_ready_fleet`: no eligible fleet in the observed snapshot.
- `blocked_by_preflight`: stale/future state, disabled policy, or missing confirmation.
- `duplicate_blocked`: the same fleet/mission/observation already had a dispatch attempt.
- `dispatch_requested`: adapter returned normally; this is not proof of server acceptance.
- `dispatch_outcome_unknown`: adapter raised after dispatch began; refresh state before deciding what to do.
- `stopped`: this runner instance has been stopped.

## Safety limitations

- The default `SafetyPolicy` still disables live actions.
- The duplicate guard is in-memory only. A future persistent journal and cross-process lock are required before unattended long-running operation.
- No mission eligibility/composition database is included yet; callers must provide a mission ID.
- No concrete POI adapter is included because the actual local POI interface has not yet been verified.
- The runner does not automatically repair or resupply ships, select a mission, or infer a successful dispatch from a return value.
- The coordinator has not been tested against a real game client.

## Tests

`tests/test_expedition_runner.py` uses a fake adapter and Python standard-library `unittest` to test default-deny, confirmation, freshness, stop, no-ready-fleet, duplicate blocking, and ambiguous dispatch errors.
