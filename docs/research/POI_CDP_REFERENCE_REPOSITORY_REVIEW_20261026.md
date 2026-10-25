# Poi CDP Reference Repository Review

Date: 2026-10-26
Branch: `dev/sparkling-planner-mvp`
Scope: archive the current reference review for KancolleBot's read-only Poi state-ingestion work. This is a design review, not an implementation or runtime test.

## Decision

Prioritize a small, independently implemented Python state-normalization layer based on observed Poi data and the existing KancolleBot domain models. Do not deploy another forwarding service or copy a third-party codebase wholesale at this stage.

The most relevant reference reviewed in this round is [Yukie23212/KanColle-Discord-RPC](https://github.com/Yukie23212/KanColle-Discord-RPC), because its README describes optional CDP observation of KanColle browser-side API traffic, live-data placeholders, and local mapping/cache concepts. The user's Poi CDP endpoint at `127.0.0.1:9222` was previously verified reachable during a separate local check. This makes CDP-based observation a plausible reference direction, but does not prove that the referenced watcher can be reused directly or that every desired field is available from one response.

## Reference findings

### KanColle-Discord-RPC

- The README documents a Discord Rich Presence manager that can optionally observe game API traffic through CDP.
- Documented derived fields include fleet summaries and HP, ship-instance/master mapping, map and node context, battle rank, expedition results, quest progress, repair-dock status, and resource summaries.
- The README says a field can remain unknown until the corresponding API request is observed; absence of an observed event must not be mistaken for a known zero or a confirmed game state.
- The repository describes local caches for ship/master-name mappings, quest translations, map edges/nodes, and source metadata.
- The inspected watcher file identifies the project license as **CC BY-NC 4.0**. Do not treat its implementation as MIT or as unrestricted reusable code. Prefer documenting concepts and independently implementing the required minimal behavior. Any direct copying/adaptation requires a separate license and attribution review.
- The repository config contains example settings and unrelated sample strings; it is not evidence of the user's configuration and must not be copied into local configuration.

### FUSOU

- Keep this as a secondary reference for typed game-API parsing and fleet snapshots.
- Previously reviewed evidence was not sufficient to claim every module or behavior in the repository has been exhaustively verified.
- Extract only the design ideas that map to the current Python models; do not port the Rust/TypeScript project wholesale.

### kcwiki/kancolle-data

- Its documented namespaces cover static/master data, assets, map data, translations, and wiki data.
- It is a candidate source for static ship/master metadata and names, while Poi remains the source for current live state.
- The reviewed `doc.md` is an API/namespace overview, not a complete field schema. Verify individual fields/functions before relying on them.

### poi-plugin-info-reporter

- The reviewed README describes forwarding raw game API request/response payloads to a configurable HTTP target, with a debug mode that can capture broad `/kcsapi/` traffic.
- It was not installed, configured, or run.
- Because this can expose a broad stream of account/game data, do not enable full raw-payload forwarding merely to obtain a fixture. If ever reconsidered, first design loopback-only collection, explicit endpoint/field minimization, redaction, retention limits, and cleanup.
- It is not needed as the first next step while Poi MCP/CDP are already available.

## KancolleBot integration implications

Existing project findings on `dev/sparkling-planner-mvp` establish:

- The Python MCP client defaults to `http://127.0.0.1:12450/mcp` and has a client-side allowlist of `poi_describe`, `poi_get`, and `poi_lookup`.
- `poi_battle` and `poi_hensei_save` remain excluded from the client allowlist; do not call them as part of this read-only work.
- Existing domain contracts are `ShipState(ship_id, name, hp, max_hp)`, `FleetState(fleet_id, ship_ids, mission_id?)`, and `ObservedState` with a timezone-aware observation time and source.
- A prior bounded live snapshot contained 3 fleet records and 91 ship records. All positive fleet `api_ship` references matched ship-map keys and each referenced record's `api_id`; this is a structural join result for that snapshot only.
- The live checks did not preserve raw payload values or a sanitized fixture. Field semantics that were not verified must not be guessed.

Recommended minimal direction:

1. Use existing Poi MCP/CDP access and source evidence; do not add a new forwarding service yet.
2. Map only fields whose meanings are supported by inspected schemas or a safe, sanitized observation.
3. Keep static/master data lookup separate from current live state.
4. Distinguish transport/bridge health, active-session evidence, observed-field availability, snapshot freshness, and payload completeness.
5. Preserve Japanese game labels where displayed; keep logs free of raw game payloads, account identifiers, cookies, and secrets.
6. Add a small fixture-based normalizer and focused offline tests once a safe sanitized fixture or sufficiently documented field contract is available. Do not invent a synthetic fixture and call it live evidence.

## Safety and execution status

- Repository review only.
- No local files or Poi configuration changed.
- No plugins installed; no receiver started; no raw game payload stream captured.
- No game actions executed.
- No tests run as part of this review.
- This document is an archive of findings and a decision record, not a claim that implementation or end-to-end automation is complete.

## Next step

Return to the KancolleBot mainline: use the already confirmed live fleet-to-ship join structure and existing model contracts to define the smallest evidence-backed normalization boundary. Avoid broad validation loops and avoid further third-party repository surveys unless a specific unresolved field requires them.
