# 设计：任务维度事件归属与旧 SQLite schema 兼容评估

**状态：** P0 整改设计说明（离线，未执行 schema 迁移、未修改生产点击链）
**基线：** `3e2e2ba`
**关联：** R2 事件归属加固 / 只读架构审查 R2、R6、R8

---

## 1. 问题陈述

`SqliteDispatchEventLog` 是 append-only 事件 sink，位于远征命名的模块中，但被
`SHARED_CAPABILITIES` 标为 `task_neutral`。多个自动化任务将来共用同一个 SQLite
文件时，必须能按任务拆分事件，且任何 emitter 都不能伪造自己属于哪个任务。

整改前的两个漏洞：

1. `continuous_dispatch_controller.py:142` 用
   `payload_fields.setdefault("task_kind", self.task_kind)`。`setdefault` 意味着
   `fields` 里只要带了 `task_kind`，就能覆盖构造期传入的可信归因。
2. `tools/one_expedition_ui_loop.py:_print_event` 把 `**extra` 放在字典字面量的
   最后，`extra` 可以覆盖 `timestamp_utc` / `run_id` / `task_kind`。（`event`
   是具名形参，Python 本身已拒绝重复传入；把它列入保留键是为其他 emitter 与
   未来重构兜底，而非当前可复现的漏洞。）

两条通道（SQLite / jsonl）此前也没有统一的保留键集合。

## 2. 归属模型（谁拥有任务身份）

- **可信来源只有一个**：打开日志的 composition root（进程）。它在构造 sink 时传入
  `task_kind`，或在 jsonl 打印器里从 `RUNTIME_CONFIG.dispatch_policy_task_kind`
  取当前任务。
- **emitter 只拥有诊断字段**：`fleet_id`、`mission`、`point`、`attempt`、
  `wait_seconds` 等任意键值。
- **保留键**（`core/task_attribution.py`）：
  - `TASK_ATTRIBUTION_KEYS` = `task_kind`、`task_implemented`、
    `task_ui_click_capable`（与 `task_scope.task_event_fields()` 的三个字段一致）。
  - `EVENT_ENVELOPE_KEYS` = `timestamp_utc`、`run_id`、`event`。
  - `TRUSTED_EVENT_KEYS` = 两者并集。

两个纯函数定义唯一契约：

| 函数 | 用于 | 规则 |
| --- | --- | --- |
| `apply_trusted_task_attribution(fields, task_kind=...)` | SQLite sink | `task_kind is None` 时原样返回（旧 payload 逐字节不变）；否则剥离 `fields` 中全部归因键，再写入可信 `task_kind` |
| `merge_trusted_event_fields(extra, trusted=...)` | jsonl 打印器 | 先剥离 `extra` 中所有保留键，再写入 `trusted` 信封；`trusted` 含非保留键时抛错 |

"剥离而不是覆盖"是有意的：emitter 既不能改可信值，也不能通过额外字段制造
"看起来像可信字段"的第二份归因。

## 3. 旧 SQLite schema 兼容评估

现状 DDL（4 列，无任务列、无版本列）：

```sql
CREATE TABLE IF NOT EXISTS dispatch_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    event TEXT NOT NULL,
    payload_json TEXT NOT NULL
)
```

`load_all()` 以 `SELECT created_at, event, payload_json ... ORDER BY event_id`
读取，与具体列集合无关，只要这 3 列存在即可。

| 方案 | 兼容性 | 风险 | 结论 |
| --- | --- | --- | --- |
| **A. 不改 schema，归因放 `payload_json`**（本次采用） | 完全兼容。`CREATE TABLE IF NOT EXISTS` 不触碰已有表；旧行（无 `task_kind`）仍可读，读者须把缺失视为 `unknown`，不得默认为 expedition | 按任务过滤只能在应用层解析 JSON，无法用索引 | **采用** |
| B. `ALTER TABLE ... ADD COLUMN task_kind TEXT` | 需要迁移旧库；`IF NOT EXISTS` 对加列无幂等保护，需要 schema version 表 | 并发写入下的迁移窗口、`created_at` 语义、旧代码读到新列时的行为都需实机复核 | 本次不做 |
| C. 建任务专属表 / 视图 | 破坏"共表"这一多任务前提，且需要双写过渡 | 高 | 不做 |
| D. 复合主键 / lease 表 | 与 R2 无关，属通用任务框架阶段 D | 高 | 不做 |

**关键判断：** 本次 R2 的目标是"归因不可伪造"，不是"归因可索引"。方案 A 已完整
满足前者，且零迁移风险。用户约束明确要求"不进行 schema 迁移，除非先提交独立设计
和风险说明"；本文件即该说明，但本次**选择不迁移**，因此不产生任何 DDL 变更。

**旧库语义约定（写入设计，不改代码）：**
- 构造 sink 时未传 `task_kind` → payload 逐字节不变（既有离线测试与磁盘数据库有效）。
- 旧行缺失 `task_kind` → 归属为 `unknown`，禁止推断为任何任务。
- 真实运行的 composition root 必须传 `task_kind`；`build_runtime()` 已如此做。

## 4. 双任务共表

同一个 SQLite 路径可以有两个 `SqliteDispatchEventLog` 实例（不同构造期
`task_kind`）。两者共用 `event_id` 自增序列和同一张表；每条行的归因由**打开该
sink 的进程**决定，而不是由写入时的 `fields` 决定。这保证共表时不会串味。

注意：这里的第二个 `task_kind` 只是**日志标签**。测试里使用
`TaskKind.COMBAT.value` 之类字符串仅用于验证共表归因；`IMPLEMENTED_TASK_KINDS`
仍只有 `EXPEDITION`、注册表仍全部 `DECLARED`，不启用、不接线、不点击任何任务。

## 5. 已实现 / 未实现

**已实现（本次 P0）**
- `core/task_attribution.py`：保留键集合 + 两个纯函数。
- `SqliteDispatchEventLog.__call__()`：改用 `apply_trusted_task_attribution()`。
- `tools/one_expedition_ui_loop.py:_print_event()`：改用
  `merge_trusted_event_fields()`，并保持历史键顺序以兼容逐行比较的读者。
- 新增测试：旧 schema、双任务共表、归因不可覆盖、jsonl 信封不可覆盖、
  生产入口真实 kwargs 断言。

**明确未做（保留为后续独立节点）**
- 任何 `ALTER TABLE` / schema version / 迁移脚本。
- 把 `task_event_fields()` 的 `task_implemented` / `task_ui_click_capable`
  写入 SQLite payload（会改变既有 payload 形状，需单独评估）。
- 重命名 `dispatch_events` 表或把 sink 搬出远征命名模块
  （`SHARED_CAPABILITIES` 已记录为后续清理，本次不借解耦之名扩大清单）。
- 生产点击链修改（见 `DESIGN_production_controller_wiring.md`）。
