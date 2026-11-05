# R3-I.2 真客户端 PASS 门禁验证 — Fleet 2 专轮（WorkBuddy 交接执行报告）

> 日期：2026-11-05（UTC 时间以证据 JSON 为准）
> 授权：用户明确回复「是」/方案 2 —— 允许正常游戏 UI 刷新导航、给 **Fleet 2** 补给、只读采集与证据归档；
> **禁止派遣、禁止改编成、禁止构造/发送/重放游戏 API、禁止把被动 CDP 监听变成主动游戏请求**。
> 本报告只记录已执行、已观测、已测试、已提交、已推送、已回读的事项；未验证事项一律标注为 LIMITS。

---

## 0. 与交接文档的状态差异（先说清楚，不沿用旧状态）

交接文档把 GitHub 权威 HEAD 记为 `0b4d274527a3fd5b38c68410fd5e616b3b926b80`，
并把进度文件 blob 记为 `a665b1805d9ea21204f40e67244b09093fad5498`。本轮执行前重新核验：

| 项 | 交接文档 | 本轮实测（GitHub `get_commit` + `git ls-remote` + `git rev-parse`） |
|---|---|---|
| 远端分支 ref | `0b4d274…` | **`1cda5178a1318de1b8842056eb04fd9f00185d31`** |
| `docs/PROJECT_kancollebot_Progress.md` blob | `a665b180…` | **`4d645b964be8c33effb1ddb4d33e4374d0a9a569`**（该值正是 `0b4d274` 的内容） |
| `expedition_requirement_checker.py` blob | `09f2353b…` | `09f2353b…`（一致，未改） |
| `tools/verify_r3i_live_gate.py` blob | `7fa6378f…` | `7fa6378f…`（一致，未改） |
| R3-J 状态 | 未启动 | **阶段一规格核查已于 `1cda517` 归档**（仅方案文档，无调度代码）；本轮未推进阶段二/三 |

即交接文档落后一个提交。用户在本轮开始时选择「补做 Fleet 2 验证」，因此本轮**按交接规格重新完整执行一遍**，
而不是把上一轮 Fleet 3 的 PASS 当作本轮结果。两个关键生产文件（检查器、只读取证脚本）的 blob SHA
与交接文档给出的权威值完全一致，证明本轮**没有修改规则数据、没有修改 freshness 判据、没有修改取证脚本**。

---

## 1. 授权边界与执行方式

| 允许 | 本轮是否执行 |
|---|---|
| 正常游戏 UI 刷新导航触发自然 `api_port/port` | 是（C1 回母港 + C3 三步刷新） |
| 正常游戏 UI 给 Fleet 2 补给 | 是（B 阶段，仅 Fleet 2） |
| 只读采集、门禁验证、日志、证据归档 | 是 |
| 派遣任何舰队 | **否** |
| 修改舰队编成 | **否** |
| 构造 / 发送 / 重放游戏 API 请求 | **否** |
| 把被动 CDP 监听变成主动游戏请求 | **否** |

新增编排工具 `tools/verify_r3i_fleet2_gate.py`，**不修改任何生产文件**。生产补给工具的
`TARGET_FLEET_IDS=(3,)` 默认值只在本验证进程内被覆盖为 `(2,)`；补给工具的完整前置校验、
`validate_full_supply_progress()` 的「未处理舰队状态不得变化」断言、补给后 bounded polling 全部原样保留。
补给审计事件 `full_supply_preflight_passed` 记录 `target_fleet_ids=[2]`、`target_fleets=[2]`，
`full_supply_completed_and_verified` 记录 `non_target_fleets_untouched=true`、
`game_api_request_initiated=false`。

---

## 2. 执行时序（严格遵循交接文档第 5 节）

