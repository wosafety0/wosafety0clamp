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

### 2.1 Per-fleet target queue count

- `AutoKiraCount` is a four-element per-fleet setting. The constructor initializes each element to **6**.
- `KiraFleet(Fleet)` caps this value at the current fleet's ship count, filters the fleet's low-morale target candidates, and populates `KiraShipsViewModel.Ships` with up to that many target ship IDs.
- The later Kira update path consumes this collection as a target queue. It processes the first target, constructs a temporary formation around that target, and runs `ActAutoKira`; the list is then reevaluated as morale changes.
- Therefore `AutoKiraCount` is the number of target ships to process for that fleet, **not** the number of escorts and **not** the total number of ships in one sortie formation. The default 6 is a configuration default, not a universal recommendation.

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


## 10. Follow-up reconstruction: escort predicate and supply scope

The reference tree also contains a second decompilation output under `feitizhushou_Deobfuscation/`. The corresponding `AutoKira.cs`, `ActAutoKira.cs`, and `ActSupply.cs` were fetched and compared at the same pinned reference commit. This provides additional call-site detail, but the control flow remains obfuscated.

### 10.1 Escort candidate predicate (`DIMp7EBFLK(Ship)`)

The predicate's visible conditions indicate that a fellow candidate is rejected when any of these conditions apply:

