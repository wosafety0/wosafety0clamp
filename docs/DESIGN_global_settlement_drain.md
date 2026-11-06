# 第二阶段 P0.2 — 多舰队全局结算 Drain

状态：**编排 + 桥接 + 离线测试完成；`--global-drain` 默认关闭，未在任何真实客户端上启用过。**
模块：`expeditions/continuous_supervisor.py`（编排）、`expeditions/continuous_settlement_controller.py`（屏障迁移）、新增 `expeditions/settlement_drain_ui.py`（桥接）、`adapters/poi/continuous_expedition_ui.py`（三个对舰队无感知的公开方法）、`tools/one_expedition_ui_loop.py`（显式开关）。
测试：`tests/test_supervisor_global_drain.py`(14)、`tests/test_settlement_drain_ui_bridge.py`(11)、`tests/test_settlement_global_drain_barrier.py`(6)。

---

## 1. 要解决的问题

第一阶段的结算路径是**每 tick 一个舰队**：`_pause_reason()` 返回一个形如 `fleet_2_settlement_pending` 的字符串，再把它解析回一个舰队 id。这套写法有两个结构性缺陷：

1. **`FirstOrDefault` 换个名字**。两个舰队同时返航时，第二个舰队在选择阶段就不可见。
2. **一个舰队会挡住其他舰队**。只要有一个舰队停在结算相位，tick 立刻 `paused`，其余舰队（哪怕 READY）都不再被派遣。

而且旧的 `other_returns` 屏障无法在"多队同时返航"时放行——因为**奖励页说的是哪个远征，不是哪个舰队**，屏幕上那串页面无法归属到某一队。这不是 bug，是当时唯一的正确选择。

真正能解开这件事的只有两点：**页面可以不分舰队地被清掉**（全局 Drain），**归属可以由派遣账本给出**（第一阶段的 P0-3）。

## 2. 全量收集：一个状态解释器

`continuous_supervisor.py` 新增：

```python
@dataclass(frozen=True)
class FleetPendingAction:
    fleet_id: Any
    phase: str
    kind: str          # blocked / dispatch_reconciliation / settlement / resupply / home_port_verification
```

- `_pending_actions()` 遍历共享 Scheduler 的**每一个**舰队，把一个相位解释成一个"还缺什么"，返回完整列表。
- `pending_settlement_fleets()` / `pending_resupply_fleets()` 都从这张列表过滤，不再做字符串反解析。
- `_pause_reason()` 改为 `_pending_actions()[0].pause_reason()`：**与 P0.2 之前逐字节相同**（同样的调度顺序、同样的标签），但现在是**描述**而不是**选择**。标签只进日志和 `paused_reason`，不再决定点谁。

`_settlement_fleet_id()` / `_resupply_fleet_id()` 保留，仅被 legacy 路径使用（见第 3 节）。

## 3. 两条路径，显式选择

```python
if self.drain_session is not None and self.pending_settlement_fleets():
    return self._tick_with_global_drain(monitor_result)
return self._tick_per_fleet(monitor_result)     # legacy，逐字节保留
```

- **legacy**（无 Drain）：一个结算 + 一个补给 + 全部 READY 派遣，逐 tick 推进；`other_returns` 屏障照旧。默认行为。
- **drain**：见下。

Drain 会话必须与请求**同时**给出：`drain_session` 无 `drain_request` 直接 `ValueError`，反之亦然。原因是排水预算是点击预算，安全代码里不能有隐式默认值。

## 4. 全局 Drain 路径的次序

```python
drain = self.drain_session.drain(self.drain_request)      # 1. 一次，且不带 fleet_id
if not drain.reached_home:                                 # 2. 没证明回到母港
    return paused(f"settlement_drain_stopped:{error}")     #    零结算、零补给、零派遣
for fleet_id in self.pending_settlement_fleets():          # 3. 逐队结清（不是只第一队）
    settlement_controller.settle_one_returned(fleet_id, pages_drained_globally=True)
for fleet_id in self.pending_resupply_fleets():            # 4. 逐队补给
    resupply_controller.resupply_one_needed(fleet_id)
self._dispatch_ready_fleets(...)                           # 5. 全部 READY 派遣
```

要点：

- **一次 Drain 覆盖全账**：`SettlementDrainSession` 不接收 `fleet_id`，因此不可能"猜错舰队"。
- **Drain 失败即整 tick 停手**：页面处于未知状态时，任何后续点击都是盲点，所以结算/补给/派遣全部为零，并给 `requires_manual_intervention`。
- **不阻塞**：某队结算完变 `RESUPPLY_REQUIRED` 后立刻被补上，随后与原本 READY 的舰队一起被派遣——同一个 tick 内完成。
- `SupervisorTickResult` 新增 `drain` 字段（`DrainResult | None`），并把摘要写成 `supervisor_settlement_drain`（`pending_fleets` / `reached_home` / `completed_pages` / `actions_performed` / `final_page` / `error` / `requires_manual_intervention`）。

## 5. 屏障迁移：`pages_drained_globally`

`settle_one_returned(fleet_id, *, pages_drained_globally=False)`。

当 Drain 已经清掉页面、但 Poi 仍报 `status == 2`（客户端的 `api_port/port` 还没发）时，旧逻辑会**再点一遍奖励链**。所以新参数做两件事：

1. **先用屏幕自证**，不用 Poi 状态自证：`ui_driver.settlement_screen(fleet_id)`。
   - 仍有页面 → 说明这次 Drain 没清掉这条链 → **拒绝**（`still shows ...`），零点击，交给下一次 Drain。
   - 没有页面 → 记 `settlement_screens_gone_after_global_drain`，并把 `returned` 置为 `None`，走既有的"无奖励页即已完成"路径。
   - 驱动没有该探针 → **拒绝**，不允许"重跑一遍奖励链"。
