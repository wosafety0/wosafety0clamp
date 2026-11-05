# 生产远征闭环第一阶段（P0-1～P0-4）— 派遣身份账本、结果归属与返航刷新

状态：**代码 + 离线测试完成；Fleet 4 生产补给与真实派遣身份链均未在真客户端复验。**
模块：`expeditions/dispatch_registry.py`、`expeditions/dispatch_attribution.py`、`core/return_refresh.py`、以及四个控制器的接线改动。
测试：`tests/test_dispatch_registry.py`(13)、`tests/test_dispatch_attribution.py`(12)、`tests/test_settlement_dispatch_attribution.py`(8)、`tests/test_return_refresh.py`(15)、`tests/test_supply_tab_classification.py`(10)，另修改 7 个既有测试文件。

---

## 1. 问题：四条记录，四个问题，缺的是第四个

Fleet 4 的实机闭环跑通之后，单舰队的派遣与结算是通的；但把它接进生产控制器时暴露了一个结构性缺口——**没有任何durable记录能回答"这次返航属于哪一次派遣"**。项目里已有的三份记录各自回答别的问题：

| 记录 | 表 | 回答的问题 | 为什么不能做归属 |
|---|---|---|---|
| `ExpeditionActionJournal` | `expedition_actions` | 该舰队**能否再点**（at-most-once 安全屏障） | 没有舰船集合、没有任务时间、没有派遣身份 |
| `SqliteDispatchEventLog` | `dispatch_events` | 发生过什么（append-only 诊断事件流） | JSON payload 里的共享日志；重建身份需要"确认行 + 稍后的母港行"同时在场，手跑的单发派遣永远不产生这一对 |
| `SqliteDispatchIntentStore` | `dispatch_intents` | 待确认的意图（每队一行） | Poi 确认的那一刻**即删除**，确认后再问"派了哪几艘"已经无从回答 |

因此 P0-2/P0-3 的缺口不是"日志不够详细"，而是**缺少一个以派遣实例为粒度、在确认之后仍然存在的身份行**。

## 2. 第四层：`dispatch_identities`

新表建在**现有 dispatch 数据库文件的同一个 SQLite 里**，不是第二个数据库，也不是第二套安全账本：

```sql
CREATE TABLE IF NOT EXISTS dispatch_identities (
    dispatch_id TEXT PRIMARY KEY,
    fleet_id INTEGER NOT NULL,
    mission_id INTEGER NOT NULL,
    ship_ids_json TEXT NOT NULL,
    quest_name TEXT, task_kind TEXT, source TEXT NOT NULL,
    intent_persisted_at TEXT NOT NULL,
    action_completed_at TEXT, confirmed_at TEXT, final_time_ms INTEGER,
    status TEXT NOT NULL, settled_at TEXT,
    detail TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dispatch_identities_fleet_status
    ON dispatch_identities (fleet_id, status);
```

约束与边界：

- **加表不改表**。`dispatch_intents` / `dispatch_events` / `expedition_actions` 的 schema 与语义一行未动；旧版本写的库在首次使用时自动获得这张表。
- **不能授权也不能禁止点击**。这张表里没有任何字段被安全门禁读取；"能否再点"仍然只由 Action Journal 回答。这是一条刻意的单向依赖，防止身份账本退化成第二套安全屏障。
- 状态机只能单向推进：`pending → confirmed → settled`，以及 `pending → unknown`。`confirmed` 不可被降级为 `unknown`（"点击可能落地了"和"服务器接受了"是两个不同的事实），`settled` 不可再变。

## 3. `dispatch_id` 是"尝试"的身份，不是"结果"的身份

- 由 `observability.event_envelope.new_dispatch_id()` 生成，格式 `dispatch-YYYYMMDDTHHMMSSZ-<hex12>`。
- `validate_dispatch_id()` 只做**形状校验**：拒绝空串、数据库行号、任务名，以及任何 emitter 自造的字符串。账本入口与控制器入口都校验一次，所以 **payload 无法把伪造身份走私进库**（离线实测：`"fleet-3-attempt-1"` 被拒绝并给出理由）。
- `DispatchIdentity.identity_key()` **包含 `dispatch_id`**。同一舰队、同一任务、同一编成的两次派遣必须是两行——一旦合并，"这次返航属于哪一次"这个问题就永久失去答案。
- 语义铁律（与 P0.2b 一致）：**`dispatch_id` 存在 ≠ 发生过派遣**。只有 `dispatch_intent_persisted` + `dispatch_confirmed_by_poi` 才证明一次派遣。

## 4. 归属：唯一匹配，两个方向都拒绝

`expeditions/dispatch_attribution.py` 是纯函数，不点击、不写库、不看屏幕：

```python
attribute_dispatch(*, fleet_id, mission_id, ship_ids, records, observed_at=None)
    -> AttributionVerdict(allowed, reason, dispatch_id, record, candidate_count, ...)
```

