# Fleet 4 完整闭环实机验证报告

- 日期：2026-11-06（本地）/ 2026-11-05T16:26Z–17:01Z（UTC）
- 分支：`feat/production-settlement-resupply-wiring-20261105`
- 授权来源：用户本轮明确选择「完整闭环（推荐）」+「远征 1（练习航海）」+「我主动轮询等待」
- 结论：**Fleet 4 / 远征 1 的完整派遣闭环已在真实客户端跑通**。这是本项目第一次实机完成
  「补给 → fresh witness 门禁 PASS → 真实派遣 → 等待返航 → 结算 → 回母港 → 状态对账」全链路。

---

## 1. 授权与边界

| 项目 | 内容 |
|---|---|
| 允许 | 补给 Fleet 4；可见 UI 刷新导航取 fresh witness；真实派遣；轮询等待返航；点击结算链；只读对账与证据归档 |
| 仍然禁止（本轮全部遵守） | 修改舰队编成；构造/发送/重放游戏 API；把被动 CDP 监听变成主动请求；放宽门禁或置信度阈值 |
| 未扩大的面 | 生产文件未改一行；`verify_r3i_live_gate.py`、`live_dispatch_expedition.py`、`full_supply_ui_action.py`、规则数据、freshness 判据全部原样 |

---

## 2. 执行时序（每步都有可复核证据）

| # | 时刻 (UTC) | 动作 | 结果 |
|---|---|---|---|
| A0 | 16:26:47 | 只读基线快照 | 四队全 idle；F4 `ship_ids=[16859,14882,12794,1325]`、`fuel 11/15`、`ammo 12/15·12/15·16/20·12/15`；门禁 `UNKNOWN`（`failed=['supply']`、`unknown=['state_trust']`） |
| A1 | 16:32 | 只读探查补给界面 | 舰队选择栏为 **「1 2 3 4 他」**，Fleet 4 tab 存在但项目未校准 |
| A2 | 16:34–16:36 | 选中 Fleet 4 tab（视觉定位） | `bounds=(338,164,34,30)` 中心 `(355,179)`；截图显示 4 舰等级 **88/47/39/82**，与 Poi 快照逐位一致 → 证明选对舰队 |
| A3 | 16:35:58 | 点「艦隊全補給」 | 燃料 43870→43854（**−16 = 4×4**）；Poi 复核 4 舰 `fuel 15/15`、`ammo 15/15·15/15·20/20·15/15` |
| B | 16:38:22 | 被动观察器 + 刷新导航 + 生产门禁 | `source_freshness_verified=true`、**`overall_result=PASS`**、`allows_dispatch=true`、`cross_checked`、`failed=[]`、`unknown=[]`；9999 对照 `UNKNOWN` |
| C0 | 16:39 | prepare-only 全查点验证（**不点开始**） | `PREFLIGHT_OK` → `HOME→SORTIE→EXPEDITION_LIST` → 选中 01 → 決定 → 切舰队（游戏默认选第 2 队）→ `fleet-4-tab score=1.0` → `FLEET_4_SELECTED`，`expedition_start_clicked=false` |
| C1 | 16:41:0x | 派遣前实时一致性确认 | F4 满补给、无入渠、四队 idle、舰船集合与 PASS baseline 一致 |
| C2 | 16:41:3x | **真实派遣 `--commit`** | `DISPATCH_CONFIRMED`：`mission=[1,1,1793897769682,0]`、`ship_ids` 不变 |
| D1 | 16:41:51–17:58:54 | 只读轮询 `mission_status` | 一直为 `1`。**Poi 的 mission_status 只在客户端发出 `api_port/port` 时推进**，舰队在外时母港不会自发产生该响应 |
| D2 | 16:58:54 | 可见 UI 刷新导航（3 次点击，每步 6 采样） | `mission=[2,1,1793897769682,0]` → **返航确认** |
| E | 16:59:05–17:00:23 | 结算（生产 driver 三个原子动作） | `HOME_PORT_RETURN_NOTICE` → `RETURN_RESULT` → `BONUS_ITEMS` → `HOME_PORT`，每步两帧指纹确认；结算后 `mission=[0,0,0,0]`、无残留奖励屏 |
| F | 17:00:23–17:00:3x | 结算后对账 | 4 舰 `fuel 11/15`、弹药满、HP 全满、`cond 49`、`ndock_time=0`；母港干净无气泡 |

---

## 3. 闭环自洽性

结算后每舰燃料回到 **11/15**，与本次补给前读到的 **11/15** 完全一致。也就是说
「补给(11→15) → 派遣 → 远征消耗 4 → 返航 → 结算 → 回到 11/15」构成了一个自洽的循环，
Fleet 4 回到了它周期性的「需要补给」状态。这条自洽性同时解释了最初 `fuel_ready=False` 的读数不是 Poi 陈旧，
而是上一轮远征的真实结果。

---

## 4. 本轮发现的两个真实工程缺口（未擅自修）

### 4.1 Fleet 4 补给 tab 未校准

- `runtime_config.resupply_verified_fleet_ids = (2, 3)`；`locate_supply_fleet_tab()` 硬拒绝 `fleet_index=4`；
  `UiState` 只有 `SUPPLY_FLEET_2_SELECTED` / `SUPPLY_FLEET_3_SELECTED`。
