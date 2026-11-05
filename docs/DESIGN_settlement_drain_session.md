# P0.1 — SettlementDrainSession 接口与离线 Mock

状态：**接口 + 纯离线 Mock 测试完成；未实机验证，未接入 Supervisor。**
模块：`src/kancollebot/expeditions/settlement_drain.py`
测试：`tests/test_settlement_drain.py`（30 条）

---

## 1. 目标与边界

全局结算 Drain 只做一件事：**根据当前可见 UI 页面推进可识别的结算流程，直到页面回到母港或出现必须停止的页面。**

它明确不做的事：

- 不接收 `fleet_id`，不做结果归属，不做舰队状态对账；
- 不因其他舰队仍待结算（Poi `status == 2`）而阻断 UI Drain；
- 不为未知页面设计兜底盲点动作；
- 不构造、发送或重放任何游戏 API 请求；
- 不修改舰队编成，不触发派遣。

本阶段（P0.1）只交付接口、枚举、参数校验与 Mock 测试。生产 `continuous_settlement_controller.py`、`continuous_supervisor.py`、`mission_result_witness.py` 与旧的 `other_returns` 守卫**一行未改**，留待 P0.2。

---

## 2. 接口契约

### 2.1 枚举

| `DrainPageState` | 值 | 含义 |
|---|---|---|
| `HOME_PORT` | `home_port` | 稳定母港，Drain 正常终点 |
| `RETURN_NOTICE` | `return_notice` | 返航通知页（结算页，**不是**母港、**不是**未知页） |
| `EXPEDITION_RESULT` | `expedition_result` | 远征结果页 |
| `REWARD_PANEL` | `reward_panel` | 奖励页 |
| `NETWORK_ERROR_POPUP` | `network_error_popup` | 通信错误弹窗 |
| `UNKNOWN_DIALOG` | `unknown_dialog` | 无法识别的弹窗 |
| `UNKNOWN` | `unknown` | 无法识别的页面 |

| `DrainErrorType` | 值 |
|---|---|
| `MAX_ACTIONS_REACHED` | `max_actions` |
| `TIMEOUT` | `timeout` |
| `UI_STUCK` | `ui_stuck` |
| `UNKNOWN_DIALOG` | `unknown_dialog` |
| `NETWORK_ERROR` | `network_error` |
| `UNKNOWN_PAGE` | `unknown_page` |

### 2.2 `SettlementDrainRequest`（frozen）

| 字段 | 校验 |
|---|---|
| `max_actions: int` | 必须是 `int`（拒绝 `bool`）、必须 > 0 |
| `timeout_seconds: float` | 必须有限、必须 > 0 |
| `action_interval_seconds: float` | 必须有限、必须 > 0 |
| `stable_frame_count: int` | 必须是 `int`（拒绝 `bool`）、必须 > 0 |

`__post_init__` 与 `validate()` 都做同一套校验，因此 `drain()` 对任何路径传入的 request 都会再校验一次。

`action_interval_seconds` 要求 > 0 而不只是非负：它是**单次动作后的有界观察窗口**，长度为 0 的窗口永远无法确认一次页面转换，只会让"时间到了"冒充"页面变了"。

### 2.3 `DrainResult`（frozen）

| 字段 | 语义 |
|---|---|
| `reached_home` | 仅在母港被**稳定识别**后为 `True` |
| `completed_pages` | 已成功推进过去的结算页数量；母港不计 |
| `actions_performed` | driver 实际派发的 UI 动作数 |
| `final_page` | 结束时所处的页面 |
| `error_encountered` | 正常回到母港时为 `None` |
| `requires_manual_intervention` | 是否需要人工介入（由 error 派生） |

结构不变量（构造即校验，防止误报成功）：

- `reached_home=True` ⇒ `error_encountered is None` 且 `final_page is HOME_PORT`；
- `reached_home=False` ⇒ 必须带一个 error（不允许"静默失败"）；
- 两个计数不得为负。

### 2.4 协作端口

```python
class DrainPageObserver(Protocol):
    def observe(self) -> DrainPageState: ...

class DrainUiDriver(Protocol):
    def advance(self, page: DrainPageState) -> None: ...

class DrainClock(Protocol):
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...
```

