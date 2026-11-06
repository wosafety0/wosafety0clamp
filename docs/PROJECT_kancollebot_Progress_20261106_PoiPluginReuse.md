# KancolleBot 阶段进度：Poi 插件复用核查（2026-11-06）

## 已完成（只读核查）

- 盘点用户 Poi 插件目录，共 27 个 `poi-plugin-*` 目录。
- 确认 `poi-plugin-nanodesu-mcp` 安装版本 0.2.0，当前服务 health HTTP 200、`storeReady=true`。
- 使用 MCP 协议版本 `2025-03-26` 实际完成 initialize、initialized notification、tools/list；当前注册 `poi_get`、`poi_lookup`、`poi_describe`、`poi_battle` 四个工具。
- 实际读取并描述 `info.fleets`、`info.resources`，验证当前安装实例返回结构与 KancolleBot 既有只读适配器所需的核心路径相符。
- 检查 Akashic Records 源码与当前日志路径；其远征历史包含时间、任务名、结果文本、四项资源和两组道具名称/数量，但缺少舰队 ID、舰船 ID 和请求/响应配对身份，只能作辅助证据。

## 复用结论

- **禁止重复开发** Poi Store MCP 客户端和舰队读取插件。
- 复用现有 `src/kancollebot/adapters/poi/mcp_client.py` 与 `expedition_reader.py`。
- 远征结算身份继续以持久化派遣记录和被动 `MissionResultWitness` 为主；Akashic Records 仅作奖励明细辅助核对。
- 不接入 `poi-plugin-forwarder`（转发完整原始 API 数据）；不依赖 `poi-plugin-hand`（5 秒轮询骨架）；不调用写工具；不依赖 EZ Exped 自动切换作为 UI 证据。
- 双帧点击确认、0.97 分数阈值、0.04 margin 和 UNKNOWN fail-closed 语义不变。

## 验证边界

- **VERIFIED：** 当前安装插件的 health、MCP 握手、工具列表及 Store 路径/数据形状的只读运行验证。
- **IMPLEMENTED（既有代码）：** 仓库已有只读 MCP 客户端、远征读取器、被动结果观察器和 MissionResultWitness。
- **VERIFIED：** 已在本机通过仓库的 PoiMcpClient + PoiExpeditionReader 实际调用 lookup_mission_name(3) 和 read_reward_inventory()；任务名返回 警備任務，库存返回 8 个资源槽、19 条道具记录。
- 本轮插件复用核查未修改插件/配置，未执行游戏 UI 操作，未主动发起或重放游戏 API 请求。

## 详细产物

见 [POI 插件复用核查报告](research/POI_PLUGIN_REUSE_20261106.md)。

## 下一步

1. 由本轮适配器核查结果约束生产开发：直接复用现有 MCP reader，不新建插件或重复轮询。
2. 在不干扰当前生产任务的前提下，安排既有 `PoiMcpClient + PoiExpeditionReader` 的本机只读端到端调用验证。
3. 单舰队自然结算时，继续以 `MissionResultWitness` 和派遣记录核对身份，使用 Akashic 仅作奖励明细辅助交叉检查，再与 Poi 新鲜库存基线/读回核对。


## Follow-up: live adapter verification (2026-11-06)

- VERIFIED：本机 PoiMcpClient.health() 为 poi-nanodesu-mcp / ok / storeReady=true；MCP 工具列表为 poi_get、poi_lookup、poi_battle、poi_describe。
- VERIFIED：PoiExpeditionReader.lookup_mission_name(3) 返回 警備任務；read_reward_inventory() 读取 info.resources 和 info.useitems，分别得到 8 个资源槽和 19 条道具记录。
- VERIFIED：生产派遣控制器强制解析真实任务名后才允许派遣；生产结算控制器复用库存读取器，并按 dispatch_id 加载结算前库存基线。
- VERIFIED：Akashic 远征日志当前总数为 34，单条 11 列；仍缺少舰队/舰船/请求响应身份，只能辅助核对。
- VERIFIED：EZ Exped 已安装但当前配置 enable=false；没有启用它，也没有修改 Poi 配置。
- LIMIT：未验证完整 read_fleets() 链、当前自然结算收据及结算前后库存逐笔变化；不能据此宣布奖励到账闭环实机验收完成。
- SAFETY：本轮未执行游戏 UI 点击，未主动发起或重放游戏 API 请求，未修改 Poi 插件或配置。
