# R3-I.2 实机验证与装备/属性可信观测补充报告

> 日期：2026-11-05　性质：**真客户端只读验证**，全程未向游戏发送任何点击、输入或 API 请求。
> 证据文件：`docs/research/R3I_LIVE_EVIDENCE_20261105.json`
> 取证工具：`tools/verify_r3i_live_gate.py`
> 本报告严格区分「已证事实」「代码保证」「尚未闭环」三类，不用推断冒充实机结果。

---

## 1. 真客户端环境（已证事实）

| 项目 | 实测 |
|---|---|
| CDP | `127.0.0.1:9222` LISTENING（PID 4176）；`/json/version` → `poi/12.0.1 Chrome/152 Electron/44` |
| 目标 | 3 个：`webview` DMM `https://play.games.dmm.com/game/kancolle`、`page` poi、`service_worker` |
| Poi MCP | `127.0.0.1:12450` LISTENING，`health()` = `poi-nanodesu-mcp / ok / storeReady=true` |
| `info.fleets` | 4 支舰队，`api_mission=[0,0,0,0]`（Poi 侧全部 idle） |
| `info.ships` | 239 艘；`info.equips` 1082 件；`const.$equips` 742 条 |

**画面与数据的不一致（本轮最关键的实机观察）**：真客户端画面停在「遠征 / 成功!」结算结果页（練習航海，羽川翼 Lv111、卯月改 Lv74），
而 Poi Redux 已把四支舰队全部标为 idle。这正是规格点名的「动画期间 / 画面与状态不一致」场景。
在这一状态下被动观察 `api_port/port` **20 秒**与独立一次 **45 秒**均为 `process_timeout`、没有自然响应，
因此 `source_freshness_verified=False`，门禁必须 fail-closed。

舰队补给也确实未满（实测 Fleet 2：燃料 `8/15`、`8/15`、`8/15`、`8/15`，弹药已满），
即上一轮远征返航后尚未补给。所以 `supply` 判 REJECT 是真实状态，不是观测 bug。

---

## 2. 装备/属性可信只读观测（本次新增实现）

### 2.1 为什么必须用独立投影（实测依据）

生产 `PoiExpeditionReader` 的 239 艘全量投影只取 8 个字段，用于 port witness 的逐字段比对。
实机测试把装备/属性字段并入该投影会怎样：

| 投影 | `total` | `returned` | `truncated` | 序列化字节 |
|---|---|---|---|---|
| 原 8 字段 | 239 | **239** | **false** | 34,032 |
| 8 字段 + 装备/属性 8 字段 | 239 | **225** | **true** | 74,645 |

把字段合并会**静默破坏 `source_freshness_verified`**（witness 比对要求完整 roster）。
因此实现采用两条独立的窄投影：

1. `SHIP_DETAIL_FIELDS`（9 字段：`api_slot` / `api_slot_ex` / `api_slotnum` / 四个 `[当前含装备值, 上限]` 数组 / `api_ndock_time`）→ 实测 **239/239 不截断**（51,506 字节）；
2. `info.equips` 用 `<field> <op> <value>` 形式的 `where` 表达式按**舰队成员实际持有的装备实例 ID**定向查询，只取 `api_id` / `api_slotitem_id` → 实测 96 个 ID 一次返回、4,943 字节、`truncated=false`。

字段语义来自实机 Redux：`api_karyoku/taiku/taisen/sakuteki` 是 `[当前含装备值, 上限]`（例：`[29,29]`、`[18,39]`），
`api_slot` 用 `-1` 表示空槽，`api_slot_ex` 用 `0` 表示无。`api_slotitem_id = 75` 为鼓桶（上游 `isEqpDrum` 结论，实机 60 件实例匹配）。

### 2.2 同一份 fresh baseline 的绑定方式

证据**不来自另一次读取再拼接**，而是在 `PoiExpeditionReader.read_fleets()` 的同一次观察内完成：

```
info.fleets / info.ships(witness投影) / masters
    → 证据投影读取（本轮两次一致性读取之间）
    → 二次一致性确认（fleets/ships + 证据源都比较）
    → observed_at = clock()
    → normalize_poi_fleets(..., requirement_evidence=...)
    → ObservedFleetSnapshot 上携带 equipment_by_ship / total_stats / conditions / in_dock_ship_ids
```

