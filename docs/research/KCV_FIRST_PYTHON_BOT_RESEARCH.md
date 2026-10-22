# KCV（非提助手）优先的 Python Bot 研究计划

Date: 2026-10-21
Status: IDENTITY_CORRECTED; POI_BRIDGE_PROTOCOL_PARTIALLY_CONFIRMED; AUTOMATION_CALL_CHAIN_PENDING
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

Treat KCV as the product-experience benchmark because the user has used it extensively. Treat KCA as an active technical reference because its source is easier to inspect and it updates frequently. Do not force a fixed whole-project ranking: choose the reference per technical question based on accessible evidence, implementation cost, testability, and expected benefit. Do not attribute KCA-only behavior to KCV.

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


## Poi bridge source evidence — 2026-10-23

Status: `SOURCE_CONFIRMED` for the client-side socket protocol; Poi-side implementation and live compatibility remain `NEEDS_VALIDATION`.

Inspected uploaded reference repository `wosafety0/KCV`, branch `upload/kcv-reference-20261022-174555`:
- Assistant-related deobfuscated source: [PoiProxy.cs](https://github.com/wosafety0/KCV/blob/upload/kcv-reference-20261022-174555/feitizhushou_Deobfuscation/Grabacr07.KanColleViewer.Models/PoiProxy.cs), blob `0dc6b926b82783b52d00f3b522be1cf2ad45f59c`.
- A parallel file exists under `KCA_Decompiled/KanColleViewer/...`; use the explicitly named `feitizhushou_Deobfuscation` copy for this KCV/非提助手 evidence and do not confuse it with `kcauto_custom`.

Confirmed from `PoiProxy`:
1. Connects a TCP socket to `127.0.0.1:39788`.
2. Frames outbound JSON messages as UTF-8 text: `#k@` + JSON + `#e@`.
3. Sends a `tag:"port"` handshake containing `ver`, the local proxy `port`, `path`, and `capabilities:["stateSnapshotV1"]`.
4. Parses incoming JSON messages by `tag`; it recognizes `stateSnapshotReady` and `stateSnapshotError`, and sets `StateSnapshotReady` / `StateSnapshotError` accordingly.
5. Also recognizes other Poi messages such as `compatibleMode`; it sends UI input messages (`mouse` click/wheel/move and capture) over this channel.

Implementation implications:
- This is evidence of a local TCP bridge, not evidence that KancolleBot can read a complete authoritative game-state snapshot through this socket.
- The `stateSnapshotV1` string appears in the capability handshake. The inspected client-side file alone does not establish the snapshot schema, snapshot delivery timing, or whether full fleet/ship state is carried over this channel.
- Do not repurpose the UI input messages for gameplay automation. The Python MVP should continue to prefer a documented read-only structured-state bridge and use this socket protocol only if the Poi-side counterpart is found and the data contract is verified.
- Do not treat this as the Nanodesu/Minagi HTTP MCP interface; those are separate bridge implementations and endpoints.

## Updated reference-selection policy

- Product behavior and desired UX: start from the user's long-term experience with KCV.
- Technical implementation: inspect whichever source or interface gives the fastest reliable evidence; KCA can lead a specific implementation task when its Python source is directly testable.
- When KCV is obfuscated or its Poi-side counterpart is unavailable, set a bounded investigation effort and pivot to KCA, documented Poi APIs, runtime observation, or a minimal independent implementation.
- Record each finding as `SOURCE_CONFIRMED`, `OFFLINE_TEST_PASSED`, `RUNTIME_VERIFIED`, or `END_TO_END_VERIFIED`; never promote source inspection into runtime verification.


## KCA Poi-mode implementation evidence — 2026-10-23

Status: `SOURCE_CONFIRMED` for the documented KCA integration path; this does not itself prove that the current local installation matches the source revision.

Repository: [XVs32/kcauto_custom](https://github.com/XVs32/kcauto_custom), branch `develop`.

Relevant files:
- [reference/poi_interaction_mode.md](https://github.com/XVs32/kcauto_custom/blob/develop/reference/poi_interaction_mode.md)
- [kcauto/util/poi_client.py](https://github.com/XVs32/kcauto_custom/blob/develop/kcauto/util/poi_client.py)
- [kcauto/api/api_listener.py](https://github.com/XVs32/kcauto_custom/blob/develop/kcauto/api/api_listener.py)
- [kcauto/api/api_core.py](https://github.com/XVs32/kcauto_custom/blob/develop/kcauto/api/api_core.py)
- [kcauto/util/poi_plugins.py](https://github.com/XVs32/kcauto_custom/blob/develop/kcauto/util/poi_plugins.py)

Findings:
1. KCA explicitly supports `general.interaction_mode = "poi"`; its documentation identifies `poi-plugin-forwarder >= 1.1.0` and `poi-plugin-noro6-exporter` as required plugins.
2. Its documented defaults separate the API webhook receiver (`127.0.0.1:9223`) from the Poi control service (`127.0.0.1:38591`).
3. `PoiWebhookServer` accepts POST requests on `/webhook/kancolle`, filters KCSAPI paths and map-resource events, preserves request/response/stage/timestamp fields, and queues accepted events.
4. `ApiWrapper.update_from_api` waits for requested API enum values with an overall timeout, parses the event body through the API-specific processing pipeline, and logs missing API responses when required data does not arrive.
5. `PoiClient` uses TCP framing: four-byte little-endian packet length followed by a one-byte packet type; JSON packets and frame packets are distinct. The control protocol includes a hello/protocol handshake, ping, capture, and UI commands.
6. KCA's control client includes action methods such as click/hover/scroll/drag/refresh. These are reference behavior only; KancolleBot must keep them isolated and disabled until a separate safety review authorizes an action path.

Python implementation implication:
- KCA now provides a concrete, inspectable Python example for both KCSAPI event ingestion and Poi control-protocol framing. For a near-term prototype, prioritize reproducing the read-only webhook ingestion and API-event normalization before implementing any control commands.
- The webhook data stream is event-based and may be incomplete or delayed; use bounded queues/timeouts, preserve event timestamps and stages, reject malformed bodies, and mark state unknown when required events are missing.
- Do not equate receiving a KCSAPI event with current home-port readiness or complete fleet state. Session and freshness evidence must remain separate.
- The control TCP protocol is not the KCV `39788` protocol: the framing, ports, message schema, and server implementation differ. Do not combine them into one generic client.

## Practical reference decision after comparison

KCV remains the product-experience benchmark because the user has extensive successful usage experience. KCA should not be treated as a low-priority fallback for every technical task: its directly inspectable Python implementation makes it the more efficient starting point for API event parsing and TCP framing where those behaviors match the requirement. Investigate KCV's Poi bridge only for behavior that cannot be established from the KCA path or documented Poi APIs, and cap effort when the counterpart source is unavailable.
