# 设计：生产 supervisor 控制器接线核查、状态机与失败关闭条件

**状态：** 独立设计与风险说明（REVIEW ONLY）。**本次 P0 不修改生产点击链。**
**基线：** `3e2e2ba`
**关联：** R3 生产控制器接线核查 / 只读架构审查 R3

用户约束原文：「不进行 schema 迁移或生产点击链修改，除非先提交独立设计和风险说明」。
本文件即该独立设计与风险说明；结论是**先不接线**。

---

## 1. 生产 tick 完整调用链（逐层证据）

| # | 层 | 位置 | 事实 |
| --- | --- | --- | --- |
| 1 | 统一 CLI | `src/kancollebot/cli.py:84-88` | `dispatch-once` → `one_expedition_ui_loop._main_impl()` |
| 2 | 参数入口 | `tools/one_expedition_ui_loop.py:1016` | `_main_impl` 解析 argv、设置 `RUN_ID`/`LOG_PATH`，调用 `run(args)` |
| 3 | 运行入口 | `tools/one_expedition_ui_loop.py:1001` | `run()` 调 `_run_production_supervisor_once(args)` |
| 4 | composition root | `tools/one_expedition_ui_loop.py:778` | 组装 monitor / dispatch controller / UI driver / runtime |
| 5 | 账号事实门禁 | `tools/one_expedition_ui_loop.py:809` | `_probe_unlocked_fleet_ids()` 零点击 CDP 探测 → `resolve_fleet_availability(strict=True)` |
| 6 | runtime | `tools/one_expedition_ui_loop.py:846-858` | `build_runtime(...)` 传 `unlocked_fleet_ids` + `fleet_availability_strict=True` |
| 7 | **supervisor 装配** | `tools/one_expedition_ui_loop.py:917-920` | `ContinuousExpeditionSupervisor(monitor=runtime.monitor, dispatch_controller=dispatch_controller)` —— **只传两个参数** |
| 8 | **单次 tick** | `tools/one_expedition_ui_loop.py:933` | `result = supervisor.tick()`，此后依据 `paused_reason` / `dispatches` 退出 |
| 9 | tick 内部 | `expeditions/continuous_supervisor.py:240-369` | `monitor.tick()` → `_pause_reason()` → 结算分支 → 补给分支 → 派遣循环 |

装配处未传 `settlement_controller` / `resupply_controller`，因此
`__init__` 把它们设为 `None`（`continuous_supervisor.py:44-45`）。

## 2. 结算 / 补给控制器在生产中是否可达

**结论：不可达。** 证据链：

- `continuous_supervisor.py:248-252`：仅当 `self.settlement_controller is not None`
  才调用 `_settlement_fleet_id()`；为 `None` 时直接得到 `None`。
- `continuous_supervisor.py:275-279`：仅当 `self.resupply_controller is not None`
  才调用 `_resupply_fleet_id()`；为 `None` 时直接得到 `None`。
- 因此 `RETURNED_PENDING_UI` / `RETURN_RESULT` / `BONUS_ITEMS` /
  `RESUPPLY_REQUIRED` 这些 phase 在生产 tick 中只会命中 `_pause_reason()`
  的 `fleet_<id>_settlement_pending` / `fleet_<id>_resupply_pending`，
  随后在 `continuous_supervisor.py:304-307` 以「仍暂停」返回，
  **不会调用** `settle_one_returned()` 或 `resupply_one_needed()`。
- 生产入口的返回码 2（`one_expedition_ui_loop.py:934-941`）会打印
  `PRODUCTION_SUPERVISOR_PAUSED`。

类内测试（`tests/test_continuous_supervisor.py`）确实覆盖了注入控制器后的
结算 / 补给分支，但那是**类内方法**证据，不是生产装配证据。本次新增的生产入口
装配断言测试专门锁定这一差距。

## 3. 缺失环节清单（接线前必须逐项具备）

1. **生产 UI driver**：`ContinuousSettlementController` 与
   `ContinuousResupplyController` 需要各自实现结算点击链、补给点击链的
   `ContinuousUiDriver` / `fleet_data_provider`。生产
   `CdpContinuousExpeditionUiDriver` 目前只覆盖派遣点击链。
2. **`fleet_data_provider`**：补给控制器按设计从注入的 supply provider 读数据，
   而不是共享 `PoiFleetStateReader`（`continuous_supervisor.py:128-130` 的注释
   明确说明），生产没有任何该 provider 的实例。
3. **`allow_ui_actions=True`**：两个控制器都必须显式开启；tick 在
   `continuous_supervisor.py:254-265` / `281-295` 检查，关闭时**失败关闭**。
4. **共享依赖一致性**：结算控制器必须与 monitor 共享 scheduler、state_reader、
   event_sink（`continuous_supervisor.py:97-118`）；补给控制器必须共享
   scheduler 与 event_sink（`:120-137`）。生产目前只保证 dispatch controller
   满足这些条件。
