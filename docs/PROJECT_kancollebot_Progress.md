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

1. **已修复只读验证器覆盖不足：** Nanodesu 路径现已探测 `info.fleets`、`info.ships` 和 `const.$ships`，并对 `poi_describe` 明确使用 `info.fleets`；相关回归测试及 CI 已通过。
2. **已修复端点安全策略不一致：** MCP 客户端与验证器均拒绝 URL 内嵌凭据，并禁止自动跟随 HTTP 重定向，以免 loopback 请求被 3xx 引导至其他主机；相关回归测试及 CI 已通过。
3. **本地时间戳不等于游戏数据新鲜度：** `ObservedState.observed_at` 是读取时生成的观察时间。即使每次读取时间更新，也不能单凭它证明 POI Redux 数据来自当前登录会话或刚发生的游戏状态变化。必须独立验证登录/母港证据及状态刷新信号；未验证时禁止真实派遣。
4. **测试证据有限：** 现有状态归一化测试使用合成 payload；它们验证代码路径，不证明真实 POI 数据字段或插件版本兼容性。
5. **任务时间字段仍待证据：** `api_mission` 的任务 ID 解析有合成测试支持，但任务时间槽位与单位尚未通过当前版本的真实数据确认，不能据此推算 `return_at`。

### 当前结论

- **静态接口契约：基本匹配；验证器路径覆盖、URL 凭据校验和重定向边界已修复并通过 CI。**
- **离线测试：只能证明合成数据和模拟协议行为。**
- **Windows / Poi 12.0.1 运行时兼容、真实舰队 payload、登录有效性和数据新鲜度：尚未验证。**
- 本次检查未发起本机 MCP 请求、未执行游戏操作、未安装插件、未修改本地文件。此文档更新只代表远程 GitHub 文档写入，不代表本地同步。

### 下一步

1. 在用户现有 Poi 实例上执行只读预检，先确认目标插件、端口和健康状态。
2. 在明确授权后，捕获最小化且脱敏的真实 payload，验证三条路径、字段形状与数据刷新。
3. 独立确认登录/母港状态和数据新鲜度；缺少证据时保持真实派遣关闭。
4. 在用户现有 Poi 实例上进行只读预检；确认目标插件、端口和健康状态后，再由用户授权执行本机只读探测。
5. 捕获最小化且脱敏的真实 payload，验证三条路径、字段形状和数据刷新；在这些证据成立前，真实游戏派遣保持关闭。

### 2026-10-24 跟进修复与 CI 结果

