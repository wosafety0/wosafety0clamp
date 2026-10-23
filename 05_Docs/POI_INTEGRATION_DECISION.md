# POI Integration Decision Record

**Status:** CDP-first verification selected; no live game actions enabled  
**Date:** 2026-10-23  
**Branch:** dev/expedition-mvp-cdp-first-20261023  
**Scope:** Read-only state observation and offline expedition planning.

## Decision

First verify the existing Poi Chromium DevTools Protocol endpoint at `127.0.0.1:9222` as the lowest-cost read-only state source. Do not develop a separate Poi plugin unless testing shows a concrete blocker: unavailable state, unstable target/context behavior, unacceptable polling cost, or a clear maintenance advantage from a supported plugin API.

Keep the Python planner independent of the transport. The existing `PoiSnapshot` JSON parser remains useful as a normalized boundary and for fixture tests, but its presence does not mean a producer or live adapter is already integrated.

This is a sequencing decision, not a claim that live CDP state reads have passed. Earlier local checks established that the endpoint and a KanColle page target were visible; a complete WebSocket request and validated state extraction still require a read-only smoke test in the user's local Poi session.

## Why CDP-first

1. **Reuse the current host:** Poi remains the game host; no second client or new plugin is introduced prematurely.
2. **Lower initial cost:** use an existing debugging endpoint before adding plugin packaging, plugin lifecycle, and IPC/file-output concerns.
3. **Keep the core transport-neutral:** Python planning receives normalized immutable state rather than handling CDP details directly.
4. **Fail closed:** missing, stale, malformed, or incomplete observations must never be interpreted as permission to act.
5. **Make the plugin decision evidence-based:** only revisit plugin development when a measurable CDP limitation appears.

## Known evidence and limits

### Existing local evidence

The user's prior read-only preflight reported:
- Poi's CDP endpoint on `127.0.0.1:9222` responded to `/json/version` and `/json/list`.
- The target list included the Poi main page and a KanColle webview at `https://play.games.dmm.com/game/kancolle`.
- Local file `03_Integrations/state_fetcher-PoiCDP.py` was described as using CDP and `window.getStore()` to read fleets, ships, ship constants, and materials.
- A separate `action_utils.py` includes click helpers and references the prohibited legacy path `D:\KancolleBot\templates\...`; it must not be run or reused for this read-only task.

These findings establish endpoint/target visibility and the existence of a local state-reader attempt. They do not prove that WebSocket evaluation, all requested fields, or long-running polling are reliable. No game action is permitted in this phase.

### Remote implementation evidence

- `src/kancollebot/models.py` provides immutable normalized state models.
- `src/kancollebot/poi_snapshot.py` validates versioned JSON, timestamps, IDs, HP, and optional resource fields.
- `src/kancollebot/expeditions/planner.py` and `rotation.py` provide offline readiness recommendations and deterministic fleet selection.
- Tests use synthetic data and preserve Japanese names such as 「第1艦隊」 and 「吹雪」.

Remote repository changes do not mean the local project was modified or synchronized.

## Intended architecture

    Existing Poi host
        |
        | CDP 9222, read-only observation during MVP validation
        v
    Read-only state adapter (transport-specific)
        |
        | normalized, validated snapshot
        v
    Python planner and fleet rotation
        |
        v
    Recommendation / simulation only

Keep the CDP transport at the adapter boundary. Do not place target discovery, WebSocket framing, or JavaScript snippets inside expedition business logic. Do not use CDP to click, dispatch, collect, send game API commands, or alter game state during this phase.

## Read-only validation requirements

- Discover the game target from `/json/list`; do not hard-code a changing target ID.
- Restrict evaluation to a pure read-only expression that inspects the expected state store.
- Never inspect, print, or persist cookies, authentication tokens, request headers, or credentials.
- Return only fields needed by the normalized state contract; avoid dumping the entire store.
- Validate the returned structure and timestamp; treat missing fields as unknown.
- Distinguish endpoint reachable, target found, WebSocket connected, state store found, and schema valid. Do not collapse these into one PASS.
- Do not invoke mouse, keyboard, click, dispatch, collect, battle, or other action methods.
- Do not access `D:\KancolleBot\Source`.

## When to reconsider a Poi plugin

Investigate a dedicated plugin only if a read-only CDP prototype demonstrates one or more concrete issues:
- required state is inaccessible or incomplete from the page context;
- target or context changes make polling unreliable;
- connection overhead or reconnection behavior is unacceptable;
- an official, supported plugin API materially improves stability and can expose state without introducing unsafe action paths.

If a plugin is later justified, keep the same versioned snapshot contract and Python planner interface. Prefer an atomic JSON snapshot only if the plugin runtime has a verified supported file-write path; otherwise evaluate a loopback-only IPC alternative.

## Current MVP safety gates

- [ ] Read-only WebSocket connection to the identified KanColle target is verified locally.
- [ ] State-store lookup and a minimal field projection are verified without invoking game actions.
- [ ] Malformed, missing, stale, or incomplete state fails closed.
- [ ] Offline planner and fleet-rotation unit tests pass in CI.
- [ ] No live dispatch, collection, battle action, click automation, or API command transmission is enabled.
- [ ] No secrets, real player-state snapshots, third-party source trees, or local caches are committed.

## Explicit non-goals

- No use of `D:\KancolleBot\Source`.
- No modification of original Poi/KCA installation or cache data.
- No standalone Poi plugin unless evidence shows the CDP-first approach is insufficient.
- No claim of local execution, local file modification, or live-state success based only on remote commits.
