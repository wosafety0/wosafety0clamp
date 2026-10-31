# Passive CDP Network Observer — Design and Audit

Status: DESIGN_ONLY (2026-10-31)
Scope: offline design review for a zero-game-input observer. No live capture, no game input, no request interception, and no game-state mutation are part of this artifact.

## Verified current transport constraints

- `src/kancollebot/adapters/poi/cdp_websocket.py` has an explicit CDP method allowlist. `Network.enable`, `Network.disable`, and `Network.getRequestPostData` are not currently allowlisted.
- `CdpWebSocketTransport.send()` waits for a response whose `id` matches its command, and silently skips messages with another/no matching ID. CDP event messages received during a command are therefore discarded rather than delivered to an observer.
- The current transport is synchronous and single-channel. Merely adding `Network.enable` to the allowlist would not implement event capture; event buffering and bounded event consumption are also required.
- `Page.captureScreenshot` and the fixed read-only Redux snapshot are separately allowlisted. Neither provides passive Network events.
- Request headers and post bodies can contain cookies, authorization values, account identifiers, or other sensitive fields. Raw headers/postData must never be written to the progress log or unrestricted console output.

## Proposed stage-1 design

1. Add an explicit passive-observation interface separate from the game-input facade. Its public API must expose only bounded event summaries, not click methods.
2. Add a bounded FIFO for CDP event messages. Command responses must be matched by command ID; unrelated CDP events must be enqueued, not discarded. Enforce event count, per-message size, and elapsed-time limits.
3. Enable only the CDP Network domain required to observe naturally generated requests. Do not use Fetch interception, request pausing, request modification, replay, or manually constructed game requests.
4. Filter in memory to an exact allowlisted path family (initial candidate: `/kcsapi/api_port/port`, to be confirmed against actual observed traffic) and a small event set such as `Network.requestWillBeSent` and `Network.responseReceived`. URL matching must parse the URL and compare path, not use a loose substring.
5. Do not log full request headers or full post bodies. Emit a redacted summary: timestamp, requestId, method, origin/path, content type, presence/length of body, and only explicitly allowlisted non-secret fields after strict parsing. Never persist cookie, authorization, token, or arbitrary payload fields.
6. `requestWillBeSent.request.postData` may be absent or truncated. If needed, retrieve body only for a matching request via `Network.getRequestPostData`, under size/time bounds and with redaction before output. Missing data is `UNKNOWN`, not evidence of no request.
7. On stop/error/timeout, disable the Network domain if it was enabled by this observer, then close the socket. Cleanup failures must not trigger retries or UI actions.
8. Use fake CDP message streams for unit tests: interleaved events and command responses, absent postData, malformed payloads, sensitive headers, event overflow, timeout, and cleanup. Test that the observer never calls `Input.dispatchMouseEvent`, `Page.reload`, `Runtime.evaluate`, or game APIs.

## Acceptance gates

- Static/unit acceptance: event/response demultiplexing, redaction, bounded memory, bounded duration, and zero-input invariants pass offline tests.
- Live read-only acceptance (separate authorization/session): observe naturally generated traffic without sending game input; report only redacted summaries. A request observed once does not prove a complete or current game state.
- Stage 1 does not authorize dispatch and does not prove mission/fleet selection.
- Do not claim request headers or postData are available until verified from actual CDP events on the current Poi/WebView build.

## Stage-2/3 visual research guardrails

- Three consecutive screenshots can estimate baseline noise but cannot establish a robust threshold alone; sample static and animated regions and measure distributions across multiple windows.
- A global RGB mean or fixed “15% change” is insufficient: animations, hover, loading, scaling, and overlays can produce false positives.
- Relative coordinate anchors are not identity evidence unless viewport dimensions, scale, crop origin, and UI layout have been verified for the current session.
- A coordinate around the expected third mission row can only prove that pixels changed at that location. To infer “mission 3 selected”, the classifier must distinguish selected vs unselected appearance and be evaluated against positive and negative examples for all relevant rows. If not separable, return `UNKNOWN`.
- Do not click to create the differential sample during the offline phase. Use prerecorded/synthetic image pairs and read-only live captures only; no manually annotated templates are assumed.
- Network payload is post-action evidence. If it indicates the wrong mission/fleet, the action may already have occurred; this is a failure report, not a prevention mechanism.

## Current result

- Design and source audit: COMPLETE.
- Observer implementation: NOT_IMPLEMENTED.
- Offline tests for observer: NOT_RUN.
- Live network observation: NOT_RUN.
- UI clicks/reload/game requests during this work: NONE.