| # | 阶段 | UTC 时间 | 动作 | 点击 |
|---|---|---|---|---|
| 1 | A 环境核验 | 15:41:39.711 | 只读：CDP/Poi MCP/画面/四队状态/Fleet 2 燃料弹药/入渠/危险状态 | 0 |
| 2 | A 决策 | 15:41:39.711 | 当前读数确认 Fleet 2 `fuel_ready=false` → 需要补给 | 0 |
| 3 | B 目标限定 | 15:41:39.720 | 进程内把补给目标设为 `(2,)` | 0 |
| 4 | B 补给 | 15:42:38.505 | 生产补给工具 `run(dry_run=False)` 返回 0 | 6 |
| 5 | B 补给后只读复核 | 15:42:38.580 | 四队状态复核，仅 Fleet 2 变化且满补给 | 0 |
| 6 | C1 回母港 | 15:42:50.913 | HOME_PORT 6 次采样复证 | 1 |
| 7 | **C2 启动被动观察器** | **15:42:54.377** | 独立进程启动现有 `verify_r3i_live_gate.py`，确认 node 观察器子进程已启动（node 进程数 7→8）、probe 存活 | 0 |
| 8 | **C3 UI 刷新导航** | **15:43:17.689** | HOME_PORT→SORTIE_MENU→EXPEDITION_LIST→HOME_PORT，每步 6 次视觉采样 | 3 |
| 9 | C3 witness 记录 | 15:43:17.817 | probe 完成，`passive_observer.last_status=captured` | 0 |
| 10 | D verdict / 停止条件 | 15:43:17.817 | 11 项停止条件全部通过 | 0 |

**关键顺序**：补给（B）在观察器启动（C2）**之前**完成；观察器确认运行后才做 UI 刷新导航（C3）；
生产 reader 在 witness 捕获后采集（`observed_at=15:43:14.080834Z`），评估发生在刷新导航结束后。
这避免了「补给工具自身预检刷新产生的 port 响应」被误当作补给后的 fresh witness。

验证脚本 `tools/verify_r3i_live_gate.py` 以**独立进程**运行，参数与交接建议一致：
`--fleet-id 2 --mission-id 1 --max-state-age-seconds 30 --witness-wait-seconds 90`。
脚本自身 `safety.game_clicks_sent=0`、`game_api_requests_sent=0`、`runtime_evaluate_used=false`；
即「脚本零点击」与「WorkBuddy 执行的授权 UI 点击（10 次）」在证据中被分开记录。

---

## 3. 环境核验（重新读取，未采用历史状态）

- CDP `127.0.0.1:9222`：`Chrome/152.0.7977.54`，User-Agent `poi/12.0.1 … Electron/44.0.0`；DMM 游戏 `webview` **1 个**在线。
- Poi MCP `127.0.0.1:12450/mcp`：`poi-nanodesu-mcp / ok / storeReady=true`。
- 当前画面：干净母港页，无结算气泡、无弹窗、无转场动画。
- 四队 `api_mission=[0,0,0,0]` 全部 idle，`mission_status=0`、`mission_id=null`、`return_at=null` → **无待结算、无危险状态**（`dangerous_fleets=[]`）。
- **Fleet 2 补给前**：`ship_ids=[12613,16733,16323,13519]`、等级 `[17,15,14,31]`、四舰 `api_fuel=8`（上限 15/20）、弹药满、
  `api_ndock_time=0`（无入渠）、`api_nowhp==api_maxhp`（无损伤）、`api_cond=49`、无装备。
- 资源燃料 43898 / 弹药 63261，充足。
- 只读门禁复算（补给前）：**UNKNOWN**，`failed_checks=['supply']`、`unknown_checks=['state_trust']`；
  其余 11 项 PASS/NOT_APPLICABLE。远征 1 规则为 `cross_checked` 且不含装备/属性要求，因此补给 + 真 witness 后 PASS 可达。

---

## 4. Fleet 2 补给结果

| 项 | 补给前 | 补给后 |
|---|---|---|
| Fleet 2 `fuel_ready` | `false` | **`true`** |
| Fleet 2 `ammo_ready` | `true` | **`true`** |
| Fleet 2 `ship_ids` | `[12613,16733,16323,13519]` | **相同** |
| Fleet 1 / 3 | 满补给 | **满补给（未变）** |
| Fleet 4 | 燃料/弹药均未满 | **仍未满（未被补给）** |
| 燃料消耗 | — | **28**（4 舰 × 7），43898 → 43870 |
| 弹药消耗 | — | **0** |

审计原文：`"target_fleet_ids": [2]`、`"target_fleets": [2]`、
`"fleet_wide_supply_click_sent": {"fleet_id": 2, "click_count": 1, "retry_allowed": false}`、
`"full_supply_completed_and_verified": {"non_target_fleets_untouched": true, "game_api_request_initiated": false}`。
补给后状态变化的舰队集合为 `[2]`，舰队成员前后一致，无编成改动。

---

## 5. fresh witness 与生产门禁 verdict

`docs/research/R3I_FLEET2_LIVE_GATE_EVIDENCE_20261105.json`（既有只读脚本产出，SHA-256 `60358de2…`）：

