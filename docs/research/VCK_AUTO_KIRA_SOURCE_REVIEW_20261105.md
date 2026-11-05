# VCK AutoKira 刷闪源码研究

Date: 2026-11-05  
Status: SOURCE_REVIEWED; KancolleBot integration NOT_IMPLEMENTED; runtime validation NOT_RUN  
Scope: Private upstream reference snapshot; decompiled source inspection only. No upstream source copied into this repository.

## 1. Source and evidence boundary

The reviewed reference is stored in a private upstream repository. This public project record uses the approved neutral alias **VCK** and intentionally omits the private repository URL, real repository identity, and private branch spelling.

- Reference snapshot commit: `cb3aea65e9ed90c94271991441568874abb6e169`
- Reference branch alias: `upload/vck-reference-20261022-174555`
- Reviewed source files and Git blob IDs:
  - `.../Models/AutoExpeditionSettings.cs` — `d379b98561ffe64952a6d3138e2d7f4790b2a1ed`
  - `.../Actions/AutoKira.cs` — `16f766bd0c0e3a442d5a80180786adc415544db1`
  - `.../Actions/ActAutoKira.cs` — `e59f5fb62c927b4e1071065500ab42bee3cf5e3f`

The files are decompiled and control-flow obfuscated. Public field names and directly readable predicates provide stronger evidence than inferred meanings of obfuscated callers. Findings below distinguish direct observations from interpretation.

## 2. Source-confirmed settings and predicates

### 2.1 Per-fleet selection count

- `AutoKiraCount` is a four-element per-fleet setting. The constructor initializes each element to **6**.
- `NotMatchKiraCondition(Fleet)` indexes it by `fleet.Id - 1`; `KiraFleet(Fleet)` uses the same per-fleet value when determining how many ships to select, capped by the fleet's actual ship count.
- This supports interpreting the field as the configured number of ships to include in the Kira selection for that fleet. The default 6 is a configuration default, not a universal recommendation.

### 2.2 Morale target

- `AutoKiraTargetCond` is a four-element per-fleet setting, initialized to **75**.
- It is copied into each `ExpeditionKiraSetting.AutoKiraTargetCond`.
- A `KiraFleet` candidate-selection branch compares ship condition against this per-fleet threshold. Another branch uses a threshold of **50**, depending on fleet ID and the `KiraForSupport` setting.
- Therefore 75 is a source-observed default, not proof that every candidate in every fleet is always filtered at 75.

### 2.3 Ship readiness

- The predicate `Bw91GG8KjO(Ship)` checks that the ship's HP percentage is at least `AutoKiraHPCondition`, then requires fuel and ammunition to be at their respective maximum values.
- The constructor default for `AutoKiraHPCondition` is **60**.
- This predicate is direct evidence of an HP/fuel/ammunition readiness rule. Because the surrounding delegate wiring is obfuscated, this record does not claim that every candidate path invokes it unconditionally.

### 2.4 Locked-ship exclusion

- The source contains the explicit predicate `return !x.IsLocked;`.
- Candidate enumeration checks `AutoKiraExcludeLockedShip`; when enabled, it filters candidates to ships for which `IsLocked` is false.
- The constructor default is `AutoKiraExcludeLockedShip = true`.
- This is configurable behavior, not an unconditional invariant of the upstream application. KancolleBot must impose its own stricter policy: unknown lock state is ineligible, and automation must never unlock a ship.

### 2.5 Fellow ships and candidate filters

- `AutoKiraUseFellow` defaults to true and is consulted by candidate-building logic that adds ships beyond the primary ship.
- `AutoKiraLvCondition` defaults to **5**; `AutoKiraRareCondition` defaults to **"金色"**. The source exposes rarity conversion logic and uses level/rarity filters while constructing candidate selections.
- These are upstream configurable defaults. They do not establish the user's preferred 1–3 level escort rule, nor do they justify copying the upstream defaults into KancolleBot.

### 2.6 Flagship HP threshold

- `AutoKiraHPPercent` defaults to **60**.
- A helper reads the first ship in the current fleet and compares its HP ratio against the configured percentage; it also contains an explicit **25%** lower bound.
- The exact semantic name and all callers of this helper remain partly obscured. Treat this as evidence of a flagship-HP safety predicate, not as a fully reconstructed stop-state machine.

## 3. Action and observation flow

The reviewed `ActAutoKira` code shows an action-queue design:

- It schedules visible-position actions such as `ActClickDelay` and `ActClickSession` for sortie/map navigation.
- It uses `ActWaitSession` to wait for map progression and for the return-to-port session.
- It subscribes to naturally occurring proxy event streams for sortie battle, battle result, midnight battle, map-next, and map-start data. Battle payloads are used by handlers that update tracked HP information and map progression state.
- A battle-result parsing exception stops the action, displays a warning to inspect ship state, and logs the exception.
- Error callbacks set the action state to Error and stop the active action/queue.
- The higher-level `AutoKira` scheduler creates `ActAutoKira(CurrentFleet)`; its completion callback updates sortie quest counters and returns the assistant state to Idle.

These observations support borrowing the separation between visible UI actions, event-driven observation, bounded waits, and explicit error stops. They do **not** establish that every navigation or combat branch has been fully reconstructed from the obfuscated decompilation.

The reviewed files do not prove a hard-coded 1-1 route, a two-day-battle-only policy, or a universal no-night-battle rule. The presence of a midnight-battle subscription means KancolleBot must not infer that the upstream flow never handles night battles.

## 4. KancolleBot design implications

The following are KancolleBot policy decisions, not claims about VCK behavior:

1. Keep Poi observation read-only. Use normal visible UI for all game-state changes; do not construct, replay, or send game-server requests.
2. Preserve unknown morale, HP, supply, lock, fleet membership, and task occupancy as unknown. Never replace missing values with optimistic defaults.
3. Require explicit `api_locked == 0` for escort eligibility. Missing or malformed lock state blocks selection; never auto-unlock.
4. Use the user's planned 1–3 low-level (level 1–3) destroyer/light-cruiser escorts only when all safety conditions pass. Do not copy VCK's default level 5, count 6, target 75, or HP 60 settings as hard-coded policy.
5. Re-read morale, HP, supply, fleet composition, and task occupancy after each return. Do not infer completion from a fixed sortie count or expected morale gain.
6. Keep the 1-1 strategy and its no-voluntary-night-battle preference as an explicit KancolleBot strategy decision, separately validated against current game rules and the visible UI.
7. Implement the strategy layer independently from Poi/CDP/UI adapters. Event observations may inform state only when freshness and session identity are independently verified.

## 5. Reuse assessment

| Area | Reuse value | Boundary |
|---|---|---|
| Per-fleet selection count | High | Re-derive a typed policy field; do not copy upstream defaults |
| Configurable morale target | High | Preserve unknown values and per-fleet scope |
| HP/fuel/ammunition eligibility | High | Implement as explicit KancolleBot predicates with tests |
| Locked-ship exclusion | High | Strengthen to fail-closed unknown handling |
| Visible click + wait-session queue | Medium | Rebuild on the reviewed Poi visible-UI path; do not copy proxy request behavior |
| Battle event/HP observation | Medium | Read-only evidence only; require fresh, session-bound observations |
| Obfuscated control-flow details | Low | Do not port without independent reconstruction and tests |

## 6. Verification status and limitations

- **SOURCE_REVIEWED:** three files were fetched from the stated private reference snapshot; the blob IDs above identify the exact reviewed file contents.
- **NOT_IMPLEMENTED:** no KancolleBot source, runtime configuration, task registration, or UI controller was changed.
- **NOT_TESTED:** no unit, integration, or live-runtime test was run for this research note.
- **NOT_RUNTIME_VERIFIED:** no Poi session, game UI, dispatch, battle, or return flow was exercised.
- **LIMITATION:** decompiled control flow is obfuscated. Several field meanings are strongly indicated by their direct use, but full caller semantics and every branch are not reconstructed.
- **LICENSING / SOURCE HYGIENE:** this record contains analysis and minimal identifiers only. No third-party source code or source archive was copied.

## 7. Project priority

Expedition MVP / R3 remains the active priority. Sparkle remains a declared but unimplemented, fail-closed task. This research is a design input only and does not authorize or start sparkle implementation.


## 8. Follow-up reconstruction: fleet gate and candidate selection

A second pass over the same pinned `AutoKira.cs` blob clarifies several previously open call-site questions.

### 8.1 Fleet eligibility gate (`NeedKira(Fleet)`)

Directly visible checks include:

- The per-fleet `AutoKiraOpen[fleet.Id - 1]` switch must be enabled.
- Fleets currently marked `Sortie` or `Expedition` are rejected from this Kira scheduling path.
- The method walks the fleet's ships and rejects the fleet if a ship is in `Repair` while a matching repair-dock entry is present.
- It also checks `IsShipReserved(ship.Id)`; a reserved ship is not accepted by this eligibility path.
- After the ship scan, it delegates the remaining decision to `NotMatchKiraCondition(fleet)`.

This is stronger evidence than the earlier general statement that the upstream tool has availability checks. It is still not a complete reconstruction of every caller and state transition.

### 8.2 Per-fleet target and low-morale selection

The candidate sequence in `KiraFleet(Fleet)` is directly visible:

- The configured selection count is `AutoKiraCount[fleet.Id - 1]`, capped at the current fleet size.
- For fleet 1, and for fleets whose `KiraForSupport` option is enabled, the source sequence is ships with `Condition < 50`.
- For other fleets, the source sequence is ships whose condition is below that fleet's `AutoKiraTargetCond`.
- When the fleet's `AutoKiraFilter` equals `"等级"`, the candidate sequence is ordered by descending ship level before filling the Kira selection list.
- The selection view model stores the chosen ship IDs; this is selection preparation, not proof of a successful UI fleet composition change.

The `NeedKira` predicate and the `KiraFleet` selection sequence are distinct. A configured target threshold is not applied identically to all fleets.

### 8.3 Consequences for KancolleBot

- Model fleet availability and ship eligibility as separate predicates. A fleet-level gate should reject sortie/expedition occupancy, repair, and reserved/leased ships before any UI action.
- Do not assume the upstream selection count means "number of escorts"; it is the configured number of ships in the selection sequence, capped by fleet size.
- Preserve the difference between fleet 1/support low-morale selection (`< 50`) and other-fleet configured target selection.
- Reconstruct selected IDs and final visible formation separately. A populated selection list is not proof that the game accepted the formation.
- KancolleBot should use its stricter escort rule (level 1–3 DD/CL, explicitly unlocked, HP/supply/task checks) rather than importing this upstream level-descending candidate order.

## 9. Updated evidence status

- **ADDITIONAL SOURCE_REVIEWED:** `NeedKira(Fleet)`, `NotMatchKiraCondition(Fleet)`, and `KiraFleet(Fleet)` call-site excerpts from the same pinned `AutoKira.cs` blob.
- **CONFIRMED WITH CALL-SITE EVIDENCE:** per-fleet enable switch; sortie/expedition exclusion; repair-dock and reserved-ship checks; per-fleet count cap; fleet-1/support `< 50` branch; configured target branch; descending-level ordering when filter is `"等级"`.
- **STILL NOT VERIFIED:** the full semantics of the alternate non-`"等级"` filter branch, every delegate captured by the obfuscated compiler-generated closures, and the exact point at which the selection view model is applied to the visible game fleet.
