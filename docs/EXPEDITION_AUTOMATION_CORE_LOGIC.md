# Expedition Automation Core Logic

**Status:** AUTHORITATIVE BUSINESS-LOGIC BASELINE  
**Scope:** KancolleBot's continuous expedition automation lifecycle  
**Applies to:** The single-process expedition MVP and all subsequent implementation, refactoring, and acceptance work  
**Established:** 2026-11-06

> This document records the approved automation behavior. It is not a claim that every step is already implemented or end-to-end verified. Implementation status and verification evidence must be recorded separately.

## 1. Purpose and authority

This is the core business contract for automated expeditions. The implementation must follow this lifecycle rather than inventing additional scheduling, attribution, or multi-task abstractions.

The game server's actual fleet state is the primary fact source. Poi's fleet state (including `info.fleets` and the relevant `api_mission` state) is used to decide whether a fleet is idle, on an expedition, or awaiting settlement. UI appearance and timers are supporting signals, not substitutes for server state.

Keep two kinds of rules distinct:

- **Game mechanics:** mission-specific fleet composition, ship-type, level, equipment, attribute, and other expedition requirements. These must be sourced from the applicable records in [game-rules/EXPEDITIONS.md](game-rules/EXPEDITIONS.md), [game-rules/FLEET_COMPOSITION.md](game-rules/FLEET_COMPOSITION.md), [game-rules/SHIP_STATE_SUPPLY_FATIGUE.md](game-rules/SHIP_STATE_SUPPLY_FATIGUE.md), and related rule files. Respect each record's evidence status; community-reported or unresolved rules are not silently promoted to verified facts.
- **Automation preconditions:** this project requires full fuel and ammunition, mission-appropriate composition, and no heavily damaged (大破) ship before dispatch. These are automation requirements and must not be misrepresented as universal game rules.

## 2. Complete lifecycle

### Step 1 — Read the fleet state and check dispatch eligibility

For each fleet being considered for dispatch:

1. Read the current fleet state from Poi.
2. Confirm that the observation is source-valid and fresh enough for a dispatch decision. Do not make a decision from an expired or mismatched snapshot.
3. If the fleet is already on an expedition, do not dispatch it again; keep waiting for its timer.
4. If the fleet is idle, check all dispatch preconditions:
   - **Supply:** every participating ship has full fuel and ammunition. If not, perform the normal in-game resupply action, read the state again, and confirm full supply before continuing.
   - **Mission composition:** evaluate the selected mission's actual success requirements against the current fleet. Check the required ship count/types, flagship and fleet levels, equipment, and aggregate attributes where that mission requires them. Do not replace mission-specific rules with a generic fleet-size or flagship-only shortcut.
   - **Ship HP:** no ship in the fleet may be in 大破 state.
5. Dispatch is allowed only after all required checks pass. If a required value is unknown, stale, or unsupported by sufficiently mature game-rule evidence, do not guess that the fleet is eligible.

Full supply and the no-大破 requirement are project automation preconditions. They are not assertions that the game engine universally rejects every under-supplied or damaged escort fleet.

### Step 2 — Dispatch through the normal game UI

When all preconditions pass:

1. Navigate to the expedition UI.
2. Select the intended fleet.
3. Select the configured expedition mission.
4. Complete the dispatch confirmation through the visible game UI.
5. Read Poi again and confirm that the server-side fleet state actually changed to the expected active expedition.
6. Only after that confirmation, record the dispatch timestamp and activate that fleet's expedition timer.

A successful click is not a successful dispatch. If the resulting server state cannot be confirmed, do not start a trusted timer or issue another dispatch blindly.

### Step 3 — Wait for the expedition timer

