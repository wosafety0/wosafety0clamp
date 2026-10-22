# KanCollePlayerSimulator Reference Review — 2026-10-23

## Scope

Repository reviewed: https://github.com/KanaHayama/KanCollePlayerSimulator/tree/master

This is a targeted source review for KancolleBot. It does not install or execute the simulator, connect to a game client, or issue game actions.

## Findings from source

- The repository README describes itself as hosting open-source portions of the KanCollePlayerSimulator software and release packages.
- `肝帝模拟 改/Python脚本/附加/远征调度.py` implements resource-oriented expedition scheduling. Its comments and constants include expedition duration, expected resource gain, resource caps, and logic to avoid repeatedly switching the selected resource when totals are close. Some values are explicitly marked as estimates or TODOs and must not be treated as authoritative game data.
- `肝帝模拟 改/Python脚本/附加/编成助手.py` provides reusable ship-selection filters such as level ranges, locked ships, equipment compatibility, and upgrade state. This is useful as a checklist for fleet composition constraints.
- `肝帝模拟 改/Python脚本/独立/根据poi战斗记录构造执行单元.py` parses copied POI battle-log data to build simulator workflows. It provides evidence that POI battle logs can be a useful source for offline fixtures and scenario reconstruction; its payload is a POI battle-log clipboard format, not proof of a general live Poi HTTP API.
- The repository includes older unattended expedition and sortie configuration examples. They are historical examples, not safe defaults and not proof of current game rules.

## Value to KancolleBot

1. High: expedition prioritization and resource-utility scoring concepts.
2. High: a checklist of fleet composition and ship-selection constraints.
3. Medium-high: POI battle-log-derived offline test fixtures and scenario reconstruction.
4. Low for the current milestone: reproducing the simulator's complete UI/workflow engine or importing its legacy unattended configurations wholesale.

## Updated priority

- Keep Kuma / Poi Forwarder event transport as the primary data-ingestion investigation.
- Use KanCollePlayerSimulator as a focused reference for expedition scoring, fleet filters, and offline scenario fixtures.
- Use KancolleSniffer only to answer unresolved API decoding questions.
- Keep KCV (非提助手) as the primary automation behavior reference and KCA as a supplementary implementation reference.
- Do not let another reference project interrupt implementation of the event-normalization/state-reduction path.

## Meaning of “real gameplay actions disabled”

This is a temporary development-stage gate, not a project prohibition. Offline planning, simulation, tests, and read-only observation are allowed. Live commands that mutate game state—such as dispatching expeditions, changing fleets/equipment, starting sorties, or claiming rewards—remain disabled until the read path, preconditions, failure recovery, stop behavior, and audit logging have been validated. The intended product still aims to automate expeditions and battles in stages.

## Evidence and limitations

Reviewed source files:
- `肝帝模拟 改/Python脚本/附加/远征调度.py`
- `肝帝模拟 改/Python脚本/附加/编成助手.py`
- `肝帝模拟 改/Python脚本/独立/根据poi战斗记录构造执行单元.py`

This review is based on source inspection only. No runtime compatibility, current game-rule correctness, license compatibility for code reuse, or live Poi connection was tested. Prefer extracting ideas and writing independent Python implementations unless code reuse is explicitly justified and license-reviewed.
