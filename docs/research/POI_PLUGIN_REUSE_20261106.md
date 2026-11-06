# Poi 插件复用核查（2026-11-06）

## 状态

- **检查类型：** 本机插件只读盘点 + 当前运行实例只读 MCP 协议/数据验证。
- **插件目录：** 用户授权的 Poi 插件安装目录；本文不记录本机用户名和绝对路径。
- **仓库分支：** `feat/production-settlement-resupply-wiring-20261105`。
- **结论：** Nanodesu MCP 已安装并运行，且 KancolleBot 已有对应的只读客户端与远征读取适配器；禁止重复开发同类 Poi Store 读取插件。
- **变更范围：** 未修改、安装、卸载或配置任何 Poi 插件；未执行游戏 UI 操作；未发起或重放游戏 API 请求。

## 1. 插件目录盘点

安装目录中共有 **27 个 `poi-plugin-*` 插件目录**。与远征闭环直接相关的插件如下：

| 插件 | 安装版本 | 已确认能力 | 处理结论 |
|---|---:|---|---|
| `poi-plugin-nanodesu-mcp` | 0.2.0 | 将 Poi Redux Store 通过本机 MCP Streamable HTTP 暴露；支持读取、结构描述和主数据查表 | **主复用接口** |
| `poi-plugin-akashic-records` | 8.3.2 | 监听自然的远征结果响应并保存结果、四项资源和两种额外道具名称/数量 | **结算辅助证据** |
| `poi-plugin-hand` | 0.0.1 | 每 5 秒读取 `window.getStore('info')`，舰队快照变化时写控制台日志 | 不再作为数据源；它是用户自有的最小骨架，没有外部调用 API |
| `poi-plugin-ezexped` | 1.9.2 | 远征编成/装备要求、大成功条件和收益估算；含自动切换相关 UI 行为 | 规则参考，不作为控制器；注意其 UI 自动切换可能改变当前焦点 |
| `poi-plugin-forwarder` | 1.1.1 | 监听并将完整游戏 API 请求/响应转发至自定义 webhook | **不接入**：没有当前项目所需的奖励字段白名单边界 |
| `poi-plugin-battle-detail` | 6.0.7 | 保存战斗详细记录；Nanodesu MCP 提供 `poi_battle` 读取工具 | 与远征结算主链无关，出击战斗分析时再复用 |
| `poi-plugin-hensei-nikki` | 5.0.5 | 保存舰队编成记录 | 当前 MCP 未注册 `poi_hensei_save`；不调用写入能力 |

`poi-plugin-kancolle-assistant` 0.6.5 的入口主要注册配置面板、Redux reducer 和配置观察器；未发现可供 KancolleBot 调用的远征执行接口。其余插件以图鉴、任务、地图、装备、练级、计时或界面辅助为主，不属于当前闭环的必要依赖。

## 2. Nanodesu MCP 当前运行验证

### 2.1 安装包与运行服务身份

- 安装包 `package.json` 版本：`0.2.0`。
- 服务 `serverInfo.version` / health `version`：`0.1.0`。
- 两者版本字段不一致；不要用 MCP serverInfo 版本代替安装包版本。
- Health 实测 HTTP 200：
  - `name = poi-nanodesu-mcp`
  - `status = ok`
  - `storeReady = true`
  - `port = 12450`
- MCP 初始化实测成功：协议版本 `2025-03-26`，initialize HTTP 200，initialized notification HTTP 202，tools/list HTTP 200。

### 2.2 当前注册工具

实测 `tools/list` 返回 4 个工具：

1. `poi_get`：读取 Poi Store。支持 `path`、`where`、`select`、`limit`、`maxBytes`、`treatAs`。
2. `poi_lookup`：按显式 ID 查主数据；支持 `ships`、`equips`、`missions`、`useitems` 等表。
3. `poi_describe`：先描述路径类型、字段和集合规模，再读取未知结构。
4. `poi_battle`：读取 Battle Detail 保存的战斗记录。

当前没有注册任何写工具；尤其未出现 `poi_hensei_save`。KancolleBot 只应调用前三个只读工具。即使未来出现写工具，也不得自动将其加入当前远征控制链。

