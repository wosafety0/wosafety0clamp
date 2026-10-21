# KCV（非提助手）优先的 Python Bot 研究计划

Date: 2026-10-21
Status: IDENTITY_CORRECTED; SOURCE_CALL_CHAIN_RESEARCH_PENDING
Implementation language: Python
Runtime target: Local Windows + POI
Primary reference: KCV（非提助手）
Secondary reference: KCA / kcauto_custom

## Correct project identity

The user explicitly defines **KCV as 非提助手**, with official site:
https://kancolleassistant.mechland.cn/

Do not confuse this with the separate public `Grabacr07/KanColleViewer` project. That Viewer is not the primary automation project requested by the user.

KCA refers to `kcauto_custom`:
https://github.com/XVs32/kcauto_custom

Use KCA as a supplementary implementation and cross-check when KCV behavior is hard to understand or evidence is missing. Do not replace KCV-first research with KCA-first implementation, and do not attribute KCA-only behavior to KCV.

## Existing local research material and boundaries

The user previously identified the historical KCV source root as `D:\\KancolleBot\\Source\\KCV` and the deobfuscation report directory as `D:\\KancolleBot\\Reports\\KCV_Deobfuscation`. These are in the old project tree. Per user instruction, do not automatically scan, read, or modify the old `D:\\KancolleBot\\Source` tree. Continue from material already present in the current project, supplied files, and existing research records.

The user specifically confirmed these current-project files as KCV-related Assistant automation research material:
- `02_ReverseEngineering/KCA/OriginalReferences_Deobfuscation_Test/Grabacr07.KanColleViewer.Models/Assistant.cs`
- `02_ReverseEngineering/KCA/OriginalReferences_Deobfuscation_Test/Grabacr07.KanColleViewer.Views.Assistant/AutoSortie.xaml.cs`

Despite the historical directory name containing `KCA`, do not classify those files as kcauto_custom source. Preserve them as local research references; do not upload third-party source files to GitHub until license and necessity are reviewed. Upload our own analysis and minimal evidence instead.

## Research goals

Trace KCV's actual automation behavior and document:
1. Program entry points and task scheduling/event loop.
2. State synchronization and stale-state handling.
3. Fleet readiness and eligibility checks.
4. Expedition return, resupply and redispatch.
5. Sortie selection, battle progression, retreat/continue conditions.
6. Retry/error handling, timeouts, safe stop and recovery.
7. Which behaviors rely on KCV-specific integration and which can be implemented independently in Python.

## Evidence discipline

- `SOURCE_CONFIRMED`: directly supported by source or official documentation.
- `OFFLINE_TEST_PASSED`: deterministic tests passed on synthetic fixtures.
- `RUNTIME_VERIFIED`: observed in the user's local POI/runtime.
- `END_TO_END_VERIFIED`: complete local workflow tested with an explicit safe test.
- `NEEDS_VALIDATION`: incomplete evidence or hypothesis.
- `BLOCKED`: source/access unavailable or unsafe to proceed.

Reading a README or an unrelated viewer's source does not establish KCV automation behavior. Record source URL, version/commit, file, symbol, call chain, expected behavior, failure path, and Python design implication.

## Python implementation approach

- Use pure Python domain models and policy functions for the logic.
- Build an explicit state machine; unknown or stale state blocks actions.
- Add deterministic tests for fleet readiness, expedition timing, scheduling, retries and stop conditions.
- Keep POI adapter read-only until its actual interface is verified.
- Keep live actions disabled by default and add audit logs and a reliable stop mechanism before any real operation.

## KCA supplementary use

Consult `kcauto_custom` when:
- KCV source is obfuscated or a call chain is difficult to trace;
- a specific expedition/combat behavior needs an independent comparison;
- KCV evidence is missing and KCA offers a concrete, testable alternative.

For every borrowed design, label whether it is KCV-confirmed, KCA-only, or our own policy.

## Immediate next steps

1. Inspect the official 非提助手 site and identify the official source/download/documentation links it provides.
2. Start with the Assistant-related files already present in the current project; identify their version and trace the automation entry point.
3. Trace one complete expedition cycle from scheduling through completion.
4. Use KCA expedition code only as a comparison for missing or unclear details.
5. Verify POI state access independently; do not infer an action API from either reference project.

## Safety and licensing

Do not upload third-party source trees wholesale. Store our own analysis notes and minimal citations in GitHub; use third-party code only according to its license. No live game actions are part of this research phase.
