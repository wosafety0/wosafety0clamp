# 远征结算机制与奖励到账证据

> 本文只记录远征的游戏结算、奖励确认和 UI 生命周期。当前开发优先级是把真实结算闭环做可靠；收益排名、资源自适应和多舰队优化不属于本文范围。

## 1. 结算生命周期

远征计时到期只表示服务器侧预计完成时间已到，不等于 Poi 已取得最新状态，也不等于客户端已经结算。

当前已观察到的顺序：

1. **到期但尚未刷新**：舰队仍可能在 Poi 中显示远征中。不能仅凭本地计时器触发结算。
2. **触发母港刷新**：从母港进入其他游戏界面，再返回母港，使客户端自然发出新的 `/kcsapi/api_port/port` 请求。新鲜状态和母港右上角返航提示共同用于识别返航。
3. **接受远征结果**：通过正常游戏 UI 点击返航提示，客户端自然发出 `/kcsapi/api_req_mission/result`。请求包含 `api_deck_id`；响应给出本次远征身份、结果和奖励数据。项目只被动观察这次自然请求，不主动调用或重放游戏 API。
4. **远征结果画面（`RETURN_RESULT`）**：显示成功、大成功或失败。结果请求已被游戏处理；Poi 的 `api_mission` 从待领取状态清零，不能据此推断后续所有 UI 页面均已完成。
5. **额外道具画面（`BONUS_ITEMS`，如本次结果有相应展示）**：与结果画面是不同 UI 阶段。应独立识别和推进，不能把结果页模板或“次へ”按钮模板当作两页的区分依据。
6. **返回母港（`HOME_PORT`）**：推进结果/道具展示链后回到母港。回到母港证明 UI 链已结束，不单独证明奖励数值正确，也不替代结果响应见证。
7. **补给与再派遣**：结算链关闭、舰队归属明确、Poi 状态新鲜后，才允许补给；补给后核验燃料和弹药达到舰船上限，再允许下一次派遣。

## 2. 结果类别

项目应把 `api_clear_result` 作为结果类别的机器可读依据：

| `api_clear_result` | 结果 | 自动化处理 |
| ---: | --- | --- |
| 0 | 失败 | 明确记录失败；不得按成功奖励记账 |
| 1 | 成功 | 记录普通成功 |
| 2 | 大成功 | 记录大成功 |
| 其他/缺失 | 未知 | fail closed，不得猜测 |

上述 0/1/2 映射由社区 API 解析实现交叉印证；项目已有的实机样本捕获到 `clear_result=1`。正式运行仍应保留原始整数，未知值不得折叠成普通成功。

## 3. 结果响应中的奖励凭据

社区对 `api_req_mission/result` 响应的解析实现记录了以下字段：

- `api_get_material`：四项基础资源奖励，顺序为燃料、弹药、钢材、铝土。旧版解析器同时兼容长度为 4 或 8 的数组；长度 8 时还会解析后四项资源/道具计数。
- `api_useitem_flag`：两个额外道具槽位的标记。
- 当对应标记大于 0 时，读取 `api_get_item1` 或 `api_get_item2` 中的 `api_useitem_id` 和 `api_useitem_count`。

因此，**结果响应是奖励明细的首要机器凭据**；`BONUS_ITEMS` 是后续展示阶段，不能把推进到该画面当成奖励才开始入账的证据。客户端工具 Logbook 在处理该结果响应时，按 `api_clear_result` 判断成败，并从 `api_get_material` / `api_get_item1` / `api_get_item2` 读取奖励。

注意：以上字段结构来自社区逆向解析代码，不是官方公开 API 规范。项目必须通过下一次自然结算响应进行当前客户端实测，不能只凭旧解析器把字段视为已在当前版本验证。

## 4. 结果页与奖励到账必须分开判定

以下证据不能互相替代：

- **返航提示**：证明客户端观察到返航状态；不证明已接受结果。
- **`api_req_mission/result` 请求/响应配对**：请求侧 `api_deck_id` 与响应侧任务名、结果、舰船 ID 一致时，证明游戏处理了哪支舰队的哪次远征。
- **`api_clear_result`**：证明结果类别；不能把所有非零值不加区分地当作大成功。
- **奖励响应字段**：证明游戏在该次结算中声明的资源/道具奖励。
- **Poi 新鲜状态**：用于复核资源和道具库存是否反映结算；必须与结算前快照、时间戳及期间其他资源操作一起解释。
- **`BONUS_ITEMS` / `HOME_PORT`**：分别证明 UI 展示链的阶段，不单独证明资源数值。
- **`api_mission` 清零**：证明任务结果已被接受/任务状态已结束；不等于奖励明细已核对，也不等于奖励页已关闭。

资源库存可能受到上限或其他操作影响，因此不能只用“库存增加了某个数”作为唯一凭据。应保存结果响应奖励明细，并在单舰队、无并发资源操作的验证窗口内与新鲜 Poi 库存快照交叉核对。

