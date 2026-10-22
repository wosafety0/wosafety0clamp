# Minimal Poi Session-Evidence Extension

Date: 2026-10-22
Status: implementation and Node host-shaped tests added; Poi 12.0.1 runtime compatibility and MCP path visibility are not yet verified.

## Purpose

The extension observes only two Poi Redux response action types:

- `@@Response/kcsapi/api_port/port`
- `@@Response/kcsapi/api_get_member/require_info`

Poi's current public response pipeline validates the outer KCSAPI success envelope before dispatching these response actions and normally replaces the envelope with `api_data` in `payload.body`. The extension therefore uses the allowlisted action type and its `payload.time`; it does not re-parse the resolved body or retain body fields.

## Minimal state and expected read path

The reducer extension is registered by Poi's plugin lifecycle under the package name `kancollebot-poi-session-evidence`. Poi's current public source nests plugin reducer state under `ext.<package-name>._`, so the expected Nanodesu read-only MCP query path is:

```text
ext.kancollebot-poi-session-evidence._
```

This path is source-informed, not yet verified against the installed Poi 12.0.1 binary or the local Nanodesu MCP runtime. Query it only after the plugin has been installed and loaded through Poi's supported plugin mechanism. The extension does not create a new listener, HTTP server, token, or dependency.

The stored evidence has only these fields: `bridgeInstanceId`, `responsePath`, `responseOk`, `observedAt`, `invalidated`, and (only while unavailable) `reason`. It never stores response/request bodies, account IDs, cookies, credentials, or fleet/ship data.

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

## Validation performed in CI

The Node built-in test runner covers initial fail-closed state, allowlisted responses, ignored actions, malformed timestamps, payload-data non-retention, unload invalidation, and non-main-window behavior. This is a host-shaped unit test only.

## Required local verification before enabling reads

1. Load the extension manually in the existing Poi 12.0.1 installation using Poi's supported plugin mechanism.
2. Confirm that `ext.kancollebot-poi-session-evidence._` is visible through the existing loopback Nanodesu `poi_get` tool.
3. Confirm a fresh `api_port/port` or `api_get_member/require_info` response updates `observedAt` and that no raw body fields appear.
4. Unload the extension and confirm the path becomes unavailable/invalidated.
5. Confirm the Python reader validates the ISO-8601 timestamp against its maximum age and fails closed when the path is absent.
6. The Python expedition reader now queries this path before every fleet read and validates the ISO-8601 timestamp against a 120-second maximum age. A missing path, invalidated record, unsupported action, future timestamp, or stale evidence blocks the read.
7. Repeat on plugin reload and account/session transitions. Do not enable dispatch or relax the session-read gate based on CI alone.

## Source references

- https://github.com/poooi/poi/blob/master/views/redux/create-store.ts
- https://github.com/poooi/poi/blob/master/views/redux/reducer-factory.ts
- https://github.com/poooi/poi/blob/master/views/services/plugin-manager/lifecycle.ts
- https://github.com/momocow/poi-plugin-nanodesu-mcp/blob/main/src/server.ts
