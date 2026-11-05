# 统一运行日志基础设施（P0.2）

状态：已实现、已离线测试、未做实机验证。
权威分支：`feat/production-settlement-resupply-wiring-20261105`。

本文件替代一次性的"设计方案"，因此它同时承担三件事：**现有能力盘点**（阶段 A）、
**新增契约**（阶段 B/C）、**外部参考机制的实际采用情况**（含明确不采用的理由）。
外部参考研究结论由交接文档给出，本轮未重新研究 KCV / KCA Poi-CDP 插件 /
KanColle-Agent，也未阅读其源码。

---

## 1. 现有能力盘点（只读，执行前核验）

| 能力 | 位置 | 结论 |
| --- | --- | --- |
| 追加式 SQLite 事件账本 | `expeditions/continuous_dispatch_controller.py::SqliteDispatchEventLog` | 保留。`dispatch_events(event_id, created_at, event, payload_json)` 四列，**不做 schema 迁移**；本轮新增测试锁定列集合不变、旧库仍可读 |
| 可信 task 归属 | `core/task_attribution.py` | 复用。`TRUSTED_EVENT_KEYS` 三个键 + `merge_trusted_event_fields` 的"trusted 最后覆盖"规则不变；新增的运行日志信封是另一层，不修改它 |
| UI 点击审计 | `adapters/poi/continuous_expedition_ui.py` | 复用。`continuous_ui_click_attempted` / `_sent` / `_ambiguous` 等 20 个事件一行未改，只由新的 sink 换成"同时写 JSONL" |
| 安全操作账本 | `expeditions/action_journal.py` | 保留，且是清理规则的**禁删对象**（测试锁定） |
| 调度 / 结算 / Drain / Witness | `continuous_supervisor.py` / `continuous_settlement_controller.py` / `settlement_drain.py` / `mission_result_witness.py` | 复用。不新建状态机；Supervisor 原本**一个事件都不发**，本轮补上决策事件 |
| 网络事件脱敏 | `adapters/poi/cdp_network_events.py` | 复用。新增公开包装 `redact_sensitive_text` / `is_sensitive_field_name`，运行日志调用同一套正则 |
| 现有 JSONL 出口 | `runtime_config.dispatch_log_path` → `tools/one_expedition_ui_loop.py::_print_event` | 保留原样（单文件、无上限、无轮转）。新的运行日志是**另一个目录**的受控日志，不覆盖它 |
| 现有配置 | `runtime_config.py` | 新增四个上限字段 + 两个路径属性，作为唯一容量来源 |

盘点结论：**事件生产者已经有 57 个事件名和统一的 `event_sink` 注入点**，缺的不是
"再多记点"，而是（a）统一信封与关联 ID，（b）有界、可轮转、可清理的落盘，
（c）关键审计写不进去时的安全行为。这三件事本轮补齐，其余一律不动。

---

## 2. 统一事件格式

一行一个 JSON 对象，追加写入。信封字段固定在行首：

```json
{"timestamp_utc":"...","event":"continuous_ui_click_attempted","run_id":"run-...",
 "task_kind":"expedition","event_id":"event-...","dispatch_id":"dispatch-...",
 "action_id":null,"battle_id":null,"fleet_id":4,"source":"poi-redux:...",
 "observed_at":"...","observed_at_origin":"context","action":"expedition_start"}
```

三类字段，三种信任级别：

1. **只可信**（`timestamp_utc` / `event` / `run_id` / `task_kind` / `event_id`）：
   由 sink 写入。emitter 传了也**直接丢弃**，无法伪造行身份。
2. **上下文优先、可提升**（`dispatch_id` / `action_id` / `battle_id` / `fleet_id` /
   `correlation_id` / `source` / `observed_at`）：上下文有值则上下文赢；上下文没有
   值时把 emitter 的值**提升**到信封槽位，而不是丢弃。原因很实在：现有 57 个事件
   里 `fleet_id` / `observed_at` 本来就在 body 里，直接丢弃是数据损失，不是安全收益。
3. **诊断字段**：其余全部原样保留（经脱敏与截断）。

`observed_at_origin`（`context` / `emitter`）显式标出"这个观察时间是谁说的"，
避免把推断时间当成已验证的游戏状态时间。

**关联 ID 语义**（关键：**不是去重键**）：

- `run_id`：一次进程运行。同一目录里两个进程永不共享。
- `dispatch_id`：一次派遣实例。**相同舰队 + 相同任务 + 相同舰船 ≠ 同一次派遣**，
  所以 `dispatch_id` 由运行时生成注入，绝不从 payload 推导，也绝不做"相同 payload
  合并"。测试 `test_two_identical_dispatches_are_never_merged` 锁定这一点。
- `action_id`：一次 UI/业务动作。
- `event_id`：一行事件。
- `battle_id`：预留，当前不产生。
- `correlation_id`：跨事件的临时串联，不代表唯一对象。

生成方式：`run/dispatch/action/battle/event/corr` + UTC 紧凑时间戳 + 12 位随机十六进制。
时间戳只为人读，唯一性来自随机段。

