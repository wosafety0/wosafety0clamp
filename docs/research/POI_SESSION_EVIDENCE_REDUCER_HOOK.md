# Poi Redux Response Hook: Session Evidence Feasibility

Date: 2026-10-22
Status: source-level feasibility confirmed; runtime compatibility unverified.

## Finding

The public Poi source contains an extension-reducer path that can observe Redux actions without monkey-patching `dispatch`:

- `views/redux/create-store.ts` exports `extendReducer(key, reducer)`, which rebuilds the root reducer with an extension under `ext[key]`.
- `views/redux/reducer-factory.ts` passes extension reducers into `combineReducers`; the extension reducer receives the current action.
- `views/redux/actions/response.ts` defines explicit response action types for `@@Response/kcsapi/api_port/port` and `@@Response/kcsapi/api_get_member/require_info`.
- `views/redux/actions/types.ts` defines response payloads with `method`, `path`, `body`, `postBody`, and `time`.

This is a better candidate than inferring liveness from persisted Redux snapshots. Poi explicitly restores `_storeCache` from localStorage in `create-store.ts`, so populated state can exist without a fresh successful game response.

## Proposed bridge behavior

A minimal Poi plugin could register a namespaced extension reducer and retain only a small evidence record:

- a fresh per-plugin-load instance identifier;
- the allowlisted response path;
- an explicit success result derived from the response body;
- the response observation time, after confirming its unit and semantics;
- invalidation state or a hard-unavailable bridge signal on unload/reset.

The reducer must ignore all other actions and must not retain raw response bodies, request bodies, cookies, credentials, or account identifiers. The bridge should expose only the evidence record over a loopback-only authenticated endpoint. Its identity must change on plugin restart, and the reader must fail closed if the bridge disappears or the evidence expires.

## Unresolved proof obligations

1. Verify the exact response body success shape and whether action dispatch occurs only after a fully parsed response in the targeted Poi 12.0.1 build.
2. Verify the semantics and units of the action `time` field against source and a synthetic host-shaped test.
3. Verify extension reducer registration, state visibility, and cleanup on plugin unload in the targeted runtime.
4. Ensure account changes, store reset, failed responses, bridge restart, and missing bridge invalidate old evidence.
5. Exercise the built plugin and a real HTTP client in a Poi-shaped harness; report a graphical Poi 12.0.1 smoke test separately.

## Safety and compatibility boundary

This is a source-level hook, not a stable public plugin API guarantee. Do not enable automation based on this finding alone. Do not loosen the Python reader's fail-closed gate until the bridge implementation and invalidation behavior are tested. No game endpoint is called and no live game action is performed by this research.
