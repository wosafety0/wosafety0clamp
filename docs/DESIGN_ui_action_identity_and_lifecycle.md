# 第三阶段 — 动作身份、生命周期关联与旧日志上限

状态：**代码 + 离线测试完成；`--run-log-dir` 仍默认关闭，未在任何真实客户端上跑过本阶段改动。**
模块：`adapters/poi/continuous_expedition_ui.py`（唯一输入边界）、`expeditions/continuous_settlement_controller.py`、`expeditions/continuous_resupply_controller.py`（生命周期 + 审计门禁）、`runtime_config.py`（日志上限）、`tools/one_expedition_ui_loop.py`（轮转 + 门禁接线）。
测试：`tests/test_ui_action_identity.py`(8)、`tests/test_ui_loop_log_rotation.py`(9)。

本阶段的全部改动都来自 P0.2c 只读审计（`docs/research/P0.2c_CORRELATION_SCOPE_AUDIT.md`）的四条结论，且**只做那四条**：审计报告里"建议顺序 ①`_record_decision` 覆盖异常路径、②`run_started` 记 `correlation_capable`"属于日志基础设施的另一条线，本轮**未动**。

---

## 1. 三个互不相同的身份

项目里已有三种相似但不可互换的字符串。混用其中任意两个，都会让日志"看起来能追溯、实际追溯错"。

| 身份 | 生成处 | 作用域 | 回答的问题 | 不可用于 |
| --- | --- | --- | --- | --- |
| `dispatch_id` | `supervisor._dispatch_with_identity()`（每队每轮一次，门禁之前） | **整个派遣尝试**（含被拒绝的尝试） | "这一次派遣尝试" | 结算、补给、单次点击 |
| `action_id` | `continuous_expedition_ui._click()`（每次输入一次） | **一次已派发输入**（`attempted` / `ambiguous` / `sent`） | "这一次点击" | 归属、去重、账本主键 |
| `correlation_id` | `new_correlation_id()`（每次结算/补给运行一次） | **一个业务生命周期**（一次结算链、一次补给） | "这一轮结算/补给" | 派遣归属 |

`new_battle_id()` 已存在但本轮**零使用**：战斗只有预留字段，没有任何事件写入（见第 6 节）。

`_click` 之所以是 `action_id` 的**唯一**生成处，是因为它是驱动里唯一真正发出输入的地方。`step` 是历史遗留的重试序号（同一个补给会用到 2/3/4），**明确不当身份**：一次派遣含多次点击，若用 `step` 当身份，两次不同派遣的同一 `step` 会撞成同一身份。

## 2. `_click`：唯一输入边界与 fail-closed 顺序

```python
with correlation_scope(self._event_sink, action_id=new_action_id()):
    self._event_sink("continuous_ui_click_attempted", {...})   # 先写
    try:
        self._input.click(point)                               # 后点
    except Exception:
        self._event_sink("continuous_ui_click_ambiguous", {...})
        raise
    self._event_sink("continuous_ui_click_sent", {...})
    if self._settle_seconds:
        self._sleep(self._settle_seconds)
```

三条性质：

1. **`attempted` 必须先落地**。sink 抛错时点击不会被发出——"记不住的点击不许发"是顺序性质，不是日志细节。`test_a_failed_click_is_ambiguous_and_never_sent` 与 `test_settlement_sends_nothing_when_the_audit_log_is_unwritable` 分别从两侧钉住这一点。
2. **`ambiguous` 取代 `sent`**。输入通道抛异常时，"点没点上"未知，日志必须承认未知，不能补一行 `sent` 假装成功。
3. **作用域包住等待**。`_settle_seconds` 在作用域内，因此等待期间由该点击引发的行也继承同一 `action_id`。

本轮把**补给的两处裸点击**收进这条边界（P0.2c 结论 4 的具体缺陷）：

| 位置 | 之前 | 现在 |
| --- | --- | --- |
| 补给舰队页签 | `self._input.click(...)` → 记 `continuous_ui_supply_tab_click_sent` → 睡 `settle_seconds`（**先点后记**，无 `attempted`、异常无 `ambiguous`） | `_click(..., action="select_supply_fleet_tab", step=2)` |
| 补给按钮 | `self._input.click(...)` → 记 `continuous_ui_supply_click_sent` | `_click(..., action="click_fleet_supply_all", step=3)` |
| 回母港 | `_click(..., step=3)` | `_click(..., step=4)`（避让补给按钮占用的 3） |

`continuous_ui_supply_tab_click_sent` / `continuous_ui_supply_click_sent` 两个事件名**原样保留**（下游读者、既有断言、实机证据对照都靠它）。它们现在与 `continuous_ui_click_sent` 并存：后者是通用输入行，前者是补给语义行，都在同一 `action_id` 下。