## 5. 当前项目证据与缺口

### 已验证

- 2026-11-05，Fleet 3 的自然 `api_req_mission/result` 请求/响应被实机捕获：请求侧 `api_deck_id=3`；响应侧任务名 `練習航海`、`api_clear_result=1`、舰船 ID 与对应派遣记录逐位一致。结果接受后 Poi Fleet 3 状态变为 idle。该见证已持久化，并在进程重启后成功重建。
- 2026-11-06，L4 soak 已完成：Fleet 2 的远征 3 共 6 次、Fleet 3 的远征 1 共 2 次、Fleet 4 的远征 2 共 1 次；全部派遣实例均结算关闭，三队最终 READY 且燃料/弹药补给满。Fleet 2 有两次同进程完整往返闭环。
- `RETURN_RESULT` 与 `BONUS_ITEMS` 必须独立识别。L4 实机发现不同远征的获得物品页内容区存在模板差异，已用规范化状态变体机制支持远征 1、2、3 的已采集模板；未采集的新任务仍应 fail closed。

### 已实现（离线测试通过，尚未当前客户端实机核验）

- `poi_mission_result_observer_20261105.js` 已被动提取奖励白名单：`api_get_material`、`api_useitem_flag`、`api_get_item1`、`api_get_item2`；只保存资源数组、槽位标记、道具 ID/数量，不保存原始请求体或响应体。
- Python observer wrapper 对奖励字段做结构校验；缺失/畸形字段保留身份见证，但将奖励收据降级为 `incomplete`，不伪装成完整。
- `MissionResultWitness` 已持久化结构化 `MissionRewardReceipt`，并严格区分成功、大成功、失败和未知结果码。
- 结果见证身份现在包含请求/响应时间；同一舰队、任务和舰船组合的不同远征轮次不会再被误去重。与派遣记录交叉核验时，早于本轮派遣的旧见证会被拒绝。
- GitHub Actions CI #1670 已通过：Node 测试、两个被动观察器语法检查、CLI 冒烟、Python 编译及 1,849 项 unittest 全部通过；22 项因可选视觉依赖缺失而跳过。

### 尚未验证/待实现

- 被动结果观察器与持久化收据目前仍通过独立捕获工具使用，**尚未自动接入生产结算控制器**；生产闭环还没有在第一次结果点击前自动启动并等待该观察器。
- 新增奖励字段尚未在当前游戏客户端的下一次自然结算中实机捕获，尚未与结算前/后的 Poi 新鲜资源和道具库存做逐笔核对。
- L4 soak 的 UI 生命周期和 READY 终态已通过实机验证，但不能据此声称每次奖励明细都已核对。

## 6. 下一步实施顺序

1. 将结果观察器做成可取消的后台 provider，在结算 UI 第一次点击前持续监听自然的 `api_req_mission/result` 响应。
2. 将本轮见证与持久化派遣记录绑定：舰队、舰船、结果请求/响应时间必须匹配本轮派遣；未知结果码或不完整奖励收据必须阻止结算状态前进。
3. 单舰队自然结算时保存点击前 Poi 资源/道具基线，捕获响应奖励明细；返回母港后读取新鲜 Poi 库存并交叉核对，记录库存上限或期间其他操作造成的差异。
4. 核验 `RETURN_RESULT → BONUS_ITEMS（如出现）→ HOME_PORT` 的每个 UI 阶段，再补给并读回燃料/弹药满补状态。
5. 只有上述证据齐全，才允许同一舰队再次派遣。任何阶段证据缺失都停在当前状态，不盲点、不重发 API。

## 7. 参考来源与证据等级

- [KanColle Wiki*：远征](https://wikiwiki.jp/kancore/%E9%81%A0%E5%BE%81) —— 社区整理的远征成功/大成功/失败规则与奖励差异。
- [KanColle Wiki：Expedition Introduction](https://kancolle.fandom.com/wiki/Expedition/Introduction) —— 远征结果与大成功奖励说明。
- [Logbook：MissionResultDto.java](https://www.javatips.net/api/logbook-master/main/logbook/dto/MissionResultDto.java) —— `api_clear_result` 的 0/2/其他映射。
- [Logbook：ResourceItemDto.java](https://www.javatips.net/api/logbook-master/main/logbook/dto/ResourceItemDto.java) —— `api_get_material`、`api_useitem_flag`、`api_get_item1/2` 解析方式。
- [Logbook：GlobalContext.java](https://www.javatips.net/api/logbook-master/main/logbook/data/context/GlobalContext.java) —— 客户端收到远征结果响应后，按结果读取并登记奖励。
- 项目实机记录：[docs/PROJECT_kancollebot_Progress.md](../PROJECT_kancollebot_Progress.md) 中 2026-11-05 R3-H 与 2026-11-06 L4 soak 条目。

外部社区代码属于逆向资料，项目实机响应与 Poi 新鲜读回优先级更高。若外部资料与当前游戏实测冲突，以可重复的当前实测为准并记录差异。