2. **把舰队级屏障换成账本归属**：`other_returns` 非空时，
   - `pages_drained_globally=False` → 旧错误（`cannot be attributed to one fleet`）；
   - `True` 且**无** `dispatch_registry` → 新错误（`no dispatch ledger is wired to attribute the result`）；
   - `True` 且有账本 → 记 `settlement_pages_drained_globally`，继续走 P0-3 的 `attribute_dispatch`（零匹配/多匹配仍然零点击拒绝）。

即：**屏障没有被删除，是被更窄的条件替换**——页面歧义由 Drain 消除，结果归属由账本回答。

## 6. 桥接：唯一一处屏幕名 ↔ 页面枚举的映射

`expeditions/settlement_drain_ui.py`，两个类（都只要求鸭子类型，不 import 驱动）：

| 驱动方法（`CdpContinuousExpeditionUiDriver` 新增） | 语义 |
|---|---|
| `probe_settlement_page() -> str \| None` | 哪个奖励页在屏上（只读，一次有界探针/页） |
| `probe_home_port() -> bool` | 是否稳定母港（只读） |
| `advance_settlement_page(screen)` | 该页接受的**唯一**输入，然后返回（不等待、不重试） |

映射表 `SCREEN_TO_PAGE`：`HOME_PORT_RETURN_NOTICE → RETURN_NOTICE`、`RETURN_RESULT → EXPEDITION_RESULT`、`BONUS_ITEMS → REWARD_PANEL`。

- 未映射的屏幕名 → `DrainPageState.UNKNOWN`（**不做最佳猜测**）。
- 无奖励页 + 无母港 → `UNKNOWN`。
- `advance()` 收到 `HOME_PORT` / 对话框 / `UNKNOWN` → `ValueError`（Drain 契约里不可推进的页面被当成编程错误）。
- `fleet_id` 从头到尾不出现：这三个方法都不接受舰队参数，这是本阶段的要点。

`advance_settlement_page` 的 `_click(..., step=0)` 是刻意的：`step` 1/2/3 属于逐舰队结算链，0 表示"Drain 发起的输入"，两者不混。

## 7. 生产开关

`tools/one_expedition_ui_loop.py`：`--global-drain`（默认**关**）+ 四个预算参数（`--drain-max-actions` 8、`--drain-timeout-seconds` 45.0、`--drain-action-interval-seconds` 2.5、`--drain-stable-frame-count` 2）。

- 驱动缺少三个方法时发 `global_drain_unavailable`（含 `missing_methods`），回落到逐舰队路径，不抛异常。
- 没有结算控制器时同样回落（无物可排水）。
- 接线成功发 `global_drain_wired`（含四个预算、`passes_fleet_id=False`）；`production_supervisor_started` 增加 `global_drain_wired` 字段。
- 判定用 `getattr(args, "global_drain", False) is True`：测试替身里非布尔的真值属性**不得**悄悄打开一条会发 UI 输入的路径。

## 8. 故障注入矩阵

| 失败方向 | 期望 | 覆盖它的测试 |
|---|---|---|
| 两个舰队同时返航 | 一次 Drain，两队都结清 | `test_one_drain_settles_both_returned_fleets_and_frees_the_ready_one` |
| 返航队阻塞 READY 队 | READY 队同 tick 被派遣 | 同上（`dispatch.calls == [2,3,4]`） |
| Drain 卡住 | 零结算/零补给/零派遣 + `settlement_drain_stopped:ui_stuck` | `test_a_drain_that_stops_early_clicks_nothing_else` |
| 无结算控制器 | Drain 不运行 | `test_without_a_usable_settlement_controller_the_drain_is_not_run` |
| Drain 无预算 | 构造即 `ValueError` | `test_a_drain_session_requires_an_explicit_request` |
| 只有预算没有会话 | 构造即 `ValueError` | `test_a_request_without_a_session_is_refused` |
| 页面仍在屏上 | 拒绝，零点击 | `test_a_chain_still_on_screen_is_a_stop_not_a_second_run` |
| 驱动无法自证页面消失 | 拒绝，零点击 | `test_a_driver_that_cannot_prove_absence_is_refused` |
| 无账本仍要跨队结算 | 拒绝（屏障未删） | `test_without_a_ledger_the_old_refusal_stands` |
| 未带新参数 | 旧屏障行为不变 | `test_without_the_flag_the_fleet_barrier_is_unchanged` |
| 未映射屏幕 / 母港要推进 | `UNKNOWN` / `ValueError` | `test_settlement_drain_ui_bridge` |
| 无 Drain 的旧路径 | 逐 tick 一队，标签不变 | `test_without_a_drain_session_two_returns_still_wait_their_turn` |

## 9. 限制

1. **从未在真实客户端启用过**。`--global-drain` 默认关；本阶段所有"通过"都是离线替身。真机上"点击奖励页会让客户端发 `api_port/port`"这个前提**未验证**，而它决定结清后 Poi 何时读回空闲。
2. **桥接只在单元层面测过映射**；`probe_settlement_page` / `probe_home_port` 依赖既有指纹探针，未在真实帧上标定"母港 vs 未知页"的边界。
3. **未验证一次真实的多队同时返航**（用户明确禁止为测试重复派遣 Fleet 4）。
4. Drain 的 `action_interval_seconds=2.5` 是估计值：既有的 `REWARD_SETTLE_SECONDS=1.6` 是单帧等待，页面转场更慢时会得到 `UI_STUCK` 而不是少点，方向是安全的。
5. 第三阶段（统一日志生产接线：`action_id`、结算/补给生命周期关联、`one_expedition_ui_loop.jsonl` 无上限）**未开始**。