`advance()` 的契约：只派发该页面唯一被允许的那一次输入，**派发后立即返回**，不等待页面变化、不重试、不接触游戏 API、不接受 `fleet_id`。

### 2.5 `SettlementDrainSession`

```python
SettlementDrainSession(
    observer, driver, *, clock=None,
    frame_poll_interval_seconds=DEFAULT_FRAME_POLL_INTERVAL_SECONDS,  # 0.05
).drain(request) -> DrainResult
```

采样间隔不放进 request，是为了让公开的 request 契约严格保持规格里的四个参数；它是实现细节，可注入以便测试。

---

## 3. 页面推进规则

| 当前页面 | 动作 | 成功条件 | 异常处理 |
|---|---|---|---|
| `HOME_PORT` | 不操作 | Drain 正常结束 | — |
| `RETURN_NOTICE` | 一次推进动作 | 出现不同的稳定已知页 | 窗口内无变化 → `UI_STUCK` |
| `EXPEDITION_RESULT` | 一次推进动作 | 出现不同的稳定已知页 | 同上 |
| `REWARD_PANEL` | 一次推进动作 | 出现不同的稳定已知页 | 同上 |
| `NETWORK_ERROR_POPUP` | **不点击** | — | `NETWORK_ERROR` |
| `UNKNOWN_DIALOG` | **不点击** | — | `UNKNOWN_DIALOG` |
| `UNKNOWN` | **不点击** | — | `UNKNOWN_PAGE` |

未知页面没有兜底动作：`advance()` 永远不会收到非推进页。

---

## 4. 判定顺序

每一轮循环固定按此顺序判定，顺序本身是安全语义：

1. 终态错误页 → 立即返回对应 error（**先于**预算与超时判断，避免把"识别到弹窗"误报成"预算耗尽"）；
2. `HOME_PORT` → `reached_home=True`；
3. `actions_performed >= max_actions` → `MAX_ACTIONS_REACHED`；
4. 全局超时到期 → `TIMEOUT`；
5. 否则执行一次动作，然后进入转换等待。

转换等待分两段：

- **第一段**：等待"页面不再是刚才那一页"，上限为 `action_interval_seconds`；到期仍未变 → `UI_STUCK`。
- **第二段**：一旦页面变了，改为等待新页面达到 `stable_frame_count` 连续帧，上限是**整场 timeout**（不再是 interval）。这样慢动画不会被误判成卡死。

全局超时在每一轮采样前检查，优先于 `UI_STUCK`：**整场预算耗尽就是 `TIMEOUT`**，不会因为单页卡住而改判。

---

## 5. 关键语义决定

1. **`completed_pages` 只在推进到"已知页面"时 +1**，包括母港与 `RETURN_NOTICE`；推进到错误弹窗或未知页不算完成。母港本身永不计数。
2. **`requires_manual_intervention`**：`NETWORK_ERROR` / `UNKNOWN_DIALOG` / `UNKNOWN_PAGE` / `UI_STUCK` / `TIMEOUT` 为 `True`；`MAX_ACTIONS_REACHED` 为 `False`——它是**有意的预算停止**，页面仍可识别，调用方可以用新预算重新 Drain。
3. **页面弹回原页**：点击后页面变了一帧又回到刚点过的页面并稳定 → `UI_STUCK`，**不会再点第二次**。这条保证是结构性的：`_wait_for_change()` 永不返回"刚操作过的那一页"。
4. **driver / observer 抛异常不转成 `DrainResult`**：会话无法知道点击是否已经落地，因此原样向上抛，且**不再尝试任何动作**。把未知的点击结果包装成"可恢复的结果"会掩盖故障。
5. **`reached_home=True` 不代表对账完成**：它只说明 UI Drain 阶段结束，舰队状态仍需在 P0.2 重新读取 Poi 后对账。

---

## 6. Mock 测试矩阵

测试全部离线：`FakeDrainClock`（只有 `sleep` 推进时间）+ `TimelineSettlementUi`（绝对时间线页面假体，同时充当 observer 与 driver，**点击非当前页或非推进页会直接失败**）。

