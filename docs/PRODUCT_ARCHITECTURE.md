# 产品架构（Product Architecture）

> 状态：DRAFT（本轮治理任务产出，待评审）
> 实测基准日：2026-11-05
> 适用仓库：`wosafety0/wosafety0clamp`
> 本文档只描述**架构与模块边界**，不重复分支治理（见 `BRANCHING_STRATEGY.md`）与开发路线（见 `DEVELOPMENT_ROADMAP.md`）。
> 现状证据来自仓库实测（含 `origin/feat/expedition-min-ships-20261105` @ 5290f51、PR #35、子代理只读梳理）。

---

## 1. 总体结构

KancolleBot 是一个**只读优先、fail-closed** 的舰娘游戏自动化机器人。设计上分成两层：

- **公共平台层（Platform）**：与具体玩法无关的通用能力（Poi 接入、只读访问、快照、新鲜度、模型、视觉识别、UI 操作、执行门禁、调度、日志/归因、异常恢复、配置、测试基建）。
- **业务模块层（Modules）**：远征（Expedition）、战斗（Combat）、刷闪（Sparkle）。每个模块独立负责自己的状态机与领域决策，**只通过稳定契约与平台层通信**。

当前**只有远征模块有实质实现**；战斗、刷闪模块尚未存在（仅契约中预留 `reserved`/`dispatchable` 占位，见档1 契约与 `task_scope.py`）。

---

## 2. 公共平台层（现状 + 职责）

| 能力 | 状态 | 关键文件 | 说明 |
|---|---|---|---|
| Poi 连接与接口适配 | IMPLEMENTED（只读范式已建立） | `adapters/poi/mcp_client.py`、`dock_reader.py`、`expedition_reader.py` | 只读 MCP/Redux 客户端，allowlist 仅 `poi_describe/poi_get/poi_lookup`，拒绝非回路端点与写工具。 |
| 实时 CDP 观测/控制 | IMPLEMENTED | `cdp_websocket.py`、`cdp_ui_driver.py`、`continuous_expedition_ui.py` | 可见 UI 输入，坐标来自已审录制，回路-only。 |
| 只读数据访问 | IMPLEMENTED | `expedition_reader.py` | 只读取 Redux 状态，无 dispatch 方法。 |
| 数据快照 | IMPLEMENTED | `background_port_snapshot_provider.py`、`core/observed_state.py` | 后台快照提供；快照一致性要求见第 6 节。 |
| 数据新鲜度与一致性 | IMPLEMENTED（fail-closed） | `adapters/poi/port_freshness.py:79-139` | 校验形状、age、字段精确相等；旧/缺/歧义观测一律 `raise`。 |
| 舰队/舰船/装备/资源模型 | IMPLEMENTED | `core/models.py:8-30` | `ShipState`/`FleetState`，frozen+slots，构造即校验。 |
| 视觉识别 | IMPLEMENTED（缺依赖 fail-closed） | `visual_state_gate.py`、`ui_state_gate.py`、`visual_target.py` | 模板匹配（OpenCV 可选）、无障碍文本分类。 |
| UI 操作 | IMPLEMENTED | `cdp_input.py`、`cdp_ui_driver.py` | 回路-only、allowlist、坐标受控。 |
| 任务执行门禁 | IMPLEMENTED（fail-closed） | `safety/action_preflight.py`、`safety/policy.py`、`task_scope.py:303` | 纯证据检查，实现级 fail-closed 守卫。 |
| 任务调度 | IMPLEMENTED（离线状态机） | `expeditions/continuous_scheduler.py` | 多舰队状态机 + `UiOperationArbiter` 互斥。 |
| 日志 / 事件归因 / 监控 | IMPLEMENTED | `core/task_attribution.py`、`continuous_monitor.py` | 可信事件封套；调用方 `**extra` 不能覆盖信任 `task_kind`。 |
| 异常恢复 | IMPLEMENTED | `dispatch_recovery.py`、`action_journal.py` | 崩溃安全意图持久化、at-most-once 日志。 |
| 配置与策略管理 | IMPLEMENTED | `task_scope.py`（TaskKind/FleetPolicy 单一事实来源）、`runtime_config.py` | `fleet_policy_for()` 含 fail-closed 守卫。 |
| 自动化测试基建 | IMPLEMENTED | `tests/`（78 个 Python unittest 测试模块 + 3 个 Node `.cjs` 测试文件；用例数以实际运行输出为准） | unittest + `node --test`；CI 见 `BRANCHING_STRATEGY.md` §2.4。 |

> **平台层约束**：业务模块不得复制一套 Poi 接口或数据读取逻辑。当前存在两套 Poi 访问范式（只读快照层 vs 实时 CDP 层），职责不同（观测 vs 控制），但概念重叠，需用文档明确边界（见第 7 节）。

---

## 3. 远征模块（Expedition）

- **状态**：IMPLEMENTED（派遣可用，完整闭环 PARTIAL）
- **职责**：舰队可派遣性评估、FleetPolicy、编队要求、任务选择、补给、派遣、计时等待、收取、循环调度、多舰队协调、异常恢复。
- **关键文件**：
  - `mission_catalogue.py` + `data/expedition_min_ships.json`（M1：api_deck_num→minimum_ships，不推断船型；用户已加硬化提交 5290f51 拒绝强制类型转换）。
  - `mission_eligibility.py`（组合需求/资格评估，含旗舰等级）。
  - `continuous_dispatch_controller.py`（派遣事务边界，已接线）。
  - `continuous_settlement_controller.py` / `continuous_resupply_controller.py`（R3 两半，**已实现但未接线**）。
  - `continuous_supervisor.py`（协调器，组合根未传结算/补给控制器 → R3 gap）。
  - `continuous_scheduler.py`（状态机）。
