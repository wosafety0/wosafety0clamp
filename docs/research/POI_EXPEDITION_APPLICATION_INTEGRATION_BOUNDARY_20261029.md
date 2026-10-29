# Poi expedition application integration boundary — 2026-10-29

## Scope

Read-only review of the current feature branch's `PoiExpeditionReader`, `ExpeditionRunner`, `SafetyPolicy`, `ObservedState`, `PoiMcpClient`, `web_ui.py`, project README, and related offline test definitions. This review did not execute project code or tests, contact the local Poi service, refresh the game, send input, or initiate game API requests.

## Verified architecture boundary

- `PoiExpeditionReader` is explicitly read-only. It reads fleet and ship collections, looks up referenced ship master records, confirms mutable collections with a second read, and can optionally accept a passive in-memory port witness. It has no `dispatch_expedition` method.
- `ExpeditionRunner` depends on an injected `ExpeditionAdapter` protocol requiring both `read_fleets()` and `dispatch_expedition()`. The protocol is a contract, not a concrete Poi action adapter. The reviewed `src/kancollebot/adapters/poi/__init__.py` only declares a sanitized read-only status protocol.
- `SafetyPolicy` defaults `allow_live_actions=False` and requires explicit confirmation by default. `authorize_planned_action()` rejects stale observations and observations without `source_freshness_verified=True` before policy authorization. Runner code also requires a durable action journal when live actions are allowed and blocks ambiguous outcomes from automatic retry.
- `web_ui.py` is a health/status console: its start/pause/stop controls are disabled until a verified runner lifecycle API exists. The README describes it as an early health-diagnostics scaffold.
- `tools/verify_poi_freshness_bridge.py` is an opt-in read-only diagnostic. It wires the passive observer to the reader, prints a sanitized summary, and always reports `game_actions_enabled=false`; it does not construct or run `ExpeditionRunner`.

## Conclusion

The repository currently has separable read-only observation, offline planning/runner logic, and a disabled diagnostic UI, but the reviewed path does not establish an application-level integration connecting the reader to a managed runner lifecycle. A concrete live dispatch adapter is not present in the reviewed Poi adapter files. This is an integration boundary, not a test failure.

## Safe next development slice

1. Add a read-only application composition layer that can instantiate the reader and expose sanitized status/planning results without calling `ExpeditionRunner.run_once()` or any game-action interface.
2. Keep the lifecycle UI controls disabled until lifecycle semantics (start/pause/stop, cancellation, shutdown, and error reporting) have offline tests.
3. Use a fake adapter in tests to prove that default policy and missing freshness evidence never call dispatch.
4. Only after this non-action path is validated should a separate design review consider how an action adapter could be implemented; that later step requires independent safety review and explicit authorization. Do not wire a real game action into the UI as part of this slice.

## Verification state and restrictions

- Implementation status: research note only; no code changed in this review.
- Validation status: GitHub source readback and static inspection only; no tests were run in this review; no current live witness was captured.
- Existing prior offline test results remain historical evidence and were not rerun here.
- Keep `source_freshness_verified` false unless the current in-memory witness gate passes; keep live dispatch disabled.
