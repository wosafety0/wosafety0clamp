# R3-I.3 真客户端完整 PASS 门禁验证报告（2026-11-05）

> 本报告是 `R3I_LIVE_VERIFICATION.md` 第 5 节「尚未闭环」的**闭环补证**。
> 原始证据：`docs/research/R3I_LIVE_PASS_EVIDENCE_20261105.json`（摘要 SHA-256 `0a7a69323458138927a57801f41e694ba1d5ad97eb7ec67272718ed398dca934`）。
> 取证工具：`tools/verify_r3i_live_pass_gate.py`。

---

## 1. 授权范围（用户选项 ②）

用户明确授权**可见 UI 刷新导航 + 补给，但不派遣**。据此本轮执行了两类带游戏内副作用的动作：

| 允许 | 实际执行 |
|---|---|
| 推进残留奖励页、返回母港 | 有界点击（每步先证明页面再点击） |
| 可见 UI 刷新导航 `HOME_PORT → SORTIE_MENU → EXPEDITION_LIST → HOME_PORT` | 生产 `VisibleUiPortRefreshDriver`，每次点击后有转场等待 + 6 次采样复证 |
| 补给（消耗游戏资源） | **生产工具 `tools/full_supply_ui_action.py` 原样调用**，默认目标 Fleet 3，未改任何生产文件 |
| **不派遣** | 停在 verdict；未调用 `dispatch_one_ready()` / `prepare_dispatch()`，未写 intent，未点击派遣/远征开始控件 |

**未扩大生产工具授权面**：补给工具保持 `TARGET_FLEET_IDS=(3,)` 默认；没有把它改成可补给任意舰队，也没有 monkey-patch 其目标常量。评估目标相应选为 **Fleet 3 / 远征 1（練習航海）**——该规则是 `cross_checked` 且不含装备/属性要求，正是生产 `minimum_confidence="cross_checked"` 下唯一能真正判 PASS 的规则类，而不是绕过置信度门禁。

> 上一轮报告写的是「Fleet 2 燃料 `8/15` 需补给」。本轮改用 Fleet 3：它同样 `fuel_ready=false`，补给后同样能满足远征 1 的全部条件，且是项目在 R3-G 已实机验证过的成熟补给路径。

---

## 2. 真客户端环境（已证事实）

- CDP `127.0.0.1:9222`：`poi/12.0.1 Chrome/152 Electron/44`，目标含 DMM 游戏 `webview`。
- Poi MCP `127.0.0.1:12450`：`poi-nanodesu-mcp / ok / storeReady=true`。
- 开局截屏：「遠征 成功!」结算结果页（練習航海）；Poi Redux 四队 `api_mission=[0,0,0,0]`。
- 资源 `info.resources`：燃料 43914，`info.ships` 239/239 不截断。
- 视觉依赖 `cv2/numpy` 就绪。

---

## 3. 两次执行（如实记录，不隐藏失败）

**第一次（13:04:58–13:05:11Z）失败，补给未执行。** Phase A 有界推进奖励链（`RETURN_RESULT → BONUS_ITEMS → HOME_PORT`，2 次点击）成功回母港；随后补给工具的 `_acquire_fresh_witness` 发出 3 次刷新点击，但在**最后一步 `return_home_port` 之后的 HOME_PORT 复证**上，6 次采样 `Expected HOME_PORT; no stable fingerprint match` 而抛 `VisibleUiRefreshError`。事后截屏确认画面已是清晰、稳定的母港 → 判定为**奖励链刚结束、淡入转场长于 1.0s 复证窗口**的已知间歇问题，不是状态错误。工具 fail-closed 停止，未补给、未派遣。

**修正**：验证工具在本进程内把刷新驱动的 `settle_seconds` 从 `1.0` 加宽到 `3.0`（只改等待；三次点击、6 次采样验证、禁止模糊重试的规则与全部补给动作仍是生产代码），并在 Phase A/B 之间加 3s 稳定等待。**生产文件一字未改。**

**第二次（13:07:46–13:09:25Z）成功。** 全程 99.392 秒，Phase A 0 次点击（已在母港）。两次尝试合计约 15 次授权点击；证据 JSON 精确记录的是第二次成功的 10 次。

---

## 4. 链路与结果

| 阶段 | 动作 | 审计结果 |
|---|---|---|
| A | 奖励页 → 母港 | 0 次点击（第二次运行时已在母港） |
| B | 生产补给工具 `run(dry_run=False)` | `return_code=0`、30 条 append-only 审计事件，含 `fresh_port_witness_captured`、`full_supply_preflight_passed`、`fleet_wide_supply_click_sent`、`full_supply_completed_and_verified`；6 次点击（3 次刷新 + 补给菜单 + 舰队页签 + 全补给按钮）；Fleet 3 补给后 `fuel_ready=true` / `ammo_ready=true` |
| C | 同进程被动观察 + 可见 UI 刷新 | `provider.last_status="captured"`；自然 `/kcsapi/api_port/port` HTTP 200，`responseReceivedAt=2026-11-05T13:09:19.497Z`；`refresh_stats.outcome="completed"`、3 次点击、终态 `HOME_PORT`；4 次点击（1 次从补给页回母港 + 3 次刷新） |
| D | 生产读取 + 门禁 | 见下 |

**门禁判定（`fleet_id=3`, `mission_id=1`, `max_state_age=30s`）**

```json
{
  "overall_result": "PASS",
  "allows_dispatch": true,
  "rule_confidence": "cross_checked",
  "passed_checks": ["state_trust","rule_source","fleet_identity","fleet_available",
                    "ship_count","flagship_valid","flagship_level","fleet_total_level",
                    "ship_type_composition","equipment","fleet_stats","supply","damage"],
  "failed_checks": [],
  "unknown_checks": []
}
```

