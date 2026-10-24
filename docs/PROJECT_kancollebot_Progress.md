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

- 最新身份校验测试提交 `2f58ae3bfa93665b8a9710ac1df195ef288fec8f` 的完整单元测试共 **94 项通过**（`Ran 94 tests ... OK`）。

### 2026-10-24 工作连续性规则与下一步阻塞项

#### 必须执行的每轮归档规则

- 每轮有持久价值的工作完成后，必须主动将结论、证据链接、未解决问题、风险和下一步写入本仓库的项目进度文档或对应技术文档；不得等待用户提醒。
- 对可能因上下文丢失而遗失的项目约束，应同步维护项目规则/指令文档，并将稳定的跨会话工作偏好保存到长期记忆。
- 写入前先读取当前远程文件和分支状态；写入后重新读取或核对提交结果，确认远程内容确实存在。没有执行并核验时，严禁声称 GitHub 已更新。
- 明确区分 GitHub 远程提交、本机文件修改、本机运行验证和 CI 测试；其中任何一项都不能代替其他项。远程更新不代表本地工作树已同步。
- 记录应优先保留可复核的事实：提交 SHA、CI 链接、检查范围、实际执行与未执行的动作、剩余阻塞项。不得将静态审查或合成测试描述为实机验证。

#### 下一步：POI 12.0.1 本机只读运行时证据

当前核心阻塞不是继续增加合成测试，而是取得用户现有 POI 实例的只读运行时证据。按以下顺序验证：

1. **服务与工具清单：** 只读检查 Nanodesu 的 `GET http://127.0.0.1:12450/health`，记录 HTTP 状态、`name`、`status`、`storeReady`；再通过 `POST /mcp` 执行 MCP 初始化及 `tools/list`，记录实际工具名称和协议版本。只允许调用读取类工具，禁止调用 `poi_hensei_save`、编队修改、派遣或其他写入/游戏操作工具。
2. **三条数据路径：** 只读查询 `info.fleets`、`info.ships`、`const.$ships`，保存脱敏后的结构摘要（类型、条目数、关键字段是否存在、是否截断），不需要归档整份游戏状态。
3. **新鲜度与登录信号：** 在不操作游戏的前提下，间隔一段时间重复读取同一只读路径，比较可观察字段及时间变化；检查插件/POI 已暴露的状态字段或日志中是否存在可靠登录信号。不得仅凭 health 成功或 `storeReady=true` 推断已登录、数据新鲜或游戏状态完整。
4. **结论分层：** 分别记录服务握手、工具清单、路径结构、字段兼容性、数据刷新和登录信号的 PASS/FAIL/UNKNOWN。静态上游夹具和 GitHub Actions 离线测试只能作为辅助证据。
5. **安全边界：** 本步骤不关闭/重启 POI，不修改文件或配置，不安装软件，不发送游戏操作，不请求任何写工具，不改变系统代理；若必须由本机执行脚本采集证据，先完成只读预检，并由用户明确触发执行。

#### 当前状态（截至本条记录）

- 远程分支：`dev/expedition-closed-loop-mvp`；PR #5 仍为 Draft，未合并。
- 离线测试：最新记录为 94 项通过；CI 链接：https://github.com/wosafety0/KancolleBot/actions/runs/40067608925
- 上游公开源码和夹具支持部分字段契约判断，但不等于本机运行证据。
- 尚未确认本机 Nanodesu health / 实际 `tools/list` / 三条实时路径 / 登录信号 / 数据新鲜度。当前状态均不得标记为实机 PASS。
- 此次仅更新 GitHub 远程文档；未修改或同步本地工作树。


### 2026-10-24 POI 12.0.1 本机只读运行时预检结果

#### 实际执行证据