---

## 3. 落盘、容量与三天清理

目录：`logs/run/`（`RUNTIME_CONFIG.run_log_directory`）。`logs/` 是 gitignore 的，
但同目录还有 action journal、派遣数据库、标定截图 —— 因此运行日志**独占子目录**，
清理规则不需要"哪些文件不能删"的黑名单，只需要"只处理本目录里匹配
`run-YYYY-MM-DD[.NNN].jsonl` 的文件"这一条白名单。

| 项 | 默认值 | 配置位置 |
| --- | --- | --- |
| 保留天数 | 3 | `run_log_retention_days` |
| 单文件上限 | 8 MiB | `run_log_max_file_bytes` |
| 目录上限 | 64 MiB | `run_log_max_total_bytes` |
| 快照文件名 | `current_state.json` | `run_log_snapshot_name` |

行为：

- **按日轮转**：文件名带 UTC 日期。
- **单文件上限**：写入前先算目标文件，超限则开新分片 `run-YYYY-MM-DD.001.jsonl`。
  一条事件永远不跨两个文件（先算大小再写）。
- **三天窗口**：`today - (retention_days - 1)` 为界，早于边界的分片删除。即保留
  今天、昨天、前天。
- **目录上限**：按日期/分片从旧到新删，直到不超上限；**正在写的分片永不删**。
- **清理只作用于运行日志分片**：验收报告、设计文档、证据 JSON、action journal
  SQLite、当前快照都不匹配文件名模式，因此结构上不可达（不是靠 deny list）。
- **清理失败不抛异常**，写入 `PruneReport.errors`，并由 sink 记一行
  `run_log_prune_failed` 告警；成功则记 `run_log_prune_completed`（含删了哪些、
  剩多少字节、当时的三个上限）。清理过程记录自身执行结果。
- **当前快照**：临时文件 + `fsync` + `os.replace` 原子替换；失败删除临时文件并抛
  `SnapshotWriteError`，旧快照保持完好，调用方不得报告成功。

---

## 4. 关键审计失败的安全行为

`audit_policy.CRITICAL_AUDIT_EVENTS` 列出必须"先落盘、再动手"的行：
派遣意图/确认/回港验证/各类 blocked、每一次点击的 attempted/sent/ambiguous、
补给点击与确认、结算步骤、Drain 的每一个动作与终局、`supervisor_paused`
（安全停止）、`settlement_blocked` / `resupply_blocked` / `blocked_fleet_recovered`。

两种失败，两种行为：

| 类别 | 写失败时 |
| --- | --- |
| 普通诊断（相位变化、监控 tick、run 生命周期、清理报告） | 记 `degraded_reason`、回调 `on_error`、**不抛**，运行继续 |
| 关键审计（上表） | `fsync` 后仍失败 → 抛 `AuditLogUnavailable` |

fail-closed 靠**发射顺序**保证，不靠"再检查一次"：

- `CdpContinuousExpeditionUiDriver._click` 先发 `continuous_ui_click_attempted`，
  后调 `input.click()`。sink 抛异常 → 点击根本没发生。
  （测试 `test_a_failing_critical_row_prevents_the_click_and_is_not_retried`
  用**真实** driver 的同一个 `_click` 证实 clicks==[] 且只尝试一次。）
- `ContinuousDispatchController` 新增可选 `audit_gate`，在读取 baseline、
  `prepare_dispatch()`、任何点击**之前**调用。写不进去 → 连 intent 都不落。
- `SettlementDrainSession` 的 `settlement_drain_action` 在 `driver.advance()` 之前发。

不做的事：**不重试写入**。一次重试成功会让"日志说成功了"和"动作发生了"变成两个
不同的断言。也**不自动恢复**：一旦写失败，本 run 的 `ensure_writable()` 保持拒绝，
修好磁盘后应由操作员重启进程（新 `run_id`），而不是让同一个 run 假装日志完整。

---

## 5. 接入点（最小改动）

| 位置 | 改动 |
| --- | --- |
| `observability/`（新增包） | 信封 / 落盘 / 清理 / 脱敏 / 审计策略 / sink |
| `runtime_config.py` | 四个上限 + `run_log_directory` / `run_log_snapshot_path` |
| `continuous_supervisor.py` | `tick()` 拆成 `_tick_once()` + `_record_decision()`，发 `supervisor_tick` / `supervisor_paused`；monitor 的 sink 不可调用时静默跳过 |
| `settlement_drain.py` | 可选 `event_sink`，发 started / action / page_changed / finished |
| `continuous_dispatch_controller.py` | 可选 `audit_gate` |
| `tools/run_continuous_expedition_monitor.py` | `build_runtime(run_log_dir=...)` 用 `TeeEventSink` 包住原 SQLite sink；`--run-log-dir`；run 起止写 `run_started` / `run_stopped` |
| `tools/one_expedition_ui_loop.py` | `--run-log-dir`（默认关）；派发控制器接 `audit_gate` |