### 2.3 当前实际数据结构验证

只读查询已确认：

- `info.fleets` 是数组，当前总数 4；样本字段包含 `api_id`、`api_mission`、`api_ship`。
- `info.fleets` 使用 `api_id` 作为舰队 ID；不得按数组位置猜测 ID。
- `api_mission` 是至少四项的数组；当前读取能返回真实四舰队记录，但任务时间槽位的单位/语义仍必须遵循项目现有验证状态，不得据此擅自推算返航时间。
- `info.resources` 是 8 项数组，`poi_describe` 给出的顺序为：燃料、弹药、钢材、铝土、快速建造、快速修理、开发资材、改修资材。
- `info.resources` 的数组索引与 `poi_lookup(kind="useitems")` 的道具 ID 空间不同，禁止互相映射。
- `poi_lookup(kind="missions", ids=[...])` 可查询远征主数据；中文显示受当前 PowerShell 输出编码影响，KancolleBot 应按 UTF-8 JSON 解码，不应依赖控制台显示文本。

这些是当前安装实例的只读运行证据，不代表数据新鲜度已经由读取时间自动证明。项目仍须使用独立的母港/自然响应新鲜度证据，不能把 `ObservedState.observed_at` 当作游戏状态刷新时间。

## 3. Akashic Records 的结算证据边界

实际 Store 路径：

`ext.poi-plugin-akashic-records._.mission.data`

当前读取到 31 条历史记录。插件源码 `views/api-resolver.js` 对 `/kcsapi/api_req_mission/result` 的处理逻辑为：

- 结果记录时间戳；
- 远征名称；
- 将 `api_clear_result` 映射为“失败/成功/大成功”文本；
- 成功/大成功时记录 `api_get_material` 前四项；失败时写四个 0；
- 对 `api_useitem_flag` 标记的道具记录道具名称和数量。

记录行是位置数组，字段布局为：

| 索引 | 含义 |
|---:|---|
| 0 | 记录时间戳（毫秒） |
| 1 | 远征名称 |
| 2 | 本地化结果文本 |
| 3–6 | 燃料、弹药、钢材、铝土 |
| 7–8 | 道具槽 1 名称、数量 |
| 9–10 | 道具槽 2 名称、数量 |

**不可越权使用：** 该日志不保存舰队 ID、舰船 ID、请求/响应配对身份或原始结果码。它不能单独证明某一条记录属于本轮哪支舰队，也不能替代 `MissionResultWitness`。时间戳/远征名称/奖励数值只可用于辅助交叉核对；存在多支舰队并发、相同远征或同一时间窗多次结算时，必须拒绝仅凭 Akashic 记录归属。

## 4. KancolleBot 现有实现：禁止重复开发

目标分支已有以下实现：

- `src/kancollebot/adapters/poi/mcp_client.py`
  - 默认端点 `http://127.0.0.1:12450/mcp`；
  - 默认只允许 loopback；
  - 拒绝 URL 凭据；
  - 禁止自动跟随 HTTP 重定向；
  - MCP initialize / initialized / tools/list 协议处理；
  - 只允许调用 `poi_describe`、`poi_get`、`poi_lookup`。
- `src/kancollebot/adapters/poi/expedition_reader.py`
  - 检查 health 的服务身份、状态和 `storeReady`；
  - 检查必需只读工具；
  - 校验集合类型、总数、返回数和 `truncated`；
  - 归一化舰队、舰船、装备状态；缺失/畸形/截断数据拒绝放行。
- `src/kancollebot/adapters/poi/mission_result_observer.py` 与 `tools/hand_sandbox/poi_mission_result_observer_20261105.js`
  - 被动监听客户端自然产生的远征结果响应；
  - 只提取白名单字段，不保存原始请求/响应体。
- `src/kancollebot/expeditions/mission_result_witness.py`
  - 对舰队、任务、舰船、结果码、请求/响应时间和奖励收据进行结构化建模与核验。

