# KancolleBot 开发进度

## 2026-10-21

### 已完成

- 核实仓库 `wosafety0/KancolleBot` 为私有仓库，默认分支为 `main`。
- 建立 Python 包基础结构、舰船/舰队数据模型、安全策略、只读 POI 适配器契约、单元测试和 GitHub Actions CI 工作流。
- 新增 `ObservedState` 通用观察状态封装，要求时区感知时间戳，并提供可测试的状态新鲜度校验；未来时间戳、过期状态和无效时间配置会被拒绝。
- 为状态新鲜度校验添加离线单元测试，覆盖新鲜、过期、未来时间戳、无时区时间和负最大年龄。
- 初始实现不建立网络连接，不执行真实游戏操作。
- 创建研究分支 `dev/kcv-research-python-direction`，研究方向确定为：KCV（非提助手）优先、KCA（kcauto_custom）补充、Python 自研、POI 本地运行。

### 项目身份与研究边界

- **KCV = 非提助手**，官网：https://kancolleassistant.mechland.cn/ 。它是首要自动化研究对象。
- **KCA = kcauto_custom**，仓库：https://github.com/XVs32/kcauto_custom 。仅用于补充和交叉验证，不能把 KCA 行为误记为 KCV 行为。
- 不要将 KCV（非提助手）与独立的 `Grabacr07/KanColleViewer` 官方 Viewer 项目混为一谈。
- 历史记录中的 KCV 源码根目录为 `D:\\KancolleBot\\Source\\KCV`，反混淆报告目录为 `D:\\KancolleBot\\Reports\\KCV_Deobfuscation`。这属于旧项目路径，按用户要求不得自动扫描、读取或修改；只可使用当前项目中已有副本、已提供材料和已有研究记录。
- 当前项目中的 `02_ReverseEngineering/KCA/OriginalReferences_Deobfuscation_Test/Grabacr07.KanColleViewer.Models/Assistant.cs` 与 `.../Grabacr07.KanColleViewer.Views.Assistant/AutoSortie.xaml.cs`，由用户明确指定作为 KCV 相关 Assistant 自动化研究材料。虽然路径含 `KCA`，不得因此将其归类为 KCA 源码；本地研究保留，上传第三方源码前须单独审查许可证与必要性。

### 验证状态

- 仓库写入：基础框架、研究文档和观察状态新鲜度检查已写入远程研究分支。
- CI：提交后以对应最新提交的实际工作流结果为准；本次新增代码尚待对应 CI 确认。
- POI 接口：尚未验证；当前仅有只读协议定义。
- KCV 自动化调用链：待逐项追踪并提供源码证据。
- KCA 行为研究：待按模块开展对照。
- 自动远征：未实现。
- 自动战斗：未实现。
- 真实游戏操作：仍默认关闭；新鲜度检查只是基础保护层，不代表可以执行游戏操作。

### 下一步

1. 从非提助手官网确认官方源码、版本、文档或下载入口。
2. 优先使用当前项目已有的 Assistant 相关材料，识别入口、调度和执行调用链；不扫描旧目录。
3. 追踪远征完整流程，再研究出击、战斗判断、撤退/继续和舰队轮换。
4. 在证据不足处对照 KCA，明确标记 KCV 已证实、KCA 独有或自研策略。
5. 验证 POI 实际支持的状态读取方式。
6. 扩展纯 Python 状态模型、策略函数和离线模拟测试；真实操作默认关闭。

## 2026-10-24 — POI Nanodesu MCP 静态契约复核

### 本次检查范围

- GitHub 分支：`dev/expedition-closed-loop-mvp`。
- 检查文件：`src/kancollebot/adapters/poi/mcp_client.py`、`expedition_reader.py`、`expedition_state_v2.py`、`src/kancollebot/expeditions/runner.py`、`tools/verify_poi_bridge.py` 及相关离线测试。
- Nanodesu 插件的 `index.ts`、`server.ts`、`tools.ts`、`query.ts`、`serialize.ts`、`paths.ts` 是在本次对话中由用户提供的源码片段；结论属于静态源码分析，不是本机运行结果。