- 用户在本机 PowerShell 5.1.26100.9711 执行只读预检，报告时间为 `2026-10-24 13:52:15 +08:00` 至 `13:52:28 +08:00`。
- 检查目标为 `127.0.0.1:12450`。报告明确记录：未执行游戏操作、未调用写入工具、未修改配置/文件、未改变代理或系统设置。
- `GET /health` 成功：`name=poi-nanodesu-mcp`、`status=ok`、`storeReady=True`；health 字段只有 `name, port, status, storeReady, version`，没有明显登录状态字段。身份检查 PASS。
- MCP 初始化成功，协议版本 `2025-03-26`，服务器名 `poi-nanodesu-mcp`、版本 `0.1.0`；`tools/list` 返回 5 个工具：`poi_battle`、`poi_describe`、`poi_get`、`poi_hensei_save`、`poi_lookup`。所需只读工具 `poi_describe` 与 `poi_get` 存在。仅列出工具，不代表调用了写入/战斗工具。
- `poi_describe(info.fleets)` 成功，返回 `kind=array`，并报告路径 `info.fleets`。
- `info.fleets`：PASS，`kind=array`，3 条记录，`truncated=False`；样本字段包括 `api_member_id, api_id, api_name, api_name_id, api_mission, api_flagship, api_ship`。
- `info.ships`：PASS，`kind=object-map`，91 条记录，`truncated=False`。
- `const.$ships`：FAIL，读取器检测到响应 `truncated=True`，因此按 fail-closed 规则拒绝该路径。此处是本轮主要阻塞项；尚不能确认截断是数据本身、序列化字节/条目限制还是查询参数导致，需要结合 Nanodesu 的查询/序列化实现继续静态检查，并在不扩大读取范围的前提下设计安全验证。
- 10 秒间隔复读中，`info.fleets` 和 `info.ships` 的 SHA-256 均保持不变；这只能证明该时间窗口内返回内容未变，不能证明数据陈旧，也不能证明刷新机制正常。由于 `const.$ships` 首次检查失败，没有可比较的成功基线。
- 登录状态：UNKNOWN。health 没有明显登录字段，`storeReady=True` 仅证明插件认为 Redux store 可用，不能证明当前已登录或处于母港。

#### 本轮结论

- 服务健康、MCP 握手、工具枚举和两条运行时路径已获得本机实测 PASS 证据。
- 数据路径完整性为 2/3；`const.$ships` 的截断使当前读取器无法完成舰船主数据归一化。不得绕过截断检查或把部分主数据当作完整数据。
- 数据新鲜度与登录信号尚未验证；10 秒哈希不变不能单独作正面或负面判断。
- **MVP 实机兼容性仍未通过**：核心阻塞从“完全没有本机运行证据”缩小为“主数据路径截断 + 登录/新鲜度证据缺失”。远征闭环仍未验证。

#### 下一步

1. 只读检查当前 Nanodesu 上游/本机实现中的 `const.$ships` 查询投影、序列化条目上限与字节上限，确认 `truncated` 的具体触发条件；优先通过现有 GitHub 源码和测试研究，不要求用户再次上传整份插件目录。
2. 设计最小化的只读分段查询/字段投影验证，前提是插件公开 API 明确支持且每段都能检测完整性；不允许通过忽略 `truncated` 标志来“修复”。
3. 继续寻找可靠登录/母港信号；若不存在可验证信号，明确把登录态作为未解决前置条件，不开放真实派遣。
4. 对每次新的运行时结果继续按项目规则归档，并明确区分本机实测、静态分析、CI 与远程文档写入。

#### 证据来源与边界

- 运行时数值、哈希和状态来自用户在本对话提供的本机预检报告；未由助手直接连接用户电脑。
- 本次文档记录属于 GitHub 远程更新，不代表本地工作树已同步。


### 2026-10-24 主数据截断根因定位：Nanodesu 提供定向查询接口

#### 上游源码证据

- 收到本机运行时报告后，复查公开上游 momocow/poi-plugin-nanodesu-mcp 的 src/tools.ts、src/query.ts、src/serialize.ts 与 src/paths.ts。当前上游 main 对应文件 blob：tools.ts b757db4d91e4a52d169c2496f3983920e90a717b；serialize.ts 25e4e4150284e8d4d97c4daa2743102ca9ffbcd7；paths.ts 4da5270a4fd15c6f6f6416ddf30b3ab1343cf369。
- poi_get 默认 limit=200、maxBytes=65536，硬上限为 262144 字节；超出字节预算时会返回 truncated=true 的部分集合。上游注释明确指出 const.$ships 整表约 1.6 MB，远大于允许的最大响应体，因此直接 poi_get(path="const.$ships") 整表读取不可能在默认接口限制内完整返回。
- 上游提供 poi_lookup 专用主数据查询：kind="ships" 映射到 const.$ships，参数为显式 ids，每次最多 200 个 ID，并可通过 select 只返回需要的字段。此接口返回以 ID 为键的记录对象，而不是 poi_get 的 kind/items/truncated 集合封装。
- poi_get 的 select 是逐记录字段投影；但当前读取器需要的只是舰队实际引用的主数据记录，优先评估 poi_lookup(kind="ships", ids=..., select=["api_fuel_max","api_bull_max"])，而不是提高全表字节上限或忽略截断标志。
- info.ships 的舰船实例字段 api_ship_id 可用来确定主数据 ID；应从 info.fleets.api_ship 引用的舰船实例中收集去重后的 api_ship_id，只查询当前舰队所需主数据。缺少 ID、缺少返回记录或字段缺失时继续 fail-closed。
- 以上根因定位来自上游静态源码。尚未在用户本机调用 poi_lookup 验证当前安装版本的实际响应形状，因此不能将修复或本机兼容性标为完成。