`sink.py` 的 `RunLogSink.__call__(event, fields)` 与现有
`Callable[[str, Mapping], None]` **签名完全一致**，所以 monitor / dispatch /
settlement / resupply / visible_ui_refresh / UI driver 六处注入点都**不需要改代码**
就能拿到统一日志；`TeeEventSink` 让 supervisor 的"必须是同一个 sink 对象"校验继续成立。

---

## 6. 外部参考机制：实际采用情况

| 来源 | 机制 | 采用情况 |
| --- | --- | --- |
| KCV AutoExpedition | 完成检测与结算执行分离 | **已具备**（monitor 检测 `status==2`，settlement controller 执行），本轮不改 |
| KCV | 待处理队列 + fleet ID 防重复入队 | **已具备**（调度器相位 + `other_returns`），本轮不改 |
| KCV | 结果归属用舰船 ID 匹配 | **不采用**其"取第一个匹配"做法；本项目已用 `dispatch_id` + 舰船集合 + 时间窗，无法唯一匹配时 fail-closed |
| KCV ActWait / AutoActionQueue | 动作生命周期、等待事件而非固定延时、超时即停、停止清理 | **已具备**（`SettlementDrainSession` 的 observe→确稳→一次动作、双上限、`UI_STUCK`）。本轮只补可追溯事件，**不移植 C# 动作框架、不新建动作队列** |
| KCV | 不把"点击已发送/等待到期"当成业务成功 | **已具备**且本轮显式固化：`click_attempted` / `click_sent` / `*_confirmed` / `*_verified` 是四件不同的事 |
| KCA Poi-CDP 插件 | 快照带观察时间与来源 | **采用**：`source` / `observed_at` / `observed_at_origin` 进信封 |
| KCA | 数据新鲜度是显式字段 | **已具备**（`source_freshness_verified`），本轮把 `observed_at` 提升为信封字段 |
| KCA | generation token 防旧回调覆盖 | **暂不采用**：当前无并发快照写者；引入代次需要改采集链路，超出本次范围。`run_id` 已能区分进程 |
| KCA | 网络/API 异常进日志 | **已具备**（`Network.loadingFailed` 投影 + `sanitize_network_event`） |
| KCA | 停止时清理定时器/订阅 | **已具备**（`provider.close()`），本轮只补 `run_stopped` |
| KanColle-Agent | Data MCP / Poi MCP / 任务图 / 装备规则 / Wiki Researcher | **暂不采用**：属架构路线，不在本次范围。只吸收"来源 + 观察时间 + 可信度可追溯"这一条，落进信封 |

---

## 7. 未做与限制

1. **零实机验证**：本轮未启动 Poi/CDP、未点击真实 UI、未派遣。所有通过都是离线测试
   与一次临时目录内的端到端冒烟（tee → JSONL → 回读 → 摘要）。
2. **生产入口默认未开启运行日志**：`--run-log-dir` 需显式给出，默认行为与 Fleet 4
   验证时**逐字节一致**。理由是不让已验证的链路因新 I/O 改变；代价是默认没有统一
   日志。是否改为默认开启是需要单独决定的策略问题。
3. **战斗日志只预留**：`battle_id` 槽位与 `new_battle_id()` 存在，无任何战斗事件类型
   定义、无战斗流程。
4. **未做资源统计 / 收益账本**，未改 `action_journal`、未改 SQLite schema。
5. `dispatch_id` 目前需要 composition root 用 `sink.correlation(...)` 绑定；生产入口
   尚未包住派遣作用域，因此实机跑出来的 `dispatch_id` 会为空（`null` 合法，但不理想）。
6. 现有 `logs/one_expedition_ui_loop.jsonl` 仍是单文件无上限，本轮未接管；它的容量
   问题独立存在。

---

## 8. 测试矩阵（53 条新增）

- `tests/test_run_log_envelope.py`（23）：信封字段与信任级别、伪造拒绝、提升规则、
  单行可独立解析、坏行不致整档不可读、同派遣共享 `dispatch_id`、两次相同派遣不合并、
  两个 run 可区分、脱敏与截断、tee 不改主 sink 行为、快照原子替换、停止后可从日志
  重建生命周期、旧 SQLite 四列不变且旧行可读、关键事件清单。
- `tests/test_run_log_retention.py`（14）：单文件上限与分片、事件不跨文件、
  按日轮转、三天窗口、报告/数据库/快照不被删除、目录上限从旧删起、正在写的分片不删、
  目录外路径拒绝、清理失败只报错不抛、清理结果自成事件、失败清理有告警行、策略校验。
- `tests/test_run_log_failure_safety.py`（12）：普通写失败告警不中断、关键写失败抛错、
  门禁在失败后保持关闭且只能靠新 run 恢复、不可写目录拒绝、快照失败保留旧快照、
  审计门禁挡在派遣之前（无 Poi 读、无点击、无 intent、无事件）、真实 `_click` 在
  关键行失败时不点且不重试、Drain 动作行失败时不推进、Drain 健康时一页一动作。
- `tests/test_run_log_wiring.py`（4）：Supervisor 记录 tick / paused 与舰队相位、
  非可调用 sink 被容忍、决策真实落到磁盘。