本次通过当前 Poi 实例直接验证了 MCP health、协议初始化、工具列表和若干 Store 路径/数据形状；**尚未在本机直接执行仓库中的 `PoiMcpClient` + `PoiExpeditionReader` 完整调用链**。因此不能把“直接 MCP 运行验证”扩大描述为“项目适配器端到端实机验收”。

## 5. 给生产闭环的接入约定

1. 继续复用现有 `PoiMcpClient` 和 `PoiExpeditionReader`；不得另写 Poi Store 轮询插件或第二套 MCP 客户端。
2. 状态读取先 `poi_describe`，再按明确字段调用 `poi_get`；主数据 ID 通过 `poi_lookup` 解析。
3. 任何集合响应都必须验证 `kind`、`total`、`returned`、`truncated`；截断、数量不一致、字段缺失一律 fail closed。
4. `info.resources` 只能按已描述的八项位置语义解析；道具 ID 必须走 `poi_lookup(kind="useitems")`，不得把数组下标当道具 ID。
5. Akashic 记录只作奖励明细的辅助证据，不作为派遣归属、结果码或状态机推进凭据。
6. 本轮派遣归属以持久化 dispatch record 与被动 `MissionResultWitness` 的舰队 ID、舰船集合、任务名和时间交叉核对为准。
7. 奖励到账验证应同时保留结果响应奖励收据、结算前 Poi 库存基线和结算后新鲜 Poi 库存；单独的 Akashic 文本日志或单独的库存变化均不足以证明完整到账。
8. 不调用 `poi_hensei_save`；不使用 `poi-plugin-forwarder` 转发完整原始响应；不让 `poi-plugin-hand` 重复轮询；不依赖 EZ Exped 自动切换作为 UI 页面证据。
9. 视觉页面仍由现有指纹机制识别；固定坐标点击前的双帧确认、0.97 分数阈值、0.04 margin 和 UNKNOWN fail-closed 语义不变。MCP 网络事件只提示何时探测，不授权点击。

## 6. 验证状态与限制

### 已验证

- 插件目录只读盘点：27 个 Poi 插件目录。
- Nanodesu 安装包版本：0.2.0。
- Health：HTTP 200，服务身份正确，Store ready。
- MCP 协议：initialize / initialized notification / tools/list 成功。
- 工具列表：4 个只读工具，未出现写工具。
- 当前运行实例：`info.fleets`、`info.resources`、Akashic mission log 路径可描述/读取；`poi_lookup(kind="missions")` 可调用。
- Akashic 远征日志的 11 列语义已由实际安装源码核对。

### 尚未验证

- 当前分支中的 `PoiMcpClient` 与 `PoiExpeditionReader` 在本机 Poi 进程上的完整端到端调用。
- Akashic 历史行与某次自然结算响应的逐笔匹配；由于日志缺少舰队/舰船身份，这种匹配只能作为辅助，不可作为主归属。
- 新奖励收据与当前客户端自然结算、结算前后 Poi 库存的逐笔实机核对。

### 本轮未做

- 未修改插件、安装包、配置或 Poi 全局设置。
- 未执行任何游戏 UI 点击。
- 未主动发起或重放游戏 API 请求。
- 未修改 KancolleBot 运行代码；本文件仅记录复用接口和验证证据。


## 7. Follow-up: live adapter reuse and settlement wiring?2026-11-06?

- ?????????????????? PoiMcpClient + PoiExpeditionReader ?????? Nanodesu MCP?lookup_mission_name(3) ?? ?????read_reward_inventory() ?? 8 ????? 19 ????????????????? UI ????? API ???
- ???????? Poi ??? Store ??????????????? mcp_client.py?expedition_reader.py?????? lookup_mission_name ? read_reward_inventory ?????????????????????
- ?????????????????????????????????????? Poi ???????
- ???????? fleet_id + dispatch_id ???????????????? UI ?????????????? Poi ??/???????????????????
- ?????Python unittest 1,886 ????20 ????Node 29/29?Python compileall?git diff --check ???
- ????????????????????????????????????????????????????????Poi ??????????????????? read_reward_inventory ? observed_at ?????????? Poi Redux ??????????