- 实机事实：补给界面的 tab 栏是 **「1 2 3 4 他」**，第 4 个 tab 客观上存在，只是没有校准区域与指纹。
- 本轮做法：用与 `locate_supply_fleet_tab` **相同**的轮廓算法，配以由已校准 1/2/3（间距 45px）几何推导出的区域
  `(330,155,50,50)` 定位，并在**任何补给点击之前**用「屏幕显示的 4 舰等级 88/47/39/82 == Poi 快照等级序列」证明选对了舰队。
- 未做：没有把该区域写进生产常量，也没有伪造 `SUPPLY_FLEET_4_SELECTED` 指纹。这需要一个显式的校准任务。

### 4.2 返航检测必须依赖一次导航

`Poi` 的 `mission_status` 不会因为时间到达而变 2。舰队在外时母港不产生 `api_port/port`，
所以只读轮询永远停在 `1`。**必须由一次可见 UI 导航触发客户端请求**，状态才会推进。
这不是缺陷而是事实约束，但它意味着「只读轮询等待返航」在实现上不可能独立成立。

### 4.3 派遣未写入 durable dispatch 记录

Fleet 4 由 `live_dispatch_expedition.py` 派出，该工具不写 `ContinuousDispatchController` 的 dispatch 事件，
因此 `settle_visible_reward_chain()` 拿不到 `expected_ship_ids`，会以零点击拒绝。
本轮**没有伪造该记录**，而是直接驱动同一个生产 driver 的三个原子动作
（`collect_return_result` / `claim_bonus_items` / `claim_reward`），并在点击前用 R3-F 的归属规则自证：
舰船 ID 集合 `[16859,14882,12794,1325]` 与派遣记录一致 + 其余三队 `mission_status=0`（唯一待结算）。

---

## 5. 点击与副作用审计

| 类别 | 数量 | 说明 |
|---|---|---|
| 逐条可审计的授权 UI 点击 | **24** | 6 份 JSONL 日志逐条可查：补给 4、门禁刷新 3、prepare 5、commit 6、结算 6 |
| 探查/恢复脚本的点击（未入统一日志） | **15** | 补给 tab 探查 4、补给入口 3、回母港恢复 4、prepare 前回母港 3、全补给 1 |
| 合计授权 UI 点击 | **39** | 全部是游戏正常 UI 导航 / 补给 / 派遣 / 结算 |
| 只读 probe 自身点击 | **0** | `verify_r3i_live_gate.py` `game_clicks_sent=0` |
| 工具构造/发送/重放的游戏 API | **0** | 所有 `api_*` 请求均由游戏客户端自然发出，只被被动观察 |
| 派遣 intent 写入 | **0** | 未走控制器，未产生 intent 记录 |
| 舰队编成修改 | **0** | 舰船集合全程 `[16859,14882,12794,1325]` 未变 |
| 生产文件修改 | **0** | 本轮提交只新增文档与证据 |

---

## 6. 门禁证据（原样，未放宽）

- `source_freshness_verified = true`，`observed_at = 2026-11-05T16:38:22.380696Z`
- 被动观察器：`started=true`、`last_status=captured`、`natural_port_response_captured=true`
- `overall_result=PASS`、`allows_dispatch=true`、`rule_confidence=cross_checked`
- 13 项检查：9 PASS + 4 NOT_APPLICABLE，`failed_checks=[]`、`unknown_checks=[]`
- 规则：`XVs32/kcauto_custom data/expedition/expedition.json@18d61ab3fe91`，`rule_version=2026-11-05.1`
- 未知规则对照：mission **9999 = UNKNOWN**，fail-closed 保留

---

## 7. LIMITS（如实）

1. **只验证了一支舰队、一个远征**（Fleet 4 / mission 1）。多舰队并发、鼓桶类远征、带装备/属性要求的远征均未实机验证。
2. **结算未走 `settle_visible_reward_chain`**，因此本轮没有产生控制器的 `startup_settlement_*` 事件链，
   也没有 `dispatch_requirement_evaluation` 与 intent 的 `baseline_observed_at` 同源实机证据（仍由确定性测试锁定）。
3. **Fleet 4 补给依赖现场几何推导**，生产常量仍未覆盖第 4 个 tab。
4. **返航检测依赖导航刷新**，纯只读轮询无法判定返航。
5. **单次闭环**，未验证连续多轮、异常中断后恢复、以及 `blocked` 状态的真实恢复路径。
6. 资源数值取自 `info.resources` 定位数组；截图读数在缩放显示下对「弹药/钢材」两位数字存在歧义，报告以 MCP 数组为准。

---

## 8. 证据清单

| 文件 | 内容 |
|---|---|
| `docs/research/P0_FLEET4_LIVE_GATE_EVIDENCE_20261106.json` | 生产门禁完整 JSON（未改动的只读 probe 产出） |
| `docs/research/P0_FLEET4_CLOSED_LOOP_EVIDENCE_20261106.json` | 闭环全阶段汇总（授权、补给、门禁、派遣、返航、结算、对账、点击审计） |
| `docs/research/P0_FLEET4_UI_ACTION_LOG_20261106.jsonl` | 24 条逐条可审计的已发送点击 |
| `docs/research/P0_FLEET4_VERIFICATION.md` | 本报告 |