`normalize_observed_fleets()` 生产路径**不再接受外部注入**装备/属性证据，只从这条带 freshness 判定的记录里取；
三个同名关键字参数仅保留为离线测试覆盖。控制器额外断言 `snapshot_source == baseline.source` 且
`ship_ids == _snapshot_for(baseline, fleet_id).ship_ids`，并在审计事件写入
`baseline_observed_at` / `baseline_source_freshness_verified` / `baseline_ship_ids`。

若证据读取失败、被截断、两次一致性读取不一致、或某字段畸形 → 该舰队证据置 `None`（条件判 UNKNOWN），
**不会污染其它舰队，也不会让整个观测伪装成一次派遣决策**。

### 2.3 实机采集结果（2026-11-05T12:43:29Z，零点击）

| 舰队 | 舰船 ID | 鼓桶数 / 携带舰数 | 火力 | 对空 | 对潜 | 索敌 | 证据齐全 |
|---|---|---|---|---|---|---|---|
| 1 | `(23,)` | 0 / 0 | 98 | 84 | 0 | 48 | ✔ |
| 2 | `(12613,16733,16323,13519)` | 0 / 0 | 92 | 74 | 108 | 27 | ✔ |
| 3 | `(16374,13350,6,5747)` | **10 / 4** | 194 | 243 | 235 | 150 | ✔ |
| 4 | `(16859,14882,12794,1325)` | **8 / 3** | 147 | 188 | 203 | 108 | ✔ |

证据摘要 SHA-256：`cf77241155d96c2c08b0f3550d2ffb28f13759fb77a3cd761eef7b8142ff53fb`。

结论：**装备与属性条件不再恒为 UNKNOWN**。同一份数据下，有证据时 `equipment` / `fleet_stats` 会明确 PASS 或 REJECT
（离线测试 `test_with_equipment_evidence_the_drum_condition_is_no_longer_unknown`、
`test_missing_equipment_is_rejected_when_evidence_exists`、`test_stat_totals_below_requirement_are_rejected` 锁定）。
需注意：生产入口仍要求 `minimum_confidence="cross_checked"`，而要求装备/属性的远征分别是
`single_source`（24/40/101/102/103）、来源冲突（21/37/38）或编成未结构化（104/105），
**因此整体 PASS 仍受规则置信度门禁限制，这不是观测缺失造成的 UNKNOWN**。本轮没有擅自放宽该门禁。

---

## 3. 真客户端门禁判定（已证事实）

对 Fleet 2 / 远征 1 在生产同一链路上跑 `ExpeditionRequirementChecker(minimum_confidence="cross_checked")`：

| 输入 | 结果 |
|---|---|
| `source_freshness_verified=False`（结算动画页、无自然 port 响应） | **UNKNOWN**，`state_trust` UNKNOWN、`supply` REJECT，`allows_dispatch=False` |
| 远征 9999（规则缺失对照） | **UNKNOWN**，`rule_source` 等 7 项 UNKNOWN，`allows_dispatch=False` |

`safety`：`game_clicks_sent=0`、`game_api_requests_sent=0`、`game_input_sent=false`、
`dispatch_attempted=false`、`runtime_evaluate_used=false`。取证脚本只写它自己的 JSON 证据文件。

这是一次**真实的「动画期间 / Poi 数据无 freshness 证明 → 安全拒绝、零点击」**实机证据，而不是模拟。

---

## 4. REJECT / UNKNOWN 零点击验证（代码保证 + 确定性测试）

用户点名的三类风险场景，`tests/test_continuous_dispatch_controller.py::R3IFreshnessAndLatencyGateTests` 全部以
「读取阶段即失败」为断言：无 `dispatch_requirement_evaluation`、无 `dispatch_blocked_requirement_check`、
无 `dispatch_intent_persisted`、`ui.dispatches=[]`、`ui.home_calls=[]`、intent 未写、仅 1 次 Poi 读。

