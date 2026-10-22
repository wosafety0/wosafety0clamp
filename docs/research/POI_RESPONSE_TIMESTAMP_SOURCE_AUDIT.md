# Poi Response Timestamp Source Audit

Date: 2026-10-22
Status: partial source confirmation; timestamp propagation to the Redux response action is not yet proven.

## Confirmed source facts

The current public Poi source defines `GameResponsePayload.time` as a JavaScript number in `views/redux/actions/types.ts`.

In `lib/game-api-broadcaster.ts`, `sendRequest` and the successful `sendResponse` path pass `Date.now()` to their emitted events. The response event is emitted only when the response body can be parsed and the HTTP status is 200. The broadcaster therefore uses Unix epoch milliseconds at that event boundary.

In `views/redux/reducer-factory.ts`, `onGameResponse` copies a supplied `time` value into the Redux action payload. In `views/redux/actions/response.ts`, the typed `api_port/port` and `api_get_member/require_info` action creators accept `GameResponsePayload`.

## Remaining gap

The source files inspected so far do not, by themselves, prove the complete call chain that forwards the broadcaster's `network.on.response` event timestamp into the particular Redux response action payload in the exact Poi 12.0.1 build. Nor does current-main source establish binary/version parity with the user's installed build.

Accordingly, treating `payload.time` as epoch milliseconds is a plausible source-informed contract, not yet a runtime-verified guarantee for Poi 12.0.1. The Python extractor currently performs that conversion as a prototype. It must not be used to unblock the session gate until the forwarding path and target-version behavior are verified.

## Safety decision

- Keep the session-read gate fail-closed.
- Do not infer session validity from cached Redux data, a populated fleet list, or bridge health.
- Do not install or modify a Poi plugin automatically.
- Do not call game endpoints directly or perform live game actions.
- Before integration, trace the event-to-action call chain in the target Poi source/version and validate timestamp units using a synthetic host-shaped test or a sanitized local observation.

## Sources inspected

- https://github.com/poooi/poi/blob/master/views/redux/actions/types.ts
- https://github.com/poooi/poi/blob/master/views/redux/actions/response.ts
- https://github.com/poooi/poi/blob/master/views/redux/reducer-factory.ts
- https://github.com/poooi/poi/blob/master/lib/game-api-broadcaster.ts
