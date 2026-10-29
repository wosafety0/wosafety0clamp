# Poi freshness witness acceptance/rejection review — 2026-10-29

## Scope and evidence

Read-only static review of the current branch files:

- `tools/hand_sandbox/poi_port_response_observer_20261029.js`
- `src/kancollebot/adapters/poi/port_response_observer.py`
- `src/kancollebot/adapters/poi/port_freshness.py`
- `src/kancollebot/adapters/poi/expedition_reader.py`
- Existing offline tests for the observer, freshness predicate, reader integration, and diagnostic summary.

No local source was executed in this review; no test suite was run. No Poi refresh, game input, game API request, plugin installation, or configuration change was performed.

## Confirmed acceptance path

1. The observer discovers the local CDP target through loopback `127.0.0.1:9222`, requires exactly one target whose URL is HTTPS on `play.games.dmm.com` and contains `/game/kancolle`, and rejects a non-loopback/unexpected CDP WebSocket endpoint.
2. The observer enables the CDP Network domain only. Static source contains no `Runtime.evaluate` call and explicitly reports that no game input or observer-initiated game API request was sent.
3. It waits for a naturally occurring successful `/kcsapi/api_port/port` response, requires HTTP 200 and a `kancolle-server.com` host/subdomain, skips disk-cache/service-worker/prefetch-cache responses, then requests the body only after `Network.loadingFinished`.
4. It parses the body, builds a compact projection, records response/body timestamps, a SHA-256 digest of the raw response text and byte count, and emits the compact witness to child stdout only when the parent explicitly sets `KCB_POI_FRESHNESS_WITNESS_STDOUT=1`.
5. The Python wrapper accepts exactly one bounded JSON witness from stdout. Missing script, timeout, nonzero exit, missing/duplicate/oversized/invalid JSON output all return no witness.
6. The reader obtains the witness before its MCP reads, validates fleet and ship collections, re-reads both mutable collections, and rejects a visible state change during the observation. It marks `source_freshness_verified=True` only if the witness is recent and the relevant fleet/ship records match exactly. The default reader has no witness provider and remains fail-closed.

## Rejection behavior covered by source and test definitions

- Wrong target count/URL, unexpected CDP endpoint, CDP errors/closure, no natural qualifying response, failed response loading, non-200 status, or cached response.
- Invalid/oversized body or invalid response JSON.
- Missing, duplicate, malformed, oversized, timed-out, or failed child witness output.
- Invalid schema/source/host/path/status/timestamps/hash shape/safety flags; stale or future timestamps; incomplete or duplicate fleet/ship records; mismatched fleet mission/slot or ship mutable fields.
- MCP collection truncation, count mismatch, missing master data, and visible fleet/ship changes between the first read and confirmation read.

These are static source/test-definition findings only. The existing tests were not re-run in this review.

## Limits that remain material

- The freshness predicate checks that `responseBodySha256` has digest-shaped syntax but cannot recompute it because the raw response body is not retained in the compact witness. The digest is metadata from the observer, not an independently revalidated integrity proof.
- The in-memory handoff avoids trusting a historical disk snapshot, but the witness schema does not cryptographically authenticate the child process or its stdout. Treat it as a same-process trust boundary, not a cryptographic attestation.
- The observer writes the compact snapshot to the Desktop snapshot directory even when it also returns the in-memory witness. That file contains fleet mission arrays and selected ship state fields; avoid treating it as freshness proof.
- The reader's confirmation reads detect visible changes but do not provide an atomic Redux snapshot or revision token; an ABA change may go undetected.
- The diagnostic script is an isolated read-only verifier, not a production application entrypoint or a closed-loop expedition executor. No actual application construction path was found in the reviewed files that wires the provider into an expedition runner.
- The current target's natural-response end-to-end witness has not been verified. Existing source and offline tests do not prove current Poi runtime compatibility or real server freshness.

## Conclusion

The static acceptance/rejection path is coherent and deliberately fail-closed when evidence is absent or inconsistent. No production code or safety gate should be loosened on the basis of this review. Keep live dispatch disabled and `source_freshness_verified` false until a naturally occurring response passes the existing live witness gate and the integration is independently verified.

Implementation status: review note only; no code changes.
Validation status: GitHub source readback/static inspection; tests not run in this review; no live witness.
Use limitation: do not use this note as proof of runtime acceptance, cryptographic authenticity, atomic snapshot consistency, or authorization to dispatch.
