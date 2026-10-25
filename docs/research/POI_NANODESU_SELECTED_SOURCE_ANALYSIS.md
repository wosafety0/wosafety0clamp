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

1. Run the existing offline Python test suite in the local checkout before changing client behavior.
2. Make a small, isolated hardening change to reject URL userinfo in `PoiMcpClient`, add a regression test, and rerun the offline tests.
3. Decide separately whether `poi_battle` belongs in the read-only client allowlist; it is not currently enabled.
4. Only after offline tests pass, consider a local loopback health/describe probe. Do not dispatch actions.


## Python client cross-check (source inspection, 2026-10-25)

Inspected `src/kancollebot/adapters/poi/mcp_client.py`, `tests/test_poi_mcp_client.py`, `tools/verify_poi_bridge.py`, and `tests/test_poi_bridge_verifier.py` on this branch.

- The client uses only the Python standard library, defaults to `http://127.0.0.1:12450/mcp`, negotiates MCP initialization, sends `notifications/initialized`, caps MCP responses at 2 MiB, and caps health responses at 64 KiB.
- Its current explicit tool allowlist is exactly `poi_describe`, `poi_get`, and `poi_lookup`. It rejects `poi_hensei_save` and unknown tools. **`poi_battle` is not currently callable through this client**, despite being a read-only tool exposed by the bridge; keep this exclusion until a separate change adds it with tests.
- Offline tests cover loopback rejection, the default endpoint, write-tool rejection, initialization notification, tool-level errors, unknown-tool rejection, and redacted structural summaries.
- The verifier probes health, MCP initialization, tool listing, `poi_describe`, and `info.fleets` / `info.ships`; it deliberately marks session validity `NOT_VERIFIED` and summarizes structure instead of printing raw game payload values.
- Review note for a future hardening pass: the client constructor checks loopback by default but does not explicitly reject URL userinfo (username/password), unlike the bridge verifier's URL validator. This is not evidence of credential exposure in current use, but aligning both validators would remove an avoidable inconsistency. Do not change it without a separate targeted patch and offline tests.
- No Python tests were executed during this GitHub source inspection; the statements above describe test coverage visible in source, not a test run result.


## Endpoint credential validation hardening (2026-10-25)

- Updated `src/kancollebot/adapters/poi/mcp_client.py` to reject endpoint URLs containing username/password userinfo, including empty userinfo, before accepting the endpoint.
- Added two offline regression tests in `tests/test_poi_mcp_client.py`: one with `user:secret@127.0.0.1` and one with `@127.0.0.1`.
- Source and test file changes were committed and then fetched back from GitHub to verify the new code is present.
- **Execution status:** tests have not been run in a Python environment during this turn. Do not interpret the presence of tests as a passing test run.
- No local checkout was modified; no Poi runtime was contacted; no game actions were issued.

## Updated next step

Run the offline test suite in the local checkout with the project's existing Python environment. Capture the exact command, exit code, test count, and failures. Only if the suite passes should a read-only loopback health/describe probe be considered. Keep `poi_battle` excluded from the client allowlist until a separately reviewed change and regression tests are approved.


## Offline test execution result (2026-10-25)

- Tested commit: `f61292ae79e20a3af79dd65d825abef623dd2f71` on `dev/sparkling-planner-mvp`; the fetched commit matched the expected reviewed commit.
- Test command: `python -m unittest discover -s tests -p 'test_poi*.py' -v`.
- Environment correction: the first attempt failed during test-module import because the repository's `src` directory was not on Python's import path. This was an invocation/environment issue (`ModuleNotFoundError: No module named 'kancollebot'`), not a failing assertion.
- Rerun set `PYTHONPATH` to the temporary worktree's `src` directory for that test process only. Package import succeeded and all 13 discovered tests passed (exit code 0, unittest summary `Ran 13 tests`, `OK`).
- Coverage included endpoint loopback validation, credential and empty-userinfo rejection, remote endpoint rejection, MCP initialization notification, write-tool and unknown-tool rejection, tool errors, and verifier payload redaction/shape summaries.
- Isolation: temporary detached worktree only; temporary worktree cleanup passed. The original worktree remained clean and unmodified. No dependencies were installed, no Poi runtime was contacted, and no game actions were issued.
- This confirms the selected offline tests on the tested commit only. It does not verify live Poi connectivity, current game-session validity, data freshness, or end-to-end game behavior.

## Revised next step

Perform a separate, explicitly read-only local loopback health/initialization/describe probe only after confirming the user's Poi runtime is available and the endpoint is listening. Do not invoke game actions or `poi_hensei_save`; keep `poi_battle` excluded from the Python client's allowlist unless separately reviewed and tested.
