# Working Decision — Return to KancolleBot Mainline

Date: 2026-10-25
Branch: dev/sparkling-planner-mvp

## Decision
Stop the free-model response-parser investigation. Do not build a complex parser test harness and do not make more provider requests as part of this detour.

## Model API disposition
- Mistral: one smoke test returned non-empty text; usable only as a provisional option, not yet validated for production reliability or free-quota availability.
- Groq, OpenRouter Free, Cerebras: HTTP requests returned successfully, but the current smoke test did not extract non-empty text. Treat as unconfirmed, not usable dependencies.
- Gemini: current smoke test returned HTTP 404.
- DashScope: returned HTTP 403, including one explicit direct-connect attempt.
- Zhipu: returned HTTP 429, including one explicit direct-connect attempt.
- No automatic retries; do not spend additional daily quota on these unresolved cases.

## Return-to-mainline state
The last confirmed Poi data-structure check remains valid within its limited scope: info.fleets had 3 records and info.ships had 91 records; all 14 positive api_ship references matched the ship mapping keys and corresponding api_id values. This does not establish session freshness or end-to-end automation readiness.

Resume the existing Poi integration mainline. Keep poi_describe, poi_get, and poi_lookup as the only allowed Poi MCP tools. Keep poi_battle and poi_hensei_save disabled. Do not infer undocumented game-state semantics or start a normalizer without a safe sanitized fixture. Prefer the next smallest mainline task that advances stable Poi integration without game actions or broad source copying.

## Scope and evidence
This record summarizes already observed results only. No provider API calls were made while writing this decision. No game actions were performed.