```json
"observation": {
  "observed_at": "2026-11-05T15:43:14.080834+00:00",
  "source": "poi-redux:info.fleets+info.ships",
  "source_freshness_verified": true
},
"passive_observer": {
  "started": true, "wait_seconds": 90.0,
  "thread_alive_at_capture": true,
  "last_status": "captured",
  "natural_port_response_captured": true
}
```

`source_freshness_verified=true` 不是脚本自填标志：它由生产 `PoiExpeditionReader` 调用
`port_snapshot_matches_current_state(...)` 得出，该函数校验 witness 的 `responseReceivedAt` /
`bodyCapturedAt` 均在 `--max-state-age-seconds 30` 窗口内，并逐字段比对 port 快照与当前 Poi Redux
状态（含 `api_fuel`）。即补给后的满燃料值同时出现在 witness 与 Poi 状态中，故判定为同一份 fresh baseline。

**Fleet 2 / 远征 1：`overall_result=PASS`、`allows_dispatch=true`、`rule_confidence=cross_checked`。**

- `passed_checks`（13 项）：`state_trust, rule_source, fleet_identity, fleet_available, ship_count,
  flagship_valid, flagship_level, fleet_total_level, ship_type_composition, equipment, fleet_stats, supply, damage`
- `failed_checks=[]`、`unknown_checks=[]`
- 远征 **9999 未知规则对照：`UNKNOWN`**（`unknown_checks=['rule_source','ship_count','flagship_level','ship_type_composition','equipment','fleet_stats']`），保持 fail-closed，未被删除。

生产配置 `minimum_confidence="cross_checked"` 未放宽；规则数据、freshness 判据、max_state_age 均未修改。

---

## 6. 同一 baseline 核验

| 交接要求 | 实测 |
|---|---|
| `source_freshness_verified == true` | ✅ `true` |
| `observed_at` 在 30s 窗口内 | ✅ `15:43:14.080834Z`，probe `captured_at_utc=15:43:14.081776Z`，`elapsed_seconds=22.447` |
| Fleet 2 燃料弹药均为满值 | ✅ `fuel_ready=true`、`ammo_ready=true` |
| 装备证据、属性证据完整 | ✅ `equipment_by_ship` 非 null（4 舰全覆盖）、`total_stats={firepower:92, anti_air:74, asw:108, los:27}` |
| `snapshot_source` 与 baseline source 一致 | ✅ 三者均 `poi-redux:info.fleets+info.ships` |
| `baseline_ship_ids` 与实际成员完全一致 | ✅ `[12613,16733,16323,13519]`，与补给前、补给后、probe 证据三处一致 |
| 检查器生产配置 | ✅ `minimum_confidence="cross_checked"` |
| `dispatch_requirement_evaluation` 与准备事件引用同一 `baseline_observed_at` | **见 LIMITS**：本轮授权禁止派遣，故未产生控制器事件链 |

装备/属性证据通过 `ObservedFleetSnapshot` 上的 `equipment_by_ship` / `total_stats` / `conditions` /
`in_dock_ship_ids` 挂在**同一条**记录上，不是另一轮读取拼接。`verify_r3i_live_gate.py` 的 blob
`7fa6378f…` 未变，说明走的正是生产读取链 `read_fleets() → normalize_observed_fleets() → evaluate()`。

**额外 TOCTOU 复核**：`R3I_FLEET2_POST_REFRESH_CONSISTENCY_20261105.json` 在 UI 刷新结束后约 10 分钟
（`15:53:09Z`）做纯只读复采，与 PASS baseline（`15:43:14Z`）逐字段比对 4 支舰队 × 12 个字段，
`differences=[]`、`state_unchanged_after_refresh=true`。这次较晚读取自身 `source_freshness_verified=false`
（正确地没有 witness），**仅用于状态比对，绝不作为门禁 baseline**。

> 说明：首次比对脚本漏算 `drum_count` / `drum_carriers` 两个**派生字段**，产生 8 条 `archived=0/10/8,
> current=null` 的假差异；按与证据相同的 `DRUM_MASTER_ID=75` 口径重算后归零。该修正过程原样记在证据的
> `reconciliation` 字段中，没有改写任何观测值。

---

## 7. REJECT / UNKNOWN 零点击回归（保留）

