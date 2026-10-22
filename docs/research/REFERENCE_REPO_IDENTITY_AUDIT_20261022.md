# Reference Repository Identity Audit — 2026-10-22

## Result

**IDENTITY MISMATCH FOUND — do not treat this upload as the primary KCV (非提助手) source.**

The private repository `wosafety0/KCV` currently points to branch `upload/kcv-reference-20261022-174555`, commit `5f67fd8c134665d64919445edea061b3ec5eaa06`. Its README begins with “KCA Reverse Engineering” and describes the directories as KCA material. The recursive tree contains `KCA_Decompiled/` and `feitizhushou_Deobfuscation/`; both contain C# namespaces and project paths such as `Grabacr07.KanColleViewer.Actions` and `Grabacr07.KanColleViewer.Models`.

This is evidence that the uploaded repository contains a decompiled Grabacr07.KanColleViewer/KCA-oriented codebase, not verified source for the user-designated primary reference, KCV = 非提助手 (https://kancolleassistant.mechland.cn/). A folder named `feitizhushou_Deobfuscation` is not sufficient proof of project identity when its source namespaces and the repository README point elsewhere.

## What was inspected

- Repository metadata: `wosafety0/KCV`, private; default branch `upload/kcv-reference-20261022-174555`.
- README: `README.md`, blob `95c0dc2d3610fa867d5c907182ec11f73f0454c5`.
- Recursive tree at uploaded commit: 1,070 entries; the relevant C#/project-file search surfaced 151 paths.
- Representative paths:
  - `KCA_Decompiled/KanColleViewer/Grabacr07.KanColleViewer.Actions/ActAutoSortie.cs`
  - `KCA_Decompiled/KanColleViewer/Grabacr07.KanColleViewer.Actions/AutoSortie.cs`
  - `feitizhushou_Deobfuscation/Grabacr07.KanColleViewer.Actions/ActAutoSortie.cs`
  - `feitizhushou_Deobfuscation/Grabacr07.KanColleViewer.Actions/ActNightFight.cs`
  - `feitizhushou_Deobfuscation/Grabacr07.KanColleViewer.Actions/ActClickSession.cs`

## Call-chain findings from the inspected comparison code

These are **comparison-only findings**, not KCV (非提助手) findings.

1. `AutoSortie.Start()` enables the automation controller; its controller owns current-action state and can stop the current action.
2. `ActAutoSortie` builds ordered `AutoActionQueue` steps. The inspected start sequence clicks a fleet/map UI coordinate, then creates an `ActClickSession` associated with `api_req_map_start`.
3. Progression waits for API-observable events such as `api_req_map_next`, `api_req_sortie_battleresult`, and `api_req_combined_battle_battleresult`, while issuing coordinate-based clicks for continue/retreat/night-fight choices.
4. `ActClickSession.StartAction()` subscribes to a parsed API-session observable, sends a UI click, and completes its delay timer when the matching observable event arrives. This is a UI-driving plus observed-network-event pattern, not a documented POI API contract.
5. The decompiled code contains complex control flow and obfuscated helper names. It is unsuitable as a direct implementation specification without source identity, version, and behavior validation.

## Python design implications

- Do not port these classes or infer that POI exposes these same observables.
- Keep the current Python implementation read-only and pure; retain fail-closed state checks and live actions disabled.
- A future battle design should be built as a tested state machine with explicit observed events, deadlines, cancellation, and recovery. Event names must come from a verified source or a redacted runtime fixture.
- To resume primary KCV research, locate/confirm a source archive whose metadata, README, namespaces, and version identify it as 非提助手. Do not scan the prohibited legacy path `D:\\KancolleBot\\Source`.
- Keep KCA/Grabacr07 code labeled as supplementary comparison material only. Do not copy third-party source into the Python project.

## Evidence status

- `SOURCE_CONFIRMED`: repository README, tree layout, representative file paths, and the listed event names are present in the uploaded source.
- `NEEDS_VALIDATION`: whether any portion of this uploaded material was derived from 非提助手 despite the repository's KCA/Grabacr07 indicators.
- `BLOCKED`: claiming the primary KCV automation call chain from this repository.
- No source files were copied into the implementation repository; no local POI connection or real game action was performed.


## Official website cross-check

Official product page: https://kancolleassistant.mechland.cn/ (checked 2026-10-22).

The official page identifies the product as “(非)提助手”, describes automatic expeditions, automatic sorties/ship rotation, and says it supports POI mode. It states that its operations are performed through simulated mouse clicks with randomized coordinate offsets rather than sending game-server packets. The page links to downloadable release packages (v2.6.2, dated 2026-08-19), but the page inspected does not provide a source-code repository link.

This confirms product identity and gives a high-level interaction constraint, but **does not expose the internal automation call chain**. The downloaded package may contain useful local help or assemblies, but no such package has been inspected in this cloud task. Do not infer exact action timing, battle-state transitions, retries, or fleet readiness from the product description alone.

Source status:
- `SOURCE_CONFIRMED`: official page's product description and published feature claims.
- `NEEDS_VALIDATION`: implementation details inside release package and POI mode integration.
- `BLOCKED`: internal source call chain from the website alone.
