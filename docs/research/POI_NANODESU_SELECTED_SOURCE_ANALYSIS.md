# Nanodesu MCP Selected Source Analysis

Date: 2026-10-25
Branch: `dev/sparkling-planner-mvp`
Source snapshot commit: `30b1ce1c52561520cecf816046350247ec909c07`
Scope: the eight selected files under `research/poi-plugin-nanodesu-mcp/selected-followup/`; source review only, no local Poi/game actions.

## Confirmed architecture

1. **Lifecycle and host integration** — `index.ts` starts the MCP server only in Poi's main window. It resolves `window.getStore`, reads the configured port, optionally loads quest-line data and battle-detail records, and conditionally loads hensei-nikki conversion plus `window.dispatch`. Startup has a 15-second timeout and reports failures in plugin status.
2. **Transport and tools** — `src/server.ts` registers MCP Streamable HTTP at loopback by default, port `12450`, path `/mcp`. The registered read tools are `poi_describe`, `poi_get`, `poi_lookup`, and `poi_battle`. The server also has `GET /health` and writes a port-discovery file unless disabled.
3. **Store boundary** — `src/poi.ts` adapts Poi's globals rather than importing development-only modules. Missing `getStore` is reported explicitly; config reads fall back safely.
4. **Path allowlist** — the selected source's imports show the bridge does not intend to expose the entire Redux store. `src/paths.ts` (present elsewhere in the repository, not in the eight-file export) defines allowed roots and denies sensitive/unsafe roots such as `config`, `layout`, `plugins`, and unrestricted `ext`; specific plugin state is separately allowlisted.
5. **Query and response limits** — `src/tools.ts` sets a default limit of 200 items and a default response cap of 65,536 bytes, with a hard maximum of 262,144 bytes. Master-data lookup requires explicit IDs and caps each call at 200 IDs. Battle reads cap each call at 50 IDs because full records are comparatively large. The serializer drops functions, handles cycles/depth, and truncates collections to fit; oversized single values return an error.
6. **Battle data** — `src/battles.ts` reads individual gzip-compressed JSON records from Poi's `battle-detail` data directory on demand, rather than loading the full archive into the store. Missing/corrupt records are handled per ID by the tool layer.
7. **Request log** — `src/request-log.ts` retains a bounded, in-memory recent request list. It deliberately does not persist entries or attribute them to a client.
8. **One conditional write tool** — `src/hensei.ts` describes `poi_hensei_save`, which dispatches `@@HENSEI_SAVE_DATA` for poi-plugin-hensei-nikki. The server registers this tool only when both dispatch and the plugin's converter are available. This is a real state-changing capability and must remain excluded from KancolleBot's client allowlist.
9. **Validator caveat** — `src/validator.ts` supplies a passthrough JSON Schema validator to avoid an Ajv dependency collision in Poi. Its own comments state it is safe only because the SDK uses it for elicitation responses and this server does not elicit. If elicitation is ever added, this must be replaced with a real validator. Tool arguments still use Zod schemas.

## Integration decisions

- Keep the Python client **read-only by explicit client-side allowlist**: `poi_describe`, `poi_get`, `poi_lookup`, and optionally `poi_battle`.
- Never call `poi_hensei_save` from KancolleBot, even if the server advertises it.
- Default to `http://127.0.0.1:12450/mcp`; reject non-loopback destinations and use bounded connect/read timeouts and response-size limits.
- Initialize MCP and honor the negotiated session/transport protocol before tool calls; do not treat a plain HTTP 200 as proof that tool calls work.
- Use `poi_describe` before selecting paths/fields. Query actual IDs from observed data and resolve them with `poi_lookup`; do not guess field meanings or mix ID spaces.
- Treat bridge readiness, cached store contents, active game session, and data freshness as separate signals. Non-empty cached fleets/ships alone do not prove that the game is currently logged in.
- Keep all game actions disabled until state acquisition, normalization, UI/action verification, and post-action confirmation have independent tests.

## Limits of this review

- This was a source inspection of the committed snapshot, not a runtime test.
- No new files were copied from the installed plugin, no plugin was installed or modified, and no game actions were issued.
- Windows Poi compatibility, the actual live fleet payload, session-validity signal, and end-to-end expedition behavior remain unverified.

## Next step

Inspect the local read-only Python MCP client and its tests, then compare its HTTP/session/error handling against this source contract. Only after offline tests pass should a local loopback health/describe probe be considered; do not dispatch actions.
