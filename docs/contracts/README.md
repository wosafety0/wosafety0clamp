# Task contract v1 — 决策层 ↔ 执行层的只读对表文件

本目录定义 wosafety0clamp **接受什么形状的任务意图**，以及**它绝不接受什么**。
它是一份对表契约，不是接口实现：当前仓库里**没有任何运行时消费者**会读取
`task-contract-v1.json`，也没有任何代码会因为这份文件的存在而改变行为。

存在的理由只有一个：把「谁能决定、谁只能执行」写成可校验的文本，避免将来接入
外部决策层（例如 LLM 规划器、攻略助手、数据 MCP）时，把决策权和点击权混到一层。

- 契约文件：`task-contract-v1.json`
- 真值来源：`src/kancollebot/task_scope.py`（`TaskKind` / `FleetPolicy`）
- 稳定性测试：`tests/test_task_contract.py`（代码与 JSON 不一致即失败）
- 版本：`1.0.0`

## 角色与边界

| 角色 | 归属 | 能做什么 | 不能做什么 |
| --- | --- | --- | --- |
| 生产方（producer） | 外部决策层，例如 KanColle-Agent 的 Skills / Data MCP / LLM 规划器 | 读游戏知识、读玩家快照、给建议、产出意图 | 拿到游戏会话、点击 UI、跳过 freshness 核验、改写舰队政策 |
| 消费方（consumer） | 本仓库 wosafety0clamp | 校验意图、自行取得状态证据、执行或拒绝、记录事件 | 生产决策、把建议当成已验证事实、替外部层补全缺失数据 |

一句话：**我们消费决策，不生产决策。**

## 三条硬约束（写死，不随版本放宽）

1. **不引入第二套 Poi MCP。** `127.0.0.1:12450` 是唯一端点。接入任何外部 Poi
   快照服务（例如 Agent 的 `poi-plugin-mcp`）都算新开数据通道，会绕开现有
   freshness 证据链，因此不在本契约允许范围内。
2. **决策层没有执行权。** 外部层不得直接调用 CDP、不得持有 `game_session`、
   不得触发点击。所有游戏动作只能经由本项目 `fail-closed` 的编排层发生。
3. **声明 ≠ 实现。** 契约里出现 `combat` 不等于 combat 可用。除 `expedition`
   外，所有任务的 `implemented` 与 `ui_click_capable` 恒为 `false`，且
   `accepts_intent` 恒为 `false`——意图会被拒绝，不是被排队。

## 字段说明

| 字段 | 含义 |
| --- | --- |
| `contract` / `version` | 契约名与语义版本；只有新增字段才升末位，删改字段必须升主版本 |
| `role` / `direction` / `read_only` | 本仓库在这份契约里的定位：消费方、入站、只读 |
| `authority` | 生产方与消费方各自的责任边界，用于人工审阅 |
| `hard_constraints` | 不可放宽的约束清单；测试会断言其存在 |
| `event_attribution_keys` | 事件归因键；必须与 `task_event_fields()` 完全一致 |
| `tasks[].task_kind` | 任务类型枚举值，必须与 `TaskKind` 一一对应且不重不漏 |
| `tasks[].implemented` | 是否有经过审阅的实现路径 |
| `tasks[].ui_click_capable` | 是否具备已校准的可见 UI 点击能力 |
| `tasks[].accepts_intent` | 当前是否会受理该任务的意图（实现且可点击才为真） |
| `tasks[].dispatchable_fleet_ids` | 该任务允许派遣的舰队（**任务策略，不是游戏事实**） |
| `tasks[].reserved_fleet_ids` | 该任务保留、不派遣的舰队 |
| `tasks[].resupply_verified_fleet_ids` | 已通过补给视觉证据校验的舰队 |

注意 `expedition` 的 `reserved_fleet_ids = [1]` 是**远征策略**：Fleet 1 是出击
主力队，游戏不会把它放进远征页签。这**不是**「Fleet 1 不能做任何事」的全局
事实，战斗任务必须自己声明策略，不得复用这一条。

## 意图示例

以下形状**仅用于对表**，当前没有任何运行时消费者。任何接入都必须先补
状态机、freshness 与授权门禁，再谈解析。

```json
{
  "contract": "kancollebot.task-contract",
  "version": "1.0.0",
  "task_kind": "expedition",
  "intent": { "fleet_id": 2, "mission_id": 5 },
  "note": "自由文本，仅供人工追溯，不参与任何判定",
  "requested_at": "2026-11-04T14:00:00Z"
}
```

消费方处理顺序（若将来实现）应为：任务门禁 → 舰队可用性 → 状态新鲜度 →
视觉证据 → 执行 → 归属记录。任一环节不通过即拒绝，且**不得**用生产方提供的
数值替代本地观测。

## 版本策略

- 新增可选字段：末位版本 +1（`1.0.0` → `1.0.1`），消费方可忽略。
- 删除或改变既有字段语义：主版本 +1（`1.x` → `2.0.0`），旧版本文件保留。
- `hard_constraints` 与 `role` 不参与版本演进：它们被写死，改动等于另立契约。

## 如何校验

```bash
PYTHONPATH=src python -m unittest discover -s tests -p "test_task_contract.py" -v
```

测试会从 `task_scope` 重新推导契约并与本 JSON 逐字段比对。**改了舰队策略却忘了
更新契约，测试失败**——这是本文件唯一的强制力来源。

## 非目标

- 不是运行时接口：没有解析器、没有调度器、没有队列。
- 不是外部项目的数据格式承诺：不描述 KanColle-Agent 的 `kc_*` / `poi_*` 工具。
- 不是游戏知识库：舰船、装备、远征、任务数据一律不进入本契约。