匹配条件全部为**合取**：同舰队 + 同任务 id + 同舰船**集合**（顺序无关）+ `status == "confirmed"` + 未 settled + `confirmed_at <= observed_at`（后者防止未来时间戳的行被优先选中）。

- 零匹配 → `UNATTRIBUTED`。
- 多匹配 → 同样 `UNATTRIBUTED`，并带上 `candidate_count` 与全部候选 id。
- 观测到的舰船集合为空 → 直接拒绝（"没有任何编成证据"不等于"匹配任何东西"）。

两个方向用同一个原因码，是因为**猜错和猜不到是同一种错误**；差异只体现在 `candidate_count` 与 `considered_dispatch_ids` 这两个诊断字段里。

## 5. 接线

### 5.1 派遣侧（P0-2）

- `ContinuousDispatchController.dispatch_one_ready(fleet_id, *, dispatch_id=None)`：id 在**第一次观测之前**生成或校验；`_persist_dispatch_identity()` 在任何输入之前落库；点击已发出但结果未证实时 `_mark_dispatch_outcome_unknown()`。
- `ContinuousExpeditionSupervisor._dispatch_with_identity()` 先 mint 再传：`new_dispatch_id()` → `correlation_scope(..., dispatch_id=...)` → `dispatch_one_ready(..., dispatch_id=...)`。**日志里的 id 与库里的 id 是同一个**。
- `tools/live_dispatch_expedition.py`（单发路径，绕过 supervisor）本轮补上同样的账本：`--commit` 前 `record_intent`，确认后 `record_confirmed`，异常/未确认 `record_unknown`；并在 PREFLIGHT 之后拒绝开路——`unsettled_dispatch_ids()` 非空即 `DISPATCH_BLOCKED_PRIOR_UNSETTLED`、return 3。理由：一条未结清的身份行意味着"上一次派遣还没结算完"，此时再发一次会让两个实例无法区分。
- **两条路径的差异是结构性的，不是疏漏**：supervisor 每轮迭代 = 一次尝试，天然有唯一语义边界；单发工具没有外层循环，所以它自带一道"先结清再发"的闸门。

### 5.2 结算侧（P0-3）

`ContinuousSettlementController` 新增 `dispatch_registry`（可选）：

- `settle_one_returned()` 在**任何 UI 调用之前**调 `_attribute_returned()`；不允许则发 `settlement_unattributed`（含 `dispatch_clicks_sent: 0`、`candidate_count`、`considered_dispatch_ids`）并抛 `ContinuousSettlementError`。两条返回路径都调 `_close_dispatch_record()` 把实例标为 `settled`，于是**同一次返航不会被第二次归属**。
- `settle_visible_reward_chain()` 在 `expected_ship_ids is None` 且账本在场时用 `_expected_ship_ids_from_registry()` 解析编成；解析失败则 `_refuse_startup(..., failed_check="dispatch_registry_attribution")` 并抛 `StartupSettlementRefused`，成功则发 `settlement_composition_from_registry`。这条正是 Fleet 4 实机"奖励页在场时拿不到 fresh witness"的缺口补丁：编成不再需要调用方凭空传入。
- 归因读库失败（含库文件打不开）→ `ContinuousSettlementError("durable dispatch ledger is unreadable ...")`，**不是**回退到屏幕证据。`SqliteDispatchRegistry._connect` 把所有 `sqlite3.Error` 包成 `DispatchRegistryError`（`IntegrityError` 例外，留给 `record_intent` 转成更具体的"重复记录"）。

### 5.3 返航五态与 generation token（P0-4）

`core/return_refresh.py`（放在 `core/` 是因为 `adapters` 不得 import `expeditions`，见 `tests/test_import_direction_guard.py`）：

| 状态 | 含义 | 能否驱动下游 |
|---|---|---|
| `expected_return` | 仅计时值 | 否 |
| `state_before` | 刷新前的观测 | 否 |
| `refresh_action` | 实际发出的那**一次**可见导航 | — |
| `state_after` | 严格晚于动作时间的观测 | 否 |
| `confirmed_return` | 唯一可被下游动作的状态 | **是** |

规则：

- `begin_watch()` 单调递增 `generation`；点击前与点击后各一次 `is_current()` 复检；`invalidate(reason)` 在超时/取消/关停时让所有在飞轮次失效。
- 只对**新鲜**的 status-2 观测认返航（`_trusted(before)`），否则必须靠一次导航刷新；导航**从不重试**，计时器到期**从不**当作返航。
- 后置观测只有在 `after_at > action_started_at` 时才被接受为"刷新后的证据"。
- 冷却（`min_refresh_interval_seconds`）、网络异常、页面 UNKNOWN 都结束本轮且不宣称返航。
- `FreshnessRefreshingStateReader` 侧：新增 `generation` / `invalidate()` / `confirm_return()`；`ensure_fresh()` 在进入刷新锁后比对 `started_generation`，不一致则发 `stale_generation_suppressed` 并**返回未经刷新的观测**（让调用方自己的 `require_fresh` 继续 fail-closed），不发点击。