- **实机零点击 UNKNOWN 对照**：`R3I_FLEET2_ZERO_CLICK_UNKNOWN_CONTROL_20261105.json`
  ——补给完成后，用同一脚本以 `--witness-wait-seconds 0`（不启动观察器、不做任何 UI 动作）评估：
  `source_freshness_verified=false` → **Fleet 2 / 远征 1 判 UNKNOWN**（`unknown_checks=['state_trust']`），
  `allows_dispatch=false`；远征 9999 亦 UNKNOWN；`safety` 全零（0 点击、0 游戏 API、0 输入、未派遣）。
  这直接证明「即使燃料已满足，没有独立 fresh witness 也绝不 PASS」。
- **补给前只读复算**：`failed_checks=['supply']`（明确 REJECT 条件）+ `state_trust` UNKNOWN → 整体 UNKNOWN、零点击。
- **确定性测试**：`R3IFreshnessAndLatencyGateTests` 覆盖网络延迟致 baseline 超龄、Poi 读超时、
  动画期间无 fresh witness、future-dated observation、`source_freshness_verified=false`、入渠 REJECT
  六类零点击拒绝；`test_the_gate_and_the_intent_bind_the_same_fresh_baseline` 锁定评估与 intent
  共享同一 `baseline_observed_at`。本轮全量测试通过（见第 9 节），这些断言未删改、未放宽。

---

## 8. 点击与副作用审计（两类点击严格区分）

| 类别 | 次数 | 来源 |
|---|---|---|
| **WorkBuddy 授权 UI 点击** | **10** | B 补给及其预检刷新 6 + C1 回母港 1 + C3 刷新导航 3 |
| **验证脚本自身点击** | **0** | `verify_r3i_live_gate.py` 的 `safety` 载荷 |
| 构造 / 发送 / 重放游戏 API | **0** | `game_api_requests_authored_by_tool=0`；补给审计 `game_api_request_initiated=false` |
| 派遣尝试 / dispatch intent | **0 / false** | `dispatch_attempted=false`、`dispatch_intent_written=false` |
| 舰队编成改动 | **0** | `fleet_composition_changes_proposed=false`，成员前后一致 |
| `Runtime.evaluate` | **未使用** | 被动 CDP Network 观察器只读 |
| 生产文件修改 | **0** | 仅新增工具、测试、证据与文档 |

点击计数由 `count_supply_clicks()` 从 append-only 审计事件重算，只统计**已发送**的点击事件；
`fleet_wide_supply_button_verified` 的预置 `click_count`（计划值）与 `*_completed` 的汇总
`ui_click_count` 不计入，刷新驱动的 `clicks_sent` 累计值取最大值一次。新增 16 条确定性测试锁定该契约，
包括 `bool` 是 `int` 子类时不得计为 1 次点击。

---

## 9. 停止条件（11 项全部通过）

```
natural_port_witness_captured      true      fleet_stats_evidence_complete   true
source_freshness_verified_true     true      baseline_ship_ids_match         true
fleet2_fuel_and_ammo_full          true      snapshot_source_matches         true
equipment_evidence_complete        true      verdict_is_pass                 true
unknown_rule_control_preserved     true      minimum_confidence_cross_checked true
no_ui_stop_reason                 true
failed=[]   stop_reasons=[]   exit_code=0
```

未触发任何停止条件：90 秒内有自然 port witness、freshness 为 true、Poi 双次读取一致、
Fleet 2 满补给、成员身份前后一致、证据无缺失/截断/冲突、verdict 为 PASS、页面稳定性经 6 次采样确认、
没有需要超出授权的动作。

---

## 10. 证据清单与哈希

| 文件 | SHA-256 |
|---|---|
| `tools/verify_r3i_fleet2_gate.py` | `44d8894c429d1a865eb17a0744e93303d98b2feb25a9ed49ed4050ceee6f3139` |
| `tests/test_verify_r3i_fleet2_gate.py` | `c0659fc113effd7724c82bfc98ef7d51004e697780676627148fa4b0f42831ff` |
| `docs/research/R3I_FLEET2_LIVE_GATE_EVIDENCE_20261105.json` | `60358de258494f158bd85b6a2be7c74fefa7469b393e78b91c3fe215a7414601` |
| `docs/research/R3I_FLEET2_UI_ACTION_LOG_20261105.jsonl` | `1f3717334392591e15e58c0d930b9a66acfca7072aefb63172135a37e790622c` |
| `docs/research/R3I_FLEET2_VERIFICATION_SUMMARY_20261105.json` | `c5212f66d06bd0dbc615f4fed091488ea9536c83f440c12026ab41b2ceb57209` |
| `docs/research/R3I_FLEET2_ZERO_CLICK_UNKNOWN_CONTROL_20261105.json` | `091b1d4b023fb60c3b063efe6c11f08267fca1a6a1d2f527d79a40646d1738fd` |
| `docs/research/R3I_FLEET2_POST_REFRESH_CONSISTENCY_20261105.json` | `2ec5260fd76d128c371c5c697bdd27ea13f157b386f8e3f930b0cecb95bb9016` |

