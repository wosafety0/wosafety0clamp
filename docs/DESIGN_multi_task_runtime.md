# 多任务运行时设计（组装层已接只读 CLI；生产点击链未接线）

> **状态：阶段 A+（组装层接线）+ 离线验证。** 三套接口已有纯内存参考实现
> （`src/kancollebot/task_runtime/`）与离线测试（`tests/test_multi_task_runtime.py`）。
> 2026-11-04 新增 `context.py`：把注册表、舰队租约与 UI 仲裁组装到**同一个
> `TaskRegistry`**，并通过只读 `kancollebot tasks` 暴露注册状态。这是 D-0 步，
> **仍没有任何生产 UI/点击入口消费它**；既有远征路径继续走 `task_scope` 门禁与
> 现有视觉门。全部任务默认 `DECLARED`（`ENABLED` 需实机视觉校准证据）。
> 组装层不执行点击、不连接 Poi/CDP、不读写游戏状态数据库，也不改动
> `monitor` / `dispatch-once` 行为。声明一个任务 ≠ 实现它。

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

## 5. 迁移路径（A、A+ 已执行；B–E 未执行）

1. **A（已执行，2026-11-04 早）**：边界修正、三套接口、离线测试、归档。
2. **A+ / D-0（已执行，2026-11-04 晚）**：`context.py` 把注册表 / 舰队租约 /
   UI 仲裁组装到**同一个 `TaskRegistry`**，经只读 `kancollebot tasks` 暴露注册
   状态；全部任务保持 `DECLARED`，生产点击链与 `monitor` / `dispatch-once`
   零改动。离线测试见 `tests/test_task_runtime_context.py`（10 项）。
3. **B（未执行）**：解耦 3 项 `needs_decoupling`（舰队快照拆出 `api_mission`、
   补给「忙」谓词泛化、坐标移入 ScreenRegistry），每项带实机校准证据后才降级为
   `task_neutral`。
4. **C（未执行）**：持久化 lease 表 + `fleet_runtime` / `dispatch_intents`
   复合主键迁移，含旧库兼容与回滚演练。
5. **D 剩余（未执行）**：把 context 接进 `build_runtime` 的任务门禁、
   `UiOperationArbiter.operation` 与 `ContinuousExpeditionSupervisor.tick()`
   的点击链（注册 → 租约 → 仲裁 → 现有互斥锁 → 点击），一次只启用一个任务，
   远征行为逐字节不变。**前置**：B 的实机校准证据、C 的迁移评审、独立评审与
   实机窗口。
6. **E（未执行）**：加入第二个任务（战斗/编成等），需独立授权与实机窗口。

## 6. 离线测试覆盖

`tests/test_multi_task_runtime.py`：注册策略隔离（伪造/错配策略拒绝、未实现任务
不可 ENABLED、能力门禁）、租约（跨任务冲突、不同舰队共存、TTL、过期不自动抢占、
owner 校验、未解锁舰队拒绝）、仲裁（未启用/无视觉门拒绝、优先级、不中断点击）、
以及「三套接口全程零 Poi / 零点击 / 零生产数据库」的隔离断言。

`tests/test_task_runtime_context.py`（A+ 新增，10 项）：全部声明任务按各自派生
策略注册为 `DECLARED`、三套接口共享同一注册表、错配注册表被拒、无实机证据时
`require_enabled` 一律拒绝、生产门禁仍委托 `task_scope`、租约与 UI 准入对
`DECLARED` 任务 fail-closed、只读 CLI 暴露注册状态、离线构造的 ENABLED 远征能
贯通准入链且互斥键为 `fleet_id`、以及全流程零 SQLite / 零 UI 锁副作用。

## 7. LIMITS / 未决

- 组装层**只**接入只读 `kancollebot tasks`（展示注册状态）；三套接口**没有**
  接入 `cli.py` 的 `monitor` / `dispatch-once`，也不改变任何现有默认值。
  注册表的 `ENABLED` 不授权任何生产点击，`context.describe()` 明确标注
  `click_path_wired=False`。
- 没有实现 ScreenRegistry、`api_cond`/HP 读取、编成规则、任务队列或持久化 lease。
- `FleetPhase` 与 `DispatchIntent` 仍是远征语义；多任务并发前必须抽任务无关
  Intent。
- 优先级策略与安全交接时机需在真实 Poi 会话上复核；离线测试只能证明决策逻辑，
  不能证明 UI 时序安全。

## 8. 实机验收前置清单（未执行）

本节是 D/E 阶段的验收准备。**本轮未执行任何真实点击、派遣或实机操作**；离线
测试只证明决策逻辑，不能证明 UI 时序安全或真实客户端可用性。

### 必需前置（缺一不可，均需用户单独授权 / 提供）

1. 真实 Poi 会话与游戏窗口；一次性可见 UI 操作授权；操作者全程旁看。
2. P1-1 奖励领取视觉门禁校准证据（双帧 / 视觉证据）。
3. Fleet 4 补给页签与复选框指纹校准证据（当前遇到 `RESUPPLY_REQUIRED` 一律暂停）。
4. 远征完整循环实机证据：派遣 → 返回 → 结算 → 补给（P1-2、P1-3）。
5. 阶段 B 的 3 项 `needs_decoupling` 实机校准证据，才可把能力降级为 `task_neutral`。

### 验收项与停止条件

- 把远征注册为 `ENABLED` 时，`screen_ids` 与 `visual_gates_verified` 必须逐项
  对应上面的真实证据；证据缺失即停止，不得用构造器开关代替。
- 在真实 UI 时序下复核 `FleetLeaseTable` 与 `poi_ui_operation` 跨进程互斥锁的
  关系：租约只是准入层，不得替换互斥锁；任何点击仍须持有该锁。
- 复核 `PREEMPT_REQUESTED` 的让出时机；`may_interrupt_in_flight_click` 恒 False，
  绝不中断半个点击序列。
- 一次只启用一个任务，并记录远征路径的事件 payload 对照，证明「行为逐字节不变」。
- 出现歧义、证据冲突或意外 UI 状态一律 fail-closed 并停止，不重试。

### 可复现的离线基线（本轮已执行，作为对照）

```
python -m compileall -q src tests tools
PYTHONPATH=src python -m unittest discover -s tests
node --test tests/static_decoder_extract.test.cjs tests/verify_cdp_snapshot.test.cjs tests/poi_port_response_observer.test.cjs
PYTHONPATH=src python -m kancollebot.cli tasks
```
