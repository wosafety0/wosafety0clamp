# Minimal Poi Session-Evidence Extension

Date: 2026-10-22
Status: implementation and Node host-shaped tests added; Poi 12.0.1 runtime compatibility and MCP path visibility are not yet verified. Package naming was checked against Poi's current plugin loader, which requires the package name to start with `poi-plugin-`.

## Purpose

The extension observes only two Poi Redux response action types:

- `@@Response/kcsapi/api_port/port`
- `@@Response/kcsapi/api_get_member/require_info`

Poi's current public response pipeline validates the outer KCSAPI success envelope before dispatching these response actions and normally replaces the envelope with `api_data` in `payload.body`. The extension therefore uses the allowlisted action type and its `payload.time`; it does not re-parse the resolved body or retain body fields.

## Minimal state and expected read path

The reducer extension is registered by Poi's plugin lifecycle under the package name `poi-plugin-kancollebot-session-evidence`. Poi's current public source nests plugin reducer state under `ext.<package-name>._`, so the expected Nanodesu read-only MCP query path is:

```text
ext.poi-plugin-kancollebot-session-evidence._.evidence
```

This path is source-informed, not yet verified against the installed Poi 12.0.1 binary or the local Nanodesu MCP runtime. Query it only after the plugin has been installed and loaded through Poi's supported plugin mechanism. The extension does not create a new listener, HTTP server, token, or dependency. However, the stock Nanodesu MCP plugin currently denies the entire `ext` root and only allows individually reviewed plugin reducers. Therefore this state path is **not readable through an unmodified Nanodesu installation**. Its `ALLOWED_EXT_SUBPATHS` mechanism also currently applies only after a plugin package has been allowlisted, so a narrowly scoped integration needs both an explicit package entry and an exact `_.` state-subpath restriction. Do not treat the source-derived path as an operational bridge until that transport/allowlist change is separately reviewed and tested.

The reducer state nests the evidence under a dedicated `evidence` object so Nanodesu can allowlist that exact subpath rather than the entire extension state. The plugin imports Node's built-in `crypto` module using the broadly compatible `require('crypto')` form rather than the newer `node:` specifier, reducing risk on older Electron/Node runtimes. The stored evidence object has only these fields: `bridgeInstanceId`, `responsePath`, `responseOk`, `observedAt`, `invalidated`, and (only while unavailable) `reason`. It never stores response/request bodies, account IDs, cookies, credentials, or fleet/ship data.

## Lifecycle and safety

- Each main-window plugin load generates a random 128-bit instance ID and starts in an invalidated state.
- A recent allowlisted response creates a minimal evidence record.
- Missing/invalid timestamps invalidate the record.
- Plugin unload emits an invalidation action; Poi's current lifecycle then clears the reducer extension by registering an undefined reducer.
- Other action types are ignored.
- Non-main windows do not initialize evidence.
- The Python reader must still enforce its timestamp freshness limit and fail closed on absent, invalidated, expired, or mismatched evidence.
- This is evidence of a recent successful response action, not a cryptographic proof and not a guarantee that the session remains connected continuously. Another in-process extension could forge an action.
- No game endpoint is called, no game action is dispatched, and no plugin is installed automatically.

## Current integration boundary

The Python expedition reader remains fail-closed and is not wired to this extension. Source inspection of Nanodesu `src/paths.ts` confirmed that `ext` is explicitly denied because arbitrary plugin state is not assumed serializable, with a separate `ALLOWED_EXT_PLUGINS` allowlist. This package is not on that allowlist. The reducer extension can be unit-tested in isolation, but reading it through stock Nanodesu is currently blocked by design. Do not silently widen the external bridge's allowlist or install a separate server without a security review.

## Validation performed in CI

The Node built-in test runner covers initial fail-closed state, allowlisted responses, ignored actions, malformed timestamps, payload-data non-retention, unload invalidation, and non-main-window behavior. This is a host-shaped unit test only.

## Required local verification before enabling reads

1. Load the extension manually in the existing Poi 12.0.1 installation using Poi's supported plugin mechanism.
2. First review and apply a narrowly scoped Nanodesu allowlist change: add this package to `ALLOWED_EXT_PLUGINS` and add only `_.evidence` to its `ALLOWED_EXT_SUBPATHS`. Stock Nanodesu currently denies this `ext` path, so visibility through its existing `poi_get` tool is expected to fail until that explicit change is deployed.
3. Confirm a fresh `api_port/port` or `api_get_member/require_info` response updates `observedAt` and that no raw body fields appear.
4. Unload the extension and confirm the path becomes unavailable/invalidated.
5. After a transport is selected, integrate it with the Python reader and verify ISO-8601 freshness validation; until then the reader must remain fail-closed.
6. When a reviewed transport is available, wire the Python expedition reader to it and validate the ISO-8601 timestamp against a 120-second maximum age. A missing path, invalidated record, unsupported action, future timestamp, or stale evidence must block the read.
7. Repeat on plugin reload and account/session transitions. Do not enable dispatch or relax the session-read gate based on CI alone.

## Source references

- https://github.com/poooi/poi/blob/master/views/redux/create-store.ts
- https://github.com/poooi/poi/blob/master/views/redux/reducer-factory.ts
- https://github.com/poooi/poi/blob/master/views/services/plugin-manager/lifecycle.ts
- https://github.com/momocow/poi-plugin-nanodesu-mcp/blob/main/src/server.ts

## Poi v12.0.1 source-tag compatibility check

A read-only source comparison was performed against the `v12.0.1` tag (not only Poi's moving `master` branch):

- `views/services/plugin-manager/read-plugin.tsx` enforces the `poi-plugin-*` package-name prefix.
- `views/services/plugin-manager/lifecycle.ts` registers and unregisters reducers by `plugin.packageName`, and calls `pluginDidLoad` after reducer registration.
- `views/redux/reducer-factory.ts` wraps each extension reducer under `ext.<packageName>._` and isolates reducer exceptions.
- The response action declarations include the two allowlisted action names used by this extension.

This establishes source-level compatibility evidence for the published tag only. It does **not** prove that a locally installed Poi 12.0.1 binary loads this plugin, that the running process has the expected window context, or that the Nanodesu MCP query returns the path. Those runtime checks remain open and must be performed before enabling the Python reader.