**已知副作用（刻意接受）:** 补给按钮路径现在先等 `settle_seconds` 再等 `SUPPLY_SETTLE_SECONDS`，比之前多一个 settle 间隔（默认各 1.0 s）。选它是因为"每个输入都一样"比"省一秒"更重要；要省这一秒必须给 `_click` 加跳过标志，那会把唯一的边界重新变成两条路径。

`getattr(args, "global_drain", False) is True` 这类"非布尔真值不算打开"的写法同理适用于本阶段：**没有 `event_sink` 就不 mint 身份**（`correlation_scope` 在 sink 不支持关联时退化为空作用域，行照常发出、`action_id` 留空），因此测试替身不会被强行赋予身份，也不会因为缺少身份而改变派遣行为。

## 3. 结算与补给：独立生命周期，不借 `dispatch_id`

两者都在既有的 `ui_arbiter.operation(...)` 边界内，与 `_dispatch_with_identity()` 同形，因此不需要新概念：

```python
lifecycle_id = new_correlation_id()
with self.ui_arbiter.operation(f"continuous-settlement-fleet-{fleet_id}"), \
        correlation_scope(self.event_sink, correlation_id=lifecycle_id):
    self.event_sink("settlement_started", {
        "fleet_id": fleet_id,
        "lifecycle_kind": "settlement",
        "pages_drained_globally": bool(pages_drained_globally),
        "game_api_requests": False,
    })
```

- **为什么不挂 `dispatch_id`:** 一次返航可以在**从未见过其派遣**的进程里被结算（进程重启、账本先于日志），把 `dispatch_id` 写进结算行等于宣称一个不存在的因果。两者之间**真正的持久链接是 dispatch 账本**（P0-2），不是日志字段。
- **`settlement_started` 是本轮新增的唯一事件名**。之前 happy path 没有起点行，结算链只能从 `settlement_blocked` 或中间的 UI 行反推。它带 `pages_drained_globally` 与 `game_api_requests: False`，让"这次结算是全局 Drain 之后"与"全程零游戏请求"在日志里可自证。
- **补给同理:** `resupply_action_started` 增加 `lifecycle_kind="resupply"`，与已有的 `calibration_level` / `fingerprint_template_available` 并列。补给发生在**该次派遣的奖励链已经关闭之后**，借 `dispatch_id` 会把两个生命周期焊死。
- 信封 12 字段未改，`correlation_id` 是既有字段，`lifecycle_kind` 是诊断字段。

## 4. 审计门禁：写不下去就不动作

两个控制器新增可选 `audit_gate: Callable[[], None]`，在**第一次读取/观测之前**调用（结算在 `settle_one_returned` 开头，补给在 `ui_arbiter.operation` 之前）：

```python
if self.audit_gate is not None:
    self.audit_gate()
```

值来自 `observability/audit_policy.py` 的 `audit_gate(sink)`，入口按 `RUNTIME_CONFIG` 组装后传入（`tools/one_expedition_ui_loop.py`）。语义是：**审计日志不可写时，结算与补给一次 UI 调用都不发、一行都不写**。默认 `None` 表示"调用方没有声明门禁"，此时行为与之前逐字节相同——这是刻意保留的逃生口，因为单发工具与离线测试不总有 sink。

与 `_click` 的关系：门禁是**生命周期级**的前置（连读都不做），`_click` 的 `attempted` 是**输入级**的前置。两层都不能省：门禁挡不住"服务启动时能写、跑着跑着磁盘满"，`attempted` 挡不住"读观测时就该停手"。

## 5. 旧日志上限：`logs/one_expedition_ui_loop.jsonl`

这个 jsonl 早于统一运行日志，**此前无任何上限**：循环跑着就一直追加，唯一结局是写满磁盘。

```python
cap = RUNTIME_CONFIG.ui_loop_log_max_bytes          # 16 MiB
if LOG_PATH.stat().st_size >= cap:
    os.replace(LOG_PATH, LOG_PATH.with_name(LOG_PATH.name + ".1"))
```

四条边界：

1. **只有一份上一文件**（`.1`）。不是无限编号，也不是按天分片——按天分片是统一运行日志的职责。
2. **当前流永远在配置路径上**。轮转把旧行搬走，不换当前文件的路径，所以"打开 `LOG_PATH` 就是打开日志"这条读者契约不变。
3. **这不是保留策略**。`ui_loop_log_max_bytes` 不删任何证据：被轮转的内容是完整可回放的 JSONL 分片，躺在数据旁边；三天窗口仍只属于统一运行日志，`run_log_dir` 默认仍是关。
4. **轮转失败向上抛**，与追加失败同处理。日志静默停止记录，比循环停下来更糟。