### 已确认

- MCP 客户端默认连接 `http://127.0.0.1:12450/mcp`，默认拒绝非 loopback 主机，只允许调用 `poi_describe`、`poi_get`、`poi_lookup`；写入工具 `poi_hensei_save` 不在允许列表中。
- 客户端使用标准库，设置超时和响应大小上限；读取器要求 health 返回 `status == "ok"` 且 `storeReady is True`，随后检查必要工具是否存在。
- Nanodesu 查询层的集合结果结构（`kind`、`items`、`total`、`returned`、`truncated`）与读取器预期的数组/对象映射封装基本一致。序列化层在因字节上限丢弃条目时会设置截断标志，读取器会拒绝明确标记为截断的数据。
- 状态归一化要求 `info.fleets` 为数组，`info.ships` 和 `const.$ships` 为数字 ID 键对象；会拒绝缺失舰船、缺失主数据、异常 HP 和未知任务数组形状。
- 任务返回时间未被猜测；`return_at` 仍为 `None`。这是保守处理，不代表已具备准确的远征完成时间预测。
- `runner.py` 具备安全策略前置检查、重复尝试拦截、可选持久化 journal，以及派遣后的二次读取确认；适配器调用成功本身不会被直接当成服务器已接受。

### 发现的验证缺口与待处理项

1. **只读验证器覆盖不足：** `tools/verify_poi_bridge.py` 的 Nanodesu 路径目前探测 `info.fleets` 和 `info.ships`，没有探测读取器必需的 `const.$ships`。因此验证器报告成功也不能证明 `PoiExpeditionReader.read_fleets()` 所需的三条路径都可用。后续应补充该路径的结构摘要与离线测试。
2. **端点校验不一致：** `verify_poi_bridge.py` 会拒绝 URL 内嵌用户名/密码；`PoiMcpClient.__init__()` 当前没有相同检查。建议统一策略，在 MCP 客户端构造时也拒绝 URL 凭据。
3. **本地时间戳不等于游戏数据新鲜度：** `ObservedState.observed_at` 是读取时生成的观察时间。即使每次读取时间更新，也不能单凭它证明 POI Redux 数据来自当前登录会话或刚发生的游戏状态变化。必须独立验证登录/母港证据及状态刷新信号；未验证时禁止真实派遣。
4. **测试证据有限：** 现有状态归一化测试使用合成 payload；它们验证代码路径，不证明真实 POI 数据字段或插件版本兼容性。
5. **任务时间字段仍待证据：** `api_mission` 的任务 ID 解析有合成测试支持，但任务时间槽位与单位尚未通过当前版本的真实数据确认，不能据此推算 `return_at`。

### 当前结论

- **静态接口契约：基本匹配，尚有验证器覆盖与 URL 凭据校验改进项。**
- **离线测试：只能证明合成数据和模拟协议行为。**
- **Windows / Poi 12.0.1 运行时兼容、真实舰队 payload、登录有效性和数据新鲜度：尚未验证。**
- 本次检查未发起本机 MCP 请求、未执行游戏操作、未安装插件、未修改本地文件。此文档更新只代表远程 GitHub 文档写入，不代表本地同步。

### 下一步

1. 修正 Nanodesu 只读验证器：增加 `const.$ships` 探测，并测试缺失/错误形状。
2. 统一 MCP 客户端和验证器的 URL 凭据拒绝规则，增加离线测试。
3. 运行 GitHub Actions，记录对应提交的实际 CI 结果。
4. 在用户现有 Poi 实例上进行只读预检；确认目标插件、端口和健康状态后，再由用户授权执行本机只读探测。
5. 捕获最小化且脱敏的真实 payload，验证三条路径、字段形状和数据刷新；在这些证据成立前，真实游戏派遣保持关闭。
