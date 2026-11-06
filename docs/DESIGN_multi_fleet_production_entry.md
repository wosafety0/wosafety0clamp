# 多舰队生产远征入口：组合层设计与风险边界

**状态：DESIGN ONLY；未实现、未测试、未在 Poi 实机验证。**  
**基线分支：** `dev/settle-only-wait-p0-20261106`  
**基线提交：** `a21512081439b949df108dd2f1d91358f8db1cf6`  
**范围：** 在不重写现有 Drain、结算、补给、派遣控制器的前提下，设计一个真正持有多支舰队的生产组合入口。

## 1. 审计结论

当前仓库已经有多舰队 Drain 的核心能力，不应再实现第二套 Drain：

- `ContinuousExpeditionSupervisor` 收集所有 pending settlement/resupply fleets；启用 `drain_session` 后，一次 Drain 清理全局奖励页，再逐队结算、逐队补给，最后按 scheduler 顺序派遣所有 READY 舰队。
- `ContinuousSettlementController.settle_one_returned(..., pages_drained_globally=True)` 会验证奖励页已消失，并通过 `SqliteDispatchRegistry` 做唯一派遣归属；无账本、零匹配、多匹配或页面仍在时拒绝。
- `ContinuousResupplyController` 虽然每次只补一支舰队，但同一 supervisor 可以逐队调用；它与 UI driver、scheduler、event sink、arbiter 共享运行上下文。
- `CdpContinuousExpeditionUiDriver` 已声明支持 Fleet 2/3/4，按 `fleet_id` 和 `mission_id` 执行可见 UI 派遣。

真正缺口位于生产 composition root：

- `tools/one_expedition_ui_loop.py` 只传 `dispatch_fleet_ids=(target_fleet_id,)`，mission requirements 也只为单个目标构建。
- 启动奖励页归属、启动结算和等待逻辑均围绕一个 `target_fleet_id` 编写。
- `--global-drain` 虽可显式接线，但在单舰队入口中不等于多舰队生产运行。
- `tools/run_continuous_expedition_monitor.py` 可以监控多支舰队，但它是只读入口，不能替代可派遣生产入口。

## 2. 目标入口与显式配置

建议新增独立入口：`tools/multi_fleet_expedition_loop.py`。不改变既有单舰队入口的行为。

配置采用显式 JSON 文件，不把任务分配写死在代码里：

```json
{
  "assignments": {
    "2": 3,
    "3": 1,
    "4": 2
  }
}
```

上例仅复用此前 L4 soak 使用过的 Fleet 2→mission 3、Fleet 3→mission 1、Fleet 4→mission 2 作为**候选配置示例**，不是默认策略，也不代表通用远征优化结果。实际启用前必须确认舰队解锁事实、任务规则置信度、每队编成及补给校准。

启动校验必须拒绝：

1. 空配置、重复/非法 fleet id、非正 mission id、未知任务。
2. assignment fleet 不属于 `RUNTIME_CONFIG.dispatchable_fleet_ids`。
3. 目标舰队未被 Poi 当前账号快照证明已解锁；不得把配置候选当成账号事实。
4. mission requirement 缺失、冲突或低于生产要求的 `cross_checked` 置信度。
5. 任务映射与 runtime 实际 scheduler fleet 集合不完全一致。
6. `--global-drain` 未显式启用，或 Drain UI 能力不齐。
7. UI 动作未显式授权，或审计 sink 无法在动作前写入。

通过 `requirements_for_assignments({fleet_id: mission_id}, rule_set=DEFAULT_RULE_SET)` 为每队生成对应 requirement；调用 `build_runtime(dispatch_fleet_ids=..., mission_requirements=..., unlocked_fleet_ids=..., fleet_availability_strict=True)`。不得复用 monitor builder 的默认任务映射来猜测生产分配。

## 3. 共享持久化与依赖拓扑

一个多舰队进程必须只有一套共享持久化与运行时依赖：