#### 实施建议与安全边界

1. 修改 PoiExpeditionReader：先读取舰队与舰船实例，再从实际被舰队引用的舰船中收集唯一主数据 ID，调用只读 poi_lookup 获取对应主数据；不再对整张 const.$ships 调用 poi_get。
2. 为 lookup 结果建立独立严格校验：返回值必须是数值 ID 键对象；每个被引用的主数据 ID 必须存在；所需燃料/弹药上限字段必须存在且类型有效。
3. 增加离线回归测试覆盖 ID 去重、缺失主数据、错误响应形状与最小字段投影；之后先由 CI 验证，再通过用户本机只读验证器确认实际 poi_lookup 兼容性。
4. 不调用 poi_battle 或 poi_hensei_save；不提高全表响应限制，不绕过 truncated 检查，不执行游戏操作。


### 2026-10-24 POI lookup runtime check

- User-provided local PowerShell 5.1 report: 13:57:37-13:57:39 +08:00; endpoint 127.0.0.1:12450.
- Read-only boundary held: no game actions, write tools, file/config changes, proxy/system changes, or full master-table read. No master IDs or field values printed.
- Health precondition and MCP initialization passed; protocol 2025-03-26. Read tools poi_get and poi_lookup were present.
- Runtime counts: 3 fleets, 91 ship instances, 12 fleet-referenced ship instances and 12 unique master IDs.
- poi_lookup for those IDs with select api_fuel_max and api_bull_max returned an object map with 12 records, 0 missing records and 0 invalid required fields. Result PASS.
- This confirms the installed instance supports the targeted lookup call and response shape for these 12 references. The reader code has not yet been changed; broad compatibility is not yet proven.
- Current runtime evidence: health, MCP handshake/tool list, info.fleets, info.ships and targeted master lookup pass. Login state remains UNKNOWN. No changes across a 10-second observation window does not establish freshness. End-to-end expedition closure remains unverified.
- Next: update PoiExpeditionReader to derive and deduplicate referenced api_ship_id values and use poi_lookup; add tests for duplicate/empty IDs, missing records, wrong shapes and invalid required fields; run CI. Continue investigating login/home-port signal and refresh evidence. Do not enable live dispatch until these gates are established.
- Evidence was supplied by the user from local execution; assistant did not directly access the user's computer. This entry is a remote GitHub documentation update only and does not sync the local worktree.


### 2026-10-24 POI targeted master lookup implementation

- Runtime evidence from the user's local read-only report established that `poi_lookup(kind="ships", ids=[...], select=["api_fuel_max","api_bull_max"])` returned all 12 requested master records with no missing records and no invalid required fields.
- Updated `src/kancollebot/adapters/poi/expedition_reader.py` to require the read-only `poi_lookup` tool, derive unique master IDs from fleet-referenced ship instances, request only the two required master fields, and fail closed on missing records, malformed object-map responses, or invalid required field values. It no longer requests the full `const.$ships` table through `poi_get`.
- Source commit: `b1433606f8472227338a44715a21ec26dee468af`. The remote file was fetched back and verified at blob `289cb4388ef22522cfc088a791b04720a7c78d6d`.
- Updated `tests/test_poi_expedition_reader.py` to cover targeted lookup, deduplication of repeated master IDs, missing records, wrong lookup shape, invalid required fields, missing lookup tool, and truncated fleet/ship responses.
- Test commit: `4d15e3c3e5aef20825009dadb7f4b03f40a0090c`. The remote test file was fetched back and verified at blob `9ef299371a4fb2033a0395d1aa3361f5f5df5c25`.
- CI status: not yet confirmed. The workflow-run lookup returned no PR-triggered runs for these commits; do not interpret that as a passing test run. Follow up by inspecting the branch's actual Actions runs or running the test workflow through an available verified path.
- Runtime status: this code change is based on a successful local `poi_lookup` smoke check for 12 referenced IDs, but the modified reader has not yet been executed against the user's live Poi instance. Login-state evidence and data freshness remain UNKNOWN; end-to-end expedition closure remains unverified; live dispatch stays disabled.
- Scope: GitHub remote files were changed and read back successfully. No claim is made that the user's local working tree was updated. No local machine access, game actions, write tools, configuration changes, or system/proxy changes were performed by the assistant.


### 2026-10-24 Targeted lookup CI failure follow-up