- **生产入口**：`tools/one_expedition_ui_loop.py`（CLI `dispatch-once`）、`tools/run_continuous_expedition_monitor.py`（CLI `monitor`，只读）。
- **已知缺口**：R3 未接线；完整闭环（收取→再派遣→返回稳定页）尚未在无人工干预下端到端验证。

---

## 4. 战斗模块（Combat）

- **状态**：DESIGNED / PLANNED（契约预留，无实现）
- **职责**：战斗场景识别、战斗状态机、战斗决策、战斗执行、结果确认、风险/资源控制、异常中止与恢复。
- **约束**：复用公共平台（视觉识别、UI 操作、门禁、归因），**不复用远征专属业务状态机**。
- **现状**：档1 契约 `tasks[]` 中 combat 的 `implemented=false`、`ui_click_capable=false`；`task_scope.py` 无 combat 政策。无代码。

---

## 5. 刷闪模块（Sparkle）

- **状态**：DESIGNED / PLANNED（契约预留，无实现）
- **职责**：舰船状态评估、刷闪策略、流程状态机、疲劳管理、舰船轮换、与远征调度的资源协调、异常恢复。
- **约束**：复用公共平台；独立状态机。
- **现状**：契约中 sparkle `implemented=false`；无代码。

---

## 6. 跨模块调度（规划，未实现）

- **状态**：PLANNED
- **范围**：模块间资源/舰船冲突、占用管理、任务优先级、调度公平性、运行状态监控、暂停/恢复/取消。
- **现状**：仅远征模块内有 `UiOperationArbiter` 与 `FleetPhase` 互斥；跨模块协调尚未设计。
- **约束**：这部分是架构规划，**不代表当前已实现**。

---

## 7. 模块依赖与接口契约

- **稳定契约**：
  - `task_scope.py`：TaskKind / FleetPolicy 的**单一事实来源**，提供 fail-closed 守卫。
  - 档1 `docs/contracts/task-contract-v1.json`：**当前无运行时消费者**（README 自证“没有任何代码因该文件改变行为”）。测试仅做对表 `task_scope.py` 的稳定性校验。**这是明确的整合缺口**——契约与生产代码未打通。
  - `core/task_attribution.py`：可信事件归因封套，防止调用方伪造 `task_kind`。
- **依赖方向**：业务模块 → 平台层（单向）；模块之间不得直接依赖彼此内部实现。
- **快照一致性要求**：Poi 快照需通过 `port_freshness` 形状/age/字段精确相等校验；不一致即 fail-closed。

---

## 8. 安全约束与满足情况

| 约束 | 状态 | 证据 |
|---|---|---|
| 模块间通过稳定契约通信 | PARTIAL | `task_scope.py` 已建立；但档1 契约未接入运行时。 |
| 业务模块不直接依赖其他模块内部实现 | MET（设计）/ UNVERIFIED（无第二模块） | 仅远征模块存在。 |
| 平台层不含玩法专属业务决策 | MET | 平台层为通用能力；玩法决策在 `expeditions/`、`task_scope.py`。 |
| 任务执行具备可验证状态前置条件 | MET | `safety/action_preflight.py`、`task_scope.py:303`。 |
| 不确定/过期数据必须 fail-closed | MET | `port_freshness.py`、M1 拒绝越界/强制类型。 |
| 禁止伪造时间戳/默认零值/不可靠快照证明新鲜 | MET | `port_freshness.py` 校验 age 与字段精确相等；无零值兜底。 |
| 禁止通过 API 直接制造未授权游戏操作 | MET | 只读 MCP allowlist 无写工具；CDP 仅可见 UI 输入。 |
| UI 自动化必须有明确页面状态确认与异常中止 | MET（远征）/ UNVERIFIED（端到端） | `ui_state_gate.py`、`visual_state_gate.py`；但无人工干预的端到端闭环尚未验证。 |

---

## 9. 现有架构问题（待解决）

1. **R3 未接线**（最高优先）：生产组合根位于 `tools/one_expedition_ui_loop.py:914-932`；当前仅构造并注入 dispatch controller，未构造/注入 settlement 与 resupply controller。控制器和 supervisor 分支逻辑已实现；生产 `fleet_data_provider` 尚无实时 Poi `FleetSupplyObservation` 构造来源。详见 `DEVELOPMENT_ROADMAP.md` P0。
2. **档1 契约未接入运行时**：契约是文档+稳定性测试，生产代码不读取。需决定是“消费契约”还是“以 `task_scope.py` 为唯一真值并废弃 JSON 契约”。
3. **双 Poi 访问范式**：只读快照层与实时 CDP 层概念重叠，需文档明确边界，避免未来各模块各建一套。
4. **死代码**：`adapters/poi/expedition_state.py`（135 行旧版）已被 `expedition_state_v2.py` 取代且无任何导入，建议删除。
5. **7 舰编成 vs `MAX_FLEET_SIZE=6`**：Agent 测试暴露潜在限制；需先确认 MVP 是否需要 7 舰，再评估（独立问题，不在 M1 范围）。
6. **模块缺失**：战斗、刷闪模块尚无实现，跨模块调度未设计。
