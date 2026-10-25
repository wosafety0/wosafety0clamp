# Free Model Provider Direct-Connection Test

Date: 2026-10-25
Branch: `dev/sparkling-planner-mvp`
Scope: one synthetic inference request each to DashScope and Zhipu using explicit direct connections; no real game data.

## Verified results

- Read-only preflight found no process-level HTTP(S)/ALL proxy environment variables set.
- The .NET default proxy resolver reported a configured proxy for both provider URLs.
- A no-network capability check confirmed that `HttpWebRequest.Proxy = $null` is supported.
- A subsequent inference test explicitly set `request.Proxy = $null` for each provider. It made exactly one request per provider, with no retries.
- DashScope: `HTTP 403` both before and during the explicit-direct test.
- Zhipu: `HTTP 429` both before and during the explicit-direct test.
- No API keys were printed. The request contained only a synthetic JSON-format instruction; no game data was sent.
- No Windows global proxy, environment variable, or system setting was modified.

## Interpretation

Explicitly bypassing the .NET default proxy did not change either HTTP status. The available evidence does not support attributing these errors to the default proxy path alone.

- DashScope `403`: authorization, model access, account policy, or another server-side access restriction remains possible; the response body was intentionally not logged, so the exact cause is unknown.
- Zhipu `429`: rate limiting or quota restrictions remain possible; the response body was intentionally not logged, so the exact cause is unknown.

Provider catalog availability does not prove that inference is authorized or that free quota is available.

## Decision / next step

- Do not retry these providers in this round; preserve quota and avoid repeated requests.
- Continue using providers that return parseable inference output. A previous one-request smoke test produced usable output from Mistral; Groq, OpenRouter, and Cerebras returned HTTP success but the client reported empty/unparsed model output, so their response parsing needs a separate review before judging model quality.
- Revisit DashScope and Zhipu only when there is a specific reason to inspect sanitized provider error codes or when quota/permissions may have changed.

## Safety and execution boundary

- No real game payloads, account identifiers, or secrets were included in the prompts or reports.
- No game actions or Poi write tools were called.
- The result documents the observed request outcomes only; it does not prove the underlying cause of either status code.
- This document was archived directly to GitHub. No local project files were modified by the archive operation.
