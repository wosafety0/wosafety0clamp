# Poi Response Action Payload and Timestamp Audit

Date: 2026-10-22
Status: current public source call chain confirmed; installed Poi 12.0.1 parity and runtime behavior remain unverified.

## Confirmed current-source call chain

1. `lib/game-api-broadcaster.ts` emits `network.on.response` only when it can parse the response body and HTTP status is 200. It supplies `Date.now()`, which is Unix epoch milliseconds.
2. `views/env-parts/data-resolver.ts` registers `handleProxyGameOnResponse` for that event. The handler parses the response and request bodies and queues the timestamp unchanged.
3. `parseResponses` checks the outer response envelope. If `body.api_result !== 1`, it emits `network.invalid.result` and returns without dispatching a response action.
4. On success, it sets `resolvedBody = body.api_data ?? body`, builds `details` with the original timestamp, and calls `dispatch(onGameResponse(details))`.
5. `views/redux/reducer-factory.ts` copies the supplied `body` and `time` into the response action payload. The action types in `views/redux/actions/response.ts` include `@@Response/kcsapi/api_port/port` and `@@Response/kcsapi/api_get_member/require_info`.

## Important correction to the prototype contract

The Redux action's `payload.body` is normally the resolved `api_data`, not the original outer KCSAPI envelope. Therefore checking `payload.body.api_result == 1` is incorrect for ordinary successful response actions and would reject valid evidence. Success is established by the current-source dispatch path filtering the outer envelope before the action is dispatched.

The Python prototype now accepts the resolved body shape. If an unexpected envelope-shaped body explicitly includes `api_result`, it still rejects anything other than exact numeric 1. This is a contract-level test, not proof that the plugin reducer has run inside Poi.

## Remaining compatibility gaps

- The source inspected is the current public branch; it does not establish exact source/binary parity with the installed Poi 12.0.1 build.
- `extendReducer` is a source-level extension mechanism, not a stable third-party API guarantee. Reducer registration, state observation and unload behavior still need runtime validation.
- A reducer cannot independently prove that the host action was not forged by another in-process extension. The evidence is only as trustworthy as the Poi renderer and installed plugins.
- Timestamp units and freshness must be verified against the target build before opening the session-read gate.

## Safety decision

- Keep the session-read gate fail-closed.
- Do not infer session validity from cached Redux data, a populated fleet list, or bridge health.
- Do not install or modify a Poi plugin automatically.
- Do not call game endpoints directly or perform live game actions.

## Sources inspected

- https://github.com/poooi/poi/blob/master/lib/game-api-broadcaster.ts
- https://github.com/poooi/poi/blob/master/views/env-parts/data-resolver.ts
- https://github.com/poooi/poi/blob/master/views/redux/reducer-factory.ts
- https://github.com/poooi/poi/blob/master/views/redux/actions/response.ts
- https://github.com/poooi/poi/blob/master/views/redux/create-store.ts
- https://github.com/poooi/poi/blob/master/views/services/plugin-manager/lifecycle.ts