**未改动的关键文件（blob SHA 与交接文档权威值一致）**：

| 文件 | blob SHA |
|---|---|
| `tools/verify_r3i_live_gate.py` | `7fa6378f30eb2088666138c483e1b26755ee0ba1` |
| `src/kancollebot/expeditions/expedition_requirement_checker.py` | `09f2353b71e53e81c5093f7ec7483b9d039d7a52` |
| `tools/full_supply_ui_action.py` | `47ef0e45870170b0613b6436a6cc478518a9a048` |
| `src/kancollebot/adapters/poi/expedition_reader.py` | `52366adc258bc997c14fc6122852d144d189cf19` |
| `src/kancollebot/expeditions/data/expedition_requirements.json` | `6c41a63688b5b3863da54f4de06d4842a2284e41` |

证据 digest：`840e532c29e601833e69c2561ede8f0b12e0f6131bbe48fdf3b77972f5507d59`。
UI 操作日志 10 条，每条含序号、UTC 时间戳、阶段、动作、目标、前后观察、结果、页面稳定性、
Poi 读取、点击数、说明；C2 含观察器启动证据，C3 含 witness 捕获状态与时间，D 含最终 verdict 与停止原因。

---

## 11. 测试与静态检查

| 命令 | 结果 |
|---|---|
| `PYTHONPATH=src python -m unittest discover -s tests` | **Ran 1531 tests — OK (skipped=2)**（R3-I.3 基线 1515，净增 16） |
| R3-G/R3-H 9 模块定向回归 | **Ran 185 tests — OK** |
| `python -m compileall -q src tests tools` | OK |
| `node --test tests/*.test.cjs` | **25/25 pass** |
| `git diff --check` | OK |
| `PYTHONPATH=src python -m unittest tests.test_verify_r3i_fleet2_gate -v` | **16 tests OK** |

---

## 12. LIMITS（不得当作已验证）

1. **未产生控制器事件链**：授权禁止派遣，本轮停在 verdict，未调用 `dispatch_one_ready()` /
   `prepare_dispatch()`、未写 intent。因此「`dispatch_requirement_evaluation` 与后续准备事件引用同一
   `baseline_observed_at`」仍由确定性测试 `test_the_gate_and_the_intent_bind_the_same_fresh_baseline`
   锁定，**本轮实机证明的是门禁建立在一份 fresh 且编成/装备/属性同源于一条记录的 baseline 上**。
2. **完整派遣循环未实机验证**：PASS 证明「门禁会放行」，不是「已经派遣」。
3. **规则数值未做实机成功/失败回归**；要求装备/属性的远征仍受 `single_source`、来源冲突（21/37/38）、
   编成未结构化的置信度限制，整体仍 UNKNOWN，未放宽。
4. `in_dock_ship_ids` 分支当前真客户端无入渠舰船，仅离线验证。
5. 生产补给工具 `_acquire_fresh_witness` 的 `settle_seconds=1.0` 在奖励链刚结束后仍可能复现
   转场复证失败（R3-I.3 已记录）；本轮验证进程内加宽到 3.0，**生产文件未改**，仍列为独立待办。
6. 交接文档把 R3-J 记为「未启动」，但仓库权威状态是阶段一规格核查已在 `1cda517` 归档；
   本轮未推进阶段二（历史回放）或阶段三（实机集成），二者需单独授权。

---

## 13. R3-J 门槛判定

按交接文档第 8 节逐条：真客户端取得 fresh witness ✅、Fleet 2 补给确认完成 ✅、
Fleet 2 / mission 1 生产门禁得到真实 PASS ✅、同一 baseline 证据完整 ✅、
REJECT/UNKNOWN 零点击回归仍通过 ✅、R3-G/R3-H 回归通过 ✅、
新增证据完成 GitHub 推送及远端回读核验 ✅（以交付回复的五项状态为准）。

**未因规则置信度导致 UNKNOWN，无需放宽门禁。**
R3-J 阶段一（规格核查与方案）已于 `1cda517` 启动并归档；本轮补齐 Fleet 2 专属证据后，
gate 按交接口径已满足。阶段二/三仍按规格另行推进与授权。
