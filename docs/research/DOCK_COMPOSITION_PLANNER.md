# Dock Composition Planner (offline)

Status: offline planner + read-only verification. **No UI execution.**

## Problem

A fleet can be blocked from an expedition by mission composition rules (minimum
ship count, flagship level, allowed ship types). The existing
`fleet_composition_planner` only handled swapping one non-flagship ship between
two fleets to fix a flagship-level blocker. Nothing could propose recruiting
ships from the dock, and nothing could be checked against the real inventory.

## Data source

`info.ships` is the full owned-ship table (91-92 entries on the verified
account) while `info.fleets` references only the 14 assigned instances, so the
dock inventory is derivable from read-only state alone.

- `kancollebot/adapters/poi/dock_reader.py` reads `info.fleets`, `info.ships`,
  and one `poi_lookup` for `api_stype` of every master ID, then returns fleet
  compositions plus the unassigned dock. Read tools only; no dispatch or input.
- `kancollebot/expeditions/dock_composition_planner.py` is pure and offline.

## Planner contract

`plan_dock_composition(fleet, requirement, dock) -> DockCompositionPlan`

- Selection policy: flagship slot goes to the highest-level qualifying ship (the
  current flagship if it still qualifies); fillers are the lowest-level allowed
  candidates so expensive hulls stay in reserve.
- Removals are limited to ships whose type the mission forbids. When a dock
  flagship is required and the fleet already meets the minimum count, the
  lowest-level remaining ship is swapped out so expedition consumption does not
  grow.
- Every proposal is re-verified with `evaluate_mission_composition`; a plan that
  would not verify is returned as `blocked_plan_failed_verification`.
- Blocked decisions are explicit: `blocked_no_allowed_type_candidate`,
  `blocked_insufficient_dock_ships`, `blocked_no_qualifying_flagship`.
- Actions are proposals only: `remove_ship` (with current slot), `add_ship`
  (with master ID, slot 0 only for a flagship), `set_flagship`.

## Read-only verification tool

```
python -m tools.plan_fleet_composition --fleet 3
python -m tools.plan_fleet_composition --fleet 2 --minimum-ships 5 --flagship-level 1
```

Exit code 0 when the fleet is eligible or a plan exists, 2 when blocked or the
read fails. Verified on the live account: 92 ships, 80 unassigned; fleet 3 was
already eligible; forcing fleet 2 to five ships produced a single `add_ship`
proposal for dock ship 15 (level 1); a level-99 flagship requirement returned
`blocked_no_qualifying_flagship`.

## Not covered

Applying a plan (編成 UI: add/remove/reorder) needs a new CDP adapter and
calibration; it stays gated behind explicit authorization, like any other live
input.