- Locked-ship exclusion is enabled and the ship fails the lock check.
- The ship level is not below `AutoKiraLvCondition`.
- Its rarity value does not pass the configured rare-condition comparison.
- It is not ship type ID 2 (the code's destroyer type branch).
- It is in repair.
- HP percentage is below `AutoKiraHPCondition`.
- Fuel or ammunition ratio is below **30%**.

The constructor default for the level threshold is 5 and for HP threshold is 60%. The 30% fuel/ammunition floors are literal predicate constants, not configurable fields in this method.

This explains the purpose of `AutoKiraLvCondition`, `AutoKiraRareCondition`, and `AutoKiraHPCondition` more directly than the settings declarations alone. It also shows that this upstream fellow-candidate predicate is destroyer-specific; it is not evidence that light cruisers are accepted by the same path.

### 10.2 Fellow supply scope

- `AutoKiraSupplyFellows` is passed inverted to `ActSupply(CurrentFleet, !AutoKiraSupplyFellows)`.
- In `ActSupply`, the second parameter is explicitly named `flagShipOnly`. When true, the action adds a click on the flagship-only supply control; when false, it adds the all-ships supply check action.
- Therefore, when `AutoKiraSupplyFellows = true`, the upstream刷闪 supply action is configured to supply the whole fleet, including fellow ships. When false, it uses flagship-only supply.
- The same inverted flag is passed to `ActionHelper.NeedsSupply` before dispatching the supply action. The exact helper overload's internal filtering semantics are not reconstructed here, so no stronger claim is made about its preflight coverage.

### 10.3 KancolleBot implications

- If KancolleBot later supports escort rotation, define escort eligibility explicitly and independently; do not copy the upstream DD-only restriction if the approved strategy allows both DD and CL.
- Consider fuel/ammunition minimum ratios as policy inputs, but choose thresholds through KancolleBot requirements and tests rather than inheriting 30%.
- Distinguish flagship-only supply from whole-fleet supply. A successful supply action must be verified against every selected ship, not inferred from the button click.
- A missing lock observation must remain ineligible even if the upstream UI's configurable lock-exclusion switch is disabled.

## 11. Updated evidence status

- **ADDITIONAL SOURCE_REVIEWED:** second decompilation output for `AutoKira.cs`, `ActAutoKira.cs`, and `ActSupply.cs`; exact blob IDs are recorded in the follow-up research notes and can be fetched from the pinned reference snapshot.
- **CONFIRMED WITH CALL-SITE EVIDENCE:** fellow candidate checks for lock option, level, rarity, destroyer type, repair status, HP, fuel and ammunition; `AutoKiraSupplyFellows` maps to whole-fleet versus flagship-only supply through the inverted `flagShipOnly` argument.
- **STILL NOT VERIFIED:** the obfuscated lock/rarity helper implementations and the exact preflight coverage of the `ActionHelper.NeedsSupply` overload.


## 12. Formation application: visible UI action chain

Additional files were reviewed from the same pinned reference snapshot:

- `ActChangeOrganization.cs` — blob `2235a4e72212bccfd06445567d197bad921fe51c`
- `ActChangeOneShip.cs` — blob `754bfbe9f464c012f4bdbfc51654db39c5405168`

### 12.1 What the generic formation action actually does

- `ActChangeOrganization(fleetID, newShips, forceClearAll, ...)` accepts a target ship-ID array; it does not itself preserve a copy of the original fleet composition.
- Before acting, it rejects duplicate positive ship IDs.
- It compares the live fleet against the requested target and checks whether the target is already satisfied.
- Its action queue navigates to the Organization UI with `ActClickSession`, optionally selects the target fleet, and when `forceClearAll=true` can click the UI control that removes all escorts before rebuilding the requested slots.
- It then queues one `ActChangeOneShip` action per slot that requires a change. `ActChangeOneShip` navigates ship-list pages, applies the configured filter/sort UI, selects the ship, and confirms the change through a UI click/session action.
- After the queue returns to the home port, the completion callback calls `StartAction()` again so the current fleet can be compared with the target. A repeated formation signature or four unsuccessful passes causes an error/stop rather than unbounded retry.
- These actions use visible UI coordinates and session waits; the proxy request identifiers are used as completion evidence for those UI actions.

### 12.2 Kira-specific application and restoration chain

Follow-up call-site tracing now establishes that the generic formation action is used by the Kira scheduler:

- `KiraFleet(Fleet)` copies the current fleet's ship IDs into `uQFpLxEG9U` before building the target queue. The same list is used by `IsShipReserved` while `CurrentFleet` is active.
- `Update()` reads the first target ID from `KiraShipsViewModel.Ships`. `JVjpi4cK7X()` builds a six-slot target array, and the `Update` state machine passes it to `ActChangeOrganization(CurrentFleet, newShips, forceClearAll: true)`.
- Once the formation action completes, the Kira flow proceeds to `K1jp8Rwgts`, which starts `ActAutoKira(CurrentFleet)`.
- The scheduler later creates another `ActChangeOrganization` using `uQFpLxEG9U.ToArray()`. Its completion callback clears the saved list and resets `CurrentFleet = -1`. This is source evidence of a scheduler-level original-formation restoration path.
- The generic `ActChangeOrganization` action itself still does not snapshot the original formation; the snapshot and restore are implemented by the surrounding `AutoKira` scheduler. Because the state machine is control-flow-obfuscated, the exact branch conditions and all interruption paths still need separate reconstruction and runtime validation.

## 13. AutoKiraClearEquips: a real equipment mutation

Additional source: `ActClearEquips.cs`, blob `8e21133fac18034d6d443948022896336b956959`.

The call chain in `AutoKira.cs` is now clearer:

- The check is gated by both `AutoKiraUseFellow` and `AutoKiraClearEquips`.
- It examines the current fleet only when at least two ships are present, then checks the equipment slots of fleet position index 1 (the second ship).
- If that second ship has equipped slots, the scheduler creates `ActClearEquips(CurrentFleet, 1, equippedSlotCount)`.
- `ActClearEquips` uses the visible Remodel UI: open Remodel, choose the fleet, choose the ship slot, remove the equipment, then return to the home port.
- The action uses `api_slotset` for the single-slot case and `api_unsetslot_all` when the equipped-slot count is at least two.

This is not a passive filter and does not mean “clear equipment from every fellow.” At the reviewed call site it clears equipment from the second ship in the current fleet. No re-equipping/restoration step was established in this call path. KancolleBot must not copy this behavior without an explicit equipment snapshot, ownership/slot verification, and a tested restoration plan; otherwise it risks leaving a player's ship unequipped.

## 14. Flagship HP and repair-bucket threshold

Additional source: `ActRepair.cs`, blob `ffd7e4951f29a06bb5dbd896c57de9c114cf4974`.

### 14.1 `AutoKiraHPPercent`

- The helper `s8Upa8I6pu()` reads the current fleet's flagship at ship index 0 and computes current HP divided by maximum HP.
- It returns the “HP needs attention” result when flagship HP is at or below 25%, or when the ratio is below the configured `AutoKiraHPPercent`.
- The scheduler checks this helper before proceeding through its repair/maintenance branch. This is evidence of a pre-dispatch maintenance gate, not proof that this setting is the post-battle retreat threshold.
- The default setting remains 60% from the settings constructor; that is an upstream default, not a KancolleBot policy recommendation.

### 14.2 `AutoKiraRepairTime`

- The configured value is multiplied by 60 and passed to `ActRepair` as seconds.
- The AutoKira repair path also passes the same seconds threshold to `ActionHelper.NeedsUseBucket(remainingRepairTime, secondsToUseBucket)`.
- When that helper returns true, the path creates an `ActUseBucket` action and reports “使用修复桶”.
- Therefore this setting participates in the repair-bucket decision threshold; it is not a generic maximum repair wait.
- The helper implementation is now located: `ActionHelper.NeedsUseBucket` returns `remainingRepairTime >= threshold`. With the default 10-minute setting, a bucket is used when remaining repair time is **at least 10 minutes**; shorter repairs do not meet this bucket condition.

### 14.3 `ActionHelper.NeedsSupply`: exact preflight semantics

Additional source: `ActionHelper.cs`, blob `8fc6c7a57831c627ef50b58aa2fea3c9095a671b`.

- `NeedsSupply(fleet, percent, flagShipOnly)` builds a list containing either all `fleet.Ships` or only `fleet.Ships[0]` when `flagShipOnly=true`.
- With `percent >= 1f` (the AutoKira call passes exactly `1f`), it checks whether every included ship has both fuel and ammunition at maximum. If any included ship is below maximum in either resource, the helper returns true (“supply needed”).
- For `percent < 1f`, it compares each included ship's fuel and ammunition ratios against that percentage and returns true if either ratio is below the threshold.
- A null fleet or an empty fleet returns false. This is an upstream fail-open edge: false can mean “no supply needed” **or** “there was no fleet/ship to inspect.” KancolleBot must treat missing/empty observations as unknown and block dispatch rather than copy this behavior.
- AutoKira calls the helper with `!AutoKiraSupplyFellows` as `flagShipOnly`. Thus `AutoKiraSupplyFellows=true` checks the whole fleet at 100% fuel/ammunition; false checks only the flagship at 100%.

## 15. ActAutoKira: map, battle, retreat, and failure flow

### 15.1 Map selection and battle UI

- The constructor path initializes the map selector variable to `1`; the coordinate calculation selects the first map on the first map page. In this reviewed action, the selected map is therefore 1-1.
- The queue selects the requested fleet, opens Sortie, selects map 1-1, confirms the map, selects the sortie action, and waits for the map-info session.
- It requests the “单纵” formation coordinate through its formation helper.
- The battle queue waits for the battle session and then uses the `Sortie_not_nightfight` UI coordinate while waiting for the battle-result session. This is direct evidence that this path chooses the no-night-battle option; the separate subscription to the midnight-battle event is an observation handler, not evidence that the action deliberately starts night battle.

### 15.2 Post-battle HP stop gate

- After a battle queue finishes, the action increments its battle counter and calls `kR337kpmUA()`.
- That helper iterates the selected fleet's ships and compares the observed friendly HP captured in `HPs_f` with each ship's maximum HP.
- If any present ship is at or below 25% HP, it returns false; the callback then waits for the retreat UI and the `api_port` session.
- If no present ship meets that threshold, the callback advances through the map-next UI/session path and builds the next action queue.
- The counter field is declared and incremented in this file, but the reviewed file contains no read/use of it as a maximum-battle termination condition. Do not infer a configured battle cap from that counter.
- The source establishes map 1-1 selection and a health-based retreat condition, but does not by itself establish a fixed “exactly two battles” rule. The number of battle nodes reached depends on the map flow and observed progression.

### 15.3 Event observation and failure behavior

The action subscribes to parsed proxy event streams for sortie battle, battle result, map start, map next, and midnight battle. Battle-result parsing exceptions stop the action, show a warning to inspect ship status, and log the exception. Queue error callbacks set the action state to Error and, on the retreat/advance paths, call `StopAction()`. The AutoKira-level error callback then enters `GNMpkkXilJ()` / `ActReset`: it resets the UI toward the home port, but does not itself restore the saved fleet formation. The saved snapshot and `CurrentFleet` are not cleared in that handler, so error recovery must not be treated as equivalent to normal formation restoration. The separate cleanup routine `xlTpvgtV0K()` clears `CurrentFleet`, the Kira target queue, and the saved-ID list, but `GNMpkkXilJ()` does not call it. `NeedAction()` returns true whenever `CurrentFleet != -1`; after `ActReset` returns the assistant to Idle, `CanAction()` can therefore consider the operation actionable again. This indicates a possible automatic re-entry/retry path; no explicit retry budget or backoff was found in the reviewed code, but actual repetition depends on the outer assistant scheduler and was not runtime-tested.

This is useful as an observation/error-handling reference only. KancolleBot must continue to keep Poi/CDP observation read-only and perform all game mutations through visible UI actions; it must not reproduce the upstream proxy/request layer.

## 16. Source blob inventory for this follow-up

All files below were fetched from the pinned reference snapshot identified in Section 1:

| File | Blob SHA |
|---|---|
| `AutoKira.cs` (second decompilation output) | `ba9b2094eb26f947e725364398aefa9041c5d7ed` |
| `ActAutoKira.cs` (second decompilation output) | `49ab7708935df2ebd3758a80bc7cbfb970f5521d` |
| `ActChangeOrganization.cs` | `2235a4e72212bccfd06445567d197bad921fe51c` |
| `ActChangeOneShip.cs` | `754bfbe9f464c012f4bdbfc51654db39c5405168` |
| `ActClearEquips.cs` | `8e21133fac18034d6d443948022896336b956959` |
| `ActRepair.cs` | `ffd7e4951f29a06bb5dbd896c57de9c114cf4974` |
| `ActSupply.cs` | `7640d36ed41e77d197d028d91c6199713efd44ea` |

## 17. KancolleBot design consequences and remaining gaps

- Treat formation candidate IDs, the UI formation action, and post-action live-fleet verification as three separate stages.
- Treat equipment clearing as a high-impact mutation with a mandatory restoration design; the upstream behavior is not a safe default.
- Separate the pre-dispatch flagship HP maintenance threshold from the post-battle 25% fleet-wide retreat threshold.
- Model repair-bucket policy explicitly: the reviewed upstream helper uses a bucket when remaining repair time is greater than or equal to the configured threshold. Do not inherit that policy without a KancolleBot-specific decision.
- Do not import the upstream proxy layer. Reimplement only the necessary observation and visible UI behavior behind KancolleBot's Poi adapter and UI controller.
- Still unresolved: whether the outer assistant scheduler repeatedly re-enters the same target after `ActReset`; the precise no-eligible-fellow continuation behavior; complete repair candidate selection; whether any higher-level scheduler imposes a separate battle cap.
- Status remains SOURCE_REVIEWED only. No KancolleBot code changed, no tests run, no Poi/game runtime validation performed, and no R3 files or branches modified.


## 18. Confirmed target-to-formation path and actual fellow count

This follow-up resolves the earlier Section 12.2 uncertainty. The candidate IDs are not merely a display list: they feed the Kira formation builder.

### 18.1 Target queue and flagship assignment

- `KiraFleet(Fleet)` collects the current fleet's original ship IDs in `uQFpLxEG9U`, sets `CurrentFleet`, and adds selected low-morale target IDs to `KiraShipsViewModel.Ships`.
- With the per-fleet filter set to `"等级"`, the target candidates are ordered by descending level. `JVjpi4cK7X()` assigns `KiraShipsViewModel.Ships[0]` to formation slot 0, so the first queued target becomes flagship for that run.
- The update path applies the generated six-slot array through `ActChangeOrganization(..., forceClearAll: true)`, then starts `ActAutoKira(CurrentFleet)`. This is direct call-site evidence of candidate-to-live-formation application.
- The queue is target-oriented: the same fleet can process several target ships sequentially. The first target is not necessarily a user-designated ship unless the queue/order is explicitly controlled.

### 18.2 Fellow selection is at most one unassigned destroyer

When `AutoKiraUseFellow=true`, `JVjpi4cK7X()` searches the organization ship collection and writes only formation slot 1. The other four slots remain `-1` in the returned six-element array. The reviewed function therefore adds **at most one fellow**, not two or three.

The fellow candidate predicate directly observed in this function requires:

- ship type ID **2** (destroyer branch);
- level strictly below `AutoKiraLvCondition`;
- not currently repairing;
- rarity value strictly below the configured rarity condition;
- HP percentage at least `AutoKiraHPCondition`;
- fuel ratio at least **30%** and ammunition ratio at least **30%**;
- a name that does not contain the target flagship's name;
- not locked when `AutoKiraExcludeLockedShip=true`.

The builder then iterates every fleet and removes any candidate whose ship ID is already present in that fleet. The fellow must therefore be **unassigned to every fleet** at the time of selection. The remaining pool is not explicitly sorted before `First()`; the chosen fellow depends on the organization ship enumeration order, not a demonstrated lowest-level or best-fit ranking.

If no candidate remains, slot 1 stays `-1`. The separate `TpUpjAQ341()` pre-check can return false in its no-fellow branch; the obfuscated caller does not establish a universal fail-closed stop for every possible pre-existing formation. KancolleBot should explicitly require the configured number of eligible fellows and stop when that requirement is not met.

When `AutoKiraUseFellow=false`, the builder leaves slots 1–5 as `-1` and constructs a target-only formation.

### 18.3 Original formation restoration and equipment side effect

- **Normal completion:** the original fleet membership snapshot is captured before target processing and later passed back to `ActChangeOrganization` with `forceClearAll: true`. The completion callback clears the snapshot and resets `CurrentFleet`. This confirms a normal-path formation restoration mechanism.
- **Error path:** `GNMpkkXilJ()` logs “自动刷闪执行失败”, sets the assistant state to Error, and starts `ActReset`. `ActReset` performs UI recovery/navigation back to the home port; the reviewed action contains no Organization/formation restoration action. The error handler itself does not clear `uQFpLxEG9U` or reset `CurrentFleet`, and it does not invoke the saved-formation `ActChangeOrganization` path. Therefore the source does **not** guarantee original formation restoration after an error or interrupted sortie; a partially changed formation may remain. The separate `xlTpvgtV0K()` cleanup helper clears the target queue and snapshot, but the error handler does not call it. Since `NeedAction()` treats a non-negative `CurrentFleet` as actionable and `ActReset` returns the state to Idle, the scheduler may re-enter the same target path; this is a potential retry/reset loop, not a runtime-confirmed result.
- The temporary fellow is placed in slot 1. The `AutoKiraClearEquips` branch therefore clears equipment from that fellow in the temporary formation.
- The saved snapshot contains ship IDs, not equipment-slot state. The reviewed restore path restores fleet membership only; it does not restore the fellow's cleared equipment. This is a persistent side effect and a strong reason not to copy upstream equipment clearing into KancolleBot.

## 19. ActionHelper exact helper semantics

Additional source: `ActionHelper.cs`, blob `8fc6c7a57831c627ef50b58aa2fea3c9095a671b`.

- `NeedsSupply` returns true if any included ship is below the requested fuel or ammunition threshold. At the AutoKira call's `percent=1f`, that means both resources must be full for every included ship.
- `flagShipOnly=true` restricts the list to the flagship; false checks every ship in the fleet. Combined with the inverted `AutoKiraSupplyFellows` argument, true means whole-fleet full supply and false means flagship-only full supply.
- Null or empty fleet input returns false rather than an “unknown/not ready” result. This behavior is unsuitable for KancolleBot's fail-closed readiness gate.
- `NeedsUseBucket` constructs a TimeSpan from the configured seconds and returns `remainingRepairTime >= threshold`. The default 10-minute setting therefore uses a bucket for repairs with at least 10 minutes remaining.

## 20. Follow-up source inventory

Additional exact blob IDs from the pinned reference snapshot:

| File | Blob SHA |
|---|---|
| `ActionHelper.cs` | `8fc6c7a57831c627ef50b58aa2fea3c9095a671b` |
| `KiraShipsViewModel.cs` | `02345550530dcabf4d583864a850391b6e6bcfeb` |
| `ActReset.cs` | `61c583e14b59e4f19080b3a63a8de805cfcfdb9b` |
| `AutoExpeditionSettings.cs` (second decompilation output) | `8aa102bdaf2674c491820c82906fd0fe1e81a288` |

Status remains SOURCE_REVIEWED only. No KancolleBot runtime code changed; no tests or Poi/game runtime validation were performed; R3 was not modified.


## 21. Target morale completion threshold

The AutoKira update loop reads the actual `Ship.Condition` value. For the current target:

- If condition is **85 or higher**, the target is removed from the processing queue.
- Otherwise, if condition is greater than or equal to the fleet's configured `AutoKiraTargetCond`, the target is also removed.
- The effective completion threshold is therefore `min(AutoKiraTargetCond, 85)`. A configured target above 85 does not make this loop continue past 85.
- When the target remains below both thresholds, the update loop continues through its repair/supply/formation/sortie handling for that target.

This is a per-target queue completion rule, distinct from the post-battle 25% HP fleet-wide retreat rule in Section 15.2. Source accessor `lA7tMa290FfCHEQoFgif` returns `Ship.Condition`; source file blob is the `AutoKira.cs` blob listed in Section 16.


## 22. Error reset, cleanup, and possible re-entry

Additional call-site evidence from `AutoKira.cs` and `ActReset.cs`:

- `GNMpkkXilJ()` is the shared Kira error handler. It logs the failure, sets `State=Error`, creates `ActReset`, and sets `State=Idle` when the reset action finishes.
- `ActReset` is UI recovery/navigation toward the home port. The reviewed implementation does not perform a fleet Organization change and therefore does not restore the saved original fleet.
- A separate `xlTpvgtV0K()` routine clears `CurrentFleet`, clears `Assistant.Instance.KiraShips.Ships`, and clears `uQFpLxEG9U`. The reviewed `GNMpkkXilJ()` error handler does not invoke this cleanup routine.
- `NeedAction()` returns true whenever `CurrentFleet != -1`. Since the reset completion callback returns the assistant to Idle without clearing that field, `CanAction()` can regard the same Kira task as actionable again. The source therefore exposes a **potential re-entry/retry loop** after an error. No explicit retry counter/backoff was found in this path; whether the outer assistant scheduler repeats it in practice remains unverified.
- KancolleBot design implication: an error must transition to a paused/reconciliation state. Before resuming, re-read the live fleet, current screen, target morale, supply, and saved original formation; never blindly repeat the last click or sortie after a reset.

This section is a static call-flow finding, not a runtime assertion. It does not establish that KCV always loops after every error.


## 23. AutoKira settings defaults and effective supply behavior

Additional source: `AutoExpeditionSettings.cs` (second decompilation output), blob `8aa102bdaf2674c491820c82906fd0fe1e81a288`.

Constructor defaults directly observed:

- `AutoKiraOpen[0] = false`; `AutoKiraOpen[1..3] = true`. By array index, Fleet 1 is disabled by default and Fleets 2–4 are enabled.
- `KiraForSupport[0] = true`; `KiraForSupport[1..3] = false`.
- `AutoKiraCount[0..3] = 6`; `AutoKiraTargetCond[0..3] = 75`; `AutoKiraFilter[0..3] = "等级"`.
- `AutoKiraUseFellow=true`; `AutoKiraClearEquips=true`; `AutoKiraExcludeLockedShip=true`.
- `AutoKiraLvCondition=5`; `AutoKiraHPCondition=60`; `AutoKiraRareCondition="金色"`.
- `AutoKiraHPPercent=60`; `AutoKiraRepairTime=10`.
- The backing field for `AutoKiraSupplyFellows` is declared as a bool and has no assignment in the reviewed constructor or other field-write sites; its default instance value is therefore false unless a saved/user setting overrides it.

Combined with Sections 14.3 and 19, the default `AutoKiraSupplyFellows=false` means the automatic preflight/supply path checks and supplies **only the flagship**, not the temporary fellow. The fellow's candidate predicate only requires fuel and ammunition ratios of at least 30%; it is not guaranteed to be fully replenished by the default policy. This is an upstream resource-saving behavior, not a safe readiness policy to inherit blindly.

These values are defaults only. Persisted settings can override them, so runtime behavior must be based on the loaded settings snapshot rather than constructor defaults.


## 24. Modern Poi MCP adaptation boundary (KanColle-Agent)

Read-only source review of `Yukikaze030/KanColle-Agent`, `main` at commit `835de05a836061f9f57c484edd2455e299ff63fd`. This review does not modify that repository.

### 24.1 Useful data already exposed

The current Poi MCP adapter exposes ship instance/master IDs, level, HP, max HP, condition, locked, damage, fleet membership, repair-dock boolean, equipment slots, and fleet composition. `getFleets` also reports coverage for fleets, ships, and equipment and returns missing ship/equipment identifiers. The tools refuse an unloaded ships domain and return `not_ready` when the fleet domain is not loaded. Those domain-level guards are useful foundations.

### 24.2 Data gaps and unsafe normalization defaults

The current `ShipRecord` schema does not contain per-ship fuel or ammunition; the resource fields are player-wide totals. Therefore the KCV fellow requirement of per-ship fuel and ammo ratios cannot be evaluated from the normalized `ShipRecord` alone.

The Poi bridge currently normalizes missing fields as follows:

- `condition: s.api_cond ?? 49` — missing morale becomes a plausible numeric value.
- `locked: Boolean(s.api_locked)` — missing lock state becomes `false`, indistinguishable from confirmed unlocked.
- `max_hp: s.api_maxhp ?? s.api_max_hp ?? s.api_nowhp ?? 1` — missing maximum HP can fall back to current HP, making a damaged ship appear to have a 100% HP ratio.
- `level: s.api_lv ?? 0` — missing level becomes 0 and can falsely satisfy a low-level fellow filter.
- `dock: repairing.has(s.api_id)` — the repair set is built from `readRepairs`; that reader defaults missing `info.repairs/info.repair` to an empty array, so absent repair data can look like “not in dock.”
- `fleet_id: fleetByShip.get(s.api_id) ?? null` — null can mean unassigned, but can also result when no fleet mapping was loaded. Only interpret it as unassigned after independently confirming complete fleet coverage.

The shared `ShipRecord` type makes `condition`, `locked`, `hp`, and `max_hp` non-optional, so consumers cannot distinguish source values from these fallbacks.

### 24.3 Repair freshness is not independently represented

- `SnapshotStore.DomainName` includes `operations` but no `repairs` domain.
- `setRepairs` stores the repair list and marks the broader operations domain `partial`; it does not establish a dedicated repairs coverage state.
- `getOperations` returns the repairs array without a repairs-specific coverage marker, and `poiGetOperations` returns that result as successful without checking repair completeness.
- `getFleets` coverage includes fleets, ships, and equipment, but not repairs. Therefore `dock=false` is not independently freshness-qualified by the current fleet response.

### 24.4 KancolleBot adapter requirements

For strict fellow eligibility and dispatch safety, do not use the normalized defaults as proof. The adapter should preserve raw field availability or expose tri-state values:

- `condition: number | null`, `locked: boolean | null`;
- `hp/max_hp: number | null`;
- per-ship `fuel/max_fuel` and `ammo/max_ammo` as nullable values;
- `dock: boolean | null` with explicit repairs coverage;
- fleet membership only treated as “unassigned” when fleet-domain coverage is complete.

Add a dedicated repairs freshness/coverage signal and keep domain `updated_at` timestamps available to the safety gate. Until these gaps are closed, use read-only raw Poi store observations for the missing fields or reject the candidate as unknown. Never let missing lock, fuel, ammo, HP maximum, or repair state pass an eligibility gate.

### 24.5 Pinned source blobs

| File | Blob SHA |
|---|---|
| `packages/shared/src/types.ts` | `e6ff7de70a47d92e5121dfcc8c547ae112a01396` |
| `packages/poi-plugin-mcp/poi/store-bridge.cjs` | `6d48c76295e43b8b634286984293cec7102babc1` |
| `packages/poi-plugin-mcp/src/services.ts` | `4130a3c97b3d1cfd4284f15cb17f55e924079e06` |
| `packages/poi-plugin-mcp/src/snapshot.ts` | `efaeef9f005d02b1804a62c7318eeb72ff627c6b` |
| `packages/poi-plugin-mcp/src/tools.ts` | `56eb30450272c52e701bd88876142baca55bff67` |

This is a static source compatibility review. No plugin or KancolleBot adapter code was changed, and no live Poi runtime data was queried.