| 规格用例 | 测试 | 断言要点 |
|---|---|---|
| Test 1 正常返回母港 | `test_1_normal_return_to_home` | `reached_home`、`completed_pages=2`、`final_page=HOME`、`error=None`；访问 3 页但只计 2 |
| Test 2 最大动作数 | `test_2_action_budget_stops_before_home` | `MAX_ACTIONS_REACHED`、`actions_performed=1`、**实际点击 1 次**、`requires_manual=False` |
| 预算恰好耗尽 | `test_budget_exhausted_exactly_is_still_success` | `max_actions=2` 恰好够用 → 仍 `reached_home` |
| Test 3 网络弹窗 | `test_3_network_error_popup_stops_without_clicking` | `final_page=NETWORK`、`NETWORK_ERROR`、人工介入、只点过结果页 |
| Test 4 动画延迟 | `test_4_animation_delay_is_waited_out_without_a_second_click` | 不提前 `UI_STUCK`、点击序列无重复、点击后至少 2 帧仍见原页 |
| Test 4 对照 卡死 | `test_4_control_no_change_within_bound_is_ui_stuck` | `UI_STUCK`、只点 1 次 |
| Test 5 多舰队待结算 | `test_5_pending_other_fleets_do_not_block_the_drain` | 两支 `status==2` 时仍完成；**Poi 读取次数为 0** |
| 接口无 fleet 身份 | `test_request_has_no_fleet_identity` / `test_drain_takes_only_the_request` | 字段与签名锁定，防止后续偷偷加 `fleet_id` |
| 初始已是母港 | `test_initial_home_port_needs_no_action` | 0 动作、0 完成页 |
| 初始未知页 / 未知弹窗 | `test_initial_unknown_page_is_reported_and_not_clicked` 等 | 明确 error、零点击 |
| 返航通知页 | `test_return_notice_is_a_settlement_page` | 计入 `completed_pages`（3 页） |
| 参数非法 | `test_invalid_request_parameters_are_rejected` | 16 组 subTest（含 `bool`、`inf`、`nan`、0、负数） |
| 执行中超时 | `test_whole_drain_timeout_wins_over_stuck` | 整场超时优先于 `UI_STUCK` |
| 初始稳定阶段超时 | `test_timeout_during_initial_stabilisation` | `TIMEOUT`、0 动作 |
| 页面识别不稳定 | `test_never_stable_page_times_out` | 逐帧翻转永不稳定 → `TIMEOUT`、零点击 |
| 点击动作异常 | `test_driver_failure_propagates_and_is_never_retried` | 异常上抛、只尝试 1 次 |
| 弹回原页 | `test_page_bouncing_back_is_stuck_not_clicked_twice` | `UI_STUCK`、只点 1 次 |
| `DrainResult` 不变量 | `DrainResultContractTests`（5 条） | 禁止"母港 + error"、禁止静默失败、枚举值锁定 |
| 构造器校验 | `test_session_rejects_*`（3 条） | 缺 `observe`/`advance`、非法采样间隔 |

合计 **30 条**，全部离线、确定性、无真实等待（`Ran 30 tests in 0.001s`）。

---

## 7. 未覆盖与限制（LIMITS）

1. **完全没有实机验证**：没有启动 Poi/CDP，没有点击真实 UI，没有派遣。Mock 通过 **不等于** 实机通过。
2. **未接入生产**：Supervisor、`ContinuousSettlementController`、Witness 未改；`other_returns` 守卫仍在原位。当前代码库运行时行为与本轮之前完全一致。
3. **页面识别器未实现**：`DrainPageObserver` 只是协议；真实实现（把可见 UI 映射到 7 种页面）属于后续阶段，需另做视觉/语义识别与稳定性验证。
4. **没有 `fleet_id` 就没有归属**：本模块故意不解决"这次结算属于哪支舰队"，P0.2 必须靠重新读取 Poi + 账本对账解决。
5. **`MAX_ACTIONS_REACHED` 之后 UI 处于流程中间**：调用方是否立刻重开一轮，需要 P0.2 的策略决定；本阶段只保证不超预算点击、不重复点击。

---

## 8. P0.2 接入点

1. Supervisor 改为 Drain 驱动：`drain(request)` 取代现有"逐舰队推进奖励页"的入口；
2. UI Drain 与舰队状态账本解耦：Drain 结束后重新读取 Poi，再逐舰队对账；
3. 保留未完成派遣的舰队级安全屏障（旧 `other_returns` 逻辑迁移，而不是直接删除）；
4. 最后再处理 Witness 的事件级身份与 `dispatch_id` 关联。