### 5.4 Fleet 4 补给校准（P0-1）

三件互不替代的事实，分别用三个概念表达，不合并：

| 概念 | 位置 | 值 | 含义 |
|---|---|---|---|
| 实机已验证 | `resupply_verified_fleet_ids` | `(2, 3)` | 有指纹模板 + 实机点击过 |
| 代码结构已校准 | `resupply_structurally_calibrated_fleet_ids` | `(2, 3, 4)` | 页签 region 有界、定位器接受该 index、能证明选中了哪个页签 |
| 可用集合 | `resupply_enabled_fleet_ids` | 两者并集 | 控制器实际门禁 |

导入期断言：`verified ⊆ calibrated ⊆ dispatchable`。日志侧 `resupply_action_started` 带 `calibration_level`（`live_verified` / `structural_only`）与 `fingerprint_template_available`，`structural_only` 的补给每次都会在日志里显式标注——**没有模板就说没有模板**。

Fleet 4 无 `SUPPLY_FLEET_4_SELECTED` 模板，替代方案是**无模板的相对亮度判定**：

- region 由 45 px 等距的页签条几何推导：`SUPPLY_FLEET_TAB_REGIONS[4] = (330, 155, 50, 50)`（195/240/285 → 330）。
- `classify_supply_tab_selection()` 对四个 region 取 Rec.601 平均亮度，要求"最亮者 ≥ 90.0"且"比次亮者亮 ≥ 12.0"，否则返回 `UNKNOWN` + 全部分数。
- 只在请求舰队属于 `TEMPLATE_FREE_SUPPLY_FLEETS = (4,)` 时启用；**Fleet 2/3 的模板路径逐字节未动**。
- 它证明"页签条里恰好一个被画成选中样式，且是请求的那个"；它不证明"这个客户端的选中样式真的长成阈值假设的样子"。所以阈值刻意偏严、宁可 UNKNOWN，并由动作后的 Poi 逐字段交叉核对兜底。

## 6. 故障注入矩阵

| 失败方向 | 期望 | 覆盖它的测试 |
|---|---|---|
| 账本里零匹配 | 零点击 + `UNATTRIBUTED` | `test_no_dispatch_record_means_zero_clicks` |
| 账本里两个同构实例 | 零点击 + `candidate_count == 2` | `test_two_matching_dispatches_mean_zero_clicks` |
| 舰船集合不符 | 零点击 | `test_a_record_with_another_ship_set_means_zero_clicks` |
| 库文件不可打开 | 拒绝，不回退屏幕证据 | `test_an_unreadable_ledger_is_a_refusal_not_a_guess` |
| 伪造 dispatch_id | 入口即拒 | `test_a_forged_identifier_is_refused` |
| 同构两次派遣 | 两行、不合并 | `test_two_identical_dispatches_are_two_rows_and_never_merge` |
| confirmed 被要求降级 | 抛错 | `test_a_confirmed_dispatch_cannot_be_downgraded_to_unknown` |
| 未确认就结算 | 抛错 | `test_an_unconfirmed_outcome_cannot_be_settled` |
| 结算后重复归属 | 第二次零点击 | `test_a_single_matching_dispatch_lets_the_chain_run`（断言 `settled`） |
| 过期 generation 点击 | 拒绝，无 UI 调用 | `test_invalidating_before_a_refresh_stops_the_click` |
| 计时到期但未刷新 | 不宣称返航 | `test_the_timer_alone_never_confirms_a_return` |
| 亮度差不足 | `UNKNOWN`，不发点击 | `test_a_marginal_difference_is_unknown` |
| Fleet 2/3 模板路径 | 事件序不变 | `test_continuous_expedition_ui_resupply` 既有用例 |

## 7. 限制（明确未验证的部分）

1. **Fleet 4 生产补给未在真客户端复验**。region 是几何推导、判定是无模板亮度，两者都没有在实机上跑过生产路径；`calibration_level="structural_only"` 就是这个事实的机器可读形式。**不得**把本轮的离线通过当作实机验收。
2. **真实派遣身份链未在真客户端验证**。`dispatch_intent_persisted` / `dispatch_confirmed_by_poi` / `record_confirmed` 的落库顺序与"每队每轮一行"只在离线替身上验证过。
3. **未跑一次完整派遣循环**。用户明确禁止为测试重复派遣 Fleet 4，本轮零实机点击。
4. `live_dispatch_expedition.py` 的"先结清再发"闸门会让**历史遗留的 open 行永久挡住单发工具**，需要人工核账（这是设计而非缺陷：无法区分就不发）。
5. 返航刷新的 `witness_wait_seconds` 只是"等自然 `api_port/port`"的等待，不产生任何 API 请求；若客户端始终不发（例如空白页故障），本轮结束且不宣称返航。
6. 多舰队全局 Drain（P0.2 第二阶段）与统一日志接线（第三阶段）本轮**未开始**。