- 已修复 `tools/verify_poi_bridge.py`：Nanodesu 只读验证现在也会请求 `const.$ships`，报告中单独列出 `master_ships` 结构摘要。
- 已修复 `PoiMcpClient.__init__()`：拒绝带用户名或密码的 endpoint URL，与只读验证器的规则保持一致。
- 新增回归测试：覆盖 MCP endpoint 凭据拒绝，以及 Nanodesu 验证器确实查询 `const.$ships`。
- 对应 GitHub Actions CI 已通过：
  - [MCP endpoint 凭据校验](https://github.com/wosafety0/KancolleBot/actions/runs/40066344757)
  - [Nanodesu 主数据路径探测](https://github.com/wosafety0/KancolleBot/actions/runs/40066348061)
  - [凭据拒绝回归测试](https://github.com/wosafety0/KancolleBot/actions/runs/40066365407)
  - [主数据路径回归测试](https://github.com/wosafety0/KancolleBot/actions/runs/40066370136)
- 当前第一轮修复代码提交：`f983910a2dec913003c793bf19fe9cc05f29a6b3`。CI 通过证明 GitHub Actions 中的自动化检查通过，不代表 Windows / Poi 12.0.1 运行时验证通过。
- 未进行本机 HTTP/MCP 请求、未触发游戏操作、未安装软件；本次代码和文档修改发生在 GitHub 远程分支，不能视为本地工作树已同步。

### 2026-10-24 第二轮安全与探测覆盖修复

- MCP 客户端与只读验证器均已禁用 HTTP 自动重定向，避免 loopback URL 的请求被 3xx 响应引导到其他主机。
- Nanodesu 验证器的 `poi_describe` 现在明确查询 `info.fleets`，与 `PoiExpeditionReader` 使用的路径一致。
- 新增回归测试，覆盖重定向拒绝及指定舰队描述路径。
- 对应 GitHub Actions CI 均通过：
  - [阻止 MCP 重定向](https://github.com/wosafety0/KancolleBot/actions/runs/40066496214)
  - [验证器 loopback 与路径修复](https://github.com/wosafety0/KancolleBot/actions/runs/40066500191)
  - [MCP 重定向回归测试](https://github.com/wosafety0/KancolleBot/actions/runs/40066504528)
  - [舰队描述路径回归测试](https://github.com/wosafety0/KancolleBot/actions/runs/40066507173)
- 本轮代码测试对应提交：`6bbd34a635fe2e1b03bca9dd3885526a610fd476`。CI 通过仅表示远程自动测试通过；本机 POI 实际兼容性和游戏会话新鲜度仍未验证。


### 2026-10-24 状态归一化边界校验

- 复核 `expedition_state_v2.py` 时发现 HP 校验此前没有拒绝负当前 HP 或当前 HP 大于最大 HP；布尔值最大 HP 也可能因 Python 中 `bool` 是 `int` 子类而通过类型检查。
- 已增加 fail-closed 校验：拒绝越界当前 HP，并显式拒绝布尔值最大 HP。
- 新增回归测试覆盖当前 HP 为负数、超过最大值，以及最大 HP 为布尔值。
- 对应 GitHub Actions CI 通过：[越界 HP 回归测试](https://github.com/wosafety0/KancolleBot/actions/runs/40066596931)、[布尔最大 HP 回归测试](https://github.com/wosafety0/KancolleBot/actions/runs/40066630335)。
- 最新相关代码/测试提交：`0659f8a573b939bd133c83cbc990271a45c56aaa`。这些测试验证输入校验逻辑；真实 POI payload 的字段范围仍须通过只读运行时捕获确认。


### 2026-10-24 Nanodesu 上游源码交叉检查

- 直接读取了公开仓库 [momocow/poi-plugin-nanodesu-mcp](https://github.com/momocow/poi-plugin-nanodesu-mcp) 的 `main` 分支；当时指向提交 [`2ba0a461ee44d64bf9d19b3467cf7565ad51b943`](https://github.com/momocow/poi-plugin-nanodesu-mcp/commit/2ba0a461ee44d64bf9d19b3467cf7565ad51b943)，提交时间为 2026-09-22。
- 上游 `package.json` 标记版本为 `0.1.0`，默认 MCP 端口为 `12450`。上游 `index.ts` 的生命周期逻辑包含主窗口检查、限时启动、状态面板错误报告和卸载时关闭 server。
- 上游 `server.ts` 默认绑定 `127.0.0.1`，提供 `GET /health` 和 `POST /mcp`，限制请求体大小，并验证 Host；health 测试明确覆盖空 store 时 `status: ok` 但 `storeReady: false`，因此 health 成功不能单独证明游戏数据已就绪。
- 上游测试还覆盖了无 CORS 头、端口文件权限/清理、端口绑定失败、路径解析、查询筛选和序列化截断。它们是上游测试证据，不等于用户本机安装版本已运行通过。
- 上游 Git tree 中 `src/server.ts` 和 `src/tools.ts` 的 blob 大小分别为 21,564 和 17,947 字节，与此前本机只读报告中的这两个文件大小一致；这只能作为版本线索，不能替代逐字节哈希比较，也不能证明所有本地文件都与上游一致。
- 当前结论仍为：上游源码与用户提供的核心实现片段总体一致；本机插件的完整版本身份和 Windows / Poi 12.0.1 运行兼容性仍未通过本地运行证据确认。没有发起本机 HTTP/MCP 请求，也没有执行游戏操作。


### 2026-10-24 缺失舰队字段的 fail-closed 修复

- 进一步审查发现：旧归一化逻辑在缺少 `api_id` 时会按数组位置推断舰队 ID；在缺少 `api_mission` 时会将任务状态默认为未出征。这两种推断都可能把未知状态误当成可派遣状态。
- 已修改为：舰队必须显式提供 `api_id`，且 `api_mission` 必须是至少 4 个元素的数组；缺失或形状异常时直接拒绝归一化，不再推断“未出征”。
- 新增回归测试覆盖缺失舰队 ID 和缺失任务数据。
- 对应 GitHub Actions CI 通过：[归一化修复](https://github.com/wosafety0/KancolleBot/actions/runs/40066850272)、[回归测试](https://github.com/wosafety0/KancolleBot/actions/runs/40066853537)。
- 当前代码/测试提交：`95f3f1bbc09fdaf08b9a5105fc090138a454899c`。这进一步收紧了离线数据校验，但真实 POI payload 是否始终包含这些字段仍需只读运行时证据确认。


### 2026-10-24 上游夹具揭示的主数据字段不匹配

- 从上游公开夹具 `test/fixtures/store.json` 解析到：舰船实例 `info.ships` 使用 `api_fuel` / `api_bull`；主数据 `const.$ships` 使用 `api_fuel_max` / `api_bull_max`。
- 该夹具的结构检查结果：2 个舰队均含 `api_id` 和至少 4 项的 `api_mission`；35 条舰船实例对应 35 条主数据记录，主数据引用均可解析；舰船 HP/当前燃料/当前弹药与主数据燃料/弹药上限字段均为数值。此统计仅描述上游静态夹具。
- 原归一化器错误地从主数据读取 `api_fuel` / `api_bull`，与上游夹具字段不符，会将实际有弹药的舰船误判为补给不足。此前合成测试沿用了错误字段名，未能发现此问题。
- 已将归一化器改为读取 `api_fuel_max` / `api_bull_max`，并更新测试数据；现有补给就绪测试能验证当前弹药与主数据弹药上限相等时为就绪。
- 更新后的测试提交 `0631f2a9ecff2c2d61af92964944a195fa5754df` 的 GitHub Actions CI 已通过：[CI run](https://github.com/wosafety0/KancolleBot/actions/runs/40066923707)。紧邻的纯代码提交 `ecc07142dd79f9d451e2542e69c806462371a92e` 在测试夹具尚未同步更新时失败；该中间提交失败原因已从 CI 日志确认，后续测试更新提交通过。
- 上游夹具是静态样本而非用户本机实时数据；字段名依据现有公开证据修正，但最终仍需在本机只读捕获中复核。


### 2026-10-24 读取器端到端离线契约测试

- 新增 `tests/test_poi_expedition_reader.py`，使用合成 MCP 文本响应覆盖 `PoiExpeditionReader` 的主要边界：正确的三条数据路径、主数据补给上限字段、截断响应拒绝、缺失任务字段 fail-closed、store 未就绪时停止，以及缺少必需工具时停止。
- GitHub Actions CI 已通过：[reader contract tests](https://github.com/wosafety0/KancolleBot/actions/runs/40067026502)。
- 测试提交：`b1a9384891f8b49ae4ed69db167ea1d7be691cf2`。
- 这些仍是离线合成响应测试；尚未证明 MCP 会话与用户本机 Nanodesu 插件能够完成真实握手，也没有证明本机 Redux store 的字段/刷新行为与上游夹具完全相同。


### 2026-10-24 持久化重复派遣保护

- 审查发现 journal 原先只按 `fleet_id + mission_id + observed_at` 判断重复。由于 `observed_at` 是每次读取时生成的本地时间，即使 Redux 缓存未变化，重启后也可能得到新的键，无法可靠阻止结果不明后的重试。
- 已修改 `ExpeditionActionJournal.reserve()`：在原子事务中，若同一舰队存在 `reserved`、`submitted` 或 `unknown` 记录，就拒绝新的预约，不再依赖新观察时间来绕过未决记录。
- `runner.py` 的阻止原因已改为明确说明存在未决 journal 记录；新增测试覆盖未决记录跨观察时间、跨任务 ID 的阻止，以及已确认记录允许后续新观察。
- GitHub Actions CI 已通过：[journal retry protection tests](https://github.com/wosafety0/KancolleBot/actions/runs/40067141815)。
- 测试提交：`9d534af977efcb4053f0b7a4ed967289a1031c1b`。
- 当前限制：未决记录会 fail-closed 阻止该舰队后续尝试；自动核对和清除未决记录的流程尚未实现，不能把“状态时间戳变新”当作自动解除条件。真实派遣仍保持关闭。


### 2026-10-24 真实操作的持久化 journal 强制门槛

- 发现 runner 在 `allow_live_actions=True` 且显式确认的情况下，仍允许 `journal=None`；这会使跨进程重复保护退化为单实例内存集合。
- 已增加 fail-closed 门槛：只要策略允许真实操作，必须注入持久化 journal；没有 journal 时返回 `journal_unavailable`，不调用 adapter 的派遣方法。
- 更新 runner 测试：成功派遣和结果不明场景使用临时 SQLite journal；新增无 journal 时禁止派遣的回归测试。
- GitHub Actions CI 已通过：[durable journal requirement tests](https://github.com/wosafety0/KancolleBot/actions/runs/40067331235)。该提交的完整单元测试共 **93 项通过**，日志显示 `Ran 93 tests ... OK`。
- 测试提交：`160c1ea9233d6561cbf2ada1793b10f696133ac8`。紧邻的纯代码提交因既有测试尚未同步到新安全契约而失败；随后的测试更新提交通过。
- 真实游戏操作仍默认关闭；此改动只加强未来启用操作时的前置条件，不代表真实派遣已实现或验证。


### 2026-10-24 Bridge 身份与生命周期复核

- 读取器现要求 `GET /health` 的 `name` 必须为 `poi-nanodesu-mcp`，再检查 `status` 与 `storeReady`；错误服务即使返回通用 `status: ok` 也会被拒绝。对应测试已通过：[bridge identity test](https://github.com/wosafety0/KancolleBot/actions/runs/40067608925)。
- 对公开上游 `index.ts` / `src/server.ts` 的静态复核确认：插件只在 POI 主窗口启动，启动上限为 15 秒，启动错误会显示在状态面板；服务默认绑定 `127.0.0.1`，端口占用时不自动切换端口，卸载时关闭 server；health 的 `storeReady` 只检查 store 是否含 `info`，不代表已登录或数据新鲜。
- **上游待验证边缘情况：** `withTimeout()` 只让等待方超时，并不会取消原始启动 Promise。如果底层 server 启动在超时后才完成，外层可能已丢弃返回的 handle，存在 listener 未被正常关闭的可能。当前没有复现证据；这属于上游生命周期风险，不代表本机已经发生。
- 上游来源：[index.ts](https://github.com/momocow/poi-plugin-nanodesu-mcp/blob/main/index.ts)、[server.ts](https://github.com/momocow/poi-plugin-nanodesu-mcp/blob/main/src/server.ts)、[timeout.ts](https://github.com/momocow/poi-plugin-nanodesu-mcp/blob/main/src/timeout.ts)。
- 身份校验对应代码提交：`2f58ae3bfa93665b8a9710ac1df195ef288fec8f`。紧邻的纯代码提交因测试尚未同步新 health 契约而失败；后续测试更新提交通过。