- 一个专用 SQLite 数据库：`SqliteFleetStateStore`、`SqliteDispatchIntentStore`、`SqliteDispatchRegistry`、`SqliteDispatchEventLog` 均指向同一文件。
- 一个 monitor/scheduler/state reader/event sink 实例；各控制器必须通过 supervisor 现有 identity assertions。
- 一个 Poi 只读状态读取链、一个 CDP game webview session、一个 `CdpContinuousExpeditionUiDriver`。
- 一个 `UiOperationArbiter`，供 freshness refresh、dispatch、settlement、resupply 和 global drain 共用。
- 一个运行日志 sink；每个 tick、舰队状态变化、Drain 摘要、settlement attribution、resupply cost、dispatch identity、暂停原因和退出结果均可追踪。

不得把 Fleet 2/3/4 各自独立的 SQLite 文件直接拼成一次多舰队运行。跨队唯一归属依赖共享 dispatch registry；不同数据库不能共同回答同一批 pending returns 的候选集合。第一版使用新的共享数据库。首次启动若目标舰队已有在途远征但共享账本没有唯一 confirmed dispatch identity，必须拒绝自动结算；不从舰队编号、到达顺序或任务相同推断归属。

## 4. 启动恢复边界

第一版只支持在稳定 `HOME_PORT` 画面启动：

- 启动先进行只读账号舰队快照、视觉状态双帧核验、共享数据库恢复和所有目标舰队状态核对。
- 若奖励页、未知页、模态窗口或画面不稳定，零点击退出；不得将单舰队 `_probe_startup_attribution(args, runtime, target_fleet_id)` 复用于多舰队。
- 对共享数据库中已有的 active/returned 任务，必须能以唯一 `dispatch_id`、fleet id、mission id、ship id set、confirmed 状态核对 Poi 当前观测。
- 任一目标舰队存在未解决 intent、unknown dispatch outcome、缺失/重复账本匹配、编成不符或 phase 冲突，整轮 fail-closed。
- 新数据库首次启用时，最保守的首跑条件是所有目标舰队处于 Poi 可证实的 idle/READY 状态；不自动“收养”没有共享身份记录的在途远征。

这条限制避免启动画面无法归属时错误领取奖励。后续如需支持任意奖励页启动，应另行设计多舰队 startup attribution，不能悄悄放宽本门禁。

## 5. Tick 生命周期

每 tick 复用现有 supervisor，不复制业务状态机：

1. monitor 读取所有目标舰队并执行 freshness/identity checks。
2. 若存在一个或多个 pending settlement fleet：执行**一次** global Drain。
3. Drain 未稳定到 HOME_PORT：本 tick 零 settlement、零 resupply、零 dispatch。
4. Drain 成功后，对全部 pending settlement fleet 逐队执行 `settle_one_returned(..., pages_drained_globally=True)`；每队必须通过唯一账本归属。
5. 对全部 `RESUPPLY_REQUIRED` fleet 按 scheduler 顺序逐队补给；每次动作重新读取新鲜 Poi supply snapshot 并验证燃料/弹药。
6. 对全部 READY fleet 按配置顺序派遣各自任务；每队必须完成完整页面 checkpoint、编成门禁、满补给门禁、派遣身份持久化、Poi 新 mission tuple 确认及双帧 HOME_PORT 证明。
7. 记录 tick 汇总；异常立即停止，不自动重试有歧义的 UI 输入。

UI 操作仍然串行，不并发点击。现有 supervisor 的 Drain 路径已经提供“全量 pending → 单次 Drain → 逐队结算/补给 → READY 队派遣”的编排基础。

## 6. 主循环与成功条件

多舰队入口不能保留单舰队假设：

- 不再要求 `len(result.dispatches) == 1`；应逐项验证返回 dispatch 与配置 assignment 一一对应。
- 不再只检查一个 `target_fleet_id`；应检查所有 configured fleets 的 scheduler phase、mission id、ship ids、final_time_ms 和 `home_port_verified`。
- `wait-for-return` 需要按全体目标舰队的 phase 判断：所有仍在途且 home verified 时才继续等待；存在 BLOCKED、DISPATCHING、UNKNOWN 或无可信观察时按明确预算停止。
- 明确区分“本 tick 完成了多队派遣”“所有目标队进入 active”“所有目标队完成一个结算周期”“soak 达到指定周期数”。不能以单 tick dispatch 成功冒充完整 soak。
- 设置有限的 `max_ticks`、单调时钟超时和明确退出码；到期不代表返航，必须由 Poi 新鲜观测确认。

