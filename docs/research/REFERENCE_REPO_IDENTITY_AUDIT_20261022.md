# Reference Repository Identity Audit — 2026-10-22

## Result

**Project identity is established by the user: KCV = 非提助手, and `wosafety0/KCV` is the user's designated decompiled reference repository for 非提助手.** This project must use that identity as its research premise.

The uploaded repository is at branch `upload/kcv-reference-20261022-174555`, commit `5f67fd8c134665d64919445edea061b3ec5eaa06`. Its README begins with “KCA Reverse Engineering”, and the tree contains both `KCA_Decompiled/` and `feitizhushou_Deobfuscation/`, with C# namespaces such as `Grabacr07.KanColleViewer.Actions` and `Grabacr07.KanColleViewer.Models`. These are **metadata, directory-layout, or code-lineage inconsistencies to resolve within the repository**, not grounds to reject the user's stated project identity.

For implementation claims, keep repository identity separate from component lineage: verify which subdirectory, build/version, and call chain a finding belongs to. Do not assume every file in the repository represents the same 非提助手 version or component. The official product page (https://kancolleassistant.mechland.cn/) independently confirms the product name and high-level features, but does not expose its internal call chain.

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

These findings are source-level observations from the uploaded reference repository. Before attributing a specific behavior to a particular 非提助手 version, confirm the containing subdirectory/build lineage and the surrounding call chain. They are not a POI API contract.

1. `AutoSortie.Start()` enables the automation controller; its controller owns current-action state and can stop the current action.
2. `ActAutoSortie` builds ordered `AutoActionQueue` steps. The inspected start sequence clicks a fleet/map UI coordinate, then creates an `ActClickSession` associated with `api_req_map_start`.
3. Progression waits for API-observable events such as `api_req_map_next`, `api_req_sortie_battleresult`, and `api_req_combined_battle_battleresult`, while issuing coordinate-based clicks for continue/retreat/night-fight choices.
4. `ActClickSession.StartAction()` subscribes to a parsed API-session observable, sends a UI click, and completes its delay timer when the matching observable event arrives. This is a UI-driving plus observed-network-event pattern, not a documented POI API contract.
5. The decompiled code contains complex control flow and obfuscated helper names. It is unsuitable as a direct implementation specification without source identity, version, and behavior validation.

## Python design implications

- Do not port these classes or infer that POI exposes these same observables.
- Keep the current Python implementation read-only and pure; retain fail-closed state checks and live actions disabled.
- A future battle design should be built as a tested state machine with explicit observed events, deadlines, cancellation, and recovery. Event names must come from a verified source or a redacted runtime fixture.
- Continue from the user-designated repository, tracing only the smallest call chain needed for the current feature. Do not scan the prohibited legacy path `D:\\KancolleBot\\Source`.
- Keep KCA/Grabacr07 code labeled as supplementary comparison material only. Do not copy third-party source into the Python project.

## Evidence status

- `SOURCE_CONFIRMED`: repository README, tree layout, representative file paths, and the listed event names are present in the uploaded source.
- `USER_CONFIRMED_IDENTITY`: the repository is the user's designated 非提助手 decompilation reference.
- `NEEDS_VALIDATION`: the exact lineage/version represented by each subdirectory and whether individual findings apply to the target build.
- `BLOCKED`: treating every file as one uniform version or treating observed events as a POI API contract.
- No source files were copied into the implementation repository; no local POI connection or real game action was performed.


## Official website cross-check

Official product page: https://kancolleassistant.mechland.cn/ (checked 2026-10-22).

The official page identifies the product as “(非)提助手”, describes automatic expeditions, automatic sorties/ship rotation, and says it supports POI mode. It states that its operations are performed through simulated mouse clicks with randomized coordinate offsets rather than sending game-server packets. The page links to downloadable release packages (v2.6.2, dated 2026-08-19), but the page inspected does not provide a source-code repository link.

This confirms product identity and gives a high-level interaction constraint, but **does not expose the internal automation call chain**. The downloaded package may contain useful local help or assemblies, but no such package has been inspected in this cloud task. Do not infer exact action timing, battle-state transitions, retries, or fleet readiness from the product description alone.

Source status:
- `SOURCE_CONFIRMED`: official page's product description and published feature claims.
- `NEEDS_VALIDATION`: implementation details inside release package and POI mode integration.
- `BLOCKED`: internal source call chain from the website alone.