- While the fleet remains on an expedition and its timer has not expired, put the controller/script into a real sleep for the remaining wait duration (or the next scheduled deadline). Do not poll Poi, inspect CDP state, capture screenshots, classify frames, or perform other read-only probes during this wait. Do not busy-loop.
- When the timer expires, wait an additional **3 seconds** before starting the return-state refresh sequence.
- Timer expiry means only that the system should begin checking for a return. It does not prove that the fleet has returned, that settlement is pending, or that the result has been collected.
- Wake early only for an explicitly supported external shutdown/cancellation signal; do not add periodic screenshot or state polling as a wake-up mechanism.

Each fleet's waiting decision follows its own actual state and timer. A fleet that is still on an expedition must not block an unrelated idle fleet from being considered, subject to the shared UI being operated serially. If no other eligible work exists, sleep until the next fleet deadline rather than repeatedly sampling the screen or game state.

### Step 4 — Refresh the fleet state through Sortie → Home Port

After the additional 3-second delay:

1. Use the normal UI to enter **出击 (Sortie)**.
2. Locate and activate the **母港 (Home Port)** control in the upper-left navigation area.
3. Do not identify or click this control by a fixed coordinate alone. Use the expected navigation region as a search constraint, then recognize the button from visual characteristics such as its brightness, color distribution, contrast, and other stable appearance features. The button may be animated/rotating, so recognition must tolerate changes in orientation and animation frame rather than relying on one static template.
4. Use a deliberately tolerant initial match threshold, then calibrate it against representative live frames. The relaxed threshold is a test hypothesis, not an already-proven setting: verify that it still distinguishes the Home Port control from nearby UI elements and animation/background changes before unattended use. Combine visual match with the expected page/context; do not click on a weak or ambiguous match.
5. Read the fleet state from Poi again, using a fresh observation.
6. Check whether the target fleet is now in the pending-settlement state.

If the target fleet is **not** pending settlement:

- Do not infer completion from the timer or the navigation sequence.
- Repeat **出击 → 母港**.
- Read the fleet state again.
- Continue this refresh-and-read cycle until the target fleet is observed as pending settlement.

The end condition for this step is the server-side pending-settlement state, not merely reaching the Home Port screen.

### Step 5 — Drain all pending expedition settlements

Once a pending-settlement fleet is observed, process settlement through the visible UI.

1. Wait **3 seconds**.
2. Click the center of the game screen once.
3. Recognize the current screen.
4. If the screen is not Home Port, wait 3 seconds and repeat the center-click plus screen-recognition cycle.
5. When the screen is recognized as Home Port, read the Poi state of **all fleets**, not only the fleet that triggered settlement.
6. If any fleet is still pending settlement, continue the settlement loop: wait 3 seconds, perform the prescribed center click, recognize the screen, and re-check all fleet states when Home Port is reached.
7. Finish only when both conditions are true at the same time:
   - the current screen is recognized as Home Port; and
   - no fleet is pending settlement.

This is a global settlement drain: one fleet reaching Home Port does not mean that all fleets have been settled.

The center-click target and its behavior while the Home Port screen is visible must be calibrated against the actual UI before unattended production use. This is an implementation verification of the specified click loop, not permission to invent a coordinate or click an unrelated Home Port control.

### Step 6 — Route each fleet by its current state

After the global settlement completion condition is satisfied, read the fleet states again and decide the next action per fleet:

- **Idle:** return to Step 1. Re-check supply, mission composition, and HP from current data; then dispatch only if all preconditions pass.
- **On expedition:** do not dispatch again. Keep or resume waiting for that fleet's timer; after it expires, repeat Steps 3–5.
- **Pending settlement:** settlement is not complete. Continue Step 5; do not start another dispatch cycle.
- **Unknown, stale, or inconsistent:** obtain a new authoritative observation before any state-changing action. Do not turn an uncertain observation into an assumed idle or completed state.

The loop continues for as long as the automation is running.

## 3. State and action invariants

