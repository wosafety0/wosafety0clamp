# 多任务运行时设计（DESIGN ONLY — 未接入生产）

> **状态：设计与离线验证阶段。** 本文件描述的三套接口已有纯内存参考实现
> （`src/kancollebot/task_runtime/`）与离线测试（`tests/test_multi_task_runtime.py`），
> **没有任何生产入口调用它们**。它们不执行点击、不连接 Poi/CDP、不读写游戏状态
> 数据库，也不改动任何既有 CLI 行为。声明一个任务 ≠ 实现它。

## 1. 目标与边界

项目后续要加入自动化战斗、刷闪、编成、做任务。当前只有远征一条链路，但多个
任务会争抢同一份物理资源：同一个 Poi 窗口、同一批舰队、同一套持久化状态。
本设计只回答「**多任务如何被安全地登记、隔离和仲裁**」，不回答「如何打一场战斗」。

三套接口的分工：

| 接口 | 回答的问题 | 不负责 |
|---|---|---|
| `TaskRegistry` | 这个任务被允许存在吗？它声明了哪些能力？ | 执行任务、点击、调度 |
| `FleetLease` | 此刻这艘舰队归谁？ | 游戏内编成、舰队状态机 |
| `UiArbitration` | 此刻谁可以进入 Poi UI？ | 真正串行化（仍由内核互斥锁负责） |

## 2. 已核实的事实（2026-11-04，只读核对）

| 位置 | 事实 | 后果 |
|---|---|---|
| `adapters/poi/ui_operation_lock.py:19` | 单一命名内核互斥锁 `KancolleBot.PoiUiOperation.v1` | 全进程只有一把锁，没有任务归属、优先级、排队 |
| `expeditions/continuous_scheduler.py:256` | `UiOperationArbiter.operation(label)` 只是 `poi_ui_operation(label)` 的薄封装 | label 是自由字符串，无任务维度 |
| `expeditions/fleet_state_store.py:25` | `fleet_runtime(fleet_id INTEGER PRIMARY KEY, ...)` | 一个 fleet_id 只有一行；远征占着 Fleet 2 时战斗/编成会覆盖它 |
| `expeditions/dispatch_recovery.py:86` | `dispatch_intents(fleet_id INTEGER PRIMARY KEY, ...)` | 同上，崩溃恢复的意图也只能有一个 |
| `expeditions/dispatch_recovery.py:42` | `DispatchIntent` 用 `RUNTIME_CONFIG.dispatchable_fleet_ids` 校验 | 这是**远征策略**；战斗要用的 Fleet 1 会在这里被拒 |
| `expeditions/continuous_scheduler.py:17` | `FleetPhase` 9 个状态全是远征语义 | 状态机本身不可跨任务复用 |
| `adapters/poi/cdp_fleet_state.py:80` | `read_poi_fleet_states` docstring 为 "Fleet 2/3 supply"，`PoiFleetMissionState.mission` 是 `api_mission` 四元组 | 无士气 `api_cond` / HP，刷闪与战斗所需数据缺失 |
| `adapters/poi/full_supply.py:298` | preflight 硬编码 `api_mission != [0,0,0,0]` 表示「舰队在外」 | 「舰队忙」被写死成远征语义 |
| `adapters/poi/visual_target.py:52` | `SUPPLY_FLEET_TAB_REGIONS` 只有键 1/2/3 | 坐标是远征补给屏专属，不是通用常量 |
| `kancollebot/cli.py:17` | 只有 `monitor` / `dispatch-once` 两个硬编码命令 | 无任务注册表、无通用 composition root |

`task_scope.py` 的共享能力边界已按**符号级**重新分类：3 项 `task_neutral`、
3 项 `needs_decoupling`、2 项 `task_specific`。`require_task_neutral_capability()`
把这份清单变成可执行门禁：第二个任务引用 `needs_decoupling` / `task_specific`
符号时会 fail-closed，而不是「先用了以后再解耦」。

## 3. 接口契约

### 3.1 `TaskRegistry`（`task_runtime/registry.py`）

`TaskRegistration` 是一个**能力声明**，不是可执行任务：`task_kind`、状态、
`FleetPolicy`、已校准画面 id、视觉门是否验证、依赖能力清单、是否需要显式授权。

状态：`DECLARED`（声明）→ `CALIBRATED`（画面/视觉门已校准）→ `ENABLED`
（允许进入 UI 动作）。不变量：

1. `fleet_policy` 必须**逐字段等于** `task_scope.fleet_policy_for(task_kind)`。
   只校验 `policy.task_kind` 标签不够：`dataclasses.replace` 可以保留远征的
   Fleet 1 禁派与 `(2,3,4)` 上限、只把标签改成 combat，从而把远征策略走私进
   战斗任务（离线测试复现过这个伪造）。构造期校验标签配对，注册期校验策略同源。
2. `ENABLED` 必须同时满足：`fleet_policy.implemented`、`ui_click_capable`、
   `visual_gates_verified`、至少一个 `screen_ids`、`authorization_required=True`。
   授权不可关闭。
3. `required_capabilities` 的名字必须来自 `SHARED_CAPABILITIES`（防拼写错误）。
   `ENABLED` 的每一项都必须能通过 `require_task_neutral_capability()`；
   `DECLARED` / `CALIBRATED` 可以把非中立项登记为**待解耦依赖**，但不得据此执行。
4. 注册为 `ENABLED` 以及 `require_enabled()` 查询时，都委托
   `task_scope.require_task_enabled()` 做第二道校验 —— 注册表不复制策略规则，
   避免两套真值。未实现的任务仍可登记为 `DECLARED`，只是永远拿不到 UI 准入。