5. **共享 UI 仲裁器**：点击链必须共用同一个 `UiOperationArbiter`，否则可见点击
   不互斥。
6. **实机视觉证据**：P1-1 奖励领取视觉校准、Fleet 4 补给指纹、双帧核验、
   完整无人干预循环仍未完成（见 `docs/DESIGN_multi_task_runtime.md` §8）。
7. **授权**：真实会话与一次性授权，本轮明确禁止实机操作。

## 4. 状态机（接线后应遵循，本次不实现）

```
                  monitor.tick() 每 tick 一次
                          |
                  _pause_reason()
        +---------+---------+---------+---------+
        |         |         |         |         |
     BLOCKED  DISPATCHING SETTLEMENT RESUPPLY  ACTIVE(未验证母港)
        |         |         |         |         |
        |         |    settle_one_  resupply_  |
        |         |    returned()  one_needed()|
        |         |         |         |         |
        +---------+----+----+---------+---------+
                       |
             重新计算 _pause_reason()
                       |
        仍暂停 ----> 返回 paused（不派遣任何舰队）
                       |
                  dispatch_controller.allow_ui_actions?
                     False -> paused("ui_actions_disabled")
                       |
             按 dispatch_fleet_ids 顺序，仅 READY 才 dispatch
                       |
         +-------------+---------------------------+
   派发后非 ACTIVE                母港未验证
   -> paused(post_dispatch_not_active)  -> paused(post_dispatch_home_port_not_verified)
                       |
              SupervisorTickResult(dispatches, settlements, resupplies)
```

**每 tick 上限**：最多一次结算，其后最多一次补给（`continuous_supervisor.py:302-303`
注释）；若仍有第二个舰队需要动作，本 tick 报告暂停，留待下一 tick。这个上限是
防止一次 tick 内连续多次不可逆 UI 点击的刻意设计。

## 5. 失败关闭条件（接线前必须先固化）

1. **任何 pause reason 未被本 tick 解决 → 不派遣任何舰队**，包括其他 READY 舰队。
2. **控制器存在但 `allow_ui_actions` 为假 → 暂停**，绝不"绕过未解决的舰队去派别的"。
3. **补给需求存在而控制器不可用 → 暂停**：`RESUPPLY_REQUIRED` 的舰队永远到不了
   `READY`，继续派遣其他舰队会把它永久搁置。
4. **派发后状态不是 ACTIVE 或母港证据不成立 → 立刻暂停**，不再处理后续舰队。
5. **结算 / 补给点击链任一步缺少两帧 UI 证据 → 抛错且不重试**，由人介入。
6. **账号解锁事实未验证 → 不派遣**（R4 门禁，dispatch-once 已 strict=True）。
7. **任何视觉依赖缺失 → 启动期失败关闭**（`require_visual_dependencies`）。
8. 以上条件在**离线状态机测试**中可全部覆盖；但条件 5 的 UI 时序安全**只能**
   在真实 Poi 会话上验证，离线测试不证明它。

## 6. 风险说明：为什么本次不接线

| 风险 | 说明 |
| --- | --- |
| 不可逆点击 | 结算 / 补给点击链直接改变游戏内状态；缺少 §3 全部 7 项前置时接线等于用未校准的点击链做真实派遣后的收尾 |
| 证据层级不足 | 现有类内测试只证明状态机逻辑，不证明 UI 时序、坐标轴、双帧证据和 Fleet 4 补给指纹 |
| 状态机耦合 | 接线后 tick 会在同一 tick 内连续执行「结算 → 补给 → 派遣」，任何一步失败都会让 durable intent 与游戏状态分叉 |
| 授权边界 | 用户明确「禁止真实游戏操作」「实机验收暂缓，等待真实会话和一次性授权」 |
| 可逆性 | 不接线时生产行为与 `3e2e2ba` 完全一致；接线会引入无法用离线证据回退的 UI 行为 |

**决定：** 本次 P0 只交付核查证据、状态机 / 失败关闭设计、以及锁定当前装配的
生产入口断言测试。**不修改** `one_expedition_ui_loop.py` 的 supervisor 装配、
不新增结算 / 补给 UI driver、不开启任何点击链。接线作为独立节点，须先满足
§3 全部前置并取得一次性授权。

## 7. 未解决风险（如实列出）

- 生产中舰队进入 `RETURNED_PENDING_UI` / `RESUPPLY_REQUIRED` 后，单次
  dispatch-once 运行会返回 2 并停止；**目前没有自动收尾能力**。这是已知限制，
  不是缺陷修复完成。
- `ContinuousExpeditionSupervisor` 的 `SETTLEMENT_PHASES` / `RESUPPLY_PHASES`
  与 `FleetPhase` 枚举是硬编码字符串集合，未来多任务化需要 Intent 抽象。
- 结算 / 补给控制器的 `allow_ui_actions` 默认关闭，生产装配时若误传 `False`
  会静默退化成「永远暂停」；接线时应加启动期断言。