## 7. 离线验收矩阵

新入口与组合层测试至少覆盖：

| 场景 | 预期 |
|---|---|
| assignments 2/3/4 完整且账号解锁一致 | 构建包含全部目标舰队的 scheduler |
| 缺失一队任务映射/出现额外映射 | 启动拒绝，零 UI 输入 |
| Fleet 4 未解锁或未通过补给校准 | 拒绝相关动作，不自动裁剪成“看似成功” |
| global Drain 未启用/能力不齐 | 多舰队模式启动拒绝，不回落单队 Drain |
| 两队不同任务同时 pending | 一次 Drain、两次唯一 settlement、逐队 resupply |
| 两队同任务但 ship ids 不同 | 通过各自唯一账本行分别归属 |
| 两条相同 fleet/mission/ship identity 候选 | `UNATTRIBUTED`，零结算点击 |
| 一队账本缺失/unknown/未 confirmed | 整轮 fail-closed，不靠另一队成功推断 |
| Drain 未到 HOME_PORT | 零 settlement/resupply/dispatch |
| 一队补给验证失败 | 不派遣任何 READY 队；失败原因可定位 |
| 一队派遣后 Home Port 证据失败 | 停止后续队伍派遣并记录已完成部分 |
| 启动画面为奖励页/UNKNOWN | 零点击退出 |
| run log 或 SQLite 写入失败 | 在对应动作前阻止动作 |
| wait tick 中无 READY 队且仍有 active fleet | 继续有界等待，不误判 ui_actions_disabled |
| 到期但 Poi 未确认返航 | 超时停止，不触发结算或重复点击 |

已有 `tests/test_supervisor_global_drain.py`、`tests/test_settlement_global_drain_barrier.py` 和 `tests/test_settlement_drain.py` 是基础覆盖；新测试应补充 production composition root、assignment parsing、共享数据库和多舰队启动门禁，而不是重复已有 Drain 单元测试。

## 8. 实机验收顺序

1. 只读启动核对：Poi unlocked fleet ids、当前页面、所有舰队任务 tuple、共享 DB 状态、assignment config。
2. 单 tick 多队 dispatch：仅在全部目标舰队 READY、补给满足且配置逐队通过门禁时执行；确认每队实际 mission tuple 与配置一致，并回到 HOME_PORT。
3. 等待自然返航；不为制造同时返航而重复派遣。
4. 观察到两队或以上真实 pending 时，才验证一次 global Drain、逐队 attribution、settlement、resupply。
5. 完成至少一个完整周期后再进入多轮 soak；每轮都核验账本无 open/unknown identity、日志完整、各队状态与资源变化。
6. 任一歧义或 UI_STUCK 即停，不自动重试点击。

此前 L4 soak 的 assignment（Fleet 2→3、Fleet 3→1、Fleet 4→2）可用于准备候选配置；但该 soak 报告不证明 global Drain 真机能力。当前仓库设计文档也明确标注 `--global-drain` 尚未在真实客户端启用。

## 9. 实施顺序与状态

- **P0.2a — 配置与 composition root：** 新入口、严格 assignment parser、账号解锁检查、单一共享数据库、统一 UI arbiter、global Drain 必需门禁。
- **P0.2b — 多舰队 startup reconciliation：** 首版只接受 HOME_PORT 启动；逐队核对共享 ledger 与 Poi tuple；拒绝无账本在途任务。
- **P0.2c — 多舰队主循环：** 移除 target 单队假设，按全体 fleet states 验证 dispatch/wait/return。
- **P0.2d — 离线测试：** 覆盖本文件第 7 节；CI 通过后才允许受控实机。
- **P0.2e — 实机验证：** 不主动制造同步返航；自然产生多队 pending 后验证 Drain 与全生命周期。

**当前结论：** 本文件只定义接口契约、启动约束和验收条件。没有创建入口、没有修改生产代码、没有执行 Poi 操作，也没有通过测试。