- `observation.source_freshness_verified = true`（witness 由本进程被动观察器交付，逐字段与 239 艘 Redux 状态匹配，未伪造、未从磁盘读取）。
- 13 项检查全部 PASS，其中 `equipment` / `fleet_stats` 是 `NOT_APPLICABLE` 型通过（远征 1 无此要求），`supply` 由补给后的真实满补给通过。
- Fleet 3 实机证据：4 艘 `[16374,13350,6,5747]`、等级 `[88,92,76,74]`、10 个鼓桶 / 4 艘携带、属性合计 `firepower 194 / anti_air 243 / asw 235 / los 150`，均来自**同一条** `ObservedFleetSnapshot`。

### 同一 fresh baseline 绑定

证据 `same_baseline_binding` 在真客户端复现了生产控制器的两条断言：

```json
{
  "baseline_source": "poi-redux:info.fleets+info.ships",
  "normalized_snapshot_source": "poi-redux:info.fleets+info.ships",
  "source_matches": true,
  "baseline_ship_ids": [16374, 13350, 6, 5747],
  "normalized_ship_ids": [16374, 13350, 6, 5747],
  "ship_ids_match": true,
  "baseline_source_freshness_verified": true,
  "state_trusted": true,
  "equipment_evidence_present": true,
  "fleet_stats_evidence_present": true,
  "same_fresh_baseline": true
}
```

**证据边界（不夸大）**：本轮没有调用 `ContinuousDispatchController.dispatch_one_ready()`（那会真的派遣）。「门禁评估与 intent 持久化共享同一 `baseline_observed_at`」由确定性测试 `test_the_gate_and_the_intent_bind_the_same_fresh_baseline` 与 `test_equipment_and_stat_evidence_travel_with_the_same_baseline` 锁定；本轮实机证明的是**门禁评估确实建立在一份 fresh、且装备/属性/编成同源于一条记录的 baseline 上**。二者合起来构成用户要求的「派遣前检查使用同一份 fresh baseline」证据链。

---

## 5. 安全边界与审计计数更正

`safety` 载荷：

```json
{
  "authorised_ui_clicks_sent": 10,
  "click_breakdown": {
    "phase_A_settle": 0,
    "phase_B_resupply_and_its_refresh": 6,
    "phase_C_post_resupply_refresh": 4
  },
  "game_api_requests_authored_by_tool": 0,
  "game_api_requests_replayed_or_constructed": 0,
  "dispatch_attempted": false,
  "dispatch_intent_written": false,
  "fleet_composition_changes_proposed": false,
  "runtime_evaluate_used": false
}
```

UI 点击会让**游戏自己**发出请求（包括被观察的 `api_port/port`）；本工具没有构造、重放或主动发送任何游戏 API 请求，也没有使用 `Runtime.evaluate`。

**审计计数更正（透明说明）**：工具初版把所有 `click_count` 字段相加，误把 `fleet_wide_supply_button_verified` 的**预置**点击数计入，又漏掉 `ui_refresh_click_sent` 的累计 `clicks_sent`，首次归档报告 8 次。已修正 `count_supply_clicks()`（只统计「已发送」事件，刷新取累计最大值），用**同一份原始审计事件**重算为 10 次，并在证据 JSON 中保留 `safety_reconciliation` 块记录原值、修正值和依据。观测事实、`verdict`、`same_baseline_binding` 全部未改动。新增 `tests/test_verify_r3i_live_pass_gate.py`（8 条）锁定此计数契约。

**REJECT / UNKNOWN 零点击验证保留**：上一轮 `R3I_LIVE_EVIDENCE_20261105.json` 的真客户端 `UNKNOWN` 零点击证据、`R3IFreshnessAndLatencyGateTests`（延迟 / 超时 / 动画期 / 未来时戳 / 陈旧 / 入渠 REJECT）以及既有 `R3IRequirementGateTests` 全部保留，本轮未删改、未放宽。

---

## 6. 仍未闭环的限制（不因 PASS 而消失）

1. 规则数值本身**仍未做实机成功/失败回归**；本轮 PASS 只证明门禁在真实 fresh baseline 上会放行远征 1。
2. 要求装备/属性的远征仍是 `single_source`、来源冲突或编成未结构化，`minimum_confidence="cross_checked"` 下整体仍 UNKNOWN —— **规则数据问题，未擅自放宽**。
3. `in_dock_ship_ids` 分支真客户端当前无入渠舰船，仅离线验证。
4. 没有执行完整派遣循环（用户选项 ② 明确不含）；「PASS 之后 UI 派遣链」的实机证据仍属未验证。
5. 视觉门的转场窗口是间歇性的：本轮靠加宽验证工具的等待绕过，**未改生产 `full_supply_ui_action.py` 的 `settle_seconds=1.0`**。生产工具在奖励链刚结束时仍可能复现同类失败（fail-closed，不会误操作）。这应作为独立修复项记录。

---

## 7. R3-J gate 判定

| gate 条件 | 状态 |
|---|---|
| 真客户端完整 PASS 门禁 | ✔ 本报告 + `R3I_LIVE_PASS_EVIDENCE_20261105.json` |
| 确认派遣前检查使用同一份 fresh baseline | ✔ 实机 `same_fresh_baseline=true` + 确定性测试锁定 intent 同源 |
| 保留 REJECT / UNKNOWN 零点击验证 | ✔ 保留并通过 |
| 实机证据归档 | ✔ 本轮归档（本地提交 + 推送） |
| GitHub 远端回读 | ✔ 本轮执行（见交付回复五项状态） |

**gate 满足后 R3-J 在本轮启动阶段一**（规格核查与实施规划），不提前实现自动换船或动态舰队调度。