1. **Server state is authoritative.** Poi fleet state determines lifecycle decisions; UI recognition confirms where the client is, not whether a server-side dispatch or settlement succeeded.
2. **Freshness is mandatory at decision boundaries.** Validate source, timestamp/freshness, required fields, and relevant consistency when reading external game state. Reuse a validated snapshot within one decision; read again after an action that can change game state or when freshness expires.
3. **No click-only success.** Dispatch, return, settlement, and resupply must be confirmed by their relevant postconditions.
4. **No timer-only return.** A timer triggers refresh and inspection; it never establishes return or settlement by itself.
5. **Settlement is fleet-wide.** Do not end settlement while any fleet remains pending.
6. **Home Port is part of the settlement postcondition.** All fleets clear is insufficient if the visible client is not confirmed at Home Port; Home Port is insufficient if any fleet remains pending.
7. **No duplicate dispatch.** A fleet observed on an expedition is waiting, not eligible for another dispatch.
8. **Use normal UI actions.** Game-server traffic may be observed passively, but the automation must not construct, send, replay, or trigger game API requests. Do not use page-reload loops.
9. **Keep the MVP direct.** Implement this lifecycle with the smallest clear controller and the required Poi/UI adapters. Do not introduce speculative multi-task runtimes, fleet leases, complex dispatch attribution, or generic orchestration frameworks unless a demonstrated requirement later needs them.
10. **Keep an audit trail.** Log meaningful state transitions, the observation and decision basis, freshness, UI action, dispatch/timer lifecycle, settlement progress, resupply, and final outcome. Logs must make it possible to reconstruct why the controller took each action.

## 4. Compact control loop

```text
READ FLEET STATE
  ├─ on expedition ───────────────> WAIT FOR TIMER
  ├─ pending settlement ──────────> DRAIN SETTLEMENT
  └─ idle
       └─ CHECK SUPPLY / MISSION COMPOSITION / HP
            ├─ not eligible ──────> DO NOT DISPATCH; resolve and re-check
            └─ eligible
                 └─ DISPATCH VIA UI
                      └─ CONFIRM SERVER STATE = ON EXPEDITION
                           └─ RECORD TIMESTAMP; START TIMER
                                └─ SLEEP UNTIL TIMER EXPIRES (NO SCREENSHOTS / POLLING)
                                     └─ WAIT 3 SECONDS
                                          └─ SORTIE -> VISUALLY RECOGNIZE HOME PORT CONTROL -> CLICK
                                          └─ READ FRESH POI STATE
                                               ├─ not pending -> repeat refresh
                                               └─ pending -> DRAIN ALL SETTLEMENTS
                                                    └─ HOME PORT + NO FLEET PENDING
                                                         └─ READ ALL FLEETS
                                                              ├─ idle -> eligibility check
                                                              └─ on expedition -> wait
                                                                   (repeat continuously)
```

## 5. Implementation and acceptance boundary

This document defines the required behavior, not the current implementation status.

For each implementation stage, report separately:

- which lifecycle steps are implemented;
- which focused tests passed and what they cover;
- whether the Poi freshness and UI state gates are integrated;
- whether the expedition wait uses sleep without screenshot, CDP, or Poi polling;
- whether the animated Home Port control is recognized using rotation-tolerant visual features (including brightness/color), and whether the relaxed threshold was tested against false matches;
- whether the normal UI path was verified;
- whether the complete lifecycle was reproduced in the live game.

A navigation-only test is not a dispatch test. An offline test is not a live-game test. One successful live cycle does not prove repeated or multi-fleet stability.

Any change to this business flow must update this document first or in the same change, explain the behavioral reason, and preserve the distinction between game mechanics and automation policy.


## Change history

### 2026-11-06 — Clarify idle waiting and animated Home Port recognition

- During an active expedition timer wait, the script sleeps until the deadline; no periodic screenshots, CDP inspection, Poi reads, or other redundant read-only probes are performed.
- After timer expiry, the existing additional 3-second delay remains.
- Home Port navigation recognition is not coordinate-only: use the expected screen region plus rotation/animation-tolerant visual characteristics such as brightness and color.
- A relaxed recognition threshold is to be calibrated and tested against live representative frames, including false-positive checks. It is not marked verified until those tests exist.