| 场景 | 测试 | 机制 |
|---|---|---|
| 网络延迟（读取耗时使 baseline 超龄） | `test_network_latency_that_ages_the_baseline_is_refused_zero_click` | `observed_at` 早于窗口 → `require_fresh` 抛 `Poi observation is not trusted` |
| 网络超时 | `test_a_poi_read_timeout_never_reaches_the_gate_or_the_ui` | `TimeoutError` 直接冒泡，门禁根本不执行 |
| 动画期间（无 fresh witness） | `test_animation_period_without_a_fresh_witness_is_refused_zero_click` | `source_freshness_verified=False` → `require_source_freshness_verified()` 拒绝 |
| Poi 数据陈旧 / 未来时戳 | `test_a_future_dated_observation_is_refused_zero_click`（+ 上述超龄用例） | `is_fresh()` 拒绝负龄与超龄 |
| 门禁内 REJECT（补给、入渠、身份混入） | 既有 `R3IRequirementGateTests` + `test_in_dock_evidence_on_the_baseline_blocks_with_zero_clicks` | 两个只读审计事件后抛错，`dispatch_clicks_sent=0` |

「同一份 fresh baseline」由 `test_the_gate_and_the_intent_bind_the_same_fresh_baseline` 锁定：
`dispatch_requirement_evaluation.baseline_observed_at == dispatch_intent_persisted.baseline_observed_at`，
且 `baseline_ship_ids=[1,2]`、`baseline_source_freshness_verified=True`、评估事件早于 intent 事件。

---

## 5. 后续闭环：真客户端完整 PASS 门禁（R3-I.3，已补证）

> **本节状态已被 `R3I_LIVE_PASS_VERIFICATION.md` 更新为「已闭环」。** 以下内容保留作为当时未授权状态的
> 原始记录，不删改，便于追溯 gate 的演进。

本轮（R3-I.2）当时**没有**在真客户端跑出一次完整 PASS 门禁。原因是两个真实前提都不满足，且补齐它们需要
向游戏发送 UI 点击（有游戏内副作用），当时在获得明确授权前没有执行：

1. 取得 `source_freshness_verified=True` 需要一次自然 `/kcsapi/api_port/port` 响应；客户端停在结算结果页时
   被动观察 20s / 45s 均无响应，必须靠**可见 UI 刷新导航**回到母港才可能触发。
2. 目标舰队燃料 `8/15` 未满，`supply` 必然 REJECT；需要**补给**（会消耗游戏资源）。

用户随后选择**授权可见 UI 刷新导航 + 补给、但不派遣**。`tools/verify_r3i_live_pass_gate.py` 在真客户端
取得 `overall_result=PASS` / `allows_dispatch=true`、`source_freshness_verified=true`、
`same_fresh_baseline=true`，13 项检查全部 PASS，取证停在 verdict、未派遣。证据与完整说明见
`docs/research/R3I_LIVE_PASS_EVIDENCE_20261105.json` 与 `docs/research/R3I_LIVE_PASS_VERIFICATION.md`。

取证脚本始终**不发送派遣决定点击**——PASS 证明的是「门禁会放行」，不是「已经派遣」。完整派遣链属于另一类
副作用，仍需单独授权。离线的完整 PASS 门禁证据（`test_only_a_pass_verdict_reaches_the_existing_dispatch_flow`、
`test_the_gate_and_the_intent_bind_the_same_fresh_baseline`、
`test_equipment_and_stat_evidence_travel_with_the_same_baseline`）继续保留，与实机证据互为补充。

---

## 6. 本轮命令与结果

| 命令 | 结果 |
|---|---|
| `PYTHONPATH=src python -m unittest discover -s tests` | **Ran 1507 tests — OK (skipped=2)**（R3-I 基线 1473，净增 34） |
| R3-G/R3-H 9 模块定向回归 | **Ran 185 tests — OK** |
| `python -m compileall -q src tests tools` | OK |
| `git diff --check` | OK |
| `node --test tests/*.test.cjs` | **25/25 pass** |
| `PYTHONPATH=src python tools/verify_r3i_live_gate.py --fleet-id 2 --mission-id 1 --witness-wait-seconds 20` | 退出 0，证据 JSON 已归档 |
| 被动观察器独立运行 45s | `process_timeout`，无自然 port 响应 |

新增测试：`test_requirement_evidence.py`(16)、`test_expedition_requirement_checker.py`(+6)、
`test_poi_expedition_reader.py`(+5)、`test_continuous_dispatch_controller.py`(+7)。
