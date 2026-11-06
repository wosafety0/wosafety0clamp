# DeepSeek Expedition Proposals: Independent Fact Check

Date: 2026-10-22
Status: PUBLIC-SOURCE REVIEW; NO LOCAL INSTALL OR LIVE-GAME TEST

## Executive decision

Adopt the ideas selectively:
- Use the Poi MCP fork's operation recorder as an optional offline evidence source, not as a state-machine generator or replay engine.
- Prefer small, task-specific read tools for the probe; verify actual freshness and response shape instead of equating a successful bridge health check with a valid game session.
- Keep one fleet/one expedition unit as the testable execution boundary, coordinated by a scheduler. The architecture is sound, but the exact claim about kcauto's internal class structure has not been independently confirmed.
- Keep the probe read-only. Do not enable WebView input or Debug eval for initial validation.

## 1. Recorder / "Record play"

Primary sources:
- Fork README: https://github.com/minagi/poi-plugin-mcp/blob/main/README.md
- Recorder specification: https://github.com/minagi/poi-plugin-mcp/blob/main/docs/RECORDING.md
- Recorder implementation: https://github.com/minagi/poi-plugin-mcp/blob/main/lib/poi-interaction-recorder.js
- Automatic interaction recorder: https://github.com/minagi/poi-plugin-mcp/blob/main/lib/poi-auto-interaction-recorder.js

Verified:
- The Minagi fork documents an optional operation-recording feature, disabled by default.
- It records mouse click/drag/hold events and screenshots; it also records observed Poi game.response paths, request parameters and response JSON.
- Its documented session layout includes manifest.json, events.jsonl, frames/, responses/, states/, storage/, and checkpoints/.
- Some state is captured around session start/stop, and selected equipment/UI state is sampled after mouse-up.
- The recorder specification says it does not replay recorded input.

Not supported by the documentation:
- It does not claim to automatically label screenshots as "home port", "expedition selection", or other semantic UI states.
- It does not claim to record a complete fleet snapshot after every mouse event.
- It does not generate a state machine, state.ts, transition guards, or validated click coordinates automatically.
- "Record play" should not be interpreted as a playback/replay facility: the documented feature is recording, not input playback.

Decision:
Use a short, manually annotated expedition recording as an evidence bundle if the fork is deliberately installed and tested. Extract observed transitions and correlate them with game responses offline, but retain manual state labels, explicit uncertainty, and independent tests. Do not install it automatically as part of cloud development.

## 2. Narrow MCP queries / probe

Primary source:
- Tool definitions and implementation: https://github.com/minagi/poi-plugin-mcp/blob/main/lib/mcp-tools.js
- Bridge/tool wiring: https://github.com/minagi/poi-plugin-mcp/blob/main/mcp-server.js
- Fork README: https://github.com/minagi/poi-plugin-mcp/blob/main/README.md

Verified for the inspected Minagi fork:
- `get_fleet_status` accepts `fleetId` 1-4 and returns that fleet's mission, ship summaries and metrics; its documented output includes morale, fuel/ammo, Formula 33 LOS and fighter power.
- `search_ships` supports `minMorale` and `maxMorale` filters.
- `get_resources` returns named resource values.
- `get_all` is described as a last-resort broad account dump; task-specific tools are preferred.
- `get_fleet_status` currently obtains shared ships/equipment/names/master/basic data internally to calculate its projection. Therefore the strongest proven benefit is smaller, relevant result payloads for the caller, not necessarily that the plugin itself reads only one fleet's raw data.

Important limitations:
- These tool names belong to the Minagi fork and must not be assumed to exist in the upstream npm package or the separate nanodesu MCP implementation.
- A successful `/health` response establishes bridge health only. It does not prove the player is logged in, that state is fresh, or that a mission result was accepted.
- The current project branch's first MCP reader was designed around nanodesu-style `poi_describe`, `poi_get`, and `poi_lookup`; it is not yet a verified client for the Minagi fork's `get_fleet_status`, `search_ships`, and `get_resources`. Do not conflate these adapters.

Decision:
Build the probe around a small provider interface. Implement separate adapters for each bridge only after their tool schemas and response contracts are tested. Start with one fleet query plus resources; add morale search only if the rotation policy needs it. Include timestamp, source, response status, truncation/error status, and explicit "session validity unknown" where no freshness/session signal exists.

## 3. Per-fleet execution unit

The proposed `ExpeditionUnit(fleetId, expeditionId)` with a small state machine is a sound modular design. It enables isolated tests with fake state/action providers and prevents the top-level scheduler from containing UI details.

The specific claim that kcauto implements this exact "Execution Unit" class/state model has not been confirmed from an authoritative source in this review. Treat it as a design analogy, not an established fact about kcauto internals.

Recommended boundary:
- Unit: validate observation, evaluate readiness, request at most one dispatch, wait, reconcile result, stop on uncertainty.
- Scheduler: fairness/rotation across fleets, wake-up times, global stop, shared resource policy.
- Read adapter: state only.
- Action adapter: isolated, disabled by default until independently validated.
- Durable journal: reserve before dispatch and persist ambiguous outcomes to block duplicate actions after restart.

The current project already has an offline runner and is adding a durable journal foundation. The remaining work is to wire these pieces together with explicit post-action observation; a method returning successfully is not proof of server acceptance.

## 4. Read-only signals vs visual state

Partly verified:
- Fleet mission data, ship morale, resources and fleet metrics are available from the inspected fork's tools.
- This makes a read-only probe a high-value first integration test and can reduce how often screenshots are needed for state estimation.

Not verified:
- Fleet fields alone cannot safely prove every UI state, game-session validity, mission completion, or successful dispatch.
- The exact mission return timestamp field and units must be validated against source and a sanitized runtime sample before scheduling actions from it.
- A "fleet is not on expedition" observation does not itself prove the correct page is visible or that a UI click target is safe.

Decision:
Use MCP for state facts when the response is fresh and semantically verified; use targeted UI evidence for navigation/action preconditions where data does not establish them. Fail closed on unknown or stale values.

## 5. Revised implementation order

1. Finish offline tests for normalized fleet status, morale, resources, malformed responses and unknown/stale states.
2. Implement a read-only `probe.py` / CLI over a selected bridge adapter; emit compact sanitized JSON/text and no action calls.
3. Verify the selected bridge and its compatibility with Poi 12.0.1 before any installation or runtime claims.
4. If available, manually record one expedition flow with the optional recorder; manually annotate semantic states and correlate recorded game responses.
5. Implement and test one `ExpeditionUnit` with fake read/action adapters.
6. Add durable journal transitions and a post-action state reconciliation loop.
7. Only then validate one real dispatch with explicit live-action gating; expand to fleet rotation after the one-fleet loop is proven.

## Scope and safety

This review is based on public repository source and documentation only. No plugin was installed, no local Poi instance was queried, no recording was made, and no in-game action was performed.