- The GitHub Actions CI runs for test commit `4d15e3c3e5aef20825009dadb7f4b03f40a0090c` and subsequent progress-document commit `c07c4790aff7e52e8063fec5f91c8096edbe4dd2` both completed with **failure**:
  - [Test commit CI](https://github.com/wosafety0/KancolleBot/actions/runs/40069110427)
  - [Latest branch CI](https://github.com/wosafety0/KancolleBot/actions/runs/40069142123)
- GitHub reports the failed check as job `test` with two annotations, but the available log/annotation retrieval paths did not expose the failure details in this session. Root cause is therefore **UNKNOWN**; do not claim tests pass.
- The branch head at the time of this note is `c07c4790aff7e52e8063fec5f91c8096edbe4dd2`. The source and test files were successfully written remotely and fetched back, but require CI failure diagnosis before the implementation can be accepted.
- Next: retrieve the two check annotations or job logs through a supported GitHub endpoint, identify the exact failing assertion/import/test, make a minimal fix, then confirm a green CI run. Do not run live dispatch. Local runtime verification of the modified reader, login-state proof, freshness proof, and end-to-end expedition closure remain outstanding.


### 2026-10-24 CI failure retrieval attempt (follow-up)

- Re-queried both failed workflow jobs via the GitHub Actions job-summary endpoint:
  - Run `40069110427`, job `120798507550`: `conclusion=failure`; step-summary endpoint returned an empty list.
  - Run `40069142123`, job `120798593159`: `conclusion=failure`; step-summary endpoint returned an empty list.
- The job-list endpoint confirms both runs contain a single failed `test` job, but the connector returns `steps=null`; the step-summary wrapper also returns `steps=[]`. Job-log download previously returned `BlobNotFound`, and the annotations endpoint was rejected by the connector URL allowlist. Thus no failing step name, traceback, annotation text, or assertion has been retrieved.
- Re-read the remote reader and test source for static inspection. No uniquely proven CI root cause emerged from this review; any code edit now would be speculative. **CI remains FAIL / root cause UNKNOWN.** No rerun was triggered to avoid spending Actions minutes without a diagnosis.
- Next: obtain the run's failing annotation/log through a supported route, then make the smallest evidence-based fix and verify one CI run. Until then, the targeted lookup reader remains unaccepted for CI despite the successful separate local lookup smoke test.
- No source or test code was modified in this follow-up. This entry records a remote documentation update only; it does not synchronize the local working tree. Live game actions remain disabled.


### 2026-10-24 CI evidence retrieval attempt 2

- Checked both failed runs for downloadable artifacts: neither run exposes any artifacts.
- Queried combined commit status for the source/test commit and the two subsequent documentation commits; the status endpoint returned no legacy status entries. This does not override the failed Actions job conclusions already recorded.
- Available GitHub connector inventory exposes job logs, step summaries, run jobs, and artifacts, but no dedicated check-run annotation retrieval tool. Current job-step summaries are empty, run-job payloads expose `steps=null`, and log download previously returned `BlobNotFound`.
- The exact CI failure remains unavailable through these supported paths. No source change or workflow rerun was made; speculative edits would be unsafe and a rerun would consume Actions usage without clarifying the existing failure.
- Next practical evidence path: inspect the failed job details/logs in GitHub Actions UI, or provide the failing annotation/traceback text; then repair based on the actual error and verify one run. Existing remote source/test commits remain unaccepted until CI is green.


### 2026-10-24 CI failure source review (attempt 3)

- Repeated the log retrieval through the GitHub connector for failed job `120798593159`; it again returned `BlobNotFound`. The run's jobs endpoint confirms one failed `test` job and no exposed step records.
- Direct fetch of the check-run annotations/check-run detail endpoints was rejected by the connector's URL allowlist. The run artifact endpoint returned `total_count=0`.
- Re-fetched the branch's `src/kancollebot/adapters/poi/expedition_reader.py` (blob `289cb4388ef22522cfc088a791b04720a7c78d6d`), `tests/test_poi_expedition_reader.py` (blob `9ef299371a4fb2033a0395d1aa3361f5f5df5c25`), `expedition_state_v2.py` (blob `d2ec4502c9d221d3b3b7cea0ff20a778d1bfaa6a`), and `.github/workflows/ci.yml` (blob `c4ac259d0186d6937c7e3a41e4a9b5ffd1bc48d5`) for static review. This confirms the files are retrievable but does not identify the failing CI step or root cause.
- Decision: **no speculative source/test edit and no CI rerun**. CI remains FAIL / root cause UNKNOWN; the targeted lookup implementation is not accepted until the actual failure is retrieved and a green run is observed.
- This is a progress-document-only remote update. It does not prove local worktree synchronization. No live game action, configuration change, proxy/system change, or workflow rerun was performed.


### 2026-10-24 CI runner-start evidence review (attempt 4)

- Retrieved the raw workflow-run and job JSON directly from the GitHub REST endpoints, which exposed evidence not present in the connector's summarized job wrapper.
- Run `40069110427` / job `120798507550`: created `2026-10-24T06:10:00Z`, completed `06:10:03Z`, `conclusion=failure`, `runner_id=0`, empty `runner_name`, and `steps=[]`.
- Run `40069142123` / job `120798593159`: created `2026-10-24T06:10:25Z`, completed `06:10:27Z`, `conclusion=failure`, `runner_id=0`, empty `runner_name`, and `steps=[]`.
- The log-download endpoint returns `BlobNotFound`; the workflow-run log URL returns an empty body; the check-run detail/annotation URLs remain blocked by the connector allowlist. The failure therefore appears to occur **before a runner executes any workflow step**, rather than being a demonstrated Python test assertion failure. This is a strong inference from metadata, not a confirmed GitHub failure message; exact cause remains UNKNOWN.
- Decision: do not edit source/tests or rerun Actions based on this evidence. First inspect the GitHub Actions UI's job/check annotations or account Actions/billing/usage restrictions. The previous account context reported Actions budget blocking, which is a plausible lead but is not proven by these job records.
- Remote documentation-only update. No source changes, rerun, live game action, local worktree sync, or system/proxy modification was performed.


### 2026-10-24 CI runner-start evidence review (attempt 5)

- Rechecked the latest 10 workflow runs on branch `dev/expedition-closed-loop-mvp`. The five newest runs, including the documentation-only commits at `58c4e3339e46e331e869a1624d090f25a5e11dba`, `06c41328b61a8a51670aff07bccb32c0a8850e73`, `65b89cdf568f9885b883d5fc1533cfcdff0fcd0b`, and `c18ce913daff9a95817c909be0265bef76ade586`, all failed in about three seconds with `runner_id=0`, empty `runner_name`, and zero steps.
- The most recent run is [run 40071161938](https://github.com/wosafety0/KancolleBot/actions/runs/40071161938), job `120804691695`; its raw job JSON has the same no-runner/no-step signature. This shows the failure is not isolated to the targeted-lookup source/test commit: documentation-only pushes also trigger the same pre-step failure pattern.
- Workflow configuration remains ordinary `ubuntu-latest` with checkout, Python 3.12 setup, compileall, and unittest steps. No Python step has been shown to execute in these failed runs.
- Conclusion: a repository/account/Actions runner-start restriction or infrastructure problem is now a stronger lead than a code-level test failure. Exact cause remains **UNKNOWN** because check annotations and logs are still inaccessible through the available read-only connector routes. Do not assert that billing/budget is the cause without a GitHub UI message.
- Decision: do not edit application code or trigger another run. Inspect the latest run's UI annotations and repository/account Actions usage/billing restrictions before retrying. This is a remote documentation update only; no local sync, source modification, workflow rerun, game action, or system/proxy change was performed.

### 2026-10-24 CI runner allocation comparison (attempt 6)

- Compared raw job metadata from recent failing run [40071865003](https://github.com/wosafety0/KancolleBot/actions/runs/40071865003) with earlier successful runs.
- Latest failing job 120806843621 completed in about three seconds with conclusion=failure, runner_id=0, empty runner_name, and steps=[].
- Earlier successful run [40067331235](https://github.com/wosafety0/KancolleBot/actions/runs/40067331235) used runner_id=1000001830 and executed the normal sequence: Set up job, checkout, Python setup, compileall, unit tests, and cleanup. Another successful run [40066923707](https://github.com/wosafety0/KancolleBot/actions/runs/40066923707) used runner 1000001811 and completed all five main steps.
- This comparison strengthens the conclusion that recent failures occur before runner allocation/step execution, rather than showing a Python test regression. It still does not identify the exact platform error or prove a billing/budget cause.
- CI configuration is unchanged: ubuntu-latest, Python 3.12, compileall, then unittest. No source-level fix is justified by the currently available failure evidence.
- Decision: keep application source/tests unchanged and do not trigger further Actions runs until the GitHub UI exposes the job-level failure message or Actions service/account restrictions are verified.
- This entry is a remote progress-document update only. No local worktree synchronization, source edit, workflow rerun, live game action, or system/proxy modification was performed.


### 2026-10-24 Expedition closed-loop readiness and next plan

- User-facing status: the real in-game expedition closed loop is **not ready to run yet**. Existing code and prior offline-test evidence do not establish live Poi compatibility or a successful dispatch/confirmation cycle.
- Verified/previously reported foundations: Poi MCP health and read-only tool discovery passed; `info.fleets` and `info.ships` returned data; a targeted `poi_lookup(kind="ships")` smoke check returned 12 referenced master records with no missing/invalid required fields. The updated `PoiExpeditionReader` itself has not been validated against the live Poi instance.
- Outstanding gates, in order:
  1. Restore a trustworthy offline verification path: inspect the latest GitHub Actions UI failure detail or run the existing tests locally. Recent Actions failures show no runner allocation and no steps; the exact platform/account cause remains unknown, so do not attribute them to code or billing without evidence.
  2. Run the branch's unit tests and fix only evidence-backed failures; do not trigger more Actions runs until the runner-start restriction is understood.
  3. Against the live Poi MCP, read only: verify tool schemas/response shapes, execute the updated reader, validate fleet/ship/master-data joins, and fail closed on truncation or missing data.
  4. Establish a reliable in-port/login signal and data-freshness signal; a successful health check or unchanged snapshot alone is insufficient.
  5. Inspect and verify the CDP target/WebSocket path and the exact dispatch UI interaction contract without clicking or submitting in the game.
  6. Add/verify post-dispatch readback and confirmation handling, including timeout/unknown-result behavior and persistent action-journal reconciliation. If the outcome is uncertain, stop rather than retry blindly.
  7. Only after all prior gates pass, request explicit user authorization for a tightly bounded single-fleet live test; then confirm the result by re-reading game state. Keep live dispatch disabled until that authorization and verification.
- Current decision: continue with offline tests and read-only integration checks first. No real expedition was run; no game action, local machine change, proxy/system change, or local worktree sync is claimed by this documentation update.
- CI and live-runtime blockers are separate: a blocked Actions runner does not itself prove the expedition code is wrong, and a green offline CI run would not by itself prove the live expedition loop works.


### 2026-10-24 Local test preflight blocked by unexpected project path

- User-provided Windows PowerShell 5.1 read-only report at 2026-10-24 15:10:48 +08:00:
  - `D:\Project_KancolleBot` exists.
  - `D:\Project_KancolleBot\05_Docs` does not exist; expected `PROJECT_RULES.md` was not found.
  - No `PROJECT_RULES.md` candidate was found in the root or expected docs directory.
  - Git reports: `fatal: not a git repository (or any of the parent directories): .git`.
  - No file modifications, network requests, game actions, or access to the prohibited legacy source directory occurred.
- Result: **local offline tests were not started**. This report does not establish that project files were deleted; the specified path may be an incomplete directory, moved checkout, or wrong location. The cause remains unknown.
- Next step: perform a strictly shallow, read-only inventory of the project root and its immediate parent, including hidden entries and the presence of `.git`; do not recursively search the drive, recreate directories, clone over the path, or access `D:\KancolleBot\Source`. Locate the authoritative checkout and `05_Docs\PROJECT_RULES.md` before reading rules or running tests.
- Remote documentation update only. This does not modify or synchronize the local machine. Live expedition dispatch remains disabled.


### 2026-10-24 Shallow inventory identifies Cloud-only directory

- User-provided read-only inventory at 2026-10-24 15:13:33 +08:00 confirms `D:\Project_KancolleBot` exists but contains exactly one immediate child: directory `Cloud`.
- The path has no `.git`, `05_Docs\PROJECT_RULES.md`, `src`, `tests`, or `.github\workflows\ci.yml`. The inventory also listed immediate children of `D:\`; it did not perform recursive search.
- No file changes, network requests, game actions, or access to `D:\KancolleBot\Source` occurred.
- Interpretation: the configured project path is not currently a recognizable checkout. This does **not** prove that source files were deleted; the `Cloud` child may be a sync/workspace entry or unrelated directory.
- Next step: inspect only the `Cloud` directory's attributes/reparse-point metadata and its immediate children. Do not recurse across the drive, clone into this path, or run tests until the authoritative checkout and local `05_Docs\PROJECT_RULES.md` are located.
- Remote progress documentation only; no local sync or source changes are claimed. Live dispatch remains disabled.


### 2026-10-24 Cloud child identified; checkout not yet verified

- User-provided read-only report at 2026-10-24 15:14:24 +08:00: `D:\Project_KancolleBot\Cloud` is an ordinary directory (Attributes=Directory; no LinkType/Target reported) containing exactly one immediate child directory, `KancolleBot-dev-poi-page-health-gate-20261023`.
- No recursion, file-content reads, file modifications, network requests, game actions, or access to `D:\KancolleBot\Source` occurred.
- The child name suggests a candidate working copy/snapshot but does not prove it is a Git checkout or authoritative project. Next step is a shallow inventory of that child's immediate entries and existence checks for `.git`, `05_Docs\PROJECT_RULES.md`, `src`, `tests`, and the CI workflow. Do not read source/rules contents, run tests, clone, or modify until the candidate is identified and the authoritative project rules can be read.
- Remote progress documentation only; no local changes or live game actions are claimed. Live dispatch remains disabled.


### 2026-10-24 User clarified Cloud is a historical GitHub sync directory

- User clarified that `D:\Project_KancolleBot\Cloud` contains a directory previously synchronized locally from the GitHub repository; it is not an arbitrary unknown directory.
- The currently observed path `D:\Project_KancolleBot` itself contains only `Cloud` and is not a Git worktree. The child `Cloud\KancolleBot-dev-poi-page-health-gate-20261023` remains a candidate historical sync copy; its internal structure and Git metadata have not yet been inspected.
- Plan: shallowly inspect that candidate's first-level entries and expected project markers, then read its project rules before any source/test work. Do not overwrite it, clone into it, or assume it is current merely from the name. No recursive search, file changes, network requests, game actions, or access to `D:\KancolleBot\Source` are authorized/performed by this note.
- Remote progress-document update only. Live expedition dispatch remains disabled.


### 2026-10-24 Historical sync candidate confirmed as project-shaped Git checkout

- User-provided shallow inventory at 15:15:28 +08:00 confirms candidate `D:\Project_KancolleBot\Cloud\KancolleBot-dev-poi-page-health-gate-20261023` contains `.git`, `.github`, `data`, `docs`, `src`, `tests`, `upload`, `.gitignore`, `pyproject.toml`, and `README.md`; expected CI workflow and `docs\PROJECT_kancollebot_Progress.md` exist.
- The candidate does not contain `05_Docs\PROJECT_RULES.md`. This is consistent with a differently structured historical GitHub sync copy; it does not satisfy the local project rule-file requirement by itself.
- Next: inspect Git branch/status and commit identity read-only, and check only root/docs for any candidate rules file. Do not reveal credentials from remote configuration, read source files, modify files, run tests, or access `D:\KancolleBot\Source` before resolving the applicable rules.
- No local modifications, network requests, game actions, or test execution occurred. This is a remote progress-doc update only; live dispatch remains disabled.


### 2026-10-24 Next preflight authorized: candidate Git metadata and rules-name discovery

- User authorized continuation with `1`.
- Next local step is metadata-only inspection of the historical sync candidate: Git top-level, current branch, short worktree status, HEAD hash/subject, remote names only (no remote URLs), and immediate root/docs filenames matching rule-related names.
- No source content is to be read, no tests run, no network operations, and no changes made. If a plausible rule file is found, read and validate it before proceeding to tests or code changes. If none is found, pause and ask for the authoritative rules location rather than assuming the remote progress document substitutes for local project rules.
- Live expedition dispatch remains disabled. This plan note is a remote documentation update only.


### 2026-10-24 Local candidate metadata checked; current branch mismatch and policy gate

- User-provided read-only metadata report for `D:\Project_KancolleBot\Cloud\KancolleBot-dev-poi-page-health-gate-20261023`:
  - Git top-level resolves to that candidate path.
  - Local branch: `dev/poi-page-health-gate-20261023`.
  - HEAD: `4df92ddef35ac4b28a473b16c1eea957251292e4`, subject `docs: record GitHub-only project directory policy`.
  - `git status --short` returned no entries (clean worktree).
  - Remote name is `origin`; its URL was intentionally not printed.
  - No rule-related filenames were found at candidate root or immediate `docs` level.
- Read the repository's `docs/PROJECT_kancollebot_MASTER.md` from the candidate branch (blob `2921d78cf0abd8e25c12c8dd57526d4fe82c1cca`). It states that GitHub is the authoritative project directory, local copies were deleted, and Windows work must use a separate new directory cloned from the specified GitHub repository/branch without overwriting existing paths, then verify HEAD and required files. It also reiterates no access to `D:\KancolleBot\Source`.
- Compared with active development branch `dev/expedition-closed-loop-mvp`, whose master document blob is `91186c1e8520c92d9999bef4a98172a6eb24aae6`; that branch's master document does not yet include the local-copy policy paragraph. The inspected local candidate is therefore a clean historical branch, not the current expedition branch.
- Decision: do not run tests against the historical candidate. To run the active branch locally, the next step requires explicit authorization to create a separate, new directory and clone the exact branch; no existing path will be overwritten. No local changes, tests, game actions, or access to `D:\KancolleBot\Source` occurred. Remote documentation update only; live dispatch remains disabled.


### 2026-10-24 User authorized fresh local clone and offline tests

- User approved continuation after the historical local candidate was found clean but on branch `dev/poi-page-health-gate-20261023`, with HEAD `4df92ddef35ac4b28a473b16c1eea957251292e4`; that candidate is not to be reused as the test checkout.
- Current target branch resolved via GitHub API: `dev/expedition-closed-loop-mvp`, expected HEAD at authorization time `b7a74e8773c4cde00bf0b7761681939770e6b86d`.
- Plan for the user-run Windows script: create a separate new checkout at `D:\KancolleBot-Worktrees\expedition-closed-loop-mvp-20261024` only if the destination does not exist; clone only the target branch; verify branch and exact HEAD; read `docs\PROJECT_kancollebot_MASTER.md` before tests; run the repository's existing offline CI commands (compileall and unittest discovery) using an already installed Python >=3.11, with no package installation.
- The script must stop before tests if the destination already exists, clone fails, HEAD/branch differs, rules file is missing, or Python is unsuitable. It must not change global settings, touch the historical Cloud copy, access `D:\KancolleBot\Source`, or perform game actions. Generated bytecode inside the new checkout may be created by compileall.
- This note records the plan only. Local clone and test execution have not yet happened. Live expedition dispatch remains disabled.


### 2026-10-24 Fresh clone attempt stopped before rules and tests

- User-provided PowerShell 5.1 report at 15:35:49 +08:00 shows the attempt ended with `OVERALL_RESULT: FAILED`, `CLONE_CREATED: False`, `RULES_READ: False`, `TESTS_STARTED: False`; both test exit codes remain `-1`.
- The report records only `Cloning into 'D:\\KancolleBot-Worktrees\\expedition-closed-loop-mvp-20261024'...` as an error and omits the clone exit code. Therefore the underlying Git/network error is **not established**. A likely PowerShell 5.1 interaction is that native Git stderr became a terminating error under `$ErrorActionPreference = 'Stop'`; this is a hypothesis, not a confirmed root cause.
- Do not immediately rerun clone: the destination may exist as a partial directory. Next step is a shallow read-only inspection of the exact target path (existence, immediate entries, `.git` presence, and if present `git rev-parse`/status only), without deleting or overwriting anything. Then check the latest remote branch tip before designing a corrected script.
- GitHub branch metadata currently reports `dev/expedition-closed-loop-mvp` at `e5237f5dc1efe6c890ca6124c99e013a96c3faf7`, a documentation-only commit whose parent is the previously pinned `b7a74e8773c4cde00bf0b7761681939770e6b86d`. The earlier script's expected HEAD is now stale for the branch tip.
- No tests ran; project rules were not read locally; no package was installed; no game action, global setting change, historical Cloud copy modification, or access to `D:\\KancolleBot\\Source` is reported. Clipboard verification is non-blocking per user preference.
- This entry is a remote progress-document update only; it does not establish any local filesystem change or successful clone. Live expedition dispatch remains disabled.


### 2026-10-24 Clone target inspection resolves the clone outcome

- User-provided read-only inspection at 15:40:25 +08:00 confirms `D:\\KancolleBot-Worktrees\\expedition-closed-loop-mvp-20261024` is a valid Git worktree with the expected branch `dev/expedition-closed-loop-mvp`, HEAD `e5237f5dc1efe6c890ca6124c99e013a96c3faf7`, and successful `rev-parse`, branch, and status commands.
- The directory contains `.git`, `.github`, `docs`, `src`, `tests`, `tools`, `.gitignore`, `pyproject.toml`, and `README.md`. The reported status command exited 0; its output was empty, consistent with a clean worktree.
- Conclusion: the earlier clone did in fact complete. The first script aborted while processing Git's `Cloning into ...` stderr output under Windows PowerShell 5.1, before it set its internal `cloneCreated` flag or recorded the native exit code. This is strongly consistent with a PowerShell error-handling issue; the report does not show a Git clone failure. No reclone or deletion is needed.
- The inspected HEAD matches the currently reported GitHub branch tip `e5237f5dc1efe6c890ca6124c99e013a96c3faf7`; this supersedes the earlier pinned parent commit `b7a74e8773c4cde00bf0b7761681939770e6b86d`.
- Next: use the existing checkout only after reading its `docs/PROJECT_kancollebot_MASTER.md`, re-check branch/HEAD/clean status, and then run the existing offline compile and unittest commands. The test script must handle native stderr without treating it as a terminating PowerShell exception and must preserve any pre-existing process-level `PYTHONPATH` and `PYTHONDONTWRITEBYTECODE` values.
- This update is remote documentation only. At the time of this note, project rules have not yet been read locally and tests have not yet been started. No package installation, game action, global settings change, historical Cloud copy modification, or access to `D:\\KancolleBot\\Source` occurred. Live dispatch remains disabled.