5. 同一 `task_kind` 重复注册：内容相同幂等，内容不同拒绝（不静默覆盖）。

`require_enabled(kind)` 是唯一的「可以进入 UI」查询入口。

### 3.2 `FleetLease`（`task_runtime/fleet_lease.py`）

身份是 `(task_kind, fleet_id, owner_run_id, lease_id)`，但**互斥键是 `fleet_id`**：
同一时刻一艘舰队最多一个活跃租约，与任务类型无关。这是物理约束（游戏里只有一个
第 2 舰队），不能用「按任务分表」绕开。

- `acquire()` 校验：任务已启用（委托 `TaskRegistry` / `require_task_enabled`）、
  `fleet_id` 在 `all_fleet_ids` 且在观测到的解锁集内、TTL 为有限正数、
  `owner_run_id` / `purpose` 非空。
- 冲突即 `LeaseConflictError`，**不自动续租、不自动抢占**。过期租约仍然阻塞，
  必须先显式 `reclaim_expired()`（有留痕）再获取 —— 静默抢走别人的舰队比拒绝
  更危险。
- `renew()` / `release()` 都校验 `owner_run_id`，防止一个任务释放别人的租约。
- `holder(fleet_id)` / `active_leases()` 是只读视图，供仲裁与诊断使用。
- 时钟可注入，全部离线可测；参考实现**只在内存**，不写 SQLite。

与现有持久化的关系：`fleet_runtime` / `dispatch_intents` 的 `fleet_id` 单主键
必须先迁移成含 `task_kind` 的复合键或独立的 lease 表，才能承载第二个任务。
**本轮不做该迁移**（见 §5）。

### 3.3 `UiArbitration`（`task_runtime/ui_arbitration.py`）

`UiRequest` 带 `task_kind`、`operation`、`priority`（`SAFETY` < `OPERATOR` <
`AUTOMATION`，数值越小优先级越高）、`run_id`、`visual_gate_verified`。
`UiArbiter` 是 `Protocol`；`InMemoryUiArbiter` 给出参考决策：

| 条件 | 决策 |
|---|---|
| 任务未注册/未启用 | `REJECTED_TASK_DISABLED` |
| `visual_gate_verified=False` | `REJECTED_NO_VISUAL_GATE` |
| 无 holder | `GRANTED` |
| 有 holder 且请求优先级不高于 holder | `REJECTED_BUSY` |
| 有 holder 且 `SAFETY` 请求更高优先级 | `PREEMPT_REQUESTED` |

**安全边界：`PREEMPT_REQUESTED` 的 `may_interrupt_in_flight_click` 恒为 `False`。**
高优先级只能「请求让出」，不能中断已经开始的半个点击序列；由 composition root
在当前操作结束后交接。`ui_operation_label(request)` 生成
`"<task_kind>:<operation>:<run_id>"`，供现有 `poi_ui_operation(label)` 使用。

本仲裁是**准入决策层**，不替换 `ui_operation_lock.py` 的跨进程互斥锁；互斥锁仍是
最后一道串行化，且任何真实点击仍须持有它。

## 4. 被明确放弃的方案

- **按任务复制一套入口/调度器**：会让 UI 锁、补给、证据链各分裂一份，冲突更隐蔽。
- **抢占式 UI 中断**：中断半个点击可能触发未审查的游戏动作，收益低于风险。
- **自动回收过期租约**：会把「进程崩溃」误判成「舰队空闲」。改为显式回收。
- **把 capabilities 按模块粒度分类**：实测同一模块内既有共享事件 sink 又有远征
  调度事务，模块级分类必然误判。
- **本轮直接改 SQLite 主键 / 接线生产入口**：属破坏性迁移，需实机窗口与独立评审；
  用户要求「先做设计与离线测试」。

## 5. 迁移路径（均未执行）

1. **A（本轮）**：边界修正、三套接口、离线测试、归档。
2. **B**：解耦 3 项 `needs_decoupling`（舰队快照拆出 `api_mission`、补给
   「忙」谓词泛化、坐标移入 ScreenRegistry），每项带实机校准证据后才降级为
   `task_neutral`。
3. **C**：持久化 lease 表 + `fleet_runtime` / `dispatch_intents` 复合主键迁移，
   含旧库兼容与回滚演练。
4. **D**：composition root 接线（注册 → 租约 → 仲裁 → 现有互斥锁 → 点击），
   一次只启用一个任务，远征行为逐字节不变。
5. **E**：加入第二个任务（战斗/编成等），需独立授权与实机窗口。

## 6. 离线测试覆盖

`tests/test_multi_task_runtime.py`：注册策略隔离（伪造/错配策略拒绝、未实现任务
不可 ENABLED、能力门禁）、租约（跨任务冲突、不同舰队共存、TTL、过期不自动抢占、
owner 校验、未解锁舰队拒绝）、仲裁（未启用/无视觉门拒绝、优先级、不中断点击）、
以及「三套接口全程零 Poi / 零点击 / 零生产数据库」的隔离断言。

## 7. LIMITS / 未决

- 三套接口**没有**接入 `cli.py` 的 `monitor` / `dispatch-once`，也不改变任何
  现有默认值；`kancollebot tasks` 只展示边界，不展示注册表状态。
- 没有实现 ScreenRegistry、`api_cond`/HP 读取、编成规则、任务队列或持久化 lease。
- `FleetPhase` 与 `DispatchIntent` 仍是远征语义；多任务并发前必须抽任务无关
  Intent。
- 优先级策略与安全交接时机需在真实 Poi 会话上复核；离线测试只能证明决策逻辑，
  不能证明 UI 时序安全。
