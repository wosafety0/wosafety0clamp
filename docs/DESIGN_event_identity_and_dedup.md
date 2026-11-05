# 设计：事件 ID 与远征生命周期 ID 的边界

**状态：** event_id 已实现并可回读；dispatch_id 仅完成证据审查与设计，未实现。
**范围：** 离线日志身份，不改 SQLite schema，不接生产结算/补给点击链。

## 1. 当前已核实的数据模型

- `dispatch_events.event_id` 已是 SQLite `INTEGER PRIMARY KEY AUTOINCREMENT`，但旧的 `SqliteDispatchEventLog.load_all()` 没有返回它。本分支将让写入调用返回新 ID，并在读取结果中暴露 `event_id`；既有四列 schema 不变。
- `DispatchIntent` 当前没有 `dispatch_id`。持久化表以 `fleet_id` 为主键，保存 mission、ship IDs、baseline、prepared_at、reserved 和 action completion 时间；一个舰队最多一个 pending intent。
- `FleetRuntime` / `fleet_runtime` 保存 phase、mission tuple、Home Port 标志和 blocked reason，没有生命周期 ID。
- `confirm_reward_claimed()` 将 fleet 转入 `RESUPPLY_REQUIRED` 并清空 `mission`。因此，仅从后续 resupply state 已无法重新计算原派遣身份。
- 当前 dispatch 事件包含 fleet/mission/ship/观察时间等字段；settlement/resupply 事件记录 fleet 与视觉证据，但没有稳定的 dispatch lifecycle ID。

## 2. 两种 ID 不可互换

| ID | 代表什么 | 用途 |
|---|---|---|
| `event_id` | 一条持久化日志记录 | 唯一定位、排序、去重单条事件 |
| `dispatch_id` | 一次远征从派遣到归还、结算、补给的生命周期 | 把多个事件关联为同一事务 |

不能把 `event_id`、`fleet_id` 或 `mission_id` 冒充 `dispatch_id`：一个 dispatch 有多个 event；同一舰队会反复执行相同 mission。`event_id` 只标识数据库中的一行，可作为稳定读取游标；它不能识别两次重复发出的语义相同事件。

## 3. 推荐的后续契约

1. 在持久化 dispatch intent 之前生成非空、不可变的 `dispatch_id`。
2. intent 恢复必须恢复同一个 `dispatch_id`；同一 intent 的重试不得生成新 ID。
3. 将 ID 传入 dispatch、return observation、settlement、reward claim、resupply 和 READY-confirmed 事件。
4. 在 `FleetRuntime` 中保留 ID，直到 resupply 成功并确认 READY；之后才清除。
5. 旧数据库行没有该 ID 时标记为 unknown，不从时间戳或相邻事件猜测补齐。
6. `event_id` 用于唯一定位日志行和推进消费游标；它不能单独去重重复发出的语义事件。
7. `dispatch_id` 用于生命周期分组。若同一阶段可能被重复发出，还需独立的 `event_key` / `idempotency_key`，其组成必须区分合法的重复阶段/尝试，不能简单使用 `dispatch_id + event_name`。
8. 以上 ID 都不能代替状态证据。

**dispatch_id 的存储选项尚未定案：**

- **事件日志派生方案（无 schema 迁移）：** 从已持久化的 `DispatchIntent` 字段确定性计算 ID，在 `dispatch_intent_persisted` 事件中写入；settlement/resupply 通过注入的只读 resolver 从同一 append-only 事件日志恢复当前 fleet 的未闭合 lifecycle。resolver 必须在缺失、歧义或日志损坏时 fail-closed，并证明同一 fleet 在 lifecycle 闭合前不会出现可覆盖它的新 dispatch。
- **直接持久化方案：** 将 ID 加入 intent/runtime 持久化记录；当前表没有字段，因此需要独立的 additive migration、旧行 unknown 语义和回滚方案。

两种方案都需要崩溃恢复、重复事件和多舰队并发测试。此分支不执行 `ALTER TABLE`，不改变既有数据库，也不预选实现方案。

## 4. 结果归属与状态证据

结算页或一次点击本身不能证明结果属于哪支舰队。调度器应根据新鲜 Poi fleet state、mission tuple 与状态机前置条件确认哪个舰队进入待结算阶段。日志中的 `fleet_id` 是控制器处理的目标，不是服务器结果归属的独立证明。若多个舰队同时待结算，必须逐个以状态读回和两帧 UI 证据推进；不得用“刚刚点击了哪个舰队”替代状态核验。

## 5. 本次边界

- 只暴露现有 event_id；不改变 event 表四列 schema。
- 不新增 dispatch_id 字段或数据库迁移。
- 不接线 settlement/resupply 生产控制器。
- 不连接 Poi，不执行 UI 输入、派遣、结算、补给或账号操作。