位置守卫：`logs/one_expedition_ui_loop.jsonl` 与 Action Journal、派遣/监控 sqlite 同在 `logs/`，而三天清理**只走** `logs/run`。因此清理规则不需要一张"不许删"的名单就能保护它（`test_the_legacy_stream_is_outside_the_directory_cleanup_owns`）。

`ui_loop_log_max_bytes` 在导入期断言 `>= 1024`，与 `run_log_max_file_bytes` 同规格；配置仍是 frozen dataclass，测试用 `dataclasses.replace` 造小 cap，不改默认值。

## 6. 本轮刻意不做的事

- **不改日志默认启用策略。** `--run-log-dir` 默认 `None`，统一运行日志仍关闭；本阶段只是让"开了以后的行更可用"，而不是让它默认开。
- **不新增第二个日志、第二个账本、第二个调度队列。** `action_id` 走既有 `correlation_scope`，生命周期走既有 `correlation_id`，轮转走既有 `LOG_PATH`。
- **战斗只有预留字段。** `new_battle_id()` 存在但零调用；本轮不新增任何战斗事件，也不把 `battle_id` 塞进远征行。
- **不动 P0.2c 结论 1/2 的跨线程与异常路径问题。** 那两条要改 `_record_decision` 与 `RunLogSink` 的能力声明，属于日志基础设施，未授权范围内。

## 7. 故障注入矩阵（全部离线）

| 注入 | 期望 | 测试 |
| --- | --- | --- |
| 输入通道抛异常 | 有 `attempted` + `ambiguous`，**无** `sent`；异常上抛 | `test_a_failed_click_is_ambiguous_and_never_sent` |
| 补给按钮输入通道关闭 | 同上，且 `continuous_ui_supply_click_sent` 不出现 | `test_a_closed_input_channel_on_the_supply_button_logs_ambiguity` |
| 审计日志不可写（结算） | 零 UI 调用、零日志行、错误上抛 | `test_settlement_sends_nothing_when_the_audit_log_is_unwritable` |
| 审计日志不可写（补给） | 同上 | `test_resupply_sends_nothing_when_the_audit_log_is_unwritable` |
| 两次连续点击 | 两个不同 `action_id`，均不含 `dispatch_id` | `test_two_clicks_get_two_identities` |
| 一次点击的两行 | `attempted` 与 `sent` 共享同一 `action_id` | `test_each_click_row_carries_the_same_action_id` |
| 补给三次点击 | 全部经 `_click`，三个不同 `action_id` | `test_every_supply_click_is_routed_through_the_one_input_boundary` |
| 结算 happy path | `settlement_started` 带 `lifecycle_kind="settlement"` 与自有 `correlation_id`，无 `dispatch_id` | `test_settlement_opens_its_own_lifecycle` |
| 日志达到 cap | 旧流搬到 `.1`，当前路径只剩新行 | `test_reaching_the_cap_moves_the_old_stream_to_one_previous_file` |
| 已有 `.1` | 被整份替换，不累积 `.2/.3` | `test_a_second_rotation_replaces_the_previous_one_instead_of_growing` |
| 轮转目标被目录占用 | `OSError` 上抛，当前流内容不变 | `test_a_rotation_failure_propagates_and_leaves_the_stream_untouched` |
| `LOG_PATH is None` | 不写任何文件 | `test_nothing_is_written_when_no_path_is_configured` |

## 8. 限制

1. **零实机。** 未启动 Poi/CDP、未点击、未派遣、未补给。`action_id` 是否真的逐行落到真实 sink、`settlement_started` 是否在真实结算链里位置正确，都只有替身级证据。
2. **`action_id` 只覆盖 `continuous_expedition_ui` 的输入。** `visible_ui_refresh.py` 的 `ui_refresh_*` 事件族仍走自己的路径，没有 `action_id`；本轮未合并两者（合并会改刷新的事件序，属于未授权重构）。
3. **生命周期 ID 与派遣账本没有机器可验证的互指。** 结算行不带 `dispatch_id` 是刻意的，代价是"这次结算对应哪次派遣"必须靠 `dispatch_identities.confirmed_at` 与结算时间窗对齐来推断，而不是读一行就知道。
4. **轮转是"每次追加前检查一次大小"**，不做后台清理；一次追加最多让文件超出 cap 一行（单行长度）。这与 `run_log` 的"一个事件不跨文件"原则一致，但意味着 cap 是软上限。
5. **补给多等一个 settle 间隔**（见第 2 节），是刻意的取舍，未在实机测量其对总时长的影响。
