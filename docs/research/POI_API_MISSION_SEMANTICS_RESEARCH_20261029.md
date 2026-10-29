# KanColle `api_mission` Field Semantics — Source Cross-Check

Date: 2026-10-29
Status: RESEARCH_NOTE; LIVE_TARGET_RUNTIME_VALIDATION_PENDING
Scope: Static source/documentation review only. No Poi refresh, game input, or game API request was performed.

## Findings

The current KancolleBot branch's `expedition_state_v2.py` validates `api_mission` as a four-item array, interprets slot 0 as a status, slot 1 as a mission ID, and rejects any active state whose slot 2 is zero. It deliberately leaves `return_at=None` because slot semantics and timestamp units have not been validated against a current authoritative target payload.

Two external sources independently describe the common `api_mission` layout:

1. KanColle Wiki community guide, “Guide to reading Raw API Data”:
   - `api_mission = [s, n, t_f, a]`
   - `s`: expedition state; listed values 0 idle, 1 active, 2 returned, 3 recalled.
   - `n`: expedition ID.
   - `t_f`: final/return time, described as milliseconds from the Unix epoch.
   - URL: https://kancolle.fandom.com/wiki/User_blog:Rephira/Guide_to_reading_Raw_API_Data
2. The public `shichiria/kancolle-browser` source, `src-tauri/src/api/fleet.rs`, implements `parse_expedition()` using slot 0 as the mission state, slot 1 as mission ID, and slot 2 as return time; the source comment explicitly describes the same four-slot layout.
   - URL: https://github.com/shichiria/kancolle-browser/blob/main/src-tauri/src/api/fleet.rs
   - Read-back revision during this review: blob SHA `62556b8d7a979aaeee7a1548a9f430dbc98d0c67`.
3. A separate API sample in `sage417/kancolle-server` includes `api_mission: [0,0,0,0]` in a fleet response, corroborating the idle-array shape but not the active timestamp semantics.
   - URL: https://github.com/sage417/kancolle-server/blob/master/doc/api/battle/map_traveller.txt

## What this changes — and what it does not

- **Positive research result:** slot 0 / slot 1 / slot 2 semantics have corroboration from a community API guide and an independent implementation. This narrows the earlier uncertainty and identifies the exact next validation target.
- **Not official server documentation:** these are community-maintained sources and an independent client implementation, not a current official KanColle API specification.
- **Not live-verified:** the user's current Poi/server response has not been re-captured and correlated with observed expedition transitions in this review. The timestamp unit and meaning must still be checked against a current target response and its timing.
- **Status 3:** the community guide lists a recalled state. The current KancolleBot v2 normalizer rejects statuses outside 0, 1, and 2. Do not expand accepted statuses solely from this note; first establish how the current target represents recalled/returning states and ensure all such states remain non-dispatchable.
- **Timestamp conversion:** do not wire a seconds-vs-milliseconds conversion into the production planner until a current observed sample confirms the units and a test covers the conversion and time-zone behavior. The older `expedition_state.py` and current `expedition_state_v2.py` have different timestamp behavior; do not switch readers or reuse the older conversion without review.
- **Safety:** production `source_freshness_verified` remains false unless the existing live-witness gate passes. Real game actions remain disabled. No game action or API request was initiated.

## Next validation

When a qualifying natural response is available without refreshing or manipulating the game, capture only the relevant sanitized `api_mission` values and correlate slot 2 with the current time and observed expedition status. Until then, treat this note as source-backed research, not runtime acceptance.
