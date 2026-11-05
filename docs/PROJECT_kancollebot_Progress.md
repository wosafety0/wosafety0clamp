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
- The path has no `.git`, `05_Docs\PROJECT_RULES.md`, `src`, `tests`, or `.github\workflows\ci.yml`. The inventory also listed immediate children of `D:`; it did not perform recursive search.
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


### 2026-10-24 Windows offline tests expose SQLite connection lifetime bug

- User-provided PowerShell 5.1 report at 16:22:20 +08:00 confirms the active checkout at `D:\\KancolleBot-Worktrees\\expedition-closed-loop-mvp-20261024`, branch `dev/expedition-closed-loop-mvp`, HEAD `e5237f5dc1efe6c890ca6124c99e013a96c3faf7`, clean initial worktree, rules read before testing, and Python `3.13.16` passing the `>=3.11` gate.
- `python -m compileall -q src tests` passed (exit code 0). Unit tests ran 98 tests and failed with 7 errors, all shown in the report as Windows `PermissionError: [WinError 32]` while `tempfile.TemporaryDirectory` tried to remove a temporary `actions.sqlite` file.
- Inspected the active branch's `src/kancollebot/expeditions/action_journal.py` and `tests/test_action_journal.py`. The journal uses `with self._connect() as db` in its database operations. Python's sqlite3 connection context manager handles commit/rollback but does not close the connection; this explains why the temporary database can remain locked on Windows when the directory cleanup runs. This is a code-backed diagnosis consistent with the reported stack traces.
- Likely correction: ensure each SQLite connection is closed after its transaction/context exits (for example, combine `contextlib.closing` with the connection context manager). Re-run the full offline suite after applying the fix. Do not mark the tests as passing until a new local execution report confirms it.
- Verification remains partial: compileall passed, unit tests failed. No packages were installed, no network request or game action was performed by the local test run, live dispatch remains disabled, and the prohibited legacy source directory was not accessed.
- This entry is a remote progress-document update only; it does not mean the user's local checkout was modified or synchronized. A source-file write attempt through the available GitHub tool was blocked before any write result was returned; therefore no source fix is claimed as applied.


### 2026-10-24 SQLite connection lifetime fix committed; local rerun pending

- Following the Windows offline test report (98 tests, 7 errors from `PermissionError: [WinError 32]` deleting temporary `actions.sqlite` files), updated `src/kancollebot/expeditions/action_journal.py` on the active GitHub branch.
- The journal now uses a private `_connection()` context manager: it enters the sqlite3 connection context for transaction handling and closes the connection in a `finally` block. The constructor, `reserve`, `transition`, and `get` use this helper, so connection lifetime is explicit and temporary database files should no longer remain open after each operation.
- Remote source update commit: `e3a3c4a941e9115d64902d9725fb81c73e7bf4e5`; source blob SHA `3cc882e58be1d50016b8cb0fcfd978502c3618a9`. GitHub readback exactly matched the written source content and confirmed all four database call sites use the helper.
- Verification status: **source change committed and remote readback verified; local compile/tests after the fix are still pending**. Do not infer test success from the source diff. The user's local checkout is still at the previously tested commit until it is safely fast-forwarded.
- Next: in the existing checkout, verify the expected branch and clean status, fast-forward only to the current remote branch (never reset or overwrite local work), re-read the current master rules, then rerun `python -m compileall -q src tests` and `PYTHONPATH=src python -m unittest discover -s tests -v` with Python >=3.11. Record exact exit codes and test counts. If the worktree is not clean or fast-forward is not possible, stop before modifying anything.
- No packages were installed and no game action was performed. Live dispatch remains disabled. This is a remote GitHub code/document update; it is not evidence of local synchronization or a passing local test run.


### 2026-10-24 SQLite fix offline retest blocked by GitHub connectivity
- Local retest target: `D:\\KancolleBot-Worktrees\\expedition-closed-loop-mvp-20261024`.
- Timestamp reported by the local PowerShell run: 2026-10-24 16:29:17 +08:00.
- Outcome: FAILED before tests started. `git fetch` could not connect to `github.com:443` (connection failure after 21253 ms); fast-forward sync was false, project rules were not read, Python version was not checked, and compileall/unittest were not run.
- Safety state: no package installation, no game actions, `LIVE_DISPATCH=DISABLED`, and legacy source directory not accessed.
- This result does not establish whether the SQLite fix passes or fails. Next step: perform read-only local Git/worktree/remote configuration checks and retry synchronization only after connectivity is available; do not alter proxy or system settings.
- Clipboard verification failed and was ignored per user preference.

## 2026-10-24 16:43 +08:00 — Local sync precondition false negative

- The scoped-proxy fetch succeeded earlier: `origin/dev/expedition-closed-loop-mvp` advanced from `e5237f5dc1efe6c890ca6124c99e013a96c3faf7` to `b8f3860c041671d08c774954ec32707e1e60168a`.
- The local sync-and-test script then skipped fast-forward because it compared Git's slash-separated repository root (`D:/KancolleBot-Worktrees/expedition-closed-loop-mvp-20261024`) to a backslash-separated target string. This is a script path-normalization defect, not evidence of a Git merge conflict or test failure.
- At the time of the report, local HEAD remained `e5237f5dc1efe6c890ca6124c99e013a96c3faf7`, the branch was `dev/expedition-closed-loop-mvp`, and the worktree was clean. No merge or tests were performed in that attempt.
- Next: normalize repository paths before comparison; re-check clean status and `git rev-list --left-right --count HEAD...origin/dev/expedition-closed-loop-mvp`; only fast-forward when local ahead count is zero; then read the project rules and run compileall plus unittest. `LIVE_DISPATCH` remains disabled; no game actions or package installation.


### 2026-10-25 真实远征派遣验收门槛与当前状态

- 用户询问何时可以进行真实派遣。当前不批准直接开启真实派遣；采用分阶段验收，不要求整个项目全部完成后才进入真实派遣准备。
- 当前已知阻塞：Windows 本地 checkout 的 SQLite 修复后离线复测尚未收到结果。上次报告因路径格式比较错误跳过快进同步，仍停留在旧 HEAD；因此修复后的 compileall 和完整 unittest 均未验证。
- 进入真实派遣前必须满足以下门槛：
  1. 本地分支快进同步至已审查的远程提交，工作区干净，项目规则读取成功。
  2. compileall 与完整离线单元测试通过；对 SQLite journal 的关闭、跨进程重复派遣阻止、未知结果 fail-closed 等保护有回归测试证据。
  3. 在用户现有 Poi 实例上完成只读桥接预检：确认插件身份、健康状态、storeReady、登录/母港证据、舰队与任务字段形状，以及数据刷新/新鲜度；禁止在此阶段发出操作请求。
  4. 完成不触碰真实游戏的模拟/假 adapter 验收，覆盖派遣前置条件失败、重复提交、超时或结果未知、状态复核失败、停止条件与审计日志。
  5. 真实操作开关保持关闭，直到用户单独明确授权一次受控真实派遣；首次只允许一项明确的远征任务，派遣后立即只读复核状态，结果不明则停止，不自动重试或扩展批量操作。
- 只有前四项通过并获得第 5 项单独授权后，才可执行首次受控真实派遣。离线测试通过不代表 Poi 实机接口或实时游戏状态已验证。
- 当前状态：SQLite 修复后本地测试待复验；Poi 运行时数据新鲜度与登录状态尚未验证；真实派遣保持禁用。没有执行游戏操作或修改用户本地文件。


### 2026-10-25 KancolleBot 独立 Python 环境决策

- 用户提出在首次真实远征前为项目配置独立运行环境；已决定采用项目目录内的 `.venv` 虚拟环境方案，避免项目依赖污染全局 Python 环境。
- 执行顺序为只读预检优先：检查当前工作树/分支与规则、Python 解释器版本、`pyproject.toml` 和其他依赖声明、源码中的第三方导入、`.gitignore` 是否排除 `.venv`，并确认目标环境路径不存在或可安全复用。
- 预检通过后才创建虚拟环境；不安装软件或依赖，除非后续检查明确证明需要且得到相应授权。优先使用标准库和现有依赖声明，不猜测或凭空生成锁定清单；不修改系统 PATH、全局 Python、Windows 代理或其他系统设置。
- 在独立环境中重跑项目规定的编译与完整离线单元测试，并记录解释器版本、实际命令、退出码和测试数量。环境创建成功不等于测试通过，也不等于 Poi 实机兼容性已验证。
- 本条是方案/决策记录；目前尚未执行本地预检、创建 `.venv` 或安装任何依赖。首次真实派遣仍须满足既有验收门槛并取得用户单独授权，真实操作开关保持关闭。


### 2026-10-25 独立环境只读预检结果

- 用户在 Windows PowerShell 5.1 于 2026-10-25 14:51:20 +08:00 执行只读预检，目标 `D:\KancolleBot-Worktrees\expedition-closed-loop-mvp-20261024`；Git root 验证通过，分支为 `dev/expedition-closed-loop-mvp`，本地 HEAD `e5237f5dc1efe6c890ca6124c99e013a96c3faf7`，工作区干净，项目规则文件存在并已读取。
- `pyproject.toml` 声明 `requires-python = ">=3.11"`、`dependencies = []`；没有 requirements/lock 文件；`.gitignore` 已忽略 `.venv/`，且目标 `.venv` 尚不存在。检测到 Python 3.13 与 3.15 的安装路径，但版本探测命令自身在 PowerShell 传参时丢失了 Python 字符串引号，导致 `TypeError`，退出码 1。因此预检总体 FAILED，不是 Python 安装损坏的证据。
- 后续建议使用 Python 3.13，先修复版本检查脚本，再决定创建本地 `.venv`；不安装第三方依赖或修改全局配置。当前本地 HEAD 仍是旧提交，SQLite 修复后的离线复测必须先安全快进同步。
- 本轮未创建环境、未安装包、未修改本地文件或执行游戏操作；`LIVE_DISPATCH` 仍禁用。本条仅是 GitHub 远程文档更新，不代表本地已同步。


### 2026-10-25 Python 3.13 与 Git 快进同步验证

- 用户提供 2026-10-25 14:52:31 +08:00 Windows PowerShell 5.1 报告：Python 3.13.16 验证通过，项目规则已读取，本地工作区同步前后均干净，目标 `.venv` 不存在。
- 对 `dev/expedition-closed-loop-mvp` 执行带命令级代理的 `git fetch` 成功（exit 0）；同步前 ahead/behind 为 `0 11`；`git merge --ff-only` 成功（exit 0）；本地 HEAD 从 `e5237f5dc1efe6c890ca6124c99e013a96c3faf7` 更新为 `ec6a18879a80c0a6786296eb76de7e5ea5f234b6`，与该次 fetch 的远程跟踪 HEAD 一致。
- 结论：**本地 Git 快进同步 PASS**；尚未创建虚拟环境、安装依赖或运行 SQLite 修复后的离线测试。下一步在现有 checkout 创建 Python 3.13 `.venv`，运行编译与完整 unittest，记录准确结果。
- 本次本地 Git 同步已发生；本条另行记录于 GitHub 远程进度文档。未执行游戏操作，`LIVE_DISPATCH` 继续禁用。


### 2026-10-25 Python 3.13 独立环境与 98 项离线测试通过

- 用户提供 Windows PowerShell 5.1 本地执行报告，时间 2026-10-25 14:53:24 +08:00，目标 `D:\KancolleBot-Worktrees\expedition-closed-loop-mvp-20261024`，测试时本地 HEAD 为 `ec6a18879a80c0a6786296eb76de7e5ea5f234b6`，分支/规则/工作区/依赖声明预检 PASS。
- 已在本地创建 `.venv`（创建退出码 0），解释器 `.venv\Scripts\python.exe` 为 Python 3.13.16，虚拟环境验证 PASS。采用 `--without-pip`，未引导 pip，也未安装第三方依赖。
- 在独立环境中运行 `python -m compileall -q src tests`：exit 0；运行 `python -m unittest discover -s tests -v`（`PYTHONPATH=src`）：exit 0，`Ran 98 tests in 2.130s`，`OK`。
- 结论：**独立 Python 环境创建成功，98/98 离线测试通过**；此前 Windows SQLite 临时数据库占用错误未在本轮复现，修复后的本地离线回归验收通过。此结论仅覆盖本次测试运行的代码，不证明 Poi 实机/实时状态/真实派遣可用。
- 没有执行游戏操作，`LIVE_DISPATCH=DISABLED`。下一步：在用户现有 Poi 实例上进行严格只读桥接与数据新鲜度预检，之后再完成模拟派遣验收；真实派遣需另行明确授权。
- 此条为远程 GitHub 进度归档；本地测试及虚拟环境由用户执行，不能将此文档提交视为本地已再次同步至最新远程 HEAD。


### 2026-10-25 Poi CDP read-only preflight
- User-reported local preflight at HEAD `ec6a18879a80c0a6786296eb76de7e5ea5f234b6`: clean worktree; Python 3.13.16 venv; seven Poi processes; TCP port 9222 listening; Poi adapter source files found; plugin directory exists.
- `CDP_HTTP_RESULT: FAILED` is **inconclusive** because the check used unsupported PowerShell 5.1 `Invoke-RestMethod -NoProxy`. Do not diagnose CDP as broken from this result.
- Next: repeat a localhost-only HTTP GET using PowerShell 5.1-compatible .NET `HttpWebRequest` with `Proxy = $null`; inspect `/json/version` and `/json/list` metadata only, no WebSocket execution or game actions.
- No local files modified by this preflight, no live dispatch. Remote documentation commit does not imply local worktree sync.


### 2026-10-25 CDP HTTP recheck verified
- User-reported read-only localhost CDP HTTP recheck: `/json/version` PASS (`Chrome/152.0.7977.54`, protocol `1.3`); `/json/list` PASS with 3 targets: KanColle `webview`, Poi `page`, DMM `service_worker`.
- Previous CDP HTTP failure was due to an incompatible PowerShell 5.1 check, not demonstrated CDP unavailability.
- No files modified or game actions; `LIVE_DISPATCH=DISABLED`. Next: inspect existing Poi bridge read-only contracts and validate game login/homeport and state snapshot without invoking dispatch or mutating APIs. This is user-reported local execution, not independently executed on their machine.


### 2026-10-25 Poi bridge source review
- User-reported read-only source review at local HEAD `ec6a18879a80c0a6786296eb76de7e5ea5f234b6` on `dev/expedition-closed-loop-mvp`: all five files present; `SOURCE_INSPECTION: PASS`, `ISSUE_COUNT: 0`.
- `cdp_targets.py` is discovery-only; `expedition_state.py` and `_v2.py` normalize fleet data; `observed_state.py` checks freshness. `cdp_input.py` has `Input.dispatchMouseEvent` and must not be invoked during read-only validation.
- Source review did not execute code or prove state retrieval. Next inspect target selection and normalization contracts before a read-only snapshot. `LIVE_DISPATCH=DISABLED`; no local files modified.


### 2026-10-25 CDP target and fleet contract review
- User supplied full read-only contract review for `cdp_targets.py`, `expedition_state_v2.py`, and `observed_state.py` at local HEAD `ec6a18879a80c0a6786296eb76de7e5ea5f234b6`.
- Important mismatch: `list_page_targets()` filters `type == "page"`, but observed KanColle game target is `webview`; default `select_poi_renderer(title_hint="poi")` selects Poi UI, not the game. Do not assume current selector can read the game renderer.
- `normalize_poi_fleets()` expects `info.fleets` array, numeric-key `info.ships` and `const.$ships`, timezone-aware observed_at; missing/inconsistent records fail closed. Any HP below maximum is conservatively marked damaged; mission return time remains unknown (`None`). `ObservedState` rejects future/stale observations.
- Next: inspect read-only CDP target metadata without logging debugger URLs/IDs, then design explicit game webview selection and verify Redux field shapes; no input commands, no live dispatch. Source inspection only; no runtime snapshot confirmed.


### 2026-10-25 WebView discovery PowerShell array parsing failure
- User-reported local read-only WebView discovery produced `TARGET_COUNT: 1`, combined `TYPE=webview page`, `HOST=INVALID_URL`, and zero game matches, although prior CDP `/json/list` returned three distinct targets.
- Most likely PowerShell 5.1 JSON-array enumeration/wrapping bug in the diagnostic script (`@($reader.ReadToEnd() | ConvertFrom-Json)`), not proof the game webview disappeared. Recheck by assigning `ConvertFrom-Json` result to a variable and enumerating with `@($parsed)`; validate each target individually.
- No WebSocket connection, files modified, or game actions. `LIVE_DISPATCH=DISABLED`. Next: corrected metadata-only recheck, then plan explicit game webview target selection.


### 2026-10-25 Game WebView target discovery verified
- User-reported corrected PowerShell 5.1 metadata-only check: exactly one game target matched (`type=webview`, host `play.games.dmm.com`, path `/game/kancolle`), debugger endpoint present; Poi `page` target was separately identified.
- `GAME_WEBVIEW_DISCOVERY=PASS`; WebSocket was not connected, files were not modified, no game actions; `LIVE_DISPATCH=DISABLED`.
- Next: inspect existing read-only snapshot implementations and tests; do not open a WebSocket or evaluate game JavaScript until a separately reviewed, strictly read-only transport is available.


### 2026-10-25 Read-only Poi MCP snapshot path source review
- Remote source review confirms `PoiExpeditionReader` and `PoiMcpClient` already implement a read-only snapshot path using loopback HTTP MCP at `http://127.0.0.1:12450/mcp`; allowed tool names are limited to `poi_describe`, `poi_get`, and `poi_lookup`.
- Reader checks bridge identity/health/store readiness, tool availability, rejects truncated or malformed payloads, requests only referenced master ship fuel/ammo maxima, and fails closed during normalization. Offline tests cover targeted lookup and malformed/truncated payload cases.
- Source review only; no runtime MCP health check or live snapshot request was performed. Existing user report shows CDP game WebView discovery passes, but that does not establish MCP bridge availability.
- Next step should be a separate read-only HTTP health endpoint check (`/health`) against the configured loopback MCP port; only if healthy, consider a snapshot read via the existing reader, without calling any non-allowlisted tools. Keep live dispatch disabled.


### 2026-10-25 Poi MCP health preflight passed
- User-provided local report confirms `GET http://127.0.0.1:12450/health` returned HTTP 200; bridge identity `poi-nanodesu-mcp`, status `ok`, and `storeReady=True`. Listener was present.
- This was a health-only request: MCP session was not initialized, fleet data was not read, no WebSocket was connected, no files were modified, and game actions remained disabled.
- Next validation may use the existing `PoiExpeditionReader` through its loopback-only MCP client. Restrict calls to the existing read-tool allowlist (`poi_describe`, `poi_get`, `poi_lookup`), print only a sanitized fleet snapshot, and do not dispatch gameplay actions.


### 2026-10-25 Live fleet snapshot blocked by worktree path mismatch

- Time: 2026-10-25 15:10 +08:00.
- Read-only snapshot attempt result: FAILED before Git metadata, MCP health, or fleet snapshot checks.
- Exact error: expected path `D:\\KancolleBot-Worktrees\\expedition-closed-loop-mvp` does not exist.
- Previously confirmed actual worktree path: `D:\\KancolleBot-Worktrees\\expedition-closed-loop-mvp-20261024`; branch previously observed as `dev/expedition-closed-loop-mvp`. These are historical observations and must be revalidated locally before use.
- No WebSocket connection, fleet read, file modification, or game action occurred. `LIVE_DISPATCH=DISABLED`.
- Next step: read-only path discovery, then validate repository root, branch, HEAD, clean/dirty state, venv interpreter, and health endpoint against the discovered actual worktree. Only after those checks pass, retry the read-only live fleet snapshot.
- Do not treat this path error as a Poi MCP or snapshot-reader failure; those checks were not reached.


### 2026-10-25 Worktree path rediscovered; snapshot retry unblocked

- Time: 2026-10-25 15:13 +08:00.
- Read-only path discovery passed. Actual worktree: `D:\\KancolleBot-Worktrees\\expedition-closed-loop-mvp-20261024`.
- Branch: `dev/expedition-closed-loop-mvp`.
- HEAD: `ec6a18879a80c0a6786296eb76de7e5ea5f234b6`.
- Worktree: clean (0 status lines). `.venv\\Scripts\\python.exe` exists.
- The previously attempted path without the `-20261024` suffix does not exist; the earlier snapshot attempt failed before health or snapshot checks.
- Next step: rerun the read-only health check and live fleet snapshot against the confirmed worktree. No game actions or file modifications occurred; `LIVE_DISPATCH=DISABLED`.


### 2026-10-25 Snapshot attempt reached health pass but failed before usable result

- Time: 2026-10-25 15:21 +08:00.
- Confirmed local preflight: correct worktree `D:\\KancolleBot-Worktrees\\expedition-closed-loop-mvp-20261024`, branch `dev/expedition-closed-loop-mvp`, HEAD `ec6a18879a80c0a6786296eb76de7e5ea5f234b6`, clean worktree.
- Poi MCP health check passed.
- Fleet snapshot did not produce a usable result: `SNAPSHOT_READ=NOT_RUN`, `FLEET_COUNT=0`; report captured only `Traceback (most recent call last):`, with no traceback details.
- No WebSocket connection, project file modification, or gameplay action was reported. `LIVE_DISPATCH=DISABLED`.
- Next step: read-only diagnosis only—verify the venv interpreter/version and imports, then reproduce only the failure with sanitized exception class and final traceback lines. Do not retry the live snapshot until the cause is identified.


### 2026-10-25 Python diagnostic output capture needs correction

- Time: 2026-10-25 15:22 +08:00.
- Read-only diagnostic confirmed Python 3.13.16 starts and the Git worktree is clean.
- The diagnostic itself failed while invoking the import probe: PowerShell reported `System.Management.Automation.RemoteException` and only retained `Traceback (most recent call last):`.
- This does not prove a project import failure; the script's stderr capture/pipeline handling is insufficient.
- Next step: capture Python stdout and stderr separately in memory via .NET process redirection, preserve exit code, and report a sanitized traceback tail. Do not initialize MCP or read live fleet data during diagnosis.
- `LIVE_DISPATCH=DISABLED`; no files or game state changed.


### 2026-10-25 Python import diagnostic V2 passed

- User-reported local diagnostic time: 2026-10-25 15:23:57 +08:00.
- Worktree: `D:\\KancolleBot-Worktrees\\expedition-closed-loop-mvp-20261024`; branch `dev/expedition-closed-loop-mvp`; HEAD `ec6a18879a80c0a6786296eb76de7e5ea5f234b6`; worktree clean.
- Python 3.13.16 launched successfully. Import probe exit code was 0; `kancollebot`, `kancollebot.adapters.poi.mcp_client`, `kancollebot.adapters.poi.expedition_state_v2`, and `kancollebot.adapters.poi.expedition_reader` all imported successfully; no Python stderr was present.
- This rules out import-time failure in these four modules for this environment, but does not yet identify why the earlier snapshot attempt failed. MCP session initialization and fleet reading were not performed in this diagnostic.
- Safety: no files modified, no WebSocket connection, no fleet data read, no game actions; `LIVE_DISPATCH=DISABLED`.
- Next step: isolate the failure with a separate read-only MCP protocol/session probe that captures HTTP status, JSON-RPC error details, and sanitized response metadata. Only use the existing allowlisted read tools; do not call dispatch/write tools. If initialization succeeds, test the smallest read-only query before attempting the full fleet snapshot.


### 2026-10-25 Read-only MCP session probe passed

- User-reported local run time: 2026-10-25 15:24:47 +08:00.
- Worktree: `D:\\KancolleBot-Worktrees\\expedition-closed-loop-mvp-20261024`; branch `dev/expedition-closed-loop-mvp`; HEAD `ec6a18879a80c0a6786296eb76de7e5ea5f234b6`; worktree clean; Python 3.13.16.
- Read-only probe results: health PASS; MCP initialize PASS; tools/list PASS; `poi_describe(info.fleets)` PASS; required allowlisted tools present; no reported error.
- This establishes that the live loopback MCP endpoint can initialize a session, list tools, and describe the fleet path. It does not yet establish that fleet/ship payload retrieval and normalization succeed; no fleet instance data was read in this probe.
- Safety: no WebSocket connection, no files modified, no gameplay action; `LIVE_DISPATCH=DISABLED`.
- Next: run the existing `PoiExpeditionReader.read_fleets()` in a separate read-only step. Emit only a sanitized summary (fleet count, fleet IDs/status/mission IDs and ship counts; no names or sensitive session data), and preserve the exception class and traceback tail on failure. Do not invoke any write or dispatch method. If it fails, stop and diagnose before retrying.

### 2026-10-25 Read-only fleet snapshot returned three fleet objects

- Local report time: 2026-10-25 15:25:25 +08:00.
- Read-only execution context: `D:\KancolleBot-Worktrees\expedition-closed-loop-mvp-20261024`, branch `dev/expedition-closed-loop-mvp`, HEAD `ec6a18879a80c0a6786296eb76de7e5ea5f234b6`, clean worktree, Python 3.13.16.
- `PoiExpeditionReader.read_fleets()`: PASS; returned 3 fleet objects; no exception or traceback.
- Sanitized projection: fleet 1 `mission_id=null`; fleet 2 `mission_id=null`; fleet 3 `mission_id=2`. All three projected `status` and `ship_count` values were null.
- Interpretation: the read path returned fleet objects, but the reported null fields mean the summary projection has not yet demonstrated status or ship-list extraction. Do not claim full snapshot validation or infer that fleets are empty. Inspect the actual snapshot model fields and raw object shape using a bounded, read-only diagnostic.
- Safety: no WebSocket connection, no files modified, no gameplay actions; `LIVE_DISPATCH=DISABLED`.
- Next step: inspect the declared dataclass/model fields and emit only type, field names, list lengths, and sanitized identifiers; then correct the summary projection or investigate normalization if the expected fields are genuinely absent. Continue to defer dispatch until freshness/login/homeport and safety gates are separately verified.

### 2026-10-25 Snapshot summary projection corrected against model schema

- Follow-up source review of `src/kancollebot/adapters/poi/expedition_state_v2.py` confirms each `ExpeditionFleetSnapshot` is constructed with `fleet_id`, `ship_ids`, `mission_id`, `return_at`, `fuel_ready`, `ammo_ready`, and `damaged_ship_ids`.
- The previous report's `status` and `ships`/derived `ship_count` fields were not fields on the snapshot model, so their null values are a report-projection mismatch, not evidence that the reader omitted ship data.
- `mission_id=null` for fleets 1 and 2 is consistent with the normalizer: mission ID is only set when `api_mission[0] > 0`. Fleet 3's `mission_id=2` indicates the active mission field was populated.
- Correct follow-up: rerun a strictly read-only summary using `len(fleet.ship_ids)`, `fleet.ship_ids` (IDs only), `fleet.fuel_ready`, `fleet.ammo_ready`, `fleet.damaged_ship_ids`, `fleet.mission_id`, and `fleet.return_at`. Do not invent a `status` field. Still no dispatch; login/homeport/freshness evidence remains a separate gate.

### 2026-10-25 Read-only fleet summary V2 passed

- Local report time: 2026-10-25 15:27:22 +08:00; observed timestamp reported by the reader: `2026-10-25T07:27:22.198235+00:00` (same instant in UTC).
- Execution context: `D:\KancolleBot-Worktrees\expedition-closed-loop-mvp-20261024`, branch `dev/expedition-closed-loop-mvp`, HEAD `ec6a18879a80c0a6786296eb76de7e5ea5f234b6`, clean worktree, Python 3.13.16.
- `PoiExpeditionReader.read_fleets()`: PASS; Python exit code 0; 3 normalized fleet snapshots; no exception or traceback.
- Fleet 1: 6 ship instances `[13,73,70,10,40,62]`; no active mission ID reported; fuel/ammo readiness both true; no damaged ships reported.
- Fleet 2: 4 ship instances `[1,3,4,43]`; no active mission ID reported; fuel/ammo readiness both false; no damaged ships reported. Treat as not ready for expedition planning until the live fuel/ammo values and normalization semantics are independently verified.
- Fleet 3: 4 ship instances `[12,7,33,2]`; mission ID `2`; fuel/ammo readiness both true; no damaged ships reported.
- All snapshots currently report `return_at=null`; this remains intentional because mission timing slots/units have not been confirmed.
- Safety: no WebSocket connection, no files modified, no gameplay actions; `LIVE_DISPATCH=DISABLED`.
- Interpretation/limits: this validates the reader's current normalized output, not that the data is fresh for the currently logged-in game session or that the player is at homeport. The timestamp is the observation time, not independent freshness proof. Do not dispatch.
- Next gate: read-only validate game session/homeport and freshness using supported Poi state/metadata, without WebSocket connection or gameplay actions. Keep dispatch disabled until all runtime and safety gates are separately accepted.

### 2026-10-25 Read-only session/homeport path discovery

- User-reported local run time: 2026-10-25 15:28:55 +08:00.
- Independent environment confirmed: `D:\\KancolleBot-Worktrees\\expedition-closed-loop-mvp-20261024\\.venv`; interpreter `...\\.venv\\Scripts\\python.exe`; Python 3.13.16. Branch `dev/expedition-closed-loop-mvp`, HEAD `ec6a18879a80c0a6786296eb76de7e5ea5f234b6`, clean worktree.
- Read-only MCP checks passed: health identified `poi-nanodesu-mcp`, `status=ok`, `storeReady=true`; required tools `poi_describe`, `poi_get`, and `poi_lookup` were present; MCP initialize succeeded.
- `poi_describe` succeeded for `info`, `info.basic`, `info.fleets` (array), and `info.ships` (object-map). Probes for `api_port`, `api_basic`, `api_member`, `api_world`, `api_data`, and `api_start2` returned expected unknown-root errors because Nanodesu exposes roots such as `info`, `const`, `fcd`, `battle`, `sortie`, and `timers`, rather than raw KanColle API root names. These errors do not indicate bridge failure.
- This probe only inspected path descriptors and field names; no game-state values were read. It did not establish a login/session indicator, homeport indicator, or independent Redux refresh/freshness signal.
- Safety: no WebSocket connection, no files modified, no gameplay actions; `LIVE_DISPATCH=DISABLED`.
- Next step: use only supported `info`/plugin roots in a bounded, sanitized descriptor search for session/homeport/freshness candidates; inspect actual data values only after identifying a relevant documented path. Do not infer freshness from `ObservedState.observed_at`, and do not dispatch before the gate is independently validated.

### 2026-10-25 Read-only session/homeport field discovery V2

- User-reported local run time: 2026-10-25 15:29:50 +08:00. Independent environment: `D:\\KancolleBot-Worktrees\\expedition-closed-loop-mvp-20261024\\.venv`, Python 3.13.16; branch `dev/expedition-closed-loop-mvp`, HEAD `ec6a18879a80c0a6786296eb76de7e5ea5f234b6`, worktree clean.
- Poi MCP health/readiness, session initialization, and allowlisted read-tool discovery passed. Descriptor discovery returned:
  - `info.basic` keys include `api_active_flag`, `api_member_id`, `api_nickname`, `api_level`, `api_tutorial`, and `api_tutorial_progress`; these are potential account/basic-state indicators but do not by themselves prove the current page is at homeport or that data is fresh.
  - `info.fleets`: array, total 3, sample fields include `api_id`, `api_mission`, `api_ship`, and `api_flagship`.
  - `info.ships`: object-map, total 91, sample fields include `api_id`, `api_ship_id`, `api_fuel`, `api_bull`, and `api_nowhp`.
  - `sortie` exposes `sortieStatus`, `sortieMapId`, `currentNode`, and related fields; `battle` exposes `_status` and `result`; `timers` exposes `cond`.
  - `misc` only exposes `canNotify`; `const` exposes master-data maps; `wctf`/ `fcd` expose plugin state and metadata.
- Raw API roots such as `api_port` and `api_basic` are not Nanodesu roots. `questline` and several plugin branches are unavailable because their providers are not installed or have not stored state; do not install plugins for this task.
- This was descriptor-only discovery: no state values were read, no WebSocket connection, no files modified, no game actions; `LIVE_DISPATCH=DISABLED`.
- Next safe step: read only a small sanitized set of existing values from `info.basic`, `info.fleets`, and `sortie` using `poi_get`, if its schema supports path arguments. Report only presence/type/boolean and numeric IDs needed to assess basic account data and active sortie state; never expose member ID, nickname, or other personal values. This may establish basic-state presence and sortie indicators, but still does not prove homeport or freshness unless an explicit trustworthy field supports it.
### 2026-10-25 Read-only diagnostic subprocess deadlock and recovery

- User-reported incident: the PowerShell diagnostic stalled while synchronously calling StandardOutput.ReadToEnd() before StandardError.ReadToEnd(). This sequential pipe-drain pattern can deadlock if the child fills the stderr pipe while the parent waits for stdout EOF.
- Read-only process inspection identified PID 29044 as the diagnostic Python parent launched by PowerShell PID 32976, and PID 29248 as its Python child. Both command lines were the same encoded read-only probe targeting only info.basic, info.fleets, and sortie through the allowlisted Poi MCP read client; neither was a game-action process.
- User-reported cleanup result at 2026-10-25 15:54:11 +08:00: PASS_BOTH_DIAGNOSTIC_PROCESSES_TERMINATED; PIDs 29248 and 29044 terminated, PowerShell PID 32976 retained. No game actions and no project files modified.
- This incident is a diagnostic harness/process-capture failure, not evidence that Poi MCP, PoiExpeditionReader, or the requested data reads failed. The intended read probe did not produce a verified result, so no state conclusions may be drawn from it.
- Next attempt must avoid sequential blocking reads from redirected stdout/stderr. Prefer Start-Process with separate temporary stdout/stderr files and a bounded timeout, or use concurrent asynchronous stream draining; on timeout, terminate only the verified diagnostic child process. Clean up temporary files, report timeout/exit code honestly, and keep LIVE_DISPATCH=DISABLED. Do not reconnect CDP WebSocket, invoke game inputs, or broaden tool permissions.

### 2026-10-25 Read-only state diagnostic V3 — partial data-shape result

- User-reported local run time: 2026-10-25 15:57:54 +08:00; worktree `D:\\KancolleBot-Worktrees\\expedition-closed-loop-mvp-20261024`.
- Diagnostic report: health PASS, MCP initialize PASS, allowlisted read tools PASS; three `poi_get` requests for `info.basic`, `info.fleets`, and `sortie` returned without a reported exception. Python exit code was not captured in the user-visible report (blank field), so process exit status is not independently established.
- `info.fleets` was decoded as an array of 3 fleets with ship counts 6, 4, and 4 (14 total). The summary extracted `mission_id=null`, `null`, and `1`; this differs from an earlier read where fleet 3 mission ID was `2`. Do not assume a gameplay transition or stable state until payload structure and mission slot semantics are checked.
- The six probed `info.basic` fields (`api_active_flag`, `api_level`, `api_tutorial`, `api_tutorial_progress`, `api_starttime`, `api_experience`) were all reported missing. The three probed `sortie` fields (`sortieStatus`, `sortieMapId`, `currentNode`) were also reported missing. This may reflect an unexpected wrapper/field mapping rather than absent game state; raw payloads were intentionally not emitted, so shape remains unresolved.
- Safety report: no CDP WebSocket, no game actions, no project files modified, `LIVE_DISPATCH=DISABLED`. Standard error was reported absent.
- Next step: perform a bounded, sanitized read-only shape inspection for these three responses. Emit only top-level container type, top-level key names, wrapper keys such as `kind/items/total/returned/truncated`, and the presence/type of target fields at the actual returned nesting level. Never print raw values, account identifiers, nicknames, or full payloads. Confirm `api_mission` slot semantics before interpreting mission IDs. Keep live dispatch disabled.
- Provenance: this entry records the user-provided diagnostic report; it does not claim a direct local execution by the assistant.

### 2026-10-25 Read-only payload shape V1 — wrapper discovered, probe harness failed

- User-reported local run time: 2026-10-25 15:59:22 +08:00; worktree `D:\\KancolleBot-Worktrees\\expedition-closed-loop-mvp-20261024`.
- Health, MCP initialize, and allowlisted read-tool checks reported PASS. `info.basic` and `sortie` each decoded to a dictionary whose only top-level keys were `kind` and `value`; the target fields were not present at that wrapper level. This is evidence of an unresolved wrapper/value-unwrapping issue, not evidence that basic account fields or sortie state are absent.
- `info.fleets` decoded as a dictionary with keys `items`, `kind`, `returned`, `total`, and `truncated`; total=3, returned=3, truncated=false. `items` is a list of three dictionaries, with `api_id`, `api_ship`, and `api_mission` fields detected on the first item.
- Sanitized `api_mission` shape: all three fleets have four integer slots. Fleets 1 and 2 had zero in all four slots. Fleet 3 had positive integers in slots 0, 1, and 2 and zero in slot 3. Numeric values were intentionally suppressed. Do not map these slots to mission ID or timing until the provider schema/implementation confirms semantics; the earlier reported mission ID changed from 2 to 1 and remains unresolved.
- The diagnostic process/report itself FAILED after the JSON summary: `ERROR_TYPE=RuntimeException`, `ERROR=不能对 Null 值表达式调用方法。`; Python exit code was blank. Therefore the data-shape summary is useful partial evidence, but the harness did not complete cleanly and process exit status is unverified. Clipboard verification was `FAILED_UNAVAILABLE` and is non-blocking under the user's preference.
- Safety report: no raw payload/account values emitted, no CDP WebSocket, no game actions, no project files modified, `LIVE_DISPATCH=DISABLED`.
- Next safe step: inspect the read-only `poi_get` response envelope and the MCP client's result-unwrapping code, using key names and types only. Specifically determine how the `kind/value` wrapper is represented and whether `value` itself is another typed object; avoid invoking methods on null, do not print raw values, and capture the child process exit code reliably. Keep live dispatch disabled.
- Provenance: this entry records the user-provided local diagnostic report; it does not claim direct local execution by the assistant.

### 2026-10-25 Read-only source review — MCP result envelope handling

- Read the current branch versions of `src/kancollebot/adapters/poi/mcp_client.py`, `src/kancollebot/adapters/poi/expedition_reader.py`, and `src/kancollebot/adapters/poi/expedition_state_v2.py` through GitHub; this was a remote source review, not a local worktree check or modification.
- `PoiMcpClient.call_read_tool()` returns the complete MCP `tools/call` result dictionary after JSON-RPC validation. It does not unwrap `structuredContent` or `content`.
- `expedition_reader._tool_value()` returns `structuredContent` directly whenever it is non-null; otherwise it parses exactly one text content item as JSON. It does not unwrap a nested `{kind, value}` wrapper. The reader's collection validation expects `kind` plus `items` at the outer level, which matches the reported `info.fleets` shape but not the reported `info.basic`/`sortie` wrappers.
- The `api_mission` normalizer interprets slot 0 as status and slot 1 as mission ID only when status > 0; this assumption is present in source but still requires authoritative confirmation of the provider's schema before operational use. `return_at` intentionally remains `None`.
- Source review alone cannot establish the exact nested contents of live `structuredContent.value` or explain the local Null method exception. Next step: a bounded live read-only probe should report only response envelope types/key names, `structuredContent` type/key names, text-item JSON shape, and safe nested type/key summaries for `info.basic`, `info.fleets`, and `sortie`; it must avoid calling methods on null and must capture the Python process exit code reliably.
- No source files changed, no dependencies installed, no local game actions or WebSocket activity. `LIVE_DISPATCH=DISABLED` remains required.
- Provenance: source contents were fetched from the remote branch and reviewed; this entry does not claim local execution.

### 2026-10-25 Read-only envelope inspection V1 — payload shape confirmed, harness exit handling defect

- User-reported local run time: 2026-10-25 16:04:44 +08:00; worktree `D:\\KancolleBot-Worktrees\\expedition-closed-loop-mvp-20261024`.
- The sanitized report confirmed each `poi_get` MCP tool result had only the outer `content` key and no `structuredContent`. Each `content` list contained one text item, whose JSON parses to the actual data envelope.
- `info.basic` parsed as `{kind, value}` where `value` is a dictionary containing the expected basic-state field names, including `api_level`, `api_tutorial`, `api_starttime`, and `api_experience`. The earlier missing-field result was caused by inspecting the wrapper rather than its `value` member.
- `sortie` likewise parsed as `{kind, value}`; `value` is a dictionary containing `sortieStatus`, `sortieMapId`, `currentNode`, and other sortie-state field names. This confirms field availability in the response shape only; no claim is made about whether the values indicate active sortie or fresh state.
- `info.fleets` parsed as an array-kind collection envelope with `items` length 3, not truncated in the previous probe. This shape aligns with `PoiExpeditionReader._collection_items()` expectations once the text JSON is decoded by `_tool_value()`.
- The harness reported `PYTHON_EXIT_CODE=UNVERIFIED`, `RESULT_JSON_VALIDATION=INVALID_JSON`, and then emitted a second JSON object with `ERROR_TYPE=SystemExit`, `ERROR_MESSAGE=0`. The inspected script catches `BaseException` around `sys.exit(main())`; because `sys.exit(0)` raises `SystemExit(0)`, that handler incorrectly converts successful completion into a second FAILED JSON record and exit code 2. The PowerShell report also failed to capture/validate the process exit code, so process success is not established despite the first sanitized JSON saying PASS.
- No raw payload or account values were emitted. No WebSocket, game actions, or project-file modifications were reported. `LIVE_DISPATCH=DISABLED`.
- Next safe step: fix only the temporary diagnostic harness (not project source) to avoid catching `SystemExit`: call `code = main()` and then `sys.exit(code)` outside the `except Exception` handler; use `except Exception`, not `BaseException`; ensure stdout contains exactly one JSON record; capture and verify child exit code via a robust process wait/poll path. Before further live reads, the wrapper/field-name finding is already sufficient; any additional probe should be limited to sanitized presence/type indicators and not print values.
- Provenance: this entry records the user-provided local report and remote source review; it does not claim the assistant executed the local probe.


### 2026-10-25 Read-only envelope inspection V3 — reads passed, process result unverified

- User-reported local run time: 2026-10-25 16:11:34 +08:00; worktree `D:\\KancolleBot-Worktrees\\expedition-closed-loop-mvp-20261024`.
- Reported checks: process started; JSON valid; health, MCP initialize, read tools, fleets, ships, and master ships all PASS. Session validity remains NOT_VERIFIED.
- The wrapper report nevertheless states `OVERALL_RESULT=FAILED`, gives only a generic criterion failure, and leaves `PYTHON_EXIT_CODE=UNVERIFIED`. These fields do not identify a failing MCP read. Do not reinterpret them as proof that the bridge reads failed, but do not claim the verifier process completed successfully either.
- Remote source review of `tools/verify_poi_bridge.py` shows its normal Nanodesu success path emits one JSON object with `overall=PASS_WITH_SESSION_UNVERIFIED` and returns exit code 0 when no check has status FAIL. Its exception path emits `overall=FAILED` and returns 2. The user-facing V3 summary does not preserve the underlying verifier JSON/exit code, so the mismatch is in the diagnostic wrapper/result-capture path until proven otherwise.
- Safety: report says no WebSocket, no game actions, no project files modified; `LIVE_DISPATCH=DISABLED`. Clipboard status is `UNAVAILABLE_IGNORED`, which is non-blocking per user preference.
- Next step: stop repeating MCP reads for now. If process status must be closed out, make one minimal local check of the existing captured stdout/stderr and `Process.ExitCode` (without issuing additional MCP calls); otherwise continue using the already confirmed sanitized payload shapes as partial evidence. Keep session validity/freshness unverified and dispatch disabled.
- Provenance: this entry records the user-provided local report and a remote read-only review of the verifier source; it does not claim direct local execution or local file modification.


### 2026-10-25 V3 artifact preflight — verifier artifacts not captured

- User-reported local preflight time: 2026-10-25 16:13:07 +08:00; mode READ_ONLY.
- Two temporary roots were found and one candidate file matched: `kcb_envelope_c52d4ee5441b46e6b44babce47c4ea5a.py`, last written at 16:04:43, size 4,556 bytes. The candidate is the diagnostic script itself; the preflight did not report captured stdout, stderr, JSON result, or a stored process exit code.
- Exit status remains unrecoverable unless it was persisted in an artifact. Do not repeat MCP reads merely to recover this historical process status; the available V3 report already records health, initialize, read tools, fleets, ships, and master ships as PASS, while overall result and Python exit code remain unresolved.
- Safety: no MCP requests, no WebSocket, no game actions, no project files modified; `LIVE_DISPATCH=DISABLED`. Clipboard check is intentionally ignored per user preference.
- Conclusion: artifact inspection completed, but it did not recover process-exit evidence. Treat the read results as partial evidence only; session validity and data freshness remain unverified, and dispatch stays disabled.
- Provenance: this entry records the user-provided local preflight report. It does not claim assistant-side local execution or local filesystem changes.


### 2026-10-25 Follow-up after artifact preflight — keep session gate closed

- After the V3 artifact preflight, performed remote read-only source review only; no local process was launched and no additional Poi MCP request was made.
- Current `PoiExpeditionReader` validates bridge identity/readiness and normalizes fleet/ship/master data, but it does not establish login validity, homeport state, or payload freshness. Its `ObservedState.observed_at` timestamp is generated locally at read time and is not an independent source freshness signal.
- Current `tools/verify_poi_bridge.py` deliberately records `session_validity=NOT_VERIFIED`; its PASS result covers bridge protocol/read calls and structural summaries, not active game session or data freshness. The V3 wrapper's generic overall failure and missing process exit code remain unresolved historical capture issues; do not repeat reads just to recover them.
- The previously confirmed `info.basic` and `sortie` field names only establish that those fields exist in response payload shape. Without a verified field contract and trustworthy refresh/change signal, they cannot yet be used to authorize dispatch.
- Next work should be a source/schema-only investigation of a documented session/homeport indicator and a reliable Redux refresh/freshness signal. If no authoritative indicator exists, record the gate as unavailable rather than inferring from field presence, health status, or a newly generated observation timestamp.
- Safety remains: no WebSocket, no game actions, no local project file changes, and `LIVE_DISPATCH=DISABLED`.
- Provenance: GitHub source files were fetched read-only; this entry records source review and the user's artifact-preflight report, not local execution.


### 2026-10-25 Source/schema investigation — no authoritative session/freshness signal found in Nanodesu API

- Read-only review of the public upstream `momocow/poi-plugin-nanodesu-mcp` source at commit `2ba0a461ee44d64bf9d19b3467cf7565ad51b943`, focusing on `src/server.ts`, `src/poi.ts`, `src/paths.ts`, and `src/tools.ts`. No local runtime was contacted and no game actions were issued.
- `src/server.ts` implements `/health` by calling `getStore()` and checking only that the returned value is an object containing the `info` key; the response has service identity/version/port and `storeReady`, but no game-login state, homeport state, store revision, or last-refresh timestamp. The MCP request adapter obtains the store anew for each request. The server explicitly describes the API as stateless, with no subscriptions or retained session state.
- `src/poi.ts` shows the plugin's supported bridge into Poi is `window.getStore`; it does not expose a documented Redux revision counter or refresh timestamp. `src/paths.ts` allowlists data roots such as `info` and `sortie`, but path availability itself is not evidence of login or freshness.
- Consequently, the already observed fields under `info.basic` (including `api_active_flag`) and `sortie` (`sortieStatus`, `sortieMapId`, `currentNode`) can be considered only candidate state indicators until their semantics are grounded in the relevant Poi/KanColle reducer or API source. Their mere presence cannot prove the current session is authenticated, at homeport, or freshly updated. A stable hash over a short interval likewise does not prove either freshness or staleness.
- **Decision:** do not create a PASS gate from Nanodesu health/storeReady or a locally generated `ObservedState.observed_at`. No authoritative session/homeport + independent freshness signal has been identified in the inspected bridge API. Keep this gate UNKNOWN/UNAVAILABLE and `LIVE_DISPATCH=DISABLED` unless a separately sourced, trustworthy indicator is found.
- Next source-only step: trace the definitions and update triggers for `info.basic.api_active_flag` and `sortie.sortieStatus` in Poi's own Redux reducers and upstream API handling; determine exactly what each can prove and whether any timestamp/revision is maintained. If the source contains no reliable freshness indicator, stop searching for one in Nanodesu's health response and retain the fail-closed gate.
- Safety: no local files changed, no MCP requests, no WebSocket, no game actions, no installations or system/proxy changes. This is public upstream static-source analysis only; it does not verify the user's installed plugin version or live game state.


### 2026-10-25 Poi Redux source trace — homeport reset is meaningful, freshness still unproven

- Read-only source review of official `poooi/poi` repository tree at commit `fb76074ce457808be4df9fc97ab2d73cb56ee2f7`: `views/redux/info/basic.ts`, `views/redux/info/index.ts`, and `views/redux/sortie/index.ts`; cross-checked the `api_get_member/basic` response type and ElectronicObserver API list. No local runtime or MCP endpoint was contacted.
- `info.basic` is populated from `api_port/port`'s `api_basic`; the `api_get_member/require_info` handler only merges `api_member_id`, and mission/practice/battle results update selected level/experience fields. The Poi reducer does not maintain an update timestamp or monotonic revision. The source does not use `api_active_flag` as a local session-validity gate. External API type declarations confirm the field exists, but do not establish a reliable meaning for dispatch authorization.
- `sortie.sortieStatus` is a four-boolean array initialized false. Poi sets a fleet flag on `api_req_map/start` and resets sortie state on `api_port/port` response (also on a start2 option-setting request). Thus an observed all-false array is consistent with a port/reset state but is not, by itself, proof of an authenticated homeport; it can also be the initial state or a reset triggered by other app flow. A true flag is useful evidence of a tracked sortie, not evidence that dispatch should be enabled.
- The `api_port/port` reducer event is the strongest source-level homeport-related signal found in this pass, because Poi explicitly resets sortie tracking when that response arrives. However, the state exposed by `getStore()` contains no receipt time/revision for that event. Reading the current state cannot establish how recently the event occurred. `ObservedState.observed_at` remains only the local read time, not the game's update time.
- **Decision:** source tracing clarified candidate semantics but did not yield a sufficient session + freshness gate. Do not infer session validity from `api_active_flag`, `sortieStatus`, or health/storeReady. Keep `session_validity=NOT_VERIFIED` and `LIVE_DISPATCH=DISABLED`. A future trustworthy gate would need a separately observed response/event receipt timestamp or sequence number plus an explicitly sourced session/homeport criterion; do not add a guessed timestamp to existing store data.
- Next step: check whether Poi's existing API action payload/response metadata exposes a response-received timestamp or sequence at a supported read-only path. If not, document that the bridge lacks independent freshness evidence and keep dispatch blocked rather than adding invasive instrumentation.
- Safety: no local files changed, no MCP requests, no WebSocket, no game actions, no installations or system/proxy changes. These findings are from public upstream source and do not prove the user's installed Poi build matches this commit.


### 2026-10-25 Poi response timestamp trace — timestamp exists on events, not in exposed Redux state

- Read-only review of official `poooi/poi` source at commit `fb76074ce457808be4df9fc97ab2d73cb56ee2f7`: `lib/game-api-broadcaster.ts`, `views/redux/reducer-factory.ts`, `views/redux/actions/types.ts`, `views/redux/info/basic.ts`, and `views/redux/sortie/index.ts`.
- Positive finding: `GameAPIBroadcaster.sendResponse()` emits `network.on.response` with `Date.now()` after it has parsed a response body and checked HTTP status 200. The timestamp is carried as `time`; `GameResponsePayload` includes `time: number`; `onGameResponse()` passes that timestamp into the Redux action payload and top-level action object. This is a local response-observation time, not a timestamp issued by the game server.
- Limitation: the inspected reducers update business state but do not persist the response `time` alongside `info.basic` or `sortie`. The Nanodesu MCP adapter exposes `getStore()` paths and serialized store values, not the Redux action stream or broadcaster events. Therefore this useful timestamp is not currently available through the inspected read-only store path, and cannot be reconstructed from current state values alone.
- A timestamp on an event would help measure freshness only if a trusted read-only bridge exposed it for the relevant latest successful response and bound it to the correct game/session identity. A generic last-response time would not, by itself, prove that the game is authenticated or at homeport. No live runtime or MCP request was used to test this.
- **Decision:** retain `session_validity=NOT_VERIFIED` and `LIVE_DISPATCH=DISABLED`. Do not infer freshness from state read time, value hashes, or `api_active_flag`. A future change would require an explicitly authorized, minimal read-only event-metadata exposure and a separate sourced session/homeport criterion; no instrumentation or code change was made in this round.
- Next step: inspect the Nanodesu MCP plugin's existing public adapter/event integration to determine whether it can access this response timestamp without adding a WebSocket or using write dispatch. If not, report the missing timestamp exposure as a bridge limitation and keep the fail-closed gate.
- Safety: public-source review only; no local files changed, no MCP requests, no WebSocket, no game actions, no installations or system/proxy changes. The checked Poi commit may differ from the user's installed build.


### 2026-10-25 Nanodesu adapter boundary trace — no existing read-only response-time path found

- Read-only source review of `momocow/poi-plugin-nanodesu-mcp` at commit `2ba0a461ee44d64bf9d19b3467cf7565ad51b943`: `index.ts`, `src/poi.ts`, `src/server.ts`, `src/tools.ts`, and the design document.
- The plugin resolves `window.getStore` and each MCP read calls it on demand; tools resolve and serialize current store paths. The plugin does not subscribe to Poi's Redux action stream or `GameAPIBroadcaster` `network.on.response` event. Its request log/status concerns MCP requests, not game API response receipts. The design explicitly excludes pushed events and snapshot/API-traffic history; transport is stateless and pull-based.
- Therefore the Poi event timestamp found in the prior pass cannot be obtained through the existing Nanodesu read-only tool/path API. No evidence in this source pass supports treating a store field or health response as equivalent. A future timestamp feature would require an explicit new read-only exposure (and likely an event hook or maintained metadata), which is a code change and is outside this read-only round.
- **Decision:** do not modify the plugin or add event capture now. Record the current bridge as lacking independently exposed API-response freshness evidence. Keep `session_validity=NOT_VERIFIED` and `LIVE_DISPATCH=DISABLED`; do not infer a valid session/homeport from `storeReady`, `api_active_flag`, or an all-false `sortieStatus`.
- **Next safe option:** perform a local, read-only version/provenance check of the installed Poi and Nanodesu plugin files (paths and hashes only, no MCP requests) to establish whether the reviewed upstream source matches the actual runtime. Then decide whether a separately approved minimal read-only metadata extension is worth designing; do not implement it without explicit approval.
- Safety: public upstream source only; no local files changed, no MCP requests, no WebSocket, no game actions, no installations or system/proxy changes. Installed runtime equivalence remains unverified.


### 2026-10-25 Local Poi provenance preflight — executable identified, plugin path unresolved

- User-provided read-only preflight report identifies the running Poi executable at `D:\\Games\\poi\\poi.exe`, product/file version `12.0.1`, SHA-256 `A41664FB46DB3AB49B38D4CEF2D6DC482E35EE0C30D3681DD744CE4E0D4870C5`. The main process PID was 20680 with remote debugging port 9222; child renderer/utility processes were also present.
- The preflight's limited directory candidates did not find a directory matching `nanodesu` or `mcp`. This is **not proof the plugin is absent**: discovery only checked selected likely roots and directory names. The actual MCP endpoint had been observed in earlier user-provided context, but no live request was made in this pass.
- Local worktree report: `dev/expedition-closed-loop-mvp`, HEAD `ec6a18879a80c0a6786296eb76de7e5ea5f234b6`, clean. This is a local observation and does not imply the local branch includes the newest remote progress-document commits.
- **Decision:** runtime/source equivalence remains unverified because the installed plugin directory, manifest version, and source hashes were not located. Continue with a bounded read-only directory/package-manifest inventory under the Poi data root and installed app resources; do not inspect secrets/config values, modify files, issue MCP requests, use WebSocket, or perform game actions.
- Safety state unchanged: `SESSION_VALIDITY=NOT_VERIFIED`, `LIVE_DISPATCH=DISABLED`. No network/MCP/WebSocket/game actions, local project edits, installations, or system/proxy changes were reported in the submitted preflight.

## 2026-10-25 — Bounded Poi plugin manifest discovery V2

- User-provided read-only report completed at 2026-10-25 16:42:04 +08:00. Searched roots: `%APPDATA%\poi` (1,632 directories), `%APPDATA%\poi\plugins` (1,591 directories), and `D:\Games\poi\resources\app.asar.unpacked` (20 directories). `%APPDATA%\poi\node_modules`, `D:\Games\poi\resources\app`, and `D:\Games\poi\plugins` were absent.
- No matching `package.json` manifest for Nanodesu MCP was found in the bounded search. This does **not** prove the plugin is absent: code may be bundled in `app.asar`, use a different package/directory name, or be outside the searched roots.
- The user reported no network/MCP requests, WebSocket, game actions, file modifications, or system/proxy changes. The script reported no errors.
- **Decision:** installed plugin identity/version and source equivalence remain unverified. Do not claim the plugin is absent or identical to the public source. A further step, if needed, should be a separately approved, bounded, read-only inspection of package/archive metadata only; avoid extracting or modifying the installed app.
- Safety gate unchanged: `SESSION_VALIDITY=NOT_VERIFIED`; `LIVE_DISPATCH=DISABLED`. No MCP request, WebSocket use, or game action is authorized by this result.

## 2026-10-25 — Poi app.asar read-only metadata preflight

- User-provided report completed at 2026-10-25 17:02:59 +08:00. `D:\\Games\\poi\\resources\\app.asar` exists as a 121,224,523-byte file; reported last-write time `2026-10-19 16:21:45`; SHA-256 `0B8F90E489F400A3659DAEB639800CDFED85C93171C40FA7FCE9EDB364CB5D0A`.
- `D:\\Games\\poi\\resources\\app.asar.unpacked` exists; `D:\\Games\\poi\\resources\\app` was not found. This is metadata-only evidence; archive contents have not been inspected, extracted, or modified, and it does not establish the installed MCP plugin's identity/version.
- Reported safety state: no network/MCP requests, WebSocket use, game actions, file modifications, or system/proxy setting changes; no script errors.
- **Decision:** if continuing, inspect only the ASAR archive header/index metadata in a bounded, read-only manner; do not extract files or modify the installation. Do not assume Nanodesu MCP is present based on the archive's existence.
- Safety gate unchanged: `SESSION_VALIDITY=NOT_VERIFIED`; `LIVE_DISPATCH=DISABLED`.

## 2026-10-25 — Poi app.asar index read-only preflight V4

- Result: `JSON_PARSE=PASS`; the read-only parser located the JSON index at byte offset 16.
- Archive metadata: `D:\Games\poi\resources\app.asar`; length 121,224,523 bytes.
- Header fields: pickle size 4; header size 7,816,984; string size 7,816,980; JSON size 7,816,973.
- Traversal: 34,968 index entries visited; 0 paths matched `nanodesu`, `poi-plugin-nanodesu-mcp`, or `mcp`.
- Safety: archive not extracted or modified; no network, MCP, WebSocket, or game actions; no project files or system/proxy settings changed.
- Gate state remains `SESSION_VALIDITY=NOT_VERIFIED` and `LIVE_DISPATCH=DISABLED`.
- Interpretation limit: this only rules out matching path names in the inspected `app.asar` index; it does not prove the plugin is absent from external plugin directories or under other names.



## 2026-10-25 — Poi plugin source discovery V1 (user-reported local read-only scan)

- User-provided report completed at 2026-10-25 17:27:06 +08:00. Roots scanned: `%APPDATA%\poi\plugins` (10,887 files enumerated) and `D:\Games\poi\resources\app.asar.unpacked` (77 files enumerated); 277 `package.json` files were counted.
- **Direct local manifest evidence:** `poi-plugin-nanodesu-mcp` version `0.2.0`, entry `index.ts`, found at `%APPDATA%\poi\plugins\node_modules\poi-plugin-nanodesu-mcp\package.json`. Its nested `@modelcontextprotocol/sdk` dependency is version `1.32.1`.
- **Additional MCP plugin evidence:** source-string matches identify `%APPDATA%\poi\plugins\node_modules\poi-plugin-mcp\mcp-server.js`, `lib\poi-http-bridge.js`, `lib\settings-view.js`, and `lib\settings.js`; its package manifest text reports name `poi-plugin-mcp`, version `0.2.29`, main `index.js`, and bin `poi-mcp`. This manifest was among those the PowerShell 5.1 JSON parser reported as malformed, so the metadata is based on the visible manifest text and should be revalidated with an encoding-aware/raw bounded read if needed.
- Source scan reported 5,395 files scanned, 149 matching files, 7 large files skipped, and no enumeration-limit roots. Many matches are SDK examples/type declarations or unrelated numeric occurrences; these are leads, not 149 independent plugin findings.
- Four package manifests failed JSON parsing because the displayed author/title text contained malformed or mojibake bytes: `poi-plugin-anchorage-repair`, `poi-plugin-item-info`, `poi-plugin-mcp`, and `poi-plugin-ship-info`. These errors do not indicate that the plugins failed to load; they indicate the scan could not parse those manifest contents as JSON.
- Clipboard copy reported `FAILED`; per user preference, clipboard failure is non-blocking and was not debugged.
- Safety reported: no network/MCP/WebSocket/game actions, no file modifications, and no system/proxy changes. `SESSION_VALIDITY=NOT_VERIFIED`; `LIVE_DISPATCH=DISABLED`.
- **Conclusion:** unlike the earlier bounded search, this report positively locates a Nanodesu MCP package directory and also finds a separate `poi-plugin-mcp` implementation. This establishes on-disk package presence, not that either plugin is currently loaded, listening, or compatible with the running Poi build. Do not send MCP requests or enable dispatch as part of this discovery.
- **Next step:** perform a narrowly scoped, read-only provenance check on just these two package directories: read their package manifests and entry-file metadata/hashes (no source-wide recursion), compare with public upstream only if needed, and inspect configuration only for non-secret endpoint/port fields. Keep live dispatch disabled and session validity unverified.

## 2026-10-25 — Poi independent plugin source discovery

- Task: `KANCOLLEBOT_POI_PLUGIN_SOURCE_DISCOVERY_V1`
- Mode: `READ_ONLY`; local report timestamp: 2026-10-25 17:29:05–17:29:33 +08:00.
- Search roots:
  - `%APPDATA%\poi\plugins`: exists; 10,887 files enumerated.
  - `D:\Games\poi\resources\app.asar.unpacked`: exists; 77 files enumerated.
  - No enumeration limit was hit.
- Manifest discovery: 277 `package.json` files encountered. Two relevant manifests identified:
  - `poi-plugin-nanodesu-mcp`, version `0.2.0`, declared main `index.ts`, at `%APPDATA%\poi\plugins\node_modules\poi-plugin-nanodesu-mcp\package.json`.
  - `@modelcontextprotocol/sdk`, version `1.32.1`, nested under the Nanodesu package.
- Source scan: 8,451 files scanned; 154 matching files reported; 11 large files skipped. The Nanodesu package `index.ts` matched `McpServer` and `poi-plugin-nanodesu-mcp`. Separate `poi-plugin-mcp` package source matched `17777` and `tools/list`; do not conflate the two packages.
- Manifest parsing errors: four manifests failed parsing due to malformed/encoding-corrupted text in local files: `poi-plugin-anchorage-repair`, `poi-plugin-item-info`, `poi-plugin-mcp`, and `poi-plugin-ship-info`. This does not invalidate the two positively identified package manifests, but means not every manifest was parsed successfully.
- Verified conclusion: the Nanodesu MCP package exists in the independent Poi user plugin tree. This does not establish that Poi currently loads it, that its service is running, or that its game-state data is fresh/valid.
- Safety: no network requests, MCP requests, WebSocket use, game actions, file modifications, or system/proxy changes were reported. `SESSION_VALIDITY=NOT_VERIFIED`; `LIVE_DISPATCH=DISABLED`.
- Clipboard: reported `FAILED`; not investigated, per user preference.
- Next step: read the installed Nanodesu package metadata and source as text only to identify its plugin entry/lifecycle and read-only data path. Do not execute plugin code, contact local endpoints, or enable live dispatch.



## 2026-10-25 — Installed Poi MCP plugin entrypoint review V1

- Task: `KANCOLLEBOT_POI_PLUGIN_ENTRYPOINT_REVIEW_V1`; user-provided local report timestamp 2026-10-25 17:30:46 +08:00; read-only static inspection only.
- Nanodesu package path: `%APPDATA%\\poi\\plugins\\node_modules\\poi-plugin-nanodesu-mcp`; manifest reports `poi-plugin-nanodesu-mcp@0.2.0`, `main=index.ts`, Poi metadata id `poi_nanodesu_mcp`, title `なのです`, and description “Expose live game state to agents over MCP”.
- Its `index.ts` imports the POI store resolver, dispatch resolver, server, settings/status panels, battle reader, and related helpers. `pluginDidLoad()` checks for the main window, resolves `window.getStore`, reads port configuration, and starts the MCP server; the reported source contains a log template for listening on `http://127.0.0.1:<port>/mcp`. This is static source evidence only, not proof that the plugin is currently loaded or listening.
- A separate package exists at `%APPDATA%\\poi\\plugins\\node_modules\\poi-plugin-mcp`. Its manifest text reports `poi-plugin-mcp@0.2.29`, main `index.js`, and metadata describing a local Poi state/WebView capture bridge with opt-in authenticated input. The reported manifest could not be parsed by the local PowerShell JSON parser due to malformed/encoding-corrupted text; treat its displayed fields as text evidence, not validated JSON metadata. Its source references port 17777, a `~/.poi-mcp/port` file, and local HTTP routes. Do not conflate this package with Nanodesu.
- The separate `poi-plugin-mcp` source includes input-capable code paths and an `inputEnabled: false` default in `lib/settings.js`; this does not establish actual runtime configuration or prove the input paths are unreachable. Do not call its endpoints or enable input as part of this review.
- Reported safety: no code execution, network requests, MCP requests, WebSocket use, game actions, file modifications, or system/proxy changes. `SESSION_VALIDITY=NOT_VERIFIED`; `LIVE_DISPATCH=DISABLED`. Clipboard copy reported failed and was not investigated.
- **Conclusion:** two distinct MCP-related plugin packages are present on disk. Nanodesu's entrypoint is statically identified, but neither package's loaded state, active port, runtime compatibility, authenticated session state, nor game-data freshness is verified.
- **Next step:** continue bounded read-only inspection of Nanodesu's server/tool implementation and the installed source's relevant read-only paths; do not execute code or contact local endpoints. Keep live dispatch disabled unless a separately authorized safety gate is established.


## 2026-10-25 — Nanodesu server/tools review V1 (report completeness limitation)

- User-provided report for `KANCOLLEBOT_NANODESU_SERVER_TOOLS_REVIEW_V1`, timestamp 2026-10-25 17:31:33 +08:00, states the intended plugin root exists and reports no errors or external/runtime actions.
- **Important limitation:** the returned report contains only task/root/safety/error summary lines. It does not include any per-file existence, size, hash, line count, or source-match sections, so it provides no new evidence about the contents of `src/server.ts`, tool definitions, write-capable operations, request limits, or read-only data paths. A zero error count cannot be interpreted as proof that those files were inspected.
- Safety reported: no code execution, network/MCP requests, WebSocket, game actions, file modifications, or system/proxy changes. `SESSION_VALIDITY=NOT_VERIFIED`; `LIVE_DISPATCH=DISABLED`.
- **Decision:** do not infer server/tool behavior from this incomplete output. Re-run a smaller read-only inventory that lists only actual files directly under the installed Nanodesu package and its immediate `src` directory, including file names, byte sizes, and SHA-256; then inspect only confirmed-existing target files as text. Do not execute code or contact local endpoints.


## 2026-10-25 — Nanodesu file inventory V1 (report incomplete)

- User-provided `KANCOLLEBOT_NANODESU_FILE_INVENTORY_V1` report timestamp: 2026-10-25 17:31:59 +08:00.
- Report states the root path and safety conditions, with `ERRORS: NONE`; however, it contains no directory-existence results, direct-file counts, filenames, sizes, or SHA-256 hashes. Therefore it does not establish whether the inventory loop ran or what files exist.
- No code execution, network/MCP requests, WebSocket, game actions, file changes, or system/proxy changes were reported. `SESSION_VALIDITY=NOT_VERIFIED`; `LIVE_DISPATCH=DISABLED`.
- **Next diagnostic:** avoid another broad script. Use a minimal direct listing of only the known `index.ts` and `src\server.ts` paths, reporting each path's existence and byte size. If this still produces summary-only output, treat it as a report/rendering issue and ask for the actual PowerShell console output rather than infer file state.

### 2026-10-25 — Nanodesu two-file existence and hash check

- Task: `KANCOLLEBOT_NANODESU_TWO_FILE_CHECK_V1`.
- Local root reported by user: `C:\Users\QuirrellRevess\AppData\Roaming\poi\plugins\node_modules\poi-plugin-nanodesu-mcp`.
- Read-only file check confirmed `index.ts` exists; size **4,772 bytes**; SHA-256 `82ECFB964ADD4209C440D63A389C0AE530CC69E0B813DA62F2A5EB66969732B1`.
- Read-only file check confirmed `src\server.ts` exists; size **21,564 bytes**; SHA-256 `D90C13A3C75CB32EE99003D519AF90701549897250BA2C76386B3875BBD837FF`.
- Scope limitation: existence and hashes are verified from the user-provided PowerShell report; this result does **not** establish that either source file's logic has been reviewed.
- Reported safety state: no network requests, MCP requests, game actions, or file modifications; `LIVE_DISPATCH=DISABLED`; `SESSION_VALIDITY=NOT_VERIFIED`.
- Next step: retrieve the relevant source excerpts locally in read-only mode (especially server initialization, tool registration/handlers, request routing, and any dispatch-capable paths); review before considering any execution or live integration.

### 2026-10-25 — Nanodesu server source review

- User-provided read-only source dump reviewed for `poi-plugin-nanodesu-mcp@0.2.0`.
- Verified source identities from the supplied report:
  - `index.ts`: 4,772 bytes; SHA-256 `82ECFB964ADD4209C440D63A389C0AE530CC69E0B813DA62F2A5EB66969732B1`.
  - `src/server.ts`: 21,564 bytes; SHA-256 `D90C13A3C75CB32EE99003D519AF90701549897250BA2C76386B3875BBD837FF`.
- MCP tools registered unconditionally in the reviewed server source: `poi_get`, `poi_lookup`, `poi_battle`, `poi_describe`.
- Conditional write capability: `poi_hensei_save` is registered only when both `dispatch` and `hensei` are defined (`src/server.ts:332-385`). Its handler calls `poiHenseiSave({ store: getStore(), dispatch, calc: hensei }, args)`; it can change Poi live state and the Hensei Nikki plugin writes the record to disk immediately according to the tool description. Do not invoke this tool during read-only verification.
- `index.ts:87-99` attempts to resolve Poi dispatch and load the Hensei calculator; failure to obtain either leaves the write tool unregistered. Source inspection alone does not establish which runtime branch is active.
- Default server host is `127.0.0.1` and default port is `12450` (`src/server.ts:35-38, 407-410`); actual port is configuration-driven at plugin entry.
- `GET /health` reports `storeReady` when the store is an object containing `info` (`src/server.ts:415-441`). This is a shallow readiness check; it does not prove authenticated game session validity or data freshness.
- `POST /mcp` is the MCP route; the server creates a fresh stateless transport per request. Request body limit is 1 MiB (`src/server.ts:39, 443-495`).
- Safety: source was reviewed from user-provided text only. No source was executed; no network/MCP/WebSocket requests, game actions, or file modifications were performed. Maintain `LIVE_DISPATCH=DISABLED` and `SESSION_VALIDITY=NOT_VERIFIED`.
- Next read-only review: inspect `src/tools.ts`, `src/poi.ts`, `src/validator.ts`, `src/battles.ts`, and relevant tests/package scripts. Determine exact path filtering, serialization/size bounds, argument validation, and the write handler's guards without calling the MCP server.



### 2026-10-25 — Nanodesu tool boundary review V1

- Task `KANCOLLEBOT_NANODESU_TOOL_BOUNDARY_REVIEW_V1`; user supplied a read-only source dump dated 2026-10-25 18:14:55 +08:00. The report states that no code was executed, no network/MCP/WebSocket requests or game actions occurred, and no files or system/proxy settings were changed.
- Source identity: `src/tools.ts`, 17,947 bytes / 566 lines, SHA-256 `2E6E600466815AFC10342A77B1511B72EC99D127DDCA9A7D70D35C88ACCA8016`; `src/poi.ts`, 4,962 bytes / 149 lines, SHA-256 `A4F5E3179980F8F5927BF244C9CE2DC343AA7B76715BEC640CBDDD86577BE77F`.
- `poi_get` reads store data through `resolveStorePath`, applies query/select/limit and byte bounds. Defaults: 200 rows and 65,536 bytes; hard maximum 262,144 bytes. Path protections in `src/paths.ts` remain to be reviewed.
- `poi_lookup` uses a fixed allowlist of 13 master-data kinds, requires explicit IDs, and caps a call at 200 IDs.
- `poi_battle` reads saved battle records through an injected reader, caps each call at 50 IDs, and applies output byte bounds.
- `poi_describe` describes data structure and collection time ranges. Its path safety also depends on `resolveStorePath`.
- `poi_hensei_save` is write-capable. It requires a Hensei calculator and Poi dispatch function, checks that the destination state exists, requires exactly one of `decks` or `code`, rejects empty compositions, and refuses a duplicate title unless `overwrite: true`. At `src/tools.ts:494`, it calls `dispatch(buildSaveAction(...))`; source comments state that Hensei Nikki persists the resulting record. Do not call this tool in read-only verification.
- `src/poi.ts` reads Poi globals including `isMain`, `getStore`, `dispatch`, `config`, and `APPDATA_PATH`. `resolveGetStore` and `resolveDispatch` throw when their required globals are absent. `readPortConfig` validates ports in the range 1–65535 and falls back on invalid/unavailable configuration. `writeConfig` can persist a setting through Poi's config API; it was not called. Source-level capability does not establish current runtime configuration.
- Safety remains `LIVE_DISPATCH=DISABLED` and `SESSION_VALIDITY=NOT_VERIFIED`. No MCP endpoint should be called for this review.
- Next read-only review: inspect `src/paths.ts`, `src/query.ts`, `src/serialize.ts`, `src/validator.ts` if present, and `src/hensei.ts` to assess path traversal protections, query/selection behavior, serialization bounds, schema validation, and persistence semantics. Compare registered tool schemas to handler contracts without sending requests.


## 2026-10-25 — Nanodesu path/query/serialization boundary review V2

- User-provided local read-only report completed at 2026-10-25 18:16:01 +08:00 for installed package `poi-plugin-nanodesu-mcp@0.2.0`. Root: `%APPDATA%\\poi\\plugins\\node_modules\\poi-plugin-nanodesu-mcp`.
- Source identity from the report:
  - `src/paths.ts`: 14,391 bytes; SHA-256 `74B4FFE37347A3118A86355FC7FA6856D39871FB6C44C47B33CE6BBF8F0D7D81`.
  - `src/query.ts`: 16,464 bytes; SHA-256 `12741D8BCE824DBDB1CEF714BEC85B95A2711B9C946BFBBC996618FF3A9F7F27`.
  - `src/serialize.ts`: 4,096 bytes; SHA-256 `6231F938C5372DA767BACF2FD150705951DE83D575F214C17906D7751C1B1DE2`.
  - `src/hensei.ts`: 6,087 bytes; SHA-256 `4440FF8C85CDBFE4A2FDC8DC85197A45AB2AF299BD05905A8DEBC8711DE87C11`.
  - `src/validator.ts`: 1,793 bytes; SHA-256 `8DAB2F723B806D8E4C7D3A1E91BAFBDF1D38FEF08DD6540721B4DFF6AB79E180`.
  - `src/server.ts`: 21,564 bytes; SHA-256 `D90C13A3C75CB32EE99003D519AF90701549897250BA2C76386B3875BBD837FF`.
  - `index.ts`: 4,772 bytes; SHA-256 `82ECFB964ADD4209C440D63A389C0AE530CC69E0B813DA62F2A5EB66969732B1`.
  - `package.json`: 746 bytes; SHA-256 `40215403960E8B3BE81DFE52DF627C9FD1000E1B597811CAEE6FA80798A670C1`.
- Static path policy: `src/paths.ts` exposes a fixed set of store roots and an explicit allowlist of plugin state under `ext`. Whole `ext`, `config`, `layout`, `plugins`, `ui` and arbitrary `ext` plugin state are denied. Battle Detail is narrowed to `_.indexes` because a sibling field contains an Immutable.js List.
- Static query behavior: `where` is parsed into a restricted expression grammar; `select` projects requested paths; `limit` truncates the matched result set. In `applyQuery`, arrays and object maps are filtered before slicing to the requested limit, so `limit` caps returned rows but does not cap the number of entries scanned.
- Static serialization behavior: `toJsonSafe` defaults to depth 16, omits functions/symbols/undefined object fields, converts non-finite numbers to null, stringifies bigint, and marks cycles/depth overflow. `fitToBytes` rejects oversized scalar/object values and trims collection results to the largest prefix that fits the byte cap, updating `returned` and `truncated`.
- Potential path-boundary issue to investigate, not yet a confirmed vulnerability: path traversal uses ordinary property access and `key in container` checks. A dedicated offline test should check inherited-property names such as `constructor` and `__proto__`, including behavior of `resolveStorePath` and query/select field paths. No exploit was run and no runtime request was made.
- Write semantics: `src/hensei.ts` builds action `@@HENSEI_SAVE_DATA` for `poi-plugin-hensei-nikki`; the handler path dispatches the action, and the companion plugin's observer is documented as persisting its state to a JSON file. This remains a write-capable operation and must not be invoked during read-only validation. The local runtime registration branch remains unverified.
- Source review report stated: no code execution, network/MCP requests, WebSocket use, game actions, file modifications, or system/proxy changes. Safety gate remains `SESSION_VALIDITY=NOT_VERIFIED`; `LIVE_DISPATCH=DISABLED`.
- Next step: read the complete `src/validator.ts` and the relevant tool schemas/handlers around `poi_get`, `poi_describe`, and `poi_hensei_save`; design a pure offline path test for inherited-property edge cases without contacting Poi or dispatching actions. Do not treat source-level review as runtime validation.


## 2026-10-25 — Nanodesu path boundary review V3 (incomplete report)

- User-provided local report for `KANCOLLEBOT_NANODESU_PATH_BOUNDARY_REVIEW_V3` ended at 18:16:59 +08:00. It reports PowerShell 5.1, read-only mode, no source execution, network/MCP requests, WebSocket use, game actions, file modifications, or system/proxy changes; `LIVE_DISPATCH=DISABLED` and `SESSION_VALIDITY=NOT_VERIFIED`.
- The report does not include `ROOT_STATUS`, package metadata, per-file review sections, file hashes, or read errors. The claimed `SOURCE_INSPECTION_COMPLETED` is not sufficient evidence that source files were inspected. Do not infer that the plugin root was present or that source review passed.
- Existing V2 evidence supports a potential concern, not a confirmed vulnerability: `src/paths.ts` uses `key in container` and ordinary property access during path resolution; query/select field access uses ordinary property access. Inherited names such as `constructor` and `__proto__` have not yet been tested, even against offline synthetic objects.
- Existing findings remain: explicit allowed store roots and plugin-state allowlists; query filtering happens before limit slicing, so limit bounds returned rows rather than scan work; serialization defaults to depth 16 and byte-caps collection output; `poi_hensei_save` is the known dispatch-backed write path and must remain unused.
- Next: correct the read-only report script so it always prints root validation and per-file status, including explicit `NOT_FOUND`/`READ_ERROR` records, then run only offline synthetic-object tests for inherited-property behavior. Do not contact Poi, call MCP tools, use WebSockets, or dispatch actions. Runtime/session validity remains unverified.


## 2026-10-25 — Nanodesu path boundary review V4

- User-provided local report for task `KANCOLLEBOT_NANODESU_PATH_BOUNDARY_REVIEW_V4`, timestamp 2026-10-25 18:17:51–18:17:53 +08:00. Reported PowerShell 5.1 read-only mode and `ROOT_STATUS: PRESENT`; package metadata parsed as `poi-plugin-nanodesu-mcp@0.2.0`, main `index.ts`.
- The report records `READ_OK` for all seven expected source files: `src/paths.ts` (14,391 bytes; SHA-256 `74B4FFE37347A3118A86355FC7FA6856D39871FB6C44C47B33CE6BBF8F0D7D81`), `src/query.ts` (16,464 bytes; `12741D8BCE824DBDB1CEF714BEC85B95A2711B9C946BFBBC996618FF3A9F7F27`), `src/serialize.ts` (4,096 bytes; `6231F938C5372DA767BACF2FD150705951DE83D575F214C17906D7751C1B1DE2`), `src/hensei.ts` (6,087 bytes; `4440FF8C85CDBFE4A2FDC8DC85197A45AB2AF299BD05905A8DEBC8711DE87C11`), `src/server.ts` (21,564 bytes; `D90C13A3C75CB32EE99003D519AF90701549897250BA2C76386B3875BBD837FF`), `src/tools.ts` (17,947 bytes; `2E6E600466815AFC10342A77B1511B72EC99D127DDCA9A7D70D35C88ACCA8016`), and `src/poi.ts` (4,962 bytes; `A4F5E3179980F8F5927BF244C9CE2DC343AA7B76715BEC640CBDDD86577BE77F`).
- Static scan printed 266 matching lines with zero reported read/hash errors. Output was capped per file (45 matches maximum), so the displayed snippets are search leads rather than a complete code review. `src/paths.ts` output includes `key in container` and `plugin in ext` checks; this warrants targeted offline testing but is not proof of exploitable traversal. `src/query.ts` requires direct implementation review to establish how field-path reads behave.
- `src/serialize.ts` snippets confirm a default depth cap of 16, cycle/depth markers, JSON byte sizing, and collection trimming to a byte cap. `src/server.ts` statically imports filesystem write helpers and shows tool registration code, including a conditional write-tool branch. `src/tools.ts` shows `poiHenseiSave` calling `dispatch(buildSaveAction(...))` at line 494. This remains the known write-capable path and must not be invoked.
- Safety reported: no source execution, network/MCP/WebSocket requests, game actions, file modifications, or system/proxy changes; `LIVE_DISPATCH=DISABLED`; `SESSION_VALIDITY=NOT_VERIFIED`. The report explicitly says inherited-property tests were not executed.
- **Conclusion:** V4 repairs the missing inventory/evidence sections from V3 and establishes local on-disk presence plus file hashes. It does not establish runtime plugin registration, session validity, vulnerability exploitability, or that the plugin is safe for live use.
- **Next step:** run a standalone offline synthetic-object test (not importing or executing plugin code) contrasting `in`, ordinary property access, and `Object.prototype.hasOwnProperty.call` for `constructor`, `__proto__`, and `toString`. Then inspect the complete relevant source logic around path resolution and query/select field access, still without contacting Poi/MCP or invoking any dispatch/write path.


## 2026-10-25 — Local Nanodesu source availability and upload scope

- V4 local report establishes that the installed plugin source is present at `C:\Users\QuirrellRevess\AppData\Roaming\poi\plugins\node_modules\poi-plugin-nanodesu-mcp`, including `src/paths.ts`, `src/query.ts`, `src/serialize.ts`, `src/hensei.ts`, `src/server.ts`, `src/tools.ts`, and `src/poi.ts`; the report contains file hashes for each.
- GitHub branch `dev/expedition-closed-loop-mvp` has a Python project and existing `tests/` suite plus Nanodesu integration documentation, but the inspected `src/` directory currently lists `src/kancollebot/`, not the installed Nanodesu TypeScript source files. The local installed plugin source has not been uploaded to the repository in this work round.
- The next code review needs the complete `src/paths.ts` and `src/query.ts` contents; `src/serialize.ts` and `src/server.ts` are useful secondary context. Do not upload the whole plugin package or its dependencies. Keep the repository private and use a separate research branch; exclude credentials, runtime data, caches, and all unrelated plugin files.
- Upload is not yet performed or claimed. Earlier project policy says not to copy full POI/KCA source trees without separate approval; this request concerns a narrowly scoped set of installed plugin source files only. Await explicit confirmation before uploading those files.
- Network access for GitHub repository reads succeeded through the connected GitHub integration during this turn; this does not verify local Clash connectivity or authorize any system/proxy changes. No local files were accessed directly or modified.

## 2026-10-25 — Upstream Nanodesu repository identified

- Identified the upstream public repository: `momocow/poi-plugin-nanodesu-mcp` (https://github.com/momocow/poi-plugin-nanodesu-mcp).
- GitHub API readback confirms the repository is public, TypeScript, and uses `main` as its default branch.
- Upstream `main` package metadata currently reports version `0.1.0`; the local installed package was reported as `0.2.0` during the read-only preflight.
- Decision: use the upstream repository directly as the public baseline; do not re-upload upstream files unnecessarily. The four local files may still be valuable because the local package version differs, but their source-level differences must be established before deciding whether to archive them in this private research repository.
- No source files were uploaded in this round; no local files, Poi runtime, game state, or system proxy were changed.
- Next step: inspect upstream source and history, then compare local version-specific findings using the previously recorded local SHA-256 values. Keep all analysis read-only.

### 2026-10-25 — Correction: Nanodesu archive destination was not created

- A user-reported 404 exposed that the previously suggested path `research/poi-plugin-nanodesu-mcp/src` did not exist on `dev/expedition-closed-loop-mvp` at the time checked.
- Read-only GitHub branch inspection confirmed the branch root contains `.github`, `docs`, `src`, `tests`, and `tools`; there is no root-level `research` directory.
- The local export report for `Nanodesu_SelectedSource_20261025_182202.zip` says `ARCHIVE_CONTENT_VERIFIED: True` and `GITHUB_UPLOAD: NOT PERFORMED`. Therefore the four files remain local; no remote source archive was created by that export.
- Do not claim the suggested destination URL exists until files are actually committed and remotely read back. Before a future direct upload, use an isolated temporary clone and stage only the four authorized files plus this progress document; stop on any destination conflict or authentication failure.
- Selected local files and SHA-256 values remain recorded in the conversation's V4 export report. No local files, POI runtime, game state, or system proxy were modified by this GitHub inspection.
- Next step: avoid uploading the ZIP to chat; use the user's local direct-upload workflow only if the user still wants a private copy. Otherwise continue reviewing the public upstream repository `momocow/poi-plugin-nanodesu-mcp` directly.


### 2026-10-25 — Source-of-truth correction: local Nanodesu version is authoritative

- User clarified that the public upstream code is old; future implementation analysis must use the user's locally exported Nanodesu source as the source of truth.
- Authoritative local export: `Nanodesu_SelectedSource_20261025_182202.zip`, locally verified per the user's report. Selected files and SHA-256: `src/paths.ts` 74B4FFE37347A3118A86355FC7FA6856D39871FB6C44C47B33CE6BBF8F0D7D81; `src/query.ts` 12741D8BCE824DBDB1CEF714BEC85B95A2711B9C946BFBBC996618FF3A9F7F27; `src/serialize.ts` 6231F938C5372DA767BACF2FD150705951DE83D575F214C17906D7751C1B1DE2; `src/server.ts` D90C13A3C75CB32EE99003D519AF90701549897250BA2C76386B3875BBD837FF.
- The archive report says local archive contents were verified, but GitHub upload was not performed. Do not treat the four files as present in this repository until a direct upload and remote readback are confirmed.
- The public repository `momocow/poi-plugin-nanodesu-mcp` may be used only for historical/contextual comparison, clearly labeled as upstream and not authoritative for the user's installed version.
- Safety boundary unchanged: source review remains static/read-only; do not execute plugin handlers, trigger MCP/network/WebSocket/game actions, or invoke `poi_hensei_save`.
- Next step: use the locally exported four files as the analysis baseline. If access to their actual contents is required, upload those selected files directly to the private repository and verify the exact remote paths and hashes before proceeding; do not ask the user to upload the ZIP to chat unless direct upload is unavailable.


## 2026-10-26 — Fail-closed review of the existing expedition normalizer

- Re-read `expedition_state_v2.py` and `expeditions/planner.py` from the target branch; this was a static GitHub source review only.
- Confirmed the normalizer currently maps `api_mission[0]` to a status and `api_mission[1]` to a mission ID when status is positive, while intentionally leaving return time unknown. The field-slot semantics remain unverified by authoritative live observations.
- The planner blocks a snapshot when it has a non-null mission ID but no trusted return time. However, if the status-slot assumption is wrong, an active expedition could be misclassified as no mission and potentially reach a readiness recommendation. Therefore the current normalized mission fields must not be used as a live-action authorization gate.
- No code was changed in this review. No local observation report was received in this turn, so live Poi health, `sortie` shape, tests, and the real expedition loop remain unverified/not run.
- Decision: avoid speculative code changes. Next evidence needed is one bounded read-only Poi observation of fleet mission slots and `sortie` shape; if static shape alone cannot establish transitions, compare passive observations before/after a manually operated expedition. No game actions from this workflow.


## 2026-10-26 18:01 +08:00 — Live passive Poi observation

- User-supplied local report: READ_ONLY; game actions DISABLED; local repository unchanged; no local GitHub sync.
- Health PASS, MCP initialize PASS, read-only tools poi_describe/poi_get/poi_lookup available; fleet observation PASS, fleet count 3.
- Each fleet api_mission has four integer slots; all three arrays were [0,0,0,0]. Single snapshot cannot validate slot semantics or expedition transitions.
- Sortie read PASS; envelope kind/value; sortieStatus four booleans, sortieMapId integer and other sortie fields. Not evidence of expedition reward/dispatch semantics.
- Python exit 0, read-only observation PASS. Clipboard verification FAILED_CLIPBOARD_ERROR, independently of MCP result.
- Decision: no code changes based on zero-only state; dispatch remains disabled. Next evidence is one bounded passive before/after comparison around a separately authorized or manual real expedition. Tests NOT_RUN; real MVP loop NOT_VERIFIED.


## 2026-10-26 19:09 +08:00 — Real expedition passive transition observed

- User manually dispatched Fleet 2 on Expedition 1 (15-minute practice voyage), manually handled completion/reward; script performed read-only Poi `info.fleets` sampling, no game actions. Script's static TARGET_EXPEDITION_ID=2 is stale and must NOT be interpreted as the actual expedition.
- BEFORE_DISPATCH 18:37:12: api_mission=[0,0,0,0].
- AFTER_DISPATCH 18:37:36: api_mission=[1,1,1793011949741,0].
- BEFORE_REWARD 19:09:12: api_mission=[2,1,1793011949741,0].
- AFTER_REWARD 19:09:34: api_mission=[0,0,0,0].
- Observed transitions support slot 0: 0 idle, 1 underway, 2 completed/pending reward; slot 1: mission ID 1 for this expedition; slot 2: 13-digit timestamp-like value, units/meaning not independently validated; slot 3: always 0, unknown.
- Python four reads PASS; clipboard verification PASS. Actual reward receipt not independently checked; automated dispatch and automated reward collection NOT_TESTED. This is a successful manually operated real expedition state-transition observation, not autonomous MVP acceptance.
- Next: minimally adapt existing expedition normalizer/guard to distinguish idle, underway, and pending reward; validate time slot before scheduling; never dispatch when slot 0 nonzero or unknown. Keep game writes disabled pending separate explicit authorization. No local code tests run in this observation.


## 2026-10-26 — Focused offline regression tests added

- Added `tests/test_expedition_state_v2.py` with seven unittest cases covering observed idle `[0,0,0,0]`, underway `[1,1,timestamp,0]`, completed/pending reward `[2,1,timestamp,0]`, fail-closed unknown status, inconsistent idle slots, missing timestamp, and wrong slot count.
- Both underway and pending-reward normalized snapshots are asserted to remain `BLOCKED_UNKNOWN_RETURN_TIME` in the existing planner; this avoids false dispatch readiness while timestamp semantics are not authoritative.
- Test file was created on GitHub and fetched back for inspection. Tests have NOT been executed: no cloud runner/Actions used, and no local test run performed. Code/test presence is not a test PASS.
- Related normalizer commit: `aec2d4652f1e821cfebe8a99da54b2298fc0a1da`; test commit: `fea18431c12e92245a894de02031adf27f7facba`.
- Next: inspect repository conventions and run this focused test in the existing local venv only if the user chooses a batched local test; no game actions.


### 2026-10-26 — Focused test blocked by stale local worktree

- User-reported read-only preflight checked the existing worktree at `D:\\KancolleBot-Worktrees\\expedition-closed-loop-mvp-20261024`; the expected `tests\\test_expedition_state_v2.py` was missing locally.
- Result: `BLOCKED`; no tests were executed, no Git sync/pull was performed, and the script did not modify repository files or perform game actions.
- Clipboard verification reported `FAILED`; this does not change the preflight result and no clipboard PASS is claimed.
- Remote GitHub branch already contains the focused test file (commit `fea18431c12e92245a894de02031adf27f7facba`) and the normalizer change (commit `aec2d4652f1e821cfebe8a99da54b2298fc0a1da`). The local worktree must not be treated as synchronized with those remote commits.
- Decision: do not repeat the same local test command against this stale worktree. Next step is to make the remote branch content available to an execution environment without GitHub Actions, or, if no such runner is available, stop claiming tests are executed and continue only with source-level review. Live dispatch and reward collection remain unimplemented/unverified; no game actions.


## 2026-10-26 — Expedition action interface investigation

- Cloud-first static review of the current branch confirmed that `src/kancollebot/adapters/poi/mcp_client.py` only allows `poi_describe`, `poi_get`, and `poi_lookup`; `call_read_tool()` rejects every other tool name. `PoiExpeditionReader` has no dispatch or reward-collection method. No repository search result identified an existing expedition action adapter.
- The upstream Nanodesu MCP documentation describes its boundary as read-only with respect to the game: it exposes Poi/Redux state and selected records for querying, not game requests or UI operation. This is useful corroboration but is not treated as authoritative for the locally installed `0.2.0` plugin, which differs from upstream `0.1.0`.
- Confirmed blocker: the current KancolleBot + Nanodesu read path can observe the manually performed transition, but the existing interface cannot perform dispatch or reward collection. Do not add guessed calls or repurpose `poi_hensei_save`; that tool writes Poi plugin state and is not a verified game action.
- Decision: keep live game actions disabled. Do not add a fake adapter or claim autonomous closure. The remaining implementation route must identify a separate, evidence-backed game executor (for example, an existing authorized executor or verified game-request/UI mechanism) and independently verify both dispatch and reward collection before wiring it in. If no such mechanism is found, stop and report the blocker rather than building another reader or extending speculative architecture.
- Evidence links: [KancolleBot MCP client](https://github.com/wosafety0/KancolleBot/blob/dev/expedition-closed-loop-mvp/src/kancollebot/adapters/poi/mcp_client.py), [expedition reader](https://github.com/wosafety0/KancolleBot/blob/dev/expedition-closed-loop-mvp/src/kancollebot/adapters/poi/expedition_reader.py), [upstream Nanodesu documentation](https://glama.ai/mcp/servers/momocow/poi-plugin-nanodesu-mcp/tree).
- Test execution: NOT_RUN. Static review only. Game actions: NOT_PERFORMED. GitHub Actions: NOT_USED.


## 2026-10-26 — Improve the next experiment: observe the reward screen

- Process correction: the next experiment should explicitly capture the state while the game is sitting on the expedition reward screen, before the user clicks to collect. This is the missing intermediate observation between the already-recorded `[2,1,timestamp,0]` completion/pending-reward sample and the post-collection `[0,0,0,0]` sample; it should have been proposed proactively rather than waiting for the user to point it out.
- Minimal next capture, using only existing read-only Poi tools and manual game interaction: (1) once the reward screen is visible, do not click yet; read the selected fleet's full `api_mission` and a small relevant state snapshot, recording the actual fleet index/ID and observation time; (2) after the user manually collects the reward, read the same fields again. Do not hardcode the target mission from a stale script constant; derive/record it from the actual observation.
- Compare the reward-screen snapshot with the post-collection snapshot. Where available, include only the relevant resource/reward counters before and after so collection can be corroborated independently; do not print sensitive or unrelated raw game data. The purpose is to establish which observable fields distinguish completed/pending reward from reward collected, not to claim reward receipt from `api_mission` alone.
- No game interaction is authorized for the script. Manual click remains user-controlled; script remains read-only. Do not implement an automatic claim until the observation identifies a reliable state signal and a separately verified executor exists.
- Status: experiment plan updated; new capture NOT_YET_PERFORMED. No tests run, no game actions performed by automation, no GitHub Actions used.


## 2026-10-26 — Read-only per-screen Poi snapshot script

- Added `scripts/poi_ui_snapshot.ps1`, a Windows PowerShell 5.1-compatible interactive collector. Start it once and press Enter at each game screen to append one JSONL snapshot; enter Q to exit.
- It uses only the loopback Poi MCP endpoint and allowlists `poi_describe`, `poi_get`, and `poi_lookup`. Current snapshot captures observation time, all fleet IDs/names, each fleet's raw `api_mission`, ship-instance count, and per-fleet ship slots with instance/master ID, level, fuel, ammo, and condition when available. It does not hardcode a target fleet or expedition ID.
- Log path is alongside the script: `scripts/poi-ui-snapshots.jsonl` when run from the repository script location. This local output file is expected to be created by the user running the script; no local machine was accessed from this cloud operation.
- Safety: no game clicks, dispatches, reward claims, writes, software installs, proxy changes, or GitHub Actions. Script source was committed and fetched back from the target branch; this is source readback only, NOT a Windows PowerShell runtime test.
- Clipboard report handling copies the candidate full report and verifies it by `Get-Clipboard -Raw`; runtime outcome remains untested until the user runs the script.
- Commit: `f160fdd8c9b72da5cca8bf91158d5f5e2bf19342` (script added), then `24a069c05d82b3ce3e16f862b466ef09c0a8b6a5` (clipboard report verification refinement).
- Status: script committed; PowerShell runtime NOT_RUN; real Poi capture NOT_YET_PERFORMED; game actions NONE; GitHub Actions NOT_USED.
- Next: run the script with Poi MCP already available. Press Enter on idle, underway, completed/reward screen, and post-claim screens as applicable; then share `poi-ui-snapshots.jsonl` so the observed field transitions can be compared without guessing.


## 2026-10-26 20:54 +08:00 — Follow-up passive expedition transition captures

- User-provided PowerShell snapshot output from the existing read-only collector recorded 3 fleets and 91 ship instances per sample. The script did not click, dispatch, claim rewards, or modify game state.
- Idle samples at 19:37:57, 19:38:33, and 19:38:42 showed all three `api_mission` arrays as `[0,0,0,0]`.
- At 19:39:00, Fleet ID 2 showed `[1,1,1793015637180,0]`; at 19:39:10, Fleet ID 3 also showed `[1,2,1793016547786,0]`.
- At 20:49:17, both were in status 2: Fleet ID 2 `[2,1,1793015637180,0]`, Fleet ID 3 `[2,2,1793016547786,0]`. At 20:49:28, Fleet ID 2 was idle while Fleet ID 3 remained status 2. At 20:49:39, both were idle. Additional samples through 20:49:48 remained idle.
- User clarified that the game was on the expedition reward screen and pressing Enter to return was followed by `[0,0,0,0]`. This aligns with status 2 transitioning to idle during the user-operated reward-screen flow, but the collector did not capture resource/reward counters; reward receipt is not independently verified.
- Fleet names appeared as mojibake in console output. Numeric `api_mission` fields remained readable; no encoding fix was attempted because it does not block this MVP decision.
- Conclusion: live read-only observation now supports the transitions idle (status 0) → underway (status 1) → completed/pending handling (status 2) → idle after the manually operated reward-screen flow. The timestamp-like third slot remains uninterpreted. This is state-observation evidence only, not proof of autonomous dispatch or reward collection.
- Implementation blocker reconfirmed from current branch source: `PoiMcpClient.call_read_tool()` allows only `poi_describe`, `poi_get`, and `poi_lookup`; `PoiExpeditionReader` is read-only and has no dispatch/claim methods. Do not guess a game API or repurpose `poi_hensei_save`, which writes Poi/Hensei plugin state rather than performing a verified expedition action.
- Decision: do not add another reader or speculative executor. Keep live actions disabled. The next development step is to identify an existing, evidence-backed game executor or a verified game-request/UI mechanism; if none can be established, report the blocker rather than claim autonomous closure. No code tests were run in this turn; GitHub Actions were not used.


## 2026-10-26 — Executor path triage after state-transition evidence

- Re-read current branch coordinator and Poi adapter source. The coordinator has an injected `dispatch_expedition` protocol method, but no concrete Poi executor is implemented; the current MCP client explicitly allowlists only `poi_describe`, `poi_get`, and `poi_lookup`. This protocol is an interface seam, not evidence that a working executor exists.
- Reviewed public Poi architecture documentation/source guidance: Poi is Electron/React/Redux; its documented Redux actions include actions synthesized from game API responses. This establishes an internal state/action architecture, not a supported plugin API for issuing expedition game requests. No request-capable, stable plugin contract was verified in this pass.
- Public search did not identify a verifiable upstream source for the separately installed `poi-plugin-mcp` package with its reported optional input capability. The local installed source remains the decisive evidence for whether it can issue an actual game action safely; cloud review cannot inspect the user's local filesystem.
- Decision: do not invent a Redux action, invoke undocumented game APIs, or enable the separate plugin's input feature. No executor path is verified yet. The shortest next step is a narrowly scoped, read-only source dump of the installed `poi-plugin-mcp` package's manifest plus only the files implementing its HTTP routes/input handler and the default/guard settings; exclude tokens, credentials, and unrelated config values. If that package's input path only injects mouse/keyboard events, assess whether it can execute the fixed expedition flow without manual template authoring; otherwise stop and report the blocker.
- Status: no code changes to runtime logic; no tests run; no game actions; no local files accessed; no software installed; no proxy/system settings changed; GitHub Actions not used.
- Public reference: Poi repository architecture guidance https://github.com/poooi/poi/blob/master/AGENTS.md (architecture context only; not a supported game-action API contract).


## 2026-10-26 21:11 +08:00 — Local poi-plugin-mcp input bridge source confirmed

- User-provided read-only source report located installed package `poi-plugin-mcp` version `0.2.29` under the current user's Poi plugin directory. This is a user-supplied local report; the cloud workflow did not access the local filesystem.
- Static source evidence confirms the bridge has a POST `/input` handler. It requires a valid static bearer token, `inputEnabled === true`, writer-token authorization (or legacy observation mode if enforcement is disabled), an active input lease claim, and a `performInput` provider. Input operations are serialized through a pending-operation queue.
- `/debug/evaluate` is a separate arbitrary-JavaScript WebView evaluation surface, gated by `debugEvalEnabled === true` and writer-token authorization. Do not use or enable it as a shortcut for game control.
- The package settings defaults are `inputEnabled: false` and `debugEvalEnabled: false`; this report does not establish the currently loaded runtime settings, token availability, writer-token registration, or whether the input provider can address expedition controls without manually authored coordinates/templates.
- The provided bridge source was truncated at 18,000 characters and did not include the route registration table or the implementation of `lib/poi-input.js`, `lib/poi-input-lease.js`, and related input validation. Therefore exact accepted operations and their coordinate/accessibility semantics remain unverified.
- Decision: a potentially reusable UI input path exists, so continue with one bounded static inspection of those input implementation files and the bridge's route registration only. Do not toggle settings, retrieve/print tokens, make HTTP requests, or send input. If the implementation requires hand-built screen templates or button coordinates, stop rather than create a manual labeling workflow.
- User's report states clipboard verification FAILED due a Windows clipboard operation error; do not claim clipboard PASS. This is independent of the source-inspection finding.
- Status: static source evidence only; no tests run; no game actions; no runtime settings changed; no GitHub Actions used.


## 2026-10-26 21:27 +08:00 — Static input executor inspection

- Read-only local report inspected `poi-plugin-mcp` files `lib/poi-input.js`, `lib/poi-input-lease.js`, `lib/writer-tokens.js`, and `index.js`.
- `poi-input.js` confirms coordinate-based operations only in the inspected executor: move, click, drag, scroll, key, and text. Mouse coordinates are validated against a canonical 1200×720 canvas and scaled to current WebView dimensions. This is not evidence of semantic button targeting.
- Input lease code enforces a short TTL (default 10 seconds, maximum 30 seconds), owner/run identity, action scope where applicable, and monotonically increasing input sequence numbers.
- Writer-token registry supports expiring delegated tokens. The prior static inspection identified input as disabled by default; current runtime settings, token registration, and actual endpoint availability remain unverified.
- `index.js` wires telemetry into the bridge controller, but this report did not include the full HTTP bridge route registration or the telemetry internals needed to determine whether dynamic semantic targeting is available elsewhere.
- Assessment: the existing input transport is real in source, but it currently requires coordinates. Do not create manual PNG/button-coordinate inventories yet. Next, inspect only existing telemetry/action-event and bridge route code for dynamic target identification. If no non-manual target locator exists and operation depends on manually labeling many coordinates, stop rather than expanding the approach.
- No game actions, HTTP requests, settings changes, tests, or GitHub Actions were performed in this inspection. Clipboard verification in the user's report failed; it is not a PASS.


## 2026-10-26 21:38 +08:00 — Telemetry and HTTP route feasibility assessment

- Read-only local source report inspected `poi-telemetry.js`, `bridge-controller.js`, and selected contexts from `poi-http-bridge.js`.
- Confirmed HTTP observation endpoints include `/quest-list`, `/mission-board`, `/quest-action`, `/equipment-action`, `/equipment-selection`, `/unsetslot`, `/fleet-action`, `/action-events`, and `/action-events/wait`. These expose captured game/API telemetry and action-event history; the inspected excerpts do not establish a semantic UI-target-to-click locator.
- Confirmed `/input` routes to the coordinate-based input executor and lease endpoints govern input ownership/authorization. `/debug/evaluate` is an arbitrary JavaScript WebView execution path, disabled by default and explicitly not to be used as a shortcut.
- Feasibility: reuse of the existing input transport is plausible, but automatic dispatch/claim is not yet demonstrated. Dynamic semantic target identification is the missing evidence. Do not start manually producing large PNG/button-coordinate inventories. If the existing telemetry/action-event source cannot supply a non-manual locator, stop rather than expand into template labeling.
- Current report was read-only: no HTTP requests, game actions, settings changes, tests, or GitHub Actions. User-reported clipboard verification was FAILED.


## 2026-10-26 — POI action-event/API-response static inspection: stop decision

- Scope: read-only inspection of installed `poi-plugin-mcp` files `lib/poi-action-events.js` (12,477 bytes, 414 lines) and `lib/poi-api-responses.js` (4,836 bytes, 165 lines).
- Evidence from the supplied local report: action events expose fields including `path` and `generation`; API responses capture `/kcsapi/` paths and support filtering by path/generation.
- Assessment: this is evidence of API/action telemetry and response-history correlation, not evidence of a semantic UI target locator, scene hit-testing, or a mechanism mapping a stable action ID to a live button position.
- Decision: stop further telemetry-route exploration unless new concrete source evidence appears. Do not create manual PNG templates or a button-coordinate inventory. Do not call input endpoints, send HTTP requests, change settings, or perform game actions.
- MVP impact: the coordinate input channel may exist in the inspected package, but a reliable non-manual target-location mechanism and the real expedition dispatch/claim loop remain unproven. No claim of automation success.
- Local report caveat: clipboard verification was left as `PENDING`; it is not a PASS.
- GitHub Actions: not used.


## 2026-10-26 — Correction: project-specific constraints were not verified

- Correction to the preceding POI telemetry stop-decision entry: the statement "Do not create manual PNG templates or a button-coordinate inventory" was an assistant-added constraint, not a verified KancolleBot project rule and not an explicit user decision for this repository. It must not be treated as an authorization boundary or as a binding project requirement.
- The earlier static inspection evidence remains limited to the supplied report: `poi-action-events.js` records action/API telemetry fields such as `path` and `generation`; `poi-api-responses.js` captures `/kcsapi/` responses and supports path/generation filtering. This does not itself establish whether a template-based or coordinate-based approach is appropriate.
- Correct decision status: no semantic UI locator was demonstrated by that inspection; feasibility of other UI targeting approaches remains UNASSESSED. Choose the next step using KancolleBot's own rules, evidence, cost, reliability, and safety constraints—not constraints imported from another project.
- No game actions, HTTP requests, input endpoint calls, or settings changes were performed for this correction. GitHub Actions were not used.
- Audit note: this correction supersedes the prior entry only where it asserted a prohibition on manual templates/coordinate inventory. It does not erase the original inspection evidence or claim the expedition closed loop is complete.


## 2026-10-26 — MVP checkpoint: concrete POI dispatch adapter remains missing

- Reviewed the current branch's `src/kancollebot/expeditions/runner.py`, `src/kancollebot/adapters/poi/expedition_reader.py`, `src/kancollebot/adapters/poi/mcp_client.py`, `src/kancollebot/expeditions/planner.py`, `src/kancollebot/adapters/poi/expedition_state_v2.py`, and `src/kancollebot/safety/policy.py`.
- Confirmed: `ExpeditionRunner` already has a dispatch protocol seam and fail-closed preflight/journal/post-observation checks; `PoiExpeditionReader` is read-only; `PoiMcpClient` only allows `poi_describe`, `poi_get`, and `poi_lookup`; mission timestamp slots are deliberately not interpreted as return times; `SafetyPolicy.allow_live_actions` defaults to false.
- Concrete MVP blocker: no verified concrete adapter currently connects `dispatch_expedition(fleet_id, mission_id)` to a supported POI game-action interface. The separate `poi-plugin-mcp` input source inspected in local reports uses coordinates, and the supplied evidence does not establish its live runtime enablement or the exact safe dispatch sequence.
- MVP scope remains one fixed fleet + one fixed expedition, then observe dispatch, wait for completion, claim reward, and verify the fleet is dispatchable again. This checkpoint does not prohibit coordinate/template approaches; those options remain open for evidence-based evaluation.
- Next action: verify the exact `poi-plugin-mcp` `/input` request schema and runtime input-enable status using read-only source/config inspection only; do not send an input request. Then implement only the smallest adapter supported by evidence, retaining live-action gates. No game actions, HTTP requests, local setting changes, or GitHub Actions were used for this checkpoint.
- Status: code/source review only; real dispatch/claim loop remains NOT IMPLEMENTED and NOT VERIFIED.


## 2026-10-26 — Cross-project rule ownership audit (UmaAI-Research / KancolleBot)

- Mode: repository-backed documentation audit; no runtime or game interaction.
- Reconfirmed this repository's own rule/progress sources on `dev/expedition-closed-loop-mvp`: `docs/PROJECT_kancollebot_MASTER.md` defines project rules and `docs/PROJECT_kancollebot_Progress.md` records progress, decisions, and corrections.
- Rule ownership: the UmaAI-Research MVP-01 constraint against manual per-screen screenshots, bulk PNG template creation, and hand-labelled button coordinates is specific to UmaAI-Research. It must not be imported as a KancolleBot requirement.
- The prior KancolleBot correction remains authoritative: the statement “Do not create manual PNG templates or a button-coordinate inventory” was assistant-added, not a verified KancolleBot rule or an explicit user decision for this repository. No semantic UI locator was demonstrated by the cited inspection; other targeting approaches remain unassessed, not prohibited.
- Decision rule: evaluate any KancolleBot targeting approach using KancolleBot's own documented safety boundaries and evidence, cost, reliability, and implementation scope. Do not treat another project's constraints as binding unless the user explicitly adopts them for this project.
- Evidence/limits: this conclusion comes from the current branch's MASTER and Progress documents and the corresponding UmaAI-Research branch's project progress and focused capture progress. This audit does not establish that any specific targeting approach is safe or feasible, and does not mean the expedition closed loop is complete.
- No game actions, HTTP/input endpoint calls, local setting changes, or GitHub Actions were performed.


## 2026-10-26 — Cross-project documentation entry order confirmed

- User-confirmed operating rule: before KancolleBot work, read `docs/PROJECT_kancollebot_MASTER.md` and `docs/PROJECT_kancollebot_Progress.md` on branch `dev/expedition-closed-loop-mvp`.
- Before UmaAI work, read `docs/PROJECT_UMAAI_Progress.md` on branch `analysis/qianye-reverse-engineering-20261025`, then the relevant topic-level `PROGRESS.md`.
- If the actual branch differs, verify the branch and file tree before choosing paths; do not guess.
- Project rules are isolated: do not move a rule from one project into another unless the user explicitly adopts it for the receiving project. Resolve uncertainty by checking that project's authoritative files and the rule's provenance.
- No code or runtime configuration changes, game actions, or GitHub Actions were performed for this documentation update.


## 2026-10-26 — MVP delivery reset: stop analysis loops and prioritize the first real expedition cycle

- User feedback: the project has spent too long without delivering the MVP. This criticism is valid: the repository still has only a dispatch protocol seam and read-only POI adapter; no concrete action adapter exists, and no real dispatch/claim loop has been demonstrated.
- Scope is now explicitly limited to one fixed fleet and one fixed expedition: dispatch, verify underway, wait for completion, claim/handle the result, verify idle and dispatchable again.
- Immediate engineering priority: implement the smallest concrete POI action adapter using the installed `poi-plugin-mcp` input route, after checking the exact local request contract and required lease/auth fields. Do not continue broad telemetry exploration or unrelated architecture work.
- Do not invent request schemas, coordinates, tokens, or runtime state. If local source/config evidence is required, request one bounded read-only report from the user's Windows installation; no game-action request during preflight.
- After adapter implementation, run focused offline tests for request construction, safety gates, ambiguous outcomes, and stop behavior. Then request explicit user authorization before one real game cycle. Never claim MVP acceptance until dispatch, result handling, and redispatch readiness are evidenced.
- This is a priority/status correction only. No local code, runtime settings, game state, or gameplay actions were changed. GitHub Actions were not used.



## 2026-10-26 — CDP UI-click MVP route restored; supplied loop script static review

- User supplied a proposed Windows PowerShell generator for a Python implementation under `D:\KancolleBot`, including CDP screenshot/click helpers, state reading through `window.getStore('info.fleets')`, action classes, a scheduler, and `tests/start_main_loop.py`.
- Route decision: continue with **CDP UI clicks plus state observation** for this MVP. Do not make investigation of a POI plugin input API a prerequisite. State reads via `Runtime.evaluate/window.getStore` are page-state reads; the action route itself is CDP mouse input, not direct game API packet dispatch.
- Static review only; the supplied generator was not executed, files were not created/overwritten, no local machine or game session was accessed, and no game actions were performed.
- Blocking defects identified in the supplied code:
  1. `get_ws()` selects a target with `type == "webview"` and URL containing `kancolle`, while `fetch_state()` independently selects `type == "page"` and title exactly `poi`. These selectors may resolve different targets or no target; the script does not validate that state and clicks refer to the same live game page.
  2. `ActCollect.execute()` clicks the notification, then immediately returns if the home-menu template matches. This can report collection complete while still on the home screen, without proving the reward was collected.
  3. The scheduler hardcodes `is_authorized=True` for both collect and dispatch, bypassing the guard's stated manual-approval requirement.
  4. The scheduler scans every fleet ID in `[2, 3, 4]` and dispatches any idle one, rather than enforcing one fixed fleet and one fixed mission. `mission_id` is not used to select or verify the expedition; the mission selection click is a hardcoded coordinate.
  5. The loop can enqueue real, irreversible clicks every second based on a single state read, but does not verify each intermediate screen/selection before proceeding or verify a server-confirmed dispatch state afterward.
  6. After the start click, the code may click the back coordinate repeatedly without identifying the current screen. The home-template score alone is not sufficient proof that dispatch succeeded.
  7. The state fetcher suppresses every exception and returns `None`, losing the reason for failure; the scheduler has no bounded timeout or visible fatal-stop path for persistent target/state failures.
  8. The generated script does not preflight dependencies (`cv2`, `numpy`, `websocket-client`), target identity, template existence, viewport scaling, or coordinate calibration before entering the action loop.
- Required next step: do not run this generated loop unchanged. First perform a read-only preflight on the actual local worktree and target CDP page; verify the exact paths and the installed dependencies/template. Then adapt the smallest path to a single fixed fleet + single fixed expedition, with explicit authorization before irreversible clicks, per-screen observation, a hard stop on uncertainty, and evidence of dispatch/result/redispatch. Do not expand into POI input-API research.
- User-provided coordinates (`(301,374)`, `(1079,343)`, `(1037,668)`, `(987,671)`, notification `(969,52)`) are recorded as unverified user-supplied inputs, not independently calibrated evidence.
- Status: static review only; no script execution, local file changes, game actions, or GitHub Actions. MVP real expedition cycle remains NOT VERIFIED.


## 2026-10-26 — Automation boundary clarified: client-mediated actions allowed; direct game API requests prohibited

- User decision: automation methods are acceptable provided they do **not bypass the game client by directly constructing and sending game-server API requests**, e.g. direct calls to `api_req_mission/start`.
- Allowed routes include Poi/client-provided internal interfaces and plugins, CDP/UI automation, and simulated mouse/keyboard input, subject to evidence that the action is mediated by the running client rather than a direct game-server request.
- This supersedes the overly narrow wording in the earlier CDP route note: CDP mouse clicks are a fallback, not the only permitted route. Do not assume that every plugin or internal method is safe; trace the actual call path and distinguish client-mediated input from direct protocol requests.
- Resume targeted source tracing already discussed, rather than restarting broad research: non提助手 `AutoActionQueue -> ActExpedition -> ActFinishExpedition`, then compare the concrete expedition action path in `kcauto_custom`. Record source evidence for how each action is executed; feature descriptions alone are not proof.
- Research must not block the MVP. Prefer a verified, reusable client-mediated action route if one is found promptly; otherwise return to the existing CDP/UI path and continue the single fixed-fleet + single fixed-expedition real loop.
- MVP acceptance remains: dispatch, verify underway, wait for completion, collect/handle result, and verify the fleet is dispatchable again. No game action was performed by this documentation update; no local files were changed and GitHub Actions were not used.

## 2026-10-26 — MVP deadline and low-overhead stability plan summarized

- User set the immediate delivery target to **2026-10-27 around 22:00 China Standard Time (UTC+08:00)**, rather than the previous 48-hour window. This is a target, not a claim that completion is guaranteed.
- MVP remains strictly one fixed fleet + one fixed expedition. Acceptance requires evidence for: read current state → dispatch through the running game client → verify underway → wait for completion → claim/handle result → verify the fleet is idle and dispatchable again.
- Action boundary: do not bypass the game client by constructing/sending game-server API requests (for example, direct `api_req_mission/start`). Poi/client-mediated interfaces, CDP/UI, or simulated input remain candidates only when their actual execution path is verified.
- No dependency on manually captured/annotated screenshots is desired for the chosen route. Prefer structured Poi/CDP state and runtime page inspection; use automatic visual inspection only if structured state cannot verify a required transition. This is a project direction, not evidence that semantic UI targeting is already available.
- Stability/performance proposals accepted for design review:
  1. State-driven scheduling and bounded sleep instead of high-frequency polling; cap sleep duration and handle missing/invalid completion timestamps safely.
  2. Separate structured state reads from screenshots; capture images only when necessary.
  3. Reuse CDP WebSocket connections where practical, correlate responses by request ID, and handle timeout/disconnect/target changes before reconnecting.
  4. Use bounded rotating logs (proposed 5 MiB per file, 3 backups) and throttle repetitive messages.
  5. Fail closed on unknown state, disconnects, cat/error screens, or ambiguous action outcomes. A failed/ambiguous dispatch must trigger state reconciliation, not an automatic repeated click.
  6. Process supervision and 24/7 endurance work are deferred until the first real expedition cycle is proven.
- Important corrections to the supplied sample design: do not scan fleets `[2, 3, 4]`; keep the fixed fleet/mission. Do not rely on `sleep(30)` as the duplicate-action guard. Do not assume mission state value `2` alone proves the result is claimable. Avoid `min([])` when no valid completion timestamp exists. A persistent WebSocket must match each response to its request ID.
- Execution order: (1) read-only inspect the actual branch/files and current implementation; (2) select the shortest verified client-mediated action path; (3) implement only the minimum dispatch/verification path; (4) execute one authorized real cycle and collect evidence; (5) record outcome; (6) add low-overhead long-running optimizations after the cycle is demonstrated.
- Current status: **planning/decision recorded; no code implementation or performance benchmark is claimed.** The concrete dispatch/claim adapter and real expedition loop remain unverified. No game action was performed for this documentation entry; no local files were changed; GitHub Actions were not used.


## 2026-10-26 — Anti-cat recovery recorded; MVP scope unchanged

- User decision: record anti-cat / network-recovery capability as a core KancolleBot requirement, without changing or delaying the current MVP scope.
- Scope boundary: the active MVP remains one fixed fleet + one fixed expedition, completing the verified cycle: read state → dispatch → verify underway → wait for completion → claim/handle result → verify the fleet is idle and dispatchable again.
- Deferred capability: after network/connectivity failure, pause game actions and keep attempting safe client-mediated reconnection/recovery; once reconnected, re-read and reconcile the actual game state before resuming. Never blindly repeat a potentially completed click/action.
- Safety behavior: if the client/session is unavailable, the server rejects the operation, or the current game state remains ambiguous, fail closed and wait/report rather than continuing clicks. “Keep retrying recovery” does not mean “keep retrying gameplay actions.”
- Priority decision: anti-cat is recorded as a core future reliability requirement, but its implementation/research must not expand or displace the current expedition MVP. No implementation or runtime behavior is claimed by this record.
- This update changes only this GitHub progress document. No local files, game state, Poi settings, or GitHub Actions were touched.


## 2026-10-27 — Kca POI interaction and expedition confirmation trace (read-only)

### Source evidence
- Reference repository: `XVs32/kcauto_custom`, branch `develop`, inspected source revision `d1160f166300e38050eefc778d738ee25f8d3317`.
- `kcauto/util/poi_client.py`: `PoiClient` uses a loopback TCP socket, default port `38591`; packets have a 4-byte little-endian length prefix and a packet-type byte. JSON commands include `hello`, `ping`, `capture`, `click`, `hover`, `scroll`, `drag`, `refresh`, and `quest_stats`. Frame packets carry request ID, width, height, channel count, and BGRA bytes.
- `kcauto/util/interaction_drivers.py`: `PoiDriver.connect()` checks required POI plugins, starts the API webhook listener, and connects to `PoiClient`; click/capture operations go through that local interaction service. Kca also has separate Direct Control and Chrome CDP drivers.
- `kcauto/util/poi_plugins.py`: Kca expects `poi-plugin-forwarder`, `poi-plugin-noro6-exporter`, and `poi-plugin-interaction`; its forwarder minimum is 1.1.0. The checked-in install hint references a special forwarder branch. Do not install or change plugins without separate authorization.
- `kcauto/api/api_listener.py`: receives webhook POSTs at `/webhook/kancolle`, filters forwarded KCSAPI/map-resource events, and queues them for consumers. This is an observer/response telemetry path, not evidence that Kca directly sends game-server API requests.
- `kcauto/expedition/expedition_core.py`: after the client UI dispatch click, Kca waits for the observed `EXPEDITION_START` event before updating its local underway/return-time model. The observed response is a confirmation signal; the click itself is not treated as sufficient proof.
- `kcauto/util/kca.py`: result collection clicks through the client UI, waits for port-state API observation and the next/result UI, and loops while result screens remain. Collection is a sequence with observation, not a single notification click.
- `tests/test_poi_client.py` supplies a fake local server for protocol round-trip tests. These are protocol tests, not proof of compatibility with the user's installed POI runtime.

### KancolleBot implications
- The existing Nanodesu MCP endpoint `127.0.0.1:12450/mcp` is a distinct read-only interface; it is not the Kca interaction protocol and cannot be assumed to expose click/capture operations.
- Reusing Kca's POI interaction route would require its matching interaction/forwarder plugin protocol and a verified installed service. Do not assume the already-installed Nanodesu MCP plugin satisfies this dependency.
- Reusable design principles: keep UI input and API-response observation separate; wait for a matching observed dispatch event; verify each result-screen transition; reconcile live state after timeout/disconnect before any retry.
- Do not copy the third-party implementation wholesale. Evaluate licensing and the current KancolleBot architecture before any code reuse. The current MVP remains one fixed fleet + one fixed expedition.
- Status: static source review only. No local HTTP/TCP requests, plugin installs, config changes, game actions, or GitHub Actions were performed. Real POI protocol compatibility and the dispatch/claim closed loop remain NOT VERIFIED.

## 2026-10-27 — Kca Windows executable startup failure diagnosis (user log; read-only)

- User supplied a kcauto_custom.exe startup log dated 2026-10-21. Configuration loads and the expedition module / Fleet 1 activate, but startup exits at util\\kca.py::hook_chrome with: No running Kantai Collection tab found in Chrome.
- Direct failure cause evidenced by the log: Kca cannot find a supported Kantai Collection tab in Chrome. The trace does not show a dispatch attempt; this is a startup/browser-hook failure, not an expedition-action failure.
- Two files are also missing: data\\temp\\reinforce_general_category.json and data\\temp\\equipment_list.json. Kca logs instruct the user to start from the splash screen to populate equipment data. These indicate incomplete first-run data initialization, but they are not the exception that terminates this run; execution continues until Chrome hooking.
- The later OpenCV imwrite errors state that screenshot images are empty. They occur during crash screenshot saving after the main exception and are secondary diagnostics, not evidence of the original failure.
- Compatibility implication: Kca's executable is not currently a proven runnable foundation in the user's environment. If Kca were tested again, first-run data initialization and the expected Chrome game-tab / debugging setup would need separate read-only verification. Do not install plugins or change browser/system settings as part of diagnosis without explicit authorization.
- Project decision remains unchanged: use Kca as a source/behavior reference unless a bounded compatibility check proves that making it run materially shortens the KancolleBot MVP. Continue prioritizing KancolleBot's own single-fleet, single-expedition client-mediated loop; direct game-server API requests remain prohibited.
- Evidence boundary: this entry records interpretation of the user-provided log only. No local machine inspection, executable launch, configuration change, game action, plugin installation, or GitHub Actions run was performed. KancolleBot's real expedition/claim loop remains NOT VERIFIED.


## 2026-10-27 — Kca Chrome hook vs Poi mode source confirmation (read-only)

- Inspected upstream `XVs32/kcauto_custom` at revision `d1160f166300e38050eefc778d738ee25f8d3317`, files `kcauto/util/kca.py`, `kcauto/util/interaction_backend.py`, `kcauto/util/interaction_drivers.py`, `kcauto/kca_enums/interaction_modes.py`, and `kcauto/config/config_core.py`.
- `Kca.hook_chrome()` delegates to the configured interaction backend; it does not itself always mean Chrome. The observed log text `Hooking into Chrome.` comes specifically from `ChromeHookDriver.connect()`, confirming this run selected a Chrome-based mode rather than the dedicated Poi driver.
- Chrome mode reads `general.chrome_dev_port`, enumerates CDP tabs, looks for a URL containing the Kancolle API URL and a Poi tab URL ending in the configured postfix, then raises the exact logged exception when no game API tab is found.
- A dedicated `poi` mode exists. Its driver checks for Kca-specific POI plugins, starts the webhook listener, and connects to Kca's POI interaction service on its configured control port. This is not the same interface as the Nanodesu MCP endpoint; installed plugin/runtime compatibility remains unverified.
- The missing equipment JSON files are checked during initialization, but the fatal exception shown in the supplied log occurs later during Chrome target discovery. The empty OpenCV screenshots are post-crash secondary errors.
- Decision: do not reinstall or patch Kca yet. A bounded next check, if user chooses to continue, is read-only inspection of the active Kca config's interaction mode and configured ports, plus confirmation whether Kca-specific POI plugins/services exist. Do not change config or install plugins during preflight. If that route is not already supported by the user's environment, return to KancolleBot's own Poi/CDP MVP path.
- Evidence boundary: public upstream source review plus user-supplied log only. No local config/runtime inspection, plugin install, gameplay action, or GitHub Actions run. KancolleBot real expedition/claim loop remains NOT VERIFIED.


## 2026-10-27 — CDP transport implementation: guarded input primitives

### Implemented on the MVP branch
- Added `src/kancollebot/adapters/poi/cdp_websocket.py`: a standard-library WebSocket transport restricted to loopback debugger URLs and an explicit command allowlist (`Page.getLayoutMetrics`, `Page.captureScreenshot`, `Input.dispatchMouseEvent`). It rejects `Runtime.evaluate` and other commands, correlates CDP responses by request ID, limits frame size, and does not retry ambiguous input.
- Tightened `src/kancollebot/adapters/poi/cdp_input.py`: every click first reads current viewport metrics and rejects out-of-bounds recorded coordinates before sending mouse input. Invalid coordinate types and non-finite values are rejected.
- Updated `tests/test_cdp_input.py` and added `tests/test_cdp_websocket.py` for viewport bounds, input event ordering, no-retry ambiguity, loopback-only transport, and rejection of arbitrary JavaScript evaluation.
- Remote commits:
  - `c527d5cfacb2c17ff66f1f1c060a766a21330daf` — CDP WebSocket transport.
  - `11d082b5228d3a03f4660882480f590f7d736c2f` — live viewport guard.
  - `d833bafcfd16c2298ad9bb1fd959688758c11583` — viewport/input regression tests.
  - `93d26e7b254bdc5d4bfffc23ef1080fbbdea0d0d` — transport safety-boundary tests.

### Verification and remaining blockers
- GitHub read-back confirmed the new transport, updated input primitive, and tests exist on `dev/expedition-closed-loop-mvp`.
- **Tests have not been executed in this session.** The added tests are test source only; no CI workflow was triggered and no local worktree is available here to run the suite.
- No CDP endpoint was contacted, no WebSocket was opened, no screenshot was captured, and no mouse input or game action was sent.
- The transport is a low-level primitive, not yet a concrete expedition adapter. The MVP still needs a verified target selection + recorded/validated action sequence + fresh state verification for dispatch, result handling, and return to idle. User-supplied coordinates remain unverified and must not be treated as calibrated.
- Real expedition/claim closed loop remains **NOT VERIFIED**. The default live-action safety policy remains disabled.


## 2026-10-27 — CDP target-to-input session wiring

- Added `connect_poi_session()` to `cdp_websocket.py`: it composes the existing loopback-only target discovery/unique-title selection with the allowlisted WebSocket transport, and exposes a `CdpInput` facade through the session object.
- Added offline test cases for successful wiring and refusing to open a WebSocket when target selection is ambiguous.
- Remote commits:
  - `9dd41b5a189d8d9352eeba2a78f36a479b5864fb` — connect selected Poi target.
  - `2d2e44b300c4dc05939f6505953d1c56c90d8d1d` — session setup tests.
  - `ce94d1afc94fc4da351e0c8eb3c3ec2cba483360` — expose the guarded input facade.
- GitHub read-back is being used to verify the saved source. No CI was triggered and no tests have been executed in a local worktree.
- This makes target discovery and low-level input transport composable, but it still does not provide a game-specific expedition click sequence or prove screen transitions. No local CDP endpoint was contacted and no game action was performed. Real dispatch/claim/redispatch remains **NOT VERIFIED**.


## 2026-10-27 — Read-only CDP session preflight command

- Added `tools/verify_cdp_session.py` to compose the selected Poi target, loopback WebSocket transport, and a read-only `Page.getLayoutMetrics` request.
- The report explicitly states whether mouse input, JavaScript evaluation, or gameplay actions were sent; a successful preflight does not mark the MVP accepted.
- Commit: `b008123d08bc4a1b00899fc0ef3f5e3f4295302c`.
- GitHub source read-back is the available verification in this session. The script has not been run against the user's PC; no local endpoint was contacted, no screenshot/input was sent, and no GitHub Actions workflow was triggered.
- Next engineering gate: when a local Poi/CDP runtime is available, run this read-only preflight first; then validate the action targets and transitions before enabling any single dispatch. The real expedition/claim loop remains **NOT VERIFIED**.


## 2026-10-27 — Private path mapping and public-release guard

- Updated `docs/PROJECT_kancollebot_MASTER.md` with a fail-closed path-resolution order: inspect the live repository/branch/tree first, consult the private alias map for unresolved references, verify every mapped path, and stop on missing or ambiguous mappings.
- Added a mandatory privacy-audit gate before changing repository visibility. No visibility change was made; repository metadata still reports `private`.
- The mapping document was generated as a separate local artifact, but its upload to the ChatGPT Library was rejected by the Library's current upload quota limit. It is **not yet installed** at the path referenced by the rule. Until it is uploaded, the rule requires stopping rather than guessing if a path cannot be resolved.
- The project master rules were read back from GitHub and the new policy was verified. The privacy audit is **not complete**: the existing project rules and repository history still contain identifying project terms, so the repository must not be made public yet.


## 2026-10-27 — Public-safe progress policy and current repository status

- Live GitHub metadata now reports this repository as public. Older progress entries that describe it as private are historical records and must not be interpreted as current metadata.
- From this entry forward, public progress records will use only approved opaque aliases. Do not record real project identities, executable filenames, invocation relationships, or alias-to-identity mappings in progress text, commit messages, issue/PR content, workflow logs, or generated reports.
- The authoritative alias/path mapping remains in a separately authorized private index. This public repository records only whether the index was consulted and whether the live path was verified; it must not name the private index repository or reproduce its mappings.
- This policy update does not remove identifiers from earlier commits, old branches, historical PR diffs, or prior progress entries. Historical exposure cleanup is a separate task and is not claimed complete.
- MVP engineering status is unchanged by this documentation update: no verified live dispatch/claim/redispatch cycle is claimed. Continue cloud-first, use GitHub Actions for repository tests where available, and do not require local execution unless a test depends on the Windows/client runtime.

 
## 2026-10-27 — Semantic UI discovery groundwork (cloud-only)
- Added a read-only accessibility-tree locator that requires one exact accessible name/role match and validates DOM geometry. Ambiguous, ignored, malformed, off-viewport, and scrolled-coordinate cases fail closed. The locator sends no input.
- Expanded the approved CDP command set only for read-only accessibility and DOM geometry inspection. Mouse input remains the only permitted input command; arbitrary JavaScript evaluation remains blocked.
- Added a bounded read-only diagnostic to report only unique expedition-related accessible labels. It does not print the page URL, does not capture screenshots, and sends no input or game action.
- GitHub Actions run [40464083320](https://github.com/wosafety0/wosafety0clamp/actions/runs/40464083320) completed successfully for commit `a3b062752798fa0c56787c1e60528038af6d19d5`: source compilation passed and all 122 unit tests passed.
- An earlier CI run exposed a mismatch in a test fixture for the fail-closed scroll guard, and another exposed an incorrect filter-test expectation. Both were corrected; the latest full run is green.
- Cloud-side code and documentation are committed. No local runtime was contacted, no local files were changed, and no gameplay input was sent.
- **MVP status remains NOT VERIFIED:** actual UI accessibility labels, live state transitions, dispatch, completion/result handling, reward collection, and redispatch readiness have not been observed end-to-end. The next required evidence is a bounded read-only scan against the user's running client; GitHub Actions cannot substitute for that local runtime observation.

## 2026-10-27 — Read-only mission timestamp probe

- Runtime evidence supplied from the client's read-only state snapshot; observation time: `2026-10-27T09:23:32.863Z`. No gameplay input or configuration change was made by this probe.
- Three fleet records were observed. Record 1 had mission tuple `[0, 0, 0, 0]`. Records 2 and 3 had tuples `[2, 1, 1793090153208, 0]` and `[2, 2, 1793091065462, 0]`.
- The third values convert as Unix millisecond timestamps to `2026-10-27T08:35:53.208Z` and `2026-10-27T08:51:05.462Z`; both are earlier than the observation time by approximately 47m 39.655s and 32m 27.401s, respectively.
- Confirmed: the values are numeric and behave as millisecond-epoch timestamps. The time comparison alone does not establish whether the client has processed a result screen or whether a reward was collected.
- Mission-state interpretation: both nonzero records have first value `2`. This is consistent with the known returned-state interpretation, but the current runtime's complete result-handling/claim state was not independently observed in this probe. Do not infer claim completion from the tuple alone.
- Safety boundary: this was a read-only snapshot; no dispatch, result-claim action, configuration edit, or repeated action was performed. Do not disturb or re-dispatch either active/previously observed fleet while the real-cycle status is unresolved.
- Current acceptance: Redux state access and timestamp conversion are verified; result handling, reward collection, and readiness for a subsequent dispatch remain **NOT VERIFIED**. The one-fleet/one-mission real closed loop is still not accepted.
- Next step: inspect the client's own mission-state and result-screen handling definitions read-only, then use fresh state evidence to determine whether a result is pending. No game action until the state is unambiguous and the next action is separately authorized. No CI workflow was triggered.

## 2026-10-27 — Poi Hand WebView read-only bridge breakthrough

### Verified from user-provided live Poi DevTools output

- Hand plugin entry point: `%APPDATA%\\poi\\plugins\\node_modules\\poi-plugin-hand\\index.js`.
- `pluginDidLoad()` executed after Poi restart; log confirmed `[Hand] WebView read-only probe started`.
- The Poi renderer document exposed exactly one `webview` element: `WebView count: 1`.
- `webview.getWebContentsId()` returned `2`.
- `typeof webview.executeJavaScript === 'function'` evaluated to `true`.
- The call to `webview.executeJavaScript(...)` resolved successfully and returned:
  - URL: `https://play.games.dmm.com/game/kancolle`
  - Title: `艦隊これくしょん -艦これ- - DMM GAMES`
  - `canvasCount: 0`

### Conclusion

- **Confirmed:** The Hand plugin's renderer context can locate the game WebView and execute a read-only JavaScript expression in its page context. This closes the previous gap between inspecting the Poi shell document and inspecting the actual game page.
- **Not confirmed:** DOM-based game controls, clickable targets, input dispatch, reliable UI interaction, or any game action. A zero Canvas count does not establish that the game UI is absent; inspect DOM/iframe structure next.
- The Electron `vm module is unsupported` warning appeared separately; the reported inspection promise still resolved. Do not attribute the warning to probe failure without additional evidence.
- Plugin translation/update metadata warnings did not prevent this probe from succeeding.

### Safety and MVP status

- This was a read-only page inspection; no click, game API request, expedition dispatch, reward claim, or other game-state mutation was performed by this probe.
- Do not dispatch again or interfere with the already-running expedition. The real expedition loop remains incomplete until result collection and safe re-dispatch readiness are verified.
- Next step: inspect page structure read-only (iframe metadata, interactive element counts and limited element summaries) through the already-verified `webview.executeJavaScript` path. Do not add click logic until candidate controls and safe interaction semantics are established.
- Evidence source classification: live runtime result pasted by the user from local Poi DevTools; not independently executed by the assistant. No local filesystem synchronization is implied by this GitHub record.


### 2026-10-27 — Nested game-frame read-only inspection

#### Runtime evidence supplied by user

The Hand plugin's read-only probe inspected the outer DMM game page and its iframe named `game_frame`:

- Outer URL: `https://play.games.dmm.com/game/kancolle`; `readyState: complete`.
- Outer page contains 15 iframes.
- The matching `game_frame` is visible, reported as 1200 × 1200, and its `contentDocument` was accessible without an access error.
- Its document URL is the DMM Gadget iframe URL whose encoded gadget target is `https://w00g.kancolle-server.com/gadget_html5.xml`.
- Inner document `readyState: complete`, `bodyChildCount: 17`, `bodyTextLength: 0`, `canvasCount: 0`, `iframeCount: 1`, `buttonCount: 0`, `inputCount: 0`.
- The probe therefore reached a nested iframe boundary, but did not yet inspect the nested iframe's own URL, document, or rendering elements.

#### Interpretation

- **Confirmed:** The outer page's `game_frame` document can be inspected from the verified Hand → WebView JavaScript path.
- **Not confirmed:** The game canvas, actionable game controls, or any reliable game input mechanism. The zero Canvas/control counts apply only to the inspected Gadget document and must not be generalized to the nested iframe.
- The next useful step is read-only inspection of the single nested iframe's metadata and access status (name, id, src, dimensions, visibility, and—only if accessible—document readiness, Canvas and interactive-element counts). Do not click, dispatch input, or issue game requests during this probe.
- Poi log messages about missing plugin translation/update metadata did not prevent this inspection result from being returned. The Electron `vm module is unsupported` warning is separate and does not by itself prove this probe failed.

#### Safety and MVP status

- This probe was read-only; no game-state mutation is evidenced by this log.
- Do not repeat dispatch or interfere with the already-running expedition. The real expedition loop remains incomplete until result collection and safe re-dispatch readiness are verified.
- Evidence source: runtime log pasted by the user; not independently executed by the assistant. This GitHub update does not imply local filesystem synchronization.


### 2026-10-27 — Game canvas reached through nested iframe (read-only)

#### Runtime evidence supplied by user

The Hand plugin's nested-frame inspection returned a successful result:

- Outer page: `https://play.games.dmm.com/game/kancolle`.
- The DMM Gadget document was accessible and contained two child iframes.
- The game iframe has id `htmlWrap`, targets the `w14h.kancolle-server.com/kcs2/index.php` page, and reported `documentAccess: accessible` and `readyState: complete`.
- The game document title was `艦隊これくしょん - 艦これ -`.
- The game document reported `canvasCount: 1`; the Canvas dimensions and client dimensions were all 1200 × 720.
- The other child iframe was a news/message frame and reported no Canvas.
- The pasted log contained a full game URL with an `api_token` query parameter. That credential-bearing URL is intentionally omitted from this record; do not copy it into logs, issues, commits, or reports.

#### Interpretation

- **Confirmed:** The read-only path from the Hand plugin through Poi's WebView and DMM Gadget frame reached the game document and read its Canvas metadata.
- **Not confirmed:** Canvas input/click semantics, a reliable client-side interaction path, expedition result collection, or re-dispatch readiness. A Canvas being present does not prove that clicks work.
- The earlier `canvasCount: 0` was from the outer Gadget document, not the actual game document.
- Plugin translation/update metadata messages and the Electron renderer `vm` warning appeared in the same console output, but the inspection promise resolved with a successful result; these messages do not negate the observed Canvas result.

#### Safety and MVP status

- This was a read-only inspection; no click, dispatch, reward claim, or game API request is evidenced by this probe.
- Do not repeat dispatch or interfere with the already-running expedition. The real expedition loop remains incomplete until result collection and safe re-dispatch readiness are verified.
- Because the pasted game URL included an `api_token` value, treat it as exposed session material: avoid sharing the unredacted URL again and reload/re-enter the game session when practical so a fresh session URL/token is issued. Do not attempt any direct API call with that token.
- Evidence source: runtime log pasted by the user; not independently executed by the assistant. This GitHub update does not imply local filesystem synchronization.


### 2026-10-27 — Canvas viewport geometry measured (read-only)

#### Runtime evidence supplied by user

The Hand plugin's zero-click Canvas metadata probe returned:

- Canvas internal dimensions: 1200 × 720.
- `getBoundingClientRect()`: left 0, top 0, width 1200, height 720.
- Derived scale: x = 1, y = 1.
- `canvas.onmousedown` and `parent.onmousedown` both evaluated false.

#### Interpretation and limits

- **Confirmed:** Within the game iframe's own viewport, Canvas internal coordinates match CSS display coordinates at a 1:1 scale for this observation.
- The field was named `screenRect` by the probe, but `getBoundingClientRect()` reports coordinates relative to the current document's viewport. Since this Canvas is inside a nested iframe, left/top 0,0 are **not** Windows desktop screen coordinates. Mapping to the outer WebView or desktop still requires accounting for iframe offsets and the relevant browser/window metrics.
- The two `onmousedown` checks being false do not prove that no listeners exist; listeners may be registered with `addEventListener` on the Canvas, ancestors, or other targets.
- **Not confirmed:** Any input event being accepted by the game, CDP-to-screen coordinate mapping, reward collection, or expedition re-dispatch readiness.

#### Safety and MVP status

- This probe was zero-click/read-only; no game-state mutation is evidenced by the supplied log.
- Do not repeat dispatch or interfere with the expedition already underway. The real expedition loop remains incomplete.
- The Poi translation warning, Electron renderer `vm` warning, and `No quest tracking data!` message appeared in the same console output but do not by themselves invalidate the successful metadata result.
- Evidence source: runtime log pasted by the user; not independently executed by the assistant. This GitHub record does not imply local filesystem synchronization.


### 2026-10-27 — WebView input API and host rectangle preflight

#### Runtime evidence supplied by user

The Hand plugin's read-only probe reported:

- `typeof webview.sendInputEvent === 'function'`: true.
- WebView host-document rectangle: left = 0, top = 29, width = 1200, height = 720.
- The inner traversal returned `未找到 canvas` at `frame2.contentDocument.querySelector('canvas')`; no inner geometry JSON was returned in this run.

#### Interpretation and limits

- **Confirmed:** The WebView object exposes a `sendInputEvent` function, and its bounding rectangle in the Poi host document begins at (0, 29) with size 1200 × 720.
- API presence does not prove a call succeeds or that the game accepts input. The host-document rectangle is not itself a coordinate offset to add to WebView-content coordinates; these coordinate spaces must remain distinct.
- The failed Canvas lookup means this run did not produce a complete inner geometry sample. It does not negate the earlier user-supplied observation that a Canvas existed; the nested frame/document may have changed, not loaded yet, or the traversal may be targeting a different document state. Re-check frame IDs, URLs with credential query parameters redacted, readiness, child-frame counts, and canvas counts per document before calculating coordinates.
- No clicks or game-state changes were performed or evidenced by this probe. Do not repeat dispatch or interfere with the expedition already underway. The real expedition loop remains incomplete.
- Evidence source: runtime log pasted by the user; not independently executed by the assistant. This GitHub record does not imply local filesystem synchronization.


### 2026-10-27 — Hand plugin nested Canvas probe succeeded on first attempt

#### Runtime evidence supplied by user

- The closure-based bounded read-only probe completed successfully on attempt 1.
- The outer DMM page reported `readyState=complete` and 15 iframes.
- `game_frame` was accessible and reported a 1200 × 860 rectangle, with two child iframes.
- The nested `htmlWrap` game iframe was accessible and reported `readyState=complete`, one Canvas, and a 1200 × 720 rectangle.
- Canvas internal dimensions and client dimensions were all 1200 × 720; its bounding rectangle was left = 0, top = 0, width = 1200, height = 720 in the game iframe viewport.
- The user-provided log included a credential-bearing `api_token` query parameter. It is deliberately not reproduced here. Treat it as exposed session material; do not repost it and reload/re-enter the game when practical to obtain a fresh session URL/token.

#### Interpretation and limits

- **Confirmed:** The Hand plugin can traverse the nested frame structure and read Canvas geometry in one attempt. The previous `this._probe is not a function` lifecycle issue is no longer present in this run.
- The Canvas rectangle is relative to the game iframe viewport, not automatically Windows desktop coordinates. Although the iframe rectangles are top-left aligned in the supplied snapshot, this alone does not prove that `webview.sendInputEvent` accepts those coordinates or that the game responds to input.
- No input event, click, dispatch, reward claim, or other game action was performed by this probe.
- Next step remains a read-only coordinate-space validation; do not run a click test until the input coordinate origin and viewport clipping are independently established.
- The expedition result/claim/redispatch closed loop remains **NOT VERIFIED**. This is a remote documentation update only; no local file synchronization is implied.


### 2026-10-27 — Hand plugin joint read-only state and Canvas probe

#### Runtime evidence supplied by user

- The joint probe completed and printed its report without a reported probe exception.
- The Poi-host-side store lookup returned fleet 1, named `第1艦隊`, with `api_mission = [0,0,0,0]`.
- The nested WebView traversal returned Canvas geometry: bounding rectangle `x=0, y=0, width=1200, height=720`; intrinsic Canvas dimensions `1200 × 720`.
- The probe log explicitly states that no input events were sent and no game state was modified by this probe.

#### Interpretation and limits

- **Confirmed for this snapshot:** the host-side state lookup returned fleet data, and the nested game document exposed Canvas geometry.
- The observed mission array is `[0,0,0,0]`; this single observation does not independently establish the full semantics of each array element or prove the guard correct for all states. Treat missing, malformed, or ambiguous state as a hard stop.
- Canvas coordinates are document/viewport geometry, not verified desktop coordinates or proof that `sendInputEvent` uses the same origin. No physical-input behavior was tested in this run.
- Do not issue another click based on this probe. Continue with read-only coordinate-space checks before any input experiment.
- The real expedition result/claim/redispatch loop remains **NOT VERIFIED**. This is a GitHub progress record only; it does not imply local filesystem synchronization.


### 2026-10-27 — Physical input test at (50, 50) correlated with visible UI change

#### Runtime evidence supplied by user

- The Hand plugin reported its readiness check passed, then waited 20 seconds before input.
- It logged two click sequences at WebView coordinates `(50, 50)`, and logged completion after the second sequence.
- The user observed a visible UI change after the two-click test.

#### Interpretation and limits

- **New evidence:** unlike the earlier send-only test, the user observed an actual UI change temporally associated with the input test. This supports that the input path can cause a visible interface effect at this tested setup/coordinate.
- Because two clicks were issued before the observation, this run does not identify which click caused the change. The exact UI transition was not described, so avoid claiming that a particular hide/show control was definitively activated.
- The result does not validate arbitrary coordinates, prove coordinate mapping in general, or establish the state guard's correctness. Do not use repeated clicks as a coordinate-search loop.
- Keep game actions limited to the MVP. Do not repeat dispatch or interfere with an expedition already underway. The expedition completion/result claim/re-dispatch loop remains **NOT VERIFIED**.
- This is a remote GitHub progress update based on user-supplied logs and observation; no local synchronization is implied.


### 2026-10-27 — Confirmed reversible UI toggle at (50, 50)

#### User-confirmed visual result

- The first click at `(50, 50)` hid the game UI.
- The second click at the same coordinate restored the UI.
- The two clicks were issued by the Hand plugin's test loop in the same session/layout.

#### Conclusion and limits

- **Confirmed for the tested session/layout:** the `(50, 50)` input sequence triggered a reversible UI hide/show behavior. This is strong evidence that the tested input path reaches a game/UI interaction target.
- This does not establish a universal safe coordinate, validate other coordinates, or prove that the state guard is robust. The toggle target can be layout/state dependent.
- Avoid repeated clicking outside deliberate tests. Do not repeat dispatch or interfere with the expedition already underway.
- Next: use this as the physical-input-path milestone; keep the next step narrowly scoped to read-only identification of the expedition UI/control and its coordinate frame before any expedition-related click. The actual expedition completion/result claim/re-dispatch loop remains **NOT VERIFIED**.
- This is a remote GitHub progress update based on user-confirmed observations; no local filesystem synchronization is implied.


### 2026-10-27 — CDP execution context mapped to the game htmlWrap frame

#### Runtime evidence supplied by user

- The read-only CDP discovery connected to the local debugger endpoint on port 9222 and successfully enabled the CDP Runtime domain.
- The frame tree uniquely identified the game iframe named `htmlWrap`, with sanitized URL `https://w14h.kancolle-server.com/kcs2/index.php` and frame ID `4D4C916E7B825BBB208E594675BC3F33`.
- CDP reported two execution contexts for that frame: context ID `73` with `isDefault=true`, and context ID `74` with `isDefault=false` and name `Electron Isolated Context`.
- The probe reported `TARGET_CONTEXT_IDENTIFIED`; 38 execution-context events were observed overall.
- The user-supplied report states that no game input was sent and `Runtime.evaluate` was not used.

#### Interpretation and limits

- **Confirmed for this session:** the CDP frame-to-context mapping is available. Context 73 is the default page context for the target frame; context 74 is an Electron isolated context. This identifies where a future, tightly bounded read-only inspection could be scoped.
- No game-engine object, scene graph, expedition control, or expedition state has yet been identified by this result alone. Do not infer those capabilities merely from the existence of an execution context.
- Next step: if continuing, use only a bounded read-only inspection against context 73, with no input dispatch and no game actions; inspect minimal metadata such as document/Canvas presence and safe global-property names, without dumping storage, cookies, tokens, or network credentials.
- Do not use `Runtime.evaluate` until a separate inspection script and its exact read-only scope are agreed. The real expedition completion/result claim/re-dispatch loop remains **NOT VERIFIED**.
- This is a remote GitHub progress update based on user-supplied runtime evidence; no local filesystem synchronization is implied.


### 2026-10-27 — CDP endpoint ownership preflight

#### Runtime evidence supplied by user

- Read-only PowerShell inspection found TCP port 9222 listening on loopback address 127.0.0.1, owned by Poi process PID 12612.
- The CDP metadata endpoints `/json/version`, `/json/list`, and `/json` all returned HTTP 200. Browser reported as Chrome/152.0.7977.54 and protocol version 1.3.
- Both target-list endpoints returned one target. Its type was reported as `page webview page service_worker worker worker`, and its URL was `devtools://devtools/bundled/devtools_app.html`; no game page was directly listed.
- No debugger attachment, file change, or game input was performed by this preflight.
- The user-supplied report marked clipboard verification FAILED.

#### Interpretation and next step

- **Confirmed:** port 9222 belongs to Poi PID 12612 and exposes responding CDP metadata endpoints.
- The current `/json/list` result alone does not expose the game page as a target. This does not invalidate the earlier user-supplied discovery that identified the `htmlWrap` frame and contexts 73 (default page context) and 74 (Electron isolated context) during a separate Runtime-enabled discovery session.
- Do not infer that the game frame is gone, and do not attach another debugger or restart Poi solely based on this target-list result.
- Next step: inspect the existing Hand plugin's debugger/target handling and, if needed, perform a bounded read-only check of the current process command line and CDP discovery metadata. Do not print WebSocket URLs, session query strings, cookies, local storage, or credentials. Keep game input disabled.
- The expedition completion, result claim, and re-dispatch loop remains **NOT VERIFIED**.
- This progress update is a remote GitHub write; no local filesystem synchronization is implied.


### 2026-10-27 — Read-only CDP global metadata probe and static main.js extraction

#### Evidence and actions

- Connected through the existing local CDP endpoint on port 9222 and dynamically located the game WebView script `/kcs2/js/main.js`.
- Retrieved the script source with `Debugger.getScriptSource`; the returned source length was **8,495,851 characters**. This was static source retrieval only: no page source was executed, no game function was called, and no game input was sent.
- A separate bounded read-only global metadata probe observed the game page at origin `https://w14h.kancolle-server.com`, pathname `/kcs2/index.php`, with `readyState=complete` and one Canvas sized `1200 × 720`.
- The probe found `window.PIXI`, `window.KCS`, and `window.createjs` as ordinary data-property objects. It did not find direct globals named `app`, `game`, `stage`, `renderer`, `application`, `scene`, or `SceneManager`.
- The PIXI properties inspected (`Application`, `Container`, `DisplayObject`, `Sprite`, `Graphics`, `Stage`, `ticker`, and `utils`) were accessors with getters; the probe deliberately did not invoke those getters. `KCS` exposed only its own `init` function. The report marked `gettersInvoked=false`, `gameFunctionsCalled=false`, `recursiveTraversal=false`, and `gameInputSent=false`.
- Static source inspection located `_0x466acb()` inside a module wrapper. It uses obfuscated property accesses to create a PIXI application/renderer-like object, sets logical dimensions using values `720` and `360`, appends its view to `document.body`, and initializes another module object.
- **Correction recorded:** `_0x460358` is not itself the decoder definition; in the inspected wrapper it is an alias, `const _0x460358 = _0x3353`. The candidate decoder is `_0x3353`, which indexes the table returned by `_0x3f23()`, performs base64-style decoding followed by URI decoding, and caches decoded strings. The full `_0x3f23` table/rotation initialization and the exact decoded property names have not yet been extracted and verified.

#### Current status

- **VERIFIED:** source retrieval succeeded at 8,495,851 characters; the static inspection and global metadata probe remained read-only.
- **NOT COMPLETED:** complete extraction of `_0x3353`, `_0x3f23` and its array-rotation initialization; offline decoding of the property indices used by `_0x466acb()`; identification of a reliable fleet-state object.
- The presence of a Canvas and PIXI globals does not prove that a renderer/scene object is directly accessible or that fleet state can be read through those globals.
- No game source was executed, no game function was called, and no input was sent. Do not repeat the already completed global scan; the next technical step is a bounded static extractor for the exact function/table definitions, followed by offline decoding.
- The real expedition completion, reward claim, and re-dispatch loop remains **NOT VERIFIED**.
- Evidence source: runtime reports and source-analysis results recorded in the conversation on 2026-10-27. This remote documentation update does not imply local filesystem synchronization.

### 2026-10-27 — Archived historical Hand static main.js decoder probe

#### Code artifact

- Archived the user-provided historical sandbox script at [`tools/hand_sandbox/static_main_decoder_20261027.js`](../blob/dev/expedition-closed-loop-mvp/tools/hand_sandbox/static_main_decoder_20261027.js).
- Purpose: use Poi's local CDP endpoint at `127.0.0.1:9222` to locate the game WebView, enable the CDP Debugger domain, retrieve the already-loaded `/kcs2/js/main.js` source with `Debugger.getScriptSource`, and print bounded source excerpts around selected obfuscated-symbol patterns.
- Safety behavior declared by the script: it does not call `Runtime.evaluate`, execute retrieved source, invoke game functions, or send game input. It limits target-list size, has request/global timeouts, and strips URL query data from the displayed script path by using `URL.pathname`.
- Historical result associated with this probe: the conversation records successful retrieval of `main.js` at **8,495,851 characters** and a `STATIC_SOURCE_ANALYSIS_SUCCESS` report. The historical report showed 10 excerpts. This result is sourced from the conversation's runtime report, not a fresh execution during archival.
- Follow-up correction: the script searches for an `_0x460358` definition, but later static inspection established that `_0x460358` is an alias to `_0x3353` inside the relevant module wrapper. Therefore this script is a historical source-retrieval/excerpt probe, not a complete decoder extractor. It does not yet reliably extract the complete `_0x3353` definition or the `_0x3f23` table-rotation initialization.
- Status: **code archived; historical source-read result documented; decoder extraction remains incomplete**. No fresh runtime test was run as part of this archival action. This remote write does not imply local filesystem synchronization.


### 2026-10-27 — Native WebSocket CDP execution-context discovery and read-only evaluate

#### Code/progress audit result

- A backward review of the 2026-10-27 conversation found a completed native .NET WebSocket/CDP probe whose execution-context discovery and read-only evaluation result were not explicitly recorded in this progress log. The separate static `main.js` retrieval/excerpt script is already archived at `tools/hand_sandbox/static_main_decoder_20261027.js`; do not duplicate it.
- The probe connected to Poi's local CDP endpoint at `127.0.0.1:9222`, discovered the game WebView's default execution context over successive context events (ContextId **71**, then stable ContextId **73**), and successfully enabled the Runtime domain.
- Recorded output: `STATUS: CONTEXT_FOUND`; `DETAIL: ContextId=73, Generation=3, Runtime.enable=SUCCESS, Stability=window_passed`.
- A bounded read-only `Runtime.evaluate` then returned `{"accessible":true,"readyState":"complete","canvasCount":1,"safeGlobals":["PIXI","KCS"]}`, with `STATUS: EVALUATE_SUCCESS`. The conversation explicitly records no game input.
- This is useful progress because it establishes that the native CDP path can identify a stable main-world context and perform a limited read-only query, not merely connect to port 9222. It does **not** prove that game actions can be safely automated or that a usable fleet-state object is exposed.
- A related earlier context-only discovery mapped frame `htmlWrap` (FrameId `4D4C916E7B825BBB208E594675BC3F33`) to default ContextId **73** and isolated ContextId **74**, with `Runtime.enable: PASS`; that earlier probe did not call `Runtime.evaluate`.
- **Code archival limitation:** the historical conversation search returned a description of the full PowerShell probe but did not return its literal complete source text. To avoid fabricating or silently rewriting historical code, no reconstructed script is committed in this entry. The exact probe source should be archived as a code file only when its original code block is retrievable.
- No game functions were called and no game input was sent during the documented read-only probe. The real expedition completion, reward claim, and re-dispatch loop remains **NOT VERIFIED**.
- This is a remote GitHub documentation update; it does not imply local filesystem synchronization.


### 2026-10-27 — Real expedition dispatch confirmed; do not repeat

#### User-confirmed runtime milestone

- The user confirmed that one real expedition dispatch was initiated by the automation program, not manually. This is the first recorded evidence that the project reached an actual client-side dispatch action; it is not, by itself, proof that the full expedition closed loop works.
- **Safety constraint:** do not send another dispatch and do not interfere with the expedition currently in progress. The next step is observation only: wait for the existing expedition to finish, detect the completion state, claim the result through the client, then read state again to verify the fleet can be dispatched again.
- Acceptance remains incomplete until all of the following are observed and documented: the current expedition completes, the reward/result is claimed, and the same fleet is confirmed eligible for a subsequent dispatch. Do not infer completion from a timer alone or count the dispatch-start event as a closed loop.
- The conversations available to this audit confirm the dispatch milestone, but did not return the literal complete source of the dispatching script in a retrievable form. No guessed or reconstructed dispatch code is committed by this entry.
- No additional dispatch or game input was performed during this archival action.
- This is a remote GitHub documentation update; it does not imply local filesystem synchronization.


### 2026-10-27 — 成果归档规则落地与代码产物核查

- 已在 `docs/PROJECT_kancollebot_MASTER.md` 增加“成果与进度持久化规则”，涵盖实际产物保存、证据与版本追溯、无进展测试默认不记录、未验证代码的状态标记、归档状态核验及跨对话恢复。
- 本轮核查确认现有只读静态源码探测脚本已存在于 `tools/hand_sandbox/static_main_decoder_20261027.js`（远端 blob SHA: `c343c99c762836a9f2c0eccfdc1c618a562214d0`）；其历史用途和限制已在本进度文件前文记录，因此没有重复创建副本。
- 本轮再次核对现有记录：原生 WebSocket/CDP execution-context 探测的完整原始脚本目前没有可验证的仓库文件；既有进度也明确指出对话检索未取得完整源码。为避免伪造历史代码，本轮不重建该脚本，也不把结果记录冒充源码归档。
- 本次仅修改项目规则与进度文档；没有修改运行代码、安装依赖、执行游戏操作或触发 GitHub Actions。
- 远端写入状态：项目规则文件已提交至分支 `dev/expedition-closed-loop-mvp`，commit `86651d2a7b63fe4559ccceb32880cbd1046709d3`；本进度文件更新已成功并经远端回读确认。具体进度文件更新 commit 以本次操作返回值为准；本地工作树同步状态未检查。
- 下一步：对该分支的规则与进度文件执行远端回读核验；随后继续只归档能取回原始代码或有明确复用价值的成果，不重建无法验证的历史脚本。真实远征完成、领取结果、同舰队再次出击的闭环仍为 **NOT VERIFIED**。

### 2026-10-27 — POI interaction-server branch replacement already tested; KCAuto connection still failing

#### Confirmed historical findings

- The locally installed `poi-plugin-forwarder@1.1.1` was reported to contain five files and **no** `interaction-server.js` or `protocol.js`. Its reported roles/ports were the webhook receiver target `127.0.0.1:9223` (route `/webhook/kancolle`) and relay `9224`; this installation did not provide the KCAuto interaction service on `38591`.
- The upstream branch `XVs32/kcauto_custom` / `feature/poi-interaction-server`, commit `ac2aaa7f478701443a8d357f1ed0b8a4f28b8299`, was reported to contain `interaction-server.js` (8,927 bytes) and `protocol.js` (1,628 bytes), with package version `1.1.0` and a default interaction endpoint `127.0.0.1:38591`. This is a source-branch finding, not proof that the service was active locally.
- The user explicitly reported that the feature-branch files had already been substituted into the local plugin and tested, but **KCAuto still could not connect to Poi**. A prior file-hash comparison reportedly matched six files to the target branch, while the active on-disk/load-state question remained unresolved.
- Therefore, repeating the same branch-file replacement is not a valid next step. The unresolved issue is runtime/plugin loading and connection diagnostics, not merely whether the branch contains code for port `38591`.

#### Port and protocol separation

- `9222`: Poi Chromium remote-debugging/CDP endpoint; useful for target/context discovery, not the interaction-server TCP protocol itself.
- `38591`: candidate local KCAuto↔Poi interaction-server TCP endpoint; branch source documents the framing/protocol, but the local listener/load state must be independently verified.
- `9223` and `9224`: reported forwarder webhook/relay ports; they are not substitutes for the interaction-server endpoint.
- The KCAuto source trace already recorded above identifies `kcauto/util/poi_client.py`, `kcauto/util/interaction_drivers.py`, and `kcauto/util/poi_plugins.py` as the source entry points for future read-only diagnosis. Do not upload or duplicate the third-party plugin source tree; retain only these references and the conclusions needed for the project.

#### Status and next step

- **Verified from historical user reports/source inspection:** the installed v1.1.1 file inventory lacked the two interaction-server files; the target feature branch contained them; replacing branch files did not restore KCAuto connectivity.
- **Not verified:** whether the running Poi process loaded the replaced files, whether a listener was bound on `38591`, and the actual connection error after replacement.
- Next step, if resumed: read-only inspect the current plugin's loaded process and listener state on `38591`, plus the sanitized KCAuto interaction-mode/connection error. Do not reinstall or replace the same files again; do not perform gameplay actions or change global settings.
- Provenance: this entry records the earlier conversation's user-reported local results and source-review findings; it is not a fresh local execution. No game action or GitHub Actions run was performed for this archival update.



### 2026-10-27 — Original PowerShell CDP execution-context discovery script recovered and archived

- The user supplied the full PowerShell script headed `Poi CDP Execution Context Discovery`. It queries the local Poi CDP target list on `127.0.0.1:9222`, enables `Runtime` event delivery, captures `Runtime.executionContextCreated`, maps observed contexts to the `htmlWrap` frame via `Page.getFrameTree`, and emits `TARGET_CONTEXT_IDENTIFIED` when its conditions are met.
- The script explicitly states that it does not send game input and does not call `Runtime.evaluate`. This archive action did not execute the script or interact with the game.
- Archived source file: `tools/hand_sandbox/cdp_context_discovery_20261027.ps1`.
- Provenance limitation: the source text was recovered from the user's current message. It matches the target script's distinctive structure and status marker, but byte-for-byte identity with the historical chat message has not been independently established.
- Remote verification: file was created on branch `dev/expedition-closed-loop-mvp` in repository `wosafety0/wosafety0clamp`, commit `0401e20a59638132a06485dbd4e6b77bf6a09dfd`; remote readback returned blob SHA `f335cbe4d09452731c97f4dc0f5ee8c23bd930cc` and confirmed the expected header, `Runtime.enable`, `TARGET_CONTEXT_IDENTIFIED`, and the `Runtime.evaluate: NOT USED` marker.
- No local worktree synchronization was performed or verified. No GitHub Actions workflow was run.
- Follow-up caution: this script waits for Runtime events after `Runtime.enable`; if the expected context events are not captured, that absence alone does not prove the contexts do not exist. The clipboard logic verifies the base report, then copies a final report but does not verify that final copy again.


### 2026-10-27 — Read-only renderer/source reference probe

#### Confirmed evidence from user-provided runtime output

- The page was `https://w14h.kancolle-server.com/kcs2/index.php`, `readyState=complete`, with exactly one canvas at 1200×720 (client/offset size and bounding rectangle matched).
- Global own-property inspection found data properties `PIXI`, `KCS`, and `createjs`; the names `app`, `game`, `stage`, `renderer`, `application`, `scene`, and `SceneManager` were absent as globals.
- On the `PIXI` object, `Application`, `Container`, `DisplayObject`, `Sprite`, `Graphics`, `Stage`, `ticker`, and `utils` were accessor descriptors; the probe recorded getter presence but did not invoke getters. `loader` was a data property. `KCS.init` was a data property whose value type was function; it was not called.
- The loaded `/kcs2/js/main.js` source was retrieved as text, length 8,495,851 characters. Static excerpts show `_0x466acb=()=>{...` inside a module wrapper, where `const _0x460358=_0x3353`. Thus `_0x460358` is a local alias for the string decoder `_0x3353`, not a separate decoder definition.
- The focused excerpt shows `_0x466acb` setting PIXI dimensions to 720×360, constructing a PIXI application with options including `antialias:false`, `backgroundColor:0`, and a `forceCanvas` value read through the decoder, then appending its view and creating another object from a decoded constructor name. Exact property names remain undecoded.
- User-provided probe output explicitly records: getters not invoked, game functions not called, no recursive traversal, and no game input sent. This entry is based on the supplied output; it is not a fresh execution by this archival action.

#### Status and next step

- **Verified from the supplied output:** read-only source retrieval and own-property descriptor inspection completed.
- **Not yet verified:** decoded names for the renderer/application properties, the live application instance reference, or a usable fleet-state object.
- Next: use a bounded static extraction to capture the complete `_0x3353` definition and `_0x3f23` string-table/rotation initializer, then decode offline outside the game page. Do not call page getters or game functions to resolve these names.
- No game input or action was performed for this entry. The expedition completion/reward claim/re-dispatch loop remains **NOT VERIFIED**.
- Archival state: this progress entry is being written to the remote repository branch; local worktree synchronization is not implied.

### 2026-10-27 — Static decoder extraction script archived

- Repository identity was verified by successful reads from `wosafety0/wosafety0clamp`; target branch: `dev/expedition-closed-loop-mvp`.
- Read the project master rules and existing progress document before writing. The existing static probe is `tools/hand_sandbox/static_main_decoder_20261027.js`; it extracts bounded snippets and is not a replacement for the new targeted extractor.
- Added `tools/hand_sandbox/static_decoder_extract_20261027.js` using the GitHub Contents API. Returned creation commit: `d4cc9c475a7e7aedd03b382dc0509f9b623d0074`.
- Remote readback verified the new file at the target branch: blob SHA `7f8c31fa9ae5bbe770745004c3e4f6451872c68b`, length 13,252 characters. Marker checks passed for extraction targets `_0x3353`, `_0x3f23`, `_0x466acb`, CDP `Debugger.getScriptSource`, configured host `w14h.kancolle-server.com`, and the no-`Runtime.evaluate` / no-game-input safety flags.
- The script is **archived remotely but not yet run by the user**. It is designed to retrieve source text only, report the decoder/table/factory definitions and bounded contexts around `_0x3f23` references, and list decoder calls in `_0x466acb`; it does not perform offline decoding itself.
- **Not verified yet:** whether the lexical extraction boundaries capture all required source correctly; the actual string-table rotation logic and decoded property names; PIXI application/main-object construction path. These require inspecting the script's output offline after the user runs it.
- No page source was executed, no game function or getter was called, no game input was sent, no local files were changed, and no GitHub Actions workflow was run.
- Archive state: script creation returned a remote commit SHA and a subsequent fetch confirmed the remote file/blob. Progress-document update must be independently read back before this round is considered fully archived.

### 2026-10-27 — Hand sandbox CDP target enumeration

- The read-only extractor successfully loaded and ran in the Hand sandbox after the local file's accidental leading text was removed.
- The CDP target-list request returned six targets. The visible game-related top-level target is the DMM game launcher at `https://play.games.dmm.com/game/kancolle`, and its debugger WebSocket is available.
- This confirms that the sandbox can enumerate CDP targets and that a debuggable launcher target is present. No game-page source was retrieved in this run.
- Next: inspect the selected page's frame tree with the read-only CDP `Page.getFrameTree` command, reporting only frame names, URLs, and hierarchy. Do not evaluate page JavaScript or send game input.
- No game functions or getters were called, no game input was sent, no local worktree sync was performed, and no GitHub Actions workflow was run.

## 2026-10-27 — Read-only discovery of the embedded game frame

- **Verified result:** The Hand sandbox successfully ran the read-only CDP `Page.getFrameTree` probe against the DMM launcher page.
- **Frame discovery:** The frame tree contains exactly one frame whose URL path is `/kcs2/index.php`. It is nested under the `game_frame` gadget frame and is named `htmlWrap`; its host is `w14h.kancolle-server.com`.
- **Root cause resolved:** The earlier target search inspected top-level CDP targets only. The game page is an embedded frame, so filtering top-level target URLs cannot discover it.
- **Next step:** Use CDP target/session metadata to establish a read-only debugging route to the embedded game frame, then retrieve `/kcs2/js/main.js` via `Debugger.getScriptSource` only. Do not use `Runtime.evaluate`, invoke game functions/getters, send game input, or log full frame URLs containing session parameters.
- **Safety:** The user-provided console output contained a URL with an `api_token` query parameter. This progress entry intentionally omits that value and the full parameterized URL. Avoid copying session tokens into future logs or reports.
- No local worktree sync was performed; no GitHub Actions workflow was run.

## 2026-10-27 — Embedded-frame-aware static extractor updated

- **Verified repository change:** Updated `tools/hand_sandbox/static_decoder_extract_20261027.js` to connect to the DMM KanColle launcher target, call `Page.getFrameTree`, require exactly one frame with pathname `/kcs2/index.php`, and then enable CDP debugger metadata collection before requesting `/kcs2/js/main.js` through `Debugger.getScriptSource`.
- **Race mitigation:** The extractor waits 1,000 ms after `Debugger.enable` succeeds before deciding whether a matching `Debugger.scriptParsed` event was observed.
- **Output hygiene:** Frame diagnostics retain only frame name and path; the parameterized frame URL is not printed. The source analysis receives the sanitized pathname, not the session-bearing frame URL.
- **Safety boundary:** The revised source contains no `Runtime.evaluate` command and does not execute page source, invoke game functions/getters, or send game input.
- **Verification:** The updated script was fetched back from the target branch and compared byte-for-byte with the submitted content. This verifies the remote file content, not runtime success. The user has not yet run this revision.
- **Script commit:** `0b633d05c10dbaa76687f63fa5202f8edaaca415`.
- No local worktree sync was performed; no GitHub Actions workflow was run.

## 2026-10-27 — Successful read-only retrieval of game main.js

- **Verified runtime result:** The embedded-frame-aware Hand sandbox connected to the DMM launcher target, found the unique game frame at pathname `/kcs2/index.php`, and retrieved the game script using CDP `Debugger.getScriptSource`.
- **Source metadata:** scriptId `758`; source length `8,495,851` characters. The report explicitly states that page source was not executed.
- **Safety report:** `Runtime.evaluate` unused; page source not executed; game functions not called; getters not invoked; no game input sent; no recursive object traversal.
- **Useful extraction:** The report returned the `_0x3353` decoder definition (source offsets 317022–318153) and the `_0x3f23` string-table factory (source range begins at offset 0 and ends at 317022), providing the concrete static source needed for offline decoder analysis.
- **Next useful step:** Improve the offline lexical-boundary handling for the `_0x466acb` definition, then validate extracted definitions against the retrieved source without evaluating page code.
- No local worktree sync was performed; no GitHub Actions workflow was run.

## 2026-10-27 — Decision: continue staged static analysis of obfuscated game source

- **Decision:** Continue only as a staged, read-only investigation; do not attempt to deobfuscate the entire multi-megabyte bundle up front.
- **Feasibility:** JavaScript obfuscation often preserves enough structure to recover string decoding, symbol relationships, function boundaries, and selected control/data flow. This does not guarantee recovery of original variable names, comments, or a complete original source tree.
- **Stages:** (1) validate lexical extraction of the known decoder and string-table factory; (2) locate and extract the application wrapper/factory and local decoder alias as plain text; (3) derive strings offline without evaluating code in the game page; (4) trace only the functions needed for the automation research goal; (5) validate conclusions against independent static references and keep all live-game input disabled.
- **Stop conditions:** Stop or reassess if the work requires executing page code, invoking game functions/getters, sending game input, or if a small targeted analysis does not produce actionable understanding.
- **Current baseline:** Main script retrieved via `Debugger.getScriptSource`, length 8,495,851 characters; `_0x3353` and `_0x3f23` source text extracted. This is not yet a claim that the complete game logic has been reconstructed.

## 2026-10-27 — Decision: evaluate source analysis by automation value, not deobfuscation completeness

- **Goal:** Determine whether static analysis of the obfuscated game bundle can materially improve KancolleBot reliability over screenshot-and-coordinate automation.
- **Screenshot/coordinate approach:** Low initial reverse-engineering cost; works through the visible UI; remains useful as a fallback and for actions with no stable programmatic interface. Weaknesses include layout/scale sensitivity, visual ambiguity, timing, and difficulty proving that a click achieved the intended state.
- **Source-informed approach:** May reveal state models, UI event routing, data flow, request/response structures, and existing read-only observation paths. The highest-value result is a validated way to observe state and confirm outcomes, not a cosmetically deobfuscated bundle.
- **Expected hybrid design:** Use structured/read-only state observation for decision-making and verification where a supported, stable source exists; retain screenshots for visual confirmation and UI-only workflows. Any input mechanism remains separately gated and must not be assumed available merely because source code was understood.
- **Validation criteria:** For each target workflow, measure whether source analysis provides (1) a stable state signal, (2) a clear success/failure predicate, (3) lower sensitivity to resolution/layout/timing, and (4) repeatable offline validation. Do not assume benefits until demonstrated.
- **Priority for current investigation:** First map relevant state/data paths and read-only observability; only then decide whether to investigate action dispatch. Keep live game input disabled throughout static analysis.

## 2026-10-27 — Cost-controlled investigation plan

- **Purpose:** Decide whether source analysis improves KancolleBot automation enough to justify further effort, instead of pursuing deobfuscation as an end in itself.
- **Initial budget:** Maximum 6 hours of active investigation, staged and not pre-authorized all at once; cash budget 0 using the existing environment. No new installations, paid services, or GitHub Actions.
- **Stage 1 — 45 minutes:** Fix/validate lexical extraction of the known symbols (`_0x466acb`, `_0x460358`) using source text already captured. Deliver a reproducible static extractor and validated offsets/text, or stop if the target declarations cannot be identified reliably.
- **Stage 2 — 90 minutes:** Map only the data paths relevant to fleet/ship/expedition state and compare them with existing Poi read-only tools. Deliver a short source map with exact identifiers and evidence, or stop if no actionable read-only path emerges.
- **Stage 3 — 90 minutes:** Define a small offline state-verification prototype using saved/static fixtures or existing read-only responses; no live game input. Deliver repeatable checks and explicit success predicates.
- **Stage 4 — 60 minutes:** Compare the prototype with screenshot/coordinate workflow on a small set of representative cases. Record observable differences such as ambiguity, dependency on screen geometry, and ability to prove outcome; do not claim improvement without measurements.
- **Reserve — 75 minutes:** Only for resolving a specific blocker or strengthening a result that has already shown value. No open-ended deobfuscation.
- **Go criteria:** Continue only if the work produces reusable code or a source-backed, independently verifiable state signal; can be tested without executing the game bundle; and is plausibly useful to a target workflow.
- **Stop criteria:** Stop when the timebox expires without a validated artifact, when findings are only cosmetic deobfuscation, when the only proposed route requires unsafe/unsupported live execution, or when there is no clear advantage over existing Poi/visual methods.
- **ROI metrics:** Reuse value of extracted artifacts; repeatability of state checks; ability to prove task outcomes; reduction in screen-layout dependency; maintenance burden. Compare before/after on the same small cases and do not assume a gain before measurement.
- **Safety and cost boundaries:** No new software installation, no paid APIs/services, no Actions, no live game inputs. Archive successful reusable outcomes and decisions only; do not create progress entries for failed or no-progress attempts.

## 2026-10-27 — Reusable-script inventory for cost-controlled MVP

- **Highest-value existing candidate:** `scripts/poi_ui_snapshot.ps1`. It is a Windows PowerShell 5.1 read-only Poi MCP collector with health/initialize/tool checks, explicit allowlist (`poi_describe`, `poi_get`, `poi_lookup`), fleet/ship relationship snapshots, JSONL capture, and no game actions. Treat as a reusable observation harness, not a finished expedition classifier; `api_mission` is intentionally preserved as raw data because its semantics remain unconfirmed. Before operational reuse, verify privacy-safe output and clipboard/report behavior against the current local runtime.
- **Bridge preflight candidate:** `tools/verify_poi_bridge.py`. Standard-library read-only health/tool/payload-shape verifier for nanodesu and Poi plugin MCP. It explicitly reports session validity as NOT_VERIFIED, so it is a diagnostic/preflight tool, not proof of a logged-in fresh game state.
- **CDP preflight candidate:** `tools/verify_cdp_session.py`. Read-only loopback CDP session and viewport metrics check; does not send input or evaluate JavaScript. Useful as a preflight component, not an automation controller.
- **Frame/context discovery utility:** `tools/hand_sandbox/cdp_context_discovery_20261027.ps1`. A successful live read-only run previously identified the `htmlWrap` game frame and mapped execution contexts. Reusable for diagnostics; not a runtime dependency of the MVP and should sanitize all URLs before output.
- **Static source retrieval utility:** `tools/hand_sandbox/static_decoder_extract_20261027.js`. Successfully retrieved main.js (8,495,851 chars) via `Debugger.getScriptSource` and extracted `_0x3353` and `_0x3f23`; extraction remains partial for `_0x466acb`, so classify it as a successful research utility, not production-complete.
- **Lower priority / not proven as product component:** `tools/inspect_accessible_targets.py` and the older `static_main_decoder_20261027.js`. They are bounded read-only diagnostic/research tools; current evidence does not establish useful accessible targets for the actual game UI or a complete decoder extraction. Do not integrate them into the runtime by default.
- **Existing MVP implementation to reuse rather than duplicate:** `PoiExpeditionReader.read_fleets()` and `normalize_poi_fleets()` are already present in the project codebase. Do not create a second fleet reader/normalizer. Their interpretation of `api_mission` slots still requires authoritative passive observation before it can drive actions.
- **Decision:** Reuse existing components first. The next six-hour plan should spend Stage 1 only on the current static extractor, then Stage 2 should compare the existing snapshot/bridge tools with the existing fleet reader. Do not write another generic state-shape layer or promote diagnostic scripts to production without acceptance tests.
- **Status boundary:** This is a source review of archived repository files plus prior successful run evidence. It does not mean every listed script was rerun in this review, and it does not claim the live expedition loop is complete.


## 2026-10-28 — 静态源码提取器离线回归测试通过

- 新增 Node.js 内置测试，直接从生产静态提取器源码中以词法方式取出 `extractDefinition`、`findBalancedEnd`、`scanExpressionEnd`，不启动 CDP/HTTP、不执行页面源码、不调用游戏函数。
- 回归夹具覆盖命名函数、函数赋值、局部别名、嵌套括号、字符串中的括号字符、注释中的括号字符，以及缺失符号返回空值；同时检查提取文本与原始源码切片一致。
- CI 工作流已加入 `node --test tests/static_decoder_extract.test.cjs`，位于 Python 编译和单元测试之前。
- 测试文件提交：`a281b94e35234fa921fcb1ca7f400ef2cf15c575`；CI 接入提交：`26deb6b63a4f1eec7c576a5dd0a2472ee9d686f5`。
- GitHub Actions 实际运行结果：
  - [Node 静态提取回归测试提交](https://github.com/wosafety0/wosafety0clamp/actions/runs/40607983044) — success。
  - [CI 接入提交（Node 测试、Python 编译、Python 单元测试）](https://github.com/wosafety0/wosafety0clamp/actions/runs/40607996076) — success；作业步骤均为 completed/success。
- 已从权威远程分支回读确认测试源码与 CI 配置存在，且工作流结果与对应提交 SHA 匹配。进度记录只记载上述正向成果，不记录失败尝试。
- **验证边界：** 这证明静态词法提取器的离线回归测试与当前 CI 流水线通过；不证明用户本机 Poi/Hand 环境已执行快照，也不证明已获得完整 main.js 离线文件。当前提取器仍是研究工具，不是完整解混淆器；真实游戏输入仍未执行。


## 2026-10-28 — CDP main.js 离线快照保存器与完整 CI 验证

- 在现有只读 CDP 静态提取器中增加完整源码落盘：通过 `Debugger.getScriptSource` 取得当前匹配的 `main.js` 文本后，将原始文本保存到 Windows 用户桌面下的 `KancolleBot-CDP-Snapshots` 目录；不将游戏源码或快照文件提交到 GitHub。
- 同时写入 JSON 元数据，记录 UTC 捕获时间、源文件名、清理查询参数后的脚本 URL、scriptId、字符数、UTF-8 字节数、SHA-256 和只读安全标记。源码文件采用独占创建，避免同名覆盖；元数据写入使用合法 JSON 换行。
- 新增离线回归测试，以临时目录验证保存文本与原始输入逐字节对应、字节数和 SHA-256 正确、URL 查询参数不会写入元数据，以及页面源码未执行/未使用 Runtime.evaluate 的安全标记。
- 代码提交：`c0ec0b51db0aea8e6ffee209e697c5261cb84f86`（快照能力）、`a832dffed0ca8440d8206e588b84e63cdb1708f8`（元数据 JSON 格式修正）、`1892a23f22563a70d3c0e0d2badaf4bdcd05a4ce`（测试与 URL 元数据核验）。
- 对应最终完整 CI：[GitHub Actions run 40718219059](https://github.com/wosafety0/wosafety0clamp/actions/runs/40718219059) — success；Node 静态测试、Python 编译和 Python 单元测试全部成功。
- 已从权威远程分支回读生产脚本、测试文件与工作流，并核对最终 Actions 作业步骤。当前进展为：**实现并通过云端离线测试；尚未在用户本机 Poi/Hand 中运行，也未实际捕获本机 main.js 文件。**
- 使用限制：脚本默认写入 `~/Desktop/KancolleBot-CDP-Snapshots`；若本机桌面目录重定向、Hand 沙盒禁用文件系统访问或写入被拒绝，快照会失败或只留下源码文件而无元数据。需以实际运行输出和文件哈希核验为准。真实游戏输入仍未执行。


### 快照保存器补充边界验证（2026-10-28）

- 快照文件名保留 UTC 毫秒精度，降低短时间连续捕获发生同名冲突的概率；仍使用 `wx` 独占创建作为最终覆盖保护。
- 对目标目录创建/源码写入失败增加明确的结构化错误返回；新增测试覆盖不可写目标目录。文件名格式也由回归测试检查。
- 最终验证提交：`4ad4222b26471c0e7bfaf841486336c6dc501c24`；[GitHub Actions run 40718620102](https://github.com/wosafety0/wosafety0clamp/actions/runs/40718620102) 的 Node 回归测试、Python 编译和 Python 单元测试步骤全部 success。
- 最终远程读取确认：生产脚本 blob `29e55810f86e930efbbc04f2a469aeac1a821483`、测试文件 blob `1afa94f986da30a1b21f798b64d9981c88869357`；CI 作业各步骤均已完成并成功。
- 本补充只记录通过验证的正向成果；不代表用户本机快照已执行，仍需用户在 Poi/Hand 环境运行并上传输出文件后，才能进入离线解码分析。


## 2026-10-29 — CDP main.js 快照报告 URL 脱敏回归验证

- **已核验的修复：** `tools/hand_sandbox/static_decoder_extract_20261027.js` 将脚本 URL 的安全化处理复用于快照元数据与控制台静态源码报告的 `pageSource.url`。报告不再直接打印 CDP `Debugger.scriptParsed` 提供的原始脚本 URL，避免其中的查询参数、片段和 URL 用户信息进入日志。
- **离线回归测试：** `tests/static_decoder_extract.test.cjs` 使用含模拟 `api_token`、URL 凭据与片段的合成 URL，验证元数据与控制台报告仅保留 `origin + pathname`；测试仅处理合成源码文本，不连接 CDP、Poi 或游戏页面。
- **远程代码提交：** `d16857c22e2f6aa2970ed709c97e8b8fee186ae1`；**测试提交：** `7a059f8b9d1b73b4b6074347d1f71c4f36b1160a`。
- **远程回读：** 生产脚本 blob `3cdac1e7be25f712a8176dae03f94dd12a664e48`；测试 blob `3e6f150bdd39d913643770db18558966036d39e3`，均从权威分支 `dev/expedition-closed-loop-mvp` 重新读取并核对。
- **CI 验证：** [GitHub Actions run 40774017056](https://github.com/wosafety0/wosafety0clamp/actions/runs/40774017056) 对应测试提交 `7a059f8b...`，结论为 `success`；Node 静态提取测试、Python 编译和 Python 单元测试步骤均为 `completed/success`。
- **验证边界与下一步：** 仅证明远程代码、合成离线测试和 CI；用户本机 Poi/Hand 尚未运行本次版本，`main.js` 本地完整快照与元数据仍未实际核验。下一步需用户在现有 Hand 沙盒中运行当前只读脚本，检查 `SNAPSHOT_FILE`、`SNAPSHOT_METADATA`、`SNAPSHOT_SHA256` 和 `STATUS`，再将脱敏后的摘要及必要的离线快照提供用于静态分析。真实游戏输入持续关闭；没有调用 `Runtime.evaluate`、游戏函数或 getter。


## 2026-10-29 — 离线 CDP 快照完整性核验工具通过 CI

- 新增 `tools/hand_sandbox/verify_cdp_snapshot.cjs`，仅使用 Node.js 标准库读取本地快照及 JSON 元数据；逐项检查 UTF-8 字节数、JavaScript 字符数和 SHA-256，并以明确的 `verified` 布尔值报告结果，不连接 Poi/CDP/游戏页面。
- 新增 `tests/verify_cdp_snapshot.test.cjs`，用合成源码和元数据验证完整性核对通过以及修改源码后的哈希校验失败。
- CI 已同时运行静态提取器测试和快照核验测试；[GitHub Actions run 40793353211](https://github.com/wosafety0/wosafety0clamp/actions/runs/40793353211) 的 Node 测试、Python 编译和 Python 单元测试步骤均为 success。
- 生产核验脚本提交 `b22439229a4708bc00b7fdc60b85e343278d79f7`；测试提交 `67ebca6c0ad927a65b0233fd92b10ddc74f9190b`；CI 提交 `d1ce8cbb4e663d2b0898edbaa70c6e9c55a114c8`；校验文件名处理修正提交 `038abde0f76d4d335b1277726910b40656aa987b`。
- 从权威远程分支回读核验脚本 blob `17fa136dd4b73a300ef83185ffcde734d3e54f2e` 和测试文件 blob `d9dfca0da8367a191caff7fd9366efafc477e172`。
- **使用方式：** `node tools/hand_sandbox/verify_cdp_snapshot.cjs "<path-to-main.metadata.json>"`。退出码 0 表示完整性验证通过，1 表示内容不匹配或核验错误，2 表示未传路径。
- **验收边界：** 只证明离线合成数据测试通过；用户本机尚未提供真实 `main.js` 快照，因此不宣称本机捕获或实际游戏运行成功。只归档已验证的正向成果。


### 快照完整性校验器补充边界测试（2026-10-29 白天）

- 对离线核验工具新增合成测试：Unicode/代理对字符的 UTF-8 字节数与 JavaScript 字符数分别验证；元数据字符数、字节数被改动时返回未通过；不安全相对路径、非法 SHA-256、负数字节数与非法 UTF-8 源文件被拒绝。
- 仅修改 `tests/verify_cdp_snapshot.test.cjs`，不连接游戏/CDP、不执行游戏输入。测试提交 `398766fffdbbcb9c7ef930bbe540ac35bbe097a1`，从 GitHub 权威分支回读测试文件 blob `8da0bbf8916d545ccf091ff9db7ba03eaa6afafc`。
- [GitHub Actions run 40793688562](https://github.com/wosafety0/wosafety0clamp/actions/runs/40793688562) 的 Node 静态测试、Python 编译和 Python 单元测试作业步骤均为 success。
- 验收范围限于离线合成数据及云端 CI；用户本机真实快照采集仍未验证。仅记录通过验证的正向成果。


### 快照核验器跨平台路径安全修复（2026-10-29）

- 离线校验器同时采用当前平台 `path.basename` 和 `path.win32.basename`，并拒绝反斜杠，避免在 Linux CI 环境将 Windows 风格目录分隔符误判为合法文件名；不执行游戏输入。
- 代码提交 `d17ecb9fa002f6705821c87f2e8653625227315a`；回归测试提交 `c9bb657e3791e43c992f0b9e04bf0c87b76a8c51`，覆盖模拟 Windows 风格文件名。
- [GitHub Actions run 40797386740](https://github.com/wosafety0/wosafety0clamp/actions/runs/40797386740) 对应最新测试提交，已完成且结论为 `success`。源码与测试从权威远程分支回读，blob 分别为 `b60e16bf535630b2d80d8872c36c62797e55928a`、`9e6fc36b835c26cedeb2154185ddb10ecf95819d`。
- 验收范围为合成离线数据与 CI；真实 Poi/Hand 快照采集尚未验证。


## 2026-10-29 — Static extractor report status now reflects actual completeness

- Fixed a reporting correctness issue in `tools/hand_sandbox/static_decoder_extract_20261027.js`: the JSON report previously hard-coded `STATIC_EXTRACTION_COMPLETE` even when one or more required definitions were missing. The report now derives its status from the presence of `_0x3353`, `_0x3f23`, and `_0x466acb`; missing definitions yield `PARTIAL_EXTRACTION`, and a failed snapshot save yields `SNAPSHOT_WRITE_ERROR` when definitions are otherwise present.
- Added offline tests for both partial and complete report states, including extracted decoder/table/factory/alias fields, decoder call capture, and safety flags. Tests operate on synthetic strings and instrument the production lexical functions only; they do not connect to CDP/Poi, execute page source, call game functions/getters, or send input.
- Exact-head CI [run 40802538224](https://github.com/wosafety0/wosafety0clamp/actions/runs/40802538224) passed on commit `d1012091954f037757e38684b8c4472c5f11dfae`; the Node static extraction and snapshot-verifier tests, Python compilation, and Python unit tests all completed successfully.
- Authoritative remote readback confirmed production extractor blob `80280ad548b9016f96ea1fc94e0f85c25c0d43e8` and test blob `f6a28dd71091bb2a7806638cfd2c7814fc881f63`.
- **Boundary:** offline extractor/report semantics are verified. This does not complete live-source extraction or offline analysis of the real Poi `main.js`; the current chat has no newly captured local snapshot/metadata to verify. No live game code was executed and no game input was sent.


## 2026-10-29 — Bounded extraction report and full offline sidecar

- Improved `tools/hand_sandbox/static_decoder_extract_20261027.js` so the Hand console report stays bounded instead of printing potentially hundreds of thousands of characters from the `_0x3f23` table. The console now shows definition offsets/kinds/character counts plus bounded context excerpts.
- The full extracted definitions, decoder-call expressions, and table-reference contexts are written to a separate `.extraction.json` sidecar next to the captured `main-*.js` snapshot and metadata. This preserves complete offline-analysis inputs without relying on copying a huge console payload.
- Added a synthetic regression test with a 190,000-character factory definition. It verifies the console report remains under the configured 180,000-character limit while the sidecar retains the full definition and safety flags. No real Poi source or game runtime was used by this test.
- Exact-head CI [run 40803106268](https://github.com/wosafety0/wosafety0clamp/actions/runs/40803106268) passed on commit `de4975b98e32b955fbdf77f0d344838092587a80`; JavaScript tests, Python compilation, and Python unit tests passed.
- Remote readback verified extractor blob `3341884b9fa353a628e3879289930958397f30c3` and test blob `d90d5e3d55e0141d0de70cb86168b8a343f4bbf6`.
- **Remaining real-source boundary:** this closes the tooling/output packaging issue, not the actual capture gap. No new real Poi snapshot or `.extraction.json` is available in the current session, so real `_0x3353`/`_0x3f23` rotation and decoded property names remain unverified. The extractor still uses CDP `Debugger.getScriptSource` only; no game source execution or input is performed.


## 2026-10-29 — Real Poi source snapshot verification (read-only)

- Desktop Commander read the two locally preserved Poi CDP snapshots captured at 03:04:22.385Z and 03:05:53.145Z. Both actual `main.js` files were independently hashed from disk using PowerShell `Get-FileHash -Algorithm SHA256`; both match `073df7c8e4d4680208070b22806273b332e2f5ba7ae8b80005ad5914f336561e`. Each metadata record reports 8,496,723 bytes / 8,495,851 characters, CDP script ID 907, and retrieval through `Debugger.getScriptSource`.
- The second real `.extraction.json` sidecar was read successfully. It reports `PARTIAL_EXTRACTION`, `_0x3f23` at [0, 317022), `_0x3353` at [317022, 318153), and local alias `_0x460358` at [8461300, 8461324). The decoder text shows index subtraction by `0x150`, a Base64/URI decoding routine, and a decoded-value cache. This does not yet verify rotated-table indices or decoded properties.
- Independent read-only source text search found `_0x466acb` at offset 8464467 in a `const` declaration chain with an arrow-function initializer and a later call occurrence. The existing extractor did not recognize this lexical form; its null factory definition is an extraction limitation, not evidence the symbol is absent.
- The sidecar and original game source remain local; neither is published to the public repository. The snapshot metadata and extraction report assert no `Runtime.evaluate`, page-source execution, getter calls, game-function calls, or game input.
- **Verified scope:** two on-disk source hashes agree, both metadata files agree, the sidecar is readable, and the factory name occurs in the real source. **Pending:** robust factory boundary extraction, rotation reconstruction, offline decoding, and independent validation of decoded names. No completion claim for those pending tasks.


## 2026-10-29 — Real source rotation bootstrap located

- Read-only offline inspection of the locally hashed real `main.js` at offset 318153 found the immediately following rotation bootstrap: an IIFE receives `_0x3f23` and target constant `0xd12ca`, aliases `_0x3353`, computes an arithmetic expression using `parseInt` of decoded entries, and rotates the table with `push(shift())` until the expression equals the target.
- This establishes that the table's initial literal order cannot be used directly for decoded-index mapping. The next offline step is to reconstruct the rotation without executing page code, then validate decoded strings against call sites.
- A second source-context check at offset 8464467 confirmed `_0x466acb=()=>{...` in a comma-separated declaration chain. This explains the extractor's missed factory definition and identifies the parser case to support.
- **Boundary:** rotation mechanism and target are observed from actual source text; rotation count, resolved indices, and decoded names remain unverified. No game code or inputs were executed.


## 2026-10-29 — Static extractor comma-separated arrow factory fix (local verification)

- Updated `tools/hand_sandbox/static_decoder_extract_20261027.js` so `extractDefinition` recognizes an assignment target inside a comma-separated declaration chain, while returning the target identifier as the start offset rather than the preceding comma.
- Updated `scanExpressionEnd` to stop at a top-level comma while preserving commas nested in calls, arrays, objects, and parenthesized expressions.
- Added a production-module regression test in `tests/static_decoder_extract.test.cjs` for a comma-separated arrow factory with nested object/array values, punctuation inside a string, nested calls, and a following declarator.
- Verification on the isolated local clone of `wosafety0/wosafety0clamp`, branch `dev/expedition-closed-loop-mvp`:
  - JavaScript offline tests: **13/13 passed**.
  - Python compilation: passed.
  - Python unit tests: **122/122 passed**.
  - `git diff --check`: passed.
  - The production lexical functions extracted the factory from the locally preserved real Poi snapshot at offsets **8,464,467–8,465,759**, length **1,292** characters; the extracted text begins with `_0x466acb=()=>{`, ends at the matching body close, and leaves the following comma outside the extraction.
- Verification is offline and read-only with respect to the game: the snapshot was treated as text; page source was not executed, no game functions/getters were called, and no game input was sent.
- **State boundary:** production code and tests are modified and verified in the local clone only. No local commit or GitHub push is claimed in this record; authoritative remote integration and remote readback remain pending explicit authorization for the remote write.


## 2026-10-29 — Real-source factory call-site decoding (offline)

- Added local-only reusable analyzer offline_factory_decode_20261029.js and JSON result offline_factory_decode_report_20261029.json under the snapshot directory. Both remain outside the public repository because the analyzer consumes the local game-source snapshot.
- The analyzer validates the source SHA-256, string-table size (14,271 entries), the previously independently verified rotation count (323), and the production extractor's exact factory boundary before decoding.
- Static alias propagation identified five decoder aliases in _0x466acb; 33 decoder call sites were decoded across 24 unique indices without evaluating the captured game source.
- Decoded names include settings, GC_MAX_IDLE, GC_MAX_CHECK_COUNT, iOS_device, Application, default, width, height, renderer, plugins, interaction, autoPreventDefault, location, search, substring, length, split, body, appendChild, view, stage, addChild, AppInitializeTask, and start.
- The analyzer's JSON report was re-read and parsed successfully; it records capturedSourceEvaluated=false, gameFunctionsCalled=false, gettersInvoked=false, cdpConnected=false, and gameInputSent=false.
- Scope boundary: this validates decoder mapping for the factory's statically discovered aliases and numeric literal call arguments. It does not establish runtime behavior or complete deobfuscation of the rest of main.js. No game code or input was executed.
- This result is saved locally. GitHub push and authoritative remote readback remain pending explicit remote-write authorization.


## 2026-10-29 — Shared wrapper decoder call-site cross-check (offline)

- Rechecked the preserved snapshot SHA-256 (`073df7c8e4d4680208070b22806273b332e2f5ba7ae8b80005ad5914f336561e`) and source length (8,495,851 characters).
- A whole-source lexical text scan found 10 numeric-literal calls to the wrapper-local `_0x460358` alias across 9 unique indices. With the verified 323-step table rotation and custom alphabet, the calls decode to TypeScript/ES-module helper names including `__createBinding`, `create`, `__setModuleDefault`, `__importStar`, `__awaiter`, `__importDefault`, `defineProperty`, and `__esModule`.
- Context confirms these calls belong to a wrapper/helper region around offsets 8,461,359–8,464,225, distinct from the `_0x466acb` application factory analysed previously. The adjacent `_0x3b2028(...)` calls are module-loader calls, not decoder calls, and were not misclassified as decoder evidence.
- JavaScript extractor regression tests were rerun: **9/9 test cases passed**. The broader CI run previously passed 13 JavaScript tests across two files and 122 Python unit tests.
- Scope: the global scan only counted direct numeric-literal calls to `_0x460358` and `_0x3353`; it does not claim to enumerate all aliases or decode every module. Analysis remained static; captured source was not executed and no game interaction occurred.


- Follow-up aggregation of the saved factory decode report confirmed the 33 call sites map to 24 unique decoded names. Repeated names include `default` (3), `renderer` (3), `length` (3), `settings` (2), `split` (2), and `view` (2). This gives a compact map of the application factory's statically referenced configuration, rendering, DOM, and startup names without publishing source snippets.


## 2026-10-29 — Declaration-trivia compatibility extension

- Extended the production assignment matcher to accept whitespace, block comments, and line comments between declaration keywords/delimiters and the target identifier.
- Expanded the production regression test with a commented comma-separated arrow declarator and a following declaration after a line comment.
- Validation: JavaScript tests **13/13 passed**; Python compilation passed; Python unit tests **122/122 passed** with both `src` and repository root on `PYTHONPATH`; `git diff --check` passed.
- Re-ran the production extractor against the real snapshot after the change: SHA-256 unchanged, `_0x466acb` boundaries remain **8,464,467–8,465,759**, length **1,292**, following comma excluded.
- This compatibility extension is verified in the local clone only; no GitHub push has been performed.


## 2026-10-29 — Whole-source decoder inventory (offline)

- Added local-only analyzer `offline_global_decoder_scan_20261029.js` and JSON output `offline_global_decoder_report_20261029.json` in the snapshot directory; neither is added to the public repository. The current script references the Esprima package already installed in this Windows user's npm cache, so that dependency path must be adjusted if reused on another machine.
- The analyzer revalidated the expected SHA-256, 14,271-entry string table, and 323-step rotation. Esprima tokenization (without evaluating or parsing the captured source) excludes quoted strings and comments from call-site matching. It identified **2,273** direct decoder aliases, **2,270** aliases with numeric-literal calls, and decoded **22,196/22,196** numeric-literal call sites with **0 invalid indices**. The rotation call is at source offset **318,806**: the first **12** decoder calls before it use the original table order, while the following **22,184** use the rotated table. The analyzer selects the table state by each call's source offset rather than applying the final rotation to earlier calls.
- The decoded inventory contains **6,748 unique string values** after accounting for pre-rotation calls, and summarizes call density across 17 contiguous source regions of 500,000 characters each (final region shorter). Per-region summaries also track unique decoded names and aliases; for example, the first 500,000 characters contain 1,097 calls / 338 unique values, while the final region contains 1,505 calls / 705 unique values. The report was written and read back as valid JSON (about 3.18 MB).
- Frequently repeated decoded names include `defineProperty`, `__esModule`, `dispose`, `Container`, `initialize`, `__importDefault`, `update`, `TaskBase`, `activate`, and `deactivate`; these are aggregate counts, not a claim that every decoded value is a property name.
- Added token-balanced numeric-keyed module-factory ranges to the same local report: **2,199** non-overlapping factory bodies were found; **2,198** contain at least one decoded call. Of 22,196 decoded calls, **22,159** map into a detected factory and 37 are outside these factory ranges. Per-module summaries record source boundaries, decoder aliases, unique names, and frequent decoded values; the report is about 3.17 MB. These are structural boundaries, not proof that every candidate corresponds to a semantically understood application module.
- Safety scope remains static text only: the report marks captured source evaluation, game-function/getter invocation, CDP connection, and game input as false. This is a broad decoder inventory, not full semantic reconstruction of each module or proof of runtime behavior.


## 2026-10-29 — Startup-tail module map (offline)

- The token-balanced module report was expanded to retain every decoded value for module factories at offsets >= 8,400,000, while keeping only frequency summaries for earlier modules. This preserves a focused, local-only view of the startup tail without duplicating all source text.
- Static module identifiers and decoded names in the tail include: `0x2bd6` (`width`, `height`, `_width`, `_height`); `0xdd98` (`_getInstance`, `view`, `settings`, `option`, `model`, `resources`, `scene`, `sound`, `friendlyRequest`); `0x806b` (ticker/FPS/timing and wheel-event names); and `0x3c0b` (font-loading identifiers such as `FontFace`, `fontdeck`, `typekit`, `fontloading`, and `userAgent`).
- The final module `0x59ba` contains a decoded JSON-shaped resource/voice manifest string with numeric arrays. Only its structural type and key names are noted here; the full string remains in the local report and is not copied into the repository progress log.
- These are static decoded identifiers and source boundaries, not runtime execution evidence or a claim that all module behavior has been reconstructed.


## 2026-10-29 — Domain-module decoded identifier maps (offline)

- Extended the local global report to retain full decoded identifier lists for selected domain modules, in addition to the startup tail.
- Module `0x12fc7` (offsets 1,035,173–1,051,474) has 96 decoder calls / 94 unique names and exposes the `ShipModel` / `ShipModelEdit` model surface, including `__updateAmmo__`, `__updateFuel__`, `__updateNowHp__`, `getRepairTime`, `getSlotitems`, `getSlotitemTousai`, `hpNow`, `hpMax`, `fuelNow`, `ammoNow`, and `isDamaged`.
- Module `0xd056` (offsets 1,115,980–1,130,914) has 60 calls / 58 unique names and exposes `ShipModelReplica` identifiers including `causeDamage`, `initializeHPInfo`, `initializeSlots`, `is_enemy_break`, `hp_now`, `hp_max`, `updateHpNow`, `useRation`, `useRepairItem`, and `useReplenishment`.
- Module `0xd225` contains 148 calls / 80 unique names, with `EquipType` appearing 67 times and additional equipment filtering/rarity constants. Module `0xb705` contains 64 calls / 62 unique names and exposes `GameData` / `HighScore` plus game-stage, wave, score, and timer lifecycle method names.
- This is identifier-level mapping only; it does not execute the game or prove when these methods run. Full decoded lists remain local in the JSON report.
## 2026-10-29 - Static syntax-context cross-check for ship model modules (offline)

- Added a separate local-only analyzer `offline_decoder_context_scan_20261029.js` and result `offline_decoder_context_report_20261029.json` under the preserved snapshot directory. The report is intentionally not added to the public repository because it contains a large decoded-name inventory; the captured source and full decoded values remain local.
- The analyzer reuses the existing token-only decoding pipeline and the SHA-256-pinned snapshot (`073df7c8e4d4680208070b22806273b332e2f5ba7ae8b80005ad5914f336561e`). It adds bounded token-neighbor classifications for calls in modules `0x12fc7` and `0xd056`; it does not parse/evaluate the captured source or invoke game code.
- Static syntax-context counts for 156 decoded call sites: 80 computed getter definitions, 1 computed setter definition, 32 other computed method definitions, 33 computed-member method-call forms, 6 computed-member writes, 2 computed-member read/key forms, and 2 argument/sequence forms. Examples include getter declarations for `hpMax`, `hpNow`, `fuelNow`, and `ammoNow`; these are syntax-position findings, not proof of runtime access or value semantics.
- Repeat-run validation produced an identical report SHA-256 (`2C7005CED053061A49BB8E674B4A039ED3F541442FE592E502D2B3280B79F162`). The report retained `status=PASS`, 2,602,708 tokens, 22,196 decoded calls, zero invalid indices, and the expected snapshot SHA-256.
- Existing regression verification was rerun after the analysis: JavaScript extractor tests **9/9 passed**, Python unit tests **122/122 passed**, and `git diff --check` passed. The new contextual labels are token-neighbor heuristics rather than AST-backed semantic classifications and must be treated as exploratory evidence.
- Safety boundary: no captured source evaluation, game-function/getter invocation, CDP connection, or game input occurred. The result supports further static inspection but does not establish runtime behavior or certify a read-only API surface.
## 2026-10-29 - Cross-module syntax-context inventory (offline)

- Extended the local-only contextual scan across four token-balanced module ranges: `0xd225` (equipment-type/filtering names), `0x12fc7` (`ShipModel` / `ShipModelEdit`), `0xd056` (`ShipModelReplica`), and `0xb705` (`GameData` / stage lifecycle names). Source ranges are `[511906, 516260)`, `[1035173, 1051474)`, `[1115980, 1130914)`, and `[8310559, 8334285)` respectively.
- The scan still validates snapshot SHA-256 `073df7c8e4d4680208070b22806273b332e2f5ba7ae8b80005ad5914f336561e`, tokenizes 2,602,708 tokens, decodes 22,196 numeric-literal calls with zero invalid indices, and evaluates none of the captured source. Contextual classification is bounded token-neighbor analysis, not an AST or runtime trace.
- Per-module syntax-context counts: `0xd225` has 135 computed-member/read-or-key forms, 6 computed-member writes, 1 computed-member method-call form, and 6 argument/sequence forms; `0x12fc7` has 55 getter definitions, 17 computed-method definitions, 18 computed-member method-call forms, 4 writes, 1 read/key form, and 1 argument/sequence form; `0xd056` has 25 getter definitions, 1 setter definition, 15 computed-method definitions, 15 computed-member method-call forms, 2 writes, 1 read/key form, and 1 argument/sequence form; `0xb705` has 46 computed-method definitions, 2 getter definitions, 1 setter definition, 10 computed-member method-call forms, 3 writes, 1 read/key form, and 1 argument/sequence form.
- Offset-level examples independently show `hpMax` and `hpNow` as computed getter definitions in `0x12fc7` (offsets 1,036,518 and 1,036,654), and `hp_init` / `hp_now` as getter definitions in `0xd056` (offsets 1,118,693 and 1,118,776). `0xd225` repeatedly uses `EquipType` in computed-member positions; `0xb705` contains method definitions named `initialize`, `_phaseCreateGameData`, `_phaseSetCallback`, and `_phaseInitGame`. These establish syntax position and identifier placement only, not when methods execute or what values mean.
- The expanded report was rerun twice with identical SHA-256 `04D2DBD3C1D7D4974FC44E9BD3BAB3A54FD179BD8F6E2D92409C41B067D97533
## 2026-10-29 - Ship model getter body structure (offline)

- Extended the local-only token report to match computed getter declarations to balanced body ranges and collect decoded call sites plus identifier-token references without printing or storing source text excerpts. The report now records **82** getter body ranges across the four selected modules.
- In `0x12fc7`, getter bodies for `hpMax`, `hpNow`, `fuelNow`, and `ammoNow` have verified ranges: `hpMax` `[1036537, 1036650)`, `hpNow` `[1036674, 1036787)`, `fuelNow` `[1041926, 1042039)`, and `ammoNow` `[1042063, 1042172)`. Their token shapes use a repeated local alias/helper-call pattern and computed property access; the backing fields and helper semantics remain unresolved at this stage.
- In `0xd056`, `hp_init`, `hp_now`, and `hp_max` getter bodies return computed `this[...]` property forms, with ranges `[1118713, 1118772)`, `[1118796, 1118855)`, and `[1118879, 1118938)`. The local report confirms no direct numeric-literal decoder call sites inside these selected getter bodies under the current alias inventory; this is a coverage boundary, not evidence that no decoding or indirection is involved.
- The expanded report was repeated with identical output hash `35c2208276c469c987777794cbb987184f248d13ffc738e34a23a6b32e855a9e`; snapshot hash, 2,602,708-token count, 22,196 successful direct numeric-literal decodes, and zero invalid indices remain unchanged. JavaScript tests passed **9/9** and Python tests **122/122**.
- Next static step is to trace the local helper/alias definitions and obfuscated backing-field references using token ranges and balanced scopes, rather than guessing getter values or invoking the getters. No captured source execution, getter invocation, CDP connection, or game input occurred.
## 2026-10-29 - Scoped decoder-alias propagation resolves ship getter fields (offline)

- Improved the local context analyzer to follow identifier aliases of the known decoder only inside the four explicitly bounded target modules. This is deliberately narrower than applying scope-insensitive alias propagation to the entire file; alias shadowing and arbitrary reassignment remain limitations outside these selected scopes.
- With scoped alias propagation, the report decodes **23,531** numeric-literal call sites with zero invalid indices: the previously validated **22,196** direct-alias baseline plus **1,335** additional local-alias call sites in the selected module ranges. The report has 2,493 aliases in this combined, mixed-scope analysis and must not be interpreted as a new whole-file alias census.
- Getter-body decoding now confirms the following identifier mappings by exact token offsets: `ShipModelEdit.hpMax` at 1,036,518 uses `ObjUtil`, `getNumber`, and `api_maxhp`; `hpNow` at 1,036,654 uses `ObjUtil`, `getNumber`, and `api_nowhp`; `fuelNow` at 1,041,906 uses `ObjUtil`, `getNumber`, and `api_fuel`; `ammoNow` at 1,042,043 uses `ObjUtil`, `getNumber`, and `api_bull`. The corresponding `ShipModelReplica` getters at offsets 1,118,693 / 1,118,776 / 1,118,859 map to `_hp_init` / `_hp_now` / `_hp_max` respectively.
- The report validates the pinned source SHA-256, retains 2,602,708 tokens, and passed deterministic repeat-run comparison (SHA-256 `3f43ae2b7c839de2f731b09845a9f505762e5444a180e8ddf4bcc0dd7b579acb`). Regression tests: JavaScript **9/9**, Python **122/122**. These are static token/decode results, not getter invocations or runtime observations.
- This provides a concrete next path for read-only feasibility analysis: compare the decoded model getters with known data fields and update methods, then identify the actual object/data source without calling those methods. No captured source execution, getter invocation, CDP connection, or game input occurred.
## 2026-10-29 - Static method-body cross-reference for model updates and lifecycle (offline)

- Extended token-balanced method extraction to **118** computed method bodies across the selected `ShipModel`, `ShipModelReplica`, equipment, and `GameData` module ranges. The local report retains method source offsets and decoded identifiers referenced within each body; it does not contain copied source snippets.
- In `ShipModelEdit` (`0x12fc7`), `__updateAmmo__` at offset 1,050,483 references `api_bull`; `__updateFuel__` at 1,050,581 references `api_fuel`; `__updateNowHp__` at 1,051,083 references `api_nowhp`. These names match the corresponding getter fields discovered in the same module. This is a static cross-reference, not proof of when updates occur.
- In `ShipModelReplica` (`0xd056`), `initializeHPInfo` at 1,121,802 references `_hp_init`, `_hp_now`, `_hp_max`, `_practice`, `_invisible`, and damage-type identifiers. `causeDamage` at 1,124,293 and `updateHpNow` at 1,124,655 both reference `_hp_now`, `_hp_max`, `_practice`, and damage-type fields, with `Math.floor`/`Math.max`-style decoded names present. The source ranges establish an identifiable HP initialization/update cluster for further offline review; no method was invoked.
- Other statically traced methods include `useRation` (offset 1,126,829), `useRepairItem` (1,127,735), and the `0xb705` lifecycle methods `initialize` (8,317,423), `_phaseCreateGameData` (8,317,678), and `_phaseInitGame` (8,318,191). Their body references link to local fields/helpers and view/container names; this does not authorize or imply calling mutation-capable methods.
- Scoped decoder-alias propagation remains limited to the four named module ranges. The report validates the pinned snapshot hash, 2,602,708 tokens, 23,531 decoded numeric-literal calls, zero invalid indices, and deterministic repeat-run hash `0c57085fd35435d56628516bffdfe321666176a858871ef6d3e4941672587d72`. JavaScript regression tests **9/9** and Python tests **122/122** passed.
- Safety boundary remains unchanged: no captured source execution, getter/method invocation, CDP connection, or game input. The findings are static evidence for prioritizing further read-only feasibility analysis, not runtime behavior verification.
## 2026-10-29 - Ship model constructor field inventory (offline)

- Added token-balanced constructor-body summaries to the local report. Four constructor bodies were identified in the selected `ShipModel`, `ShipModelReplica`, and lifecycle-related ranges; summaries contain decoded call names and bounded identifier inventories, not copied source code.
- The `ShipModelReplica` constructor begins at offset **1,121,145**, with body range `[1,121,232, 1,121,801)`. Scoped alias decoding identifies initialization references for `_karyoku`, `_raisou`, `_taiku`, `_soukou`, `_hp_init`, `_hp_now`, `_hp_max`, `_taihi`, `_invisible`, `_type`, `_practice`, `_index`, `_mst_id`, `_mem_id`, `_level`, `_speed`, `_slots`, and `_enemy_break`.
- Combined with the verified getters and update methods, the replica's HP state has a traceable static chain: constructor initialization references `_hp_init` / `_hp_now` / `_hp_max`; getters expose those fields; `initializeHPInfo`, `causeDamage`, and `updateHpNow` reference the same fields. This is a source-structure cross-reference, not runtime state validation.
- The expanded report repeated deterministically with SHA-256 `6a9b683fc6cf9ab825942d4c7c38167a431da53a71226ef5d085e55529fc0067`, retaining snapshot hash `073df7c8e4d4680208070b22806273b332e2f5ba7ae8b80005ad5914f336561e`, 23,531 scoped/direct decoded calls, and zero invalid indices. Tests passed: JavaScript **9/9**, Python **122/122**.
- Safety remains static-only: captured code was not executed; constructors, getters, and methods were not invoked; no CDP connection or game input occurred. The next useful step is to map how these model instances are populated from existing data sources and determine whether a supported passive read path can expose them without calling mutation-capable methods.
## 2026-10-29 - Raw ship-data ingestion path identified (offline)

- Expanded the scoped context analyzer to include `ShipModelHolder` module `0x14e15` (source range `[1,051,475, 1,055,138)`) and its adjacent `ObjUtil` export module `0xb462` (`[1,217,929, 1,219,962)`). The combined local report now has **126** token-balanced computed method bodies and six selected module ranges.
- Static token evidence identifies the `ShipModelEdit` constructor at offset **1,044,904** assigning its argument to `this['_o']`. Its `hpMax`, `hpNow`, `fuelNow`, and `ammoNow` getters pass that `_o` object to decoded `ObjUtil.getNumber` calls using API keys `api_maxhp`, `api_nowhp`, `api_fuel`, and `api_bull`, respectively. This confirms a raw-data wrapper shape without invoking the constructor or getters.
- `ShipModelHolder.setData` at offset **1,052,154** iterates an input collection, constructs `ShipModelEdit` for each item, obtains `memID`, and stores each model in `_map` keyed by the stringified ID. `updateData` at offset **1,054,760** constructs a new `ShipModelEdit`, removes any existing entry for its `memID`, then stores the replacement. `get` at **1,052,569** and `getAll` at **1,053,562** expose map lookup/collection patterns at the source-structure level.
- This provides a concrete static data path: input item → `ShipModelEdit._o` → `ObjUtil.getNumber(_o, API field)`; holder methods index model instances by `memID`. The caller that supplies the initial collection to `setData` is not yet established, so this does not yet prove a complete upstream API response path or a safe passive read endpoint.
- Repeat-run validation retained source SHA-256 `073df7c8e4d4680208070b22806273b332e2f5ba7ae8b80005ad5914f336561e`, **2,602,708** tokens, **23,606** scoped/direct decoded numeric-literal calls, zero invalid indices, and deterministic report hash `9e3748d652bdd3c3f17d63fb4f5ebc8cf90216e399cbfb1530ab10464dc80604`. JavaScript tests **9/9** and Python tests **122/122** passed.
- No captured source execution, constructor/getter/method invocation, CDP connection, or game input occurred. Next step: trace `ShipModelHolder.setData`/`updateData` callers and the source of the incoming ship collection using module-scoped alias evidence, then compare with already available passive interfaces.
## 2026-10-29 - API response handler linked to ship model update path (offline)

- Added the `PowerUpAPI`-related module `0xf293` (source range `[2,436,811, 2,439,687)`) to scoped alias decoding and method-body summaries. The local report now covers seven selected module ranges and **128** balanced computed method bodies.
- Its `_completedEnd` method at offset **2,438,286** statically references `_raw_data`, `api_unset_list`, `api_type3No`, `api_slot_list`, `api_ship`, `ShipModelEdit`, `model.ship.get`, `memID`, `__update__`, `model.deck.setData`, and `api_deck`. This links an API-response handler to retrieval and update of a ship model object, while the deck update is a separate path. The source does not establish that this handler supplies the initial `ShipModelHolder.setData` collection.
- The handler's surrounding constructor/getter shapes also expose `_url` with route `api_req_kaisou/powerup` and raw response accessors for `api_powerup_flag`, `api_ship`, and `api_deck`. These are static decoded names and ranges, not evidence that the endpoint was called during this analysis.
- Repeat-run validation passed with source SHA-256 `073df7c8e4d4680208070b22806273b332e2f5ba7ae8b80005ad5914f336561e`, **2,602,708** tokens, **23,677** direct/scoped decoder calls, zero invalid indices, and deterministic report hash `972504453e6e8b018f27b15bc5af794fa338b7fff1bd009e4f1802795d73a470`. JavaScript tests **9/9** and Python tests **122/122** passed.
- Evidence now supports a partial static update chain: raw API response fields → `ShipModelEdit` wrapper / `memID` lookup → existing ship model `__update__`. The upstream source for initial ship-map population and any approved passive read path remain unverified. No source evaluation, API request, game function/getter invocation, CDP connection, or game input occurred.
## 2026-10-29 - Initial port payload and incremental ship updates linked (offline)

- Extended the offline analyzer with per-module alias closure and cross-module call-site indexing. Alias propagation is bounded to each balanced webpack-style module factory; the updated local report covers **10 selected module ranges**, **134** token-balanced method bodies, **5,935** cross-module identifier sites, and **104** direct-alias target sites. This index is for static evidence discovery, not call-graph/runtime proof.
- The `PortAPI` module `0x1321f` constructor at offset **466,438** sets `_url` to `api_port/port`. Its `_completedEnd` body `[466,915, 470,255)` statically links `_raw_data.api_ship` through `ObjUtil.getObjectArray` to `model.ship.setData` (decoder-key offsets **468,467–468,557**). In the same handler, `api_deck_port` feeds `model.deck.setData`, while material, ndock, log, and other port data are handled by separate model setters. This is the first direct static evidence for the initial ship-holder population path.
- `ModelManager` module `0xb6f0` constructor body `[565,778, 567,076)` instantiates `ShipModelHolder` into its `_ship` field at offset **566,266**; the `ship` getter at **564,616** returns that field. Together, these references link the port response's `model.ship.setData` target to the manager-owned ship holder.
- A second, incremental path is identified in `GetShipAPI` module `0xa85b`: constructor offset **3,780,796** sets route `api_req_kousyou/getship`; `_completedEnd` body `[3,781,092, 3,781,680)` reads `_raw_data.api_ship` and passes it to `model.ship.updateData` (offset **3,781,308**), then separately processes slot items and k-dock data. This complements the port-time initial population with a targeted ship update path.
- The local report repeat-run hash is `f79e6db0b25e098f3e34e387185d81f902f41f897c3c1a018b9ed1793fe364fa`; source SHA-256 remains `073df7c8e4d4680208070b22806273b332e2f5ba7ae8b80005ad5914f336561e`; **2,602,708** tokens; **23,945** decoded calls; zero invalid indices. JavaScript tests **9/9** and Python tests **122/122** passed.
- Static data-flow conclusion: port response `api_ship` array → `model.ship.setData` → `ShipModelHolder` → `ShipModelEdit` wrappers; subsequent `api_req_kousyou/getship` responses use `model.ship.updateData`. This identifies both source-level ingestion routes but does not prove their live timing, current contents, or an externally supported read-only API. No source evaluation, endpoint request, getter/method invocation, CDP connection, or game input occurred.
- Next: map which model getters are plain field projections versus computed or side-effect-capable accessors, and compare safe passive extraction options against the already available Poi/MCP read-only endpoints before proposing any integration.
## 2026-10-29 - Getter safety classification for ship-state extraction (offline)

- Classified **55** getters in `ShipModelEdit` (`0x12fc7`) and **25** in `ShipModelReplica` (`0xd056`) using balanced method-body ranges and decoded identifier references. This is a static classification only; no getter was invoked.
- `ShipModelReplica` has a clear backing-field-only group, including `type`, `index`, `mst_id`, `mem_id`, `level`, `karyoku`, `raisou`, `taiku`, `soukou`, `hp_init`, `hp_now`, `hp_max`, `slots`, `slot_ex`, and `damageType_before`. Their getter bodies reference a single corresponding private field in the decoded inventory (for example, `hp_now` → `_hp_now`). These are the strongest candidates for a future read-only extraction design, but direct invocation has not been tested or authorized by this static analysis.
- Computed/dependent getters are distinguishable: `ShipModelReplica.damageType` references `ShipUtil.getDamageType` and HP fields; `name`, `stype`, `stype_name`, `yomi`, and `slotNum` traverse `model.ship.getMst`; `ShipModelEdit.name`/`yomi` similarly use master-data lookup, while HP/fuel/ammo getters reference `ObjUtil.getNumber` and API fields. Do not assume these helper calls are pure solely from their names.
- `ShipModelEdit` exposes many more API-field helper getters (numeric/array accessors and derived values such as speed type, slot capacity, and equipment carry). The source inventory therefore supports a conservative separation: raw replica backing fields are lower-complexity candidates; master-data/helper/computed getters need further source-level purity review; mutation methods and update methods remain out of scope for passive reads.
- Validation remains pinned to snapshot SHA-256 `073df7c8e4d4680208070b22806273b332e2f5ba7ae8b80005ad5914f336561e`; latest deterministic report hash `f79e6db0b25e098f3e34e387185d81f902f41f897c3c1a018b9ed1793fe364fa`; **2,602,708** tokens, **23,945** decoded calls, zero invalid indices. JavaScript tests **9/9** and Python tests **122/122** passed.
- Next: inspect the implementation bodies of the small set of helper functions used by candidate getters, using token-balanced source ranges only, and compare these findings with the existing Poi MCP read-only tool surface. No source execution, getter/method invocation, CDP connection, or game input occurred.
## 2026-10-29 - Utility getter helpers reviewed for passive-read suitability (offline)

- Extended the static range analyzer to inspect the exported helper bodies behind `ObjUtil` and `ShipUtil`. `ObjUtil.getNumber` body `[1,218,234, 1,218,329)`, `getObjectArray` `[1,218,649, 1,218,779)`, `getNumArray` `[1,218,852, 1,218,982)`, `getObject` `[1,219,260, 1,219,435)`, and `_getProp` `[1,219,705, 1,219,902)` are now bounded by matching tokens and cross-referenced with decoded helper names.
- In these five bodies, the decoded call relationships are limited to `_getProp` or `hasOwnProperty`; no loops or explicit member-write patterns were found in the token ranges. The wrappers perform local fallback/default handling and property lookup. This is encouraging for reading plain JSON-derived API objects, but not a formal purity proof: a property read can still invoke an accessor if the input object is not a plain data object.
- `ShipUtil.getSpeedType` body `[1,221,258, 1,221,343)` has no decoded calls, loop, or member-write pattern and is a small comparison/threshold-style computation. `getDamageType` is aliased to a local helper function rather than declared as a named exported function; its implementation still needs an explicit body-range cross-reference before it can be classified with the same confidence.
- Latest deterministic report hash: `51887a27286398c675c00ee3efa345b3b10813712b1f331017263e57a07f50f4`; source SHA-256 unchanged; **2,602,708** tokens, **24,177** decoded calls, zero invalid indices; **11** selected module ranges, **134** balanced method bodies. Regression tests passed: JavaScript **9/9**, Python **122/122**.
- Provisional extraction recommendation: prefer already materialized plain data fields or simple backing-field accessors; treat `ObjUtil` wrappers as plausible low-complexity reads only when the object origin is known to be parsed API data; defer master-data traversal and `ShipUtil.getDamageType` until the remaining helper is statically bounded. No helper or game method was invoked, and no source was executed.
## 2026-10-29 - Ship damage classification helper body bounded (offline)

- Followed `ShipUtil.getDamageType`'s export alias to its local function `_0x2fb799` in module `0x8b5`. The function begins at offset **1,220,432**, with body range `[1,220,462, 1,220,597)`. Its body computes a percentage ratio from two arguments and maps it through fixed threshold branches; the bounded token range contains no loop or member-write pattern.
- This resolves the earlier alias ambiguity: `ShipModelReplica.damageType` references a small local calculation helper through `ShipUtil.getDamageType`, while its inputs are `_hp_now` and `_hp_max`. It is a plausible pure derived value when called with ordinary numeric values, but the analysis still did not invoke it or test edge cases at runtime.
- The latest analyzer report was repeated deterministically with SHA-256 `51887a27286398c675c00ee3efa345b3b10813712b1f331017263e57a07f50f4`; **24,177** decoded calls, zero invalid indices. Regression tests remain **9/9 JavaScript** and **122/122 Python** passed.
- Read-path classification is now more complete: raw replica backing fields are the lowest-complexity candidates; `ObjUtil` wrappers are bounded property/default helpers; `getDamageType` is a bounded threshold calculation; master-data-dependent getters remain a separate category. This remains static evidence only, not permission to call these functions from a live game context.

## 2026-10-29 - Port API request setup and response handler cross-reference (offline)

- Continued source-only analysis of the `PortAPI` module `0x1321f`. Its constructor body `[466,465, 466,575)` assigns the route `api_port/port` to `_url`. Its bounded `_connect` body `[466,595, 466,894)` references request parameters `api_sort_key`, `spi_sort_order`, and `api_port`, constructs a key through `_createKey`, and reads `model.basic.member_id` before its recursive/continuation reference to `_connect`.
- The `_completedEnd` body `[466,915, 470,255)` contains the response-side processing already identified: `_raw_data.api_ship` is passed through `ObjUtil.getObjectArray` to `model.ship.setData`; `api_deck_port` separately feeds `model.deck.setData`. This joins request configuration and response-to-model population at the source-structure level.
- Cross-reference remains incomplete at the network transport/framework boundary: this token-context report does not yet establish which caller instantiates/connects `PortAPI`, how the base `_post_data` method reaches the transport, or the exact response-dispatch call chain. A search for numeric module IDs alone is not evidence that no upstream caller exists.
- The static report currently validates source SHA-256 `073df7c8e4d4680208070b22806273b332e2f5ba7ae8b80005ad5914f336561e`, `2,602,708` tokens, `24,177` decoded calls, and zero invalid indices. These are analyzer-report values, not a new regression-test run in this step.
- Safety remains unchanged: no captured source execution, network request, getter/method invocation, CDP connection, or game input occurred. Next, trace the base API transport and module registration/instantiation references using static token ranges, then assess whether any supported passive read route exists without calling mutation-capable code.

## 2026-10-29 - API connector task-queue lifecycle bounded (offline)

- Extended the local context analyzer to include the `APIConnector` module `0x9faf`, source range `[451,372, 453,053)`. Its constructor initializes a `_list` field; `add` appends tasks; `_start` enters `_connect`; and the bounded `_connect`, `_complete`, `_failed`, and `_failedEnd` bodies reference queue progression, task callbacks, and view/loading/error handling. This is evidence of a serialized task-queue lifecycle, not yet proof that this class is the network transport used by `PortAPI`.
- The report now includes 13 selected module ranges and 140 balanced method bodies. It retains the pinned snapshot SHA-256 `073df7c8e4d4680208070b22806273b332e2f5ba7ae8b80005ad5914f336561e`, `2,602,708` tokens, `24,211` decoded calls, and zero invalid indices. The report was rerun twice with identical SHA-256 `71b3dea49aff01e92c32886409d47a51d5409342144d40125ee9c6dedb73e281`.
- This narrows the lifecycle question: `PortAPI._connect` assembles request parameters, while a nearby `APIConnector` module exposes a queue/callback lifecycle; the static evidence does not yet establish the inheritance/import mapping or how the queued task reaches the actual HTTP/fetch transport. Further work should follow the base-class import and the `FetchUtil` implementation before concluding anything about transport or passive-read safety.
- No captured source was executed; no API request, getter/method invocation, CDP connection, or game input occurred. This analyzer expansion did not rerun the separate JavaScript/Python regression suites.

## 2026-10-29 - API transport and response parsing path identified (offline)

- Followed the static module-import reference from `PortAPI` (`0x1321f`) and related API subclasses to module `0x957d`, whose bounded methods expose the shared `APIBase` lifecycle. Its `_connect` body `[487,442, 488,072)` references an HTTP `post` call, URL construction, `stringify`, `_post_data`, promise `then`/ `catch`, and the `_success` / `_failed` handlers.
- The response path is statically traceable through `_success` `[488,232, 488,379)` into `_parse` `[488,408, 488,870)`. The latter references JSON parsing, `api_result`, `api_result_msg`, `api_data`, storage into `_raw_data`, and task completion. This supplies the missing shared transport/response-processing layer for the earlier `PortAPI` source chain, although it does not prove live timing or the exact external caller sequence.
- `_isRetryableUrl` `[488,093, 488,203)` contains a retryable-route regular expression that explicitly includes `api_port/port`. The base class also contains retry/timer logic. Therefore, using this API class is an active request path with retries and UI/task lifecycle effects—not a passive read interface that should be invoked merely to inspect state.
- The analyzer now covers 14 selected module ranges and 140 balanced method bodies. The report retains source SHA-256 `073df7c8e4d4680208070b22806273b332e2f5ba7ae8b80005ad5914f336561e`, `2,602,708` tokens, `24,317` decoded calls, and zero invalid indices. Two consecutive report runs produced identical SHA-256 `083c4737516af81286faa453704974c60e6b305488ee169e176e7905a358ce8e`.
- Safety remains static-only: no source execution, HTTP request, getter/method invocation, CDP connection, or game input occurred. The network transport is now identified, but a supported passive read path is still unverified; next compare existing Poi read-only interfaces and trace whether model snapshots can be observed without invoking API request or mutation-capable code.

## 2026-10-29 - Port API startup and task registration chain traced (offline)

- Followed the direct static module-require edge to `PreInitializeTask` module `0x183d8`. Its `_connectAPI` body `[1,604,475, 1,605,102)` references construction of `APIConnector`, adds `PortAPI` and several other API tasks to the connector, calls the connector's `start`, and then continues to `_loadPortResources`.
- The same module's `_start` body `[1,602,668, 1,604,454)` reaches `_connectAPI` after texture/resource cleanup references. This provides a static startup/lifecycle chain: `PreInitializeTask._start` → `_connectAPI` → register `PortAPI` in `APIConnector` → start the connector → continue loading port resources. This is code-structure evidence only; it does not prove runtime ordering for a particular session.
- Combined with the previously bounded shared API base, the static route is now connected across layers: startup task registers `PortAPI`; `PortAPI` configures `api_port/port` and its request fields; shared `APIBase._connect` uses an HTTP `post`/promise path; response parsing stores `_raw_data`; and `PortAPI._completedEnd` maps `api_ship` to `model.ship.setData` in the manager-owned ship holder.
- The local analyzer now covers 15 selected module ranges and 162 balanced method bodies. The pinned source SHA-256 remains `073df7c8e4d4680208070b22806273b332e2f5ba7ae8b80005ad5914f336561e`; the report contains `2,602,708` tokens, `24,624` decoded calls, and zero invalid indices. Repeat-run hash is deterministic at `6ad058d8caf4949825596bf7d0e0e1b033dadb2d48b12103dcefd31abeafca15`.
- This establishes the static startup-to-model-population path, but not a passive live-read facility: the path registers and runs an active API task with transport, retry, queue, UI, and response-processing lifecycle. No source execution, HTTP request, getter/method invocation, CDP connection, or game input occurred. The next decision point is technical rather than user-blocking: compare this active path with the existing Poi read-only surface and determine whether passive state can be observed without starting or invoking the API task.

## 2026-10-29 - Hosted unsupported-runtime fail-closed verification

- Created isolated test branch `feat/harness-trial-gate-runtime-negative-20261029` from the verified Harness-trial head `887eb1ff23fd04207f52707fa7323f7bf93a2b0e`. The only test mutation was setting the workflow input `runtime` to `unsupported-runtime-negative-test`; it was not applied to the normal trial branch and no PR was opened.
- Exact-head hosted run [40821913417](https://github.com/wosafety0/wosafety0clamp/actions/runs/40821913417) completed with the expected `failure` conclusion on commit `ebacf6cad0ecdea1e6a2051068bf60864c585287`. The runtime validator emitted `runtime must be either python or node.` and exited **2**; Python/Node setup and the consumer test command were skipped.
- Failure diagnostics behaved as intended: report generation succeeded and recorded runtime validation as failed and the consumer command as not run; artifact `consumer-acceptance-report` uploaded successfully (artifact ID `12875055823`); report identity/status verification failed closed; the final gate exited **1** and listed `runtime,tests,verification`. The first failing step and final gate both identify the failure path in hosted logs.
- This verifies the pinned reusable workflow's unsupported-runtime path, report/artifact preservation, and final fail-closed behavior. The report remains deliberately redacted/status-oriented; the detailed reason is in the job log rather than copied into the report. No live model request, game action, deployment, merge, or branch-protection change occurred. The negative-test branch remains isolated.

## 2026-10-29 - Runtime failure diagnosis guide updated and exact-head push CI passed

- Updated `docs/CLAMP_HARNESS_TRIAL_USAGE.md` on trial branch `feat/harness-trial-gate-20261029` at exact head `cc9374c261f918aa4c8f3ce41174cd763af9b7ef`. The guide now records the hosted unsupported-runtime negative-path evidence and explicitly distinguishes a skipped consumer test command from a test suite that actually ran and failed.
- Exact-head Push CI [40822018899](https://github.com/wosafety0/wosafety0clamp/actions/runs/40822018899) passed: runtime validation, consumer acceptance command, report generation, artifact upload, report identity verification, and final gate all succeeded. The Node setup step was skipped as expected for the configured Python runtime. The API currently returns no separate PR-context run for this exact head, so only Push CI is counted here.
- Remote branch readback confirms `feat/harness-trial-gate-20261029` points to `cc9374c261f918aa4c8f3ce41174cd763af9b7ef`. PR #14 remains open, Draft, and unmerged. The intentionally failing runtime probe remains isolated on `feat/harness-trial-gate-runtime-negative-20261029` at `ebacf6cad0ecdea1e6a2051068bf60864c585287`; its expected failure is not counted as normal acceptance.
- No merge, deployment, branch-protection change, live model request, or game operation occurred.


## 2026-10-29 - Reject globally duplicated fleet state

- Static review of `normalize_poi_fleets` found that individual fleet records were validated, but duplicate fleet IDs and the same ship instance appearing in multiple fleets were not rejected at the normalization boundary.
- Added fail-closed checks and regression tests on isolated branch `dev/expedition-state-global-uniqueness-20261029`: duplicate fleet IDs and cross-fleet duplicate ship assignments now raise `PoiStateError`.
- Exact-head GitHub Push CI passed on commit `554d045a3aa1b79d2bf673da6f89155606a94945`: [run 40827593526](https://github.com/wosafety0/wosafety0clamp/actions/runs/40827593526). Job steps report success for offline JavaScript tests, Python compilation, and Python unit tests; the log confirms **124 Python tests passed**.
- Remote readback confirmed the implementation and both new tests. Branch comparison against `dev/expedition-closed-loop-mvp` reports ahead by 2, behind by 0; this fix remains isolated and has not been merged. CI evidence validates the offline checks only, not real Poi/game runtime behavior.
- No live game operation, deployment, merge, or branch-protection change occurred.


## 2026-10-29 - Require complete Poi collection metadata

- Tightened the read-only Poi collection contract in `PoiExpeditionReader._collection_items`: a collection now must include a boolean `truncated` marker set to false, valid non-negative integer `total` and `returned` counts, and counts consistent with the complete `items` payload. Missing or contradictory completeness metadata is rejected rather than treated as a complete state.
- Added regression tests for a missing truncation marker and inconsistent returned count on isolated branch `dev/poi-collection-integrity-20261029`.
- Exact-head Push CI passed on commit `2994b23db63b1491803dc6f6bace64dd4f2d9073`: [run 40827710306](https://github.com/wosafety0/wosafety0clamp/actions/runs/40827710306). JavaScript static tests, Python compilation, and Python unit tests all completed successfully; the job log confirms **124 Python tests passed**.
- Remote readback confirmed the stricter collection validation and both regression tests. This verifies the offline contract only; it does not prove the installed Poi plugin's live payload shape or source freshness. No live game operation, deployment, merge, or branch-protection change occurred.


### 2026-10-29 Poi read consistency guard

- 只读审查发现：`PoiExpeditionReader.read_fleets()` 先后读取 `info.fleets`、`info.ships` 和引用到的主数据，然后用本地时钟生成 `observed_at`。Nanodesu 的 `poi_get` / `poi_lookup` 响应未提供可用于绑定同一 Redux 版本的源 revision token；因此本地观察时间只能表示读取时间，不能证明源状态新鲜。
- 在独立分支 `dev/poi-read-consistency-20261029` 增加最佳努力一致性门槛：主数据定向查询后重新读取完整的 `info.fleets` 与 `info.ships`；若两次返回的条目内容不一致，拒绝生成混合快照。对应源提交：`ef6fe18383d4c24222682c71412ab062bf270635`。
- 新增离线测试覆盖“读取期间状态变化时拒绝”与“状态稳定时完成二次读取”。测试提交：`42659218eeeae503947b40b17e8e80ae69763329`。
- GitHub Actions CI 对精确测试提交通过：[run 40828382725](https://github.com/wosafety0/wosafety0clamp/actions/runs/40828382725)。日志确认 JavaScript 静态测试、Python 编译成功，Python 单元测试 `Ran 126 tests ... OK`。
- 已从 GitHub 权威远程回读核对源文件 blob `9634d4b960ab71c5acd164353d5f7996dfa7e22e` 与测试文件 blob `a2494a0ded7b6497ae500834b804c1e3af5c02f8`。
- **限制：** 这不是原子快照协议；短暂变化后恢复原值（ABA）仍可能漏检。它也不证明数据新鲜或已登录，更不替代本机 Poi 运行时验证。真实派遣继续关闭；该分支未合并。


### 2026-10-29 Explicit source-freshness evidence gate

- 审查确认 `ObservedState.observed_at` 由本地读取时钟产生，只能描述观察时间；不能单独证明 Poi Redux 源数据何时刷新、是否来自当前有效游戏会话。
- 在独立分支 `dev/source-freshness-gate-20261029` 为 `ObservedState` 增加显式的 `source_freshness_verified` 标记，默认值为 `false`。安全前置使用的 `require_fresh()` 在时间年龄校验通过后，还必须要求该标记为真；否则以 `Source data freshness has not been verified` 拒绝。现有 `PoiExpeditionReader` 不会设置该标记，因此当前 Poi 读取结果默认无法通过安全前置门槛。
- 增加测试，验证即使本地观察时间只有 1 秒，只要缺少源新鲜度证据，前置检查与 runner 都会拒绝派遣；其他预检测试使用明确标记的合成可信夹具来覆盖后续逻辑。
- 精确提交 `611eebad14c4c48d4ffa6b164b3b8a46f71240e3` 的 GitHub Actions CI 通过：[run 40829028107](https://github.com/wosafety0/wosafety0clamp/actions/runs/40829028107)。日志确认 JavaScript 静态测试、Python 编译成功，Python 单元测试 **124 项通过**。
- 此门槛是保守的安全默认值，不是源新鲜度的实现或证明：当前真实 Poi 读取器没有已验证的源更新时间/版本令牌，所以真实状态仍会被拒绝用于安全敏感操作。未来只有在适配器能基于独立、可验证的源证据设置该标记时，才可允许通过。合成测试里的标记不代表真实 Poi 已通过验证。
- 远程代码和测试已回读核验；本次没有合并功能分支、没有执行真实游戏操作或部署。实机 payload、新鲜度与登录会话验证仍未完成。


### 2026-10-29 Poi cache and source timestamp audit

- 上游源码审查定位到一个此前未能排除的陈旧状态来源：Poi 的 `views/redux/create-store.ts` 将 `info` 纳入 `targetPaths`，启动时从本地 `localStorage` 的 `_storeCache` 读入初始 Redux state，并以该缓存作为 store 初始值。对应上游源码固定提交：`poooi/poi@725029997b84729df6394e1f107079b8e9437f36`。
- Nanodesu MCP 的 `/health` 中 `storeReady` 只验证 `getStore()` 返回对象且含有 `info` 属性，不表示本次会话已收到新鲜游戏响应。对应上游固定提交：`momocow/poi-plugin-nanodesu-mcp@2ba0a461ee44d64bf9d19b3467cf7565ad51b943`。因此 health OK / storeReady 不能作为源新鲜度证据。
- Poi 的舰队和舰船 reducer 会合并多个 API 响应（包括 port、deck、ship2、ship3 等），而响应 payload 虽有 `time`，当前只读 MCP `poi_get` 暴露的是归并后的状态，没有暴露能把整个 `info.fleets` 与 `info.ships` 快照绑定到同一源版本的令牌。现有实现无法据此证明两组数据在同一时点完整刷新。
- 在 `dev/source-freshness-gate-20261029` 清理了 `ObservedState.require_source_freshness_verified` 的重复定义，避免后定义覆盖前定义造成维护歧义。修复提交 `7104df6c84fc0ff0c1c2978daed1b12834d47aa8` 的 CI 通过：[run 40829343257](https://github.com/wosafety0/wosafety0clamp/actions/runs/40829343257)。修复文件已从 GitHub 回读核对，方法定义现仅有一处。
- **当前安全结论：** `PoiExpeditionReader` 仍不会设置 `source_freshness_verified=True`；在获得可验证的当前会话/源刷新证据前，真实 Poi 状态必须继续被安全前置检查拒绝。不要将缓存存在性、health 状态、本地读取时间或两次读取内容相同误认为源新鲜度证明。真实运行时刷新行为仍需只读实机证据验证。


### 2026-10-29 Candidate: timestamped Poi port-response snapshot

- 上游静态源码确认，Poi 的 `views/env-parts/data-resolver.ts` 会对成功的游戏 API 响应派发 `game.response` 事件，事件包含 `path`、解析后的 `body` 与 `time`；事件在对应 Redux 更新后派发。固定源码版本：`poooi/poi@725029997b84729df6394e1f107079b8e9437f36`。
- `lib/game-api-broadcaster.ts` 在收到 HTTP 200 且可解析的响应时，以 `Date.now()` 记录响应接收时间。该值是本机接收时间，不是游戏服务器生成时间，但可用于约束“最近一次收到有效响应”的本地时间窗口。
- 同一版本的舰队 reducer 从 `api_port_port` 响应的 `api_deck_port` 更新舰队，舰船 reducer 从同一响应的 `api_ship` 更新舰船。因此，捕获成功的 `/kcsapi/api_port/port` 响应体与其接收时间，是绕过可能陈旧的 Redux 持久化缓存、同时取得舰队/舰船基线快照的潜在只读路径。
- **实现状态：未实现；验证状态：仅上游静态源码审查通过，未在用户当前 Poi 实例验证事件可见性、实际 payload 完整性及刷新频率。** 未来实现应只监听已发生的 `game.response` 事件，不主动请求游戏 API；在收到可验证的成功 port 响应前继续 fail-closed，并对接收时间设置年龄上限。不得把这个本机接收时间称为服务器端更新时间。
- 当前 `PoiExpeditionReader` 尚未消费该事件或时间信息，故此发现不改变当前安全结论：真实状态仍不具备已验证的新鲜度证据。


### 2026-10-29 Poi port response fixture contract cross-check

- 继续对固定上游版本 `poooi/poi@725029997b84729df6394e1f107079b8e9437f36` 的只读源码与测试夹具交叉核验。测试夹具 `views/redux/info/__tests__/__fixtures__/api_port_port_typical.json` 的响应路径为 `/kcsapi/api_port/port`，响应事件形状包含 `path`、`body`、`postBody` 和本机接收时间字段 `time`。
- 该静态夹具的 `body` 同时包含 `api_deck_port` 与 `api_ship`；样本中分别有 4 个舰队和 391 条舰船记录。舰队记录包含 `api_id`、`api_mission`、`api_ship` 等字段，舰船实例包含 `api_id`、`api_ship_id`、`api_nowhp`、`api_maxhp`、`api_fuel`、`api_bull` 等字段。
- 对照同一固定版本的 `views/redux/info/fleets.ts` 与 `views/redux/info/ships.ts`，`api_port_port` reducer 分别从同一响应体的 `api_deck_port` 和 `api_ship` 更新舰队及舰船状态；`data-resolver.ts` 在 Redux 更新调用后派发 `game.response`。这进一步支持“被动监听已发生的成功母港响应”作为候选数据源，而不是额外发起游戏 API 请求。
- **验证边界：** 这是固定版本的上游源码和静态测试夹具证据，不是当前 Poi 安装版本的实时捕获。夹具时间戳属于历史样本，不能作为当前新鲜度证据；也不能证明当前游戏会话会按预期产生该事件。真实状态仍不得设置 `source_freshness_verified=True`，直到实际 Poi 运行时的事件、字段完整性与接收时间窗口经只读验证。
- 参考： [data-resolver.ts](https://github.com/poooi/poi/blob/725029997b84729df6394e1f107079b8e9437f36/views/env-parts/data-resolver.ts)、[fleets.ts](https://github.com/poooi/poi/blob/725029997b84729df6394e1f107079b8e9437f36/views/redux/info/fleets.ts)、[ships.ts](https://github.com/poooi/poi/blob/725029997b84729df6394e1f107079b8e9437f36/views/redux/info/ships.ts)、[api_port_port_typical.json](https://github.com/poooi/poi/blob/725029997b84729df6394e1f107079b8e9437f36/views/redux/info/__tests__/__fixtures__/api_port_port_typical.json)。


### 2026-10-29 Consolidated Poi safety gates and offline CI

- 将此前分离验证的四类保护合并到独立集成分支 `dev/poi-safety-consolidation-20261029`：舰队 ID 与跨舰队舰船实例全局唯一性、Poi 集合完整性元数据检查、读取期间舰队/舰船状态二次核对，以及默认关闭的源新鲜度证明门槛。
- 集成分支精确提交：`fa20ea1082dde9890900f0f5fd7f1b2f4929edbf`。该提交以当前 MVP 分支提交 `bc3ae7ab863ffee4bbcf5687c2cd2bc33e5ce6a6` 为父提交，未合并到 MVP 分支。
- 精确提交 CI 通过：[GitHub Actions run 40858134020](https://github.com/wosafety0/wosafety0clamp/actions/runs/40858134020)。成功步骤包括 JavaScript 静态提取测试、Python 源码编译和离线单元测试；日志确认 **130 项 Python 测试通过**。
- 已从 GitHub 权威远程回读核对集成分支头提交及关键源码 blob：读取器 `9634d4b960ab71c5acd164353d5f7996dfa7e22e`、状态归一化器 `1c91e67ea5c6da9bead7c89fba7f79dddd12d603`、新鲜度门槛 `b48d7a4955d53067dd5ed664f3b22c2e395abced`。
- **验证边界：** 该 CI 只验证离线测试和静态检查，不证明当前 Poi 实例的事件可见性、实时数据完整性或新鲜度。读取器仍不会设置 `source_freshness_verified=True`，因此安全前置继续拒绝未经证明的新鲜度状态；真实派遣保持关闭。集成分支未合并，也未执行游戏操作。


### 2026-10-29 Passive Poi port-response observer artifact

- 在独立集成分支 `dev/poi-safety-consolidation-20261029` 新增只读诊断脚本 `tools/hand_sandbox/poi_port_response_observer_20261029.js` 及使用限制文档 `docs/POI_PORT_RESPONSE_OBSERVER.md`。脚本通过 loopback CDP 的 Network 域等待自然发生的成功 `/kcsapi/api_port/port` 响应，跳过缓存/Service Worker/prefetch 响应，校验 API 成功码、舰队与舰船字段，并只保存精简快照和响应体哈希。
- 脚本不使用 `Runtime.evaluate`，不调用游戏函数，不发送输入，也不主动发起游戏 API 请求；如果 120 秒内没有自然发生的合格响应，会超时退出，不会尝试刷新或派遣。输出保存到桌面的 `KancolleBot-Port-Snapshots`，不保存完整原始响应。
- 精确提交 `7accb7a7634c463526c953f2fa15a2e36e92942d` 的 GitHub Actions CI 通过：[run 40858395913](https://github.com/wosafety0/wosafety0clamp/actions/runs/40858395913)。静态脚本安全契约测试通过，JavaScript 静态提取测试、Python 编译通过，**130 项 Python 单元测试通过**。
- **实现状态：脚本已保存到 GitHub 独立分支；自动化静态检查通过。运行时验证状态：未验证。** 尚未在用户当前 Poi 实例执行该脚本，因此不能声称目标页识别、CDP Network 事件、真实响应体解析或快照写入已在实机成功。该脚本是诊断探针，不会修改生产新鲜度门禁；真实派遣继续关闭。


### 2026-10-29 Draft PR for safety-gate consolidation

- 已建立 Draft PR #15：[Consolidate Poi safety gates and add passive port-response probe](https://github.com/wosafety0/wosafety0clamp/pull/15)，目标为 `dev/expedition-closed-loop-mvp`，来源分支为 `dev/poi-safety-consolidation-20261029`。
- PR 包含 11 个文件，集中整合安全门槛、离线回归测试与只读诊断探针。PR 当前为 Draft/Open/未合并；不自动合并、不修改分支保护。
- 精确代码提交 `7accb7a7634c463526c953f2fa15a2e36e92942d` 的 CI 已通过；随后只修正了探针文档中的验证状态说明。PR 分支最新头为文档提交 `f25fb75637c471d96b250e46eb770769b3e4ea94`，核心代码仍对应已通过 CI 的提交。
- 下一项必须完成的验收是：在用户当前 Poi 实例中运行被动探针，核实目标发现、自然发生的成功母港响应、精简快照输出及本地哈希。实机验证完成前，PR 保持 Draft，生产新鲜度门禁不放行，真实派遣继续关闭。

### 2026-10-30 DC 调用成本控制优化

- 按全局唯一权威规则 `Agents-global/main/AGENTS.md` 当前版本执行本轮工具成本优化；当前 `main` 指向提交 `f357d6e54b286a285e058eeff3c1dca0c591fc92`，其中包含第 14.3 节 DC 调用成本控制。
- 核对正式开发分支 `dev/expedition-closed-loop-mvp` 当前 HEAD 为 `7a04fe48854cae15172e94619fe55e03a1fcb587`；该分支树中未发现独立的 `AGENTS.md` 或 `PROJECT_kancollebot_MASTER.md`，本轮未将 Harness/试验分支文档作为正式项目规则。
- 发现并修复正式分支中的 `.github/workflows/verify-global-rules.yml`：原流程固定读取 2026-10-28 的旧规则提交 `018707cdbca1031ae94ce0f26edc9854ee78e4ed`，现改为每次从 `Agents-global/main` 解析当前 commit 与 `AGENTS.md` blob SHA，避免重复维护过期规则版本。
- 成功提交：`6fdbd436fa769889520b5cca7ca3d479ae00d3e9`；远端回读确认工作流内容与提交差异正确。
- 本轮仅使用 GitHub 连接器处理远端源码、规则、进度和提交核验，未调用 DC；因此没有产生新的 DC 调用。当前没有可核验的 DC 总量/CLAMP 专属调用量统计接口，本轮不报告节省比例。
- **验证状态：** 工作流修改已远端保存并回读核验；尚未实际运行该 workflow，因此标记为 `IMPLEMENTED`，不是 `TESTED/VERIFIED`。真实本机运行状态未改变。

## 2026-10-30 - CLAMP DC 批量本机检查入口

- 在已有 2026-10-30 DC 成本优化基础上，新增 `docs/DC_CALL_COST_OPTIMIZATION.md` 与 `scripts/dc_clamp_batch_check.ps1`，把“GitHub 优先、必要本机检查一次批量完成”的策略固化为可复用项目入口，避免重复启动多个 DC 进程。
- 本轮 DC 总体统计实测为 **1548 次调用、99% 成功、剩余 83%**；这是账户/设备总体数据，不作为 CLAMP 专属调用量。当前完整历史归因仍不足，因此**没有声称节省比例**。
- 远端文件已通过 GitHub 连接器回读；批量脚本本身尚未在本机执行，因此其运行验证状态为 **IMPLEMENTED / NOT_TESTED**，不能把它描述为已实测降低 DC 调用量。

## 2026-10-30 - DC 批量检查入口实机只读验证

- 从正式分支远端直接读取 `scripts/dc_clamp_batch_check.ps1`，在用户 Windows 环境以 PowerShell 5.1 兼容方式完成语法解析与实际只读执行；输出成功列出正式工作区、集成工作区、关键项目路径和 worktree map。
- 该执行未写入本地文件、未提交/推送本地工作区，也未修改系统设置；正式本地工作区当前仍为 `7e958b4b1ac7e599f2a0d87150985a992603f9d7`，相对远端正式分支落后 10 个提交。脚本文件在本地显示不存在，这是本地尚未同步，而不是远端文件缺失。
- 因此该入口现在状态为 **TESTED（远端脚本在本机只读执行通过）**；尚无“实际减少 DC 调用次数”的历史对照样本，仍不报告节省比例。

## 2026-10-30 - Static decoder alias-chain analysis

- Updated `tools/hand_sandbox/static_decoder_extract_20261027.js` to trace chained local identifier aliases from the factory's `_0x460358` seed (for example, `_0x1298ca` and aliases derived from it), collect calls through those aliases, and include both factory-relative `offset` and absolute `sourceOffset` in each call record. The report now includes `factoryDecoderAliases`.
- Added offline regression coverage for chained aliases, nested call arguments, ignoring alias declarations, and exact source-slice/offset consistency in `tests/static_decoder_extract.test.cjs`.
- Verified on an isolated temporary worktree at remote branch HEAD `7ec112fb5dc9cf3836863de567e095bdd1ea44be`: `node --test tests/static_decoder_extract.test.cjs tests/verify_cdp_snapshot.test.cjs` passed **15/15 tests**, exit code 0. The integration test also confirms the report exposes alias analysis and that each reported absolute source offset resolves to the exact source slice. The original local worktree was preserved; no local project files were modified by this verification.
- The current live-session snapshot remains `main-20261030T021952.142Z.js`, 8,495,851 characters / 8,496,723 bytes, SHA-256 `073df7c8e4d4680208070b22806273b332e2f5ba7ae8b80005ad5914f336561e`. This matches the previously captured source exactly.
- Applied the updated alias-chain helper directly to the full saved 8,495,851-character snapshot in an isolated temporary worktree. It found 4 aliases (`_0x460358`, `_0x1298ca`, `_0x520871`, `_0x399f9e`) and 33 factory calls across the three derived aliases (15, 10, and 8 respectively). Every call expression matched its exact absolute source slice; exit code 0. The analysis loaded only the captured source as text and did not execute it.
- **Verification boundary:** the full-snapshot alias helper check and 15/15 offline regression tests pass. This is static analysis, not runtime freshness verification. No game code, game functions, refresh, dispatch, or reward action was executed; real dispatch remains disabled.

## 2026-10-30 - Static alias analysis ignores comments and quoted literals

- Review of `extractAliasCalls()` identified a precision gap: declaration-like text in comments and quoted literals could be mistaken for local decoder aliases or call expressions because matching ran directly against raw source text.
- Added `maskNonCode()`, which replaces comments, single/double-quoted strings, and template literals with whitespace while preserving line breaks and exact character offsets. Alias declaration discovery and call matching now operate on the masked text; returned call expressions still come from the original source.
- Added an offline regression fixture containing fake alias declarations and calls inside both a quoted string and a block comment. The test verifies only the real factory decoder call is reported.
- Validation on isolated worktree at branch base `b60cc13187b201d291c96213bf98a3d46e5dc884`: `node --test tests\static_decoder_extract.test.cjs tests\verify_cdp_snapshot.test.cjs` passed **16/16 tests**, exit code 0.
- Re-ran the updated helper against the full saved snapshot `main-20261030T021952.142Z.js` (8,495,851 characters). It still finds **4 aliases and 33 calls**, and all relative and absolute call offsets resolve to exact source slices.
- **Scope/limitations:** this remains a lightweight lexical heuristic, not a complete JavaScript parser. It does not resolve lexical scope/shadowing or alias reassignment, does not tokenize regular-expression literals separately, and treats an entire template literal (including its interpolations) as non-code. These choices can still cause false positives in unusual syntax or conservative undercounting; findings must be treated as static leads, not semantic proof.
- **Safety boundary:** source was read as text only. No page source was executed, no game function or getter was invoked, and no input, refresh, dispatch, or reward action was sent. Runtime source freshness remains unverified; real dispatch remains disabled.

## 2026-10-30 - Static string-table rotation bootstrap extraction

- Extended the static extractor with `extractTableRotationBootstrap()`, which locates the function expression invoked with the string-table factory and captures its exact source range and numeric rotation target without executing the rotation loop.
- Integrated the rotation metadata and a non-duplicating string-table array summary (identifier, exact range, character count, and top-level entry count) into the extraction artifact and bounded console report. The synthetic integration fixture verifies array counting when a string contains a comma, rotation terms/target, exact source bounds, and absence of execution.
- On the full saved snapshot `main-20261030T021952.142Z.js`, the extractor identifies:
  - String-table function `_0x3f23`: source range `[0, 317022)`, 317,022 characters.
  - Its array `_0x23d509`: source range `[35, 316965)`, 316,930 characters and **14,271 top-level entries**. The extracted array exactly matches the source slice; commas inside string values do not inflate the count.
  - Decoder `_0x3353`: source range `[317022, 318153)`, 1,131 characters.
  - Rotation bootstrap: source range `[318153, 318814)`, 661 characters; invocation target `0xd12ca`. The extracted block matches the exact source slice.
  - Application factory `_0x466acb`: source range `[8464467, 8465759)`, 1,292 characters.
- Added literal-only extraction of the rotation checksum terms. The block contains 12 `parseInt`-decoder terms, all through alias `_0x24797f`, with index/divisor pairs `(0x489/0x1, 0x23ed/0x2, 0x737/0x3, 0x3587/0x4, 0x471/0x5, 0x2a8a/0x6, 0x1893/0x7, 0x2b95/0x8, 0x29ea/0x9, 0x1929/0xa, 0x1140/0xb, 0x2e86/0xc)`. These are reported as source literals only; no decoder calls or checksum calculation were executed.
- The full-snapshot cross-check still reports 4 factory decoder aliases and 33 calls, with all recorded relative/absolute offsets matching source slices. The rotation target is only reported as a literal from source; the table rotation algorithm was **not run**, and decoded string values were not generated.
- Validation on isolated worktree at the prior verified branch head `3f43380a9c67438985aad68434448e3d502e8fa6`: JavaScript syntax check passed and `node --test tests\static_decoder_extract.test.cjs tests\verify_cdp_snapshot.test.cjs` passed **17/17 tests**, exit code 0.
- **Limitations:** this extracts a syntactic bootstrap candidate and constant, not proof that the loop would terminate or that decoded strings are semantically correct. Full source freshness, live runtime behavior, and any gameplay automation remain unverified; real dispatch remains disabled.

## 2026-10-30 - Factory decoder index bounds cross-check

- Added static extraction of the decoder's literal index adjustment and annotated each factory decoder call with its raw argument, adjusted table index, and bounds result. The report includes aggregate counts in both the full extraction artifact and the bounded console output.
- Cross-checked the full snapshot using the same production helpers: decoder adjustment is `0x150`; the table contains 14,271 entries; all **33/33** factory calls have literal first arguments and adjusted indices within the array bounds. There are **0 out-of-bounds** and **0 unresolved** calls.
- The adjusted table indices range from **981** to **14,226**. Alias counts remain 15 calls through `_0x1298ca`, 10 through `_0x520871`, and 8 through `_0x399f9e`; every call expression still maps to its exact factory-relative source slice.
- Offline syntax check passed and `node --test tests\static_decoder_extract.test.cjs tests\verify_cdp_snapshot.test.cjs` passed **17/17 tests**, exit code 0.
- **Verification boundary:** the bounds check is literal parsing plus integer arithmetic only. It does not invoke `_0x3353`, calculate the rotation checksum, rotate the table, decode strings, execute page source, or establish live freshness. Real dispatch remains disabled.

## 2026-10-30 - MVP direction correction: freshness evidence before automation

- **Goal correction:** the project target is a working single-fleet expedition MVP, not deeper understanding of the obfuscated game bundle. Preserve the existing static extraction tools and their verified regression coverage, but freeze further deobfuscation unless a concrete MVP blocker cannot be resolved through supported state interfaces or passive observation.
- **Next minimal sequence:** (1) passively capture a naturally occurring successful `/kcsapi/api_port/port` response; (2) validate response provenance, completeness, receive/capture timestamps and age; (3) compare the captured fleet/ship fields with a current Poi MCP read and fail closed on stale, missing or mismatched evidence; (4) read expedition state only from supported, evidence-backed fields; (5) finish the decision path with offline fixtures for valid, stale, incomplete and inconsistent evidence, keeping all live actions gated.
- **Existing artifacts and branch boundary:** passive observer, witness wrapper, freshness comparison helper and their offline tests are on `dev/poi-safety-consolidation-20261029` and are not thereby integrated into `dev/expedition-closed-loop-mvp`. Reuse the smallest necessary parts only after reviewing branch diffs and compatibility; do not duplicate the existing `PoiExpeditionReader` or widen the MVP scope.
- **Verified evidence:** the integration-branch offline CI for commit `fa20ea1082dde9890900f0f5fd7f1b2f4929edbf` passed 130 Python tests; observer-wrapper offline tests exist. These are offline/CI results, not live runtime evidence. The previously attempted live passive observation returned `FRESHNESS_UNVERIFIED` / `no_capture`; a Poi fleet count of 3 does not prove witness freshness.
- **Not yet verified:** end-to-end natural response capture, response-to-current-Poi consistency in the live session, source freshness proof, and a real expedition loop. Do not set `source_freshness_verified=True` or report witness E2E as passed without new evidence.
- **Safety remains unchanged:** no page refresh, no initiated game API request, no game input, and no real dispatch/expedition action to manufacture a test event. Real game actions remain disabled. Use offline fixtures and passive naturally occurring events only.
- **Acceptance order:** first demonstrate a trustworthy passive evidence path and offline fail-closed decisions; then evaluate whether the minimal reader/decision path is sufficient. Treat code implementation, offline tests, live E2E, commit, push and remote read-back as separate states. This entry records the direction correction and existing verified facts only; it does not claim the witness E2E or MVP is complete.

## 2026-10-30 - MVP witness integration boundary review

- Reviewed the formal MVP reader/tests and the passive witness implementation on `dev/poi-safety-consolidation-20261029` directly from GitHub. The integration branch's reader accepts the in-memory `port_snapshot_provider` and `freshness_max_age` arguments used by `tools/verify_poi_freshness_bridge.py`; the formal MVP branch's reader does not. Therefore the diagnostic must be run from a compatible integration checkout, or the smallest reviewed compatibility change must be deliberately ported with its tests. Do not copy the diagnostic alone into the formal branch or assume branch-local tests apply across branches.
- GitHub comparison reports the branches have diverged: the integration branch is 74 commits ahead and 27 behind the formal MVP branch, with merge base `bc3ae7ab863ffee4bbcf5687c2cd2bc33e5ce6a6`. A wholesale merge would exceed the MVP scope; review and select only the witness/consistency pieces needed.
- Confirmed the existing read-only assessment layer suppresses planning unless both local state age and source freshness pass, and always returns `dispatch_eligible=False`. Preserve this gate; it is planning-only and not an execution path.
- Existing evidence remains bounded: a historical passive capture on 2026-10-29 verified one natural non-cache HTTP 200 port response and compact payload structure (3 fleets, 92 ship records), but it is not current-session freshness evidence. The later in-memory witness-to-current-MCP E2E remains unverified; the latest attempt returned `no_capture`.
- Validation status for this review: GitHub source and diff readback/static inspection only; no tests were run in this review and no live Poi interaction occurred. No code was changed. Next step is a minimal compatibility/test pass in an isolated branch/worktree, not a wholesale merge or production gate change. Real game actions remain disabled.

## 2026-10-30 - Minimal passive witness reader bridge port

- Ported the smallest reviewed witness path from the divergent integration worktree into an isolated worktree based on formal MVP remote HEAD `fd88626353084f9e4bcb2477241b3480b9b2f907`. The port adds an optional in-memory `port_snapshot_provider`, a 30-second default freshness limit, strict passive-response shape/age/cache validation, current Poi fleet/ship consistency checks, and a planning-only assessment that keeps `dispatch_eligible=False`. It also includes the observer wrapper/script, a read-only diagnostic CLI, and focused tests; no whole-branch merge or live-action path was added.
- Offline validation on Windows/Python 3.13: `python -m unittest discover -s tests -v` passed **154/154 tests**. `python -m compileall -q src tests`, `node --check tools/hand_sandbox/poi_port_response_observer_20261029.js`, the existing Node regression suite (**17/17**), and `git diff --check` all passed. CI now checks the passive observer JavaScript syntax as well.
- **Implementation/verification boundary:** the witness provider is optional and in-memory only; a missing, stale, or mismatched natural response leaves source freshness unverified. The diagnostic is read-only and planning-only, not a production executor. Offline tests do not establish live witness freshness or atomic Poi snapshots; no game input, refresh, initiated game API request, dispatch, or reward action was performed.

## 2026-10-30 - Read-only diagnostic exposes guarded fleet assessment

- Connected the existing `assess_observed_fleets` layer to `tools/verify_poi_freshness_bridge.py`. The diagnostic now reports a minimal per-fleet planning assessment alongside freshness metadata, including whether the observation was accepted, the planning decision/reason, and the invariant `dispatch_eligible: false`.
- Unverified or stale observations produce a suppressed (`decision: null`) assessment. A verified `READY_TO_DISPATCH` result remains planning-only and cannot authorize a live action; the CLI has no dispatch call or executor wiring.
- Added offline regression tests for both suppression and verified planning output. Validation on Windows/Python 3.13: **156/156 Python unit tests passed**; `compileall`, passive observer `node --check`, Node regression suite (**17/17**), and `git diff --check` passed.
- **Verification boundary:** this verifies the CLI-to-assessment composition and fail-closed output with offline fixtures only. It does not establish a new live passive witness capture or complete the real Poi E2E. Natural-response freshness remains unverified until a matching response is passively observed; no game input, refresh, initiated API request, dispatch, or reward action was performed.


## 2026-10-30 - Passive observer failure status is diagnostically specific

- Improved the in-memory observer wrapper to preserve the reviewed JavaScript observer's terminal status when no witness is emitted. Natural-response timeout, local CDP target discovery failure, CDP connection/protocol/command errors, and rejected response bodies are now distinguished instead of collapsing into `no_capture`. A witness is accepted only when exactly one valid witness accompanies `STATUS: CAPTURED`.
- Added offline regression tests for explicit timeout mapping, target discovery failure, and rejecting a witness without a matching captured status. This is diagnostic clarity only: it does not relax source-freshness validation or authorize actions.
- **Verification boundary:** the live 130-second passive diagnostic on 2026-10-30 returned `FRESHNESS_UNVERIFIED`, `observer_status: no_capture`, three Poi fleets, all three assessments suppressed, and `game_actions_enabled: false` (exit code 2). It did not capture a witness, so the root cause is not established by that output. The new status mapping is covered only by offline fixtures until another naturally occurring run. No refresh, initiated game API request, game input, dispatch, or reward action was performed.


## 2026-10-30 - Passive observer timeout event counters

- Added privacy-minimal counters to the passive CDP observer's timeout detail: whether `Network.enable` succeeded, total `Network.responseReceived` events, responses from `kancolle-server.com` hosts, `/kcsapi/api_port/port` path responses, HTTP 200 responses on that path, and skipped cached/service-worker/prefetch responses. The observer logs counts only, not arbitrary URLs or response contents.
- This makes a later passive timeout diagnostically useful: zero network response events differs from unrelated traffic being observed, a port-path/status mismatch, or cached responses being deliberately excluded. It does not claim to distinguish every possible frame/network topology and does not alter the acceptance rules.
- Current read-only local CDP discovery found exactly one matching game WebView at `https://play.games.dmm.com/game/kancolle`; it does not by itself prove that a qualifying port response will occur during a given window.
- **Validation:** full Python unit suite passed **158/158**; `compileall`, observer JavaScript `node --check`, existing Node regression suite **17/17**, and `git diff --check` passed. The 7 focused observer-wrapper tests also passed.
- **Verification boundary:** the counter behavior is syntactically and offline-regression validated, but the new counters have not yet been exercised by a completed live observation window. No game input, page refresh, initiated game API request, dispatch, or reward action was performed. Source freshness remains unverified; live actions remain disabled.


## 2026-10-30 - Surface passive observer timeout counters in bridge diagnostics

- Follow-up verification showed the observer emitted timeout counters as a `DETAIL` line, but the Python wrapper and bridge CLI only propagated the terminal status. Added strict allowlisted parsing of timeout detail fields and included those counters in the diagnostic JSON as `observer_diagnostics`.
- Only the known counter/reason keys are exposed; arbitrary detail fields (including URLs) are rejected. This is diagnostic metadata only and does not change witness acceptance, freshness limits, planning suppression, or action gates.
- Added regression coverage for timeout counter propagation and rejection of unexpected detail fields.
- **Validation:** full Python suite **160/160 passed**; observer wrapper tests **9/9 passed**; Node regression suite **17/17 passed**; `compileall`, observer JS syntax check, and `git diff --check` passed.
- **Verification boundary:** counter propagation is unit-tested, but a live diagnostic using this corrected reporting path remains pending. The preceding 130-second observation ended with `natural_response_timeout`; no qualifying witness was captured, all fleet assessments remained suppressed, and game actions remained disabled. No game input, refresh, initiated API request, dispatch, or reward action was performed.


## 2026-10-30 - Distinguish silent CDP event stream from response filtering

- After the corrected diagnostic surfaced networkEnabled=true but networkResponseEvents=0 throughout a 120-second window, extended timeout-only counters to include total CDP event messages and Network request/finished/failed event counts. These counts can distinguish a wholly silent event stream from request activity without response events; no URLs, request bodies, or page contents are logged.
- The latest live observation still does not establish whether the game was idle or whether this WebView's CDP event stream omits relevant traffic. The extra counters are diagnostic-only and do not relax freshness or action gates.
- Validation: full Python unit suite passed 160/160; observer wrapper tests 9/9; Node regression suite 17/17; Python compileall, observer JavaScript syntax check, and git diff check passed. The counters are allowlisted through the Python bridge and expose counts only. A subsequent natural-response observation is still needed to establish whether any CDP events arrive during ordinary game activity. No game input, refresh, initiated game API request, dispatch, or reward action was performed.


## 2026-10-30 - Confirm passive CDP game-frame topology

- Read-only CDP Page.getFrameTree inspection confirmed the selected DMM WebView contains a nested game_frame iframe and, beneath it, the htmlWrap frame at the Kancolle server's /kcs2/index.php path. URL query parameters and tokens were intentionally excluded from the recorded result.
- Browser-level Target.getTargets, including after enabling target discovery, exposed only the Poi page, the top-level Kancolle WebView, and a service worker; no separate iframe target was returned. This supports keeping the current WebView target as the known attach point rather than blindly adding a guessed iframe target.
- Verification boundary: this establishes frame/target metadata only. It does not prove that Network events will occur during an idle window or that the passive observer has captured a fresh port response. No page JavaScript was evaluated, and no game input, refresh, initiated game API request, dispatch, or reward action was performed.

## 2026-10-30 - Live passive witness-to-Poi MVP verification succeeded

- After the user's game session reconnected, ran the existing read-only `python tools/verify_poi_freshness_bridge.py --timeout-seconds 130 --max-age-seconds 30` diagnostic against the current Poi instance. The observer passively captured a naturally occurring port response and the bridge verified its freshness and consistency with the current Poi fleet/ship state. The process exited **0** after approximately 92 seconds.
- Exact acceptance evidence: `status=FRESHNESS_WITNESS_MATCHED`, `source_freshness_verified=true`, `observer_status=captured`, `fleet_count=3`, and `game_actions_enabled=false`. All three `observation_accepted` values were true. Fleet 1 and fleet 3 produced planning-only `ready_to_dispatch`; fleet 2 produced `blocked_unknown_return_time` because the active mission had no trusted return time.
- **Safety boundary:** all three `dispatch_eligible` values remained false. This verifies one current-session natural-response witness-to-Poi read-only assessment path, **not** a real dispatch, mission return-time source, reward collection, repeated-session reliability, or the complete autonomous expedition loop. No game input, page refresh, observer-initiated game API request, dispatch, or reward action was performed. Production action gates remain unchanged.
- The observed live success is runtime evidence; the earlier offline suite remains separately verified (160/160 Python and 17/17 Node at the prior code revision). No source code or production gate was changed in this documentation-only milestone.

## 2026-10-30 - Live pending-reward mission status and planning distinction

- With the user's second fleet returned but reward settlement not yet clicked, a read-only local Poi MCP `info.fleets` read confirmed three complete four-slot mission arrays, with fleet status codes `0`, `2`, `0` respectively. This is a live observation of `api_mission[0] == 2` for the pending-reward fleet, consistent with the user's visible state; no reward or game input was initiated.
- Added a minimal planning-only `completed_awaiting_reward` decision for a normalized mission status `2`, preserving the raw status in `ExpeditionFleetSnapshot`. Status `1` with untrusted return time remains `blocked_unknown_return_time`; status `0` remains idle. The change does **not** infer mission return timestamps, initiate collection, or authorize dispatch.
- Offline verification: **160/160 Python tests**, **17/17 Node tests**, Python compileall, observer JS syntax, and git diff whitespace checks passed. The existing pending-reward fixture now asserts the distinct result. This is a read-only assessment improvement, not an autonomous expedition-loop acceptance.

## 2026-10-30 - Pending-reward rotation safety regression

- Added offline regression coverage proving that a completed/pending-reward mission status remains `completed_awaiting_reward` even when an optional return timestamp has elapsed. This prevents an elapsed clock value from overriding an authoritative pending-settlement status.
- Added a rotation-selection regression proving a preferred fleet with pending rewards is skipped in favor of an otherwise ready idle fleet. Both are planning-only checks; neither invokes a game adapter or enables dispatch.
- Verification: full Python suite **162/162 passed**, Node suite **17/17 passed**, Python compileall, passive observer JavaScript syntax and git diff checks passed. Live gameplay execution and reward collection remain disabled and unverified.

## 2026-10-30 - Fail closed on unverified post-dispatch source evidence

- Tightened the offline expedition coordinator's post-dispatch confirmation: a newer observation with the requested mission ID is not sufficient if its source freshness is unverified or its source identity differs from the pre-action observation. In that case, the outcome remains `dispatch_outcome_unknown`, with no automatic retry.
- Added an injected-adapter regression that simulates a successful dispatch call followed by loss of source freshness proof. The result is unknown rather than confirmed. This is offline safety logic only; the actual Poi read-only adapter has no dispatch method and live gameplay remains disabled.
- Verification: full Python suite **163/163 passed**, Node suite **17/17 passed**, Python compileall, observer JS syntax and git diff checks passed. No real dispatch, reward collection, refresh, or game input occurred.

## 2026-10-30 - Post-dispatch confirmation requires underway mission state

- Tightened offline expedition runner confirmation: a newer, source-verified post-action observation must report both the requested mission ID and normalized `mission_status == 1` (underway). A matching mission ID in status `2` (completed, pending reward) is not evidence of a newly accepted dispatch and remains `dispatch_outcome_unknown`, without automatic retry.
- Updated the fake adapter's successful simulated dispatch to report underway status and added a regression simulating the same mission ID in pending-reward status. No production game action adapter was added or enabled.
- Verification: Python **164/164**, Node **17/17**, Python compileall, observer JavaScript syntax, and diff whitespace checks passed. Live dispatch and reward collection remain disabled and unverified.

## 2026-10-30 - Pending reward blocks full offline coordinator dispatch

- Added a full coordinator regression with a single pending-reward fleet (`mission_status == 2`) that is explicitly preferred, otherwise supplied and undamaged, and run under an enabled *fake* safety policy with confirmation and a durable temporary journal. The result is `no_ready_fleet`; the fake adapter's dispatch method is never invoked.
- This verifies the pending-reward block propagates beyond the planner and rotation into the actual offline runner entry point. No live adapter, reward collection or game input was enabled.
- Validation: Python **165/165**, Node **17/17**, Python compileall, observer JavaScript syntax and diff whitespace checks passed.

## 2026-10-30 - Offline string-table rotation and PIXI application factory decode

- Resumed against the saved `main.js` snapshot and independently verified its SHA-256 `073df7c8e4d4680208070b22806273b332e2f5ba7ae8b80005ad5914f336561e` (8,495,851 source characters; 8,496,723 UTF-8 bytes). Extracted only the string-table function, decoder function, and the exact 661-character rotation bootstrap `[318153, 318814)` into a Node VM isolated from the browser. The rotation loop completed under a 2-second VM timeout with target `0xd12ca`; the decoder then resolved all **33** static decoder calls in `_0x466acb`.
- Decoded factory identifiers establish that it writes `PIXI.settings.GC_MAX_IDLE = 720` and `PIXI.settings.GC_MAX_CHECK_COUNT = 360` (these are not renderer width/height). It constructs `new PIXI.Application(width, height, options)`, configures `renderer.plugins.interaction.autoPreventDefault`, appends the application's `view` to `document.body`, adds a module-provided view to `app.stage`, then starts `AppInitializeTask` with a params object containing the application view.
- Static reference check found `_0x4050d5` (the local application instance variable) only four times in the whole snapshot, all within the factory; no assignment to `window.__GAME_APP__` or equivalent global application-instance export appears in this factory. This supports investigating constructor interception as a possible capture point, but does **not** prove the runtime `PIXI.Application` property can be safely wrapped or that the stage contains a usable expedition-control mapping.
- **Verification boundary:** this was offline execution of the extracted string-table/decoder/rotation snippet in a Node VM, not execution in the game page. No browser `Runtime.evaluate`, getter invocation, page refresh, game input, initiated game API request, dispatch, or reward collection occurred. The supplied Hook remains unverified; descriptor/configurability and actual instance/stage capture still need independent evidence. Production action gates remain unchanged.


## 2026-10-30 - CDP endpoint recovery after Poi restart

- After the Poi window was reopened, a read-only request to the loopback CDP version endpoint succeeded and identified Poi 12.0.1 / Electron 44. The current target list again exposed the Poi renderer and the DMM Kancolle WebView.
- A CDP WebSocket request for `Page.getFrameTree` succeeded. The current tree contains `game_frame` and its nested `htmlWrap` frame at `/kcs2/index.php`; URL query parameters and session tokens are intentionally excluded.
- **Verification boundary:** this confirms the local CDP endpoint and game-frame topology are reachable again. It does not verify the pre-init Hook, PIXI constructor interception, application instance capture, or stage readability. No game input, page refresh, dispatch, reward collection, or observer-initiated game API request was performed in this recovery check. Production action gates remain unchanged.


## 2026-10-30 - PIXI Application export descriptor identified

- Read-only CDP script enumeration found the active game main script hash matches the saved snapshot (073df7c8e4d4680208070b22806273b332e2f5ba7ae8b80005ad5914f336561e). The loaded PIXI library is v4.8.8 at /ajax/libs/pixi.js/4.8.8/pixi.min.js, SHA-256 ddff2d0e5f1b95026ccced8aed9a03391092f83c35fff6ee077a7cbbae6227ef.
- Static inspection of that exact PIXI script confirms the library assigns its namespace to the window global. The Application export is an accessor with a getter; its property descriptor omits the configurable flag, whose ECMAScript default is false. Directly redefining the Application property is therefore not a viable interception point.
- A possible alternative is to intercept the namespace assignment before game initialization and wrap subsequent Application reads. This remains a hypothesis, not an implemented or verified hook; a controlled pre-initialization run is still required. No page script was executed and no page refresh, game input, game request, dispatch, or reward action occurred during this source inspection. Production action gates remain unchanged.


## 2026-10-30 - PIXI namespace Proxy interception mechanism isolated test

- An isolated Node.js test model reproduced the relevant export shape: `window.PIXI` is assigned a namespace whose `Application` property is a non-configurable getter. A preinstalled setter wrapped the assigned namespace in a `Proxy`; a `get` trap wrapped the constructor with a `construct` trap. The test confirmed namespace wrapping, constructor interception, instance capture, and stage readability, while preserving the original accessor (`configurable: false`). All four mechanism checks passed.
- **Scope limitation:** this validates JavaScript Proxy mechanics against a controlled mock only. It does not prove the hook can be installed before the actual game script, does not run against the live game, and does not verify actual PIXI.Application capture. The next live validation still requires a controlled document initialization. No game input, page reload, game API request, dispatch, or reward action was performed in this isolated test. Production action gates remain unchanged.

## 2026-10-30 - Runtime execution contexts mapped to the game frame

- A read-only CDP `Runtime.enable` observation collected 26 execution-context-created events from the existing DMM Kancolle WebView target. The game `htmlWrap` frame was correlated by frame ID `90DE57630652028567AE7FB3EC89DBE0` and origin `https://w14h.kancolle-server.com`; observed contexts were default/main-world ID `53` and Electron isolated-world ID `54`.
- This provides a concrete frame/context mapping for future diagnostics, but does not establish that a pre-init hook is registered, that it ran in the main world, or that a PIXI application instance was captured. CDP does not expose a general command to enumerate all scripts previously registered with `Page.addScriptToEvaluateOnNewDocument`; a returned identifier or contemporaneous logs are needed to track an individual registration. No refresh or game input was performed for this context enumeration. Production action gates remain unchanged.


## 2026-10-30 - Fleet-automation route reassessment and stop-loss gate

- Decision: optimize for the actual goal (reliable fleet automation), not continued reverse-engineering of the legacy game's rendering internals. Pause new CDP pre-init injection, PIXI interception, and lifecycle-tracker development unless the bounded feasibility review finds a concrete low-risk path to a needed capability.
- Existing-code inspection confirms the current Poi adapter already has a loopback-only, read-only MCP client allowlisted to `poi_describe`, `poi_get`, and `poi_lookup`, plus a read-only expedition reader that normalizes `info.fleets` / `info.ships` and checks source freshness when a trusted natural-response witness is available. These are existing implementation facts, not new test results from this review.
- Bounded review budget: at most one work round (about 60 minutes, and stop sooner when evidence is decisive). Prioritize existing Poi state/data interfaces and the minimum path needed for fleet decisions; consider CDP/PIXI only if an existing, low-risk access path is evidenced. No repeated page refresh, no new live-game script injection, no manual PNG/button-coordinate labeling.
- Stop conditions: no concrete low-risk entry point; instability requiring repeated refresh/injection; dependence on manual visual templates/coordinates; or evidence that proves only a rendering-engine trick without advancing fleet automation. On stop, preserve verified assets and redirect effort to the simplest existing interface path.
- Safety and verification boundary unchanged: this decision does not enable game actions. No dispatch, reward collection, game input, page refresh, or observer-initiated game request was performed for this decision. No new functional test is claimed here.


## 2026-10-30 - Stop-loss review closes current CDP/PIXI interaction route

- Read-only inspection of the installed Poi packages found `poi-plugin-forwarder@1.1.1`; its implementation forwards captured `/kcsapi/` responses and map-resource URLs to a webhook/optional KC3 relay. It is not a game-input or interaction server.
- A local read-only listener check found no listener on the previously suggested `127.0.0.1:38591`. This only describes the current port state; it does not establish that an interaction service is generally unavailable or that this port is the correct interface for the installed plugin.
- The existing `CdpInput` implementation requires a reviewed recorded point with an explicit coordinate and label; the accessibility locator requires a unique exact semantic name/role. Prior recorded AX inspection found no useful expedition/reward/dispatch labels in the Canvas-oriented game UI. Under the agreed stop-loss policy, do not proceed with this coordinate-recording or new PIXI/CDP injection route.
- Route decision: **STOP current UI reverse-engineering path**. Preserve existing CDP artifacts as research only; do not develop the proposed CDP lifecycle tracker or attempt another pre-init hook/reload. Continue only the bounded, lower-cost Poi state/data route, using existing read-only tools and captured evidence. The installed forwarder may be treated as a passive API-observation source, not as an action mechanism.
- Remaining hard blocker: the existing Python Poi client and expedition reader are read-only, and a reliable action interface for dispatch/reward collection remains `NOT_VERIFIED`. Do not claim the end-to-end expedition loop is solved. If the next bounded data-route review cannot establish mission-state semantics and a credible authorized interaction path without manual coordinate/template labeling, stop implementation and report the project as blocked rather than extending reverse engineering.
- Verification boundary: this was a read-only inspection of local package source, package metadata, and a local listener check. No functional tests were run in this review; no page refresh, script injection, game input, game-server request, dispatch, or reward collection occurred.


## 2026-10-30 - Bounded Poi data-route review: mission semantics and action path remain blocked

- Cross-source static evidence: the published KanColle raw API guide describes `api_mission` as `[state, mission_id, final_time, reserved]`, with states `0=idle`, `1=active`, `2=returned`, `3=recalled`, and describes the third slot as an epoch-millisecond time value. A separate public codebase, Harubridge ADR-0025 (2026-08-03), explicitly identifies `api_mission[2]` as a Unix-millisecond absolute expedition completion time and references Poi's countdown timer implementation. Historical reference: https://kancolle.fandom.com/wiki/User_blog:Rephira/Guide_to_reading_Raw_API_Data ; corroborating implementation analysis: https://github.com/bosshawk/Harubridge/blob/main/docs/adr/0025-clock-handling.md ; older independent parser also treats slot 2 as an absolute timestamp: https://gist.github.com/non117/cd59bdb7c8e4102a9a2e .
- Comparison with authoritative branch source `src/kancollebot/adapters/poi/expedition_state_v2.py` (blob `1a9a4cdf2b7da3f21c89a3d1a79b0588173f40e5`) found that the current normalizer accepts only statuses `0,1,2`, rejects `3`, and intentionally leaves `return_at=None`. Existing tests are synthetic fixtures; they do not establish current Taiwan-server runtime semantics. The published state mapping is useful evidence, but status 3 and any action-relevant interpretation still require a current sanitized runtime observation before code changes are treated as verified.
- A read-only inventory of 439 candidate JSON/JSONL/log/text/Markdown files under the installed Poi plugin directory, candidate Poi logs directory, and project docs found no saved runtime capture of `api_deck_port` / `api_mission` beyond the plugin's static test fixture and existing project documentation. Payload values were not printed. No new live request or game action was made.
- Action-path finding remains unchanged: installed `poi-plugin-forwarder@1.1.1` is a passive API-response/map-resource forwarder, not an interaction service; the Python Poi adapter remains read-only; no verified dispatch or reward-collection action interface is present. No safe autonomous next step can establish the missing transition evidence without either a manually operated game state being passively observed or reopening the stopped UI-injection/coordinate route (which is explicitly rejected by the stop-loss decision).
- **Decision: mark the single-expedition MVP BLOCKED, not complete.** Stop implementation work on mission-state expansion and action integration until one manually operated, passively observed expedition provides sanitized before/after `api_mission` evidence and a separate low-risk, authorized action mechanism is identified. Do not implement direct game-server calls or infer action success from timestamps. No page refresh, injection, game input, game request, dispatch, or reward collection occurred in this review; no functional tests were run and no code was changed.

## 2026-10-30 — Read-only CDP screenshot transport restored for the embedded game WebView

- Runtime `/json/list` evidence distinguishes the Poi shell (`type=page`, local `index.html`) from the embedded DMM KanColle game renderer (`type=webview`, URL `https://play.games.dmm.com/game/kancolle`). The existing page-only discovery selected the shell and could not discover the actual game WebView.
- The existing loopback-only `CdpWebSocketTransport` connected directly to the exact WebView's `webSocketDebuggerUrl`; `Page.getLayoutMetrics` and `Page.captureScreenshot` both returned valid results. The PNG signature and IHDR dimensions were validated: 1200×720, matching the CSS visual viewport 1200×720. Browser-level `Target.attachToTarget` was not required for this target in the observed runtime.
- Added `list_webview_targets()` and `connect_game_webview()` while preserving the existing page-target route. The game connector requires a unique match for the expected DMM KanColle URL and still uses the existing loopback-only, allowlisted transport; it does not call `Runtime.evaluate` or send mouse input. Added regression tests distinguishing the Poi shell, game WebView, and service worker.
- Verification: focused CDP tests passed (25/25); full Python unit suite passed (167/167); a live read-only screenshot through the new connector passed PNG validation and viewport-size matching. No page refresh, mouse input, dispatch, reward collection, or observer-initiated game API request occurred.
- Boundary: this verifies screenshot transport and image dimensions only. Screenshot scene classification, visual gate, coordinate calibration, mission-state transition semantics, and dispatch/reward action interfaces remain unverified. The single-expedition MVP remains BLOCKED.

## 2026-10-30 - Read-only screenshot repeatability verified

- Reconnected through the project's `connect_game_webview()` entry point to the exact embedded DMM KanColle WebView at `https://play.games.dmm.com/game/kancolle`; used only `Page.getLayoutMetrics` and `Page.captureScreenshot`. No `Target.attachToTarget`, JavaScript evaluation, mouse/keyboard input, page refresh, or game API request was used.
- Captured three consecutive PNG frames successfully. All had valid PNG signatures/IHDR and stable dimensions `1200x720`; the CSS visual viewport was `1200x720`, scale `1`, origin `(0,0)`. Frame SHA-256 values differed (`6bda16374a81cd12069f1f7fcb62b97bfed423770355aa73420638dd708877dd`, `6faef8f45593689daf3921aa55f77f000e1f7545742e40038a92d4ed1ba94030`, `43c49c1277c5f842b1f98ac61ffe4be87c57e352479063df8d91935f0e616110`), so only successful capture and dimensional consistency are established; pixel-identical frames and scene identity are not established.
- Result: screenshot repeatability at the transport/PNG/dimension level is VERIFIED (3/3). The actual scene has not yet been visually classified; home-port versus loading/black/modal rejection and coordinate mapping remain NOT_VERIFIED. MVP remains BLOCKED.
- The Python environment did not have Pillow available; no image-classification claim was made. No game actions or state changes were performed.

## 2026-10-30 - Captured frame visually confirmed as the rendered top-level menu

- Visually inspected one read-only PNG captured directly from the embedded KanColle WebView. The frame shows the rendered top-level menu with the three large choices for sortie, practice, and expeditions (Japanese labels were visibly present in the image). A black screen, loading overlay, or modal was not apparent in this particular frame.
- Scope: this is a single-frame manual visual observation, not an automated classifier or proof that the scene is stable across other states. No coordinates were recorded, no UI input was sent, and no screenshot containing account context was added to the repository.
- Follow the existing stop-loss decision: do not expand CDP/PIXI injection, template matching, or manual-coordinate interaction. Treat screenshot work as transport evidence only and resume the passive Poi data route. MVP remains BLOCKED on current-server mission transition evidence and a separately verified, authorized action mechanism.

## 2026-10-30 - Poi MCP cache exposes api_mission on fleet records

- The local `poi-nanodesu-mcp` health endpoint returned `status=ok` and `storeReady=true`; initialization succeeded and the expected read-only `poi_describe`, `poi_get`, and `poi_lookup` tools were available. No action tool was called.
- `poi_describe(info.fleets)` reported three fleet records and sample fields including `api_mission`. A read-only `poi_get(info.fleets)` returned three cached `api_mission` arrays, each `[0,0,0,0]` at observation time. This establishes the actual local bridge path and cached field shape, not current-server freshness or an expedition state transition.
- No dispatch, reward collection, mouse input, page refresh, or direct game-server request occurred. No source-freshness witness accompanied this read, so do not treat the idle-looking cache as authoritative live-server state.
- Next evidence prerequisite: one manually operated expedition must be passively observed before/after so the current server's mission transition can be established without this bot initiating a game action. MVP remains BLOCKED until this evidence and a separate low-risk authorized action mechanism exist.

## 2026-10-30 - Natural port-response freshness witness captured

- A fresh run of `tools/verify_poi_freshness_bridge.py --timeout-seconds 130 --max-age-seconds 120` completed successfully with exit code 0 and `status=FRESHNESS_WITNESS_MATCHED`; the observer captured a natural port response and matched it to the Poi Redux read source `info.fleets+info.ships`.
- The accepted observation contained three fleet records. Fleet 1 and fleet 3 were assessed as idle/ready based on the observed normalized state; fleet 2 was blocked because its active mission had no trusted return time. All three still had `dispatch_eligible=false`, and `game_actions_enabled=false` remained unchanged.
- This verifies one successful end-to-end freshness-witness capture through the existing passive observer and local Poi bridge. It does not verify a before/after expedition transition, action execution, or the single-expedition MVP. No game input, dispatch, reward collection, refresh, or observer-initiated game request was performed.

## 2026-10-30 - Hand read-only fleet snapshot confirmed

- Replaced the incorrect assumption that `window.getStore('info')` returns a Redux Store: the observed return value is a plain data object, so calling `getState()` on it is invalid. The `info` object exposes the expected partitions, including `fleets` and `ships`.
- A user-provided console result from the read-only Hand snapshot script enumerated all three fleet records and resolved all **14/14** referenced ship IDs against `info.ships`. The output showed fleet IDs/names, `api_mission` arrays, and ship IDs; no game action was invoked.
- **Verification boundary:** this verifies the local cached-object access and fleet-to-ship reference matching for that observed snapshot only. Polling Poi's cache does not prove that the values came from a fresh server response; the displayed mission states/timestamps are not accepted as authoritative transition evidence. No dispatch, reward collection, mouse/keyboard input, page refresh, or direct game-server request was performed.
- Status: read-only cache extraction **VERIFIED** for the supplied snapshot; source freshness and mission-state transition semantics **NOT_VERIFIED**. The single-expedition MVP remains **BLOCKED** pending a separately witnessed natural port response plus sanitized before/after mission-state evidence and a verified, authorized action mechanism.


## 2026-10-30 — Second natural port-response freshness witness captured

- Ran `python tools/verify_poi_freshness_bridge.py --timeout-seconds 130 --max-age-seconds 30` from the clean `mvp-cdp-screenshot-transport-20261030` worktree. The process exited with code 0 after 93.58 seconds and returned `status=FRESHNESS_WITNESS_MATCHED`, `source_freshness_verified=true`, `observer_status=captured`, and observation time `2026-10-30T08:58:52.336933+00:00`. The freshness witness matched the Poi Redux source `info.fleets+info.ships` and reported three fleets.
- Current normalized assessment: Fleet 1 was `ready_to_dispatch` as a candidate but remained `dispatch_eligible=false`; Fleet 2 was `blocked_damaged_ships`; Fleet 3 was `completed_awaiting_reward`. `game_actions_enabled=false` remained in effect for every fleet.
- This is a new successful natural-response freshness observation with a different observed fleet assessment from the earlier witness. It verifies capture and matching for this observation only; it does **not** prove a manually operated before/after mission transition, establish all `api_mission` slot semantics, or verify a dispatch/reward action interface. No game input, dispatch, reward collection, page refresh, or observer-initiated game API request occurred. The single-expedition MVP remains **BLOCKED**.

## 2026-10-30 — Manual-operation window: second fresh port snapshot and mission-state changes

- After the user's report of a normal manual fleet operation, ran `tools/verify_poi_freshness_bridge.py --timeout-seconds 130 --max-age-seconds 30` from the clean `mvp-cdp-screenshot-transport-20261030` worktree. It exited with code 0 after 27.04 seconds and returned `FRESHNESS_WITNESS_MATCHED`, `source_freshness_verified=true`, `observer_status=captured`, observation time `2026-10-30T09:02:26.931164+00:00`, source `poi-redux:info.fleets+info.ships`, and three fleets. The naturally occurring HTTP 200 `/kcsapi/api_port/port` response was received at `2026-10-30T09:02:25.370Z` and its body captured at `2026-10-30T09:02:25.724Z`; cache/service-worker/prefetch flags were all false.
- Compared with the prior compact natural-response snapshot received at `2026-10-30T08:58:50.375Z`, fleet 2's mission array changed from `[0,0,0,0]` to `[1,2,1793352564846,0]`; fleet 3 changed from `[2,3,1793350250783,0]` to `[1,1,1793351841447,0]`, and its ship membership changed. Fleet 1 remained `[0,0,0,0]`. This is direct evidence of changed fleet mission data across two independently captured natural responses during the manual-operation window; it does not by itself identify which individual action caused each fleet change or fully prove every mission-slot semantic.
- The current freshness-matched planning assessment marked fleet 1 `ready_to_dispatch` as a candidate, but `dispatch_eligible=false`; fleets 2 and 3 were `blocked_unknown_return_time`. `game_actions_enabled=false` remained unchanged.
- Safety boundary: the verifier only observed a naturally occurring response and compared it with the read-only Poi Redux state. No game input, dispatch, reward collection, page refresh, direct game API request, or runtime page evaluation was initiated by the observer. The single-expedition MVP remains **BLOCKED**: this is progress on fresh source matching and real mission-array transition evidence, not proof of an authorized dispatch/reward action interface or full closed-loop completion.

## 2026-10-30 — Normalize observed expedition timestamp milliseconds

- **Implementation status: IMPLEMENTED AND OFFLINE-TEST-VERIFIED.** Updated `src/kancollebot/adapters/poi/expedition_state_v2.py` to interpret the third `api_mission` slot as Unix epoch milliseconds and convert it to a timezone-aware UTC `return_at`. Fixed the missing assignment so the converted value is passed into `ExpeditionFleetSnapshot` (idle missions retain `None`). Conversion errors fail closed as `PoiStateError`; mission status 3 remains rejected, and this normalization does not enable dispatch.
- Evidence basis: two fresh natural `/kcsapi/api_port/port` captures showed third-slot values such as `1793352564846`, consistent with epoch milliseconds. A public historical API field guide also documents `api_mission` as `[state, mission, final_time, reserved]`, with states 0=idle, 1=active, 2=returned, 3=recalled and `final_time` in epoch milliseconds. The public guide is corroborating background, not a substitute for live verification of every edge case.
- Updated regression coverage for millisecond-to-UTC conversion, planner waiting behavior, and unrepresentable timestamps. Verification on 2026-10-30: targeted suites passed **10/10**, **13/13**, and **6/6**; full Python suite passed **168/168**; `compileall` and `git diff --check` passed. These are offline tests only. Runtime compatibility, returned-state timestamp semantics, and state 3 remain unverified. The MVP remains **BLOCKED** pending passive live validation and an independently verified authorized action mechanism; no game action was performed.

## 2026-10-30 — First UI-mediated live expedition dispatch verified

- **Live action result: VERIFIED.** On the logged-in Poi game UI, after collecting the prior returned expedition's result through the visible confirmation control, the read-only Poi Redux fleet state showed all three fleets idle. Fleet 2 contained four ships and was selected for expedition 3, `警備任務`.
- **User authorization and action boundary:** the user explicitly authorized game UI operations except direct packet sending. The dispatch was triggered with one left-button press/release via local CDP `Input.dispatchMouseEvent` against the visible game page. No game API method was called by the automation and no direct game-server request or packet was constructed/sent; the game client initiated its normal request in response to UI input.
- **Before/after evidence:** before the new dispatch, fleet 2 `api_mission=[0,0,0,0]`; fleets 1 and 3 were also idle. After the UI action, the game displayed `遠征に向かいました` (“departed on expedition”), and a read-only Poi Redux read returned fleet 2 `api_mission=[1,3,1793354078676,0]`; fleets 1 and 3 remained `[0,0,0,0]`. The third slot corresponds to `2026-10-30T09:54:38.676Z` as a millisecond Unix timestamp.
- **What this proves:** a real, user-authorized, client-mediated single-click dispatch can be performed and verified by a before/after state read. The previous returned mission's reward collection was also completed via the visible UI, followed by an idle-state read.
- **What remains pending:** the newly dispatched mission 3 has not yet returned. Therefore this is **not** full closed-loop acceptance for the same mission, and it does not yet prove automatic scheduling/re-dispatch by the project Bot. The next acceptance step is to observe this same fleet's mission-3 completion, collect its reward through the UI, verify idle state, then dispatch again and verify the new state transition. No tests or project source changes were made in this action round.


## 2026-10-30 - Fixed-fleet UI loop controller implemented (runtime pending)

- **Implementation status: IMPLEMENTED; offline tests PASS; live loop NOT_YET_VERIFIED.** Added a read-only Poi Redux fleet reader whose CDP transport accepts only one exact literal `Runtime.evaluate` expression returning fleet IDs, names, mission tuples, and ship membership. Arbitrary expressions, extra parameters, and other unallowlisted CDP methods remain rejected.
- Added `tools/one_expedition_ui_loop.py`, a bounded controller pinned to fleet 2 / mission 3. It requires `--authorize-ui-actions` and the exact expected active-mission timestamp; it waits for that same mission to return, attempts one UI reward-collection click, verifies the fleet becomes idle, then navigates the normal UI to re-dispatch and verifies a new active mission timestamp. It never constructs or sends a game-server request and never retries an ambiguous click.
- **Verification:** the live read-only fleet reader successfully returned fleet 2 `[1,3,1793354078676,0]` while fleets 1 and 3 remained idle. The full Python unit suite passed **175/175**; `compileall` and `git diff --check` passed.
- **Runtime limitations:** the controller has not yet been run through the same mission's return/collection/re-dispatch. The return-result confirmation coordinate is provisional and must be verified against the actual result screen; if post-click state does not confirm collection, the controller stops without retry. Do not describe the full loop as complete until live evidence is recorded.
- **Next step:** run the bounded controller against the already active fleet-2 mission 3 using its exact start timestamp, observe the return, verify collection and re-dispatch, and archive only the successful live outcome.


## 2026-10-30 - Return-time refresh gate correction (live verification pending)

- Implementation status: changed and syntax-verified; end-to-end NOT_YET_VERIFIED. The controller now allows the scheduled return time to trigger a visible UI refresh, then requires the same mission tuple to appear in returned status before the one reward-confirmation click.
- Verification so far: py_compile passed; fixed-expression fleet-reader tests passed 6/6. These checks do not validate the return-screen transition or reward-confirmation coordinate.
- Runtime remains IN_PROGRESS. The running process uses the previous script version and does not validate this correction. Do not claim loop completion until the updated live sequence is verified.


## 2026-10-30 - Returned-result navigation observation and stale-click correction

- **Live UI observation:** the fresh CDP WebView screenshot showed the mother-port screen and the Japanese return notification for fleet 2. Opening the visible Sortie control displayed `遠征 失敗！`; the result and bonus screens were advanced using their visible `次` controls, and the UI returned to mother port. Poi Redux subsequently showed all three fleets idle. The expedition itself failed; this is not recorded as a successful expedition or loop acceptance, and no re-dispatch was attempted.
- **Code correction: IMPLEMENTED; offline-verified.** In `tools/one_expedition_ui_loop.py`, removed the stale `HOME_PORT` click from the known mother-port return path. The Home Port navigation control is hidden while already at mother port, so clicking its saved coordinate was invalid; the controller now verifies the same mission tuple is unchanged and proceeds to the visible Sortie control.
- **Verification:** full Python unit suite passed **175/175**; `compileall` and `git diff --check` passed. This verifies offline regressions and syntax only. The changed live controller sequence has not been rerun, and automated visual screen classification/native-dialog handling and the full successful expedition loop remain NOT_VERIFIED.

## 2026-10-30 - Return/bonus navigation sequence adjusted (live verification pending)

- **Implementation status: IMPLEMENTED; offline-tested; LIVE NOT_VERIFIED.** Updated `tools/one_expedition_ui_loop.py` to reflect the observed two-page result flow: after the first visible `Next` is confirmed by the fleet returning to idle, the controller advances the observed bonus/items page once more before using the visible Sortie control. Removed the redundant Home Port click from the re-dispatch path because the observed sequence lands on mother port, where that navigation control is hidden.
- **Verification:** full Python unit suite passed **175/175**; `compileall` and `git diff --check` passed. The current change has not been exercised against a new live mission; the screen/page assumption is based on the prior observed result sequence and may differ by result type. Do not use this as unattended automation until live state/screen confirmation is added. Native error-dialog identification and safe cancellation remain NOT_IMPLEMENTED.


## 2026-10-30 — Accessible UI evidence gate added (runtime evidence pending)

- **Implementation status: IMPLEMENTED; offline-tested; LIVE NOT_VERIFIED.** Added `src/kancollebot/adapters/poi/ui_state_gate.py` to classify only explicit accessible-name evidence for mother port, expedition result, bonus/items page, or error dialog. Missing, conflicting, malformed, or generic-only evidence resolves to `UNKNOWN`; sensitive controller checkpoints now refuse input unless the expected state is uniquely supported by the WebView Accessibility tree.
- Error-dialog cancellation is not automated. The helper only reports a uniquely named accessible Cancel button when an error-dialog marker is present; it does not click it. Canvas-rendered dialogs may not appear in the Accessibility tree, so this is not a reliable native/game-dialog detector and cannot be treated as complete dialog handling.
- **Verification:** added 5 unit tests for explicit markers, ambiguity/unknown state, fail-closed gating, unique cancel-button requirements, and malformed trees. Full suite passed **180/180**; `compileall` and `git diff --check` passed. Python emitted environment-injected unittest argument usage warnings before the test summary; the test run itself ended `OK`.
- **Environment finding:** local Python 3.13.16 environment has no `tesseract`, `pytesseract`, Pillow, OpenCV, NumPy, EasyOCR, RapidOCR, WinRT/WinSDK, pywinauto, or comtypes module available. No dependency was installed and no live game input was sent.
- **Limitations / next step:** live Accessibility tree has not been inspected with the new gate, and the game's canvas may expose no usable labels. If the gate returns `UNKNOWN`, the controller will stop rather than guess coordinates. Next investigate a native Windows OCR/UIA path or add a reviewed, testable screenshot classifier before enabling any unattended result-page or re-dispatch clicks. The full live expedition loop remains NOT_VERIFIED.

- Live read-only Accessibility scan: `tools/inspect_accessible_targets.py` returned `NO_MATCHING_ACCESSIBLE_TARGETS` and reported that no input, JavaScript evaluation, or game action was sent. This establishes that the current Accessibility tree scan exposes no expedition-related labels; it does not establish that canvas-rendered controls or dialogs are absent. The state gate must therefore fail closed until a separate OCR/visual evidence provider is available.

## 2026-10-30 — Native Windows OCR capability inventory

- **Read-only environment verification:** On the authorized Windows device, Windows PowerShell 5.1 successfully resolved the WinRT types `Windows.Media.Ocr.OcrEngine` and `Windows.Graphics.Imaging.BitmapDecoder`. Reflection over `OcrEngine.AvailableRecognizerLanguages` returned `en-US`, `zh-Hans-CN`, `zh-Hant-HK`, and `zh-Hant-TW`; no Japanese (`ja` / `ja-JP`) recognizer was listed.
- **Conclusion / status: VERIFIED environment constraint; OCR image recognition NOT TESTED.** The native OCR API is present, but the currently enumerated recognizers do not include Japanese. It is therefore not yet a viable verified path for reading the game's Japanese labels. This does not prove Japanese language packs are impossible to add; no installation, elevated operation, or system configuration change was attempted.
- **Safety:** No screenshot was processed by OCR and no game input was sent. The existing accessible-text gate remains fail-closed; successful expedition automation and visual/native-dialog recognition remain NOT_VERIFIED.
- **Next step:** Evaluate a project-contained, optional OCR backend that can be installed without changing global Windows settings, or an explicit screenshot-state detector with fixture-based tests. Keep live UI actions disabled until recognition is validated on representative screenshots and each click is guarded by fresh state evidence.

## 2026-10-30 — Direct WinRT Japanese OCR engine creation check

- **Read-only runtime verification: VERIFIED.** Windows PowerShell 5.1 queried `OcrEngine.AvailableRecognizerLanguages` and then directly called `OcrEngine.TryCreateFromLanguage(Language("ja-JP"))`. The available list remained `en-US`, `zh-Hans-CN`, `zh-Hant-HK`, and `zh-Hant-TW`; `ja-JP listed: False`; the direct factory call returned `NULL / unavailable`.
- This strengthens the earlier inventory result: on this device and at this time, the native OCR API exists but cannot create a Japanese OCR engine. It does **not** prove Windows is inherently incapable of Japanese OCR; the language capability may be installable. A read-only `Get-WindowsCapability -Online` query could not run because the current session lacked the required permission, so the installed optional-capability state was not independently enumerated.
- **Clipboard note:** the remote PowerShell session could not access the clipboard; the result was displayed in the console only. No attempt was made to change permissions or system configuration.
- **Safety / scope:** no language pack was installed, no global settings were changed, no screenshot was passed through OCR, and no game input was sent. Actual Japanese image recognition remains NOT_TESTED.
- **Next step:** if the user elects to install the Windows Japanese OCR capability, rerun both the available-language enumeration and `TryCreateFromLanguage("ja-JP")`, then test a known Japanese fixture and a representative game screenshot. Until then, prefer a project-contained optional OCR backend for experiments; keep the existing UI state gate fail-closed and do not infer click success from OCR alone.


## 2026-10-30 — OpenCV region-scoped template-matching probe

- **Status: TESTED, preliminary; no production integration.** Installed OpenCV 5.0.0 and NumPy 2.5.4 only in a temporary directory and ran offline template matching against two existing 1200×720 screenshots. The project dependency manifest and source tree were not changed.
- Both screenshots were visually confirmed as home-port screens, not a home-port/expedition pair. In this pair, the selected top-navigation mission, top-navigation items, and sortie-menu regions scored 1.00000 at their same locations; the ship-art region scored 0.86783 at the same location (0.93811 best full-image score), and the background-window region scored 0.85482. Grayscale changed pixels were 19.85% at a threshold greater than 1 gray level and 14.67% greater than 5; mean absolute grayscale difference was 6.5547.
- **Verified conclusion:** region-scoped matching can remain stable where selected UI pixels are unchanged while unrelated ship/background regions vary. **Not verified:** scene discrimination, useful threshold, false-positive rate, viewport/theme robustness, or live integration. The fixtures cannot validate home-vs-expedition classification.
- **Safety:** offline screenshot analysis only. No game input, expedition dispatch, reward collection, page refresh, or production-code change occurred.
- Research details and per-region measurements: `docs/research/KCAUTO_CUSTOM_VISUAL_RECOGNITION.md`, section “OpenCV offline template-matching probe”.
- **Next:** obtain correctly labeled offline home-port and expedition-panel samples; test small panel-specific templates in bounded regions and evaluate positive/negative score distributions and ambiguity. Keep uncertain/conflicting evidence as UNKNOWN and do not authorize clicks from template scores alone.


## 2026-10-31 — Single-entry expedition controller hardening

- **Remote code artifact updated:** `tools/one_expedition_ui_loop.py` now starts from a verified idle fleet-2 state, attempts mission-3 dispatch within the same process, waits for the recorded return timestamp, follows the Sortie → top-left Home Port refresh path, collects only after mission-state checks, and requires a final idle-state readback.
- **Runtime output policy:** the controller writes append-only JSONL audit records to one configured log path; source checks found no screenshot API calls, PNG output, or explicit temporary/duplicate snapshot writes. Events include UTC timestamps and run IDs, and dispatch clicks have pre-action and wait-completion records.
- **Bounded recovery:** Home Port navigation retries are capped at three attempts and require the accessible UI gate to confirm the state. If it cannot confirm, the controller stops rather than proceeding blindly. After the Sortie step, the controller now forces a top-left Home Port click before retry/readback, matching the requested refresh sequence.
- **Validation:** the latest remote source was parsed by Python `ast.parse` successfully; source-level checks and GitHub readback verified the log, bounded retry, no-screenshot, and final-idle gate markers. Unit tests and live UI end-to-end acceptance were **not run**.
- **Limitations:** the current accessibility state gate may not expose labels from the game's canvas; return-result UI and coordinates are not verified, and automatic supply is not implemented. This is a saved, reusable but **unverified** implementation, not a successful live expedition run. No game input was sent in this work round.
- **Next:** solve screen-state confirmation without writing screenshots, test the menu/return-result gates and supply-needed decision, then run the authorized single-entry loop only after the preconditions are confirmed.

## 2026-10-31 — UI targeting inventory: supply / formation / repair / expedition

### Scope and evidence rules
- Inventory covers only resupply (補給), formation (編成), repair/docking (入渠), and expedition (遠征). Classify DOM, coordinates, and template matching separately; a DOM node existing is not proof that a game control has a directly operable semantic target.
- Evidence sources on this branch: `tools/one_expedition_ui_loop.py`, `src/kancollebot/adapters/poi/ui_state_gate.py`, `src/kancollebot/adapters/poi/visual_state_gate.py`, `tools/inspect_accessible_targets.py`, and `docs/research/KCAUTO_CUSTOM_VISUAL_RECOGNITION.md`.
- This is a source/document inventory, not a new live runtime test. No game input was sent.

### Current targeting inventory

| Module | Direct DOM / accessibility action | Recorded coordinates | Template matching | Status |
|---|---|---|---|---|
| Resupply | No stable semantic control target evidenced | No reviewed reusable map evidenced | No labeled, validated resupply templates evidenced | NOT_ASSESSED |
| Formation | No stable semantic control target evidenced | No reviewed reusable map evidenced | No labeled, validated formation templates evidenced | NOT_ASSESSED |
| Repair / docking | No stable semantic control target evidenced | No reviewed reusable map evidenced | No labeled, validated repair templates evidenced | NOT_ASSESSED |
| Expedition | The recorded accessibility scan returned `NO_MATCHING_ACCESSIBLE_TARGETS`; direct semantic operation is not established | Prototype records fixed points for home port, sortie, expedition mode, mission row/confirm, fleet 2 tab, start, and return confirm | `visual_state_gate.py` and the loop prototype can classify screenshots; offline probes are preliminary and labeled positive/negative sample coverage is insufficient for a production gate | PARTIAL / LIVE NOT_VERIFIED |

### Key distinctions
- The game is hosted in an accessible nested iframe and exposes a Canvas. This proves the document/frame can be inspected; it does not prove that individual game buttons exist as DOM elements.
- `ui_state_gate.py` reads accessibility-tree labels and fails closed on missing/ambiguous evidence. The recorded expedition-related scan found no matching accessible labels.
- `tools/one_expedition_ui_loop.py` contains fixed-coordinate mouse actions. Its own module docstring says the live-action prototype remains unverified; the full expedition loop is not accepted.
- Template matching has reusable code and preliminary offline measurements, but no validated general scene classifier or click authorization based solely on scores. Persistent navigation regions can match across scenes, so future templates must be panel-specific and evaluated against correctly labeled positive and negative samples.
- Poi/Redux structured snapshots are a separate read-only state-observation route, not DOM-based clicking. They can support preconditions and post-action verification once freshness and field semantics are sufficiently grounded.

### Next sequence
1. Investigate resupply, formation, and repair independently using read-only DOM/Accessibility structure and existing Poi state sources; record only actual semantic targets and their evidence.
2. Where no semantic target exists, inventory the minimal coordinate fallback only if it can be calibrated and guarded; avoid assuming all controls require templates.
3. Build small panel-specific visual templates only for controls/states that cannot be identified semantically or through reliable structured state. Keep screenshots in memory unless a separately authorized diagnostic requires a retained sample.
4. Add offline tests for target ambiguity, state preconditions, and fail-closed behavior; then perform a bounded live read-only scan. Do not claim a module is operational until its actual controls and resulting state transitions are verified.
5. Continue to treat dispatch, reward collection, supply, formation, and repair actions as separate live-action acceptance items. This inventory does not authorize or prove those actions.

### Verification status
- Inventory: source-reviewed against the listed branch files.
- New runtime tests: NOT_RUN.
- Live DOM target verification for the four modules: NOT_VERIFIED; expedition-related accessibility scan has the historical negative result stated above.
- End-to-end expedition and other-module automation: NOT_VERIFIED.


## 2026-10-31 — UI 操作来源与识别机制纠偏（证据分层）

### 用户补充的关键来源信息
- 用户明确补充：**当前讨论的 CDP 插件是 KCA（kcauto_custom）作者为 KCA 开发的插件**。此条来源信息来自用户的直接说明；本次 GitHub 远程源码检索未独立找到可引用的插件仓库/版本/提交，因此归档时保留其来源属性，不伪装成已完成上游源码核验。
- 这条信息修正了先前分析的缺口：不能把“CDP 插件的来源”与“KCA 的视觉模板匹配算法”混为一谈。CDP 插件是 KCA 作者开发的配套工具这一点，意味着后续追溯必须检查该插件的真实功能、暴露接口及历史调用记录；不能只根据 KCA 的 OpenCV 模板匹配文档推断 Poi 的 UI 操作机制。
- 仍须区分：CDP 是浏览器调试/控制通道；它可以承载 DOM/Accessibility 检查、页面状态读取或鼠标事件，但“使用 CDP”本身并不能证明具体目标是如何被识别的。KCA 的模板匹配研究属于另一条证据链，除非找到当时的调用记录，不得认定历史成功操作由模板匹配触发。

### 已有直接证据：Poi 真实远征派遣
- 2026-10-30 的实机记录已证明一次获授权的 UI 派遣成功：在可见游戏界面中通过本地 CDP 鼠标按下/释放事件触发派遣，之后从 Poi Redux 只读状态读回 fleet 2 的任务元组 `[1,3,1793354078676,0]`，并观察到游戏提示“遠征に向かいました”。操作前该舰队任务状态为 idle，其他舰队未改变。
- 这条成功记录证明“CDP 输入 + Poi Redux 状态读回”的操作/验收链曾真实成立；但当前证据尚未确定点击目标最初是由 CDP 插件提供的语义信息、人工观察后给定的坐标，还是其他定位过程产生。不可将后续 Python 坐标原型倒推为当时唯一执行工具。
- 后续 `tools/one_expedition_ui_loop.py` 是可复用的固定坐标控制器原型；它的完整返航、收获、再派遣闭环尚未通过实机验收。静态检查与离线测试不能替代 live E2E 证据。

### 用户报告的其他成功案例与证据边界
- 用户报告此前成功完成过远征收获、补给第二舰队（燃料由 11 增至 15，四艘舰船均达到 15），以及舰队人数不足时自动编入两位舰娘。
- 本次核对到的当前远程进度记录尚未找到这些动作的完整原始执行链、对应日志或逐项前后状态读回。因此这些案例应标记为**用户报告的历史成功、待追溯原始证据**，不能否认其发生，也不能在没有调用链证据时声称已确定实现机制。
- 现有 UI targeting inventory 是源码/文档范围的定位能力盘点，不是对历史实机行为的否定。将“当前没有审核过的可复用定位映射/没有 Accessibility 标签”概括为“功能没有实现”属于证据层级错误，后续报告必须纠正这种表述。

### 后续追溯要求
1. 优先检查 KCA 作者开发的 CDP 插件本体、版本/提交信息、插件接口和调用链；确认其是否暴露 DOM、截图、坐标、页面上下文或其他能力。
2. 对历史收获、补给、编成案例，分别寻找脚本、运行日志、工具调用记录以及动作前后的 Poi Redux / 舰船状态证据；按“直接执行证据、状态读回证据、用户回忆”分别标注。
3. 重建每个成功案例的完整链路：界面/状态如何被观察 → 控件如何被定位 → 输入如何触发 → 游戏客户端如何响应 → 如何独立读回确认。
4. 不得把 KCA 模板匹配研究、CDP 插件能力、KancolleBot 后续原型和已发生的 Poi 实机成功合并成未经证实的单一实现。未能找回日志只表示证据尚未找回，不表示操作未发生。
5. 不写入新截图堆积；优先复用现有日志、脚本、结构化状态和已有截图。任何未来实机动作仍须遵守既有授权和安全门禁。

### 本次归档状态
- 内容类型：纠正性正向进展与后续可复用追溯要求。
- 实机操作：本次未进行；未发送游戏输入。
- 测试：本次未运行项目测试；该记录是基于现有远程进度文档及用户补充信息的证据分层整理。
- 实现/验证状态：CDP 插件来源为用户直接确认、待上游源码独立核验；一次远征派遣已有历史实机验证；补给/编成/收获具体执行链待追溯；完整自动远征闭环仍未通过实机验收。


## 2026-10-31 — KCA source cross-check: built-in drivers and separate POI interaction mode

- Reviewed KCA's public `interaction_drivers.py`, `interaction_backend.py`, `nav/nodes.py`, `util/kca.py`, and `expedition/expedition_core.py`. Source confirms KCA's built-in `ChromeCdpDriver` sends mouse events through `PyChromeDevTools.ChromeInterface`, while `wait_and_click` waits for a fresh region-scoped template match and navigation then waits for a destination marker. Its expedition flow waits for the selection control, allows the fleet-selection animation to settle, selects the fleet, checks resupply, and only then clicks dispatch. This is useful implementation evidence, but does **not** identify the separate KCA-authored CDP plugin referred to by the user; that plugin's repository/version/call chain remains unverified.
- Separately checked KCA's documented POI interaction mode: the `feature/poi-interaction-server` variant of `poi-plugin-forwarder` exposes a loopback screenshot/input service, distinct from Chrome CDP. The local installed `poi-plugin-forwarder@1.1.1` has no `interaction-server.js` or `protocol.js`, and no listener was found on `38591`; do not assume the installed build provides that alternative service. No plugin was installed or changed.
- Offline baseline verification at source HEAD `265cf7cdbe0106c829123cf80a02c6ebb87142f5`: `PYTHONPATH=src python -m unittest discover -s tests -v` passed **194/194**; `compileall` and `git diff --check` passed. The later remote commits reviewed during archive reconciliation changed documentation only; these results do not establish live UI or end-to-end acceptance.
- Remaining implementation gap: `_dispatch()` still sends six clicks before checking the active mission tuple, and the current state gate does not classify every intermediate dispatch screen. No game input was sent during this source-review/archival round; full automated closed-loop acceptance remains unverified.


## 2026-10-31 — Blind-click dispatch guard implemented and offline-tested

- Replaced the unconditional six-click dispatch loop with an explicit `DISPATCH_STEPS` contract and a required per-step checkpoint verifier. The verifier must confirm the expected UI state before each click and, for the first five steps, after each click; any failed checkpoint prevents subsequent clicks. The final active mission is still confirmed by the read-only Poi Redux tuple.
- **Fail-closed behavior verified:** when no verifier is wired, `_dispatch` emits `dispatch_blocked` with `clicks_sent=0` and raises before any input. The current production `run()` intentionally supplies no verifier, so it cannot perform dispatch until all six UI transitions can be recognized without guessing or manually labeling new templates.
- **Offline tests:** 197/197 Python unit tests passed; `compileall` and `git diff --check` passed. Added tests cover zero clicks without a verifier, immediate stop after a failed post-click checkpoint, and successful mocked checkpoint ordering plus active-mission readback. The callback-success test is a mocked contract test, not live UI evidence.
- No game input was sent. The code change is a tested safety gate, not a completed or live-verified expedition loop.


## 2026-10-31 — Passive CDP Network Observer design audit

- Reviewed the current `cdp_websocket.py`, `cdp_input.py`, fleet snapshot reader, and related tests against the proposed zero-input Network observer.
- **Verified blocker:** the CDP transport currently discards event messages while waiting for a command response; Network domain methods are not allowlisted. Adding `Network.enable` alone is insufficient. The observer needs explicit event/response demultiplexing and a bounded event queue before live observation can work.
- **Security constraint:** request headers and post bodies may contain credentials or other sensitive data. Full headers/raw postData must not be logged; observer output must be path-filtered, bounded, and redacted. Missing `postData` is an unknown result, not proof that no request occurred.
- Added `docs/research/CDP_PASSIVE_NETWORK_OBSERVER_DESIGN.md` with the proposed architecture, offline test matrix, acceptance gates, and stage-2/3 visual-identification limitations. The document is explicitly DESIGN_ONLY; it does not claim the observer is implemented or tested.
- Visual review: a three-frame baseline and RGB/pHash threshold alone cannot identify mission 3 or fleet 2. Relative coordinates require viewport/scale/layout validation, and pixel changes prove only visual change unless selected/unselected states are empirically separable. Network payload is post-action evidence and cannot prevent a wrong dispatch.
- **Status:** design/source audit COMPLETE; observer implementation NOT_IMPLEMENTED; observer tests NOT_RUN; live network observation NOT_RUN. No game input, reload, request interception, or game API request was sent.


## 2026-10-31 — CDP passive reader draft and offline visual-difference probe (unverified)

- Updated `CdpWebSocketTransport` with a dedicated reader thread, command-response routing by active request ID, and passive `Network.*` event routing that continues while no command is pending. The in-memory event queue is bounded to 100 entries; oldest-event drops are counted, and `drain_events()` exposes only events sanitized before enqueueing.
- Added `Network.enable` / `Network.disable` to the method allowlist. These methods are not automatically invoked and no live session was opened. No request interception, replay, game API request, page reload, or game input was performed.
- Added a separate event-sanitization helper for known credential-bearing keys, sensitive URL query parameters, parseable JSON/form postData, and conservative redaction of unparsed bodies. This is a first-pass redaction layer, not a proof that arbitrary unknown secret formats are impossible; adversarial review remains required.
- Added offline test cases for event-before-response ordering, idle event routing, unmatched responses, a 1,000-event queue burst, and known sensitive fields. Added `tools/visual_diff_probe.py` plus synthetic 1200x720 image tests for identical-frame zero difference and a synthetic ROI glow measurement. The visual tool only reads caller-supplied image files; no game screenshot was captured.
- **Verification status:** changes and tests are committed to the authoritative GitHub branch and their contents were read back. Python tests, compile checks, and visual tests have **NOT BEEN RUN** in this session; GitHub commit status returned no checks, which is not a pass. No actual screenshots or live network events were collected.
- **Stage status:** implementation draft PRESENT; functional acceptance NOT_VERIFIED; live observation NOT_RUN; automatic expedition closed-loop acceptance remains NOT_VERIFIED. Do not use this draft to authorize live dispatch until local offline tests pass and the redaction/reader lifecycle is reviewed.


## 2026-10-31 — Offline CI gate added for passive observer draft (verification pending)

- Added `.github/workflows/passive-observer-tests.yml` on `dev/expedition-closed-loop-mvp`, triggered by relevant source/test/workflow changes or manual dispatch. It installs `.[vision]`, compiles changed modules, and runs the CDP transport and synthetic visual-difference unittest suites on Python 3.11.
- Remote read-back of the new workflow is pending in this entry; the workflow commit is `914418a35dcc4742854a6b10b59de78394475288`. The commit-status endpoint currently returns an empty status list, which does **not** prove that the workflow ran or passed.
- **Status:** CI definition committed; CI execution/result NOT_VERIFIED; tests NOT_VERIFIED; live game observation NOT_RUN; no game input performed. Next: verify workflow configuration and actual run result through an authoritative run record; fix any failures; only then mark offline stage-1 acceptance. Continue visual-difference validation with synthetic images first; real screenshot ROI calibration remains separate and must not be inferred from synthetic data.


## 2026-10-31 — Additional URL secret redaction coverage (unverified)

- Expanded the passive event sanitizer's URL-query redaction to cover parameter names containing token/key/password/passwd/secret/session/auth/credential/cookie, including camel-case names such as `accessToken` and `clientSecret`.
- Added regression assertions for camel-case query parameters. The offline CI workflow is configured to rerun on the changed test/helper paths, but the available commit-status endpoint still returns no checks; execution and pass/fail remain unverified.
- No raw traffic was captured, no game was controlled, and no live observation was started. Treat this change as an unverified security-hardening draft until the actual test run is retrieved and reviewed.


## 2026-10-31 — Strict Network event projection and body omission (unverified)

- Tightened `cdp_network_events.py` to project known Network events into a small allowlist: request URL/method, response URL/status/MIME type, request ID/timestamps, and limited load metadata. Arbitrary fields and all request/response headers (including cookies and Authorization) are excluded before queueing.
- Request body contents are not retained; only presence, length, and an omission marker are queued. This is intentionally stricter than attempting to redact arbitrary unknown body schemas. URL query values whose parameter names indicate tokens, keys, secrets, credentials, sessions, auth, cookies, or passwords are redacted, with camel-case regression assertions.
- Added/updated offline tests and a Python 3.11 GitHub Actions workflow. The helper, tests, and workflow have been read back from the authoritative remote. **No test execution result has been verified**: commit status has returned an empty status list; do not treat this as passing CI.
- Next gate remains: obtain an actual successful CI run (or run the exact commands in a verified local checkout), resolve all failures, and review reader lifecycle/queue bounds. No live Network.enable call, screenshot capture, reload, or game input was performed.
## 2026-10-31 — Passive observer CI recovered and offline regression gate verified

- Corrected the passive Network sanitizer's _TEXT regular expression quoting after the actual Actions job log exposed a Python SyntaxError at cdp_network_events.py:7. The earlier empty combined commit-status response was not evidence that Actions had not run: querying the repository's Actions runs collection revealed the actual run history. No repository Actions permission change was needed; checkout, Python setup, dependency installation, and test execution all ran normally.
- Hardened CDP transport failure handling: a command timeout, send failure, or reader failure now marks the transport unusable, and a timed-out transport rejects subsequent commands instead of sending another command into an ambiguous session.
- Added offline mock regression coverage for command timeout/no reuse, reader-thread exit after close error, malformed JSON frames, a synthetic 10 MiB frame rejected before body read, and URL redaction variants including api_token, multiple token parameters, and percent-encoded api%5Ftoken. Existing queue-burst coverage verifies the queue stays at 100 and counts dropped events; visual tests remain synthetic-only.
- Verified CI evidence: GitHub Actions run #10 (https://github.com/wosafety0/wosafety0clamp/actions/runs/41175023030), commit 883be9d7219e1f57c6ae2945535eca8ade367998, completed with conclusion success. The workflow installed .[vision], compiled the changed Python modules, passed all 16 CDP transport tests and all 3 synthetic visual-difference tests. The job and step results and the test log were read back from GitHub.
- Scope limit: this verifies offline tests only. No live Poi Network observation, live screenshot/noise-baseline collection, game input, dispatch/reward collection, or full expedition closed loop was performed. Real-frame noise thresholds remain unset until actual consecutive read-only frames are collected and measured.


## 2026-10-31 — Production startup fail-closed regression guard

### 已保存的改进

- `tools/one_expedition_ui_loop.py` 的 `run()` 在建立 Poi/CDP 会话前即拒绝启动，因为生产级逐步 UI checkpoint verifier 尚未接入；这样避免启动流程先执行母港恢复或其他 UI 输入后才被派遣门禁拦截。
- 在 `tests/test_one_expedition_ui_loop.py` 新增 `ProductionStartupSafetyGateTests.test_run_fails_before_opening_any_cdp_session`，通过 mock 断言 Poi 与游戏 WebView 会话都不会建立。
- 控制器改动提交：`996966c9469c6ab3c7bf0dc6cc3fb05f91724844`；测试改动提交：`c927518e932b41894460c05808233c23c3c01c4a`。
- GitHub 远程回读已确认控制器 guard 与测试方法存在。

### 验证边界

- **已验证：** GitHub 文件内容回读；新增测试方法确实存在。
- **尚未验证：** 新增测试实际执行结果；当前提交的 combined status 未返回 CI checks。
- **安全状态：** live UI loop 继续 fail-closed；未运行真实游戏操作。
- **下一步：** 在受控测试环境运行 `tests/test_one_expedition_ui_loop.py` 及完整测试套件，修复测试发现的问题；只有独立 UI 状态验证覆盖全部必要转换后，才考虑解除启动门禁。


## 2026-10-31 — Startup gate regression CI verified

- Read the authoritative GitHub Actions run records for the startup-guard and progress-archive commits. Both failed in the full unit-test step because the pre-existing CDP allowlist test still expected `Network.enable` to be blocked after passive Network observation had intentionally been allowlisted.
- Updated `tests/test_cdp_websocket_allowlist.py` to assert that `Network.enable` and `Network.disable` are permitted for passive observation while `Page.navigate`, `Fetch.enable`, and `Network.setCookie` remain blocked. Test change commit: `327035d6f6f55edfc3ff621bf5432431abe7e369`.
- Verified GitHub Actions run #1120 (`41176151196`, [run log](https://github.com/wosafety0/wosafety0clamp/actions/runs/41176151196)) completed successfully on that commit. The job compiled Python sources and ran the full unit-test suite: **210 tests passed, 3 skipped**. The log explicitly confirms both `test_passive_network_observation_is_allowed_but_mutation_methods_are_blocked` and `test_run_fails_before_opening_any_cdp_session` passed.
- Scope limit: this is offline CI evidence only. No live Poi Network observation, screenshot calibration, game input, expedition dispatch, reward collection, or complete live closed loop was performed. The production startup gate remains enabled.


## 2026-10-31 — Passive CDP observer facade implemented and CI-verified

- Added `src/kancollebot/adapters/poi/cdp_passive_observer.py`, an observation-only facade over the existing sanitized, bounded CDP Network event queue. It requires explicit exact URL paths, caps batch size and collection duration, and does not expose click/reload/evaluate/interception/replay methods.
- Added `tests/test_cdp_passive_observer.py` for exact path matching, event-type filtering, start/close lifecycle, configuration bounds, and failure handling.
- Extended `.github/workflows/passive-observer-tests.yml` to compile and test the new facade.
- Remote read-back verified the source, test, and workflow contents.
- **CI verified:** full CI run [41176600594](https://github.com/wosafety0/wosafety0clamp/actions/runs/41176600594) passed **216 tests, 3 skipped**. Dedicated passive-observer workflow [41176607303](https://github.com/wosafety0/wosafety0clamp/actions/runs/41176607303) passed 16 CDP transport tests, 6 observer facade tests, and 3 synthetic visual-difference tests.
- Updated `docs/research/CDP_PASSIVE_NETWORK_OBSERVER_DESIGN.md` to reflect implementation and verified offline status.
- **Boundary:** no live Network observation, screenshot capture, game input, game request, or end-to-end expedition loop was performed. The candidate `/kcsapi/api_port/port` path remains configurable and must be confirmed against naturally observed traffic. The production UI loop remains fail-closed; this observer does not authorize dispatch.
- **Next:** use a bounded, manually operated/read-only observation session to confirm the real endpoint path and sanitized before/after mission-state evidence. Until that evidence exists, do not infer mission transitions or enable automatic dispatch.


## 2026-10-31 — Read-only Poi Network observation CLI and target selection

- Added `tools/observe_poi_network.py`, a bounded local CLI that observes naturally generated Network events through CDP and prints only a minimal sanitized projection. It does not send UI input, reload the page, intercept/replay requests, or save raw headers/body contents.
- Added regression tests proving URL host/query and body content are omitted, malformed events are rejected, and non-finite metadata is excluded.
- Added configurable WebView URL-hint support to `connect_game_webview`; the CLI defaults to the previously observed `kcs2/index.php` URL pattern and allows an explicit override.
- **Verified:** full CI [41176759637](https://github.com/wosafety0/wosafety0clamp/actions/runs/41176759637) passed **221 tests, 3 skipped**, including the target-hint test. Dedicated offline observer CI [41176752672](https://github.com/wosafety0/wosafety0clamp/actions/runs/41176752672) passed 16 transport tests, 6 observer-facade tests, 4 output-projection tests, and 3 synthetic visual-difference tests.
- Remote source/test/workflow contents were read back. No live capture or game input was performed.
- **Next:** run the CLI locally during a manually initiated, naturally occurring game event to confirm the actual port endpoint path and capture sanitized before/after mission tuples. Until that evidence is obtained, no mission-state transition is inferred and the production UI loop remains fail-closed.


## 2026-10-31 — Corrected CDP preflight to require the game WebView

- **Implemented:** corrected the read-only CDP preflight to use `connect_game_webview` rather than the outer Poi page selector. It now requires exactly one embedded WebView matching the configured game URL and reports `target.type=webview` plus the URL hint. The default hint matches the observed DMM KanColle WebView URL; ambiguous or missing targets fail closed.
- **Runtime verification:** the preflight selected the single WebView at `https://play.games.dmm.com/game/kancolle` and read a 1200×720 CSS viewport. It sent no mouse/keyboard input, evaluated no JavaScript, and performed no gameplay action.
- **Automated verification:** added three regression tests for correct WebView selection, fail-closed behavior when no target matches, and invalid viewport rejection. The full Python suite passed **224/224**; `compileall`, both Node.js static test suites, and `git diff --check` exited successfully.
- **Scope boundary:** this fixes target identity in the diagnostic preflight only. It does not verify UI state recognition, a fresh port-response witness, supply handling, dispatch, reward collection, or the end-to-end expedition loop. Production game actions remain blocked by the existing startup/checkpoint gates.

## 2026-10-31 - Fresh port response captured after UI navigation

- **Runtime evidence:** while the game WebView was confirmed at 1200x720, ordinary visible UI navigation produced a natural `POST /kcsapi/api_port/port` response with HTTP 200. The response was received at `2026-10-31T07:27:53.156Z` and its body was captured at `2026-10-31T07:27:54.509Z`; disk cache, service-worker, and prefetch flags were all false. The observer did not initiate a game API request, send game input, or save the full raw response.
- **Sanitized state:** the response contained three fleets and 92 ship records. `api_mission` was `[0,0,0,0]` for fleets 1, 2, and 3. The freshness bridge independently returned `FRESHNESS_WITNESS_MATCHED` with `source_freshness_verified=true` and three fleet assessments.
- **Readiness boundary:** fleet 2 is currently assessed as blocked by damaged ships; fleet 3 needs resupply; fleet 1 is ready in the planning-only assessment. No fleet was dispatch-eligible and no mission action was taken. The fixed fleet-2/mission-3 MVP remains blocked until its damage/readiness preconditions are safely resolved.
- **Tool usability:** made `tools/verify_cdp_session.py` bootstrap `src/` so it can run directly from the repository root without an externally supplied `PYTHONPATH`. Direct runtime preflight selected the embedded DMM game WebView and verified a 1200x720 CSS viewport without sending input or evaluating JavaScript.
- **Automated verification:** the full Python suite passed **224 tests (3 skipped)**; the three CDP preflight regression tests passed; `compileall` and `git diff --check` exited successfully. These checks do not constitute end-to-end expedition-loop acceptance.

## 2026-10-31 - Pre-start fleet recheck blocks changed target state

- Implemented an additional fail-closed check immediately before the irreversible expedition-start click in `tools/one_expedition_ui_loop.py`: re-read the target fleet from Poi Redux and reject a newly active mission or changed ship membership compared with the initial idle state. This Redux read alone is not independent freshness evidence; the separate visual checkpoint remains mandatory.
- Added two regression tests in `tests/test_one_expedition_ui_loop.py` proving that either a changed ship membership or a newly active mission stops before the sixth (start) click.
- Offline verification: 226 Python tests passed (3 skipped), 17 Node.js tests passed, `compileall` and `git diff --check` passed. All dispatch test inputs were mocked; no live game action was performed.
- Production `run()` remains fail-closed before CDP connection. Visual checkpoint recognition and actual live-loop acceptance remain pending.

## 2026-10-31 - Standing user authorization for Poi and UI-mediated game operations

**User authorization record (standing, until revoked or superseded):** The user explicitly authorizes the assistant to autonomously perform **all Poi-related operations** and **all game operations except directly sending packets / game API requests**, without asking for approval for each action. The user expressly accepts the possibility of in-game ship loss (including sinking) and other game-resource consequences.

- **Authorized Poi scope:** Poi application/configuration/plugin operations, CDP debugging, screenshots, passive network observation, environment troubleshooting, and other Poi-related work.
- **Authorized game scope:** normal game UI operations, including navigation, fleet formation, resupply, repair, expedition dispatch, reward collection, sorties, and other UI-mediated gameplay actions; naturally generated network requests resulting from normal UI interaction are permitted.
- **Explicit exclusion:** do not directly construct, replay, or send game-server packets/API requests. Do not bypass this exclusion by scripting a direct request through CDP, JavaScript injection, or another transport.
- **Development scope:** continue autonomous implementation, project-local dependency management, tests, and approved GitHub progress archiving, subject to applicable repository rules and existing remote-write authorization.
- **Safety/verification distinction:** this authorization changes what operations the assistant may perform; it does **not** prove the production Bot's state recognition, freshness, or end-to-end correctness. Preserve engineering fail-closed checks until independently verified. Avoid unnecessary destructive changes outside the stated project scope.
- **Future handoff:** consult this standing authorization before requesting repetitive permission for Poi or ordinary game UI actions. Request a new decision only for an operation outside these boundaries or an otherwise independently required approval.

## 2026-10-31 - Explicit prohibition on treating segmented DC scripts as automation

- **Prohibition:** Do not describe, classify, report, or count a DC (Desktop Commander) script that is executed in separate segments/commands as automation merely because its segments are run one after another or because command output is collected.
- **Evidence standard:** DC segmented execution is an interactive/manual execution workflow unless a genuinely automated mechanism is independently demonstrated. Do not claim automation, unattended execution, an automated loop, or equivalent capability based only on segmented DC commands.
- **Reporting:** Distinguish clearly between manually issued DC commands, scripts executed as one complete invocation, and a verified automation mechanism. State only the level actually evidenced.
- **Scope:** This restriction applies to project implementation, testing, runtime operation, progress reports, and handoffs. It does not prohibit genuine automation implemented and verified through an appropriate mechanism; it prohibits misrepresenting segmented DC execution as automation.

## 2026-11-01 - Ooi.moe embedded game WebView identified in Poi

- **Live process evidence:** the authorized Windows device was online. Poi 12.0.1 was running from `D:\\Games\\poi\\poi.exe`; its Chromium remote-debugging endpoint was available on loopback port 9222. v2rayN and its Xray core were also running. This confirms process presence only, not a healthy Japan route.
- **CDP target/frame evidence:** `/json/list` exposed a Poi main page (`file:///D:/Games/poi/resources/app.asar/index.html`) and a child WebView titled `OOI - 舰娘在线缓存系统` at `https://ooi.moe/poi`. The WebView target was linked to the Poi page by its parent target ID.
- **Embedded game frame:** `Page.getFrameTree` showed the Ooi top-level document embedding a child frame named `externalswf`, whose game document origin/path is `https://ooi.moe/kcs2/index.php`. The query included the Ooi-provided `api_root=/kcsapi` parameter; credential/token query values were deliberately not retained in this record.
- **Main-world and Canvas evidence:** CDP `Runtime.executionContextCreated` exposed a default execution context for that embedded game frame. Read-only `Runtime.evaluate` confirmed document readiness is `complete`, exactly one Canvas and zero nested iframes are present, and the Canvas backing/client size is 1200x720 (the WebView inner window is 1280x800).
- **Interpretation boundary:** Ooi is not merely a normal DMM URL displayed as the top-level game page in this session; it inserts an Ooi-hosted WebView and an Ooi-hosted embedded game frame/proxy path. However, the embedded game's main-world context remains CDP-observable and the 1200x720 Canvas remains present. This does not yet prove Redux/state extraction, request observation/freshness, or the expedition Bot's end-to-end compatibility. For UI screenshots/input, select the containing Ooi WebView; keep Redux state reads on the separate Poi shell page. Frame-scoped selection is only needed for future frame-specific observations.
- **Safety and execution boundary:** all observations were read-only CDP metadata/DOM measurements. No game API request was constructed or sent, no input was injected, and no gameplay action was taken. DC commands were individual diagnostic invocations, not an automation mechanism.

## 2026-11-01 - v2rayN subscription and Xray profile failures diagnosed

- **Installed runtime:** read-only process inspection confirmed v2rayN 7.25.6 and Xray 26.10.29 are installed/running. The Xray process currently listens on loopback port 7890; process presence and a local listener do not establish a working Japan exit.
- **Subscription inventory:** read-only SQLite inspection of `guiConfigs/guiNDB.db` found three enabled subscriptions (`d`, `gemini`, `zou`) with 148, 123, and 11 profile records respectively (282 total). Subscription URLs, credentials, profile IDs, and server addresses were not read into the report.
- **Japan label evidence:** only one profile remark matched Japan/Japanese/Tokyo keywords (`JP`). Its profile-extra record reports delay `-1` and speed `0`; its stream-security, AllowInsecure, and certificate fields are empty. This does not establish that it is a tested or usable Japan endpoint.
- **Core failure evidence:** the 2026-11-01 v2rayN log repeatedly reports Hysteria2/Trojan profiles with unencrypted connections and `AllowInsecure` enabled without a certificate, then reports core startup failures. The log states these configurations cannot connect with Xray 26.2.6+; the installed Xray is 26.10.29. The startup log also records a DownloadService connection timeout at 08:16:11.
- **Interpretation boundary:** these findings strongly explain why many existing profiles cannot start and why the Japan-labeled profile has no successful delay measurement, but do not yet identify which subscription should provide a Japan node or prove that every Japan endpoint is unusable. No subscription URL, credential, server address, or profile secret was exposed or changed.
- **Safety:** inspection was read-only. No core downgrade, insecure TLS bypass, subscription edit, system proxy change, game input, or game API request was performed. Next step is to identify the subscription refresh failure and the exact Japan profile/group relationship without exposing subscription secrets, then choose a secure supported route.

## 2026-11-01 - JP-labelled profile traced to the gemini subscription

- A second read-only SQLite query mapped the only Japan-region label found in the 282 stored profiles (`JP`) to the enabled `gemini` subscription, which contains 123 profiles. The other enabled subscriptions contain 148 (`d`) and 11 (`zou`) profiles.
- No other profile remark matched Japan, JP, Tokyo, Osaka, Kyoto, Nagoya, Fukuoka, or their Chinese names. This is a remark-based inventory only; it does not inspect server endpoints or prove that unlabelled profiles cannot be Japan-based.
- The JP-labelled record has `ConfigType=3`, no `CoreType`, no measured delay (`-1`), zero measured speed, and no stream-security/certificate metadata. The associated subscription's stored `UpdateTime` is zero and `AutoUpdateInterval` is zero. These are stored metadata values, not proof of the provider's current service state.
- No subscription URL, password, UUID, server address, or token was read or disclosed. No subscription update or profile test was initiated.

## 2026-11-01 - Ooi WebView auto-selection added to Poi CDP adapter

- **Implementation:** added fail-closed automatic selection for exactly one supported KanColle WebView: direct DMM (`play.games.dmm.com/game/kancolle`) or Ooi (`ooi.moe/poi`). Ambiguous and unsupported target sets are rejected; explicit URL hints remain available. `connect_game_webview` and the read-only preflight now use this selection by default.
- **Runtime architecture verified:** the Ooi game runs inside the Ooi WebView child frame, while Poi Redux state is exposed by the separate Poi shell page. The expedition runner already maintains separate state and game sessions; the updated target selection now connects its UI session to the Ooi WebView without moving Redux reads into the Ooi frame.
- **Live verification:** the updated default `tools/verify_cdp_session.py` selected the actual Ooi WebView and passed a read-only 1200x720 layout preflight. Separately, the existing Poi shell session and fixed fleet snapshot reader successfully returned three fleet records. No mouse input, game action, or game API request was issued.
- **Offline verification:** full Python suite passed **230 tests (3 skipped)**; Node.js static tests passed **17/17**; `compileall` and `git diff --check` passed.
- **Acceptance boundary:** this verifies target discovery, Ooi WebView connection, viewport access, and Poi shell Redux access only. It does not verify fresh network-response observation through Ooi, visual checkpoint recognition on Ooi screens, or the expedition end-to-end loop. Production dispatch remains fail-closed.



## 2026-11-01 - Proxy egress country verified as Germany

- **Live egress evidence:** a read-only request sent through the local v2rayN HTTP proxy at `127.0.0.1:7890` successfully completed an HTTP CONNECT tunnel to Cloudflare's diagnostic endpoint. Cloudflare trace returned `loc=DE`, `colo=FRA`, `warp=off`. The tested proxy path therefore reported a Germany exit, not Japan, at the time of measurement. This CLI probe does not by itself identify which subscription/profile was selected or prove that Poi uses exactly the same route.
- **Local runtime:** v2rayN 7.25.6 and Xray 26.10.29 processes were present; the local proxy listener on port 7890 accepted connections. A listening port alone is not evidence of a Japanese route.
- **Interpretation:** combined with the previously recorded inventory (one JP-labelled profile under the `gemini` subscription, delay `-1`, speed `0`, no certificate/security metadata, and all three subscriptions showing stored `UpdateTime=0` / `AutoUpdateInterval=0`), there is currently no verified usable Japan endpoint in the local inventory. The subscription metadata alone does not establish provider expiry or current server availability.
- **Compatibility/security evidence:** local v2rayN logs repeatedly warn that `AllowInsecure` is enabled without a certificate. The installed Xray version is 26.10.29. The v2rayN maintainer's [official discussion #9460](https://github.com/2dust/v2rayN/discussions/9460) documents removal of Xray's `allowInsecure` option in v26.2.6+ and recommends valid certificate verification or certificate pinning. Do not work around this by disabling TLS verification or downgrading to an obsolete core.
- **Safety and next step:** no subscription was refreshed, no profile/database entry was modified, and no Windows system proxy setting was changed. To discover whether the existing subscriptions can supply a current Japan profile, the next step requires an authorized subscription refresh (after preserving a recoverable local configuration backup), then a sanitized inventory and a fresh Japan-exit test.

## 2026-11-01 - Ooi accessibility tree limits verified

- **Live read-only evidence:** while the Ooi-hosted game WebView was selected successfully and its 1200x720 viewport preflight passed, `Accessibility.getFullAXTree` returned only 5 nodes: one named `RootWebArea`, generic/none nodes, and one unnamed `EmbeddedObject`. No useful named game controls or state labels were exposed through the WebView accessibility tree.
- **Poi state separation reconfirmed:** the separate Poi shell Redux snapshot reader returned three fleets (fleet 1: 5 ships; fleet 2: 4 ships; fleet 3: 6 ships), all with mission status/id zero at observation time. This is a cached state snapshot, not a fresh server-response witness.
- **Implementation implication:** Ooi WebView selection and Poi Redux state access remain independently usable, but the current accessibility-based UI checkpoint gate cannot identify the game's canvas screens from the returned AX tree. Any visual recognition work must use transient in-memory captures or another independently verified signal; do not save bulk screenshots locally. Production dispatch remains fail-closed.
- **Safety:** no mouse input, JavaScript evaluation, screenshot-to-disk write, gameplay action, or game API request was performed during this check. The proxy/network configuration was not accessed or modified.

## 2026-11-01 - Ooi passive Network observer target selection corrected

- **Root cause:** the bounded passive Network observer CLI retained the old default URL hint kcs2/index.php. Under Ooi, that path belongs to the embedded child frame, while CDP exposes the containing https://ooi.moe/poi page as the selectable webview target. The observer therefore failed during target discovery even though the Ooi WebView preflight passed.
- **Implementation:** changed the observer CLI default to use the same fail-closed supported-target auto-selection as the CDP preflight (exactly one direct-DMM or Ooi WebView). Explicit --url-hint overrides remain available. Extracted build_parser() so defaults and overrides are directly testable.
- **Offline verification:** full Python suite passed **232 tests (3 skipped)**; Node.js static tests passed **17/17**; compileall and git diff --check passed. Two new tests cover automatic default selection and explicit URL override.
- **Live verification:** tools/verify_cdp_session.py selected the Ooi WebView and passed the read-only 1200x720 layout preflight. After the fix, tools/observe_poi_network.py connected without the previous CdpDiscoveryError, enabled passive observation, and completed its bounded 10-second capture cleanly with NO_MATCHING_EVENTS (zero matching natural port events while the game was idle).
- **Safety and acceptance boundary:** no game input, game action, direct game API request, request interception/replay, raw body/header saving, or screenshot-to-disk write occurred. The empty observation window verifies Ooi target discovery and observer lifecycle only; it does **not** prove that a fresh /kcsapi/api_port/port response has yet been observed through Ooi. Production dispatch remains fail-closed pending a naturally generated matching response, verified source-freshness witness, live visual checkpoint recognition, and end-to-end loop acceptance.

## 2026-11-01 - Explicit prohibition on segmented Desktop Commander scripts

- **Standing user constraint:** Do not present a sequence of separately issued Desktop Commander (DC) script fragments or segmented manual commands as an automated workflow, automation run, or completed automation result.
- **Required interpretation:** An automation milestone must be executed as one coherent, self-contained process/script with its own orchestration, logging, error handling, and verifiable completion evidence. Splitting a workflow into multiple DC calls and manually carrying state/output between them is not automation and must not be described as such.
- **Tool preference:** Prefer Personal-Windows-MCP and purpose-built connectors for local inspection and project work. Use DC only when necessary and within the user's authorization; DC availability does not relax this prohibition.
- **Reporting:** Clearly distinguish a one-off diagnostic command, a manually assisted sequence, and a genuinely automated end-to-end run. Never claim the latter based on the former two.

## 2026-11-01 - Ooi expedition menu click coordinate corrected

- **Live UI evidence:** on the actual Ooi-hosted 1200x720 game WebView, the Home Port sortie control at (280, 425) opened the sortie menu, and the expedition card at (1000, 360) opened the expedition list. The previous EXPEDITION_MODE coordinate (1105, 179) did not point to that card and was replaced with the observed (1000, 360).
- **Navigation sequence verified:** selected mission row 3 (警備任務) at (500, 335), confirmed the mission selection at (1030, 670), entered fleet selection, then returned to Home Port using (50, 50). The game was left at Home Port. No expedition-start control was clicked and no mission was dispatched.
- **Implementation:** updated tools/one_expedition_ui_loop.py and added a regression assertion for the Ooi expedition-card coordinate in tests/test_one_expedition_ui_loop.py.
- **Verification:** full Python unittest discovery passed **233 tests (3 skipped)**; Node.js static tests passed **17/17**; compileall and git diff --check passed. The Ooi read-only CDP preflight continued to select the Ooi WebView and report a 1200x720 viewport.
- **Acceptance boundary:** this verifies the observed Ooi menu/navigation coordinates only. The fleet-tab coordinate and all six visual state checkpoints are not yet independently verified; the Ooi accessibility tree exposes no useful game controls. Production dispatch remains fail-closed until every checkpoint and the end-to-end loop are verified.
- **Safety:** screenshots were captured and analyzed only in memory; no screenshot files were saved. No game API request was constructed or sent.

## 2026-11-01 - JP-LN selected and Japanese proxy egress restored

- **Confirmed root cause:** v2rayN had the `JP` subscription available, but its active profile was `IT-LN`. This explains why the proxy egress checks before the switch reported Italy rather than Japan.
- **Local profile inventory:** the current v2rayN database contains four enabled subscriptions and 289 profiles. The `JP` subscription contains seven regional profiles, including `JP-LN` (VLESS over TLS + WebSocket). Subscription URLs, server addresses, UUIDs, and credentials were not included in this record.
- **Change:** selected `JP-LN` as the default profile through v2rayN's own UI action. The saved active profile now resolves to `JP-LN` in subscription `JP`; the Xray core restarted. This was a one-off UI operation, not an automation milestone.
- **Live route verification:** the local proxy listener `127.0.0.1:7890` remained available. A request through it to Cloudflare's diagnostic endpoint returned **HTTP 200**, `loc=JP`, and `colo=KIX`, verifying Japanese proxy egress at the time of the check.
- **System configuration boundary:** the Windows system proxy remains enabled and pointed at the same local listener `127.0.0.1:7890`; its settings were not changed. No subscription refresh, profile rewrite, TLS-verification bypass, or core downgrade was performed.
- **Game boundary:** no game-server API request was constructed or sent, and no gameplay action was performed. Actual DMM/Ooi game login through this restored route has not yet been verified.
- **Combined post-switch check:** with `JP-LN` active and the proxy egress verified as Japan, Poi remained running and CDP still exposed the `webview` target titled `OOI - 舰娘在线缓存系统` at `ooi.moe/poi`. The route switch therefore did not remove the Ooi WebView. DMM authentication, the current child-frame game readiness, and Bot state extraction remain unverified. Earlier Germany/Italy egress readings are historical snapshots from before `JP-LN` was activated and do not describe the current verified route.

## 2026-11-01 - Ooi Fleet 2 tab coordinate corrected

- **Live UI evidence:** in the Ooi-hosted 1200x720 fleet-selection screen, selecting the Fleet 3 tab at (630, 170) visibly changed the selected fleet; selecting the Fleet 2 tab at (590, 170) returned to the Fleet 2 view with four ship cards. The separate Poi Redux snapshot independently reported Fleet 2 with 4 ships.
- **Implementation:** corrected FLEET_2_TAB in tools/one_expedition_ui_loop.py from (300, 100) to the observed (590, 170), and added test_fleet_2_tab_coordinate_matches_ooi_fleet_tab to tests/test_one_expedition_ui_loop.py.
- **Post-navigation state:** after returning to Home Port, the Poi Redux snapshot still showed three fleets with mission_status=0 and mission_id=0 (Fleet 1: 5 ships; Fleet 2: 4 ships; Fleet 3: 6 ships). No expedition was started.
- **Verification:** full Python unittest discovery passed **234 tests (3 skipped)**; Node.js static tests passed **17/17**; compileall and git diff --check passed.
- **Acceptance boundary:** the Fleet 2 tab coordinate is now live-verified for the current Ooi 1200x720 layout. The six visual state checkpoints and end-to-end expedition loop remain unverified; production dispatch remains fail-closed.
- **Safety:** screenshots were analyzed only in memory and not saved locally. No game API request was constructed or sent.


## 2026-11-01 — Ooi 视觉指纹识别原型（未完成视觉验收）

- **已实现并本地提交：** 新增无第三方依赖的 PNG 解码、RGB 网格指纹计算与相似度分类模块 `src/kancollebot/adapters/poi/visual_fingerprint.py`，专项测试 `tests/test_visual_fingerprint.py`，以及从 Ooi 当前 WebView 内存截图提取指纹的工具 `tools/capture_ooi_visual_fingerprint.py`。指纹数据位于 `src/kancollebot/adapters/poi/ooi_1200x720_fingerprints.json`。
- **原型数据范围：** 当前包含 HOME_PORT、SORTIE_MENU、EXPEDITION_LIST、MISSION_SELECTED、FLEET_SELECTION、FLEET_2_SELECTED 六组状态；每组使用 24×16 网格、1,152 个 RGB 数值，采样 3 次。截图仅在内存中处理，未保存原始截图文件。
- **标签可信度限制：** 数据中的 `label_source=manually_confirmed_current_screen` 只表示捕获时使用了人工确认的当前画面标签；六组标签、裁剪区域及分类阈值尚未逐一通过独立的现场视觉核验。当前六组指纹均为原型数据，不得作为生产环境可信 checkpoint，也不得据此放行派遣点击。
- **后续本地增量（本条记录时尚未提交）：** `EXPEDITION_LIST` 指纹重新采样，裁剪区域调整为 `[850, 140, 340, 520]`；新增一次性只读诊断工具 `tools/verify_ooi_visual_fingerprint.py`。该工具只捕获当前画面并在内存中分类，不导航、不点击、不保存截图。该脚本尚未经过完整独立验证。
- **当前画面诊断边界：** 一次实时诊断以 EXPEDITION_LIST 为预期状态，分类器返回 UNKNOWN：MISSION_SELECTED 得分 1.0，EXPEDITION_LIST 得分 0.94823，两者差值 0.05177，小于配置的最小区分度 0.06。该结果证明当前阈值会对这组相近匹配 fail-closed；它不证明当前画面的真实标签，也不构成任何状态识别准确率验收。
- **测试证据边界：** 本轮既有验证记录为 Python 243 passed / 3 skipped、Node.js 17/17、compileall 通过、git diff --check 通过；这些结果来自本轮先前执行，不能自动覆盖后续未提交的指纹重采样及诊断脚本。后续应在合并本地增量后重新运行相关验证。
- **生产状态：** 六阶段视觉 checkpoint 尚未完成逐项视觉核验；真实画面变化下的准确率、阈值鲁棒性、与现有 checkpoint verifier 的集成，以及端到端自动远征闭环均未验收。生产派遣保持 fail-closed。
- **安全边界：** 本轮指纹采集与诊断均为 CDP 内存截图读取；未构造、重放或发送游戏 API/服务器请求，未执行派遣或其他游戏点击，未保存截图文件，未修改代理或系统代理设置。


## 2026-11-01 — Ooi 现场视觉核验与派遣步骤安全复核

- **SORTIE_MENU 现场确认：** Ooi 1200×720 画面显示「出撃選択」及出撃、演習、遠征三个入口。对该人工确认画面重新采集 3 份内存样本（24×16 RGB 网格，裁剪区域 `[180,130,950,520]`）；紧接着的只读分类结果为 `SORTIE_MENU=0.99150`，次高 `HOME_PORT=0.73152`。该次截图未保存为文件。
- **EXPEDITION_LIST 现场确认：** 远征入口打开后，画面显示远征任务行列表，未选中任务时右侧详情区为空。一次即时分类结果为 `EXPEDITION_LIST=1.0`、`MISSION_SELECTED=0.94830`。随后重新采集时画面状态在连续检查间发生变化，使用新裁剪区域的后续分类未达到阈值，因此该次重新采集数据不作为已验收指纹。
- **MISSION_SELECTED 标签问题：** 指针停留在第 3 行时，行高亮并显示右侧任务详情；将指针移到中性区域后，高亮和详情消失。这是悬停预览，不是稳定的已选中状态。通过普通 UI 点击第 3 行后，界面直接进入舰队选择画面。因此 `MISSION_SELECTED` 不能按当前定义作为点击后的稳定 checkpoint；需重新定义为悬停预览或从生产派遣状态序列中移除，不能用它授权后续点击。
- **FLEET_SELECTION / FLEET_2_SELECTED 现场确认：** 点击第 3 行后出现舰队选择界面，默认显示舰队 3 的 6 艘舰船；点击舰队 2 标签 `(590,170)` 后，舰队 2 标签高亮并显示 4 艘舰船。两次操作均未点击远征开始按钮。当前指纹分类器对舰队 2 画面返回 `UNKNOWN`，最佳分数仅为 `HOME_PORT=0.72748`，故这些画面虽然可人工识别，指纹识别仍未通过验收。
- **关键安全风险：** 当前 `tools/one_expedition_ui_loop.py` 中 `MISSION_CONFIRM=(1030,670)`；现场舰队选择画面中的「遠征開始!」按钮覆盖约 `x=796..1045, y=640..700`，该坐标落在远征开始按钮内。若将当前悬停预览误判为稳定的 `MISSION_SELECTED`，旧步骤序列可能在舰队选择阶段提前触发派遣。现有 `run()` 仍在建立 CDP 会话和任何输入前直接 fail-closed；不得解除此门禁，必须先纠正状态序列和坐标定义。
- **当前本地工作树限制：** 指纹 JSON、`visual_fingerprint.py` 阈值及一次性验证脚本仍有未提交修改；验证脚本当前运行会因缺少 `time` 导入而报错。上述本地增量尚未纳入本次远程归档，也不应被视为已验证成果。保留这些改动，待逐项审查后再决定提交。
- **安全边界：** 仅进行了可逆的普通 UI 导航/选择及内存截图读取；没有点击「遠征開始!」，没有派遣舰队，没有构造、重放或发送游戏 API/服务器请求，没有保存截图文件，也没有修改代理设置。
- **验收结论：** 已获得 SORTIE_MENU 的一次正向指纹匹配，以及远征列表、舰队选择和舰队 2 选择的人工视觉证据；六组指纹尚未全部独立核验。MISSION_SELECTED 定义及旧派遣点击序列存在安全性问题，视觉 checkpoint 集成和端到端自动远征继续保持 BLOCKED / fail-closed。


## 2026-11-01 — 本地增量复核补充

- **脚本状态更正：** 上一条记录中的 `NameError: time` 是第一次运行时的实际结果。随后复读当前未提交文件，确认 `tools/verify_ooi_visual_fingerprint.py` 已出现 `import time`；修正后的脚本尚未重新执行，因此仍标记为未验证。该脚本会发送一次 `mouseMoved` 事件将指针移至中性位置，不点击、不导航、不保存截图；不应将其描述为完全无输入的纯只读脚本。
- **工作树测试复核：** 在设置 `PYTHONPATH=src` 后，对当时完整本地工作树重新执行：Python unittest **243 passed / 3 skipped**；Node.js **17/17 passed**；compileall、指纹 JSON 解析和 `git diff --check` 均通过。该结果仅覆盖离线测试及静态检查，不代表视觉状态分类或真实远征闭环验收通过。
- **归档范围：** 本次远程归档只包含已提交的视觉指纹原型提交及本进度记录；其余本地未提交源码、数据和诊断脚本继续保留在工作树中，不纳入本次推送。


## 2026-11-01 — 六个 Ooi 派遣前视觉 checkpoint 全部通过现场双帧核验

- **核验方式：** 使用当前生产路径 `_require_ui_state`，先尝试 Ooi Accessibility 证据；AX 树无可用游戏控件标签时，回退到 1200×720 内存截图指纹。每个预期状态最多采样 3 次，要求至少连续 2 次分类为同一预期状态；任何其他已知状态都会立即 fail-closed。诊断工具 `tools/verify_ooi_ui_state_gate.py` 只调用该门禁，不点击或导航；门禁会将鼠标移到中性位置以消除悬停干扰，不写入截图文件。
- **六个状态结果：** HOME_PORT（0.9989 / 0.9988）、SORTIE_MENU（1.0000 / 1.0000）、EXPEDITION_LIST（1.0000 / 1.0000）、MISSION_SELECTED（1.0000 / 1.0000）、FLEET_SELECTION（1.0000 / 1.0000）、FLEET_2_SELECTED（1.0000 / 1.0000）均在两次连续采样后返回 PASS。以上分数为各状态在本次现场画面中的匹配分数，不代表跨分辨率或跨主题泛化准确率。
- **EXPEDITION_LIST / MISSION_SELECTED 区分：** 在未选中任务的远征列表画面上，门禁将预期 MISSION_SELECTED 拒绝为 `FAIL_CLOSED`，实际识别为 EXPEDITION_LIST（1.0000；MISSION_SELECTED 竞争分数 0.9483）。点击第 3 行任务名称后，右侧任务详情及「決定」按钮出现；将指针移开后，行高亮消失，但详情和「決定」按钮仍保留。由此确认 MISSION_SELECTED 是稳定的任务已选状态；先前记录将详情也描述为随悬停消失，是不准确的观察，现以本条为准。
- **任务点击坐标修正：** 现场点击任务名称区域 `(350,350)` 可进入 MISSION_SELECTED；原 `MISSION_ROW=(500,335)` 未能稳定点击任务名称。已将其改为 `(350,350)`，并新增回归测试。
- **任务确认与舰队选择：** 在 MISSION_SELECTED 上点击 `MISSION_CONFIRM=(1030,670)` 的「決定」后进入舰队选择。界面进入时显示舰队 2；切换舰队 3 标签 `(630,170)` 后，FLEET_SELECTION 连续双帧通过（6 艘舰船）；切回舰队 2 标签 `(590,170)` 后，FLEET_2_SELECTED 连续双帧通过（4 艘舰船）。
- **坐标安全约束：** `(1030,670)` 是 MISSION_SELECTED 页面上的「決定」按钮，但在舰队选择页面会落入「遠征開始!」按钮区域。因此它只能在两次连续确认 MISSION_SELECTED 后点击；不得省略前置状态门禁。实际远征开始按钮 `(920,670)` 仍只允许在 FLEET_2_SELECTED 已确认后进入下一步骤。
- **实现状态：** `_require_ui_state` 已接入双帧 Ooi 指纹门禁；`build_checkpoint_verifier` 将每个 UI 步骤的前后状态检查接入 `_dispatch`。新增一次性诊断脚本 `tools/verify_ooi_ui_state_gate.py` 和 `tools/verify_ooi_visual_fingerprint.py`。指纹阈值为 minimum_score=0.97、minimum_margin=0.04；采集器和诊断器会先移动鼠标到中性位置，以避免将悬停状态误当作稳定状态。
- **生产门禁仍关闭：** `run()` 在建立 CDP 会话或发送任何输入前仍直接拒绝执行，因为 RETURN_RESULT 与 BONUS_ITEMS 两个返航/奖励 checkpoint 尚未完成独立核验。六个派遣前 checkpoint 通过，不等于自动远征闭环通过；本轮没有点击「遠征開始!」，没有派遣舰队。
- **离线验证：** 本地工作树在本轮坐标和安全注释更新前，Python unittest 执行 250 项且退出码为 0，Node.js 17/17 通过，compileall、指纹 JSON 解析及 `git diff --check` 通过。坐标修正和新增回归测试后的完整复测结果另见后续记录。
- **安全边界：** 仅使用普通 Ooi UI 导航/选择及 CDP 内存截图；没有构造、重放或发送游戏 API/服务器请求，没有保存截图文件，没有修改代理设置。上述单次诊断调用不是自动远征运行，也不计作端到端自动化验收。


## 2026-11-01 — 双帧 checkpoint 接入后的最终离线复测与安全收尾

- **最终离线复测：** Python unittest 执行 **251 tests**，结果 **OK（3 skipped）**；Node.js **17/17 passed**；compileall、指纹 JSON 解析和 `git diff --check` 均通过。该轮包含任务行坐标修正、MISSION_CONFIRM 前置状态回归测试及门禁注释修正。
- **收尾 UI 状态：** 未点击远征开始按钮；通过已验证的 Home Port 控件返回母港，随后生产状态门禁连续两次识别 HOME_PORT（0.9987 / 0.9981）并 PASS。游戏留在母港，舰队未派遣。
- **当前生产结论：** 六个派遣前视觉 checkpoint 均已通过现场双帧核验；RETURN_RESULT 与 BONUS_ITEMS 尚未核验，故 `run()` 仍在任何 CDP 连接或输入前拒绝执行。端到端自动远征仍为 BLOCKED，不得因前置六状态通过而启用。
- **自动化边界：** 本轮的六状态核验是逐状态调用的一次性诊断与普通 UI 导航，不是无人值守远征自动化；没有运行 `one_expedition_ui_loop.py --authorize-ui-actions`，没有派遣舰队。


## 2026-11-01 — Ooi 默认舰队状态修正与补给门禁现场验收

- **六状态现场核验：** 使用生产 `_require_ui_state` 双帧门禁，逐项确认 HOME_PORT、SORTIE_MENU、EXPEDITION_LIST、MISSION_SELECTED、FLEET_3_SELECTED、FLEET_2_SELECTED；每项均要求连续两帧识别为预期状态。所有截图仅在内存中处理，没有保存截图文件。
- **舰队状态命名修正：** 原 `FLEET_SELECTION` 指纹实际采自舰队 3 标签选中、显示 6 艘舰船的画面，现重命名为 `FLEET_3_SELECTED`；指纹向量及裁剪数据未改变。舰队 2 选中状态仍为 `FLEET_2_SELECTED`。
- **生产步骤纠正：** 现场确认点击 MISSION_CONFIRM 后，Ooi 默认直接进入 `FLEET_2_SELECTED`，而非通用 `FLEET_SELECTION`。因此 `DISPATCH_STEPS` 将任务确认的后置状态改为 `FLEET_2_SELECTED`，移除冗余的 Fleet 2 标签点击；完整点击序列由 6 次变为 5 次，其中前 4 次为导航/选择，最后一次才是远征开始。新增对应坐标和步骤顺序回归测试。
- **补给数据来源：** 在保持 CDP 只读表达式白名单的前提下，扩展现有固定 Poi Redux 快照：当前燃料/弹药取自 `info.ships` 的 `api_fuel` / `api_bull`，最大值取自 Poi 主数据 `const.$ships` 的 `api_fuel_max` / `api_bull_max`。字段映射参考 Poi 自身的远征补给检查实现：<https://github.com/poooi/poi/blob/master/views/services/sortie-expedition-resupply-check.ts>。没有开放任意 `Runtime.evaluate`。
- **补给门禁实现：** 新增 `PoiShipSupplyState`，解析每艘舰船的当前值与最大值；未知、缺失、不完整或超出上限的数据不会被当作满补。生产 `_dispatch` 在最后一次远征开始点击前重新读取 Fleet 2 快照，要求成员集合一致且所有舰船燃料、弹药均满补，否则拒绝点击。
- **现场补给结果：** Fleet 2 当前任务状态为 idle，4 艘舰船燃料均为 11/15；弹药分别为 20/20、15/15、20/20、20/20。4 艘舰船均未满足满补条件。
- **现场 fail-closed 验收：** 使用真实 Poi/Ooi 会话执行生产 `_dispatch`，实际完成出击菜单、远征入口、任务选择、任务确认 4 次普通 UI 导航；在最终远征开始点击前，门禁抛出 `Fleet 2 has 4 ship(s) not fully supplied; refusing to click`。额外诊断保护也禁止最终开始点击。随后重新确认 FLEET_2_SELECTED，返回 HOME_PORT 并通过双帧核验；前后 Poi 任务状态和舰船成员未变化，没有派遣舰队。
- **自动化日志：** 本次 live gate 事件写入本地忽略目录 `logs/ooi_supply_gate_live_20261101.jsonl`，逐项记录状态识别、UI 点击、等待、补给拒绝及返回母港；日志未纳入 Git。
- **离线验证：** Python unittest **259 passed / 3 skipped**；Node.js 测试进程退出码 0（17 项既有测试）；compileall、指纹 JSON 解析及 `git diff --check` 均通过。
- **生产门禁仍关闭：** 以上仅验收派遣前视觉状态、状态顺序和补给不足时的拒绝行为；没有验证满补时放行的真实派遣，没有点击远征开始。RETURN_RESULT、BONUS_ITEMS 及完整返航/收取/补给/再派遣闭环仍未独立验收，生产 `run()` 继续在建立 CDP 会话或发送输入前 fail-closed。
- **安全边界：** 没有构造、重放或发送游戏 API/服务器请求；没有保存原始截图；没有执行补给点击或消耗游戏资源；没有修改代理或系统代理设置。


## 2026-11-01 — RETURN_RESULT / BONUS_ITEMS 模板识别路径修复与现场负向核验

- **本地模板资产核查：** 恢复并检查 `D:\AI_Project\Project_KancolleBot\kancolle_templates`。成功/失败返航结果模板尺寸分别为 521×473、482×414；返航提示日文裁剪图 220×49；奖励标题「獲得ボーナス」裁剪图 221×69。以上素材位于本机独立目录，不在 Git 仓库中，调用方必须显式配置模板目录。
- **错误素材排除：** `result_next_button.png` 实际为 81×495 的纵向 FRIEND FLEET AREA 区域，不是奖励页下一步按钮。已从模板加载清单及 BONUS_ITEMS 正向条件中移除；当前仅允许真实奖励标题裁剪图作为 BONUS_ITEMS 的正向模板标记。
- **识别路径缺陷修复：** 原 `_require_ui_state` 仅在预期状态已存在于指纹 JSON 且已产生截图 payload 时才进入模板回退，导致不在指纹 JSON 中的 RETURN_RESULT / BONUS_ITEMS 无法被识别。现新增模板专用识别路径：通过 `--template-dir` 或 `KANCOLLEBOT_TEMPLATE_DIR` 指定目录，最多采样三帧，要求连续两帧命中预期状态；其他已知状态、未知帧、目录缺失均 fail-closed。生产 `run()` 启动拒绝门未解除。
- **诊断入口：** `tools/verify_ooi_ui_state_gate.py` 与 `tools/one_expedition_ui_loop.py` 均增加 `--template-dir` 参数，并在使用前验证目录存在。仓库没有写入机器专属模板绝对路径。
- **离线自动化验证：** Python unittest **263 tests passed**；Node.js **17/17 passed**；compileall、pip check 与 `git diff --check` 均通过。用内存合成 PNG 对成功结果、失败结果、返航提示和奖励标题进行正向模板测试，四项均分类为预期状态，精确模板分数均为 1.0。合成测试只证明素材加载及匹配管线，不代表真实游戏画面识别准确率。
- **真实 Ooi 负向现场核验：** 生产门禁连续两帧将当前画面识别为 HOME_PORT（指纹分数 0.9990 / 0.9990）。分别将 RETURN_RESULT 和 BONUS_ITEMS 作为预期状态调用模板专用路径，二者均正确识别当前画面为 HOME_PORT 并返回 FAIL_CLOSED。该结果验证了当前母港画面不会被误判为返航或奖励页，不是返航/奖励页的正向现场验收。
- **当前游戏状态：** Poi 只读快照显示舰队 1、2、3 的 `api_mission` 均为 `[0,0,0,0]`。Fleet 2 有 4 艘舰船，燃料均为 11/15；弹药分别为 20/20、15/15、20/20、20/20。当前没有正在返航的舰队，无法从现有画面获得真实 RETURN_RESULT / BONUS_ITEMS 正向证据。
- **本地视觉依赖：** 在被 Git 忽略的工作树 `.venv` 中安装项目已声明的可选视觉依赖：`opencv-python-headless 5.0.0.93`、`numpy 2.5.4`。没有修改 `pyproject.toml` 的依赖声明，也没有把虚拟环境纳入 Git。
- **生产及安全边界：** RETURN_RESULT / BONUS_ITEMS 的正向真实游戏画面识别仍未验收；生产 `run()` 继续在建立 CDP 会话和发送任何输入前 fail-closed。没有执行补给、派遣、收取奖励或其他消耗资源的游戏动作；没有保存截图；没有构造、重放或发送游戏 API/服务器请求；没有修改代理或系统代理设置。

## 2026-11-01 - Live Fleet 2 expedition return-result checkpoint verified

- **Authorized live operation:** Fleet 2 (four ships) was replenished through the normal Ooi UI and dispatched once on expedition 3 (Security Mission). Poi Redux readback confirmed the fleet was idle and all four ships were fully supplied before dispatch; after the start action, it confirmed `mission_status=1`, `mission_id=3`, and the expected completion timestamp. The observed fuel replenishment was 16 total units; ammunition was already full. No other fleet was operated.
- **Return completion:** after the scheduled completion time, Poi readback changed Fleet 2 mission status from active (`1`) to completed/awaiting result (`2`). The Ooi UI then presented the expedition result screen.
- **Live visual acceptance:** the production `_require_ui_state` gate, using the configured local template directory, classified the real screen as `RETURN_RESULT` in two consecutive in-memory frames. `expedition_result_success.png` matched at 0.9983 in both frames; the gate emitted `ui_state_confirmed` with evidence `two_consecutive_in_memory_template_matches` and the verification tool exited `PASS`. No screenshot was written to disk.
- **Acceptance boundary:** this is the first positive live verification of the RETURN_RESULT state. `BONUS_ITEMS` has **not** been live-verified; the normal UI transition after the result screen remains incomplete. The production `run()` startup fail-closed guard remains unchanged, and the full closed loop is not accepted. Do not infer bonus collection or final idle recovery from the RETURN_RESULT result alone.
- **Offline regression:** full Python unittest discovery passed **263 tests** (`OK`); the focused expedition-loop and visual-gate suites passed **36 tests**. These tests are separate from the live visual evidence.
- **Safety:** all game actions used the normal Ooi UI and were limited to the authorized single Fleet 2 replenishment and expedition. No game API/server request was constructed, replayed, or sent; no screenshots were persisted; no proxy/system-proxy setting was modified.


## 2026-11-01 - BONUS_ITEMS local asset pipeline verified; production guard wording corrected

- **Local asset pipeline verification:** using the actual operator-provided template directory, the classifier loaded the timestamp-named bonus-title crop (`221 x 69`) through its configured `*202556.png` discovery rule. An in-memory synthetic screen containing the actual crop classified as `BONUS_ITEMS` with a score of `1.0`. The existing success-result asset (`521 x 473`) likewise classified as `RETURN_RESULT` with a score of `1.0`. No screenshot or intermediate image was written to disk.
- **Interpretation boundary:** the prior live `0.5793` bonus-title score was measured while the observed screen was still `RETURN_RESULT`; it is a negative match on that screen, not evidence that the bonus template fails on a real bonus screen. Real `BONUS_ITEMS` positive live verification is still outstanding.
- **Production guard accuracy:** after live `RETURN_RESULT` verification succeeded, the startup guard's error message still claimed both return-result and bonus-item checkpoints were unverified. Updated the guard message and its regression assertion to identify only the remaining unverified `BONUS_ITEMS` checkpoint. The guard still rejects before opening CDP or sending input; it has not been relaxed.
- **Verification:** full Python unittest discovery **263 tests passed**; focused visual-gate/expedition-loop tests **36 passed**; local actual-asset positive controls passed for both states; `compileall` and `git diff --check` passed.
- **Acceptance boundary:** no game UI input was sent during this follow-up. `BONUS_ITEMS` remains unverified on a real game screen, the result-page transition remains blocked by the platform safety check, and the complete expedition loop remains unaccepted.

## 2026-11-01 - Reward-collection idle gate regression coverage

- **Regression coverage added:** `tests/test_one_expedition_ui_loop.py` now independently exercises `_wait_for_idle()` across four outcomes: an already-idle Fleet 2 is accepted immediately; a completed mission (`mission_status=2`) must transition to idle before success; an unexpected active mission is rejected immediately; and failure to observe idle within the bounded timeout raises without claiming reward collection success.
- **Verification:** the four focused tests passed; full Python unittest discovery passed **267 tests**; `compileall -q src tests tools` and `git diff --check` passed. This is offline controller-state coverage only and does not establish a live `BONUS_ITEMS` screen or a complete expedition loop.
- **Safety/acceptance boundary:** no game UI input, replenishment, dispatch, reward click, or API/server request was performed. The production `run()` guard remains fail-closed pending real-screen `BONUS_ITEMS` verification. No screenshots were written and no proxy settings were changed.

## 2026-11-01 - Bonus-title accessibility marker aligned with the actual UI label

- **Source inspection:** visually inspected the operator-provided 221x69 bonus-title reference. Its visible Japanese heading is `獲得ボーナス`. The accessibility classifier previously recognized `獲得資源` and `獲得アイテム` but omitted this actual title wording, so a renderer exposing the title through its accessibility tree could be incorrectly classified as `UNKNOWN`.
- **Implementation:** added the exact `獲得ボーナス` label as an explicit `BONUS_ITEMS` accessibility marker while preserving the unique-state requirement and fail-closed behavior. Added a regression assertion for that label.
- **Verification:** focused UI-state and visual-state gate tests passed **14 tests**; full Python unittest discovery passed **269 tests**; `compileall -q src tests tools` and `git diff --check` passed.
- **Acceptance boundary:** this is an offline accessibility-classifier correction based on the known title asset. It does not establish that the live renderer exposes this accessible label, does not change the visual-template threshold, and does not verify a live `BONUS_ITEMS` screen. No game UI input, API/server request, replenishment, dispatch, or proxy change was performed; the production `run()` guard remains fail-closed.

## 2026-11-01 - Reward-screen ordering and idle-evidence semantics corrected

- **Controller ordering correction:** review found `_wait_for_idle()` ran immediately after advancing `RETURN_RESULT`, before `BONUS_ITEMS` was verified. Because Poi can report Fleet 2 idle while the result/bonus UI is still pending, that ordering could log an idle observation before the reward screen had even been confirmed. The reward sequence is now isolated in `_collect_returned_expedition_reward()` and ordered as: advance the already-verified result screen; require `BONUS_ITEMS`; advance the bonus screen; then check fleet idle and recover/verify Home Port.
- **Evidence wording:** the idle event is now `fleet_idle_confirmed_after_bonus_advance`, and the bounded timeout reports failure to observe idle after the bonus advance. Fleet idle remains supporting state evidence, not standalone proof of reward collection; the bonus-screen gate and Home Port recovery remain separate requirements.
- **Regression coverage:** added an offline orchestration test that asserts `BONUS_ITEMS` is checked after the result-screen click and before the bonus advance, idle check, and Home Port recovery.
- **Fail-closed regression:** added a second orchestration test proving that a failed `BONUS_ITEMS` check prevents the second click, idle check, and Home Port recovery.
- **Correlation metadata:** the reward helper now carries the dispatched mission completion timestamp through to the final ready event; it no longer substitutes the post-return idle tuple's zero timestamp. The orchestration test asserts the original timestamp is retained.
- **Verification:** full Python unittest discovery passed **269 tests**; focused reward-ordering/idle-gate tests passed **6 tests**; `compileall -q src tests tools` and `git diff --check` passed. The actual operator bonus-title template was read successfully with Unicode-safe image decoding and measured `221 x 69`; this is asset inspection only, not live-screen verification.
- **Acceptance boundary:** no game UI input, replenishment, dispatch, or reward action was performed in this stage. Real `BONUS_ITEMS` live verification is still outstanding; the production `run()` startup guard remains fail-closed. No game API/server request or proxy change was made, and no screenshot was saved.

## 2026-11-01 - UI checkpoint evidence source propagated accurately

- **Issue found:** `_require_ui_state()` emitted the correct evidence source (`accessibility`, two-frame Ooi fingerprint, or two-frame visual template), but `build_checkpoint_verifier()` hard-coded every successful UI checkpoint as a two-frame Ooi fingerprint. This made checkpoint logs inaccurate whenever accessibility or template evidence was used.
- **Implementation:** `_require_ui_state()` now returns the evidence source only after the requested state has passed its existing gate. `build_checkpoint_verifier()` propagates that exact source into `dispatch_checkpoint_confirmed`. The state requirements, sampling counts, thresholds, click sequence, and fail-closed behavior are unchanged.
- **Regression coverage:** added assertions for accessibility evidence, Ooi fingerprint evidence, visual-template evidence, and propagation into the dispatch checkpoint event.
- **Verification:** focused one-expedition controller tests passed; full Python unittest discovery passed **270 tests**; `compileall -q src tests tools` and `git diff --check` passed.
- **Acceptance boundary:** this is an offline logging/evidence-provenance correction only. It does not verify the live `BONUS_ITEMS` screen or the complete expedition loop. No game UI input, replenishment, dispatch, reward action, API/server request, or proxy change was performed; production `run()` remains fail-closed.

## 2026-11-01 - Operator-confirmed reward-page return and live Home Port recovery

- **UI transition:** the operator confirmed the Ooi client was on the reward page and manually advanced it through the normal visible UI; the operator then confirmed the client had returned to Home Port. No automated click was sent for this transition.
- **Live Home Port verification:** after the return, `tools/verify_ooi_ui_state_gate.py --expected HOME_PORT` passed the production checkpoint gate on two consecutive in-memory frames. The observed state was `HOME_PORT` with fingerprint scores `0.9987` and `0.9989`; evidence was `two_consecutive_in_memory_ooi_fingerprints`. The diagnostic exited with code 0 and reported `screenshot_written_to_disk=false` / `screenshot_saved=false`.
- **Poi read-only state:** a fresh fixed Redux snapshot confirmed Fleet 1, Fleet 2, and Fleet 3 all had mission tuple `(0, 0, 0, 0)`. Fleet 2 membership remained ship IDs `1, 4, 8, 25`; fuel remained `11/15` for all four ships, and ammunition remained `16/20`, `12/15`, `16/20`, `16/20` respectively. No replenishment or dispatch was performed during this verification.
- **What this establishes:** the operator-driven reward-page-to-Home-Port transition is complete, Home Port is positively verified, and all three Poi fleet mission states are idle in the captured snapshot.
- **What this does not establish:** the `BONUS_ITEMS` checkpoint was not independently verified by the production two-frame gate before the operator advanced the page. Fleet-idle state does not prove reward collection. Therefore the live `BONUS_ITEMS` acceptance gate remains open, the production `run()` startup guard must remain fail-closed, and the full automated expedition loop remains unaccepted.
- **Safety:** verification used the existing read-only Ooi visual gate and fixed Poi Redux snapshot only. No game API/server request was constructed, replayed, or sent; no screenshot was persisted; no game resources were consumed; no proxy or system settings were changed.


## 2026-11-01 - Fleet 3 four-ship formation and live supply snapshot coverage

- **Operator fleet adjustment:** after the operator refreshed the game data through the normal Sortie-to-Home-Port UI route, the fresh Poi Redux snapshot showed Fleet 3 configured with four ships (instance IDs `6, 52, 32, 63`) and two empty slots. Fleet 2 and Fleet 3 both reported idle mission tuples `(0, 0, 0, 0)`.
- **Read-only adapter extension:** expanded the existing exact, allowlisted Poi Redux snapshot expression to collect current/max fuel and ammunition for Fleet 3 as well as Fleet 2. The adapter remains a fixed read-only snapshot; arbitrary `Runtime.evaluate`, game API requests, and mutation methods were not added.
- **Live Fleet 3 supply evidence:** all four Fleet 3 ships were observed at fuel `11/15` and ammunition `9/15`; none is fully supplied. Fleet 2 remains at fuel `11/15` for all four ships and ammunition `16/20`, `12/15`, `16/20`, `16/20`; none is fully supplied. These values are a point-in-time Poi cache snapshot, not a damage/repair assessment.
- **Regression coverage:** added a four-ship Fleet 3 fixture proving member-aligned supply parsing and per-ship full-supply classification.
- **Verification:** focused fleet-state suite **11 tests passed**; full Python unittest discovery **271 tests passed**; `compileall -q src tests tools` and `git diff --check` passed. A fresh live Poi read confirmed the updated Fleet 3 membership and supply fields through the extended fixed snapshot.
- **Operational boundary:** no replenishment or expedition dispatch was performed in this change. Both fleets remain idle and under-supplied, so neither is currently ready for the proposed dispatch. The existing single-Fleet-2 production loop and its unverified live `BONUS_ITEMS` fail-closed guard remain unchanged; no multi-fleet dispatch automation is claimed.
- **Safety:** no game API/server request was constructed, replayed, or sent; no screenshot was persisted; no proxy or system settings were changed.
## 2026-11-01 - Fleet 2/3 live expedition eligibility and HP preflight

- **Fresh read-only Poi observation:** at 2026-11-01 17:58:18 +08:00, Fleet 2 and Fleet 3 both reported idle mission tuples `[0,0,0,0]`. Fleet 2 contained four destroyers (ship IDs `1,4,8,25`, levels `8,4,4,4`); Fleet 3 contained four destroyers (ship IDs `6,52,32,63`, levels `2,1,1,1`).
- **HP observation:** Fleet 2 HP was `13/16,13/13,15/15,16/16`; Fleet 3 HP was `13/13` for all four ships. Fleet 2's first ship is below maximum HP but remains at 81.25% HP; this snapshot does not classify it as a damage state. Fleet 3 is at full HP.
- **Supply observation:** Fleet 2 fuel was `11/15` for all four ships and ammunition was `16/20,12/15,16/20,16/20`. Fleet 3 fuel was `11/15` and ammunition `9/15` for all four ships. Both fleets require replenishment before dispatch.
- **Mission requirement cross-check:** the current expedition reference lists mission 1 (練習航海) as minimum 2 ships, any ship type, flagship Lv1; mission 3 (警備任務) as minimum 3 ships, any ship type, flagship Lv3. Source: https://wikiwiki.jp/kancolle/遠征 . Fleet 2 meets the observed mission-1 count/type/flagship-level requirements. Fleet 3 meets count/type but its current flagship is Lv2, below mission 3's Lv3 requirement.
- **Decision boundary:** do not dispatch Fleet 3 in its current formation. A Lv3+ destroyer must become its flagship; Fleet 2 can retain mission-1 eligibility with its current Lv8 flagship. Formation changes and replenishment have not been performed. No repair is indicated by this HP snapshot.
- **Safety and scope:** this was read-only Poi MCP data retrieval; no game UI input, replenishment, repair, formation change, dispatch, reward action, or game API/server request was performed. No screenshot was persisted and no proxy/system setting was changed. This preflight does not verify the full automated expedition loop or the production BONUS_ITEMS gate.


## 2026-11-01 - Offline mission-composition eligibility evaluator

- **Implemented:** added `src/kancollebot/expeditions/mission_eligibility.py`, a pure offline evaluator for minimum ship count, flagship level, and allowed ship types. Requirements are explicit caller-supplied data; the module does not hard-code game mission rules, read Poi, mutate formations, or perform UI/game actions.
- **Input safety:** composition snapshots require aligned tuples of unique positive ship IDs and positive integer levels/type IDs; mission requirement values and allowed type IDs are validated, including rejection of booleans masquerading as integers.
- **Regression coverage:** added 10 tests covering the observed Fleet 2 composition satisfying a supplied mission-1 requirement, the current Fleet 3 flagship level blocking a supplied mission-3 requirement, a qualifying flagship promotion, insufficient ship count, disallowed type, empty fleet, and malformed inputs.
- **Verification:** focused tests **10 passed**; full Python unittest discovery **281 passed**; Node static suites **17 passed**; `compileall -q src tests tools` and `git diff --check` passed.
- **Limitations:** this is a composition-only planner primitive. It does not verify current live game state, supply, damage, mission selection on the UI, or actual dispatch acceptance. The requirements used in tests are fixtures based on the separately recorded mission reference, not a bundled authoritative game database. No live game action was performed.


## 2026-11-01 - Poi live composition metadata for mission preflight

- **Read-only adapter extension:** the fixed Poi Redux snapshot now includes each Fleet 2/3 member's level and master ship-type ID alongside existing supply fields. The allowlisted expression remains exact and read-only; no arbitrary evaluation or mutation capability was added.
- **Parser hardening:** `PoiShipSupplyState` now carries optional level/type metadata; when present, zero or malformed values are rejected. Existing supply-only fixtures remain compatible.
- **Live integration check:** the real Poi snapshot populated all four Fleet 2/3 members with level/type metadata. The offline evaluator applied the supplied mission requirements to those live observations: Fleet 2 → mission 1 returned `eligible`; Fleet 3 → mission 3 returned `blocked_flagship_level` because its Lv2 flagship is below Lv3. Both fleets remained idle in the snapshot.
- **Verification:** focused adapter + mission-eligibility tests **22 passed**; full Python unittest discovery **282 passed**; Node static suites **17 passed**; compileall and `git diff --check` passed.
- **Limitations:** live data is Poi's cached Redux snapshot, not a server freshness witness. The evaluator does not alter fleet order, supply, repair, or dispatch. No game UI input or resource-consuming action was performed; production `BONUS_ITEMS` fail-closed behavior remains unchanged.


## 2026-11-01 - Read-only dual-fleet expedition preflight

- **Implemented:** added `src/kancollebot/expeditions/expedition_preflight.py` to combine the live Poi fleet snapshot, the pure mission-composition evaluator, fleet-idle state, and member-aligned supply metadata. Added `tools/verify_expedition_preflight.py` for the current Fleet 2 → mission 1 / Fleet 3 → mission 3 assignments.
- **Fail-closed checks:** missing/duplicate supply members, incomplete composition metadata, non-idle fleets, composition failures, and unsupplied ships are reported distinctly. The default project policy requires every member to be fully supplied; this is a conservative readiness policy, not a claim that the expedition game rule itself requires 100% fuel/ammunition. The command exits 2 when live preconditions block dispatch; this is an intentional blocked-preflight result, not a runtime error.
- **Mission rule correction:** mission 1 and mission 3 requirements do not restrict ship type in this plan, so their requirement records now leave `allowed_ship_types=None`. A separate evaluator test retains coverage for an explicitly supplied ship-type restriction.
- **Live read-only result:** at `2026-11-01T10:11:10.879951Z`, both fleets were idle. Fleet 2 → mission 1: composition `eligible`, but full-supply policy blocked all four members (fuel `11/15`; ammunition `16/20, 12/15, 16/20, 16/20`). Fleet 3 → mission 3: composition blocked because flagship Lv2 is below Lv3; full-supply policy also blocked all four members (fuel `11/15`, ammunition `9/15` each). The report identifies both blockers for Fleet 3 rather than hiding its supply state behind the composition failure.
- **Verification:** focused preflight + mission eligibility tests **21 passed**; full Python unittest discovery **293 passed**; Node static suites **17 passed**; `compileall -q src tests tools` and `git diff --check` passed.
- **Safety and limitations:** the tool reads only Poi's cached Redux snapshot through the existing fixed read-only expression. It does not change fleet order, replenish, repair, dispatch, navigate the UI, or send game API/server requests. The cache is not an independent server-freshness witness. No screenshot was saved and no proxy/system setting was changed. The production single-fleet runner and its `BONUS_ITEMS` fail-closed guard remain unchanged.


## 2026-11-01 - Safe flagship-exchange planner for the two-fleet assignment

- **Implemented:** added `src/kancollebot/expeditions/fleet_composition_planner.py` to propose a deterministic exchange of a non-flagship ship from a donor fleet with the receiver's current flagship. The planner selects the lowest-level qualifying donor (ship ID as deterministic tie-breaker), then re-evaluates both resulting formations against their separate mission requirements.
- **Safety checks:** the planner rejects an ineligible donor, receiver blockers other than flagship level, no qualifying non-flagship candidate, and overlapping ship membership. It returns an immutable proposal only; it never changes fleet order or interacts with Poi.
- **Live read-only proposal:** using the latest Poi snapshot at `2026-11-01T10:16:43.676964Z`, the planner proposed exchanging Fleet 2 ship instance `4` (Lv4, currently a non-flagship) with Fleet 3 flagship instance `6` (Lv2). Resulting Fleet 2 order/levels: IDs `[1,6,8,25]`, levels `[8,2,4,4]`; resulting Fleet 3: IDs `[4,52,32,63]`, levels `[4,1,1,1]`. Both resulting compositions satisfy the supplied mission 1 / mission 3 requirements. This is a proposal, not an applied formation change.
- **Preflight integration:** `tools/verify_expedition_preflight.py` now includes the exchange proposal alongside the two live readiness reports. It continues to report both composition and supply blockers and exits 2 while live preconditions remain unmet.
- **Verification:** flagship-planner + preflight + mission-eligibility focused tests **28 passed**; full Python unittest discovery **300 passed**; Node static suites **17 passed**; `compileall -q src tests tools` and `git diff --check` passed. The live script produced the expected exchange proposal from the actual Poi snapshot.
- **Limitations and safety:** no formation change, replenishment, repair, dispatch, or other game UI input was performed. The proposal depends on the cached Poi snapshot and the supplied mission requirements; re-read live state before any later UI operation. Production `BONUS_ITEMS` fail-closed guard remains unchanged; no API/server request or proxy/system change was made.


## 2026-11-01 - Fleet 2 and Fleet 3 replenished through the visible Ooi UI

- **Fleet 2 supply action:** from the home-port UI, opened the normal supply screen, selected Fleet 2, and activated the visible fleet-wide supply control. The UI changed all four members' fuel/ammunition bars to full. Poi read-back confirmed instance IDs `1,4,8,25` at fuel `15/15` each and ammo `20/20,15/15,20/20,20/20` respectively.
- **Fleet 3 supply action:** selected Fleet 3 in the same normal supply screen and activated fleet-wide supply. The UI changed all four members' bars to full. Poi read-back confirmed instance IDs `6,52,32,63` at fuel `15/15` and ammo `15/15` for all four.
- **Final read-only preflight:** at `2026-11-01T10:25:50.825505Z`, both fleets reported idle mission tuples `[0,0,0,0]`. Fleet 2 → mission 1 returned `ready` (composition eligible and all members fully supplied). Fleet 3 → mission 3 remained `blocked_composition` solely because its current flagship is Lv2, below the supplied Lv3 requirement; all four members are now fully supplied. The exchange planner still proposes Fleet 2 ship instance `4` (Lv4) for Fleet 3 flagship instance `6` (Lv2), preserving both mission compositions.
- **UI evidence and cleanup:** every supply action used the normal Ooi UI at the existing 1200×720 viewport; selection and post-supply bars were inspected in-memory. Temporary screenshots used for inspection were deleted; no screenshot remains in the worktree. Poi state was re-read after both actions. No repair, ship movement, dispatch, game API/server request, or proxy/system setting change occurred.
- **Acceptance boundary:** supply readiness is verified for both fleets. Fleet 3's flagship exchange is only a proposal and has not been applied; neither fleet has been dispatched. The production single-fleet runner and its `BONUS_ITEMS` fail-closed guard remain unchanged.


## 2026-11-01 - Fleet 2 / mission 1 and Fleet 3 / mission 3 dispatched

- **Formation exchange applied through normal Ooi UI:** while viewing Fleet 3 formation, changed its flagship from ship instance `6` (如月 Lv2) to ship instance `4` (文月 Lv4), selected from Fleet 2. The game automatically exchanged the ships between fleets. UI read-back showed Fleet 3 flagship 文月 Lv4 and Fleet 2 retained 五月雨 Lv8 as flagship with 如月 Lv2 in slot 2.
- **Poi verification after exchange:** at `2026-11-01T10:39:56.928898Z`, Fleet 2 ship IDs were `[1,6,8,25]`, Fleet 3 ship IDs `[4,52,32,63]`; both mission tuples were idle and all eight ships remained fully supplied. The pure planner returned `no_exchange_needed`, confirming the new formations satisfy both supplied requirements.
- **Fleet 2 dispatch:** selected expedition 1 `練習航海`, selected Fleet 2, and activated the visible `遠征開始!` control. The expedition list then displayed Fleet 2 assigned to row 01. Poi read-back at `2026-11-01T10:42:39.622635Z` confirmed Fleet 2 `api_mission=[1,1,1793530637469,0]` (active, mission 1; expected return `2026-11-01T10:57:17.469Z`). Fleet 3 remained idle at that observation.
- **Fleet 3 dispatch:** selected expedition 3 `警備任務`, switched the fleet selector from the already-busy Fleet 2 to Fleet 3, visually verified its four ships (文月 Lv4, 睦月 Lv1, 皐月 Lv1, 長月 Lv1), then activated `遠征開始!`. Poi read-back at `2026-11-01T10:44:20.663223Z` confirmed Fleet 2 remained active on mission 1 and Fleet 3 `api_mission=[1,3,1793531044934,0]` (active, mission 3; expected return `2026-11-01T11:04:04.934Z`).
- **Verification boundary:** both dispatches are confirmed by the live Poi cached Redux snapshot and the visible expedition UI. The expected return timestamps are client-observed schedule values, not proof of server completion. Both fleets are now busy; no further dispatch or fleet changes should be attempted until each return state is observed. No game API/server request, repair, proxy/system setting change, or screenshot file write was performed.
- **Next:** passively monitor the two mission tuples, apply the configured grace interval after each expected return time, and use the normal visible UI only after Poi indicates a returned state. Reward collection remains subject to the production RETURN_RESULT/BONUS_ITEMS two-frame visual gate; do not bypass its fail-closed behavior.


## 2026-11-01 - Post-dispatch Home Port recovery and returned-state readback

- **Home Port recovery:** after both visible expedition dispatches, the UI was found on the expedition list / mission-selection screen. Used the previously verified visible Home Port navigation control at `(50, 50)` through normal Ooi UI input. The production UI state gate then passed two consecutive in-memory fingerprints: HOME_PORT scores `0.9902` and `0.9900`; competing expedition-list scores were `0.4462` and `0.4456`. No screenshot was saved to disk.
- **Post-navigation Poi readback:** at `2026-11-01T11:16:56.500356Z`, Fleet 2 remained on mission 1 with `api_mission=[2,1,1793530637469,0]`; Fleet 3 remained on mission 3 with `api_mission=[2,3,1793531044934,0]`. Both are in returned state 2, and all eight ships remain fully supplied. Fleet membership remains Fleet 2 IDs `[1,6,8,25]`, Fleet 3 IDs `[4,52,32,63]`.
- **Acceptance boundary:** the requested post-dispatch UI condition is verified: the client is back at HOME_PORT rather than the expedition list. The Poi cache reports both missions returned/awaiting result handling, not idle; rewards have **not** been collected. RETURN_RESULT/BONUS_ITEMS live two-frame verification remains required before any reward click. The production fail-closed guard was not bypassed; no further dispatch, supply, repair, or fleet change was performed.


## 2026-11-01 - Enforce Home Port return immediately after dispatch

- **Requirement clarified:** after the target mission is confirmed active, the client must leave the expedition list and return to HOME_PORT before waiting for the expedition timer. The client must not remain parked on the expedition list.
- **Implemented:** added `_return_home_port_after_dispatch()` to `tools/one_expedition_ui_loop.py` and wired it immediately after `_dispatch()` returns a verified active mission. It invokes the existing bounded Home Port recovery (two consecutive in-memory fingerprints), then re-reads Poi and requires the same active mission ID, final-time timestamp, and ship membership. A mismatch fails closed before the timer wait begins.
- **Tests:** added success coverage for Home Port recovery plus same-active-tuple readback, and a negative test proving the helper rejects a mission-state change during navigation. Full Python unittest discovery **302 passed**; Node static suites **17 passed**; `compileall -q src tests tools` and `git diff --check` passed.
- **Live evidence (separate from the new code path):** after the two manual visible-UI dispatches, the client was found on the expedition list. The known Home Port control at `(50,50)` was clicked through normal Ooi UI. The production state gate confirmed HOME_PORT on two consecutive in-memory frames (scores `0.9902` and `0.9900`), followed by Poi readback confirming the same two target missions were still active at that time. A later read at `2026-11-01T11:16:56.500356Z` showed both missions in returned state 2; the client remained at HOME_PORT.
- **Limitations:** the new helper is unit-tested but has not been exercised inside the production runner because `run()` still unconditionally fails closed until BONUS_ITEMS is live-verified. The manual UI recovery is live-verified; this does not constitute automated Bot-owned dispatch acceptance. Rewards remain uncollected and the BONUS_ITEMS gate remains unchanged.


## 2026-11-01 - Live dual-frame Home Port return-notice recognition

- **Live visual evidence:** read the two operator-captured Poi screenshots `2026-11-01T21.02.08.jpg` and `2026-11-01T21.02.10.jpg` from the Poi screenshots directory. Both are 1200x720 and visibly show the Home Port screen with the Fleet 3 notification `第3艦隊 遠征から戻ってきました。`. The frames are two seconds apart and show the same notification state.
- **Template scores:** both frames matched `return_notice.png` at approximately 0.98995 and `home_sortie_menu.png` at approximately 0.99608. Result-success/failure and bonus-title scores stayed below 0.17 and 0.26 respectively. The old classifier returned `UNKNOWN` because it grouped the return-notice marker with result-screen markers and also required the normal Home Port navigation marker, which was weaker (about 0.8784) on this overlay.
- **Implemented correction:** the visual classifier now models `HOME_PORT_RETURN_NOTICE` separately from `RETURN_RESULT`, requiring paired strong Home Port menu + return-notice evidence (or a strong dynamic Home Port reference) and rejecting expedition-list, dispatch, result, and bonus conflicts. The accessible-text state model also adds `HOME_PORT_RETURN_NOTICE` and requires both Home Port and return-notice text. A return-notice marker alone remains fail-closed.
- **Regression tests:** added tests using the actual score sets from both captured frames, plus negative tests for notice-only/ambiguous evidence. The existing return-result test now confirms that a return-notice template by itself is not a result screen.
- **Verification:** GitHub Actions CI run [41310389928](https://github.com/wosafety0/wosafety0clamp/actions/runs/41310389928) completed successfully on commit `cc21b48bbc08c30d553b6851bfc09ce0d9f56ae1`: all 305 Python unit tests passed, 3 optional NumPy-dependent visual-diff tests were skipped because the optional dependency is absent in CI; JavaScript static extraction tests, passive observer syntax check, and Python compile step also passed. Source and tests were read back from the target branch after each write.
- **Operational boundary:** this verifies the two-frame Home Port return-notice state only. It does not verify RETURN_RESULT or BONUS_ITEMS on the current live session, does not prove the reward has been collected, and does not authorize removing the production BONUS_ITEMS fail-closed guard. No game input, dispatch, reward click, game API/server request, screenshot write, or proxy/system change was performed in this round.
- **Workspace status:** GitHub branch `dev/expedition-closed-loop-mvp` contains the verified code/tests at `cc21b48bbc08c30d553b6851bfc09ce0d9f56ae1`. The local Windows worktree was clean at `b97e1c09e806c491d733b99588cec4228accf7e8` before the remote changes and has not yet been synchronized; DC connectivity was intermittent during this check. Sync the local worktree before local development or test execution.


## 2026-11-01 - Local synchronized verification of return-notice classifier

- **Worktree synchronization:** fast-forwarded the clean local Windows worktree from `b97e1c09e806c491d733b99588cec4228accf7e8` to the verified remote branch commit `5fe437461467480e43935ff3f104a10715cd217b`; no local uncommitted changes were present before the sync.
- **Local verification:** from the synchronized worktree, Python unittest discovery passed **305/305**; `compileall -q src tests tools` passed; Node static suites passed **17/17**; `git diff --check` passed. The local Python environment includes the optional vision dependencies, so the three optional vision-diff tests were not skipped in this local run.
- **Actual screenshot reclassification:** using the updated local `visual_state_gate.py` against both previously captured JPEGs (base64 payload decoded in memory), both frames now classify as `HOME_PORT_RETURN_NOTICE`. Frame 1: Home Port menu 0.99608, return notice 0.98995, result success 0.16670, result failure 0.16829, bonus 0.25640. Frame 2: Home Port menu 0.99608, return notice 0.98995, result success 0.16743, result failure 0.16412, bonus 0.25517.
- **Verification boundary:** this is an actual two-frame local classification pass for the Home Port + Fleet 3 return-notice state, not merely a synthetic unit test. It does not verify the subsequent RETURN_RESULT or BONUS_ITEMS screens, reward collection, or full loop. No game input, game API/server request, dispatch, reward click, screenshot write, or proxy/system change was performed.
- **Local synchronization note:** the local worktree is now aligned to the remote branch before this progress-document update; sync the final documentation commit before further local edits.


## 2026-11-01 - Production live gate confirms Fleet 3 return notification

- Ran the existing read-only `tools/verify_ooi_ui_state_gate.py` against the currently displayed Poi WebView with expected state `HOME_PORT_RETURN_NOTICE` and the local template directory. The diagnostic read the accessibility tree, then used only neutral pointer movement and in-memory CDP screenshots; it did not navigate or click.
- The production checkpoint gate observed `HOME_PORT_RETURN_NOTICE` on two consecutive live captures. Both frames scored `home_sortie_menu.png=0.9982` and `return_notice.png=0.9982`; result success/failure remained approximately 0.16–0.17, and bonus title approximately 0.258. The gate returned `PASS` with evidence `two_consecutive_in_memory_template_matches`; `screenshot_saved=false`.
- This is stronger than reclassifying the two saved Poi screenshots: the live client was still displaying the same Fleet 3 return-notice state at the time of this check. It verifies only the notification checkpoint.
- No notification click was sent. The current `CdpInput.click()` contract accepts reviewed `RecordedPoint` coordinates; no reviewed notification click point is present in the current controller. Do not invent a coordinate from the screenshot or bypass the recorded-point contract. Next, establish a reviewed UI target point or implement and test a bounded template-location click path, then verify RETURN_RESULT with the existing two-frame gate before advancing.
- The production `BONUS_ITEMS` startup guard remains unchanged. No reward was collected, no fleet was dispatched, no game API/server request was made, and no proxy/system setting was changed.


## 2026-11-01 - Offline continuous dual-fleet scheduler foundation

- **Implemented:** added `src/kancollebot/expeditions/continuous_scheduler.py`, a pure offline lifecycle state machine for independently managed dispatch fleets. Default runtime scope is Fleet 2 + Fleet 3; Fleet 1 is explicitly read-only and Fleet 4 is deferred until unlock.
- **Mandatory dispatch invariant:** the scheduler accepts dispatch only after a READY → DISPATCHING transition and a Poi-confirmed active mission tuple. It does not permit return monitoring until the UI is confirmed at `HOME_PORT` and the full active tuple is unchanged: status, mission ID, final-time timestamp, reserved slot, and ordered ship membership. Return confirmation separately requires status 2 and the same expedition identity.
- **Lifecycle coverage:** models `IDLE`, `READY`, `DISPATCHING`, `EXPEDITION_ACTIVE`, `RETURNED_PENDING_UI`, `RETURN_RESULT`, `BONUS_ITEMS`, `RESUPPLY_REQUIRED`, and `BLOCKED`. Reward completion and resupply each require positive confirmation. Added a shared-window mutex so concurrent fleet tasks cannot operate the Poi UI simultaneously.
- **Visual input safety correction:** changed the two-frame template locator result from `VerifiedVisualTarget` to `LocatedVisualTarget`; removed the CDP helper that converted the return-notice template center directly into a click. A visual match is now localization evidence only and grants no input authority.
- **Design update:** the top of `docs/MVP_EXPEDITION_SINGLE_LOOP.md` now supersedes the retired single-fleet contract with the dual-fleet operating scope, immediate Home Port return rule, UI arbitration, safety constraints, and explicit integration gaps.
- **Offline verification:** full Python unittest discovery **320/320 passed**; `compileall -q src tests tools` passed; Node static suites **17/17 passed**; `git diff --check` passed.
- **Implementation boundary:** the state machine and arbiter are not yet connected to Poi or the production runner. Fleet lifecycle persistence/restart reconciliation is not implemented. Generic result-page progression is not yet implemented or live-verified. The production `BONUS_ITEMS` fail-closed guard remains unchanged; no game UI input, dispatch, reward claim, game API/server request, or proxy/system setting change was performed in this development step.


## 2026-11-01 - Durable fleet lifecycle state and restart recovery

- **Implemented:** added `src/kancollebot/expeditions/fleet_state_store.py`, a SQLite-backed store for Fleet 2 / Fleet 3 lifecycle state, mission status and identity tuple, ordered ship membership, Home Port verification, and blocked reason.
- **Scheduler integration:** `ContinuousExpeditionScheduler` now optionally restores configured fleet state on startup and persists every state transition before updating in-memory state. Corrupt records, duplicate/out-of-scope fleet records, and storage failures fail closed.
- **Restart safety:** active missions with a verified Home Port postcondition are restored with the wait gate intact. Returned status 2 and settlement phase survive restart. An interrupted `DISPATCHING` state is restored as unresolved and cannot be dispatched again automatically; automated reconciliation against a fresh Poi observation remains unimplemented.
- **Verification:** full Python unittest discovery **327/327 passed**; `compileall -q src tests tools` passed; Node static suites **17/17 passed**; `git diff --check` passed. New persistence tests cover active restart, interrupted dispatch no-retry, blocked-state restore, returned-state restore, corrupt tuple rejection, out-of-scope fleet rejection, and write-failure atomicity.
- **Implementation boundary:** persistence is implemented and offline-tested but is not yet connected to the live Poi runner. No unattended operation, live UI action, game API/server request, or proxy/system setting change was performed.


## 2026-11-01 - Durable dispatch intent and conservative restart reconciliation (IMPLEMENTED; verification pending)

- **Saved reusable artifact:** added `src/kancollebot/expeditions/dispatch_recovery.py` and `tests/test_dispatch_recovery.py`. This is an offline coordinator/store layer; it is not yet a live UI integration.
- **Intent-before-action ordering:** preparation requires a fresh source-verified Poi Redux baseline proving the target fleet is idle. The intent records Fleet 2/3, requested mission ID, ordered ship instance IDs, reserved slot, baseline observation time, preparation time, and later the UI-action completion timestamp. The intent is committed before the scheduler enters `DISPATCHING`.
- **Crash-window handling:** a pending intent plus a persisted `DISPATCHING` state remains unresolved until fresh Poi evidence is supplied. If the scheduler transition did not persist but the intent did, startup recovery blocks the fleet. If scheduler confirmation persisted before intent deletion, recovery clears the matching stale intent only when the stored active mission identity and timestamp agree.
- **Positive reconciliation only:** restart reconciliation accepts only a recent, source-fresh Poi Redux observation whose Fleet ID, active mission ID, ordered ship membership, reserved slot, and mission timestamp match the persisted intent. It restores `EXPEDITION_ACTIVE` with `home_port_verified=False`; therefore it cannot start the return timer or bypass the mandatory return-to-Home-Port verification.
- **No automatic retry:** fresh idle state, already-returned state, unknown status, stale/unverified observations, and mission/roster mismatch never authorize a second dispatch. Contradictory or non-positive state blocks the fleet; an already-returned mission is left for a separate manual UI settlement workflow.
- **Related invariant correction:** blocking a fleet now clears `home_port_verified`, preventing a blocked fleet from retaining permission to wait on a mission timer.
- **Verification state:** implementation saved to the authoritative branch. The new test suite and full regression suite have **NOT_RUN / NOT_VERIFIED** in this round; no test pass is claimed. The artifact is retained under the unverified-reusable-artifact rule until execution evidence is available.
- **Operational boundary:** no Poi/game UI input, dispatch, reward collection, game API/server request, or proxy/system setting change was performed. The production `BONUS_ITEMS` fail-closed guard remains unchanged.

## 2026-11-02 - Dispatch recovery offline verification

- **Workspace recovery and synchronization:** the Windows device returned online. The local worktree was clean at a9787ae9f097cba4374f0bac554dfcb285525bc6; after fetching the authoritative branch, the local commit was confirmed to be an ancestor of remote 0987574d8b493828ee5d6377929766358f98e7f7 by 21 commits. A fast-forward-only update synchronized the worktree to 0987574d8b493828ee5d6377929766358f98e7f7; no local edits were discarded.
- **Targeted verification:** tests.test_dispatch_recovery, tests.test_continuous_scheduler, and tests.test_expedition_state_v2 passed **40/40**.
- **Full offline regression:** python -m unittest discover -s tests -q passed **345/345**. The local venv had NumPy 2.5.4 and OpenCV 5.0.0 available, so optional visual tests were not skipped due to missing dependencies.
- **Static checks:** python -m compileall -q src tests tools, Node static extraction tests (**17/17**), passive Poi observer node --check, and git diff --check all passed.
- **GitHub Actions:** CI run https://github.com/wosafety0/wosafety0clamp/actions/runs/41319959737 for commit 0987574d8b493828ee5d6377929766358f98e7f7 on dev/expedition-closed-loop-mvp completed with conclusion success; its JavaScript tests, passive observer syntax, Python compile, and unit-test steps all succeeded.
- **Verification boundary:** this verifies the offline implementation and regression suite, not Poi production-runner integration, real UI settlement, live RETURN_RESULT/BONUS_ITEMS two-frame recognition, reward collection, or unattended dual-fleet operation. The production BONUS_ITEMS fail-closed guard remains mandatory and unchanged.
- **Operational boundary:** no game UI input, dispatch, reward collection, game API/server request, proxy/system setting change, or fleet/formation change was performed during this verification.


## 2026-11-02 - Continuous dispatch controller offline verification and live gate status

- **Controller implementation:** added continuous_dispatch_controller.py with one-fleet-at-a-time dispatch transactions for the configured Fleet 2 / Fleet 3 pair. It accepts one PoiExpeditionReader-compatible read_fleets() source, one shared UI driver/CDP session, one shared UiOperationArbiter, the same scheduler instance as the durable dispatch coordinator, and a required durable event sink.
- **Mission safety:** normalized Poi fleet snapshots now retain ordered ship levels and master ship types. The controller requires complete composition metadata and explicit mission requirements: Fleet 2 → mission 1 (at least two ships, flagship level 1); Fleet 3 → mission 3 (at least three ships, flagship level 3). It also requires an idle mission tuple, full supply, fresh source-verified Poi Redux observations, exact post-dispatch mission/roster/reserved-slot identity, and a future return timestamp.
- **Durable audit:** SqliteDispatchEventLog stores append-only event records and can share the scheduler/dispatch-intent SQLite database. The controller requires an event sink and defaults allow_ui_actions to false.
- **Dispatch postcondition:** after UI dispatch and Poi confirmation, the UI driver must return two consecutive HOME_PORT state labels. A later fresh Poi observation must preserve the exact active mission tuple before the scheduler permits waiting. Ambiguous outcomes block the fleet; automatic retries are forbidden.
- **Focused tests:** controller and Poi normalization tests passed **29/29**.
- **Full offline regression:** python -m unittest discover -s tests -q passed **363/363**. Python compileall, Node static extraction tests (**17/17**), passive observer syntax, and git diff --check passed. NumPy 2.5.4 and OpenCV 5.0.0 were available.
- **Live visual checkpoint:** the current Poi screen passed the two-frame HOME_PORT_RETURN_NOTICE gate. Both frames matched return_notice.png and home_sortie_menu.png at 0.9982; screenshots were not written to disk. The BONUS_ITEMS diagnostic correctly did not match the current screen (bonus_title.png score 0.2571); no BONUS_ITEMS two-frame success was claimed.
- **Freshness boundary:** the passive port-response witness timed out during a 10-second read-only diagnostic and returned FRESHNESS_UNVERIFIED. All fleet planning assessments were suppressed. A separate cached Redux diagnostic displayed Fleet 2 as idle but under-supplied and Fleet 3 as mission 3/status 2; because source freshness was unverified, these cached values were not used to authorize any action.
- **Production boundary:** no production ContinuousUiDriver is wired. No click, dispatch, reward collection, resupply, game API/server request, proxy change, or system setting change was performed. The production BONUS_ITEMS fail-closed guard remains mandatory and unchanged.

## 2026-11-02 - Continuous monitor reconciliation

- **Monitor implementation:** added continuous_monitor.py. One fresh Poi Redux observation is validated for the complete dispatchable fleet set before any state transition; duplicate or missing fleet records reject the whole tick.
- **Return correctness:** active status 1 with the exact mission tuple remains active even when the planned return timestamp has passed. Only status 2 with matching mission ID, final timestamp, reserved slot, and ordered ship IDs confirms return, and only after the scheduler has verified Home Port.
- **Independent fleet lifecycle:** the monitor can reconcile one returned fleet while keeping another fleet's readiness independent. Pending settlement remains pending; blocked fleets never auto-unblock; DISPATCHING is left to durable intent recovery.
- **Readiness:** IDLE and RESUPPLY_REQUIRED move to READY only after supply and mission composition are verified. No supply action is initiated by the monitor.
- **Audit volume:** the SQLite event sink records scheduler phase transitions rather than writing an event for every unchanged polling tick.
- **Tests:** continuous monitor tests passed **14/14**. Combined controller, monitor, and Poi normalization tests passed **43/43**. Regression across the intended archive scope passed **377/377**; the separate local supervisor prototype was excluded from that run and is not part of this archive.
- **Production boundary:** this is a read-only reconciliation tick, not a complete unattended run loop. The production UI driver, settlement controls, live freshness witness, and BONUS_ITEMS two-frame success remain pending. No game UI input, dispatch, reward claim, resupply, game API request, proxy change, or system setting change was performed.

## 2026-11-02 - Offline continuous dual-fleet supervisor

- **Implemented:** added `continuous_supervisor.py` and `test_continuous_supervisor.py`. The supervisor coordinates one monitor tick and sequential READY-fleet dispatch using the shared scheduler and controller.
- **Fail-closed gates:** BLOCKED, unresolved DISPATCHING, pending settlement, and active fleets without verified Home Port pause all dispatch. UI actions require the controller's explicit `allow_ui_actions` flag. Each dispatched fleet must reach `EXPEDITION_ACTIVE` with Home Port proof before the next fleet is attempted.
- **Focused verification:** supervisor tests passed **9/9**.
- **Full offline regression:** `python -m unittest discover -s tests -q` passed **386 tests**, with **6 optional vision tests skipped** because optional dependencies were unavailable. `python -m compileall -q src tests tools` passed. Node static tests passed **17/17**. `git diff --check` passed.
- **Implementation boundary:** this is a single-tick offline coordinator, not a persistent scheduling process and not a production UI driver. Live freshness witness acquisition, production UI integration, result/reward progression, and unattended operation remain pending. No live game UI input, dispatch, reward claim, resupply, game API/server request, proxy change, or system setting change was performed.
## 2026-11-02 - Passive freshness witness output isolation

- **Implemented:** the passive CDP port-response observer now keeps its compact witness in child-process stdout for the in-memory Poi freshness bridge and does not create `Desktop\KancolleBot-Port-Snapshots` files in that mode. Explicit standalone snapshot-export mode retains its prior file output.
- **Regression coverage:** added a Node test harness with a simulated successful passive CDP port response; verified stdout mode performs zero filesystem writes and returns a parseable witness, while standalone mode retains its explicit two-write export path.
- **Verification:** observer behavior tests **2/2**; full Python regression **386 tests passed, 6 optional vision tests skipped**; Node static suite **19/19**; Python compileall, observer `node --check`, and `git diff --check` passed.
- **Boundary:** this verifies offline observer behavior only. It does not prove a live natural port response can be captured, change the production freshness gate, enable game actions, or verify BONUS_ITEMS. No game UI input, game API request, dispatch, reward claim, resupply, proxy change, or system setting change was performed.

## 2026-11-02 - Live RETURN_RESULT checkpoint and freshness boundary

- **Live visual verification:** two consecutive in-memory CDP screenshots of the current game WebView were both classified as `RETURN_RESULT` by the existing template classifier. The success-template score was 0.9983 and failure-template score 0.5651 in both frames. No screenshot files were written and no game input was sent.
- **Freshness finding:** a 120-second passive Network observation while the screen remained on `RETURN_RESULT` observed no Kancolle server host response and no `/kcsapi/api_port/port` response. The subsequent Poi Redux read remained `FRESHNESS_UNVERIFIED`; planning for Fleet 1, 2, and 3 was suppressed. This shows that the current result screen does not provide the required passive port witness within that observation window; it does not authorize use of cached fleet state.
- **Offline verification with project venv:** `.venv` provides OpenCV 5.0.0 and NumPy 2.5.4. Full Python regression passed **386/386 with no skips**; Node static tests passed **19/19**; Python compileall, observer `node --check`, and `git diff --check` passed.
- **Safety boundary:** no game UI input, reward advancement, dispatch, resupply, game API/server request, proxy change, or system setting change was performed. `BONUS_ITEMS` remains unverified and its production fail-closed guard remains unchanged.

## 2026-11-02 - Live Fleet 3 result and reward settlement

- **Live return result:** the active Poi game WebView was classified as `RETURN_RESULT` in two consecutive frames (success-template score 0.9983). The result-confirm UI action was sent once.
- **BONUS_ITEMS:** after the result-confirm action, two consecutive frames classified as `BONUS_ITEMS`; the bonus-title template score was 0.9794 in both frames. The reward-advance/claim UI action was sent once.
- **Return to port:** after the reward action, two consecutive frames classified as `HOME_PORT` (home-menu score 0.9982). The complete observed sequence was `RETURN_RESULT → BONUS_ITEMS → HOME_PORT`; two UI actions total, no screenshots persisted to disk.
- **Scope:** this verifies one live Fleet 3 settlement sequence only. It does not verify automatic settlement across fleets, production ContinuousUiDriver integration, dispatch automation, or unattended operation. The separate passive port observer still timed out without a qualifying natural `/kcsapi/api_port/port` response; fleet planning therefore remains freshness-unverified and no further action is authorized from cached Redux state.
- **Safety:** no dispatch, resupply, game API/server request, proxy change, or system setting change was performed. The production `BONUS_ITEMS` fail-closed guard remains in place pending full end-to-end acceptance.

## 2026-11-02 - Production runner startup guard message alignment

- **Implementation:** after the live two-frame `RETURN_RESULT` and `BONUS_ITEMS` checkpoints passed, corrected the production runner's startup rejection reason from the now-obsolete “BONUS_ITEMS not live-verified” to “end-to-end acceptance has not been completed.” The unconditional rejection remains before either CDP session is opened; production UI actions remain disabled.
- **Regression:** updated the startup safety test to assert the accurate remaining gate and verify neither Poi nor game CDP session is opened. Focused startup guard test passed **1/1**.
- **Verification:** full Python suite passed **386/386**; Node static suite **19/19**; Python compileall and `git diff --check` passed.
- **GitHub Actions:** push-triggered CI run `41378499516` completed successfully; its `test` job and all listed steps passed.
- **Boundary:** this is diagnostic-message alignment only, not removal of the production guard or authorization to dispatch. Live freshness witness and full end-to-end unattended acceptance remain pending.

## 2026-11-02 - SORTIE_MENU visual checkpoint and template bundling

- **Implementation:** added `SORTIE_MENU` classification from the Japanese `出撃選択` title crop, with a strong-match threshold and fail-closed handling for weak or conflicting Home Port/result/reward evidence. The 205×56 template is bundled at `src/kancollebot/adapters/poi/assets/sortie_menu_title.png`; the classifier falls back to this bundled asset when the configured local template directory does not contain it.
- **Regression:** visual state tests passed **14/14**; full Python suite passed **389/389**; Node static suite passed **19/19**; `compileall` and `git diff --check` passed.
- **Live UI verification:** the current sortie-selection page passed two consecutive Ooi fingerprint checks as `SORTIE_MENU` (score 1.0). One normal Home Port navigation click then returned the UI to `HOME_PORT`, confirmed in two consecutive frames. No screenshot files were written. No dispatch, resupply, reward action, or game API request was performed in this stage.
- **Boundary:** this closes the missing `SORTIE_MENU` visual checkpoint only. The live passive-port freshness result from the latest combined diagnostic was not emitted because its assessment-summary call lacked the required `max_state_age` argument; do not count that run as freshness verification. The previously confirmed `natural_response_timeout` remains the last fully recorded freshness result. Fleet planning stays fail-closed and the production startup guard remains unchanged.


## 2026-11-02 - Live expedition-list navigation checkpoint

- **Live sequence:** starting from a two-frame `HOME_PORT` confirmation, one normal UI click opened `SORTIE_MENU`; the two-frame gate passed. One click on the visible expedition-mode option opened `EXPEDITION_LIST`; the two-frame gate passed. One Home Port navigation click returned to `HOME_PORT`; the two-frame gate passed.
- **Result:** `HOME_PORT → SORTIE_MENU → EXPEDITION_LIST → HOME_PORT` verified in one CDP session. Three navigation clicks total; no mission selection, dispatch, resupply, reward claim, or game API request. No screenshots were written to disk.
- **Boundary:** this verifies navigation and state recognition only. Fresh source-correlated Poi fleet state remains unverified; no dispatch is authorized and the production startup guard remains unchanged.


## 2026-11-02 - Live natural Poi port freshness witness

- **Freshness verification:** armed the reviewed passive CDP Network observer, then performed ordinary UI navigation `HOME_PORT → SORTIE_MENU → EXPEDITION_LIST → HOME_PORT`. It captured a naturally occurring successful `/kcsapi/api_port/port` HTTP 200 response with cache flags false. The in-memory Poi Redux comparison returned `FRESHNESS_WITNESS_MATCHED` at `2026-11-02T04:03:48.635886Z` (12:03:48 Beijing time), with three fleets.
- **Live planning assessment:** Fleet 1 was READY but remains prohibited from dispatch; Fleet 2 was blocked because at least one ship is damaged; Fleet 3 needs fuel and ammunition resupply. No fleet was dispatch-eligible. No repair, resupply, or dispatch was performed.
- **UI and safety:** the three clicks were normal navigation only; the sequence ended at a two-frame-confirmed HOME_PORT. The observer did not send input, evaluate page JavaScript, or initiate a game API request. No screenshots were written to disk.
- **Next integration requirement:** the new background provider removes the per-read wait as a reusable adapter, but is not yet wired into the continuous scheduler composition. Retain strict timestamp/current-state matching; stale or mismatched snapshots must never authorize planning.


## 2026-11-02 - Background passive Poi freshness provider (offline implementation)

- **Implementation:** added `src/kancollebot/adapters/poi/background_port_snapshot_provider.py`. It lazily starts a daemon worker, returns the latest in-memory port witness immediately instead of blocking each `read_fleets()` call, and rearms the one-shot passive observer after a capture. It sends no game input and initiates no game API request.
- **Safety contract:** the provider does not mark snapshots fresh or extend their lifetime. `PoiExpeditionReader` continues to validate response/body timestamps, cache flags, and exact current fleet/ship equality; expired or mismatched witnesses remain rejected.
- **Tests:** background provider concurrency/retry/re-arm/copy/close/cancellation tests passed **6/6**; cancellable observer tests passed **10/10**; full Python suite passed **396/396**; Node static suite **19/19**; compileall and `git diff --check` passed.
- **CI:** GitHub Actions push run **41387568457** for commit `2c110bc4cb1e83ed80ebd333889de2b40322f81a` completed successfully; the `test` job and all listed steps passed.
- **Limitations:** this is a reusable offline-tested adapter, not yet wired into a production composition root. `PassivePortResponseObserver.capture_cancellable()` lets `close()` terminate its in-flight Node child; generic injected observers exposing only `capture()` cannot be interrupted. Production UI actions remain disabled.


## 2026-11-02 - Live background Poi reader integration

- **Runtime wiring:** constructed `BackgroundPortSnapshotProvider` with the reviewed passive observer and injected it into the real `PoiExpeditionReader` using the local Poi MCP read-only client. The reader continued polling while the observer waited independently; no per-read 130-second wait occurred.
- **Live proof:** normal UI navigation produced a natural non-cache `/kcsapi/api_port/port` response. The integrated reader returned `FRESHNESS_WITNESS_MATCHED` after **31 polls in 35.55 seconds**, observed at `2026-11-02T04:20:54.811656Z`, covering three fleets.
- **Assessment:** Fleet 1 READY but prohibited from dispatch; Fleet 2 `blocked_damaged_ships`; Fleet 3 `needs_resupply` due to fuel/ammunition. All three observations were accepted as fresh; none was dispatch-eligible. No repair, resupply, dispatch, or reward action was performed.
- **UI/safety:** verified `HOME_PORT → SORTIE_MENU → EXPEDITION_LIST → HOME_PORT` with two-frame state gates. Three ordinary navigation clicks only; no screenshot files, game API calls, or game actions. The diagnostic explicitly reported `game_actions_enabled: false`.
- **Remaining boundary:** this proves the background provider + real reader + Poi MCP path, not a persistent scheduler composition or production UI driver. The production startup guard remains unchanged.

## 2026-11-02 - Read-only continuous monitor runtime composition

- **Implementation:** added `tools/run_continuous_expedition_monitor.py`. It composes the background freshness provider, read-only PoiExpeditionReader, ContinuousExpeditionMonitor, SQLite fleet-state/dispatch-intent/event stores, and conservative pending-intent recovery.
- **Fleet policy:** Fleet 2 → mission 1 and Fleet 3 → mission 3 remain the only dispatch candidates; Fleet 1 is read-only and Fleet 4 deferred. At this stage the runtime did not instantiate a dispatch controller, supervisor, or UI driver; the later refresh-only navigation integration is recorded separately below.
- **Safety at this stage:** the CLI had no UI-action switch and always reported `game_actions_enabled: false`. The later explicit `--enable-ui-refresh` option permits only refresh navigation; the runtime still cannot dispatch, collect rewards, or resupply.
- **Tests:** runtime-composition tests passed **4/4**; full Python suite passed **400/400**; Node static tests passed **19/19**; compileall, passive observer `node --check`, and `git diff --check` passed.
- **CI:** GitHub Actions push run **41388699840** for runtime commit `d255f3e41d46593cf14f500c26ca5b504b83e585` completed successfully; follow-up observer-diagnostic test commit `da87637541a4d6f2e0d7660239c5a0d2664b1ce1` also passed CI run **41388793961**.
- **Live diagnostic:** a five-tick read-only run without UI navigation returned `FRESHNESS_UNVERIFIED` on all five ticks. No game input or state-changing action occurred. This is a correct fail-closed result when no new natural witness arrives; the separate 31-poll reader integration remains the positive live witness evidence.
- **Boundary:** this is a persistent monitor runtime, not the production unattended scheduler. Dispatch, result settlement, BONUS_ITEMS progression, resupply, and production dispatch/settlement UI driver integration remain pending.

## 2026-11-02 - Visible UI freshness refresh adapter (offline-tested)

- **Implementation:** added `src/kancollebot/adapters/poi/visible_ui_refresh.py` with `VisibleUiPortRefreshDriver` and `FreshnessRefreshingStateReader`. The driver encapsulates only the previously live-verified visible navigation path `HOME_PORT → SORTIE_MENU → EXPEDITION_LIST → HOME_PORT`; the reader can wait for a natural, source-matched Poi port witness after that navigation.
- **Fail-closed behavior:** each click requires two consecutive matching state observations before the next step. A mismatch stops immediately; an ambiguous click is recorded as attempted but incomplete and is never automatically replayed. A bounded witness timeout returns the unverified observation unchanged, and a cooldown prevents repeated navigation attempts.
- **Verification:** the new adapter's focused tests passed **8/8**; the full Python suite passed **408/408**; `compileall` and `git diff --check` passed.
- **Implementation boundary:** at this stage the adapter was independently tested but not runtime-wired. It has since been connected to the read-only monitor behind the explicit `--enable-ui-refresh` flag (see the following entry). No live Poi navigation was executed by either implementation stage; the separate earlier live navigation and 31-poll natural-witness integration remain the only live evidence.
- **Safety boundary:** the adapter does not select a mission or fleet, dispatch, collect rewards, resupply, reload the page, or create a game-server request. Production dispatch and settlement remain disabled.

## 2026-11-02 - Optional visible freshness refresh in read-only monitor runtime

- **Runtime integration:** `tools/run_continuous_expedition_monitor.py` now accepts `--enable-ui-refresh`. Only with this explicit flag does it open a game WebView CDP input session and wrap the Poi reader with `FreshnessRefreshingStateReader`; the default runtime remains read-only and opens no game input session.
- **Allowed UI scope:** the optional path performs only the existing two-frame-gated `HOME_PORT → SORTIE_MENU → EXPEDITION_LIST → HOME_PORT` navigation. It shares a `UiOperationArbiter` and the existing durable SQLite event sink. The output distinguishes `ui_navigation_enabled` from `game_actions_enabled`; dispatch, mission selection, settlement, rewards, and resupply remain disabled.
- **Freshness policy:** after an unverified read, the wrapper performs at most one navigation sequence, waits up to 45 seconds for a naturally captured matching witness, and starts the 30-second cooldown after that wait finishes. A timeout returns the observation as unverified; an ambiguous UI step stops the monitor without retry.
- **Verification:** refresh-adapter tests **9/9**; monitor-runtime tests **9/9**; full Python suite **414/414**; compileall, observer `node --check`, CLI `--help`, and `git diff --check` passed. The UI-enabled composition tests use a fake input session and send no clicks.
- **CI for the preceding adapter stage:** GitHub Actions push run **41405966811** for commit `7ae864c759eedc76bf8c726a47a08fafe0edf3d4` completed successfully.
- **Live boundary:** the new CLI option has not been run against Poi; no live navigation was performed in this stage. The earlier 31-poll natural witness remains separate evidence. This is still a read-only monitor with optional refresh navigation, not a production expedition dispatcher or settlement loop.

## 2026-11-02 — Supply-menu checkpoint capture support (offline)

- Added `UiState.SUPPLY_MENU` to the UI checkpoint enum and added the state to `capture_ooi_visual_fingerprint.py`'s supported capture labels. The capture utility records compact RGB cell-average fingerprints; it does not persist screenshot bytes.
- Added regression coverage proving the new checkpoint label is available and that the accessibility classifier does not infer `SUPPLY_MENU` from the generic text `補給` alone. The state still requires a manually confirmed visual fingerprint before the live gate can recognize it.
- Verification: full Python suite **416/416 passed**; `compileall` and `git diff --check` passed.
- Live boundary: no supply-menu fingerprint has been captured, no supply UI click or resource-consuming action was performed, and no resupply/dispatch/reward path was enabled. A cached Poi snapshot showing both candidate fleets below full supply is not a freshness witness and cannot authorize actions.
- Additional blockers retained: mission 1 has no verified UI row target; the recorded mission 3 row target is not sufficient to infer other coordinates. Windows time service reported `Local CMOS Clock` without successful synchronization; actual clock skew remains unknown, so unattended time-based scheduling stays disabled.
## 2026-11-02 - Live SUPPLY_MENU fingerprint captured and verified

- **Live starting-state proof:** the production UI checkpoint gate confirmed `HOME_PORT` on two consecutive in-memory Ooi frames (scores 0.9984 / 0.9986). A normal UI click on the visibly labeled Home Port `補給` entry opened the supply screen; the observed click center was approximately (120,350) in the 1200×720 viewport.
- **Manual screen confirmation:** an in-memory live screenshot showed the ship-selection header `艦船選択` and the supply header `燃料・弾薬の補給`. The screenshot was not written to disk.
- **Fingerprint artifact:** `src/kancollebot/adapters/poi/ooi_1200x720_fingerprints.json` now contains a three-sample `SUPPLY_MENU` fingerprint using crop [150,105,1050,60] and grid [24,4], for 288 RGB channel values. The crop targets the stable header row and excludes dynamic ship rows/resource values. Only compact cell-average values were saved; screenshot bytes were not saved.
- **Live verification:** `tools/verify_ooi_visual_fingerprint.py --expected SUPPLY_MENU` returned PASS with score 0.99967. The production `tools/verify_ooi_ui_state_gate.py --expected SUPPLY_MENU` then returned PASS after two consecutive matches (0.9996 / 0.9997), proving the existing `_require_ui_state` gate can consume the new fingerprint.
- **Regression coverage:** added a test asserting the bundled descriptor's exact crop/grid/sample count/vector length and `manually_confirmed_current_screen` provenance.
- **UI/safety boundary:** the page was left on the supply menu with the default first fleet tab selected. No fleet selection, supply checkbox, supply-all control, repair, dispatch, reward collection, or game API request occurred. This is a page-recognition checkpoint only; it does not authorize resource actions.
- **Remaining blockers:** Fleet 2/Fleet 3 supply-selection controls still need independent visual-state proof; mission 1 still lacks a verified row target; the older cached fleet snapshot is not a freshness witness; Windows time synchronization remains unverified, so unattended scheduling stays disabled.

## 2026-11-02 - Live Fleet 2/Fleet 3 supply-tab checkpoints

- **Implementation:** added `UiState.SUPPLY_FLEET_2_SELECTED` and `UiState.SUPPLY_FLEET_3_SELECTED`, capture-tool support, and separate compact fingerprints for each selected tab. The descriptors use three samples each, a 10×8 grid, and tab-specific 30×40 crops (Fleet 2 [249,155,30,40], Fleet 3 [295,155,30,40]).
- **Classifier rule:** these two states are child states of `SUPPLY_MENU`. The fingerprint classifier excludes only the shared parent-menu score from a child state's margin calculation; the other state scores remain competitors. The focused tab crops distinguish Fleet 2 and Fleet 3 with observed cross-state similarity below 0.90.
- **Live proof:** the production checkpoint gate confirmed Fleet 2 and Fleet 3 separately on two consecutive in-memory frames (each exact-match score 1.0). Cross-state negative checks rejected the other fleet label. Normal UI transitions Fleet 2 → Fleet 3 and Fleet 3 → Fleet 2 both passed their pre- and post-state two-frame gates.
- **Regression coverage:** tests now validate both live descriptor shapes/provenance and the parent/child margin rule. Focused suites: visual fingerprints **12/12**, UI state gate **8/8**. Full Python suite: **419/419 passed**; `compileall` and `git diff --check` passed.
- **Selection-only diagnostic:** after the Fleet 2 two-frame gate passed, the first ship checkbox was temporarily checked. The live screen showed the row selected and updated the displayed required fuel amount; clicking the same checkbox again restored the row to unchecked. This is a reversible selection state, not a supply operation.
- **Safety:** only fleet-view tabs and one temporary checkbox selection were used. No individual supply or supply-all action, repair, dispatch, reward collection, or game API request occurred. Current UI remains on the supply page with Fleet 2 selected and all ship checkboxes unchecked.
- **Remaining boundary:** the selected-fleet states do not yet prove ship checkbox state or authorize any resource-consuming action. The next safe step is to identify and separately verify the individual supply-control states before implementing a guarded supply driver.

## 2026-11-02 - Fleet 2 first-ship checkbox visual substate and production gate

- **Target localization:** added a bounded OpenCV contour locator restricted to region [150,225,55,50]. The live target was uniquely detected at bounds [166,240,22,23], center [177,251.5] in the 1200×720 viewport. Nested inner/outer contours are merged by center; zero or multiple distinct candidates fail closed.
- **Input safety:** added `VisualTargetPoint` and `CdpInput.click_visual_target()`. The click path is separate from `RecordedPoint`, validates the exact source-frame SHA-256, and restricts the target label/region to Fleet 2's first-ship checkbox. Existing recorded-point behavior is unchanged.
- **Live selection proof:** the selection-only probe sent one click to the detected checkbox. The screen showed a check mark and updated the required-fuel amount; the crop similarity changed to 0.954001. The guarded restore sent one click only after confirming 41 bright mark pixels; afterward the mark count was 0. The same selection/restore sequence was repeated for positive production-gate verification. No screenshot bytes were saved.
- **Fingerprint artifact:** added `src/kancollebot/adapters/poi/supply_checkbox_fingerprints.json` with checked and unchecked descriptors, each using three samples, crop [164,238,26,26], grid [13,13], and 507 RGB channel values. Cross-state similarity is 0.954001.
- **Classifier and gate:** added `SUPPLY_FLEET_2_SHIP_1_CHECKED` and `SUPPLY_FLEET_2_SHIP_1_UNCHECKED`. Each frame first has to classify as parent `SUPPLY_FLEET_2_SELECTED`, then the checkbox crop must uniquely match one of the two states (minimum score 0.97, margin 0.04). `_require_ui_state` requires two consecutive matches for the child state.
- **Live positive/negative checks:** checked state passed its two-frame production gate; unchecked expectation was rejected while checked. After restoration, unchecked passed its two-frame production gate; checked expectation was rejected while unchecked. The Fleet 2 parent gate remained valid. Current screen was restored to Fleet 2 with the first checkbox unchecked.
- **Tests:** full Python suite **430/430 passed**; Node static suites **17/17 passed**; Python compileall, Node syntax check, and `git diff --check` passed. The focused visual-target tests ran locally with the optional OpenCV/NumPy dependencies installed.
- **Safety boundary:** only the first Fleet 2 ship checkbox was toggled and restored. No supply button, supply-all, repair, dispatch, reward, or game API action was used. Other ship checkboxes, Fleet 3 checkbox states, and all resource controls remain unverified. Resupply remains disabled.
- **Local evidence:** `logs/supply_checkbox_probe.jsonl`, `logs/supply_checkbox_gate_unchecked_20261102.log`, and `logs/supply_checkbox_negative_checked_20261102.log` are local-only because `logs/` is gitignored; the tracked fingerprint file contains only compact cell-average values, not screenshot bytes.


## 2026-11-02 - Fleet 2 all four ship checkbox visual states

- Target/input implementation: extended the approved VisualTargetPoint allowlist from Fleet 2 ship 1 to ships 1-4, each with a separate bounded region, exact label, and source-frame SHA-256 check. Live bounds: ship 1 [166,240,22,23], ship 2 [166,317,22,23], ship 3 [166,394,22,23], ship 4 [166,471,22,23].
- State model: added checked/unchecked child states for Fleet 2 ships 2-4. classify_supply_checkbox_state(..., ship_index=N) first requires the parent Fleet 2 selected fingerprint, then compares only the two descriptors for that exact ship. _require_ui_state continues to require two consecutive matching in-memory frames.
- Fingerprint data: expanded supply_checkbox_fingerprints.json to eight descriptors, two states per ship. Each state has three samples, a 13x13 RGB grid (507 values), and a row-specific crop: ship 1 [164,238,26,26], ship 2 [164,315,26,26], ship 3 [164,392,26,26], ship 4 [164,469,26,26]. Checked/unchecked similarity for the newly calibrated rows was 0.954009 (ship 2), 0.954032 (ship 3), and 0.954009 (ship 4).
- Reusable tool: added tools/calibrate_supply_checkbox.py. It supports three-sample capture for checked/unchecked states, a single-click selection-only probe, and restore guarded by checked and unchecked production gates. It exposes no resource-consuming actions.
- Live verification: ships 2, 3, and 4 each started unchecked (0 mark pixels), received exactly one selection click (41 mark pixels), had three checked samples captured, then received exactly one restore click. The checked and unchecked two-frame gates passed for each transition; restored mark counts were 0. Ship 1 retained its previously verified evidence.
- Final cross-row check: all four targets were found at their expected bounds in one in-memory screenshot. All four unchecked child states passed two-frame gates; all four checked expectations were rejected while unchecked. All four mark counts were 0. No screenshot bytes were written.
- Safety: no individual supply, supply-all, repair, dispatch, reward, or game API action occurred. Fleet 3 checkbox states and all resource controls remain unverified; resupply stays disabled.
- Validation: full Python suite 433/433 passed; Node static tests 17/17 passed; Node syntax check, Python compileall (src/tests/tools), calibration CLI help, and git diff --check passed. Commit ee58e67b3d789936dcc11389da957311854bb2ed was pushed and read back from GitHub; all 12 changed files matched their local Git blob SHA values. Push-triggered Actions run 41460331247 completed successfully, with the test job and all workflow steps passing.


## 2026-11-02 - Fleet 3 four-ship checkbox visual states

- **Fleet-tab navigation:** added a bounded rectangular visual locator for the supply Fleet 2/Fleet 3 tabs. Fleet 3 tab region [285,155,50,50], live contour [293,164,34,31]. One normal UI click navigated from Fleet 2 to Fleet 3; the destination parent fingerprint passed the two-frame gate.
- **Independent checkbox localization:** under the Fleet 3 parent two-frame gate, an in-memory scan returned 14 contour candidates. Four left-column checkbox candidates were separately localized and labeled Fleet 3 ship 1-4 at [166,240,22,23], [166,317,22,23], [166,394,22,23], and [166,471,22,23]. No other contour candidate was clicked.
- **State model and classifier:** added checked/unchecked child states for Fleet 3 ships 1-4. The shared classifier and production gate now accept fleet_index 2 or 3 and ship_index 1-4, require the matching selected-fleet parent, and compare only the requested fleet/ship pair. Two consecutive matching frames remain mandatory.
- **Fingerprint data:** expanded supply_checkbox_fingerprints.json to 16 descriptors (two states for each of four ships in each of Fleets 2 and 3). Each descriptor has three samples, 13x13 RGB (507 channel values), and its own 26x26 crop. Fleet 3 similarities: 0.954001, 0.954009, 0.954032, 0.954009 for ships 1-4 respectively.
- **Reusable utilities:** generalized tools/calibrate_supply_checkbox.py with --fleet-index 2|3 and added tools/navigate_supply_fleet_tab.py. The calibration tool exposes only capture-unchecked, probe, capture-checked, and guarded restore; a probe/restore click is limited to the named checkbox and one click per action.
- **Live verification:** Fleet 3 ships 1-4 each started unchecked (0 mark pixels), received exactly one selection click (41 mark pixels), produced three checked samples, passed the checked two-frame gate, then received exactly one restore click and passed the unchecked two-frame gate. All restored mark counts were 0.
- **Final cross-row check:** all four Fleet 3 unchecked states passed their two-frame gates; all four checked expectations were rejected while the rows remained unchecked. All four targets were found in one in-memory frame at their expected bounds. No screenshot bytes were written.
- **Validation:** Python full suite 439/439 passed; Node static tests 17/17 passed; Node syntax, compileall (src/tests/tools), both utility CLI help checks, and git diff --check passed. Commit a358afee9c0ffd7234f1d9cbeaabe0f5d07680bb was pushed and read back from GitHub; all 14 changed files matched their local Git blob SHA values. Push-triggered Actions run 41463396246 completed successfully, with the test job and all workflow steps passing.
- **Safety:** no supply, supply-all, repair, dispatch, reward, or game API action occurred. Resource controls remain unverified and resupply remains disabled.


## 2026-11-02 - Live fleet-wide resupply verified for all unlocked fleets

- **UI semantics:** confirmed by live before/after state that 艦隊全補給 supplies every ship in the currently selected fleet, not all fleets simultaneously. The all-fleet workflow therefore selects each fleet with a deficit and invokes the all-ships-in-current-fleet control once. No single-ship selection/supply is required.
- **Freshness and preflight:** extended poi_port_response_observer_20261029.js to include compact api_material records. The new full-supply tool validates a natural successful /kcsapi/api_port/port witness no older than 30 seconds, exact resource-material matching, current fleet/ship matching, complete master ship supply maxima, exactly unlocked fleet IDs [1,2,3], idle mission tuples, unique ship membership, and sufficient fuel/ammunition.
- **Preflight cost:** Fleet 1 was already full. Fleet 3 required 16 fuel and 12 ammunition; Fleet 2 required 16 fuel and 0 ammunition. Total estimated cost across the three fleets: 32 fuel and 12 ammunition. One ship was damaged; resupply did not repair it.
- **Target and input gate:** added the allowlisted fleet-supply-all-button target in region [1050,590,145,60]. Live bounds [1062,604,92,30]. The click is bound to the exact screenshot SHA-256 and requires three consecutive captures with stable target bounds after the selected-fleet two-frame gate.
- **Live action proof:** one visible UI click supplied all four ships in Fleet 3 (16 fuel/12 ammunition). A separate fresh-witness cycle supplied all four ships in Fleet 2 (16 fuel/0 ammunition). Each target fleet passed post-click Poi verification and exact resource-delta verification; neither click was retried. Fleet 1 required no click.
- **Independent final audit:** Poi read-only recheck confirmed Fleet 1 5/5, Fleet 2 4/4, Fleet 3 4/4 ships at maximum fuel and ammunition: 13/13 fully supplied. All three fleets were idle. Final audited resources: 2484 fuel / 3453 ammunition. A subsequent fresh-witness --dry-run returned full_supply_not_needed with zero cost and no resource action.
- **Implementation:** added src/kancollebot/adapters/poi/full_supply.py, tools/full_supply_ui_action.py, and tests/test_full_supply.py; extended the CDP visual-target allowlist/locator and passive port witness material inventory. --authorize-full-supply is required for a resource click; --dry-run performs preflight only. The tool fails closed on stale/mismatched state, insufficient resources, unknown fleets, ambiguous target, or post-click mismatch.
- **Validation:** Python full suite 450/450; Node static tests 17/17; Node syntax, compileall, CLI help, no-op dry-run, independent audit, and git diff --check all passed.
- **Limits:** only Fleet 2 and Fleet 3 supply-tab selected-state gates are independently verified. Fleet 1 was already full and was not clicked; if Fleet 1 needs supply later, the tool blocks until a Fleet 1 tab gate exists. The current tool processes one target fleet per fresh-witness cycle. One damaged ship remains damaged. This does not enable expedition dispatch, repair, reward collection, or unattended operation.
- **Safety:** no game API request was initiated; only natural network responses were observed. No repair, dispatch, reward claim, or single-ship supply operation occurred. Screenshots stayed in memory.

- **Remote archive and CI:** commit 57befad2c94de5ee05a6c8960d703fda3bcd8105 was pushed to dev/expedition-closed-loop-mvp. GitHub readback confirmed all 10 changed file blobs exactly matched local Git blob IDs. Push-triggered Actions run 41468859238 completed successfully; the test job and every workflow step passed.


## 2026-11-03 - Current-MVP dispatch, freshness, and observer safety reapplication

- **Baseline audit:** the earlier integrated safety candidate PR #26 (head `5ac5a61118b228b2b34e2dbbd0113c259a56a2e7`) had diverged from the current `dev/expedition-closed-loop-mvp` head `9b6aab65a1ea64f39f10ad3122465cde8095601d` (10 commits ahead / 31 behind from the merge base). The remaining safety deltas were reapplied on a new branch based directly on the current head; no stale branch was rebased or merged.
- **Dispatch intent validation:** action-completion timestamps must be timezone-aware `datetime` instances at both dataclass and SQLite-store boundaries. Before saving a dispatch intent or advancing the scheduler, the coordinator now requires `fuel_ready` and `ammo_ready` to be actual booleans and both true.
- **Freshness equality hardening:** the passive snapshot consistency validator now validates both witness and current `api_mission` as four non-negative integers, both `api_ship` slot lists as valid integer IDs or the exact integer `-1` sentinel, and current ship numeric fields before equality. This rejects bool/float equality lookalikes such as `True == 1`, `10.0 == 10`, and `-1.0 == -1`.
- **Observer target discovery:** valid JSON whose root is not an array now returns structured `TARGET_DISCOVERY_FAILED` before `.filter()`. Added static source-contract tests for child-only no-disk witness mode, loopback CDP, the literal Network-only send allowlist, and absence of input/page-navigation call sites.
- **Implementation/test commit:** `542b470666e22e7f5b52895dbd3e2ea87acbf580` on `dev/dispatch-observer-safety-current-mvp-20261103`, based on `9b6aab65a1ea64f39f10ad3122465cde8095601d`.
- **Exact-head Push CI:** [run 41500461565](https://github.com/wosafety0/wosafety0clamp/actions/runs/41500461565) completed successfully. `node --test tests/static_decoder_extract.test.cjs tests/verify_cdp_snapshot.test.cjs` passed **17/17**; observer `node --check` and Python `compileall` passed; `PYTHONPATH=src python -m unittest discover -s tests -v` passed **460 tests**, with **17 optional vision tests skipped**.
- **Remote readback:** all seven changed files were read back from GitHub at the exact commit. Blobs: `dispatch_recovery.py` `864278ed07ddf0c483b85471c7b6c3180fbca35f`; `test_dispatch_recovery.py` `2039741c974838e2507b550a7a80d865a0cfdd66`; `port_freshness.py` `160d2f4255745cc2a577b1d84386ef1eaa67be4a`; `test_poi_port_freshness.py` `4109decc7077e26c90be7c63f92f92d0b14043e4`; observer `2c735ecd8c5e05820d2a291dc6a28c1f01a32bfd`; observer source test `8587ce2fa152201d2b80c3c35b6a59374882c223`; observer documentation `4296a3dc73c4ec47645967402c8d8b5053cdafb7`.
- **Safety boundary:** offline/CI changes only in this round. No Poi connection, game input, dispatch, reward, repair, resupply, game API request, `Runtime.evaluate`, page JavaScript execution, getter access, or system/proxy modification. The production freshness/dispatch gate remains fail-closed; this commit does not authorize live operation.
- **Next:** review the current-base Draft PR and retain the production guard until the production runner/UI settlement path is independently verified under explicit authorization. Fleet 1 remains read-only for dispatch; Fleet 4 remains deferred.


## 2026-11-03 - README current-MVP status reconciliation

- **Documentation correction:** updated the root README to replace the stale initial-bootstrap status and priorities with the verified current MVP scope, read-only monitor/witness boundary, recorded operator-supervised return/reward and fleet-wide supply evidence, current Fleet 1/2/3/4 roles, and remaining production integration work.
- **Commit:** `b7412d286c5cda7dc2cd932d4265c986ac13eb8b` on `dev/dispatch-observer-safety-current-mvp-20261103`.
- **Readback:** `README.md` blob `51e77c9993c4f0705982165278c89f57fbd404c9`; the obsolete “repository bootstrap is in progress” claim is absent.
- **Exact-head Push CI:** [run 41504541237](https://github.com/wosafety0/wosafety0clamp/actions/runs/41504541237) passed. Full Python suite: **460 tests**, **17 optional vision tests skipped** because vision dependencies were unavailable; Node static tests, observer syntax, and Python compileall passed.
- **Safety:** documentation-only change. No Poi connection, game input, game API request, dispatch, reward, repair, resupply, Runtime.evaluate, page JavaScript execution, getter access, or system/proxy change occurred.


## 2026-11-03 - Exact integer HTTP status in passive Poi witness

- **Finding:** the passive port-witness validator compared `source.httpStatus != 200`; Python equality allowed a malformed JSON numeric value `200.0` to compare equal to integer 200. This weakened the strict numeric-shape boundary already applied to schema version, byte counts, fleet slots, and ship fields.
- **Implementation:** `src/kancollebot/adapters/poi/port_freshness.py` now requires `type(http_status) is int` and the exact value 200 before accepting the response metadata. `tests/test_poi_port_freshness.py` adds regression coverage for `200.0`, `True`, and string `"200"`.
- **Commit:** `897ae9324e087b88bce73927808dde04a616b1f8` on `dev/dispatch-observer-safety-current-mvp-20261103`, parent `692edc4ee7b79e4063f331d7c1995d39ac1515ae`.
- **Exact-head Push CI:** [run 41546902366](https://github.com/wosafety0/wosafety0clamp/actions/runs/41546902366) succeeded. Node static extraction tests passed 17/17; passive observer `node --check` and Python `compileall` passed; Python suite passed **461 tests**, with **17 optional vision tests skipped** because OpenCV/NumPy dependencies were unavailable.
- **Remote readback:** source blob `b981eb58201396672250ef2ffc63eb31aee55c63`; test blob `38b67cad5fb9290e1c43677ffdbd2ea39112b10d`.
- **Safety:** offline source/test change only. No Poi connection, game input, game API request, dispatch, reward, repair, resupply, `Runtime.evaluate`, page JavaScript execution, getter access, or system/proxy modification. Production freshness/dispatch remains fail-closed.


## 2026-11-03 - Multi-fleet full-supply batch

- **IMPLEMENTED:** `full_supply_ui_action.py` now handles every supported unlocked fleet with a supply deficit in one invocation. It still uses the visible fleet-wide supply button once per target fleet; it does not click individual ship checkboxes.
- **Preflight:** all target fleets are selected and validated before the first resource-consuming click. Fleet 2 and Fleet 3 are supported. If Fleet 1 ever needs supply, the batch aborts before any supply click because its selected-tab UI gate is not independently verified.
- **Freshness and batch integrity:** each fleet action has its own fresh natural `/api_port/port` witness. Between actions, the batch verifies stable Poi snapshots, unchanged fleet/ship roster, completed-fleet full supply, unchanged unprocessed-fleet costs, and exact fuel/ammunition inventory deltas. Ambiguous clicks are never retried.
- **Final verification:** after the last target, every ship in every unlocked fleet must be fully supplied and total resource consumption must match the initial all-fleet preflight exactly. Dry-run output includes all target fleet IDs and planned fleet-wide click count.
- **TESTED:** focused full-supply tests 12/12 passed; KancolleBot offline CI passed with Python 459/459, Node 17/17, and compile checks. CLI help and `git diff --check` passed.
- **LIMITS:** this is still a standalone full-supply action, not yet wired into `ContinuousExpeditionSupervisor`. No live game supply action was executed during this code change. Fleet 1 remains fail-closed if it becomes under-supplied.
- **NEXT:** connect the batch resupply workflow to the continuous scheduler only after defining safe behavior when one dispatchable fleet is active and another is awaiting resupply; do not resupply a fleet on expedition or during settlement.


## 2026-11-03 - Fleet 1 supply-tab gate and no-op batch preflight

- **IMPLEMENTED:** Added the Fleet 1 selected-tab UI state, a three-sample visual fingerprint (crop [203,155,30,40], grid [10,8]), an independently bounded tab locator (region [195,155,50,50]), and its exact visual-input allowlist. The fingerprint classifier treats Fleet 1 as a child of the supply page without weakening the minimum score or margin rules.
- **LIVE VERIFICATION:** The production UI gate identified Fleet 1 on two consecutive in-memory frames with exact-match score 1.0. Fleet 2/Fleet 3 scores remained below the 0.97 acceptance threshold. The live locator returned the unique Fleet 1 tab bounds [203,164,34,31]. No screenshot bytes were saved.
- **BATCH FLOW:** The full-supply driver now recognizes Fleet 1/2/3 selected states as valid supply-page states. If the requested fleet is already selected, it records a zero-click selection result and skips the redundant tab click; otherwise it uses the existing bounded visual target and two-frame destination gate.
- **TESTED:** Focused visual/UI suite 52/52 passed. Full Python suite 462/462 passed in 2.127 seconds. Python compile checks and git diff --check passed. No JavaScript files changed; Node tests were not rerun in this stage.
- **LIVE DRY-RUN:** Starting from the supply page, the driver passed the Fleet 1 page gate, returned to Home Port through normal UI navigation, and captured a fresh natural /api_port/port witness. Preflight covered all three idle fleets and 13 ships: fuel/ammunition deficit 0/0, target fleets empty, planned resource clicks 0. The dry-run returned full_supply_not_needed; current resources were 2500 fuel and 3453 ammunition.
- **SAFETY / LIMITS:** No resource-consuming click or game API request was initiated. One ship has hull damage, which resupply does not repair. Since no fleet currently has a fuel/ammunition deficit, an actual multi-fleet resource-action run remains unverified; no deficit was manufactured for testing. The all-fleets-idle precondition and fail-closed behavior remain unchanged.
- **NEXT:** When natural supply deficits exist, run one controlled multi-fleet batch and verify every post-click state and exact resource delta. Do not connect the batch to the continuous expedition supervisor in this stage.

- **FOLLOW-UP LIVE DRY-RUN (14:35 +08:00):** Re-ran the existing `full_supply_ui_action.py --dry-run` from Home Port using the reused Poi/DC session. The UI freshness path was confirmed as HOME_PORT → SORTIE_MENU → EXPEDITION_LIST → HOME_PORT (three navigation clicks, no sortie dispatch); the final Home Port state passed its two-frame gate. A fresh natural `/api_port/port` witness was captured and matched the current Poi snapshot. All three fleets were idle; all 13 ships were checked; fuel/ammunition deficit remained 0/0, target fleets were empty, and planned resource clicks were 0. Result: `full_supply_not_needed`. No resource-consuming click or game API request was initiated. The game remained at Home Port; one damaged ship remains damaged. Checkpoint and local session log were updated.


## 2026-11-03 - Full-supply Home Port detection fast path

- **IMPLEMENTED:** `_acquire_fresh_witness()` now checks the Home Port state first. Only when that two-frame gate rejects Home Port does it scan the supported supply-page states. This avoids four unnecessary supply-page checks when the UI is already at Home Port; the Home Port two-frame requirement and supply-page fail-closed gate are unchanged.
- **TESTED:** Added two focused tests for the Home Port fast path and supply-page fallback. Full-supply tests 14/14 passed; KancolleBot offline test batch passed with Python 464/464, Node 17/17, compile checks, and `git diff --check`. The two modified Python files also passed `py_compile`.
- **LIVE DRY-RUN:** From Home Port, the optimized path confirmed Home Port on two consecutive in-memory frames, refreshed through SORTIE_MENU → EXPEDITION_LIST → HOME_PORT, captured a fresh natural `/api_port/port` witness, and verified all three idle fleets / 13 ships. Fuel/ammunition deficit 0/0, target fleets empty, resource clicks 0. Result `full_supply_not_needed`; no game API request or resource action was initiated.
- **TIMING:** The optimized dry-run completed in 36.66 seconds end-to-end. The preceding run took approximately 44.8 seconds based on event timestamps, but it was not measured with the same stopwatch method; treat the comparison as indicative, not a controlled performance claim.
- **LIMITS:** No natural supply deficit was present, so actual multi-fleet resource clicks remain unverified. One damaged ship remains damaged. The all-fleets-idle and freshness requirements remain in force.

## 2026-11-03 - P0 Poi UI arbitration and read-only dry-run hardening

- **IMPLEMENTED:** Replaced the process-local-only `UiOperationArbiter` mutex with a shared Poi UI operation lock. Windows uses the named session-local kernel mutex `Local\KancolleBot.PoiUiOperation.v1`; a process-wide reentrant thread lock preserves nested same-thread operations. Non-Windows development uses an OS file lock.
- **FAIL-CLOSED RECOVERY:** A Windows `WAIT_ABANDONED` result releases the abandoned mutex and raises `PoiUiOperationLockError`, requiring UI inspection before the next attempt. A dedicated test holds a second handle to preserve the mutex object, terminates its owner process, verifies the first subsequent operation is rejected, and verifies a later operation can reacquire the lock.
- **ENTRYPOINT COVERAGE:** The shared lock now protects full-supply actions, continuous dispatch/refresh, checkbox calibration/probe/restore, fleet-tab navigation, Ooi visual fingerprint capture/verification (including pointer movement), and the currently disabled one-expedition UI loop. The full-supply refresh helper reuses the same arbiter instance as its parent batch.
- **DRY-RUN SAFETY:** `full_supply_ui_action.py --dry-run` now obtains only a naturally occurring `/api_port/port` witness and reads the Poi snapshot. It does not open a game UI session, navigate, refresh, click, or initiate a game API request. If no fresh natural witness exists, it fails closed.
- **TESTED:** Poi lock tests **4/4**; full-supply tests **16/16**; scheduler tests **15/15**; visible UI refresh tests **9/9**; dispatch-controller tests **16/16**. Full Python suite **470/470**, Node tests **17/17**, compileall across `src`, `tests`, and `tools`, eight CLI help/import checks, and `git diff --check` all passed.
- **AUDIT:** Scanned 68 Python/JavaScript script files for CDP mouse input and click entrypoints. All active click paths are protected by the shared lock; the one-expedition production loop remains disabled pending end-to-end acceptance, with the lock guard in place if it is later enabled. The current Git repository has one registered worktree. No active KancolleBot full-supply or continuous-monitor process was found during the process audit; Poi's child processes belong to one Electron instance, while ALAS is a separate Azur Lane application.
- **HISTORICAL COPY SAFETY:** The prior read-only comparison found 8 byte-identical files, 9 same-path files with different contents, 262 files unique to the current workspace, and 59 files unique to the Cloud copy. No historical copy was deleted or overwritten; divergent files remain preserved for a separate reviewed reconciliation.
- **LIMITS:** The new passive dry-run path has offline safety-test coverage but was not rerun live in this stage because no fresh natural port witness was established. No resource-consuming game action was performed. Actual multi-fleet resource-action acceptance remains dependent on a natural supply deficit; the batch is not integrated into `ContinuousExpeditionSupervisor`.

## 2026-11-03 - Cloud repository read-only reconciliation audit

- **SCOPE:** Compared the active MVP repository `wosafety0/wosafety0clamp` at `27ec634` with the separate Cloud repository `wosafety0/KancolleBot` at `4df92dd`. The Cloud repository and its worktree were treated as read-only throughout.
- **METHOD:** Compared SHA-256 hashes by relative path, excluding `.git`, `.venv`, `node_modules`, and `__pycache__`.
- **RESULTS:** Active workspace 281 files; Cloud workspace 76 files; 8 byte-identical same-path files; 9 same-path files with different content; 264 active-workspace-only files; 59 Cloud-only files.
- **DIVERGENCE REVIEW:** The nine divergent paths are not safe duplicates. Examples include older GitHub Actions versions and missing Node checks in Cloud CI, absent optional vision dependencies in the Cloud pyproject, older read-only health-gated runtime documentation in the Cloud README, page-only CDP target handling in Cloud versus current WebView support, and different source-freshness guarantees in `ObservedState`. Core model validation and Poi MCP client tests also differ. These files require separate design review; no wholesale merge is justified.
- **PRESERVATION:** No Cloud file was modified, deleted, moved, or overwritten. All 59 Cloud-only files and all 9 divergent files remain intact. The 8 identical files were also retained under the explicit read-only constraint.
- **ARTIFACTS:** Full path/hash manifest: `D:\AI_Project\Project_KancolleBot\diagnostics\redundancy-cleanup\cloud-comparison-20261103.json`; readable inventory: `cloud-comparison-20261103.md`; selected text diffs: `cloud-divergence-diff-20261103.txt`.
- **DECISION:** Continue active MVP development only in `wosafety0/wosafety0clamp` / `dev/expedition-closed-loop-mvp`. Retain `wosafety0/KancolleBot` as a read-only historical reference; no destructive Cloud cleanup is authorized.

## 2026-11-03 - P0 Poi UI mutex lifetime correction

- **IMPLEMENTED:** Narrowed the one-expedition UI loop's shared mutex to bounded UI transactions: initial verification/dispatch, return-refresh navigation, reward-screen advancement, and Home Port recovery. The lock is released during expedition timer waits, refreshed-state polling, and reward-completion idle polling.
- **REGRESSION GUARD:** Added an AST-based test that checks expedition timer waits and reward-completion polling remain outside every shared UI mutex scope, and verifies the four intended UI transaction scopes.
- **TESTED:** Full Python unittest suite **471/471**; Node static tests **17/17**; JavaScript syntax check, Python compileall, and `git diff --check` all passed.
- **LIMITS:** The one-expedition production entrypoint remains hard-disabled pending end-to-end acceptance. This change has offline/static test coverage only; no live expedition or gameplay action was run.


## 2026-11-03 - Fleet 1 full-supply gate alignment and live read-only preflight

- **IMPLEMENTED:** Aligned select_full_supply_targets() with the already verified Fleet 1 supply-tab gate by changing its default supported fleet set from (2, 3) to (1, 2, 3). Explicitly restricted supported sets still fail closed when a requested fleet is not included.
- **REGRESSION COVERAGE:** Added a positive test proving Fleet 1 is included when it has a supply deficit and a negative test proving an explicitly unsupported Fleet 1 still blocks the batch before any action.
- **LIVE READ-ONLY PREFLIGHT:** Poi health and the read-only MCP tools passed. The visible refresh sequence HOME_PORT → SORTIE_MENU → EXPEDITION_LIST → HOME_PORT passed its two-frame state gates and captured a natural /api_port/port witness. The corrected preflight then passed with a 7.91-second witness: 3 fleets, 12 ships, total required 34 fuel / 16 ammunition, available 2500 fuel / 3453 ammunition, target fleets [1, 2, 3].
- **TESTED:** Full Python suite **472/472**; focused full-supply tests **17/17**; Node static tests **17/17**; JavaScript syntax check, Python compileall, and git diff --check all passed.
- **SAFETY / LIMITS:** The live step used only the existing visible refresh navigation and read-only Poi queries. No resupply click, repair, expedition dispatch, reward collection, or game API request was initiated. The three planned fleet-wide supply actions were not executed. The one-expedition production entrypoint remains hard-disabled pending its separate end-to-end acceptance.

## 2026-11-03 - Active workspace exact-duplicate audit

- **TRACKED SOURCE:** SHA-256 scan of all 147 Git-tracked files found zero exact duplicate files.
- **WORKTREE ARTIFACTS:** Scanned 281 files, excluding .git, .venv, node_modules, and __pycache__. Found 4 exact-hash groups containing 63 files: three non-empty duplicate help-log pairs and one group of 57 zero-byte log files. This represents 59 extra copies beyond one file per hash group.
- **CLASSIFICATION:** Every duplicate group is under the ignored logs/ directory; .gitignore explicitly excludes that directory. No duplicate source, test, or documentation files were found among tracked files.
- **PRESERVATION:** No log was deleted or modified. These diagnostic artifacts remain until a separate log-retention policy is approved.
- **ARTIFACTS:** Full tracked-file manifest: D:\AI_Project\Project_KancolleBot\diagnostics\redundancy-cleanup\internal-duplicate-files-20261103.json; worktree manifest and inventory: working-tree-duplicate-files-20261103.json and working-tree-duplicate-files-20261103.md.
- **CLOUD INVENTORY COUNT NOTE:** The current 281-file snapshot yields 264 current-only files, 8 identical paths, and 9 divergent same-path files against the 76-file Cloud snapshot. No Cloud files were changed. The earlier 262 current-only figure in the Cloud audit summary is superseded by this re-count.

## 2026-11-03 - P0 production UI controller integration

- **IMPLEMENTED:** tools/one_expedition_ui_loop.py now wires the fresh Poi monitor, Fleet 3-only scheduler configuration, durable dispatch-intent coordinator, ContinuousDispatchController, single-tick ContinuousExpeditionSupervisor, and visible-CDP CdpContinuousExpeditionUiDriver. The runtime target is deliberately fixed to Fleet 3 / mission 3; Fleets 1 and 2 are read-only, Fleet 4 is deferred, and a dedicated SQLite database is used.
- **SAFETY:** The driver uses visible CDP mouse input only. It does not invoke game APIs or mutate page state, requires two consecutive visual frames for each UI checkpoint, rejects unsupported fleet/mission mappings, and never automatically repeats an ambiguous click. The CLI requires explicit --authorize-ui-actions.
- **LIVE VERIFIED:** Fleet 2 and Fleet 3 selected-tab fingerprints were recalibrated to disjoint tab-label crops. Both selected states passed positive two-frame recognition and rejected the opposite fleet state. Mission-3 selection and the production driver's HOME_PORT -> SORTIE_MENU -> EXPEDITION_LIST -> MISSION_SELECTED -> FLEET_2_SELECTED/FLEET_3_SELECTED -> HOME_PORT preparation/recovery path passed live. Screenshot bytes remained in memory; no screenshot files were written.
- **TESTED:** Offline production integration covers monitor/controller/supervisor composition, exact Fleet 3 / mission 3 intent, fresh pre/post Poi observations, Home Port postcondition, and fail-closed behavior for an untrusted observation. Full Python suite: 487/487; Node tests: 19/19; Python compileall, production CLI help, and git diff --check passed.
- **ACCEPTANCE LIMIT:** Live execution stopped after the pre-dispatch preparation and Home Port recovery checks. The irreversible expedition-start click was not sent; no expedition was dispatched and no resources were consumed. Post-dispatch natural freshness/readback, return/result/reward/idle, resupply integration, and unattended operation remain NOT_VERIFIED.
- **NEXT:** Perform one explicitly authorized Fleet 3 / mission 3 live dispatch only when a new natural /api_port/port freshness witness is available and all pre-dispatch supply, composition, viewport, and visual gates pass. Then verify the exact active mission tuple and Home Port state before proceeding to the return/result/reward/idle stages.

## 2026-11-03 - CDP UI-refresh failure diagnostics

- **IMPLEMENTED:** CDP command-error handling now preserves only the allowlisted CDP method name and numeric protocol error code; Chromium's raw error message is discarded. The visible UI refresh blocker walks the exception chain and records only a fixed diagnostic category, method name, and numeric error code when they match an explicit safe pattern.
- **TESTED:** Added regression coverage for CDP error responses and the initial UI checkpoint failure path. Focused CDP transport tests **17/17**; visible UI refresh tests **10/10**; full Python suite **489 tests, 18 skipped, all remaining passed**. Python compileall across `src`, `tests`, and `tools`, and `git diff --check` passed.
- **SAFETY:** The initial checkpoint failure test confirms zero clicks. No Poi UI refresh, expedition dispatch, reward collection, resupply, page reload, or game-server request was performed in this change.
- **LIMITS:** This improves offline diagnosis only. It does not establish why the previous live CDP transport failed, prove that CDP is currently healthy, or establish a fresh `/api_port/port` witness. The live refresh and expedition flow remain NOT_VERIFIED.
- **NEXT:** After this diagnostic change is archived, perform a separate read-only CDP health/state check; only consider visible navigation after the current UI state is independently confirmed.

## 2026-11-03 - Bounded visual wait for freshness UI navigation

- **IMPLEMENTED:** Added a bounded `visual_attempts` parameter to the shared UI state verifier (default remains 3; accepted range 2–12). The continuous freshness monitor now requests 6 visual samples for its navigation checkpoints. The 0.97 minimum similarity, 0.04 separation margin, two-consecutive-frame requirement, and fail-closed behavior for a different recognized state are unchanged. The production dispatch path continues to use the default 3 attempts.
- **EVIDENCE:** The prior live refresh produced three UNKNOWN samples after the single sortie-menu click; a later passive two-frame check recognized SORTIE_MENU at score 1.000. Earlier live logs also showed the first post-click sample UNKNOWN followed by successful recognition on the next sample. This supports extending the bounded observation window rather than weakening thresholds.
- **TESTED:** Added regression coverage for three UNKNOWN samples followed by two consecutive matching samples. Focused tests: UI loop 40/40, visible refresh 10/10, monitor runtime 10/10. Full Python suite: **490 tests, 18 skipped, all remaining passed**. Compileall and `git diff --check` passed.
- **LIMITS / SAFETY:** This change has offline test evidence only. No new live UI click or refresh was performed after the change. The later passive SORTIE_MENU recognition does not establish a fresh `/api_port/port` witness. Dispatch, reward collection, resupply, and unattended operation remain NOT_VERIFIED.
- **NEXT:** After remote archive and readback, run one controlled freshness-only navigation using the 6-sample monitor verifier; verify a natural non-cached `/api_port/port` response before considering any dispatch workflow.

## 2026-11-03 - Six-sample freshness navigation live verification

- **LIVE VERIFIED:** The monitor-only visible navigation completed `HOME_PORT -> SORTIE_MENU -> EXPEDITION_LIST -> HOME_PORT`. The reset from the previously confirmed SORTIE_MENU to HOME_PORT required five visual samples (the first three were UNKNOWN); the refresh sequence then confirmed SORTIE_MENU on samples 2 and 3, EXPEDITION_LIST on samples 1 and 2, and final HOME_PORT on samples 3 and 4. The original 0.97 score threshold, 0.04 margin, and consecutive-two-frame requirement were unchanged.
- **FRESHNESS WITNESS:** The passive observer returned `captured`; the monitor returned `MONITOR_TICK_OK` with exit code 0. The production reader accepts only an in-memory witness from the passive observer. Its capture path requires a natural HTTP 200 `/kcsapi/api_port/port` response from the Kancolle server, skips disk-cache/service-worker/prefetch-cache responses, validates the response body structure, and checks the witness age and exact fleet/ship field equality against the current Poi Redux read. `ObservedState.require_fresh()` then requires `source_freshness_verified=True`; the successful monitor tick proves this gate passed for this observation.
- **READINESS:** The read-only monitor reported Fleet 2 `not_ready` because at least one ship is damaged, and Fleet 3 `not_ready` because of insufficient fuel. No fleet was dispatched.
- **SAFETY:** `game_actions_enabled=false`. The observer initiated no game API request and sent no game input. No dispatch, reward collection, or resupply click occurred. Screenshots remained in memory; no screenshot files were written.
- **ARTIFACTS:** Local diagnostic log `logs/freshness_retry_6frame_20261103.jsonl`; read-only event database `logs/freshness_refresh_20261103_six_attempts.sqlite3`.
- **DOCUMENTATION CORRECTION:** Corrected the stale module docstring in `port_freshness.py`, which incorrectly claimed the validator was not wired into production; `PoiExpeditionReader` does call it to derive `source_freshness_verified`.
- **TESTED:** Freshness validator 7/7, Poi reader 18/18, background provider 6/6, passive observer 10/10; compileall and `git diff --check` passed. The previously completed full Python suite remains 490 tests with 18 skipped and all remaining passed.
- **LIMITS / NEXT:** This verifies one natural port witness and read-only monitor reconciliation, not expedition dispatch or its post-dispatch mission tuple. Fleet 3 currently needs fuel; before any resource-consuming supply action, perform a read-only resource/supply preflight and confirm the exact target and resource delta. Dispatch remains NOT_VERIFIED.

## 2026-11-03 - Fleet 3 fresh supply-cost preflight

- **LIVE VERIFIED:** Reused the passive port observer and existing full-supply preflight helpers with the six-sample visible-state verifier. The navigation sequence completed `HOME_PORT -> SORTIE_MENU -> EXPEDITION_LIST -> HOME_PORT`; three visible navigation clicks were sent. The natural port witness was captured at 11:29:44.420 UTC and matched the current stable Poi fleet/ship/resource read; the preflight completed at 11:29:49.970 UTC (approximately 5.55 seconds after witness capture).
- **FLEET 3 COST:** Four ships; fuel deficit **16**, ammunition deficit **0**. Current resource inventory: **2500 fuel / 3453 ammunition**. Resources are sufficient for Fleet 3's calculated supply cost.
- **READ-ONLY SCOPE:** The same snapshot showed Fleet 1 deficit 12 fuel / 8 ammunition and Fleet 2 deficit 6 fuel / 8 ammunition. These are recorded as observations only; Fleet 1 and Fleet 2 remain excluded from any action. The generic all-fleet plan reported all three as needing supply, so the existing all-fleet action loop must not be used for the Fleet 3-only task.
- **SAFETY:** Zero supply clicks, zero dispatch clicks, zero game API requests. Only the three visible navigation clicks were sent to obtain the natural witness. The UI ended at HOME_PORT; screenshots remained in memory.
- **ARTIFACT:** Local log `logs/fleet3_supply_preflight_20261103.jsonl`.
- **LIMITS / NEXT:** This is a verified cost preflight, not a completed supply action. Add an explicit Fleet 3-only target boundary to the supply workflow and tests before considering any resource-consuming click. Do not supply Fleet 1 or Fleet 2. Expedition dispatch remains NOT_VERIFIED.

## 2026-11-03 - Fleet 3-only supply action boundary

- **IMPLEMENTED:** Added explicit `target_fleet_ids` filtering to the pure supply target selector and progress validator. The production UI supply tool now fixes `TARGET_FLEET_IDS=(3,)`; Fleet 1 and Fleet 2 deficits are logged as read-only observations and never enter the resource-action queue. Post-action verification checks Fleet 3 completion and its exact target cost/resource delta, while confirming non-target fleet supply values remain unchanged.
- **SAFETY:** The `--authorize-full-supply` CLI description now explicitly states Fleet 3 only. No live supply click or resource-consuming action was performed in this change.
- **TESTED:** Added tests for Fleet 3-only target selection, non-target state immutability, invalid target IDs, and runtime dry-run wiring. Full Python suite: **493 tests, 18 skipped, all remaining passed**. Supply tests 20/20; freshness validator 7/7; Poi reader 18/18. Compileall, CLI help, and `git diff --check` passed.
- **CONSERVATIVE LIMIT:** The current shared plan builder still requires all three unlocked fleets to be idle and the resource inventory to cover all observed fleet deficits. This can block a Fleet 3-only action unnecessarily, but does not expand the target set or permit Fleet 1/2 actions.
- **NEXT / NOT VERIFIED:** The Fleet 3-only execution path has offline test coverage but has not yet been exercised with a resource click. Obtain a new natural freshness witness at execution time and verify the Fleet 3 target/cost again. Actual supply requires explicit runtime authorization; dispatch and the full expedition loop remain NOT_VERIFIED.

## 2026-11-03 - Fleet 3-only target boundary live preflight

- **LIVE VERIFIED:** The updated production preflight helper completed the visible navigation `HOME_PORT -> SORTIE_MENU -> EXPEDITION_LIST -> HOME_PORT` and captured a natural port witness. It returned `source_freshness_verified=true`, `target_fleet_ids=[3]`, and `selected_target_fleets=[3]`.
- **FLEET 3 COST:** Four ships; fuel required **16**, ammunition required **0**. Available inventory: **2500 fuel / 3453 ammunition**. The target cost is covered by current resources.
- **READ-ONLY FLEETS:** Fleet 1 deficit 12 fuel / 8 ammunition; Fleet 2 deficit 6 fuel / 8 ammunition. Both appear only in `read_only_fleet_deficits`; neither is selected as an action target.
- **SAFETY:** Three visible navigation clicks were used to obtain the natural witness. Zero supply clicks, zero dispatches, zero game API requests. The UI ended at HOME_PORT; screenshots remained in memory.
- **ARTIFACT:** Local log `logs/fleet3_supply_preflight_after_target_filter_20261103.jsonl`.
- **LIMITS / NEXT:** This verifies Fleet 3-only targeting and fresh cost preflight, not the resource-consuming supply click. The explicit `--authorize-full-supply` action has not been run. Obtain explicit authorization before spending the 16 fuel; dispatch and the complete expedition loop remain NOT_VERIFIED.

## 2026-11-03 - Six-sample visible refresh state verification

- **IMPLEMENTED:** The Fleet 3 supply workflow's visible refresh verifier now allows up to six visual samples when confirming each navigation checkpoint. The existing requirement for two consecutive matching in-memory frames remains unchanged; this only accommodates delayed rendering before fail-closed evaluation.
- **TESTED:** Added a unit test asserting that the production refresh verifier passes `visual_attempts=6` to the shared UI state gate. Full Python suite: **494 tests, 18 skipped, all remaining passed**. Fleet supply tests: **21/21**. Production expedition integration tests: **2/2**. Compileall and `git diff --check` passed.
- **LIMITS / NEXT:** This improves refresh checkpoint tolerance but does not itself verify or perform a supply action. Fleet 3 remains the only configured target; actual supply still requires a fresh natural port witness, live state/cost validation, one visible supply click, and post-action verification. Dispatch and the full expedition loop remain NOT_VERIFIED.

## 2026-11-03 - Six-sample return-home gate and runtime environment

- **IMPLEMENTED:** The path that exits a verified supply page back to HOME_PORT now uses the same six-sample visual state verifier as the visible freshness-refresh sequence. The gate still requires two consecutive matching in-memory frames.
- **RUNTIME REQUIREMENT:** The production visual locator dependencies (OpenCV and NumPy) are installed in the project `.venv`, not the system Python. Run the production supply tool with `.venv\\Scripts\\python.exe` so visual target localization is available.
- **TESTED:** Added a test proving supply-page return navigation uses the six-sample verifier. Supply tests: **22/22**. Full Python suite: **495 tests passed**. Compileall and `git diff --check` passed.
- **LIMITS / NEXT:** No supply-resource click has yet been verified. The Fleet 3-only target, fresh natural port witness, live state/cost checks, visual Fleet 3 selection, and post-click resource/ship verification remain mandatory. Dispatch and the full expedition loop remain NOT_VERIFIED.

## 2026-11-03 - Fleet 3 single supply action live verified

- **LIVE VERIFIED:** One visible fleet-wide supply click was sent to Fleet 3 only. A later stable double-read of the Poi store confirms all **4/4 Fleet 3 ships are fully supplied**. Fleet 3 mission state is idle `[0,0,0,0]`.
- **COST:** The preflight inventory was **2500 fuel / 3453 ammunition** and the target cost was **16 fuel / 0 ammunition**. The first stable post-action readback showed **2484 / 3453**, matching the exact expected delta. A later stable readback showed **2487 / 3453**; the intervening +3 fuel change is observed but not attributed.
- **SCOPE / SAFETY:** Exactly **one** Fleet 3 supply click; **zero retries**; no game API requests initiated. The production post-action checks confirmed non-target fleet fuel/ammunition values were unchanged. Fleet 1 and Fleet 2 remain read-only.
- **ARTIFACT:** `logs/full_supply_ui_action.jsonl` event `full_supply_completed_and_verified_after_delayed_store_sync` records the delayed stable readback and one-click boundary.
- **LIMITS / NEXT:** The game action is complete and verified, but the first immediate post-click Poi read did not yet show full supply; later stable readback did. Improve the production verifier with bounded read-only polling for store propagation before treating a non-final first read as final. Do not repeat the supply click. Expedition dispatch and the full closed loop remain NOT_VERIFIED.

## 2026-11-03 - Bounded read-only post-supply verification

- **IMPLEMENTED:** Replaced the single fixed-delay post-click Poi read with bounded read-only polling (up to 10 stable snapshots, 0.5 seconds between polls). The first poll is immediate. Polling never sends another UI click and never initiates a game API request.
- **SAFETY GATES:** Every poll verifies unchanged fleet membership/mission state and master data; non-target ship fuel/ammunition must remain unchanged; target ship fuel/ammunition may only increase toward the corresponding master maximum; material values must remain within the preflight-to-exact-cost range. Success requires every target-fleet ship fully supplied and the exact expected fuel/ammunition delta. Invalid changes stop immediately; exhaustion of the poll limit fails closed with an explicit no-repeat instruction.
- **OBSERVABILITY:** Added per-poll structured events and a success event recording the poll count, final resources, single-click count, and retry_allowed=false.
- **TESTED:** Added four focused tests covering delayed store propagation succeeding on a later read, bounded timeout without a second click, unexpected resource delta rejection, and non-target fleet mutation rejection. Full Python suite: **499 tests passed**. compileall and git diff --check passed.
- **LIMITS / NOT VERIFIED:** This change was validated with offline tests only; no live supply click, game API request, Poi navigation, or game-state mutation was performed. The Fleet 3 supply already completed and verified in the preceding live run; do not repeat it. Expedition dispatch, large-damage interception, and the full expedition loop remain NOT_VERIFIED.
- **ARTIFACTS:** tools/full_supply_ui_action.py; tests/test_full_supply.py. Local validation report: logs/delayed_supply_verification_validation_20261103.txt.


## 2026-11-03 - Pre-dispatch Taiha interception

- **IMPLEMENTED:** Poi fleet normalization now records heavy_damage_ship_ids separately from the existing damaged_ship_ids. Taiha (大破) is classified at **25% HP or below**, using integer arithmetic (api_nowhp * 4 <= api_maxhp) with the inclusive boundary covered by tests.
- **DISPATCH GATE:** ContinuousDispatchController checks the fresh normalized fleet snapshot before creating a durable dispatch intent. Any Taiha ship emits dispatch_blocked_large_damage with fleet/ship IDs, the 25% threshold, dispatch_clicks_sent=0, and replacement_implemented=false, then raises and stops. Missing or invalid HP data continues to fail closed during normalization.
- **SAFETY / SCOPE:** No ship replacement, fleet composition change, game UI click, dispatch, or game-server request was performed. Healthy-ship replacement is deliberately deferred. The existing general damaged-ship readiness policy remains unchanged and is stricter than the new Taiha-specific gate.
- **TESTED:** Added exact-25%-boundary and above-threshold classification tests, plus a controller test proving Taiha blocks before intent creation and UI input. Full Python suite: **502/502 passed**. Project Python compileall and git diff --check passed.
- **LIMITS / NOT VERIFIED:** Offline tests verify the gate logic only; no live Poi Taiha snapshot or dispatch attempt was performed. Fleet 3 / mission 3 dispatch, healthy-ship replacement, and the complete expedition loop remain NOT_VERIFIED.

## 2026-11-03 - Read-only expedition list scanner and renderer-block recovery

- **IMPLEMENTED (scanner):** `calibration/expedition_list.py` reads the expedition list panel as pixels only: the selected area tab is found from the orange label text (measured 567 highlighted pixels versus at most 9 on a neighbour), each row is anchored on the top edge of its text band at `y = 250 + 45k` with capacity 8, and a row's identity is a 16-bin level histogram over `x = 233..745` (same row 0 difference, different rows above 20, tolerance 6). `ExpeditionListIndex` stores signatures per page and recomputes coordinates only on a newly captured image, so a stale screenshot cannot be re-used for a fresh click. `classify_boundary()` returns `unknown` with `full_page_without_scroll_affordance` when a page is full and no scroll control is measurable, and never reports "end of list".
- **IMPLEMENTED (recovery):** `adapters/poi/renderer_block.py` probes the game renderer on a connection of its own and distinguishes responsive / blocked / unreachable, so a transport made unusable by a timeout cannot be mistaken for a healthy surface. `adapters/poi/modal_window.py` finds the blocking dialog by requiring a `#32770` class window whose owner exists and is disabled, accepts a Cancel button only when exactly one label matches, reads the body through UI Automation (the text is owner-drawn inside `DirectUIHWND`, so `GetWindowText` and MSAA see nothing), and refuses to press unless the body mentions a page refresh.
- **VERIFIED (live):** The scanner recognised 8/8 rows on a stored screenshot with no labelling, order `consistent`. A real refresh prompt was measured as `hwnd` class `#32770`, 366x129, owner disabled; UI Automation read `エラーが発生したため、ページ更新します。` plus buttons `确定 / 取消 / 关闭`. After the automated Cancel the game renderer answered in 0.001 s, the owner window returned to `enabled=True`, and the DOM was not rebuilt (`backendDOMNodeId=20`, 146 nodes), i.e. the session did not turn into nekomimi.
- **SAFETY:** The scanner is zero-click. The recovery path spends at most `--modal-dismiss-limit` presses (default 3), and a read-only monitor run sets the budget to `None` so it can never touch a window.
- **TESTED:** Full Python suite **756 tests, 18 skipped, OK**; Node static tests **17/17**; compileall and `git diff --check` clean.
- **LIMITS / NOT VERIFIED:** Only the 鎮守府 tab's first-row offset has been confirmed on the live game; the other six area tabs are unmeasured and the scroll affordance geometry is unknown until an account with a longer unlocked list is available (`--probe-band` is the read-only entry point for that). The refresh-prompt body marker is a single measured Japanese string. The budget running out leaves the process running without pressing again; whether that should instead exit is still open. Dispatch, reward collection and the complete loop remain NOT_VERIFIED.
- **ARTIFACTS:** `src/kancollebot/calibration/expedition_list.py`, `tools/scan_expedition_list.py`, `src/kancollebot/adapters/poi/renderer_block.py`, `src/kancollebot/adapters/poi/modal_window.py`, `tools/check_poi_renderer.py`, `tests/test_expedition_list_scanner.py`, `tests/test_renderer_block.py`, `tests/test_modal_window.py`.

## 2026-11-03 - Live diagnostic capture, and the tab strip pitch confirmed

- **IMPLEMENTED:** The monitor gained an opt-in `--diagnostic-capture-dir`. When a UI state is confirmed by two consecutive in-memory fingerprints, the screenshot behind that verdict is written once as `<STATE>.png`. It is off by default (screenshots otherwise stay in memory), the payload must actually start with a PNG signature, and a write failure is reported as `diagnostic_capture_failed` without ever blocking the state gate - the capture is evidence for later analysis, not a safety precondition.
- **LIVE VERIFIED (monitor):** One `--enable-ui-refresh --once` run navigated HOME_PORT -> SORTIE_MENU -> EXPEDITION_LIST -> HOME_PORT, confirming each checkpoint with two consecutive fingerprints, and ended with `source_freshness_verified=true` and `MONITOR_TICK_OK`. Fleet 2 came back `not_ready` (fuel and ammunition), Fleet 3 `ready_hold`.
- **LIVE VERIFIED (scanner):** The captured 1200x720 EXPEDITION_LIST screenshot was scanned offline with no labelling and produced **8/8** rows in display order `consistent`, resolving to 01 練習航海, 02 長距離練習航海, 03 警備任務, 04 対潜警戒任務, 05 海上護衛任務, 06 防空射撃演習, 07 観艦式予行, A1 兵站強化任務. Tab scores were **567, 9, 0, 2, 0, 0, 0**.
- **MEASURED (tab strip):** A variable-width tab hypothesis was tested against the capture and rejected: the low-saturation separator columns between tabs sit at **236, 296, 356, 416, 476, 536, 596**, exactly `176 + 60k`, so the uniform 60px pitch already in the code is correct and the seven area slots are real. The measurement is recorded next to the constants so it is not re-derived.
- **SAFETY:** Three ordinary visible navigation clicks, no dispatch, no supply, no reward collection, no game API request. Screenshots went to a git-ignored directory.
- **TESTED:** Full Python suite **759 tests, 18 skipped, OK** (three new tests cover capture-when-asked, capture-never-by-default, and an unreadable payload being reported without blocking). compileall and `git diff --check` clean.
- **LIMITS / NOT VERIFIED:** Only the first area tab has ever been observed *selected*, so the boundary of each area's own list, and whether the row band shifts per area, are still unverified; a tab that opens on a later area has never been seen. The scroll affordance geometry remains unknown until an account with a longer unlocked list is available. Dispatch and the complete loop remain NOT_VERIFIED.
- **ARTIFACTS:** `tools/run_continuous_expedition_monitor.py`, `tools/one_expedition_ui_loop.py`, `tests/test_one_expedition_ui_loop.py`, `src/kancollebot/calibration/expedition_list.py`.

## 2026-11-04 - Closed-loop settlement stage A: settlement controller, driver methods, supervisor orchestration

- **PLANNED (user-decided scope):** The expedition closed loop targets Fleets 2+3; live automatic reward collection and resupply were explicitly authorised by the operator; all visible settlement/resupply UI actions are to be consolidated on `PoiCdpContinuousUiDriver` (with `ProductionStateGate`), keeping the narrow F3/M3 driver as a fallback reference until the unified driver's dispatch path is re-verified live.
- **IMPLEMENTED (controller):** `expeditions/continuous_settlement_controller.py` mirrors the dispatch controller's contract: explicit `allow_ui_actions` gate, shared `UiOperationArbiter`/state reader/event sink, fresh `poi-redux:` observation before and after every UI advance (`newer_than` the action timestamp), and `settle_one_returned(fleet_id)` walking `RETURNED_PENDING_UI -> RETURN_RESULT -> BONUS_ITEMS -> RESUPPLY_REQUIRED` via `confirm_return_result`/`confirm_bonus_items`/`confirm_reward_claimed`. A re-verified observation that disagrees with the persisted returned mission identity, or a non-idle post-claim state, blocks the fleet and emits `settlement_blocked`; automatic retry is forbidden by design.
- **IMPLEMENTED (driver):** `PoiCdpContinuousUiDriver` gained `SettlementStep`/`SettlementPlan` (reviewed click chains `HOME_PORT_RETURN_NOTICE -> RETURN_RESULT -> BONUS_ITEMS -> HOME_PORT`, chain-validated) plus `collect_return_result`/`claim_bonus_items`/`claim_reward`, reusing the existing `_verify_checkpoint` two-frame gate per step and returning structured `UiStateEvidence`. Without a configured reviewed plan, or for a foreign fleet_id, the driver fails closed before any click. No production default plan exists yet: reward-screen coordinates are NOT calibrated.
- **IMPLEMENTED (supervisor):** `ContinuousExpeditionSupervisor` accepts an optional `settlement_controller` (must share the scheduler, state reader and event sink). When a fleet is in a settlement phase and the controller is enabled, `tick()` drives one settlement per tick and then continues to dispatch READY fleets in the same tick; without the controller, or with live actions disabled, the previous fail-closed `fleet_<id>_settlement_pending` pause is preserved.
- **TESTED:** 24 new offline tests: settlement controller (enablement gate, scope gate, phase gate, returned-snapshot requirement, happy path, mid-phase resume, identity mismatch blocking, non-idle post-claim blocking, stale observation refusal), supervisor settlement orchestration (pause preserved without/with disabled controller, settle-then-dispatch same tick, one settlement per tick, shared-component validation), driver settlement plan validation and execution (fail-closed without plan, wrong fleet, per-step checkpoints, mid-chain failure stops remaining clicks). Full Python suite **783 tests, 18 skipped, OK** (baseline 759).
- **SAFETY:** All new code is offline-verified only; no live click, dispatch, supply, reward collection or game API request was performed in this stage. Every settlement click requires: explicit `allow_ui_actions` on both controller and supervisor path, a reviewed `SettlementPlan`, per-step two-frame gate evidence, and a fresh post-click Poi observation consistent with the persisted mission identity.
- **LIMITS / NOT VERIFIED (live):** Reward-screen click coordinates are uncalibrated (no `SettlementPlan` production asset exists); the accessibility markers for `RETURN_RESULT`/`BONUS_ITEMS` have not been observed against real reward screens; no read-only two-frame verification of a real settlement screen has been run; no live settlement has been performed. Stage B (resupply driver + gate evidence + production plan calibration) and Stage C (Fleet 2 dispatch driver + interleaved orchestration) remain PLANNED. The complete closed loop remains NOT_VERIFIED.
- **ARTIFACTS:** `src/kancollebot/expeditions/continuous_settlement_controller.py`, `src/kancollebot/adapters/poi/cdp_ui_driver.py`, `src/kancollebot/expeditions/continuous_supervisor.py`, `tests/test_continuous_settlement_controller.py`, `tests/test_continuous_supervisor_settlement.py`, `tests/test_continuous_ui_driver.py`.

## 2026-11-04 - Expedition list scroll control measured, verified live, and gated

- **IMPLEMENTED (geometry):** `calibration/expedition_list.py` now owns the measured scroll controls: `DEFAULT_SCROLL_UP_BOX = (431, 183, 467, 219)` (centre 449,201) and `DEFAULT_SCROLL_DOWN_BOX = (433, 607, 468, 645)` (centre 450,626), both bounding the gold chevron *plus* its PREV/NEXT label, in the 1200x720 capture space. A gold pixel is `r>=195, g>=165, b<=130, r-b>=90`; the boxes held 261-372 such pixels across frames against 0 anywhere else in the panel, so the floor sits at 120. `ScrollAffordance` carries the box and the count so a caller can audit the verdict instead of trusting a coordinate.
- **IMPLEMENTED (scan/boundary/proposal):** `scan_page` probes both boxes and records `scroll_up`/`scroll_down` plus `scroll_probed` (whether the boxes fitted inside the capture). `classify_boundary` gained `PAGED` for "a measured control is present, so the list has more pages than one", keeping that distinct from `MORE_BELOW`. `plan_scroll_step` returns `click_measured_control` with the measured centre for either direction, `blocked_at_end` on a short page, `blocked_at_top` when the up control is absent, and `blocked_no_measured_control` when the boxes were read and held nothing.
- **FIX (alignment, found live):** `align_pages` used to demand that every row in the overlap match, so a real scroll failed with "consecutive pages share no rows". The cause is the viewport: it clips the first and last visible band, which measured 32px before a scroll and 24px after while all five interior rows matched at distance 0. The two ends of the overlap are now exempt, an interior mismatch still invalidates the shift, and `support` reports only the comparable rows.
- **FIX (false MORE_BELOW, found live):** on the last page NEXT disappears, yet the legacy dark-cluster probe still found a glyph in the strip and claimed `MORE_BELOW`. Measured answers now outrank it, and the legacy probe no longer proposes a point at all once the boxes were probed: on the live last page that fallback pointed at `(782, 593)`, the star icons at the rows' right edge, which is not a scroll control.
- **VERIFIED (live, 2026-11-04 11:1x-11:2x, 鎮守府海域):** one click on the measured NEXT point moved the page by exactly one row (alignments 1/5, 1/7, 1/6 on three consecutive steps, then a fourth click changed nothing). Clicking the measured PREV point restored the starting page key-for-key. Walking to the top proved both controls are directional: PREV absent on the first page, NEXT absent on the last. The tab holds **12 expeditions** - `01` 練習航海, `02` 長距離練習航海, `03` 警備任務, `04` 対潜警戒任務, `05` 海上護衛任務, `06` 防空射撃演習, `07` 観艦式予行, `08` 観艦式, `A1` 兵站強化任務, `A2` 海峡警備行動, `A3` 長時間対潜警戒, `A4` 南西方面連絡線哨戒 (A4 flagged NEW) - so four entries never appear on the eight-row page. The game was left on the exact page the session started from (verified `same_view` and key-for-key equality).
- **TESTED:** 16 new offline tests (measured control detection and floor, box-out-of-capture silence, PAGED boundary, measured proposal for both directions, blocked_at_top, blocked at a short page, clipped-edge alignment, interior-mismatch refusal, last-page proposals). Full Python suite **799 tests, 18 skipped, OK** (baseline 759 before Stage A).
- **SAFETY:** every live click was a scroll on a list, reversible and verified by restoring the original page; no expedition was started, no fleet touched, no game API request made. The click point can only ever land in the empty strip under the last row or above the first. The tool `tools/scan_expedition_list.py` still never clicks: it reports the measured controls and proposes a step.
- **LIMITS / NOT VERIFIED:** only `鎮守府海域` has been walked; the other six area tabs have never been opened, so whether their lists share this chrome or hide entries differently is unmeasured. The gold count varied 261-372 between frames for an unchanged control (likely hover/animation), which the 120 floor absorbs but which has not been explained. Rows beyond the first page are still unbound: the index only knows the signatures seen so far, so `A2`/`A3`/`A4` are readable but unnamed until labelled. No paging runner exists yet - the walk was a one-off script under `logs/`, so "read the whole tab in one command" remains PLANNED.
- **ARTIFACTS:** `src/kancollebot/calibration/expedition_list.py`, `src/kancollebot/calibration/__init__.py`, `tools/scan_expedition_list.py`, `tests/test_expedition_list_scanner.py`; live frames and reports in `logs/diag_expedition_20261104/` (`scroll_before.png`, `scroll_after_down.png`, `scroll_after_up.png`, `walk_report.json`, `walk_00_start.png` .. `walk_03_down.png`, `now.png`, `restored.png`).

## 2026-11-04 - Expedition catalogue: separate the account's unlocks from the area's ceiling

- **CORRECTION (previous entry):** the entry below says the `鎮守府海域` tab "holds twelve expeditions". That conflated two different facts. Twelve is what *this account had unlocked*; the area itself defines fourteen. The operator stated the rule explicitly: the expedition list unlocks with game progress, every account's list differs, and the wiki is the only complete reference. Nothing in this stage changes any earlier measurement, only the interpretation.
- **IMPLEMENTED (reference layer):** new `calibration/expedition_catalog.py` holds `ExpeditionCatalog` - an immutable `number -> (name, area)` table with `SOURCE = "wikiwiki.jp/kancolle / 遠征 (遠征の開放要件)"` and `RETRIEVED_ON = "2026-11-04"` recorded on the object, so the data's age is visible instead of implied. `DEFAULT_CATALOG` covers **63 expeditions in 6 areas**: 鎮守府海域 14 (01..08, A1..A6), 南西諸島海域 14 (09..16, B1..B6), 北方海域 8 (17..24), 西方海域 11 (25..32, D1..D3), 南方海域 10 (33..40, E1..E2), 南西海域 6 (41..46). Construction rejects duplicate numbers, empty names/areas and unparseable display numbers.
- **IMPLEMENTED (difference, not status):** `catalog.coverage(observed, area=...)` returns `Coverage(observed, missing, foreign)` plus `is_complete`. It normalises case/whitespace, collapses duplicates, sorts by `disp_no_sort_key`, and raises on an unknown area rather than silently comparing against nothing. `missing` is documented as *locked here OR never scanned* - pixels cannot distinguish the two, and the type does not pretend to.
- **IMPLEMENTED (naming without OCR):** `ExpeditionListIndex.entries(name_resolver=...)` fills `ListEntry.name` from any `str -> str | None` mapper (`DEFAULT_CATALOG.name_of` fits directly). Identity still comes from pixel bindings, so a number the catalogue does not know leaves `name` unset instead of invalidating the row. This also finally uses the `name` field that had been declared and never populated.
- **IMPLEMENTED (tool):** `tools/scan_expedition_list.py --catalog` prints the coverage block and names rows; `--area` scopes the comparison. Without `--area` the area is *inferred from the numbers themselves* (`_infer_area`), which sidesteps the missing tab-index-to-area mapping: a homogeneous list identifies its own area, a mixed or unrecognised one returns nothing and the caller must be explicit. JSON output gained a `catalog` block carrying area/observed/missing/extra/size/source/retrieved_on.
- **VERIFIED (offline, against live captures):** scanning the real top page with the eight visible rows labelled `01..08` produced `area: 鎮守府海域`, `observed 8`, `missing A1, A2, A3, A4, A5, A6`, and resolved all eight names from the catalogue (練習航海 .. 観艦式) with no OCR involved. Feeding the twelve rows the account actually has unlocked (`01..08`, `A1..A4`) yields `missing A5, A6` - the exact two expeditions the live walk never saw. This is the concrete demonstration that the account state and the area ceiling differ.
- **TESTED:** 20 new offline tests: catalogue size/area distribution/uniqueness/ordering, lookups including a lowercase number and an unknown one, unknown-area refusal, the live-unlock-vs-ceiling regression anchor, foreign-number reporting, duplicate/whitespace normalisation, whole-catalogue comparison, `describe`, four construction rejections, a custom catalogue standing in for the default, and `entries` naming with and without a resolver. Full Python suite **819 tests, 18 skipped, OK** (799 before this stage).
- **SAFETY:** read-only and offline. No game input, no click, no scroll, no game API request in this stage. The catalogue is static data: it cannot observe, unlock, or claim anything about the account.
- **LIMITS / NOT VERIFIED:** the wiki page's `詳細一覧表` section was truncated while reading, so expeditions newer than `A6` are unverified - the 63-entry table is a floor for the newest areas, not a proven maximum. The screen has 7 area tabs but the wiki table names 6 areas; the seventh tab's area has never been observed and no tab-index-to-area mapping is asserted anywhere. `missing` remains ambiguous between locked and unscanned, which can only be resolved by a full paging walk of the tab. Only `鎮守府海域` has been looked at live at all; the other five areas' names are wiki-sourced and have not been checked against the screen.
- **ARTIFACTS:** `src/kancollebot/calibration/expedition_catalog.py`, `src/kancollebot/calibration/__init__.py`, `src/kancollebot/calibration/expedition_list.py`, `tools/scan_expedition_list.py`, `tests/test_expedition_catalog.py`, `tests/test_expedition_list_scanner.py`.

## 2026-11-04 - One-command tab walk, and the seven area tabs measured

- **IMPLEMENTED (walker):** new `tools/page_through_expedition_list.py` is the first tool in this project that pages a tab end to end. It is separate from `tools/scan_expedition_list.py` (which still never clicks) and refuses to run without `--allow-scroll-clicks`. Every click it makes must be a *measured* scroll chevron (`plan_scroll_step` returning `click_measured_control`); anything else stops the walk. Order is: climb to the true top, index from there downwards, then return to the exact starting page.
- **FIX (order, found live):** the first version walked straight down from wherever the list happened to be. On a live run that page held rows 2..9, so `01` was silently skipped and the walk claimed 11 rows for a 12-row list. A list is not guaranteed to be showing its first row - a previous scroll leaves it part-way - so the walk now climbs until `PREV` is absent before indexing anything.
- **FIX (identity, found live):** `ExpeditionListIndex.add_page` resolved numbers by *exact* signature key, so pages after the first came back completely unbound: the same row's dark-pixel total drifts by a few counts between frames (`01281` vs `01279`), which changes the key. Added `ExpeditionListIndex.resolve`, which tries the exact key, then falls back to `RowSignature.matches` within the tolerance, and returns nothing when two different numbers both match. Live bindings were at least 14 apart in bin distance against a tolerance of 6, so the ambiguity branch has not been exercised on real data - it is there so a future collision fails closed instead of mislabelling a row.
- **FIX (labelling, found live):** added `unbound_positions()`/`label_positions()` so a freshly walked tab can be labelled in one step, in list order. Positions rather than `(page, row)` pairs, because a row visible on two pages is one list position. The count must match exactly.
- **FIX (edge clipping, found live):** the first `label_positions` run refused a valid label set with "position 8 was read inconsistently across pages". The cause is the viewport, again: a row sitting at the top or bottom edge is clipped, so its fingerprint differs from the same row mid-page. Bindings are now taken only from non-clipped readings, with clipped ones used just when a position never appeared anywhere else.
- **IMPLEMENTED (tab map):** `DEFAULT_TAB_AREAS` now records the seven labels read off a live capture: 鎮守府海域, 南西諸島海域, 北方海域, 南西海域, 西方海域, 南方海域, 中部海域. **The game's order is not the wiki table's order** - the wiki lists 西方 before 南西 - so a tab index mapped by the wiki's row order would mislabel two areas. This is human-read from the screenshot, not OCR; the project still has no text recognition.
- **IMPLEMENTED (cross-check):** both tools now resolve the area from the tab index *and* from the numbers, and report `AREA_DISAGREEMENT` instead of silently preferring one. `ExpeditionCatalog.area_of_numbers` owns the number-based inference so the two tools cannot drift apart.
- **VERIFIED (live, 2026-11-04 11:3x-11:4x):** one command walked `鎮守府海域` from the true top to the bottom and back: **10 clicks, 5 pages, 4 down steps, 12 rows named, `missing A5, A6`, restored=True**. An empty tab is handled as a legitimate state, not a failure.
- **VERIFIED (live, all seven tabs):** `鎮守府海域` 12 rows (walked); `南西諸島海域` first page 09..16 with `NEXT` present; `北方海域` 8 rows 17..24 with no `NEXT` (so at most one page, unproven either way); `南西海域` exactly 1 row (`41` ブルネイ泊地沖哨戒, NEW) with a short page proving the end; `西方海域` first page 25..32 with `NEXT`; `南方海域` first page 33..40 with `NEXT`; `中部海域` completely empty. Switching tabs **resets the list scroll to the top**, verified by comparing the restored tab-0 page against the true top page key-for-key.
- **CORRECTION (my own reading):** a first pass treated "8 rows on the first screen" as the tab's whole content and reported `missing B1..B6` for 南西諸島海域. That was wrong - `NEXT` was present on that same screen, so the tab holds more than eight. First-screen coverage compares a *page* with an *area* and must not be reported as an unlock gap; only a completed walk can say what is unlocked.
- **TESTED:** 17 new offline tests: exact/drifted/foreign/ambiguous/tab-scoped resolution, position listing, position labelling and its exact-count refusal, the tab-name table, the six listed areas matching the catalogue, the seventh tab being unlisted, the game order differing from the wiki order, and four `area_of_numbers` cases. Full Python suite **836 tests, 18 skipped, OK** (819 before this stage).
- **SAFETY:** 12 tab switches and 10 scroll clicks, all reversible; the game was left on the tab-0 top page. No expedition started, no fleet touched, no game API request. The walker still refuses to guess a coordinate.
- **LIMITS / NOT VERIFIED:** only `鎮守府海域` was walked to both ends; the other tabs were photographed on their first page only, so their true totals are unknown. `北方海域` shows 8 rows with no `NEXT`, which is consistent with exactly 8 but is not proof - a full page without a measured control stays `unknown` by design. `中部海域` is empty and is not in the wiki table this project ships, so no coverage can be computed for it. Tab switching resetting the scroll is verified on tab 0 only.
- **ARTIFACTS:** `tools/page_through_expedition_list.py`, `src/kancollebot/calibration/expedition_list.py`, `src/kancollebot/calibration/expedition_catalog.py`, `src/kancollebot/calibration/__init__.py`, `tools/scan_expedition_list.py`, `tests/test_expedition_catalog.py`, `tests/test_expedition_list_scanner.py`; frames and `index.json` under `logs/diag_expedition_20261104/` (`walk4/`, `tab1_after.png`, `tab2_page.png` .. `tab6_page.png`, `tab0_before.png`, `tab0_back.png`).

## 2026-11-04 - Closed-loop stage B: single-fleet resupply (offline)

- **IMPLEMENTED (plan):** `adapters/poi/full_supply.py` gained `FleetSupplyPlan` and `build_fleet_supply_plan`, the single-fleet counterpart of the existing all-fleet preflight. The reason is structural, not cosmetic: `build_full_supply_plan` guards the UI button that supplies *every* unlocked fleet at once, so it requires all of them to be idle (`api_mission == [0,0,0,0]`). In a continuous loop another fleet is almost always under way, so that plan could never be built. The narrow plan validates only the target fleet, still demands that it be idle, still checks freshness/warehouse/roster/duplicates, and reports `needs_supply` so a no-op can be distinguished from a real deficit.
- **IMPLEMENTED (controller):** `expeditions/continuous_resupply_controller.py` mirrors the settlement controller's contract: explicit `allow_ui_actions` gate, shared `UiOperationArbiter`, a durable event sink, a `clock`, and a maximum observation age. `resupply_one_needed(fleet_id)` performs the planner preflight, refuses a fleet that is not parked in `RESUPPLY_REQUIRED`, and then either confirms without clicking (nothing to refill) or drives `resupply_fleet`, requires two-frame `HOME_PORT` evidence, re-reads the port snapshot newer than the click, and only calls `confirm_resupply(fully_supplied=True)` after `verify_fleet_supply_complete` passes. A snapshot that fails to prove the supply blocks the fleet and emits `resupply_blocked`; automatic retry is forbidden by design.
- **IMPLEMENTED (injected data source):** the loop's `ExpeditionFleetSnapshot` carries fleet mission state and ship IDs but no per-ship fuel/ammo, so a supply cost cannot be derived from it. The controller therefore takes a `fleet_data_provider` returning `FleetSupplyObservation` (port-shaped fleets/ships/master/resources plus the freshness witness) and rejects any snapshot whose source is not `poi-redux:...`. Wiring that provider to the live Poi port store is NOT done.
- **IMPLEMENTED (driver):** `ResupplyPlan` (every step must start and end at `HOME_PORT`, because supplying is an in-page action that never leaves the home port) plus `PoiCdpContinuousUiDriver.resupply_fleet`. It fails closed without a reviewed plan, for a foreign fleet, for a busy fleet, for a missing supply snapshot, and for a fleet with nothing below capacity - that last case matters because a supply click with nothing missing still spends a real action. Success is verified from the *fleet* snapshot's per-ship fuel/ammo, not from the UI gate, since the UI state does not change across a supply; the gate only proves the right screen. `_execute_settlement_chain` gained a `chain_event` parameter so the supply chain logs `resupply_chain_confirmed` instead of borrowing the settlement event name.
- **FIX (self-contradiction, caught before it shipped):** `FleetSupplyObservation.source` defaulted to `"poi-redux"` while the controller required a `"poi-redux:"` prefix, so the default instance was rejected by its own validation. The default is now `"poi-redux:port"`.
- **TESTED:** 31 new offline tests. Plan: per-fleet cost, a fleet on expedition not blocking another fleet's plan, target must be idle, already-full fleet, empty fleet, insufficient materials, unwitnessed/stale/future snapshot, ship missing from snapshot, missing fleet, invalid ID. Controller: authorization gate, non-dispatch fleet, wrong phase, nothing-to-refill confirms without a click, happy path (9 fuel / 9 ammo on the fixture), supply that did not take effect blocks the fleet, expedition on the target fails preflight, stale observation, foreign source, observation that does not cross the click boundary, ambiguous UI evidence. Driver: missing plan, foreign fleet, already full, missing snapshot, busy fleet, verified supply clicks once and returns HOME_PORT, supply that leaves a ship empty, plan whose steps leave the home port. Full Python suite **867 tests, 18 skipped, OK** (836 before this stage).
- **SAFETY:** this stage is entirely offline. No click, no supply, no dispatch, no game API request. Every supply path requires a reviewed `ResupplyPlan`, two-frame `HOME_PORT` evidence, a post-click observation newer than the action, and a fleet-level completeness check; an ambiguous outcome blocks instead of retrying.
- **LIMITS / NOT VERIFIED (live):** supply coordinates are uncalibrated - no production `ResupplyPlan` exists, so the driver currently refuses every supply. No live supply has been performed. The Poi port store is not wired to `fleet_data_provider`, so nothing in production can construct a real observation yet. `ProductionStateGate` still declares supply checkbox states out of scope; no supply-screen evidence (fingerprint or template) has been captured. The supervisor does not yet orchestrate the resupply phase - `RESUPPLY_REQUIRED` still pauses rather than being driven. The complete closed loop remains NOT_VERIFIED.
- **ARTIFACTS:** `src/kancollebot/adapters/poi/full_supply.py`, `src/kancollebot/adapters/poi/cdp_ui_driver.py`, `src/kancollebot/expeditions/continuous_resupply_controller.py`, `tests/test_fleet_supply_plan.py`, `tests/test_continuous_resupply_controller.py`, `tests/test_continuous_ui_driver.py`.

## 2026-11-04 - Tab switching by measured cell, six tabs walked, and a signature collision found live

- **IMPLEMENTED (tab switch, gated):** `tools/page_through_expedition_list.py` grew `--switch-to N` (1-based position on the strip). The click point is `detect_area_tabs(...).centers[N]` - the same measurement that already located the strip for the selected-tab read - and the *receipt* for the click is the orange label: `Walker.select_tab` accepts the step only when the label has moved to the requested cell, and returns `tab_not_selected` / `tab_unreadable` otherwise. A tab that is already selected gets **no click at all**, because pressing it again would be indistinguishable from a failed press. The tool is still the only one in the project that presses anything, still refuses to run without `--allow-scroll-clicks`, and its module docstring now names exactly the two kinds of click it will make instead of claiming it never touches a tab.
- **FIX (claim vs evidence, caught in my own output):** the coverage block printed "both ends were reached, so 'missing' means NOT UNLOCKED" whenever anything was missing - including runs whose bottom boundary was `unknown`. The note is now conditional on `BoundaryState.END_CONFIRMED`; when the bottom was not proven it says so and states that locked and unscanned are indistinguishable there. The JSON gained `bottom_proven` and `tab_label`/`tab_scores` so the claim can be audited later.
- **FIX (wrong-row click risk, found by measurement):** `ExpeditionListIndex.locate` returned the first row inside the tolerance. Live frames prove that is a coin flip: in `西方海域`, rows `29` and `30` hash to the **same** key (distance 0) and `28` sits exactly on the tolerance (distance 6) from both. `locate` now requires a *unique* match - the live row must fit no other bound number, and no second live row may fit this one - and otherwise returns a new `ambiguous` state with a null point. Nothing clicks on ambiguity.
- **VERIFIED (live, full walks, each returning to its starting page):** `鎮守府海域` 12 rows `01..08, A1..A4`, `missing A5, A6`; `南西諸島海域` 10 rows `09..16, B1, B2`, `missing B3..B6`; `北方海域` 8 rows `17..24`, nothing missing (exactly the catalogue's 8); `南西海域` 1 row `41`, `missing 42..46` with the bottom *proven* by a short page; `南方海域` 10 rows `33..40, E1, E2`, nothing missing (`E1`/`E2` both flagged NEW on screen); `中部海域` empty. Every tab reset the list to its top page on switch, reconfirming the earlier finding. The game was left on the `鎮守府海域` top page, where the operator had it.
- **VERIFIED (live, the walk is reproducible and self-checking):** re-running a tab with no labels reused the persisted index and resolved every row; re-running tab 0 exposed that its index had only been half bound (`08`, `A2..A4` were never labelled) and reported those four positions instead of pretending completeness. Labelling them once fixed it for every later run.
- **MEASURED (defect, live frames):** the 16-bin dark-pixel signature cannot identify rows on this screen. `29 潜水艦派遣演習` and `30 潜水艦派遣作戦` are pixel-identical in the signature window (identical bins *and* identical `dark_total`, `01518`); `28` sits at distance 6 from both; an edge-clipped row drifts up to 4 between scrolls. The two ranges **overlap**, so no value of `DEFAULT_SIGNATURE_TOLERANCE` is simultaneously inclusive for one row and exclusive for its neighbour. The comment that claimed "0 for the same row, >20 for distinct rows" was wrong and has been replaced with the measurements.
- **REJECTED (my own attempt, reverted):** I widened the descriptor to 32 dark bins plus a 16-bin saturated-colour histogram, on the theory that the material icons (invisible to a luminance threshold) would separate `29`/`30`. Measured on the same frames it made things **worse**: same-row drift rose from 4 to 9 while the minimum distance between distinct rows stayed at 2, i.e. the ranges overlapped more, not less. `29`/`30` still landed 1 apart. Reverted in full; nothing of it shipped.
- **BLOCKED (live):** `西方海域` could not be walked. Binding the top page hit `signature already bound to 29, refusing to rebind to 30` and the walk stopped with the tab unlabelled, so its row count and unlock gap are both still unknown. This is the collision above surfacing where it matters, and it is a real block, not a tool bug: identity for that tab has to stop coming from pixels.
- **TESTED:** 12 new offline tests. Tab switch: click lands on the requested cell and names it `expedition-tab-4`, already-selected sends nothing, a click that changes nothing blocks, an unreadable selection blocks, an out-of-range target sends nothing, the point equals the measured cell centre. Locate: exact preferred, single unambiguous near row located, two live rows inside the tolerance ambiguous, a neighbour that also fits ambiguous, nothing close enough `not_visible`, and the measured twin case ambiguous. Full Python suite **879 tests, 18 skipped, OK** (867 before this stage).
- **SAFETY:** 43 clicks over eleven runs, all of them either a measured scroll chevron or a tab cell whose receipt is the orange label; six of them were tab switches. No expedition dispatched, no fleet touched, no game API request. Every failure path (`tab_not_selected`, `tab_unreadable`, `blocked_no_measured_control`, label mismatch) stopped without retrying and left the screen readable.
- **LIMITS / NOT VERIFIED:** `西方海域` remains unwalked (see BLOCKED), so 7 of 8 areas now have a walked-or-proven state and that one does not. On any tab whose last page is exactly full, the bottom stays `unknown` by design (`full_page_without_measured_control`), so "nothing missing" there means "nothing missing on the pages reached" - for `北方海域` and `南方海域` this happens to agree with the catalogue's own count, which is corroboration, not proof. `中部海域` is still absent from the shipped catalogue, so no coverage can be computed and its emptiness cannot be distinguished from "no area data". The identity model is the real open item: until bindings key on list position (order) rather than on a pixel fingerprint, any two expeditions that render alike stay unidentifiable, and `locate` must keep refusing them.
- **ARTIFACTS:** `tools/page_through_expedition_list.py`, `src/kancollebot/calibration/expedition_list.py`, `tests/test_expedition_list_tab_switch.py` (new), `tests/test_expedition_list_scanner.py`; frames and per-tab JSON under `logs/diag_expedition_20261104/` (`walk_tab1`, `walk_tab1b`, `walk_tab2c`, `walk_tab3`, `walk_tab4`, `walk_tab5`, `walk_tab6b`, `walk_tab7`, plus the throwaway probes `drift_probe.py`, `descriptor_compare.py`, `signature_experiment.py`).

## 2026-11-04 - Row identity moved to list position; 西方海域 unblocked and all seven tabs walked

- **IMPLEMENTED (identity model, breaking):** `ExpeditionListIndex.bindings` is now keyed by `(tab, position)` instead of `(tab, signature)`. `resolve(tab, position)` is a plain dict lookup with no tolerance, `bind` refuses two numbers on one position and one number on two positions, and `locate(expedition_id, scan, *, offset)` returns `scan.rows[position - offset]` where `offset` is the row count above the captured page. The pixel signature keeps only the two jobs it can actually do: `align_pages` (many rows vote on one shift) and `same_view`. `DEFAULT_SIGNATURE_TOLERANCE`'s comment now says so instead of claiming the gate separates rows.
- **WHY (measured, not preference):** in 西方海域 the rows `29 潜水艦派遣演習` and `30 潜水艦派遣作戦` render to the same 16-bin fingerprint *and* the same dark total `01518`, while `28` sits exactly on the tolerance from both and an edge-clipped row drifts up to 4. The two ranges overlap, so no tolerance value separates the pair - the previous stage ended in `BLOCKED` for this tab for exactly that reason. Replayed the blocking capture under the new model: all eight rows `25..32` bind, and positions 4/5 (the identical pair) come out as `29`/`30`.
- **IMPLEMENTED (what the model forbids):** a position means nothing without knowing how far the list is scrolled, so `scan_expedition_list.py` refuses to bind or reuse bindings unless the first capture is provably the list top: `TOP_UNVERIFIED` when the two scroll-control boxes do not both fit the capture, `OFFSET_UNKNOWN` when a PREV chevron is still visible. Before, a signature could name rows on any page; that ability is gone on purpose, because it was the ability that lied. The index format is version 2 and a version 1 file is rejected with an explanation rather than reinterpreted.
- **VERIFIED (live, 西方海域 - the tab that was BLOCKED):** tab 5/7 walked end to end. 9 rows: `25..32, D1`; `D1 西方海域偵察作戦` read on screen for the second page's last row and carried a NEW badge. Coverage against the catalogue: `missing D2, D3`. The walk opened at the top (`PREV absent`), one DOWN click moved the list by 1 with support 7, the second DOWN found no measured control and stopped (`blocked_no_measured_control`), and the return leg restored the starting view (`restored: True`).
- **VERIFIED (live, index reuse without any labels):** re-running tab 5 with no `--label-first`/`--label-unbound` named all eight visible rows purely from position bindings, which is the case that used to need a tolerant signature match.
- **VERIFIED (live, all seven tabs under version 2):** 鎮守府 12 `01..08, A1..A4`, `missing A5, A6`; 南西諸島 10 `09..16, B1, B2`, `missing B3..B6`; 北方 8 `17..24`, nothing missing, no DOWN control on the full page; 南西 1 `41`, `missing 42..46` with the bottom `end_confirmed` (short page, so those five are provably not unlocked); 西方 9 `25..32, D1`, `missing D2, D3`; 南方 10 `33..40, E1, E2`, nothing missing; 中部 empty. Every count and gap agrees with the previous stage's independent walk, which had used the signature model - the two models now cross-confirm each other on six tabs.
- **VERIFIED (live, positions survive the scrolls):** the 鎮守府 walk spans 5 pages (4 DOWN clicks) and still names 12 positions with no duplicates and no gaps, so `align_pages` placed every page at the right offset. The 南西諸島 and 南方 walks (3 pages each) do the same.
- **TESTED:** full Python suite **881 tests, 18 skipped, OK**. The `LocateAmbiguityTests` class existed only because of the collision and is replaced by `LocateByPositionTests`: a row is located whatever it looks like, two identically-rendered rows resolve to different points, a position below the captured page is `not_visible` until the caller supplies the offset, a row above the page is `not_visible`, and a number bound on another tab is `unbound`. `ResolveTests` became position tests (no pixel involved, one number per position, negative positions refused). Tool tests now cover the v2 index layout and both refusal paths.
- **SAFETY:** 44 clicks over twelve runs: 10 tab cells (each with the orange-label receipt) and 34 measured scroll chevrons. No expedition dispatched, no fleet touched, no game API call. Every stop was a failed-to-find control, not a guessed coordinate. The game was left on the tab and page the operator had it open on (西方海域, top page).
- **LIMITS / NOT VERIFIED:** the bottom stays `unknown` on every tab whose last page is exactly full (`full_page_without_measured_control`), so "nothing missing" on 北方 and 南方 means "nothing missing on the pages reached" - it agrees with the catalogue's own counts, which is corroboration, not proof. `中部海域` is still absent from the shipped catalogue, so its emptiness cannot be distinguished from "no area data". `locate` is not yet called by any production path: the offset has to come from the walk's alignment chain, and the dispatch engine does not yet carry that chain, so the API is ready but unused. A version 1 index is now dead weight; re-labelling a tab costs one walk.
- **ARTIFACTS:** `src/kancollebot/calibration/expedition_list.py`, `tools/scan_expedition_list.py`, `tools/page_through_expedition_list.py`, `tests/test_expedition_list_scanner.py`; commit `c1485ba` for the model change. Frames and per-tab JSON under `logs/diag_expedition_20261104/` (`walk_tab1_v2`, `walk_tab1_v2b`, `walk_tab2_v2`, `walk_tab2_v2b`, `walk_tab3_v2`, `walk_tab4_v2`, `walk_tab5_v2`, `walk_tab5_v2b`, `walk_tab6_v2`, `walk_tab6_v2b`, `walk_tab7_v2`, `walk_tab5_restore`), the replay probes `replay_tab5_positions.py` and `replay_tab2_positions.py`, and the rebuilt index `logs/expedition_list_index_v2.json` (50 bindings, six tabs).

## 2026-11-04 - Dispatch row click aimed by list position; the offset chain has a production caller

- **IMPLEMENTED (the gap this closes):** the dispatch path selected mission 3 by clicking `RecordedPoint(350, 350)`. That coordinate is the third row only while the list is at its top showing tab 1, and the game reopens the list on whichever area tab was last used - after the previous stage's walk, that is whatever tab the operator left open. Nothing aimed the click; only `MISSION_SELECTED` complained afterwards. `ExpeditionListIndex.locate` existed and was verified but had **zero production callers**, because it needs an offset and nothing produced one outside a tool.
- **IMPLEMENTED (the chain):** `ExpeditionListSession` (`src/kancollebot/calibration/expedition_list_session.py`) owns the offset. `locate(expedition_id, tab=...)` selects the tab when asked, climbs until no up-chevron is measured (the only offset establishable with no prior knowledge), then scrolls down with measured chevrons, adding each `align_pages` shift, until `locate` reports the row on the current page. Every click it sends is a measured chevron or a tab cell whose receipt is the orange label; the row itself is never pressed here. It refuses when the capture is too small to prove the top (`scroll_probed` false), when a control is not measured, when the row is unbound, and when the tab changes mid-scroll.
- **IMPLEMENTED (two call sites):** `DispatchStep` now carries **either** a recorded `point` **or** a `row` (plus optional `row_tab`), enforced as an exclusive choice in `__post_init__`. `PoiCdpContinuousUiDriver` resolves a row step against the live frame immediately before the click and re-establishes the state checkpoint afterwards, because locating may have scrolled the list. `CdpContinuousExpeditionUiDriver` (the production Fleet 3 / mission 3 driver) takes an optional `locate_row` callback; without one it keeps the constant but emits `continuous_ui_row_located_unverified`, so a run that still depends on the guess is auditable instead of silent. With one, a failure to place the row raises before the row click.
- **IMPLEMENTED (index as an artifact):** the version-2 position index moved into the package as `src/kancollebot/calibration/data/expedition_positions.json` (50 bindings, six tabs), with `load_position_index` / `save_position_index` in the calibration package so the walker, the scanner and the dispatch path share one format. It carries a `label` naming the account it was walked on - documentation, not a check, because nothing in the client exposes an account id - since positions are per-account state.
- **IMPLEMENTED (read-only probe):** `tools/locate_expedition_row.py` runs the whole chain against the live client and prints the point, the offset and the proof, writing a crop of the located row for a human to confirm. It never presses the row, which is why it is the only way to see the chain work without authorising a dispatch. It requires `--allow-scroll-clicks` and refuses without it.
- **VERIFIED (fail closed, live):** with the client back on the home port, `locate_expedition_row.py 03 --tab 1 --allow-scroll-clicks` stopped with `LOCATE_BLOCKED: the selected area tab is not readable, so switching tabs cannot be verified`, exit 2, no clicks. That is the intended behaviour for "not looking at the list", and it is the only live observation this stage has.
- **TESTED:** full Python suite **909 tests, 18 skipped, OK** (881 before this stage, 28 new). The session tests drive a fake list whose visible window moves one row per scroll click, so they assert the exact offset, the exact step count, and that a row past the eighth really does cost a scroll; they also cover climbing from a partly-scrolled start, tab selection and its refusal, an unprovable top, an unbound number, a click that fails to move the list, and a tab change mid-scroll. The driver tests cover a measured point replacing the constant, the checkpoint count after a scroll (three `EXPEDITION_LIST` checks), a row step with no session, and an unplaceable row - both failing before the row click.
- **SAFETY:** every click the new code can send is a measured scroll chevron or an area-tab cell with the orange-label receipt; `locate` returns a point and does not click it. The generic driver refuses a `row` step when no session is configured rather than falling back to a coordinate. The production driver keeps its `(fleet, mission) == (3, 3)` gate and its existing checkpoint sequence; the only new event is a row-location record.
- **LIMITS / NOT VERIFIED:** **the chain has not been exercised on a live list yet.** The client returned to the home port before the probe ran, and opening the expedition list needs the operator. So the session is TESTED offline and the refusal path is VERIFIED live, but no `located` result has been produced against the real client by this entry point - the arithmetic is the same arithmetic the walker already ran 44 clicks with, which is corroboration, not the same thing. Selecting a row other than mission 3 still has no selected-state evidence: `MISSION_SELECTED` is a fingerprint of mission 3's detail panel, so a general "any expedition" dispatch still needs a generic receipt and is not attempted here. The shipped index is valid only for the account it was walked on and nothing verifies that.
- **ARTIFACTS:** `src/kancollebot/calibration/expedition_list_session.py` (new), `src/kancollebot/calibration/data/expedition_positions.json` (new), `load_position_index`/`save_position_index`/`POSITION_INDEX_VERSION` in `expedition_list.py`, `DispatchStep.row`/`row_tab` + `_resolve_step_point` in `cdp_ui_driver.py`, `locate_row` in `continuous_expedition_ui.py`, `tools/locate_expedition_row.py` (new), `tools/scan_expedition_list.py` (now uses the shared reader/writer), `tests/test_expedition_list_session.py` (new), plus additions to `tests/test_continuous_ui_driver.py` and `tests/test_continuous_expedition_ui.py`. Commit `e0f4501`.

## 2026-11-04 - Dispatch any expedition this account has, on the fleet it names (closed loop, live-verified)

- **IMPLEMENTED (identity, two prior commits):** the three pieces that make "any expedition" mean anything shipped in `311eb91` and `2848b3f` and are unchanged here. A mission id is a master-data id, not a display number (`01`/`D1` vs `3`/`131`), so a mission id the table does not know is refused instead of guessed into a row. After the row click the panel is compared against every known expedition's name band; the winner must be the intended one (the old `MISSION_SELECTED` whole-panel fingerprint accepted 04 for mission 3 at 0.9871, so it proved layout, not identity). The decision control must be on screen (it is replaced by 遠征中止/帰還命令 while running, which scored 0.79 against it). The fleet-selection screen is read through its tab strip and the requested fleet must be the one painted selected before the irreversible start.
- **IMPLEMENTED (row placed by its bound area tab):** `ExpeditionListIndex.tab_of` answers which area tab holds a number from the walked bindings; `ExpeditionListSession.locate_any_tab` reads it and delegates to `locate`. A number bound on no tab, or on two, is an error rather than a guessed click, because the game reopens the list on whichever tab was last used. The driver's `locate_row` is now the one-argument form that resolves the tab itself. Commit `1991070`.
- **IMPLEMENTED (production entry is no longer Fleet 3 / mission 3):** `tools/one_expedition_ui_loop.py` takes `--fleet-id` and `--mission-id` (defaulting to the configured production pair), refuses a fleet outside `all_fleet_ids` as deferred, and refuses a mission id absent from the master-data table. The composition gate keeps its no-default rule: the (ships, flagship-level) pair must come from a verified `KNOWN_COMPOSITION` entry or be passed as `--minimum-ships`/`--flagship-level` read off the panel. `run_continuous_expedition_monitor.build_runtime` now takes per-fleet `mission_requirements` instead of a bare fleet tuple.
- **VERIFIED (live, real dispatch, the closed loop):** on the real game, fleet 2 was sent on mission 1 (`01 練習航海`) through the genuine `CdpContinuousExpeditionUiDriver` against the real screen. Every checkpoint fired on live frames: HOME_PORT (0.9984, two consecutive) → SORTIE_MENU (1.0) → EXPEDITION_LIST (1.0) → row located by position index at tab 0 / position 0 → name-band identity expected `01` = observed `01` (score 1.0, runner-up `37` at 0.951 named) → decision control present (1.0) → fleet 2 tab already selected (1.0) → the irreversible start click → a fresh fleet-tab re-read → start. A separate CDP read of the live Redux state then confirmed fleet 2 status `active`, mission_id 1, the same four ship ids as pre-flight. This is the first dispatch of an expedition other than the legacy (3, 3), and it used fleet 2 and mission 1, proving the generalization rather than the constant.
- **TESTED:** full Python suite **952 pass, 18 skipped**; Node observers pass. New coverage: `test_expedition_detail` (name band, decision control, fleet tabs synthesise frames from the measured crops so the real decoder and descriptor maths stay in the loop), `test_expedition_list_session` `locate_any_tab` (resolves the tab, no tab bound, two tabs bound), the entry's master-data refusal and composition-gate fall-through, and the driver's one-argument locator and missing-locator refusal.
- **SAFETY (fail closed, observed live twice):** (1) the first production-supervisor attempt stopped before any click with `Poi info.ships response was truncated` - no `ui_click_sent` was emitted; (2) a scripting mistake that called `prepare_dispatch` and then `dispatch_expedition` (which re-runs the whole navigation from HOME_PORT) was caught at the HOME_PORT checkpoint because the game was still on the fleet-selection screen - again no start click. In both cases the game was left unmutated. The one real dispatch above went through because every checkpoint passed on live frames.
- **LIMITS / BLOCKED (separate, pre-existing infra):** the production supervisor path cannot run on this account because Poi's MCP `poi_get` for `info.ships` truncates the response (239 ships; default returns 111 with `truncated:true`; even `limit`/`offset` keep `truncated:true` and `total != returned`). The supervisor's `PoiExpeditionReader` requires `total == returned and not truncated`, so the ship-read can never succeed here - this blocks all supervised dispatch equally, not just this feature, and is orthogonal to "dispatch any expedition". The live proof bypassed it by reading fleet state directly over CDP (`window.getStore('info.fleets')`, which returns all records) instead of the MCP bridge, and by letting the UI driver decide the click sequence from on-screen pixels only. The working live path is therefore the standalone CDP-based driver invocation; it is a `/tmp` verification artifact, not yet promoted to a committed tool. The /tmp script is a verification artifact.
- **ARTIFACTS:** commits `311eb91`, `2848b3f`, `1991070`; `src/kancollebot/adapters/poi/continuous_expedition_ui.py`, `src/kancollebot/calibration/expedition_list.py` (`tab_of`), `src/kancollebot/calibration/expedition_list_session.py` (`locate_any_tab`), `tools/one_expedition_ui_loop.py`, `tools/run_continuous_expedition_monitor.py`; tests `test_expedition_detail.py` (new), `test_expedition_list_session.py` (`locate_any_tab`), `test_one_expedition_ui_loop.py`, `test_one_expedition_production_integration.py`, `test_continuous_expedition_ui.py`. Live dispatch confirmed by CDP; `/tmp/live_driver_dispatch.py` is the verification script.

## 2026-11-04 - 修复 MCP info.ships 截断，并把 live 验证脚本提升为正式工具

用户确认两点：(1) 把 `/tmp/live_driver_dispatch.py` 提升为正式 live 派遣工具；(2) 修复 Poi MCP 的 `info.ships` 截断，恢复监督器路径。

- **IMPLEMENTED (MCP 截断修复):** `src/kancollebot/adapters/poi/expedition_reader.py` 的 `read_fleets` 不再要求整张 `info.ships` 完整。改为：先读不截断的 `info.fleets`（≤4 条），取被引用实例 id，若整表读被截断（`truncated:true`，且 `limit`/`offset` 分页在 Poi MCP 上无效——实测每页都回前 50 条、`union` 永远停在 50），则逐条 `poi_get info.ships.<id>`（返回单对象、不截断）凑齐被引用实例。小账号（未截断）行为不变：仍整表读一次复用。一致性复读走同一路径，比较的是舰队 + 被引用船子集，而非不可达的全名册。
- **IMPLEMENTED (live 工具提升):** `/tmp/live_driver_dispatch.py` 落到 `tools/live_dispatch_expedition.py`：去掉硬编码 `/tmp`、用 `Path(__file__).resolve().parent.parent` 定位仓库、日志与扫描图改 `tempfile`。默认 prepare-only（导航 + 证明全部 checkpoint，含起始点击前的最后一步，随后归位母港）；`--commit` 才真正点 `EXPEDITION_START` 并用 CDP 二次确认。归位改用被派遣舰队自身的 `FLEET_n_SELECTED`（修掉从 fleet2 脚本拷贝时写死的 `FLEET_2_SELECTED`）；被拒时 best-effort 归位母港而非停在半途屏。新增 `tests/test_live_dispatch_expedition.py` 冒烟测试（模块可导入 + argparse 强制 `--fleet-id`/`--mission-id`/`--authorize-ui-actions`）。
- **VERIFIED (reader 修复真实桥接):** `PoiExpeditionReader().read_fleets()` 现在能读全 4 舰队（含进行中的 fleet 2 / mission 1），此前必因截断 fail-closed。监督器路径恢复。
- **VERIFIED (live 工具 prepare-only 真实游戏):** fleet 4 / mission 2 —— 名称带确认 `02`（score 1.0，runner-up `A3` 0.9343 被点名），最终态 `FLEET_4_SELECTED`（fleet 4 页签确认），`expedition_start_clicked:false`，随后归位母港。另一次 fleet 4 / mission 1 被拒：名称带确认 `01` 后，決定键仅 0.7877（低于 0.97）——因为 mission 01 正被 fleet 2 远征中、详情面板把它换成「遠征中止」，工具据此 fail-closed 拒派。两次都未发出起始点击。
- **TESTED:** 全量 Python **959 pass / 18 skip**；Node 观察者通过。`test_poi_expedition_reader` 21 个用例现在覆盖：截断时回退逐 id（且不使用整表读）、完整路径不发逐 id 调用、回退中缺失被引用实例 fail-closed、一致性守卫捕获两次读取间变化的被引用实例。`test_live_dispatch_expedition` 4 个用例。
- **SAFETY (fail closed 再次实测):** live 工具在 mission 01 已在进行中时于起始点击前停止（決定键分数不足），游戏未被改动；被拒路径 best-effort 归位母港。
- **LIMITS / 分支更正:** 本会话实际工作分支是本地 `feat/closed-loop-settlement-20261104`（无远端跟踪），不是先前记忆里的 `dev/expedition-closed-loop-mvp`；main 未被触碰，全程本地提交不推。
- **ARTIFACTS:** commits `0e6b0f8`（reader 截断修复）、`0b6020c`（live 工具提升）；`src/kancollebot/adapters/poi/expedition_reader.py`、`tools/live_dispatch_expedition.py`（新）、`tests/test_live_dispatch_expedition.py`（新）、`tests/test_poi_expedition_reader.py`。`/tmp/live_driver_dispatch.py` 已作废（由正式工具取代）。

## 2026-11-04 - 补齐监督器路径的 freshness 证据链（更正上一节结论）

上一节把 reader 层的截断修复写成了"监督器路径恢复"，这一表述不准确：端到端跑生产入口后立刻暴露下一层阻断 `Poi observation is not trusted: Source data freshness has not been verified`（零点击、fail-closed）。本节记录该阻断的两个根因、修复与 live 复验。

- **IMPLEMENTED (根因一：截断是按响应体积，不是按条数):** 实测 `poi_get(info.ships)` 返回 111/239 且 `truncated:true`；只加 `limit=300`/`1000` 仍回 111；只加 `select`（8 字段）回 200/239 仍截断；`limit=1000` + `select` 才回 **239/239、`truncated:false`**。`offset` 被忽略（上一节已记）。投影字段包含 `api_lv`，因为 `normalize_poi_fleets` 用它判断编成完整性，漏掉会把每支舰队降级为编成不完整。`read_fleets` 现在保持"小账号 plain 全量读"原行为，仅在截断时尝试投影全量读；连投影都截断才回退逐 id 子集。
- **IMPLEMENTED (根因二：逐 id 子集无法证明 freshness):** `port_snapshot_matches_current_state` 要求 `current_ships.keys() == witness_ships.keys()`，witness 带全量 239 艘，而逐 id 回退只给舰队引用的十余艘，恒成立 False —— 这是上一节修复引入的兼容性问题。live 诊断确认 witness 本身有效（`verificationStatus=CAPTURED_UNDER_STATICALLY_REVIEWED_PROBE`、HTTP 200、`w18i.kancolle-server.com`、五个 safety flag 全 false、`shipCount=239`），失败点确在船集合不等。子集回退现被明确标注并测试为"可描述舰队、绝不声称 freshness"。
- **IMPLEMENTED (生产入口启用可见 UI 刷新):** `tools/one_expedition_ui_loop.py` 此前以 `enable_ui_refresh=False` 构造 runtime，游戏静止在母港不会发出 `/kcsapi/api_port/port`，被动观察者拿不到 witness，监督器必停。现改用已受审查的三步导航 `HOME_PORT → SORTIE_MENU → EXPEDITION_LIST → HOME_PORT`（不点任务、不派遣、不创建游戏服务器请求）；刷新 checkpoint 用归档文档 2026-11-03 已 live 验证的 **6 次采样** verifier（默认 3 次实测会让第三步 HOME_PORT 两帧确认失败），派遣 checkpoint 仍保留更严的 3 次。刷新驱动与派遣控制器共享 `runtime.ui_arbiter`，避免两个独立仲裁器破坏点击互斥。新增 `--no-ui-refresh` 诊断开关，并更正过时的 "fixed Fleet 3 -> mission 3" 帮助文本。
- **VERIFIED (live, 只读探针):** 6 次采样下三步导航全部通过，后台观察者 `captured`、witness 存在，`PoiExpeditionReader.read_fleets()` 返回 `source_freshness_verified=True`（修复前恒为 False）。无派遣点击。
- **VERIFIED (live, 完整生产入口):** `one_expedition_ui_loop.py --authorize-ui-actions --fleet-id 2 --mission-id 1` 通过 HOME_PORT（两帧）、三个刷新 checkpoint 与 freshness 门禁，`production_supervisor_started` 带 `ui_refresh_enabled:true`，完成一次 `supervisor.tick()` 并以 `PRODUCTION_SUPERVISOR_PAUSED` / `fleet_2_not_ready` / `dispatch_count:0` / `game_actions_sent:0` 收尾。即监督器路径已能读、能判、能安全停下；此前两次运行都停在 truncated/freshness，未走到调度判定。
- **TESTED:** 全量 Python **962 pass / 18 skip**；Node 观察者通过。`test_poi_expedition_reader` 23 例新增：投影全量读恢复 freshness、投影保留 `api_lv`/`api_cond` 与 `limit`、逐 id 子集绝不冒称 freshness。集成测试新增：刷新接线断言、派遣保持 3 采样而刷新 verifier 用 6 采样、CLI 默认启用刷新且 `--no-ui-refresh` 可关闭（并隔离模块全局 `LOG_PATH` 污染）。
- **SAFETY:** 全程零资源消耗点击、零游戏 API 请求；两次 fail-closed 实测（freshness 未验证时停、fleet not_ready 时停）。补给 dry-run 也 fail-closed（`observer status: process_timeout`），未执行任何补给动作。
- **LIMITS / BLOCKED (游戏资源状态，非代码缺陷):** 当前 fleet 2/3/4 `fuel_ready=False`（fleet 3 另 `ammo_ready=False`），fleet 1 虽满补给但只有 1 艘船、不满足 mission 1 的 2 艘门槛，因此监督器判 `not_ready`。要让监督器真正发出派遣，需先完成一次真实补给；现有 `tools/full_supply_ui_action.py` 是 **Fleet 3 专用**（fleet 2 只读）、真实资源点击在项目历史中明确标为 NOT_VERIFIED，且 dry-run 仍需自然 port witness，故本次未擅自执行。完整派遣闭环此前已由 `tools/live_dispatch_expedition.py --commit` 在真实游戏上独立验证（fleet 2 / mission 1，CDP 确认）；**"监督器发出派遣"仍未 VERIFIED**，需用户单独授权补给后再验。
- **ARTIFACTS:** commits `f539e2f`（投影全量读）、`8544b9b`（生产入口刷新接线）；`src/kancollebot/adapters/poi/expedition_reader.py`、`tools/one_expedition_ui_loop.py`、`tests/test_poi_expedition_reader.py`、`tests/test_one_expedition_production_integration.py`；live 日志 `C:\Users\QuirrellRevess\AppData\Local\Temp\live_production_v2.jsonl`。本地分支 `feat/closed-loop-settlement-20261104`，未推、未碰 main。

## 2026-11-04 - 游戏逻辑层显式禁止 Fleet 1 派遣远征

用户指令：补充游戏逻辑，Fleet 1 不能被派遣远征。此前 `--fleet-id 1` 能穿过生产入口：入口只校验 `target_fleet_id not in all_fleet_ids`（Fleet 1 在 all 内），随后 `build_runtime(dispatch_fleet_ids=(1,))` 把 Fleet 1 当成可派遣构造调度器；唯一拦它的只是 `build_runtime` 里硬编码的 `not in (2,3)` ValueError，不是游戏逻辑。本节把"Fleet 1 不可派遣"做成配置层权威事实。

- **IMPLEMENTED (配置层权威事实):** `src/kancollebot/runtime_config.py` 新增 `non_dispatchable_fleet_ids: tuple[int, ...] = (1,)`（解锁但永不可派遣的舰队，Fleet 1 是旗舰出击舰队、按设计保留）与派生属性 `dispatchable_fleet_ids`（`all_fleet_ids` 减去 `deferred` 与 `non_dispatchable`）。启动校验新增：`non_dispatchable ⊆ all_fleet_ids`，且 `monitor_candidate ⊆ dispatchable_fleet_ids`（候选必须是可派遣子集）。
- **IMPLEMENTED (生产入口门禁上移、先于连接):** `tools/one_expedition_ui_loop.py` 新增 `_validate_dispatch_fleet_id`，明确拒绝 `target_fleet_id in non_dispatchable_fleet_ids`（报 reserved），并保留 `not in all_fleet_ids` 按 deferred 拒绝。该门禁从 `connect_game_webview` 的 `with` 块内移到外层 `try` 内、打开游戏连接之前——非法目标零连接、零点击，仍被既有 `STOPPED_FAIL_CLOSED` 分支记录。
- **IMPLEMENTED (build_runtime 资格校验泛化):** `tools/run_continuous_expedition_monitor.build_runtime` 的派遣资格校验由硬编码 `not in (2,3)` 改为对照 `RUNTIME_CONFIG.dispatchable_fleet_ids` 子集。既显式排除 Fleet 1，也顺带去掉了对"未来解锁的可派遣舰队"的误拒（服务于先前"派遣任意远征"的泛化，而非反向收紧）。
- **VERIFIED (离线，门禁行为):** `test_one_expedition_ui_loop.DispatchFleetValidationTests`：`_validate_dispatch_fleet_id(1)` 抛 "reserved"、`(5)` 抛 "outside the configured"、`(2)/(3)` 通过；`test_continuous_monitor_runtime`：`build_runtime(dispatch_fleet_ids=(1,))` 抛 "not dispatchable"；`test_runtime_config`：`non_dispatchable_fleet_ids == (1,)`、`dispatchable_fleet_ids == (2,3)`、候选 ⊆ 可派遣。
- **TESTED:** 受影响 86 个用例全绿。新增/改动 `tests/test_runtime_config.py`、`tests/test_one_expedition_ui_loop.py`、`tests/test_continuous_monitor_runtime.py`。注意：全量套件另有 13 个 `test_full_supply` 失败，来自此前未提交的 `full_supply.py`/`full_supply_ui_action.py` 编辑（`expected_fleet_ids` 不一致），与本任务无关、本任务未触碰这些文件。
- **SAFETY:** 纯配置/门禁变更，无新增游戏点击。Fleet 1 在调度器默认 `read_only_fleet_ids=(1,)` 之上新增配置层显式排除，多层防御（config 门禁 → build_runtime 子集校验 → 调度器分组）。
- **LIMITS / NOT VERIFIED:** 仅改逻辑层，未做真实游戏实证。Fleet 1 在 UI 层本就无远征页签（生产 UI 驱动 `supported_fleets=(2,3,4)` 不含 1，`visual_fingerprint`/`navigate_supply_fleet_tab` 的 `(2,3)` 页签约束一致），本次未改这些 UI 硬编码。`dispatch_recovery.py` 的 `(2,3)` 持久化校验未改（当前配置只派遣 2/3，超出需另立任务）。
- **ARTIFACTS:** `src/kancollebot/runtime_config.py`、`tools/one_expedition_ui_loop.py`、`tools/run_continuous_expedition_monitor.py`、`tests/test_runtime_config.py`、`tests/test_one_expedition_ui_loop.py`、`tests/test_continuous_monitor_runtime.py`。本地分支 `feat/closed-loop-settlement-20261104` 已于 2026-11-04 推送 `origin/feat/closed-loop-settlement-20261104`（独立分支，未碰 main）。

## 2026-11-04 - 远程保全推送 + 补给模块代码整合

- **IMPLEMENTED (远程保全):** 将本地分支 `feat/closed-loop-settlement-20261104` 推送到 `origin` 作为独立分支（`[new branch]`，已设置上游跟踪 `origin/feat/closed-loop-settlement-20261104`），main 未触碰。完成用户要求的"本地成果先推进到远程、独立分支保全"。
- **IMPLEMENTED (补给模块整合 / 配置一致性):** 修正此前半成品 `full_supply` 编辑导致的 13 个 `test_full_supply` 失败。`build_full_supply_plan` 的 `expected_fleet_ids` 默认值回退到保守的 `(1,2,3)`（供测试与未解锁第4舰队的账号使用）；生产调用点 `tools/full_supply_ui_action.py` 的 3 处 `build_full_supply_plan` 调用已显式传 `(1,2,3,4)`（真实账号 Fleet 4 已解锁），二者不再冲突。保留的合法修复：(A) `_collection` 对 `info.ships` 走 `limit=1000 + select` 投影全量读，规避 Poi MCP 按序列化体积截断（239 艘 → 239/239 不截断）；(B) 报告调用 `select_full_supply_targets(plan, supported_fleet_ids=plan.fleet_ids)`，避免"Fleet 4 缺独立校验的 UI 选择门"误报。
- **VERIFIED:** 全量套件 `Ran 968 tests ... OK (skipped=18)`，0 失败 0 错误；`test_full_supply` 26 项全绿。此前 13 个失败已消除。
- **TESTED:** 全量套件通过。
- **SAFETY:** 未改动任何游戏点击逻辑；仅回退一个默认值并保留只读/MCP 读取修复。`.workbuddy/` 本地记忆目录未进仓库。
- **LIMITS / NOT VERIFIED:** `src/kancollebot/loop/resupply.py:evaluate_resupply_readiness` 默认 `expected_fleet_ids=(1,2,3)` 与 adapter 一致、内部自洽，但闭环补给尚未接入 `ContinuousExpeditionSupervisor.tick()`（缺口 #1），且真实 4 舰队账号若走该默认会触发 preflight 拒绝；闭环补给接入时需由调用方显式传 `(1,2,3,4)`。OpenCV/NumPy 缺失仍阻塞真实补给点击（缺口 #3）。
- **ARTIFACTS:** `tools/full_supply_ui_action.py`（整合后唯一剩余改动，已提交并推送）、`docs/PROJECT_kancollebot_Progress.md`。远端分支 `origin/feat/closed-loop-settlement-20261104` 含 Fleet 1 门禁提交 `97568e3` 与本次整合提交。

## 2026-11-04 - P0 闭环：补给接入 supervisor + 舰队配置统一

- **IMPLEMENTED (P0-1 补给接入 supervisor):** `continuous_supervisor.py` 的 `tick()` 正式编排补给阶段，闭合 派遣→结算→补给→再派遣 闭环。新增 `RESUPPLY_PHASES={"RESUPPLY_REQUIRED"}` 与 `_resupply_fleet_id(pause_reason)` 解析；`_pause_reason()` 现识别 `RESUPPLY_REQUIRED`（此前完全忽略，是闭环跑一轮就卡死的根因）；`tick()` 在结算之后衔接 `resupply_controller.resupply_one_needed()`（RESUPPLY_REQUIRED→READY）再派遣。`SupervisorTickResult` 新增 `resupplies` 字段。`ContinuousExpeditionSupervisor.__init__` 新增 `resupply_controller` 参数及共享 scheduler/event_sink 校验（resupply 控制器用注入的 `fleet_data_provider` 而非共享 `state_reader`，故不校验 reader，代码注释说明）。
- **SAFETY (P0-1 失败保护):** 若一舰队需补给但无 resupply 控制器或 `allow_ui_actions=False`，supervisor 严格 fail-closed 暂停、**不静默跳过、不派遣其他舰队**（与既有 settlement 暂停语义一致）；每 tick 至多一次结算+随之一次补给，仍有待办则报告暂停由下一 tick 续做。`resupply_one_needed` 自带 `fleet_id not in dispatch_fleet_ids` 守卫（Fleet 1/4 永不被补给）。
- **IMPLEMENTED (P0-3 集成测试):** 改写/新增 `test_continuous_supervisor_settlement.py` —— 现有 `test_settlement_controller_drives_returned_fleet_in_same_tick` 升级为带 fake resupply 控制器并断言 结算→补给→同 tick 派遣 闭合；新增 `ContinuousExpeditionSupervisorResupplyTests`：补给后同 tick 派遣、无控制器时 fail-closed 暂停、控制器禁用时 fail-closed 暂停、resupply 控制器必须共享 scheduler/event_sink 否则构造报错。
- **IMPLEMENTED (P0-2 舰队配置统一):** `runtime_config.all_fleet_ids` 由 `(1,2,3)` 改为 `(1,2,3,4)`，反映真实已解锁账号；`dispatch_recovery.py:38` 持久化校验硬编码 `(2,3)` 改为 `RUNTIME_CONFIG.dispatchable_fleet_ids`（消除配置漂移）；`tools/full_supply_ui_action.py` 3 处 `expected_fleet_ids=(1,2,3,4)` 改为 `RUNTIME_CONFIG.all_fleet_ids`（单一事实来源）；`run_continuous_expedition_monitor.py` 的 `build_runtime` 推导 `read_only_fleet_ids` 时排除 `deferred_fleet_ids`（修复 Fleet 4 被同时计入 read_only 与 deferred 导致的 disjoint 校验失败）；`one_expedition_ui_loop._validate_dispatch_fleet_id` 新增对 `deferred_fleet_ids` 的显式拒绝（"deferred" 信息），与 `dispatchable_fleet_ids` 一致。
- **VERIFIED:** 全量套件 `Ran 973 tests ... OK (skipped=18)`，0 失败 0 错误；受影响的 supervisor/settlement/resupply/config/recovery 测试全绿。
- **SAFETY (P0-2):** 配置改动不改变派遣集（`dispatchable_fleet_ids` 仍为 `(2,3)`）；Fleet 4 保持 deferred（未派遣），其"策略一致性"以"明确 deferred 而非误派"实现，未擅自提升为派遣舰队（需实机验证，见 LIMITS）。
- **LIMITS / 待决策:** (1) Fleet 4 是否提升为派遣舰队（加入 `monitor_candidate`/派遣集）是需用户确认的独立决策，当前保持 deferred——`visual_fingerprint.py:282` 的 UI 页签 `(2,3)` 约束与"Fleet 4 派遣"尚未对齐，留作后续。(2) P1-1 奖励领取视觉门禁校准、P1-2 实机测试、P1-3 持续循环测试均依赖真实游戏/OpenCV，当前受 OpenCV/NumPy 缺失阻塞（缺口 #3）。(3) CI 验证 + 与 dev 整合方案未启动。
- **ARTIFACTS:** `src/kancollebot/expeditions/continuous_supervisor.py`、`src/kancollebot/expeditions/dispatch_recovery.py`、`src/kancollebot/runtime_config.py`、`tools/one_expedition_ui_loop.py`、`tools/full_supply_ui_action.py`、`tools/run_continuous_expedition_monitor.py`、`tests/test_continuous_supervisor_settlement.py`、`tests/test_runtime_config.py`、`tests/test_one_expedition_production_integration.py`、`docs/PROJECT_kancollebot_Progress.md`。

## 2026-11-04 - P1 功能依赖检查：OpenCV/NumPy 安装核实 + 启动期 fail-closed 接线

- **IMPLEMENTED (用户下一步建议 #4):** 核实当前环境 OpenCV/NumPy 均 MISSING（`python -c importlib` 确认 `import cv2`/`import numpy` 均 `ImportError`）。新增 `src/kancollebot/adapters/poi/visual_target.py:require_visual_dependencies()`：检测 cv2/numpy 缺失并抛出带安装指引的 `VisualTargetError("Visual expedition UI requires the missing dependencies ['opencv-python-headless','numpy']; install with 'pip install opencv-python-headless numpy' (headless OpenCV is sufficient)")`。
- **IMPLEMENTED (生产入口接线):** `tools/full_supply_ui_action.py:run()` 在 dry-run 返回之后、真实点击路径前调用 `require_visual_dependencies()`；`tools/one_expedition_ui_loop.py:run()` 在 `--authorize-ui-actions` 门禁之后调用同一函数，并将 `VisualTargetError` 包装为 `LoopError("Visual expedition UI unavailable: ...")`，使缺失依赖以统一 fail-closed 错误契约进入 `_main_impl`（返回退出码 2），而非泄漏裸 `RuntimeError` 堆栈。
- **IMPLEMENTED (测试):** `tests/test_visual_target.py` 新增 `RequireVisualDependenciesMissingTests`（deps 缺失时断言 fail-closed 含 `opencv-python-headless` 与 `numpy`）与 `RequireVisualDependenciesPresentTests`（deps 存在时返回 None），用 skip 守卫保证任一环境至少其一运行。受 deps 缺失影响的 4 个生产集成测试（3 个 `test_one_expedition_production_integration` + 1 个 `test_one_expedition_ui_loop`）已通过 `patch("kancollebot.adapters.poi.visual_target.require_visual_dependencies")` 隔离该独立关切（其被测行为是编排/安全门禁，不是视觉依赖本身），并新增 `ProductionStartupVisualDependencyTests:test_authorized_run_fails_closed_without_visual_dependencies` 显式验证入口 fail-closed 接线。
- **VERIFIED:** 全量套件 `Ran 976 tests ... OK (skipped=19)`，0 失败 0 错误；缺失依赖时 `require_visual_dependencies()` 实测抛 `VisualTargetError` 且 `run()` 包装为 `LoopError`；两个生产入口模块导入正常。
- **SAFETY (fail-closed 默认):** 缺依赖时**严格暂停**、给出可操作安装指引，**不静默降级**为无视觉点击；真实游戏点击（补给/结算定位小可逆控件）依赖 cv2/numpy，未安装时绝不会半途失败在模糊的定位步骤上。
- **LIMITS / 待办:** (1) 实际安装未做（受网络代理限制）；需在能联网环境 `pip install opencv-python-headless numpy` 后再做 P1-2/P1-3 实机与持续循环测试。(2) CI 验证 + 与 dev 整合/PR 方案仍未启动（缺口 #5）。(3) Fleet 4 是否提升为派遣舰队仍是待用户决策项（保持 deferred）。
- **ARTIFACTS:** `src/kancollebot/adapters/poi/visual_target.py`、`tools/full_supply_ui_action.py`、`tools/one_expedition_ui_loop.py`、`tests/test_visual_target.py`、`tests/test_one_expedition_ui_loop.py`、`tests/test_one_expedition_production_integration.py`、`docs/PROJECT_kancollebot_Progress.md`。

## 2026-11-04 - P1 视觉依赖落地：OpenCV/NumPy 安装 + 17 项测试解阻塞

- **IMPLEMENTED / VERIFIED (安装已落地):** 此前"安装受网络代理限制未做"的阻塞已解除。实测本地代理端口：`9996`/`4160`/`6647` 均不可用，`7890` 在监听且 `curl -x ... https://github.com` 返回 200；`git push` 经 `7890` 成功。通过 `python -m pip install --proxy http://127.0.0.1:7890 "opencv-python-headless>=4.10" "numpy>=2.0"` 成功安装 `numpy 2.5.4` + `opencv-python-headless 5.0.0.93`（Python 3.13.14）。实测 `require_visual_dependencies()` 现返回 `None`，两个生产入口的依赖门禁放行。
- **VERIFIED (测试解阻塞):** 全量套件从 `976 tests OK (skipped=19)` 变为 **`976 tests ... OK (skipped=2)`** —— 17 项此前因缺 cv2/numpy 被跳过的视觉测试（含 `test_visual_target.py` 全部 OpenCV 定位/歧义/双帧像素计数测试）现真实执行并全部通过，验证了 OpenCV 5.x 兼容。仅剩 2 项跳过：`RequireVisualDependenciesMissingTests` 与 `ProductionStartupVisualDependencyTests`（在依赖已安装环境下按设计跳过，其互补的"依赖存在"分支测试正常运行）。Node 侧 `static_decoder_extract.test.cjs`、`poi_port_response_observer.test.cjs` 及 `node --test` 三文件合并（19 项）均 0 失败。
- **SAFETY:** 依赖安装只影响本机 Python 环境，未改动任何点击逻辑或运行时默认行为；缺依赖时的 fail-closed 门禁仍保留并由"无依赖"分支测试覆盖。
- **IMPACT:** P1-1（奖励领取视觉门禁校准）、P1-2（实机测试）、P1-3（持续循环测试）的**依赖阻塞已解除**；剩余阻塞是需要真实 Poi/游戏会话与用户的一次性授权，不是代码或依赖问题。
- **LIMITS:** OpenCV 5.0.0 通过测试，但 `pyproject.toml` 仅约束 `>=4.10` 无上限；CI 将以 3.12/3.13 × 有/无视觉依赖四组验证兼容性。
- **ARTIFACTS:** 本机 Python 环境（`numpy`、`opencv-python-headless`）、`pyproject.toml`（`[project.optional-dependencies] vision` 已存在，本次未改）、`docs/PROJECT_kancollebot_Progress.md`。

## 2026-11-04 - P1 功能分支 CI 验证 + 与 dev 整合方案（方案待用户决策）

- **IMPLEMENTED (功能分支 CI):** 新增 `.github/workflows/feature-closed-loop-ci.yml`。既有 `ci.yml` 仅对 `main`/`dev/**` 的 push 与 PR→main 触发，且 `pip install -e .` 不带 `vision` extra —— 导致 (a) 本保全分支完全没有 CI，(b) 即使触发也有 17 项视觉测试被静默跳过。新工作流在 `push` 到 `feat/closed-loop-settlement-20261104`、`pull_request`→`main`/`dev/**`、`workflow_dispatch` 时触发；`python-version ∈ {3.12, 3.13}` × `vision ∈ {with, without}` 四组矩阵：`with` 组 `pip install -e ".[vision]"` 跑完整视觉套件，`without` 组 `pip install -e .` 并显式卸载 cv2/numpy 以强制走 fail-closed 路径；每组均断言依赖可用性与矩阵一致、执行 `require_visual_dependencies()` 双向校验、`compileall`、全量 `unittest discover -s tests`、以及 3 个 Node 测试文件的 `node --test`。
- **VERIFIED (本地验证):** YAML 经 PyYAML 解析通过（4 组矩阵、3 类触发器、8 个步骤）；CI 内嵌 heredoc 脚本两向实测：`VISION=with` 输出 "dependency gate passed"，`VISION=without`（用 meta_path blocker 模拟缺依赖）输出 "dependency gate failed closed as designed" 并含完整安装指引；`node --test` 三文件 19 项 0 失败；`without` 场景的真实全量套件结果见本会话早前实测（976 OK / 19 skipped，缺依赖时 `require_visual_dependencies` 抛 `VisualTargetError`）。
- **VERIFIED (远端 CI，已核验):** 推送 `6d56f0f` 后远端 GitHub Actions 自动触发 run `41808783497`（`https://github.com/wosafety0/wosafety0clamp/actions/runs/41808783497`）。经 API 查询逐步确认：4 个矩阵 job 全部 `completed / success`、无失败步骤，整体 `status=completed, conclusion=success`。即 Python 3.12/3.13 × 有/无视觉依赖四种组合的全量 unittest + Node 测试 + 双向依赖门禁，均已在远端 CI 真实跑通。
- **IMPLEMENTED (整合方案，未执行):** 远端现状：`origin` 共 75 个分支；`main` = `a4f3777`（按项目约定默认分支主要承载 `.github`）；本保全分支 = `c5f30fb`；当天存在多个活跃 `dev/expedition-*-20261104` 分支（return-grace-guard / scan-session / scroll-boundary / unknown-tab-locate-guard）与 `dev/dispatch-observer-safety-current-mvp-20261103` 等，整合目标不唯一。三个候选：(A) 仅保留独立保全分支、暂不合并（当前状态，最保守）；(B) 合并/PR 到某个 `dev/expedition-*` 分支；(C) PR 到 `main`（不推荐，且默认分支只有 `.github`，需先确认项目约定）。已知技术限制：GitHub 连接器建 PR 返回 403（缺 `pull_request` 权限），建 PR 需用本机 `git credential fill` 凭据 + urllib 调 API。
- **SAFETY / 授权边界:** 依全局规则 `AGENTS.md` v2.1.0-rc1 §10.2，推送/建 PR/合并/修改远端数据须有适用授权。本次仅向用户已明确要求的"独立保全分支"推送；**未创建 PR、未合并、未触碰 `main`**。远端 ref 已用 `git ls-remote` 核验与本地 HEAD 一致。
- **需用户决策（未执行，不臆断）:** (1) 整合目标分支选 A/B/C 中哪一个、是否授权创建 PR/合并；(2) Fleet 4 是否从 deferred 提升为可派遣舰队；(3) P1-1/P1-2/P1-3 何时提供真实 Poi/游戏会话与一次性派遣授权。
- **LIMITS:** P1-1 视觉门禁校准、P1-2 实机证据（返回/结算/领取/补给四项独立证据）、P1-3 无人干预完整循环均未完成；这三项的剩余前置是真实 Poi/游戏会话与一次性授权，不再是依赖或 CI 问题。
- **ARTIFACTS:** `.github/workflows/feature-closed-loop-ci.yml`（新增）、`docs/PROJECT_kancollebot_Progress.md`。

## 2026-11-04 - 整合可逆性评估（只读分析；未执行任何整合）

- **用户问题：** "整合是否可逆"。以下为 `git` 只读核验结果（fetch 远端 ref、`merge-base`、`rev-list`、`diff --shortstat`、`merge-tree --write-tree` 冲突预演），**未创建 PR、未合并、未改写任何远端 ref、未碰 main**。
- **IMPLEMENTED / VERIFIED (仓库拓扑事实):** `origin/main` = `a4f3777`，其顶层**只有 `.github/`**（整个 `src/`、`tools/`、`tests/`、`docs/` 从来不在 main 上）。保全分支 `a5187bf` 相对 main：merge-base `39f26db`、**ahead 745 / behind 4**、差异 `204 files / +61396 / -17`。因此"PR 到 main"会把六万余行源码灌进一个只放 CI/规则文件的分支，明显不是本项目的整合路径。
- **VERIFIED (候选目标实测):**
  - `dev/dispatch-observer-safety-current-mvp-20261103` = `86f294c`：`merge-base --is-ancestor` 为真，即它是 HEAD 的**直系祖先**（behind **0**、ahead 74）。向它整合 = **纯 fast-forward，零冲突、不产生 merge commit**。
  - `dev/expedition-return-grace-guard-20261104`：behind 2（`d2ebc85` 延后归还确认、`59f7b1f` 归档）、ahead 50；`merge-tree --write-tree` 预演出现 **2 处内容冲突**：`docs/PROJECT_kancollebot_Progress.md`、`tools/run_continuous_expedition_monitor.py`。
  - `dev/expedition-closed-loop-mvp`：behind 141 / ahead 50，分叉很大。
  - `main`：`merge-tree` 预演出现 `.github/workflows/verify-global-rules.yml` add/add 与 `README.md` modify/delete 冲突。
- **VERIFIED (可逆性分层结论):**
  - **当前状态（仅推送独立保全分支）＝完全可逆、零风险**：远端只是一个额外分支 ref，不影响 main 或任何 dev 分支；删除该 ref 即撤销（本地 ref 与全部提交仍在）。
  - **开 PR 但不合并＝可逆**：关闭 PR 即撤销，代码状态零变化；残留的只有 GitHub 上的 PR/通知记录。
  - **fast-forward 到直系祖先 `86f294c`＝可逆性最高**：不产生 merge commit、不改写历史，只需把目标分支 ref 精确移回 `86f294c`（`push --force-with-lease` 指向旧 SHA）即可完全还原；源保全分支原封不动保留，可随时重新 ff。
  - **产生 merge commit 的整合＝Git 层仍可逆**：用 `git revert -m 1 <merge-sha>` 生成反向提交即可回退，不改写历史；代价是之后若想再合并同一分支，需先 revert 掉那个 revert。
  - **不可逆 / 有残留**：`reset --hard` + force push 到共享分支会改写已发布历史，破坏他人克隆/PR（本仓库同时有 75 个分支、多个当天活跃 dev 分支，风险尤其高）；CI run/Actions 日志、PR/Review 记录、通知、他人已 fetch 的引用，以及任何真实游戏点击，都无法从 Git 回退。
- **IMPLEMENTED (可逆整合方案，作为方案保存、未执行):** 阶段 1（现状，零风险）保留独立保全分支不动；阶段 2（需授权）先做只读差异核对与冲突预演（本节点已完成主要部分），再开 PR——PR 可随时关闭；阶段 3（需授权）优先选 `dev/dispatch-observer-safety-current-mvp-20261103` 做 fast-forward，若必须走 merge 则用 `--no-ff` 保留单个 merge commit 并**保留源分支不删**；阶段 4 回退一律用 `git revert -m 1`，禁止 `reset --hard`/`--force` 到共享分支。若需要整合 `dev/expedition-return-grace-guard-20261104` 的 2 个独有提交，须先人工解决上述 2 处冲突（归档文档与 `run_continuous_expedition_monitor.py`），可逆性降为"merge + revert"一级。
- **SAFETY:** 依 `AGENTS.md` v2.1.0-rc1 §10.2，建 PR/合并属需单独授权的远端写操作；本节点只做只读分析与方案归档。`git fetch` 仅更新本地 `refs/remotes/origin/*`，未改动任何远端分支。
- **需用户决策:** 选定整合目标（推荐 `dev/dispatch-observer-safety-current-mvp-20261103` 的 fast-forward）、以及是否授权 PR/合并。
- **LIMITS:** 未实际执行整合，故"回退操作"本身未验证；只验证了拓扑与冲突预演。
- **ARTIFACTS:** `docs/PROJECT_kancollebot_Progress.md`。

## 2026-11-04 - Fleet 4 提升为可派遣 + 兼容未解锁 3/4 的账号

- **用户指令:** "Fleet 4 提升为可派遣舰队，但是有的账号 Fleet 3 或 Fleet 4 并没有解锁，要考虑到"。核心是把**政策上限**与**账号事实**分离，不能因为 Fleet 4 被提升就假设每个账号都有 3/4 舰队。
- **IMPLEMENTED (概念分离):** 新增 `src/kancollebot/expeditions/fleet_availability.py` —— `normalize_unlocked_fleet_ids()` 严格校验观测到的舰队 id（非空/正整数/唯一/从 1 连续/在政策上限内），`resolve_fleet_availability()` 求「请求集 ∩ 已解锁集」，支持 `strict=True`（显式 `--fleet-id` 未解锁时**硬失败**，绝不改派别的舰队）与 `strict=False`（候选集自动裁剪未解锁舰队）。`RuntimeConfig.all_fleet_ids` / `dispatchable_fleet_ids` 明确重新定义为**政策上限**，docstring 写明"不代表某账号实际解锁"。
- **IMPLEMENTED (Fleet 4 提升):** `deferred_fleet_ids` 由 `(4,)` 改为 `()`；`monitor_candidate_fleet_ids` 由 `(2,3)` 改为 `(2,3,4)`；`dispatchable_fleet_ids` 现为 `(2,3,4)`。`ContinuousExpeditionScheduler` 的第二套硬编码 `deferred_fleet_ids=(4,)` 改为从 `RUNTIME_CONFIG` 派生，消除"两套真相"。`build_runtime` 的 `all_requirements` 补上 Fleet 4（保守复用已 live 验证的 mission 1 最低编成）。启动期校验新增 deferred ⊆ all、三组角色互斥、resupply ⊆ dispatchable。
- **IMPLEMENTED (账号兼容 / 零点击探测):** `one_expedition_ui_loop._probe_unlocked_fleet_ids()` 通过 `connect_poi_session` + `read_poi_fleet_states` 做**只读** CDP 探测（无点击、无导航），在打开游戏 webview 之前用 `strict=True` 校验 `--fleet-id`；账号未解锁该舰队 → `LoopError("... not unlocked on this account")`，零连接零点击。`build_runtime` 新增 `unlocked_fleet_ids` / `fleet_availability_strict` 参数，按账号事实裁剪 dispatch 集、把未解锁舰队放入**本次运行**的 deferred 组、`read_only` 只列真正存在且非派遣的舰队，保证 scheduler 三组角色 disjoint，并同步裁剪 `mission_requirements`（否则 `ContinuousExpeditionMonitor` 的"完全相等"校验会先崩）。启动事件新增 `unlocked_fleet_ids` / `fleet_availability_verified` / `dispatch_candidate_ceiling`；未提供观测集时**明确标记 `verified=False`**，且把未请求的舰队标为"未确认"而非 read-only，不冒称账号事实。
- **IMPLEMENTED (补给侧兼容):** `full_supply_ui_action.py` 3 处 `expected_fleet_ids=RUNTIME_CONFIG.all_fleet_ids` 改为 `_observed_fleet_ids(fleets)`（从观测到的 `api_id` 推导并校验），解决"只有 1/2/3 舰队的账号被四舰队 preflight 拒绝"和文档早前记录的默认 `(1,2,3)` 与生产 `(1,2,3,4)` 不一致。`live_dispatch_expedition.py` 的 preflight 原本就用 `read_poi_fleet_states` 检查目标存在，天然兼容未解锁账号，仅更新过时 help 文本。
- **SAFETY (补给视觉门，不臆造证据):** 新增 `resupply_verified_fleet_ids=(2,3)`。`ContinuousResupplyController.resupply_one_needed` 增加守卫：Fleet 4 **可以派遣**，但没有已校准的补给页签区域、复选框指纹与 `SUPPLY_FLEET_4_SELECTED` 状态，因此遇到 Fleet 4 的 `RESUPPLY_REQUIRED` 时**抛错暂停（fail-closed）**，既不静默跳过也不点击无法定位的控件。supervisor 已有的暂停语义会接管，不会派遣其他舰队。
- **VERIFIED:** 全量 Python 套件 **`Ran 998 tests ... OK (skipped=2)`**（0 失败 0 错误；从 976 增至 998，新增 22 项）；Node `node --test` 三文件 19/19；`compileall -q src tools tests` 通过。新增/更新的边界用例包括：2/3/4 舰队账号、`--fleet-id 4` 在 3 舰队账号被零点击拒绝、4 舰队账号显式目标 4 通过、候选集 (2,3,4) 在 2 舰队账号裁剪为 (2)、Fleet 1/5 被政策拒绝、非法/非连续/超上限解锁集被拒绝、Fleet 4 补给 fail-closed、观测驱动的 `expected_fleet_ids` 同时接受 2/3/4 舰队账号。
- **SAFETY (总体):** 未解锁 → fail-closed；无观测集 → 标记未验证而不是假设；显式目标绝不静默替换；补给无视觉证据 → 暂停。未执行任何真实游戏点击、未建 PR、未合并、未碰 `main`。
- **LIMITS / 尚未验证（不臆断）:**
  1. Fleet 4 的**编成门槛**仍 fail-closed：`KNOWN_COMPOSITION` 无 `(4, mission)` 的 live 验证对，`one_expedition_ui_loop` 派 Fleet 4 必须显式传 `--minimum-ships/--flagship-level`，否则以 "no verified composition requirement" 拒绝。测试已断言这一行为。
  2. Fleet 4 的**闭环补给**仍被上述视觉门暂停；补齐需要真实客户端上的页签区域/复选框指纹/UI state 校准，本次按用户要求暂缓实机。
  3. `_probe_unlocked_fleet_ids()` 的只读 CDP 探测未在真实 Poi 会话上验证（离线测试用 patch 的 fake 快照）；实机阶段需先确认它与 `source_freshness_verified` 链的配合。
  4. monitor 给 Fleet 4 的默认 mission 1 编成是**策略默认值**，复用已验证的任务属性，不是"Fleet 4 实机派遣成功"的证据。
  5. 整合（PR/合并）仍未执行，见上一节可逆性方案。
- **VERIFIED (远端 CI，已核验):** 推送 `46ef4f4` 后 GitHub Actions run `41812135256` 自动触发，4 个矩阵 job（py3.12/3.13 × vision with/without）全部 `completed / success`、无失败步骤，整体 `conclusion=success`。即本次 Fleet 4 / 账号可用性改动在 Python 3.12 与 3.13、有/无视觉依赖四种组合下均通过远端全量套件。
- **ARTIFACTS:** `src/kancollebot/expeditions/fleet_availability.py`（新增）、`src/kancollebot/runtime_config.py`、`src/kancollebot/expeditions/continuous_scheduler.py`、`continuous_monitor.py`、`continuous_resupply_controller.py`、`tools/run_continuous_expedition_monitor.py`、`tools/one_expedition_ui_loop.py`、`tools/full_supply_ui_action.py`、`tools/live_dispatch_expedition.py`、`tests/test_fleet_availability.py`（新增）、`tests/test_continuous_monitor_runtime.py`、`tests/test_continuous_resupply_controller.py`、`tests/test_continuous_scheduler.py`、`tests/test_full_supply.py`、`tests/test_one_expedition_production_integration.py`、`tests/test_one_expedition_ui_loop.py`、`tests/test_runtime_config.py`、`README.md`、`docs/PROJECT_kancollebot_Progress.md`。

## 2026-11-04 - 多任务架构限界：项目不止自动化远征

- **用户指令:** "这个项目后续还会加入自动化战斗、自动化刷闪、自动化编成、自动化做任务等等，并不是只有自动化远征，要考虑到"。本轮只做**架构限界与扩展点**，不实现上述任何新任务的逻辑；实机测试仍按用户上一轮要求暂缓。
- **IMPLEMENTED (只读审视结论，均为已证事实):**
  - 仓库内**不存在**任何 `task_kind` / capability / registry / 抽象 Task 概念；`cli.py` 只有 2 个硬编码命令，`build_runtime` 写死「舰队 → mission id」映射，唯一编排器是 `ContinuousExpeditionSupervisor`。扩展模式原本是「复制一整套入口脚本」。
  - **最严重的耦合**：`RuntimeConfig.non_dispatchable_fleet_ids=(1,)` 的注释自称 Fleet 1 是 "flagship sortie fleet"（出击/战斗舰队），却以**全局舰队权限**生效，派生 `dispatchable_fleet_ids` 后被 5 层消费（生产入口门禁、`build_runtime` 子集校验、`resolve_fleet_availability`、`DispatchIntent.__post_init__`、scheduler `read_only_fleet_ids`）。未来战斗自动化正是要用 Fleet 1，会在**零点击之前**被全部拒绝。这是上一轮「Fleet 1 禁止派遣远征」固化方式留下的隐患。
  - 舰队状态全局唯一：`fleet_runtime.fleet_id`、`dispatch_intents.fleet_id` 均为单主键，`_fleets` 单字典，`FleetPhase` 9 个状态全是远征语义，`MissionTuple` 单 mission 字段。多任务并发会互相覆盖并摧毁崩溃恢复。
  - UI 仲裁只有一把全局内核互斥（`KancolleBot.PoiUiOperation.v1`），无任务维度/优先级/排队，label 不落盘；`UiState`、指纹 JSON、`SUPPLY_FLEET_TAB_REGIONS`、导航链、`(2,3)` 页签白名单全为远征专属。
  - 补给本应跨任务共用（出击前也要补给），却被 `resupply_verified_fleet_ids ⊆ dispatchable_fleet_ids`、`resupply_one_needed` 的 `dispatch_fleet_ids` 守卫、独立工具写死的 `TARGET_FLEET_IDS=(3,)` 绑在远征闭环上；固定 CDP 只读表达式只给 Fleet 2/3 生成 supply 字段且无 `api_cond`（刷闪所需的士气）。
  - 事件链无任务维度：`dispatch_events` 表名是远征语义却承载 resupply/settlement/refresh 事件；`_print_event` 与 `log()` 各写各的 jsonl，字段名不一致。
  - 文档把范围写成「只有远征」：`MVP_EXPEDITION_SINGLE_LOOP.md` 的 "Explicitly out of scope" 含 "battle automation, generic frameworks"，README/HANDOFF 亦然 —— 未来每加一个任务都会先撞上这条范围约束。
- **IMPLEMENTED (落地，最小非破坏性):** 新增 `src/kancollebot/task_scope.py` 作为任务种类的单一事实来源：`TaskKind`（expedition / combat / sparkle / composition / quests）、`IMPLEMENTED_TASK_KINDS={expedition}`、`FleetPolicy`（按任务的 dispatchable/reserved/resupply-verified 集合）、`fleet_policy_for()`（远征策略**从 RuntimeConfig 派生而非复制**；未实现任务返回**空的未校准策略**，绝不借用远征策略）、`require_task_enabled()`（未实现任务在任何 UI 动作前 fail-closed）、`task_event_fields()`、`SHARED_CAPABILITIES` 清单（6 项必须保持任务无关的共享基础设施）。`RuntimeConfig` 新增 `dispatch_policy_task_kind="expedition"`，模块 docstring 与全部舰队权限字段注释明确标注「EXPEDITION-only，其他任务不得复用」；`non_dispatchable` 注释改正为「Fleet 1 是 sortie/combat 舰队，此保留是**按任务策略**而非全局事实」。
- **IMPLEMENTED (贯通生产路径):** `resolve_fleet_availability()` 新增 `task_kind` 参数（默认远征，向后兼容），策略改由 `fleet_policy_for()` 提供，未实现任务抛 `FleetAvailabilityError`，返回值带 `task_kind`；`build_runtime()` 作为 composition root 调用 `require_task_enabled()`、按策略取 dispatchable 上限、把 `task_kind` 传给可用性解析、事件 sink 与 `MonitorRuntime`；`SqliteDispatchEventLog` 新增**构造期 opt-in** 的 `task_kind` 归因（不传则 payload 逐字节不变，保证既有测试与磁盘库零破坏），生产路径显式传入；`_print_event` 的 jsonl 加结构化 `task_kind`；`cli.py` 新增只读 `kancollebot tasks` 命令，列出 5 类任务的实现状态与共享基础设施清单。
- **IMPLEMENTED (文档范围纠正):** `README.md` 新增 "Automation tasks and scope" 小节并给第 2 条优先级加上 "for expeditions" 限定；`MVP_EXPEDITION_SINGLE_LOOP.md` 的 out-of-scope 标题改为 "(historical MVP scope)" 并追加澄清，保留原文不改、明确该边界是当时 MVP 的而非项目长期边界。
- **SAFETY (fail-closed，不臆造证据):** 声明一个任务 ≠ 实现它。4 个路线图任务的舰队策略刻意留空并标为未校准，`require_task_enabled()` / `resolve_fleet_availability()` 双重拒绝，错误信息含 "nothing was clicked" 与 "expedition policy must not be reused"；`fleet_policy_for()` 校验 `dispatch_policy_task_kind`，配置被误改作用域时拒绝把远征策略套用上去。新增回归测试专门断言「Fleet 1 在远征策略里被保留，但 COMBAT 策略**没有继承**这个禁派」。未执行任何真实游戏点击、未建 PR、未合并、未碰 `main`。
- **TESTED:** 全量 Python 套件 **`Ran 1014 tests ... OK (skipped=2)`**（从 998 增至 1014，新增 16 项，0 失败 0 错误）；Node `node --test` 三文件 19/19；`compileall -q src tools tests` 通过；`kancollebot tasks` 实跑输出 5 类任务（仅 expedition 为 IMPLEMENTED）。新增 `tests/test_task_scope.py` 覆盖：5 类任务声明与唯一实现项、未知 task_kind 拒绝、远征策略派生一致性、**Fleet 1 非全局禁派**、未实现任务无校准策略、配置错配拒绝、路线图任务零点击 fail-closed、默认可用性解析仍为远征、事件结构化归因、SQLite 归因 opt-in 与向后兼容、CLI `tasks` 不组装任何 runtime、共享基础设施清单。
- **VERIFIED (远端 CI，已核验):** 推送 `0aa9d8e` 后 GitHub Actions run `41814871704` 自动触发，4 个矩阵 job（py3.12/3.13 × vision with/without）全部 `completed / success`、无失败步骤，整体 `conclusion=success`。本次多任务限界改动在两个 Python 版本、有/无视觉依赖四种组合下均通过远端全量套件。（首次推送曾遇 GitHub 侧 `Internal Server Error`，重试后成功；本地提交未丢失。）
- **LIMITS / 尚未解决的架构债（明确记录，未实现、未验证）:** 本轮**没有实现**自动化战斗/刷闪/编成/任务，只是把作用域钉死并留出扩展点。真正让第二个任务与远征并行之前仍需逐项处理：
  1. `fleet_runtime.fleet_id` / `dispatch_intents.fleet_id` 单主键 → 需 `(task_kind, fleet_id)` 复合键或跨任务 fleet-lease 占用表，否则并发会覆盖状态、破坏崩溃恢复。
  2. `FleetPhase` 9 个远征态 + 单 `MissionTuple` → 需拆成「通用舰队占用态（IDLE/BUSY/BLOCKED）」+ 任务专属子状态；`DispatchIntent` / `DurableDispatchCoordinator` 硬编码 `mission_id`、`baseline_mission=(0,0,0,0)`、`mission_status 1/2`、`poi-redux:` 证据 → 需抽出任务无关的 Intent + 可注入的证据匹配策略。
  3. `UiOperationArbiter` 只有互斥、无任务归属/优先级/排队，label 不落盘、默认 120s 超时 → 多任务共抢同一 UI 时需结构化 label 与超时归因。
  4. `UiState` / 指纹 JSON / 坐标常量 / `DispatchPlan`·`SettlementPlan`·`ResupplyPlan` / `visible_ui_refresh` 序列全远征专属 → 需按任务注册的 ScreenRegistry，且 `visual_target.py` 的 `(1,2,3)`、`visual_fingerprint.py` 的 `(2,3)` 白名单需扩展。
  5. 补给能力仍绑远征：`resupply_verified_fleet_ids ⊆ dispatchable_fleet_ids`、闭环控制器只接受 `scheduler.dispatch_fleet_ids`、`full_supply_ui_action.py` 仍 `from one_expedition_ui_loop import ...`；固定 CDP 表达式只覆盖 Fleet 2/3 且无 `api_cond/api_nowhp/api_maxhp`（刷闪/战斗需要士气与 HP），扩展表达式必须走现有 allowlist 审查。
  6. `build_runtime` 仍是远征专用 composition root（本次明确拒绝非远征 task_kind）；`cli.py` 仍是硬编码命令表（本次只加只读 `tasks`，未改注册表机制）；`ContinuousExpeditionSupervisor` 仍是唯一编排器。
  7. 事件归因只覆盖 SQLite sink（opt-in）与 `_print_event`；`full_supply_ui_action.log()` 未打 `task_kind`（补给是跨任务能力，硬贴 "expedition" 反而失真，应由调用方传入），`dispatch_events` 表名仍是远征语义。
  8. 整合目标（推荐 `dev/dispatch-observer-safety-current-mvp-20261103` 的 fast-forward）仍待用户决策与授权；P1-1/P1-2/P1-3 实机项继续暂缓。
- **ARTIFACTS:** `src/kancollebot/task_scope.py`（新增）、`src/kancollebot/runtime_config.py`、`src/kancollebot/expeditions/fleet_availability.py`、`src/kancollebot/expeditions/continuous_dispatch_controller.py`、`src/kancollebot/cli.py`、`tools/run_continuous_expedition_monitor.py`、`tools/one_expedition_ui_loop.py`、`tests/test_task_scope.py`（新增）、`README.md`、`docs/MVP_EXPEDITION_SINGLE_LOOP.md`、`docs/PROJECT_kancollebot_Progress.md`。

## 2026-11-04 - 共享能力边界修正 + 通用任务注册/舰队租约/UI 仲裁设计（设计 + 离线验证，未接线）

- **用户指令:** "修正共享能力清单的边界，补充策略隔离与默认兼容性测试 / 设计通用任务注册接口、舰队租约和 UI 仲裁接口，先做设计与离线测试 / 多任务架构限界已实现、测试通过、远端归档通过；通用任务框架尚未实现。暂不 PR、不合并"。
- **IMPLEMENTED (边界修正，更正上一节的一处表述):** 上一节写的「`SHARED_CAPABILITIES` 清单（6 项必须保持任务无关的共享基础设施）」经只读核对是**过粗且部分失实**的：清单按**模块**分类，但同一模块内可以同时存在共享原语与远征事务。实测证据：`cdp_fleet_state.py:80` 的 `read_poi_fleet_states` docstring 为 "Fleet 2/3 supply"、`PoiFleetMissionState.mission` 是 `api_mission` 四元组且无士气/HP；`full_supply.py:298` 把 `api_mission != [0,0,0,0]` 硬编码成「舰队在远征中」；`mission_eligibility.py:1` 自称 "Pure, offline expedition composition checks" 且 `MissionCompositionRequirement` 必填 `mission_id`；`visual_target.py:52` 的 `SUPPLY_FLEET_TAB_REGIONS` 只有键 1/2/3；`continuous_dispatch_controller.py` 中的 `SqliteDispatchEventLog` 是任务无关的 append-only sink，而 `ContinuousDispatchController` 明确 import 远征 scheduler/planner。据此把清单改为**符号级** `SharedCapability`（name/module/symbols/boundary/rationale/decoupling_note）共 8 项，三态分类：`task_neutral` 3 项（UI 互斥锁、视觉依赖门禁、事件 sink）、`needs_decoupling` 3 项（舰队快照、补给 preflight、补给页签坐标）、`task_specific` 2 项（远征编成校验、远征派遣事务，明确**不属于**共享能力）。`kancollebot tasks` 改为按三态分节输出。
- **IMPLEMENTED (边界从文档变成可执行门禁):** 新增 `require_task_neutral_capability(name)` —— 第二个任务引用 `needs_decoupling` / `task_specific` 符号时 fail-closed（含具体解耦指引与 "nothing was clicked"），未知能力名/未知 boundary 一律拒绝；新增 `shared_capabilities(boundary=...)` 查询；`describe_shared_capabilities()` 保留 `module: ...` 字符串形状并附加 `[boundary]` 标签，保证既有读者与 CLI 兼容。顺手修正 `fleet_availability.py` 模块 docstring 的断行瑕疵。
- **IMPLEMENTED (三套接口，DESIGN ONLY，纯内存参考实现):** 新增 `src/kancollebot/task_runtime/` 包与设计文档 `docs/DESIGN_multi_task_runtime.md`。
  - `TaskRegistry` / `TaskRegistration`：`DECLARED → CALIBRATED → ENABLED` 状态；`ENABLED` 必须同时满足已实现、可点击、视觉门已验证、有校准画面、授权不可关闭、依赖能力全为 `task_neutral`；注册与 `require_enabled()` 都委托 `task_scope.require_task_enabled()`，不复制策略规则。**离线测试复现并修掉了一个真实设计缺陷**：只校验 `policy.task_kind` 标签挡不住 `dataclasses.replace` 伪造「远征策略内容 + combat 标签」（会走私 Fleet 1 禁派与 `(2,3,4)` 上限），故收紧为「策略必须逐字段等于 `fleet_policy_for(task_kind)`」。
  - `FleetLease` / `FleetLeaseTable`：身份含 `(task_kind, fleet_id, owner_run_id, lease_id)`，但**互斥键是 `fleet_id`**（游戏里只有一个第 2 舰队）；过期租约仍阻塞、须显式 `reclaim_expired()`，不自动抢占；`renew`/`release` 校验 owner；时钟、解锁集、注册表均可注入，全程内存。
  - `UiArbitration`：`UiArbiter` Protocol + `InMemoryUiArbiter`，优先级 `SAFETY < OPERATOR < AUTOMATION`，决策含 `GRANTED` / 三种 `REJECTED_*` / `PREEMPT_REQUESTED`；`may_interrupt_in_flight_click` **恒 False**（只允许请求让出，绝不中断半个点击）；`ui_operation_label()` 生成 `"<task_kind>:<operation>:<run_id>"` 供现有跨进程互斥锁使用。仲裁是**准入层**，不替换 `poi_ui_operation`。
- **SAFETY (fail-closed，保持默认兼容):** 三套接口**未接入任何生产入口**，不改 `cli.py` 的 `monitor`/`dispatch-once` 行为、不改任何默认值；`unittest` 隔离断言用 `patch("sqlite3.connect")` 与 `patch("...ui_operation_lock.poi_ui_operation")` 证明注册→租约→仲裁全程零 SQLite、零 UI 锁调用。策略隔离测试确认：修改 `RuntimeConfig` 只影响远征策略、4 个路线图任务的空策略不变；租约层不继承远征的 Fleet 1 禁派（战斗可租 Fleet 1，策略层才决定能否点击）。默认兼容性测试确认：不传 `task_kind` 的可用性解析仍等价于远征、`SqliteDispatchEventLog` 不传归因时 payload 逐字节不变、`build_runtime`/`MonitorRuntime` 默认仍为远征、`describe_shared_capabilities()` 与 `kancollebot tasks` 旧关键字仍在。**未建 PR、未合并、未碰 `main`、未执行任何真实点击。**
- **TESTED:** 全量 Python 套件 **`Ran 1055 tests ... OK (skipped=2)`**（从 1014 增至 1055，新增 41 项，0 失败 0 错误）；Node `node --test` 三文件 19/19；`compileall -q src tools tests` 通过。新增 `tests/test_multi_task_runtime.py` 27 项（注册伪造/错配策略拒绝、未实现任务不可 ENABLED、能力门禁、跨任务租约冲突、不同舰队共存、TTL/过期/owner、未解锁舰队拒绝、优先级与不中断点击、全程离线隔离）；`tests/test_task_scope.py` 由 16 扩至 30 项（边界三态分类、同模块两符号边界不同、模块路径可解析、门禁 fail-closed、策略隔离、默认兼容）。`pyproject.toml` 用 `setuptools.packages.find where=src`，新子包自动纳入打包。
- **VERIFIED:** 本地全量验证如上；`kancollebot tasks` 实跑输出三态各 3/3/2 项。推送 `021543f` 后 GitHub Actions run `41822332848` 自动触发并已核验完成：4 个矩阵 job（py3.12/3.13 × vision with/without）全部 `completed / success`、无失败步骤，整体 `conclusion=success`。即本次边界修正与三套离线接口在两个 Python 版本、有/无视觉依赖四种组合下均通过远端全量套件。
- **LIMITS (如实标注「通用任务框架尚未实现」):** 本轮交付的是**接口设计 + 可执行离线规范**，不是运行时框架。① 三套接口无生产接线，`cli.py` 仍是硬编码命令表，`build_runtime` 仍远征专用；② 未做 `fleet_runtime`/`dispatch_intents` 复合主键迁移、无持久化 lease 表；③ 3 项 `needs_decoupling` 能力未解耦（`api_mission` 拆分、补给「忙」谓词泛化、坐标 ScreenRegistry），需实机校准证据才能降级为 `task_neutral`；④ `FleetPhase`/`DispatchIntent` 仍是远征语义，无任务无关 Intent；⑤ 无 ScreenRegistry、无 `api_cond`/HP 读取、无任务队列、无抢占式交接实现；⑥ 优先级策略与交接时机只能在真实 Poi 会话上复核，离线测试只证明决策逻辑、不证明 UI 时序安全；⑦ 整合目标与实机窗口（P1-1/2/3）继续按用户要求暂缓。迁移路径 A→E 见设计文档 §5，仅阶段 A（本轮）已完成。
- **ARTIFACTS:** `docs/DESIGN_multi_task_runtime.md`（新增）、`src/kancollebot/task_runtime/{__init__,registry,fleet_lease,ui_arbitration}.py`（新增）、`tests/test_multi_task_runtime.py`（新增）、`src/kancollebot/task_scope.py`、`src/kancollebot/cli.py`、`src/kancollebot/expeditions/fleet_availability.py`、`tests/test_task_scope.py`、`docs/PROJECT_kancollebot_Progress.md`。

## 2026-11-04 - 临时集成分支 Fast-forward + 全量测试 + 目标分支本地整合

- **用户指令:** "在临时集成分支执行 Fast-forward 和全量测试，确认通过后再合并目标分支"。测试通过后由用户选项确认两项决策：整合目标 = `dev/dispatch-observer-safety-current-mvp-20261103`（推荐项）；**仅本地合并、不推送远端**（推荐项）。
- **IMPLEMENTED / VERIFIED (阶段 1 · 临时集成分支 ff):** 本地新建 `temp/integration-ff-20261104`，起点 `dev/dispatch-observer-safety-current-mvp-20261103` = `86f294ce39595d7144a8ab9714dee10cf01e023d`；`git merge --ff-only feat/closed-loop-settlement-20261104` 输出 `Updating 86f294c..caa3e5a / Fast-forward`，exit 0，累计 126 files / +25586 / -3489。核验：临时分支 HEAD 与保全分支 HEAD 同为 `caa3e5afc0c36198cfb5fbac931b4bf18570106e`；`git diff HEAD feat/closed-loop-settlement-20261104` 为空；HEAD 单父 `021543f`，**本次 ff 未生成任何新提交或 merge commit**（`git log --merges 86f294c..HEAD` 中的 `0c55fc4` 是这 80 个提交里既有的合并提交）。拓扑前置：`merge-base --is-ancestor dev/dispatch-observer-safety-current-mvp-20261103 feat/closed-loop-settlement-20261104` 为真、behind 0 / ahead 80；对照 `origin/dev/expedition-closed-loop-mvp`（`cc54581`）非祖先（56/141 分叉），`--ff-only` 必然失败；`main` 顶层仅 `.github` 且冲突预演有 add/add。
- **TESTED (阶段 2 · 在 ff 后的集成树上跑 CI 等价全量测试；Python 3.13.14 / Node v22.22.2 / cv2 5.0.0 / numpy 2.5.4):** `python -m compileall -q src tests tools` exit 0；`require_visual_dependencies()` 在 vision=with 下通过；`PYTHONPATH=src python -m unittest discover -s tests` → **`Ran 1055 tests in 58.646s / OK (skipped=2)`**，exit 0，0 失败 0 错误；`node --test tests/static_decoder_extract.test.cjs tests/verify_cdp_snapshot.test.cjs tests/poi_port_response_observer.test.cjs` → **19 pass / 0 fail**；`git diff --check` exit 0。套件输出中的 `--timeout-seconds must be finite and positive` / `--max-age-seconds must be finite and non-negative` 是既有负例测试打印的预期 usage 文本，不是测试失败。
- **IMPLEMENTED / VERIFIED (阶段 3 · 目标分支本地合并):** `git switch dev/dispatch-observer-safety-current-mvp-20261103`（合并前 HEAD `86f294c`）→ `git merge --ff-only feat/closed-loop-settlement-20261104`，exit 0，`Updating 86f294c..caa3e5a / Fast-forward` → HEAD `caa3e5a`，与保全分支 SHA 相同、HEAD 仍为单父线性提交、`git diff --check` exit 0；本地状态 `[ahead 80]`。源保全分支 `feat/closed-loop-settlement-20261104` 与临时集成分支均保留、未删除。
- **SAFETY / 授权边界:** 依 `AGENTS.md` v2.1.0-rc1 §10.2 与用户本次选项，**未执行任何 `git push`、未建/合并 PR、未碰 `main`、未用 `--force` / `reset --hard`**；仅本地 ref 前进，回退只需把目标分支本地 ref 精确移回 `86f294c`（保全分支可随时重新 ff）。未执行任何真实游戏点击、派遣或实机操作。本轮测试为离线/静态证据层级（本机 vision=with 组合）；`vision=without` 组合本轮未重跑，其依据是已核验的远端 CI run `41822332848`（py3.12/3.13 × with/without 四矩阵全部 success）。
- **LIMITS:** ① 本次是**本地合并**，远端 `origin/dev/dispatch-observer-safety-current-mvp-20261103` 仍为 `86f294c`，协作可见的整合尚未生效，需用户另行授权推送；② `README.md` 与 `docs/HANDOFF.md` 仍把 `dev/expedition-closed-loop-mvp` 标为活跃开发基线，与本次实际整合目标不一致，本轮未改这两份文档（避免范围扩张），建议后续单独核对更新；③ P1-1 奖励领取视觉校准、P1-2 实机证据、P1-3 无人干预完整循环仍未完成；④ 通用任务框架三套接口仍为 DESIGN ONLY、未接生产；⑤ 本节点归档提交只改 `docs/PROJECT_kancollebot_Progress.md`，不含源码/测试/工具改动，阶段 2 全量测试所验证的精确提交是 `caa3e5a`。
- **ARTIFACTS:** `docs/PROJECT_kancollebot_Progress.md`（本节点）。

## 2026-11-04 - P0 只读核查 + P1 开发基线说明修正（本地，未推送）

- **用户指令:** "P0：只读核查本地整合后的代码、CI 和测试证据。P1：单独修正 README / HANDOFF 的开发基线说明。… 当前不应把 3bd949b 描述成远端已整合版本，也不应在没有授权时推送。" 分工：用户负责轻量只读审查与架构检查，WorkBuddy 负责代码集成、测试与 Git 同步。
- **VERIFIED (P0 · 代码与拓扑，只读):** `3bd949b` = `caa3e5a` + 10 行归档（`git diff --stat temp/integration-ff-20261104 HEAD` 只有进度文档）；`86f294c..3bd949b` 共 **81 个线性提交 / 126 files / +25596 / -3489**（一条核查命令曾误用 reflog `@{1}` 取错基线，已用 `git diff --shortstat 86f294c HEAD` 更正）；`git rev-list --left-right --count origin/dev/dispatch-observer-safety-current-mvp-20261103...HEAD` = `0 81`；`compileall -q src tests tools` exit 0；`kancollebot tasks` 只读 CLI 正常输出 5 个任务与符号级 8 项三态；工作区仅未跟踪 `.workbuddy/`。
- **VERIFIED (P0 · CI 覆盖缺口):** `ci.yml` 只在 `push`→`main`/`dev/**`、PR→main 触发，py3.12 单版本、`pip install -e .`（**不带 vision extra**）、2 个 Node 测试文件；`feature-closed-loop-ci.yml` 的四矩阵（py3.12/3.13 × vision with/without、3 个 Node 文件、双向依赖门禁）**push 分支只列 `feat/closed-loop-settlement-20261104`**，只有 PR→`dev/**` 才覆盖 dev base。结论：将来推送到目标 dev 分支时**只有弱化版 `ci.yml` 会触发，四矩阵不会自动跑**，无 vision extra 时 17 项视觉测试会被跳过。
- **VERIFIED (P0 · 测试证据层级):** `3bd949b` 只有本地全量证据（1055 OK / 2 skip、Node 19/19、compileall、diff-check、vision=with）；远端四矩阵证据只覆盖到已推送的 `021543f`（run `41822332848`）；**`3bd949b` 从未在远端跑过 CI**。`vision=without` 本轮未重跑，仅历史 CI 证据。远端三分支经 `ls-remote` 仍为 `86f294c` / `caa3e5a` / `a4f3777`。按用户要求，`3bd949b` 只描述为**本地 HEAD**，不是远端已整合版本。
- **IMPLEMENTED (P1 · 单独修正，纯文档):** `README.md` 把「Active development branch」段改为 `Development branches and integration state`，用分层表格列出整合目标 / 保全分支 / 临时快照的本地与远端 HEAD，写明 local only / nothing pushed / `3bd949b` 不得描述为远端已整合版本、新克隆只能拿到 `caa3e5a`、以及 `dev/expedition-closed-loop-mvp` 已是历史分支且不可 ff。`docs/HANDOFF.md` 顶部新增 `Current integration state (2026-11-04) — read this first` 块并保留 2026-11-03 历史正文，同时把 Working branch / Start here 第 2 步 / §2 Active code branch / §10 PR inventory 标注为历史并指向当前状态。**顺带纠错**：README 原第 36 行按「模块级 6 项全任务共享」描述，与 `task_scope.SHARED_CAPABILITIES` 的符号级 8 项三态（3/3/2）冲突，已改为以 `SHARED_CAPABILITIES` 与 `require_task_neutral_capability()` 为准。
- **TESTED (P1):** 改动仅 `README.md`、`docs/HANDOFF.md`；`git diff --check` 首次抓到 HANDOFF 两行尾随空格，改为无尾随空格的列表后通过；`compileall` exit 0、`kancollebot tasks` 只读 exit 0；`git diff --name-only` 确认未触碰任何源码/测试/工具文件。本节点验证范围即纯文档改动所需的静态/只读检查，全量 Python/Node 套件不在本节点重复运行。
- **SAFETY:** 本节点全部为本地只读核查与本地文档编辑，**未 push、未建/合并 PR、未碰 `main`、未用 force/reset、未执行任何真实游戏点击**；远端状态保持原样。
- **LIMITS / 待决策:** ① 四矩阵 CI 不覆盖目标 dev 分支的 push，是否扩展 `feature-closed-loop-ci.yml` 的 push 分支列表或升级 `ci.yml` 需用户决定，本轮未擅自改 CI；② README/HANDOFF 里的 SHA 是 2026-11-04 整合快照，后续本地提交会推进本地 HEAD，文档已注明以 `git rev-parse` / `git ls-remote` 为准；③ 推送仍待用户单独明确授权；④ 实机验收（P1-1/2/3）与通用任务框架接线属后续节点。
- **ARTIFACTS:** `README.md`、`docs/HANDOFF.md`、`docs/PROJECT_kancollebot_Progress.md`（本节点）。

## 2026-11-04 - P2 通用任务框架组装层接线（A+ / D-0）+ 实机验收前置清单

- **用户指令:** "P2：推进通用任务框架接线与实机验收。… 后续我负责轻量只读审查和架构检查，WorkBuddy 继续负责代码集成、测试与 Git 同步。" 同时明确「不应把 3bd949b 描述成远端已整合版本、无授权不推送」。
- **IMPLEMENTED (组装层接线，A+ / D-0):** 新增 `src/kancollebot/task_runtime/context.py`：`TaskRuntimeContext` 把 `TaskRegistry` / `FleetLeaseTable` / `InMemoryUiArbiter` 组装到**同一个注册表**（`__post_init__` 校验三者共享，防止重新出现三套真值）；`build_task_runtime_context()` 为 `declared_task_kinds()` 的 5 个任务各注册 `DECLARED`，策略一律由 `fleet_policy_for(kind, config=...)` 派生；提供 `require_task_policy()`（仍委托 `task_scope.require_task_enabled`，生产门禁真值不变）、`arbitrate()`、`acquire_lease()`、`describe()` 只读摘要。`__init__.py` 导出新符号并更新状态说明。`cli.py` 的只读 `tasks` 命令新增「Task runtime registration」段落，直接读取注册表状态（此前只展示静态能力边界）。
- **SAFETY / 设计判断（为何不标 ENABLED、不接点击链）:** `TaskRegistration.ENABLED` 需要 `visual_gates_verified=True` 与已校准 `screen_ids`，而 P1-1 奖励领取视觉校准、Fleet 4 补给指纹、完整循环实机证据均未完成；此时把任何任务标为 ENABLED 等于**冒称验证状态**。因此工厂**没有**开启 ENABLED 的开关，全部任务默认 `DECLARED`，`registry.require_enabled()` 对所有任务 fail-closed；`describe()` 明确输出 `click_path_wired=False`。生产 `monitor` / `dispatch-once` / `build_runtime` **零改动**，远征仍走原有 `task_scope` 门禁与视觉门；本次接线只到组装层与只读 CLI，不授权任何点击。
- **IMPLEMENTED (实机验收前置，未执行):** `docs/DESIGN_multi_task_runtime.md` 更新标题 / 状态块 / 迁移路径（A、A+ 已执行；B–E 未执行，D 剩余列出具体接入点 `build_runtime` 门禁、`UiOperationArbiter.operation`、`ContinuousExpeditionSupervisor.tick()`）、§6 测试覆盖、§7 LIMITS，并新增 §8「实机验收前置清单」：5 项必需前置（真实 Poi 会话与一次性授权、P1-1 视觉校准、Fleet 4 补给指纹、完整循环实机证据、B 阶段 3 项解耦证据）、验收项与 fail-closed 停止条件、可复现离线基线命令。
- **TESTED:** 新增 `tests/test_task_runtime_context.py` 10 项（派生策略注册、单一注册表共享与错配拒绝、无证据一律拒绝 ENABLED、生产门禁不变、`DECLARED` 下租约 / UI 准入 fail-closed、只读摘要标注未接点击、离线 ENABLED 远征贯通准入链且互斥键为 `fleet_id`、CLI 只读暴露、`patch("sqlite3.connect")` 与 `patch("...ui_operation_lock.poi_ui_operation")` 断言全程零 SQLite / 零 UI 锁）。全量 **1065 tests OK / 2 skip（58.504s，较 1055 +10）**、Node 19/19、`compileall -q src tests tools` 0、`git diff --check` 0。
- **VERIFIED (默认兼容):** 既有 `tests/test_kancollebot_cli.py` 的 monitor / dispatch-once 转发断言、`tests/test_task_scope.py` 的 `cli.main(["tasks"])` 与旧关键字断言全部仍通过；`importer.assert_not_called()` 证明 `tasks` 只读命令不组装生产运行时。
- **LIMITS:** ① 阶段 B / C / D 剩余 / E **未执行**：无 ScreenRegistry、无 `api_cond`/HP、无复合主键迁移、无持久化 lease、无生产点击链接入；② 实机验收**本轮未执行任何真实点击或派遣**，需用户提供真实 Poi / 游戏会话与一次性授权；③ 通用任务框架仍是「组装层 + 只读可见」，不是运行时框架；④ 推送仍待用户明确授权。
- **ARTIFACTS:** `src/kancollebot/task_runtime/context.py`（新增）、`tests/test_task_runtime_context.py`（新增）、`src/kancollebot/task_runtime/__init__.py`、`src/kancollebot/cli.py`、`docs/DESIGN_multi_task_runtime.md`、`docs/PROJECT_kancollebot_Progress.md`。

## 2026-11-04 - 双分支授权推送到 20f78ec + 远端 ref / Actions 回读核验

- **用户指令:** 明确授权同时推送 `dev/dispatch-observer-safety-current-mvp-20261103`（推进到 `20f78ec`，让协作者获得整合成果）与 `feat/closed-loop-settlement-20261104`（同步到 `20f78ec`，保留一致的远端恢复点）；利用 feat 的 push 触发 Feature CI 四矩阵、dev 按既有规则触发弱化版 CI；推送后 `git ls-remote` 回读三个 ref 并核对 GitHub Actions 实际结果。约束：**不改 CI 配置、不碰 `main`、不使用强制推送**；实机验收暂缓、README/HANDOFF 保留整合事件快照、通用任务框架后续独立推进。
- **IMPLEMENTED / VERIFIED (推送前只读预检):** 本地两分支同在 `20f78ec1bc649c393e61f2149f2e1c82ae52be9c`；远端缓存与实时 `ls-remote` 一致（dev `86f294c`、feat `caa3e5a`、main `a4f3777`）；`git merge-base --is-ancestor` 对两条远端 ref 均返回 0（纯 ff 可行）；dev 领先 83、feat 领先 3；工作区仅未跟踪 `.workbuddy/`。两次推送均使用**显式 refspec + 默认非强制** `git push origin <local>:refs/heads/<remote>`，未经 `--force` / `--force-with-lease`。
- **IMPLEMENTED (两次推送):** ① `86f294c..20f78ec dev/dispatch-observer-safety-current-mvp-20261103 -> dev/dispatch-observer-safety-current-mvp-20261103`（exit 0）；② `caa3e5a..20f78ec feat/closed-loop-settlement-20261104 -> feat/closed-loop-settlement-20261104`（exit 0）。本地 `origin/*` 跟踪 ref 随之更新到 `20f78ec`。
- **VERIFIED (`git ls-remote` 回读三个 ref):** `refs/heads/dev/dispatch-observer-safety-current-mvp-20261103` = `20f78ec1bc649c393e61f2149f2e1c82ae52be9c`；`refs/heads/feat/closed-loop-settlement-20261104` = `20f78ec1bc649c393e61f2149f2e1c82ae52be9c`（两分支远端完全一致）；`refs/heads/main` = `a4f377764d94c8f0960c931bf94c27bb7e9b5a8e`（**未改动**）。
- **TESTED (GitHub Actions 实际结果, head_sha 均为 `20f78ec`, event=push):** 本次两个 push 共触发且仅触发 2 个 run（`gh api .../actions/runs?head_sha=...` 返回 `total_count=2`）：
  - `CI` run **41830869896**（dev 分支，弱化版 `ci.yml`，push 规则 `main` + `dev/**`）：`status=completed, conclusion=success`，1 个 job `test` success；日志 `Ran 1065 tests in 78.946s`、Node `# pass 17 / # fail 0`（该工作流只跑 2 个 Node 测试文件、py3.12 单版本、无 vision extra）。
  - `Feature CI - closed-loop settlement` run **41830907874**（feat 分支，`feature-closed-loop-ci.yml` 四矩阵）：`status=completed, conclusion=success`，4 个 job 全 success — `py3.12 / vision=with`、`py3.12 / vision=without`、`py3.13 / vision=with`、`py3.13 / vision=without`；每个 job 日志均为 `Ran 1065 tests` + Node `# tests 19 / # pass 19 / # fail 0`。
  - 两个 run 创建于 2026-11-04T12:37Z、约 12:39Z 完成；`gh run watch --exit-status` 对两个 run 均返回 0。这补齐了此前 P0 核查指出的证据缺口：**`20f78ec` 现在既有本地全量证据，也有远端四矩阵证据**（此前远端四矩阵仅覆盖到 `caa3e5a` 的 run 41822634745）。
- **SAFETY:** 远端写入严格限定在用户授权的两条分支；未修改任何 `.github/workflows/*` 配置；`main` ref 未变；未创建/合并 PR；未使用 force push / reset / 破坏性操作；未执行任何真实 Poi / 游戏点击或派遣。回退仍是单行 ref 操作（远端 dev 可被 ff 回 `86f294c` 需 force，改用 revert 更安全）。
- **LIMITS:** ① dev 分支的 `ci.yml` 仍是弱化版（py3.12、无 vision extra、2 个 Node 文件），四矩阵完整证据来自 feat 分支的 run；两类 CI 的 push 触发范围按用户要求**保持原样**；② 本归档节点是**本地提交、不推送**，因此本地 HEAD 会领先用户指定的远端 `20f78ec` 1 个纯文档提交，远端两个分支仍精确停在 `20f78ec`；后续如需本地/远端完全一致需再获一次授权；③ 实机验收（P1-1/2/3）暂缓，等待真实会话与一次性授权；④ 通用任务框架（阶段 B/C/D 剩余/E）后续独立推进；⑤ README / HANDOFF 保留整合事件快照，不为其制造额外提交，当前 SHA 以 `git rev-parse` / `git ls-remote` 为准。
- **ARTIFACTS:** `docs/PROJECT_kancollebot_Progress.md`（本节点，唯一改动文件）。

## 2026-11-04 - 下一阶段 P0 整改（R1/R2/R3/R4 + 文档状态核对）落地并双分支推送

- **用户指令:** "KancolleBot 下一阶段 P0 整改任务"，基线 `3e2e2ba`、目标分支 `dev/dispatch-observer-safety-current-mvp-20261103`，**离线整改、禁止真实游戏操作**。要求 R3 生产控制器接线核查、R4 统一入口门禁、R1 Poi 适配器解耦、R2 事件归属加固、P0 文档状态核对；验收要求新增测试覆盖**实际生产入口**、全量 Python/Node/compileall/`diff --check` 通过、CI 成功、明确列出未解决风险；不启用 combat/sparkle/composition/quests；不做 schema 迁移与生产点击链修改（除非先提交独立设计与风险说明）。追加决策：**P0 收尾单独落地，决策层契约文档另开分支，不混入本次提交**。
- **IMPLEMENTED (R1 · 适配器解耦，净搬迁 + 原名 re-export):** 新增三个任务中立 core 模块 —— `core/observation_contracts.py`（`UiStateEvidence` / `ObservedFleetSnapshot` / `FleetCompositionSnapshot` / `DockShip` / `DockInventoryError` / `dock_ships_from_poi_payloads`）、`core/fleet_availability.py`（`normalize_unlocked_fleet_ids` / `parse_unlocked_fleet_ids`）、`core/task_attribution.py`（可信归因契约）。`core` 不 import `adapters` / `expeditions` / `task_runtime`。远征侧原位置保留同名别名（对象身份不变，`isinstance` 与既有 import 全部继续成立），因此**没有扩大 `SHARED_CAPABILITIES`**（仍是符号级 8 项三态 3/3/2）。`UiOperationArbiter` 从 `expeditions/continuous_scheduler.py` 移到 `adapters/poi/ui_operation_lock.py`（它的真实归属），scheduler 保留 re-export。反向依赖清理：`adapters/poi/*` 原有 9 处、`task_runtime/fleet_lease.py` 1 处 import `kancollebot.expeditions.*`，**现为 0**。
- **IMPLEMENTED (R2 · 事件归属加固，payload 层，不改 schema):** `SqliteDispatchEventLog.__call__()` 原用 `setdefault("task_kind", ...)`，调用方传的 `fields` 可覆盖构造期可信归因；改为 `apply_trusted_task_attribution()` 先剔除保留键再写入可信值。`tools/one_expedition_ui_loop.py:_print_event()` 原把 `**extra` 放在字面量最后，可覆盖 `event` / `run_id` / `timestamp_utc` / `task_kind`；改为 `merge_trusted_event_fields()` 先剔除保留键、再按历史键序写回，**jsonl 字节兼容既有读者**。表结构保持 4 列，不做 `ALTER TABLE` / 无迁移代码；理由与风险评估见 `docs/DESIGN_task_event_attribution.md`。
- **IMPLEMENTED (R4 · 统一入口门禁):** monitor CLI 新增 `--unlocked-fleet-ids` / `--fleet-availability-strict` / `--require-verified-fleet-availability`，在**打开任何会话之前**用与 dispatch-once 同一套 `core.fleet_availability.parse_unlocked_fleet_ids()` 归一化一次；空值、非整数、重复、非连续、超政策上限一律 `parser.error` fail-closed；`--fleet-availability-strict` 或 `--require-verified-fleet-availability` 缺 `--unlocked-fleet-ids` 时直接拒绝启动。两个生产入口都显式传 `task_kind=TaskKind.EXPEDITION`，不再依赖默认值。
- **IMPLEMENTED (R3 · 只核查 + 锁定现状，不改点击链):** 生产 `one_expedition_ui_loop.py` 仍只构造 `ContinuousExpeditionSupervisor(monitor=..., dispatch_controller=...)`，`settlement_controller` / `resupply_controller` 为 `None`，故生产 `tick()` 的结算/补给分支**不可达**。本轮**未修改任何生产点击路径**；改为新增装配断言测试锁死该事实（防止无声回归），并在 `docs/DESIGN_production_controller_wiring.md` 记录调用链证据、缺失环节（两个专用 UI driver、`fleet_data_provider`、显式 `allow_ui_actions`）、状态机与 fail-closed 前置条件。
- **IMPLEMENTED (P0 文档状态):** 新增 `docs/PROPOSAL_doc_status_minimal_revision.md`，逐条列出 README/HANDOFF 中"写作时为真、现在与远端冲突"的 local-only 表述与最小修订方案；**按用户要求未直接改基线文档**，整合事件快照保留为历史记录。
- **TESTED (本地):** 全量 `PYTHONPATH=src python -m unittest discover -s tests` → **`Ran 1095 tests / OK (skipped=2)`**（较 P2 的 1065 增加 30 项）；Node 19/19；`python -m compileall -q src tools tests` exit 0；`git diff --check` 与 `git diff --cached --check` 均 exit 0。新增测试文件：`tests/test_import_direction_guard.py`（AST import 方向守卫，含自测防止空转，读取用 `utf-8-sig` 兼容既有一处 BOM）、`tests/test_task_event_attribution.py`（旧 4 列 schema 可开可读可写、双任务共表可分离、`fields`/`extra` 均不可覆盖可信归因）、`tests/test_monitor_entry_gates.py`（三个新开关的透传与 fail-closed）、`tests/test_one_expedition_production_integration.py` 新增生产装配断言。
- **VERIFIED (远端 CI，head_sha 均 `8ce181a`、event=push):** dev `CI` run **41847823207** `conclusion=success`（`Ran 1095 tests in 97.735s`，Node `# pass 17 / # fail 0`；该工作流按既有规则只跑 py3.12、无 vision extra、2 个 Node 文件）；feat `Feature CI - closed-loop settlement` run **41848243578** `conclusion=success`，4 个矩阵 job 全 success（`py3.12/3.13 × vision=with/without`，每 job `Ran 1095 tests`、Node 19/19）。推送顺序按用户要求**先 dev、绿后再 feat**，未并行推。
- **SAFETY / 授权边界:** 全程离线，**未执行任何 Poi / 游戏点击、派遣或实机操作**；未改动 `.github/workflows/*`；未碰 `main`（仍 `a4f3777`）；未建/合并 PR；未使用 `--force` / `reset --hard`；`combat` / `sparkle` / `composition` / `quests` 仍为未实现且 fail-closed。提交拆分为 `9bd7074`（代码 + 测试，24 files，+1373/-280）与 `8ce181a`（设计/提案文档，3 files，+337），保证安全整改可单独审阅与回滚。
- **LIMITS (未解决风险，如实标注):** ① **R3 缺口仍在**——生产 supervisor 的结算/补给控制器依然不可达，只做了证据固化与设计，接线需另行授权；② 事件表仍 4 列，任务维度归因只存在于 payload，跨任务的 SQL 级过滤需在读取侧解析 JSON；③ `SHARED_CAPABILITIES` 中仍有 3 项 `needs_decoupling`（`fleet_snapshot`、`resupply_preflight`、`supply_tab_coordinates`），本轮未解耦；④ `ObservedFleetSnapshot` 仍是 legacy `api_mission` 形状，**不代表第二任务可复用**；⑤ monitor 的解锁集仍依赖操作员显式提供（它无法零点击探测 CDP），与 dispatch-once 的探测在**证据强度**上仍有差异；⑥ 实机验收（P1-1/2/3）继续暂缓；⑦ 本归档提交为纯文档提交，其自身的推送与 CI 在下一节点核验，**不得描述为已核验**；⑧ `.workbuddy/` 仍未加入 `.gitignore`（本轮按最小改动原则未动）。
- **ARTIFACTS:** 新增 `src/kancollebot/core/{observation_contracts,fleet_availability,task_attribution}.py`、`tests/{test_import_direction_guard,test_monitor_entry_gates,test_task_event_attribution}.py`、`docs/{DESIGN_production_controller_wiring,DESIGN_task_event_attribution,PROPOSAL_doc_status_minimal_revision}.md`；修改 `src/kancollebot/adapters/poi/*`（8）、`src/kancollebot/expeditions/*`（6）、`src/kancollebot/task_runtime/fleet_lease.py`、`tools/one_expedition_ui_loop.py`、`tools/run_continuous_expedition_monitor.py`、`tests/test_one_expedition_production_integration.py`、`docs/PROJECT_kancollebot_Progress.md`（本节点）。

## 2026-11-04 - 档 1 决策契约远程归档（`docs/decision-contract-v1`，纯文档 + 1 个测试文件）

- **用户指令:** "先推送 docs/decision-contract-v1，跑 CI 并完成 GitHub 回读；暂不启动 M1。" 理由：档 1 已实现且本地测试通过，但未过远程归档门禁；此时启动 M1 会让两个未归档工作交错。约束：确认分支基于 `0b18900`、核对提交与完整 diff、推送后跑对应 CI、从 GitHub 回读分支/提交/三个文件、**不合并 dev、feat 或 main**。
- **IMPLEMENTED (分支与提交):** 分支 `docs/decision-contract-v1` 起点 `0b18900`（`94ee0f9^` 实测等于 `0b18900`，非口述）；`94ee0f9` = 3 个新增文件 `docs/contracts/README.md` / `docs/contracts/task-contract-v1.json` / `tests/test_task_contract.py`（+354，零修改既有文件）；`e6c1f03` = README 硬约束对齐（1 file）。相对 `0b18900` 累计 ahead 2、文件集合恰为这 3 个新增文件，**无运行时代码改动**。
- **IMPLEMENTED (回读中发现并修掉的不一致):** 首次回读逐字比对时发现 README 写「三条硬约束」而 JSON 的 `hard_constraints` 为 5 条、测试断言 `>=5`——文档与其唯一用途（可 diff 的对表）矛盾。`e6c1f03` 把 README 改为与 JSON 逐条同序的 5 条，并明确写出「测试只断言 JSON 侧，README 侧靠人工同步」，不夸大机械保证。
- **TESTED (本地):** `tests.test_task_contract` → **13 tests OK**（`0.002s`）；修正后重跑仍 13 OK；`git diff --check` exit 0。全量套件在 `94ee0f9` 上为 **1108 OK / 2 skip**（本地 vision=with），本次新增提交仅改文档，未重跑全量。
- **VERIFIED (远端 CI，`gh run watch --exit-status` 均返回 0）：**
  - `94ee0f9`：`CI` run **41861112184** success（`Ran 1108 tests in 70.360s`、`OK (skipped=21)`、Node `# pass 17 / # fail 0`）；`Feature CI - closed-loop settlement` run **41861149464** 四矩阵 job 全 success，每 job `Ran 1108 tests`（vision=with `skipped=4`，vision=without `skipped=21`）。
  - `e6c1f03`：`CI` run **41862075882** success（`Ran 1108 tests in 96.401s`、`OK (skipped=21)`、Node 17/0）；`Feature CI` run **41862089098** 四矩阵 job 全 success（`py3.12/3.13 × vision=with/without`，每 job `Ran 1108 tests`，with `skipped=4`、without `skipped=21`）。
  - 本地 2 skip 与远端 4/21 skip 的差额是平台条件跳过（2 项 Windows 命名互斥体 + vision 依赖缺失时的视觉测试），不是失败；两个 run 的 `head_sha` 已逐个核对，未张冠李戴。
- **VERIFIED (触发方式说明，重要):** 该分支的 push **不会自动触发任何 CI**——`ci.yml` 只监听 `push`→`main`/`dev/**`，`feature-closed-loop-ci.yml` 的 push 只列 `feat/closed-loop-settlement-20261104`。因此这两条工作流是用 `workflow_dispatch --ref docs/decision-contract-v1` 显式触发的，**未修改任何 `.github/workflows/*` 配置、未建 PR**。代价：两条 `dispatch` run 曾排队约 25 分钟才分配到 runner（同一分支前序 run 为秒级启动），属 GitHub 侧排队，与本仓库配置无关。
- **VERIFIED (GitHub 回读，四项):** ① `gh api .../git/ref/heads/docs/decision-contract-v1` = `e6c1f039dabae54143294adce4081cd492299362`；② `git ls-remote` 实时回读 `refs/heads/docs/decision-contract-v1` = `e6c1f03…`，与 API 一致；③ `compare/0b18900...e6c1f03` = `ahead_by=2`，文件全为 `added`（上述 3 个）；④ 三文件远端 blob SHA 与本地 `git rev-parse HEAD:<path>` 逐一相等（README `2991991b`、JSON `39fd865d`、测试 `9e891fdb`）。
- **VERIFIED (未触碰的分支):** 实时 `ls-remote`：`dev/dispatch-observer-safety-current-mvp-20261103` = `0b18900`、`feat/closed-loop-settlement-20261104` = `0b18900`、`main` = `a4f3777`，三者均未变；**未创建、未合并任何 PR**。
- **VERIFIED (补齐上一节点 LIMITS ⑦):** 上一节点的归档提交 `0b18900` 自身 CI 已回读——`CI` run **41848919027** success、`Feature CI` run **41849240900** success，head_sha 均为 `0b18900`，该条「不得描述为已核验」的限制到此关闭。
- **SAFETY / 授权边界:** 仅向用户授权的 `docs/decision-contract-v1` 推送，从未 push 到 dev/feat/main；未用 `--force` / `reset --hard`；未改 CI 配置；未执行任何 Poi / 游戏点击或派遣；契约本身仍是**无运行时消费者**的只读对表文件，本次改动不可能改变生产行为（`test_task_contract.py` 只读 JSON 与 `task_scope`，不 import 生产入口）。
- **LIMITS:** ① 本归档提交是纯文档提交，位于 `e6c1f03` 之上，其自身的 CI 另行触发的结论无法写进自身文本（避免自我引用），以当次对话记录与 Actions 页面为准；被 CI 证据覆盖的精确树是 `e6c1f03`。② 契约分支**未合并**，因此 `dev`/`feat`/`main` 上仍无这份契约，任何「已对表」的说法只在该分支成立。③ 契约 JSON 侧有机检、README 侧靠人工同步（已在文档内写明），二者仍可能再次漂移。④ 本节点不启动 M1；档 2（Data MCP）继续暂缓；档 3（替换 Poi MCP）维持不做。⑤ `MAX_FLEET_SIZE=6` 与 7 舰编成的潜在冲突**本次未改一行**，作为独立问题待确认 MVP 是否真需 7 舰后再评估。⑥ 实机验收（P1-1/2/3）继续暂缓。
- **ARTIFACTS:** `docs/contracts/README.md`、`docs/contracts/task-contract-v1.json`、`tests/test_task_contract.py`、`docs/PROJECT_kancollebot_Progress.md`（本节点）。


## 2026-11-05 — 治理文档与决策契约集成（PR #36 / #37）

- **VERIFIED (PR #36):** 决策契约分支 `docs/decision-contract-v1`（head `5bd0060b0bd9078e8f6f8d260ff878f371739a5f`）通过 PR #36 合入集成主干；merge commit `ef288821dc94a036656fd6d1c04e840dcc25780b`。合并前四矩阵 Feature CI run `41938466434` 全部成功；合并后 trunk push CI run `41945098427` 成功。远端回读确认 PR merged、集成主干 HEAD 与 `docs/contracts/README.md` 内容。
- **VERIFIED (PR #37):** 治理文档分支 `docs/unified-governance-20261105`（最终 head `9a4ca8423c5ed87f3935168e69fba7c5d425aa7f`）通过 PR #37 合入；merge commit `46362481e9b39092363575711996564d0270deee`。最终 head 的四矩阵 Feature CI run `41945335731` 全部成功。合并后 trunk push CI run `41945609248` 在本记录生成时仍为 in_progress，需单独回读最终结论，不把它写作已通过。
- **IMPLEMENTED (文档修订):** 更新分支统计（81 个远端分支、65 个 `dev/*`；PR 统计快照 37 总数、28 open、7 closed-unmerged、2 merged）；补充 `temp/*` 生命周期与本地无 upstream 分支保护规则；修正生产组合根行号为 `tools/one_expedition_ui_loop.py:914-932`；澄清 78 个 Python unittest 模块与 3 个 Node 测试文件；准确描述 R3 现状及 `fleet_data_provider` 缺口。
- **VERIFIED (R3 只读核查):** WorkBuddy 在集成基线 `0b18900` 上精读生产组合根与 supervisor/settlement/resupply 控制器；相关组件测试 58 项全绿。R3 仍未接线：生产组合根未注入 settlement/resupply controller，且生产代码没有实时 `FleetSupplyObservation(...)` 构造来源。未修改 R3 运行时代码，未进行 Poi 实机验收。
- **VERIFIED (远端分支保护现状):** GitHub branches API 对 `main` 与集成主干均返回 `protected=false`，Rulesets API 返回空数组。仓库账户有 admin 权限，但当前可用 GitHub 连接器没有 branch-protection/ruleset 创建或更新写操作；因此本轮**未配置**分支保护。另，`main` 当前没有 `.github/workflows/` 文件，配置 required checks 前须先建立能在 main PR 上实际触发的 CI。
- **VERIFIED (PR #35):** M1 PR #35 仍 OPEN / DRAFT，head `3b4b86490c458a255dd9f78ec538c485ae705b3f`；保持 Draft，未合并。
- **SAFETY:** 未删除或重置任何分支。WorkBuddy 报告的 11 个本地无 upstream 分支未出现在本轮远端分支清单中；仅确认其当前没有同名远端引用，不据此清理本地分支。
- **LIMITS / NEXT:** 集成主干最新 merge commit 为 `46362481e9b39092363575711996564d0270deee`。待回读其 push CI 结果；分支保护配置因缺少可用写接口 BLOCKED；R3 实施及 MVP 实机验收仍 PENDING。

## 2026-11-05 — R3 生产接线（本地实现）：结算/补给控制器注入 + 只读 Redux 补给数据源

- **用户授权:** 明确授权 R3 **本地**代码实施（建功能分支、改 R3 源码与测试、本地测试/静态检查/离线验证）；同时修正边界为**允许真实操作 Poi 与游戏 UI**，但 GitHub 推送/PR 合并/分支治理仍按原授权（本轮未推送、未合并、未删分支、未改保护）。
- **IMPLEMENTED (分支与基线):** 分支 `feat/production-settlement-resupply-wiring-20261105`，基线为**远端追踪** `origin/dev/dispatch-observer-safety-current-mvp-20261103` = `ba8884466f2bb1d568cc46b76034711df81bb50c`（`git fetch` 后实测，与用户给定预期 SHA 一致；未基于旧本地 `0b18900`）。该基线已含 PR #36/#37，**不含** PR #35（M1）。
- **IMPLEMENTED (只读 Redux → FleetSupplyObservation 适配器):**
  - `src/kancollebot/adapters/poi/cdp_websocket.py`：新增第二个**固定**只读表达式 `READ_ONLY_PORT_SUPPLY_SNAPSHOT_EXPRESSION`，并把 `Runtime.evaluate` 校验抽成 `require_allowed_runtime_evaluate()`；白名单只接受两个固定字符串，任意表达式与额外参数仍被拒（错误文案保留 `fixed read-only fleet snapshot` 子串以兼容既有断言）。
  - `src/kancollebot/adapters/poi/cdp_port_supply.py`：`read_poi_port_supply_snapshot()` 严格校验 port 形态（3–4 舰队、mission 4 槽、roster ≤6 且 -1 合法、舰船 ID/主数据键一致、HP ≤ 上限、燃料/弹药非负、材料 ≥4 项、非安全整数拒绝）；缺字段/格式错误一律 `CdpPortSupplyError`，**不填默认值**。`observed_at` 取**读取完成之后**的时刻。
  - `src/kancollebot/expeditions/port_supply_provider.py`：`PoiReduxFleetSupplyProvider` 把 Redux 快照与 monitor 的**真实端口 witness**（`BackgroundPortSnapshotProvider`）合成 `FleetSupplyObservation`，`source="poi-redux:port"`；`source_freshness_verified` 仅在 witness 存在、未过期、经 `port_snapshot_matches_current_state` 逐字段匹配且材料清单亦匹配时为 True，否则 False 并由控制器 preflight 拒绝（fail-closed）。`observed_at` 用读取时间而非 witness 时间戳（后者仅作证据记录 `last_witness_captured_at`），因为补给后复核必须证明「读取晚于点击」。
- **IMPLEMENTED (生产组合根):** `tools/one_expedition_ui_loop.py` 组合根现在构造并注入 `settlement_controller` 与 `resupply_controller`，三者与 refresh driver **共用同一个 `UiOperationArbiter` 实例**；复用既有 `CdpContinuousExpeditionUiDriver`（同时满足结算/补给两个 UiDriver 协议），未新增点击实现；`allow_ui_actions=True` 仍受 `--authorize-ui-actions`（required）门禁。新增 `_read_poi_port_supply_snapshot()` 以短连接 `connect_poi_session` 读取 Redux（Poi 页面目标，与点击用的 game webview 分离）。新增事件 `PRODUCTION_CLOSED_LOOP_ACTIONS` 与启动事件中的 `settlement_controller_wired`/`resupply_controller_wired`/`shared_ui_arbiter` 字段。
- **UNCHANGED (安全约束):** 未放宽 `resupply_verified_fleet_ids=(2,3)`，Fleet 4 仍因无校准补给视觉门禁而 **fail-closed 拒绝点击**；未改控制器内部成功确认、防重复、歧义 block 逻辑；未改 `MAX_FLEET_SIZE` 与 7 舰问题。
- **VERIFIED (实机形态，只读):** 通过 Poi CDP（`127.0.0.1:9222`）单次只读读取确认 Redux 实际形态：`info.fleets` 为数组（4 条，含 `api_id/api_name/api_mission/api_ship`）；`info.ships` 与 `const.$ships` 是**以 id 为键的对象映射**（239 / 1755），不是数组；材料位于 **`info.resources`**（8 个整数，本机 `[44056, 62749, 61084, 32939, 484, 993, 2044, 50]`），`info.materials` / `info.material` / `const.$materials` / `info.resource` **均不存在**。表达式据此改为 `asItems()` 兼容数组与对象映射，并且只复制舰队列实际引用的主数据（本机 13 条），避免搬运 1755 条主数据。
- **VERIFIED (实机读取结果):** `read_poi_port_supply_snapshot()` 在真实会话返回 `READ_OK`：4 舰队 / 239 舰 / 13 主数据 / 8 项材料；Fleet 2 四舰 `6/15` 燃料、Fleet 3 四舰 `11/15`、Fleet 4 四舰 `11/15`（均未满补，符合此前观测）。**只读**：一次 `Runtime.evaluate`，无点击、无导航、无 API 请求。
- **TESTED (本地):**
  - 新增 `tests/test_cdp_port_supply.py`（18 项）：固定表达式被使用、`observed_at` 在载荷之后、null/非字符串/非法 JSON、缺材料、负燃料、HP 越界、roster 超 6、mission 槽数、缺舰、缺主数据、舰队 ID 不连续、舰船键不匹配、布尔材料值、非时区感知时钟、无 `send()` 传输。
  - 新增 `tests/test_port_supply_provider.py`（10 项）：匹配 witness 判定新鲜、无 witness、过期、未来时间、Redux 与 witness 不一致、材料不一致、witness 形态非法、`observed_at` 不等于 witness 时间、reader 返回错类型、构造参数校验。
  - 新增 `tests/test_supervisor_closed_loop.py`（4 项，真实控制器 + 脚本化外部边界）：**返回→结算→补给→再派遣同一 tick 完成**；补给禁用时 fail-closed 且不派遣；补给后未达满补则 block 且永不重派；脚本化 provider 仍能满足真实 `build_fleet_supply_plan`/`verify_fleet_supply_complete`。
  - 修改 `tests/test_one_expedition_production_integration.py`：**翻转**原「未接线」guard 为「已接线」正向断言（控制器装配、共享 arbiter、共享 scheduler/state_reader/event_sink、`allow_ui_actions=True`、`fleet_data_provider.port_witness_provider is runtime.provider`）；`FakeProvider` 改为可调用以匹配生产对象。
  - 修改 `tests/test_cdp_websocket.py`（+4 项）：两个固定表达式均被接受、二者互不相同、变形表达式被拒、参数必须恰好两个。
  - 全量：`PYTHONPATH=src python -m unittest discover -s tests` → **1144 tests OK / 2 skip**；Node `node --test tests/*.test.cjs` → **19 pass / 0 fail**；`python -m compileall -q src tools tests` 干净；`git diff --check` 干净。
- **LIMITS:** ① 本轮**未推送、未开 PR、未合并**，成果仅存在于本地分支。② **未做真实闭环实机验收**：未点击、未派遣、未收取、未补给；上述「同一 tick 闭环」为离线集成测试结论，不能等同于实机闭环。③ 空闲状态下手动抓取端口 witness 会 `process_timeout`（无自然 `/api_port/port` 响应）——生产路径依赖 monitor 的 UI 刷新/结算导航触发端口响应；若届时 witness 仍缺失，补给 preflight 会 fail-closed 而非误点，需在实机验收时确认该时序。④ Fleet 4 补给仍 BLOCKED（视觉门禁未校准）。⑤ 未更新 `docs/PRODUCT_ARCHITECTURE.md` / `DEVELOPMENT_ROADMAP.md` 中的 R3 状态描述（本轮只做实现，文档状态待远程归档时统一更新）。
- **ARTIFACTS:** `src/kancollebot/adapters/poi/cdp_websocket.py`、`src/kancollebot/adapters/poi/cdp_port_supply.py`（新）、`src/kancollebot/expeditions/port_supply_provider.py`（新）、`tools/one_expedition_ui_loop.py`、`tests/test_cdp_port_supply.py`（新）、`tests/test_port_supply_provider.py`（新）、`tests/test_supervisor_closed_loop.py`（新）、`tests/test_cdp_websocket.py`、`tests/test_one_expedition_production_integration.py`、`docs/PROJECT_kancollebot_Progress.md`（本节点）。

## 2026-11-05 — R3 代码审查与实机验证：驱动协议缺陷修复 + 真实补给/新鲜度验收

- **用户授权:** 本轮授权代码审查 + 真实单舰队闭环验收（允许真实 Poi 与游戏 UI 操作）；仍禁止推送、开 PR、合并、修改其它分支。
- **FINDING / FIXED (关键缺陷，本轮审查发现):** 上一提交把 `CdpContinuousExpeditionUiDriver` 当作「同时满足结算与补给协议」注入控制器，这是**错的**。该类只实现派遣（`prepare_dispatch`/`dispatch_expedition`/`return_to_home_port`）；`collect_return_result`/`claim_bonus_items`/`claim_reward`/`resupply_fleet` 只存在于另一个类 `PoiCdpContinuousUiDriver`（`cdp_ui_driver.py`），而该类**未被生产入口使用**，且其 `SettlementPlan`/`ResupplyPlan` 在生产代码中**没有任何构造来源**（仅测试构造）。按原提交运行，第一个返航舰队会以 `AttributeError` 结束，而不是完成结算。
- **IMPLEMENTED (补给链，驱动内):** `CdpContinuousExpeditionUiDriver.resupply_fleet(fleet_id)`：Home Port → `SUPPLY_MENU`(103,340) → 识别补给页（`SUPPLY_FLEET_1/2/3_SELECTED`/`SUPPLY_MENU`，6 采样）→ 必要时用 `locate_supply_fleet_tab` 视觉定位并点击舰队页签 → 连续 3 帧定位 `locate_fleet_supply_all_button` 且要求 bounds 完全一致 → 点击补给 → 回母港并双帧确认 `HOME_PORT` → 返回 `UiStateEvidence`。仅在舰队 2/3 可用（无页签校准的舰队直接拒绝），且补给完成与否**不由点击声称**，由调用方回读 Redux 逐舰核验。
- **IMPLEMENTED (组合根按能力接线):** settlement/resupply 控制器不再无条件注入。组合根检查驱动是否实现对应动作：resupply 具备 → 注入；settlement 不具备 → **不注入**并发出 `settlement_controller_unavailable` 事件（含 `missing_actions`），使 supervisor 在 `RETURNED_PENDING_UI` **继续暂停**（fail-closed），而不是点击未知坐标或崩溃。`shared_ui_arbiter` 事件字段相应容忍 settlement 为 None。
- **IMPLEMENTED (provider 时序收紧):** `PoiReduxFleetSupplyProvider` 的 freshness `now` 改为**取到 witness 之后**再计算，避免慢观察者让陈旧证据显得更新。
- **TESTED (离线):** 新增 `tests/test_continuous_expedition_ui_resupply.py`（6 项）：已选中舰队不点页签、页签未选中时先点页签再补给、按钮三帧漂移则拒绝点击、按钮定位失败则拒绝点击、Fleet 4 无校准页签直接拒绝、页面不可识别则在补给点击前停止。生产集成测试改写为「只注入驱动能执行的能力」并断言 `settlement_controller_unavailable` 事件；相关 45 项通过。全量 `PYTHONPATH=src python -m unittest discover -s tests` → **1150 tests OK / 2 skip**；Node 19 pass / 0 fail；compileall 与 `git diff --check` 干净。
- **VERIFIED (实机·补给):** 真实会话执行 `resupply_fleet(fleet_id=3)`：`HOME_PORT` 双帧确认（指纹 0.9986/0.998）→ 点击补给菜单 → 页面落在 `SUPPLY_FLEET_1_SELECTED`（0.9998/1.0）→ 视觉定位 Fleet 3 页签 bounds `[293,164,34,31]` 并点击 → `SUPPLY_FLEET_3_SELECTED` 双帧 1.0 → 补给按钮 bounds `[1062,604,92,30]` 三帧一致 → 点击 → 回母港双帧确认后返回。Redux 复核：Fleet 3 四舰 **燃料 15/15、弹药 20/20 与 15/15 全部满补**；材料 **燃料 44056→44040（−16）、弹药 62749→62735（−14）**，与预检缺口（16/14）完全一致；Fleet 2 未被补给（仍 6/15），确认单舰队语义。共 4 次 UI 点击（菜单、页签、补给、母港），无游戏 API 请求。
- **VERIFIED (实机·新鲜度):** 用 monitor 同款 `BackgroundPortSnapshotProvider` + `VisibleUiPortRefreshDriver` 走一次 `HOME_PORT→SORTIE_MENU→EXPEDITION_LIST→HOME_PORT`（3 次点击，各步双帧确认），随后捕获真实 `/kcsapi/api_port/port` witness，并由 `PoiReduxFleetSupplyProvider` 合成观测：`source_freshness_verified=true`、`source="poi-redux:port"`、`reject_reason=null`、`witness_captured_at=2026-11-05T02:08:40.837Z`、`observed_at=2026-11-05T02:08:46.687Z`（读取晚于 witness）、239 舰 / 13 主数据 / 8 项材料。这证明 `info.resources` 与 witness `api_material` 的顺序/取值一致（材料匹配通过），且全量舰船集合可与 witness 逐一匹配。
- **BLOCKED (结算):** 生产驱动**没有**返航结果/奖励页的点击链，项目中也无该链的坐标校准（`_STATE_MARKERS` 只能识别 `RETURN_RESULT`/`BONUS_ITEMS`，不能定位其按钮）。因此本轮**未派遣**：派遣后返航的舰队无法由代码结算，会把账号留在「已返航未收取」状态，违反「不确定即停止」。完整单舰队闭环（派遣→等待→结算→补给→再派遣）仍 **BLOCKED**，缺口是结算 UI 链，而非控制器或数据源。
- **SAFETY:** 未推送、未开 PR、未合并、未改分支保护、未删分支；未放宽 `resupply_verified_fleet_ids=(2,3)`（Fleet 4 仍 fail-closed）；未改 `MAX_FLEET_SIZE`/7 舰；实机操作严格限定为母港/补给页导航与一次 Fleet 3 补给，全部点击前均有双帧状态确认、点击后均有状态或 Redux 复核。
- **LIMITS:** ① 结算链缺失是下一阶段的首要开发项，需要真实返航画面做坐标/模板校准。② 补给驱动目前只用「舰队页签 + 全队补给按钮」，未走逐舰勾选路径。③ 本轮未做双舰队调度与长时间运行。④ 未更新 `PRODUCT_ARCHITECTURE.md`/`DEVELOPMENT_ROADMAP.md` 的 R3 表述（待远端归档统一处理）。
- **ARTIFACTS:** `src/kancollebot/adapters/poi/continuous_expedition_ui.py`、`tools/one_expedition_ui_loop.py`、`src/kancollebot/expeditions/port_supply_provider.py`、`tests/test_continuous_expedition_ui_resupply.py`（新）、`tests/test_one_expedition_production_integration.py`、`docs/PROJECT_kancollebot_Progress.md`（本节点）。

## 2026-11-05 — R3 实机闭环：返航气泡识别、结算链实跑、多舰队归属缺陷

- **用户授权:** 本轮授权继续 R3 实机闭环（允许真实 Poi 读与游戏 UI 点击），并追加「可以用中止远征加速舰队回港」「人工派遣了第二队和第四队远征，可以进行额外的结算链验证」；边界不变：不推送、不开 PR、不合并、不改分支保护/其它分支；Fleet 4 补给继续 fail-closed。
- **IMPLEMENTED (截图合成层):** `CdpInput.capture_screenshot(from_surface=...)`。`Page.captureScreenshot(fromSurface=True)` 取的是页面表面、丢掉合成覆盖层：2026-11-05 同一帧交替抓 4 次，返航气泡在 `True` 下 0.8332（判不出）、`False` 下 0.9994。仅 `HOME_PORT_RETURN_NOTICE` 用合成层，其余沿用原默认。
- **IMPLEMENTED (接管已观测任务):** `--adopt-observed-mission` + `scheduler.adopt_observed_mission()`。要求任务 id 等于该舰队配置任务、编制通过同一道门、status 1 还需母港已确认；status 2 直接进 `RETURNED_PENDING_UI`。不满足仍 BLOCKED 并发 `fleet_mission_adoption_rejected`。实机：Fleet 3 远征中（`mission=[1,1,…]`）被接管为 `EXPEDITION_ACTIVE`、`home_port_verified=1`。
- **IMPLEMENTED (结算续跑):** `api_mission` 在「结果被接受」的第一击就把状态清 0，而奖励链还有两屏。返航态观测改为可选：读到 idle 时记 `settlement_resumed_after_result_taken` 并按屏幕证据续跑；既无返航元组又无奖励页时用 `settlement_screen()` 探测后确认「已结算完成」(`settlement_closed_without_reward_screens`)；驱动无探测方法时保持原来的 fail-closed。
- **IMPLEMENTED (过场等待):** 奖励页每步「探测目标页 → 只点一次 → 有界轮询目标页」(`REWARD_SCREEN_POLLS=8`)；已在目标页则 0 点击续跑。补给链末尾回母港、派遣后回母港同样改为轮询——实机 2026-11-05 母港点击后 2.6 s 画面仍是补给页，单次检查把一次成功补给判成失败。
- **IMPLEMENTED (派遣后置顺序):** 先回母港、再取观测、再判 home 证据。原先派遣成功后立刻取观测会触发刷新导航，而导航前置 `HOME_PORT` 正在被开演动画遮挡，导致「点击已生效却记成 dispatch_blocked」。
- **IMPLEMENTED (回母港前置探测):** 开始点击后客户端不落在单一页面（先 `FLEET_3_SELECTED`，再回到仍选中该任务的列表）。`return_to_home_port()` 默认在 `EXPEDITION_LIST/MISSION_SELECTED/FLEET_x_SELECTED` 里只读证明当前页，命名单一状态仍严格。
- **IMPLEMENTED (`--settle-only`):** 只结算不派遣。跳过编制门（不会有启航点击），并注入 `SettleOnlyDispatcher`——共享 scheduler/state_reader/event_sink 但拒绝派遣，舰队变成 ready 时直接停。用于 Fleet 4 / Fleet 2 这类没有已验证编制的舰队。
- **IMPLEMENTED (启动即奖励页):** 进程启动时若画面被证明是奖励页（返航气泡/结果/奖励），**不发**母港恢复点击，改由结算控制器接管并发 `startup_reward_screen_detected`；`_recover_home_port` 自身也加了同样的拒绝（实机教训：重启后恢复路径在结果页点了两下，result→bonus→port 被盲推完）。
- **FINDING / FIXED (多舰队归属):** 奖励页只说明「哪次远征」，不说明「哪支舰队」。实机：Fleet 4 的结果已被先前运行接受（读 idle），两分钟后 Fleet 3 返航，本轮结算链收的其实是 **Fleet 3**，而调度库记在 Fleet 4 头上。新增 `_other_fleets_awaiting_result()`：目标舰队读 idle 且另有舰队 status=2 时拒绝结算（`cannot be attributed`），等只剩一个返航再收。
- **VERIFIED (实机·Fleet 4 完整结算链):** `fleet4_settle.sqlite3` 预置 `RETURNED_PENDING_UI` → 启动即识别奖励页、0 次恢复点击 → `settlement_resumed_after_result_taken` → 气泡(0.9992)→结果→奖励→母港三步各一次点击、每屏 6 采样双帧确认 → `settlement_reward_claimed` → 进入 `RESUPPLY_REQUIRED` 时按设计 fail-closed（Fleet 4 无校准补给页签）。
- **VERIFIED (实机·补给链):** 同一数据库上补给链 menu→Fleet 3 页签（视觉定位 bounds `[293,164,34,31]`）→`まとめて補給`（三帧 bounds 一致）→母港，四次点击全有前置/后置双帧证据。
- **VERIFIED (实机·返航气泡):** 合成层识别 `HOME_PORT_RETURN_NOTICE` 0.9991/0.9992/0.9989 多次命中，`fromSurface=True` 同帧只有 0.66–0.83。
- **TESTED:** 全量 `PYTHONPATH=src python -m unittest discover -s tests` → **1202 tests OK / 2 skip**。新增 `tests/test_continuous_mission_adoption.py`(14)、`HomePortReturnPreconditionTests`(3)、`RewardScreenFadeTests`(2)、`ResupplyExitFadeTests`(2)、`SettleOnlyDispatcherTests`(2)、结算控制器多舰队归属 2 项；`test_continuous_expedition_ui_settlement` 改写「已在目标页 0 点击续跑 / 越过目标页仍报错」。
- **COMMITS (本地分支 `feat/production-settlement-resupply-wiring-20261105`):** `9287b07`、`746c1a6`、`b26a5d0`、`506403f`、`a6ce812`、`4e37b14`、`c1a74f9`。
- **SAFETY:** 未推送、未开 PR、未合并、未改分支保护；Fleet 4 补给仍 fail-closed；没有任何一次点击发生在「未证明当前画面」的情况下（唯一例外是本节点末尾的人工收尾，见 LIMITS ⑤）。
- **LIMITS:** ① 多舰队**同时**待收时无法归属，只能串行：先只让一支返航，或人工收完一支。② 结算链无法在「启动时已有奖励页」的情形里取得可信新鲜度（监视器要求自然 `/api_port/port` witness，而 observer 在进程启动后才开始监听），`--settle-only` 对已挂着气泡的舰队会 fail-closed —— 正确做法是让循环在舰队仍在途时就运行。③ 「中止远征」仍未校准：中止不产生奖励、也走不到结算页，与本轮验证目标相反，故未启用。④ Fleet 3 的调度库在本轮结束时已被人工清空（其奖励实际由 Fleet 4 那次链收走），下一轮需重新派遣才能覆盖完整闭环的最后一段。⑤ 收尾时 Fleet 2 的气泡已挂起且 freshness 不可得，由人工在 (600,400) 点了三次（每次点击后重新分类确认：气泡→结果→奖励→母港，Redux 由 `[2,2,…]` 变 `[0,0,0,0]`）——这是本节点唯一一处非代码驱动的点击，记录在此以免被误读为自动化能力。
- **ARTIFACTS:** `src/kancollebot/adapters/poi/cdp_input.py`、`src/kancollebot/adapters/poi/continuous_expedition_ui.py`、`src/kancollebot/adapters/poi/visible_ui_refresh.py`、`src/kancollebot/expeditions/{continuous_scheduler,continuous_monitor,continuous_settlement_controller,continuous_dispatch_controller}.py`、`tools/one_expedition_ui_loop.py`、`tools/run_continuous_expedition_monitor.py`、`tests/test_continuous_mission_adoption.py`（新）等 16 个文件、`docs/PROJECT_kancollebot_Progress.md`（本节点）。

## 2026-11-05 — R3-A/R3-B：刷新归属审计层（origin + refresh_id）

- **用户指令:** 按 ①→② 推进但调整顺序：R3-A 核实 `26b13a7`（含推送与远端回读）→ R3-B 增加 `ensure_fresh(origin)` 刷新归属 → R3-C 再分析 freshness 判据（暂不改）→ R3-D 普通读取纯只读 → R3-E 实机闭环。三个实现要求：origin 必须是明确来源（`monitor_tick` / `dispatch_preflight` / `dispatch_post_action`，不能只记 `refresh`）；每次刷新关联唯一 `refresh_id` 并记录触发时间、导航步骤、witness 结果、点击次数、调用方；`ensure_fresh()` 与纯读取分离，且不得改变 fail-closed 语义。另授予"每轮通过门禁后自动提交并推送独立功能分支、随后远端回读"的权限（不含 main、force push、删分支、PR、分支保护）。
- **R3-A（本地提交）:** 分支 `feat/production-settlement-resupply-wiring-20261105`，HEAD `26b13a7db765668faa7dc25ae58901859a78a67e`。核实到该分支原先的上游是 `origin/dev/dispatch-observer-safety-current-mvp-20261103`（**非同名分支**），直接 `git push` 会覆盖别的开发成果，故改用显式 `HEAD:refs/heads/feat/...`；远端此前不存在该分支，本次为新建。
- **R3-A（远程推送）:** `* [new branch] HEAD -> feat/production-settlement-resupply-wiring-20261105`，并设为同名上游。
- **R3-A（远程回读）:** `git ls-remote` 返回 `26b13a7db765668faa7dc25ae58901859a78a67e  refs/heads/feat/production-settlement-resupply-wiring-20261105`，与本地 HEAD 逐字一致；`git diff HEAD origin/feat/...` 为空；`visible_ui_refresh.py` 的 blob 哈希本地/远端同为 `5dd4380d22fa006d972ddbd9d06191e54c17da92`；远端对象内 `await_passive_witness` 命中 1 次。
- **FINDING（刷新规模）:** 聚合 `closed_loop_adopt.sqlite3` 的 `dispatch_events`：**27 次刷新导航、75 次刷新点击、2 次派遣意图、0 次派遣确认**（03:09:21–04:11:56）。刷新并非只在派遣前发生，而是**周期性空转**——04:01:56→04:11:56 每 34–44 秒一轮三击，期间无任何派遣。根因是 `ContinuousExpeditionMonitor.tick()` 每次读观测都经由 `FreshnessRefreshingStateReader.read_fleets()` 隐式触发导航。
- **IMPLEMENTED（R3-B·导航上下文）:** `VisibleUiPortRefreshDriver.refresh_port_state(refresh_id=, origin=)` 只做审计：把 `refresh_id`/`refresh_origin` 注入该次导航发出的全部 9 类事件，并在 `last_refresh_stats` 记录 `clicks_attempted/completed/final_state/steps/outcome`。空字符串身份直接拒绝。
- **IMPLEMENTED（R3-B·归属读取）:** `FreshnessRefreshingStateReader.ensure_fresh(origin=)` 为显式入口，origin 必填且校验非空；`read_fleets()` 保留为遗留隐式入口，内部以 `origin="implicit_read"` 委托——行为不变，但隐式副作用从此在日志里可见。`ensure_fresh()` 与纯读取在接口上分离，刷新判据、cooldown、`max_wait`、fail-closed 语义一字未改。
- **IMPLEMENTED（R3-B·审计事件）:** 每次 `ensure_fresh()` 发 `ui_refresh_audit`，字段 `refresh_id / origin / outcome(already_fresh|cooldown_suppressed|refreshed|witness_timeout|blocked) / witness_verified / clicks_sent / navigation_steps / triggered_at / waited_seconds / error_type`。审计写失败被吞掉，绝不改变刷新策略。
- **IMPLEMENTED（R3-B·调用点命名）:** `monitor_tick`、`dispatch_preflight`、`dispatch_post_action`、`dispatch_home_verification`、`settlement_baseline`、`settlement_advance`。三个控制器均通过 `getattr(reader, "ensure_fresh")` 降级，普通 reader（离线 fake、未启用 UI 刷新）行为不变。
- **IMPLEMENTED（装配）:** `tools/run_continuous_expedition_monitor.py` 给刷新读取器接上 `event_sink`，审计落到既有 `dispatch_events` 表，无 schema 变更。
- **TESTED（本地）:** `PYTHONPATH=src python -m unittest discover -s tests` → **1221 tests OK / 2 skip**；`compileall -q src tests tools` OK；`git diff --check` OK；`node --test tests/*.test.cjs` → **19/19 pass**。新增 8 项：`RefreshAttributionTests`(6，含 driver 全步骤打标、空身份拒绝、refreshed/already_fresh/cooldown 三态、origin 必填、遗留入口自标 implicit)、monitor `test_a_tick_names_itself_as_the_refresh_caller`(1)、dispatch 三阶段 origin 序列断言并入既有用例(1)。
- **SAFETY:** 未推 main/集成主干、未 force push、未删远程分支、未创建或合并 PR、未改分支保护；推送目标为新建的同名独立功能分支。`26b13a7` 与本次提交均可在远端回读比对。
- **LIMITS:** ① freshness 判据本轮**未改**（按用户要求留到 R3-C）：仍是"port 响应与 Redux 全量船集一致"，本账号 239 艘下天然难成立。② `read_fleets()` 仍会隐式刷新，只是现在标 `implicit_read`；真正的只读化是 R3-D。③ 归属只证明"谁请求了刷新"，不证明"这次刷新有必要"——27 次里哪些可省要在 R3-C 用 audit 数据判定。④ 尚无实机运行验证新审计字段落库（R3-E 覆盖）。
- **BLOCKED（R3-B 远程推送未完成，已于 12:5x 结案，见下节）:** `fc3202c` 本地提交成功，但推送连续失败，**该项成果未达"远端归档验收"**。失败序列（2026-11-05T04:47Z 起）：`git push` via 9996 → `schannel: server closed abruptly`；重试 3 次 → `CONNECT tunnel failed, response 502`；7890 → `Empty reply from server`（ls-remote）/ `CONNECT tunnel failed, response 502`（push）；`curl -x 9996 https://api.github.com` → `Failed to connect`（代理进程已不存在，netstat 无 9996 监听）；`curl -x 7890 https://github.com/` → 200 但 git smart-HTTP 隧道仍 502。诊断结论：本机代理链路在本轮中途失效，非仓库权限问题——同一命令在 12:42 曾成功新建远端分支。按用户规则，依赖该成果的 R3-C/R3-D/R3-E **暂停**，待链路恢复后先补推 `fc3202c` 并回读核验，再继续。

## 2026-11-05 — R3-B 远端归档验收通过（补推 `fc3202c`）

- **用户指令:** 补推 `fc3202c` 与 `0a1e0f2`，从 GitHub 回读核对分支 HEAD、两个提交及进度文档内容；只有远端归档验收通过才开始 R3-C（用审计数据判断刷新是否必要）。
- **链路诊断更正:** 上一节的 BLOCKED 结论**部分不准**。复测发现：`7890` 实际可用（`git ls-remote` 直接返回 `26b13a7`），此前的 `Empty reply` / `502` 属瞬时故障而非端口失效；`9996` 进程确已不存在（`Failed to connect`）；直连 `api.github.com` 返回 200，但 git smart-HTTP 直连仍 `Recv failure: Connection was reset`；环境中另有 `127.0.0.1:2930` 系统代理，对 git CONNECT 返回 502，不可用于推送。**可用路径是 `git -c http.proxy=http://127.0.0.1:7890` + `NO_PROXY=127.0.0.1,localhost`。** 由此得出一条可复用判据：`curl` 能通不代表 `git push` 能通，反之某个代理的 `curl` 报错也不代表其 git 隧道不可用——必须用 `git ls-remote` 判定。
- **REMOTE PUSH:** `git push origin HEAD:refs/heads/feat/production-settlement-resupply-wiring-20261105` → `26b13a7..0a1e0f2  HEAD -> feat/production-settlement-resupply-wiring-20261105`。仍用显式 refspec，未走裸 `push`（上游指向 `origin/dev/dispatch-observer-safety-current-mvp-20261103`）。
- **REMOTE READBACK（分支 HEAD）:** `git ls-remote --heads origin` → `0a1e0f20e6bd88517aab08b677545791791ca051`；本地 `git rev-parse HEAD` → 同值，逐字一致。
- **REMOTE READBACK（两个提交）:** GitHub 连接器 `get_commit` 对远端对象独立取回：`fc3202c3063d902d6a99872972f74ce4cdd98e47`（9 文件，+457/−30：`visible_ui_refresh.py` +216/−15、`continuous_dispatch_controller.py` +20/−7、`continuous_monitor.py` +15/−2、`continuous_settlement_controller.py` +13/−4、`tests/test_visible_ui_refresh.py` +133、`tests/test_continuous_monitor.py` +25/−2、`tests/test_continuous_dispatch_controller.py` +16、`run_continuous_expedition_monitor.py` +3、进度文档 +16）；`0a1e0f20e6bd88517aab08b677545791791ca051`（1 文件，+1）。两者提交信息完整，时间戳 04:47:35Z / 04:49:54Z。
- **REMOTE READBACK（进度文档）:** `origin/feat/...:docs/PROJECT_kancollebot_Progress.md` 的 blob 哈希 `530b0f4c6dbf65e1be1cc3820bde9da0fbc6351e`，与本地 `git hash-object` 结果**完全相同**（逐字节一致，非仅文本相似）；`git diff HEAD origin/feat/...` 输出为空，工作树无差异。
- **TESTED:** 本轮为推送与回读，未改代码；上一轮门禁结果沿用（1221 tests OK / 2 skip、compileall OK、diff-check OK、Node 19/19）。归档修改仅文档，提交前重跑 `git diff --check`。
- **SAFETY:** 未推 main/主干、未 force push（快进推送 `26b13a7..0a1e0f2`）、未删远程分支、未建/合 PR、未改分支保护。
- **FINDING（R3-C 前置事实）:** 事件库最新记录停在 `2026-11-05T04:11:59Z`，534 条 `dispatch_events` 中 **`ui_refresh_audit` 计数为 0**——R3-B 的归属字段从未在实机落库。因此 R3-C 的 origin 维度**不可能由现有数据得出**，只能先做"无 origin 的刷新必要性"离线判定，origin 定量必须等一次实机运行。
- **LIMITS:** ① 本节只证明"成果已归档到远端"，不证明刷新策略正确。② `7890` 的稳定性本轮只验证到单次成功，长时间批量推送仍需重试准备。

## 2026-11-05 — R3-C：用审计数据判定刷新是否必要（只诊断，不改判据）

- **用户指令:** 远端归档验收通过后才开始 R3-C；用审计数据判断刷新是否必要。判据本身**本轮不改**。
- **数据源:** `.workbuddy/state/closed_loop_adopt.sqlite3`，`dispatch_events` 534 条，窗口 `03:09:21–04:11:59Z`（3758s），27 个刷新批次、75 次刷新点击、2 次派遣意图、0 次派遣确认。
- **判据（本轮新建，可复核）:** 刷新是否被消费 = 刷新完成事件之后是否存在消费者事件（`fleet_mission_adopted` / `dispatch_intent_persisted` / `settlement_step_started` / `resupply_started` / `dispatch_preparation_started` 等），且滞后 ≤12s。该判据不依赖 origin，用消费者时序反推刷新产出，因此对旧数据同样可用。
- **FINDING（产出二分）:** 27 批中 **10 批被消费、17 批无产出**。被消费的 10 批滞后全部落在 **0.1–0.2s**，无产出的 17 批最近消费者也要 **≥53.5s**（11 批之后完全没有消费者）——中间**没有任何样本**。这种双峰分布使"该次刷新是否被使用"可以被单点判定，而不是靠阈值取舍。
- **FINDING（点击账）:** 有效 30/75 次点击（40%），**空转 45/75（60%）**。空转不是零星噪声：尾部 `04:02:13` 之后 **11 批 / 31 点击 / 586s 零动作**；结算前三连冗余 `#10 #11 #12`（03:36:02–03:37:47，9 次点击）之后 `#13` 才真正触发结算。
- **FINDING（刷新的实际产出）:** 27 次刷新只换来 10 次有效交付——发现返航 3 次（`#1 #9 #16`）、派遣前置 2 次（`#6 #15`）、结算前置 3 次（`#2 #4 #13`）、补给前置 2 次（`#5 #14`）。**监控循环 27 次全场刷新，真正改变系统认知的只有 3 次**（三次 `fleet_mission_adopted` 均在刷新完成后 0.1s 内发生）。
- **FINDING（阻塞代价）:** 24 次完成态批次的 `sequence` 全部是同一条四段路径 `HOME_PORT>SORTIE_MENU>EXPEDITION_LIST>HOME_PORT`、恒定 3 击，平均耗时 **19.2s**；27 批累计 **473s = 窗口的 12.6%**，该区间 `refresh_lock` 持有，monitor 在此期间完全不可观察。
- **FINDING（失败开销）:** 3 批 `blocked`（`#3` `#7` `#27`）各消耗 1 次点击后 `LoopError`，`diagnostic_category=unclassified`；其中 `#7` 发生在舰队在途期，`#27` 是尾部空转的最后一击。
- **FINDING（结构性缺口，本轮最重要的发现）:** `monitor_poll_seconds=5`、`refresh_cooldown_seconds=30`、`max_state_age=30s`、单次刷新 19.2s。推导：刷新于 T 开始、T+19 产出可信观测，有效期至 T+30；下一次最早 T+30 才可刷新、T+49 才有新观测 → **每周期存在约 19s 无任何可信观测的缝隙，其间 tick 每 5s 触发一次，约 4 个 tick 必然 `require_fresh` 失败**。实测间隔中位数 35s 与"cooldown 30s + tick 5s 对齐"吻合。**结论：35s 已经是当前参数下的下限，调大 cooldown 只会加宽缝隙，不会减少总游戏输入——瓶颈是单次刷新的信息量与观测有效期不匹配，不是刷新次数。**
- **FINDING（判据为何不生效）:** `_is_trusted` 只认 `source_freshness_verified`，而该标志要求 port 响应与 Redux 全量船集一致。27 次刷新全部发生在 cooldown 到期的 tick 上，说明 `already_fresh` 分支在实机**从未命中**——"数据没变"从不被识别为"不必刷新"，这正是 63% 空转的直接来源。按用户要求，本轮只诊断，未改该判据。
- **VERIFIED（归档前置）:** R3-C 开始前已完成远端归档验收：`fc3202c` 与 `0a1e0f2` 回读一致（`get_commit` 返回完整对象）、分支 HEAD 与本地一致、进度文档 blob `530b0f4c6dbf65e1be1cc3820bde9da0fbc6351e` 双侧相同。
- **TESTED:** 本轮为离线数据分析，未改代码；统计脚本可重跑复核（判据与阈值均在归档中写明）。提交前 `git diff --check` 通过。
- **SAFETY:** 未改任何刷新判据、cooldown、`max_state_age` 或点击序列；未改代码；未推 main、未 force push、未建 PR。
- **LIMITS:** ① 因 `ui_refresh_audit` 为 0，origin 维度仍不可得；"10 批被消费"是按消费者时序反推的**上界**，不代表这 10 批全部必要——`#5`/`#14` 实际是由 `settlement_resumed_after_result_taken` 在 0.1s 内消费的，补给动作分别要到 8.9s / 9.8s 之后才开始，因此这 2 批更可能属于"结算恢复"而非"补给前置"。② 尾部 586s 空转的成因（为何 adopted 之后仍持续刷新）尚未定位，需 R3-D 纯只读化后复测。③ 未验证 `EXPEDITION_LIST` 这一步是否可省（四段路径中唯一未证明必要的中间态），属于点击裁剪候选，留待 R3-D/E。
- **NEXT:** R3-D（`read_fleets()` 纯只读化）——预计直接消除尾部 17 批空转中的相当部分，因为它会把"无消费者时的刷新"从隐式副作用变为显式拒绝。

## 2026-11-05 — R3-D：刷新责任迁移 + `read_fleets()` 纯只读化

- **用户指令:** 继续 R3-D 但调整边界——**先解耦、再纯只读化，不能直接删掉 `read_fleets()` 的刷新副作用**。四步顺序：① 刷新责任明确（monitor_tick 不再隐式导航，需要时显式 `ensure_fresh(origin=)`，派遣/结算保留各自策略）；② 纯只读化（只读、不点击、不导航、不等待，不新鲜仍 fail-closed，不降门槛）；③ 处理无消费者周期（无任务不刷新；有任务且过期时显式刷新；刷新失败停止依赖该观测的动作）；④ 评估 `EXPEDITION_LIST` 裁剪（单独验证，未经页面状态验证不删）。验收：`read_fleets()` 零 UI 副作用、monitor 无任务零刷新、有任务且过期时显式刷新仍工作、超时/过期仍 fail-closed。不改 freshness 判据、不扩到 R3-E。
- **IMPLEMENTED（① 责任迁移）:** `ContinuousExpeditionMonitor.tick()` 改为"只读 → 判可用 → 有任务才刷新"。刷新只经 `_request_refresh(origin="monitor_tick")` 发起；reader 若无 `ensure_fresh`（离线 fake、未启用 UI 刷新的运行时）直接复用本次只读结果，**不再重读**——第二次读只会拿到同一份未验证状态。刷新后**重新取 `now`**，否则 19s 的导航会用刷新前的时间戳去判新观测。
- **IMPLEMENTED（③ 无消费者周期）:** 新增 `_pending_work_reason(now)`，判据取自 **scheduler 的持久化 phase（不依赖观测新鲜度，因此不存在循环依赖）**：`IDLE`/`DISPATCHING`/`RETURNED_PENDING_UI`/`RETURN_RESULT`/`BONUS_ITEMS`/`RESUPPLY_REQUIRED`/`READY` 一律算待处理；`EXPEDITION_ACTIVE` 仅在"尚欠派遣后母港验证"或"返航时刻落在 `refresh_lead_time` 内"时才算；`BLOCKED` 不算（等人工，刷新只会得到同一份判据）。无任务时不导航，直接 fail-closed 抛 `ContinuousMonitorError`，消息含 "will not navigate for a fresh one"。有任务时发 `monitor_refresh_requested`（含 `origin` 与 `reason`）后刷新，供 R3-E 归因。
- **IMPLEMENTED（② 纯只读化）:** `FreshnessRefreshingStateReader.read_fleets()` 改为真正的只读：在 `_refresh_lock` 内直接 `self._reader.read_fleets()`，**不导航、不点击、不等待、不写 audit、不推进 cooldown**。`implicit_read` 这个 origin 连同遗留入口语义一并移除（全仓库已无引用）。`ensure_fresh()` 是唯一可能导航的入口，行为一字未改。
- **IMPLEMENTED（参数）:** `ContinuousExpeditionMonitor(refresh_lead_time=timedelta(seconds=60))`，校验非负 `timedelta`；运行工具新增 `--refresh-lead-seconds`（默认 60s）并透传。60s 的取值依据：单次刷新 19.2s + cooldown 30s ≈ 50s，留一次余量，保证返航前至少能完成一轮刷新。
- **FINDING（④ `EXPEDITION_LIST` 裁剪评估，本轮不删）:** 静态可证的部分是——`source_freshness_verified` 只取决于 `port_snapshot` 与当前 Redux fleet/ship 集是否一致（`expedition_reader.py:317-326`），**与是否经过 `EXPEDITION_LIST` 页面无关**。三步导航本身不产生 witness 数据，它的作用是让客户端重新请求 port payload，而这一步发生在"回母港"。因此真正被需要的是**"离港再回港"这个模式**，`EXPEDITION_LIST` 只是当前的中间态选择；`SORTIE_MENU→HOME_PORT` 两击是否同样能触发 port 请求，**代码无法证明，必须实机 A/B 验证**（在 HOME_PORT 点 HOME_PORT 是否触发请求同样未知）。按用户要求本轮未删除任何导航步骤。
- **TESTED:** `PYTHONPATH=src python -m unittest discover -s tests` → **1225 tests OK / 2 skip**（上轮 1221；净增 4 条新用例，另有 1 条旧用例按新语义重写）；`compileall -q src tests tools` OK；`git diff --check` OK；`node --test tests/*.test.cjs` → **19/19**。新增/重写用例：`test_reading_a_stale_state_navigates_for_nothing`（driver 一旦被调用即 fail）、`test_a_tick_names_itself_only_when_it_asks_for_a_refresh`、`test_a_tick_with_nothing_pending_refuses_to_navigate`（断言 `origins == []` 且 `read_count == 1`）、`test_a_return_due_within_the_lead_time_still_asks_for_a_refresh`（断言 reason 含 "due to return"）、`test_a_refresh_that_still_lacks_a_witness_fails_closed`、`test_an_expired_observation_still_fails_closed`。CLI 侧验证 `--refresh-lead-seconds -1` 被拒。
- **VERIFIED（验收矩阵）:** ① `read_fleets()` 零 UI 副作用 → 用例用 `self.fail` 的 driver 证明；② monitor 无任务零刷新 → `origins == []`；③ 有任务且过期时显式刷新正常 → `origins == ["monitor_tick"]` 且 `reason` 指明到期舰队；④ 超时/过期仍 fail-closed → 两条 fail-closed 用例 + 既有 `test_stale_or_unverified_observation_changes_no_fleet_state` 断言调度器状态不变。
- **SAFETY:** 未改 freshness 判据、`max_state_age`、cooldown、`max_wait` 与三步点击序列；未删除任何导航步骤；未触及 R3-E 范围；派遣/结算控制器的 `ensure_fresh(origin=...)` 路径未改（其 `read_fleets()` 降级分支只对无刷新能力的 reader 生效，语义不变）。
- **LIMITS:** ① **未实机验证**，实机空转是否真降、派遣/结算是否回归，仍需 R3-E 跑一轮。② `BLOCKED` 舰队不再触发刷新，这是"等人工"的语义延伸，但若某舰队因瞬时误判进入 BLOCKED，将不再自我恢复——需实机确认不会卡死。③ 无任务时每 tick 抛 `FRESHNESS_UNVERIFIED`，运行工具按既有路径 continue，但日志量会明显上升：这是"不点击"的直接代价，而不是新故障。④ `refresh_lead_time=60s` 是推算值，需 R3-E 用 `ui_refresh_audit` 与返航时刻对照校准。⑤ 刷新后 `now` 重取，但 `max_state_age=30s` 相对 19.2s 的刷新耗时偏紧，可能在刷新刚结束就接近过期边界——属于判据范畴，本轮按约定不改。

## 2026-11-05 — R3-E（实机闭环，未完成）：派遣成功、返航识别成功，结算链在奖励页与跨舰队归属上停住

- **用户指令:** 开始 R3-E 实机闭环；派遣优先**远征 1（15 分钟）**，其次**远征 3（20 分钟）**——这两个时长最短。远程归档仍按既定门禁。
- **IMPLEMENTED（三处实机缺陷修复）:**
  - `UiState` 补 `FLEET_4_SELECTED`。`return_to_home_port()` 的候选页由 `_supported_fleets=(2,3,4)` 动态拼接，Fleet 4 在枚举里不存在，`UiState("FLEET_4_SELECTED")` 抛 `ValueError`；`ValueError` 不在 `_NOT_THIS_SCREEN` 白名单里，于是"拒绝点击"升级成整轮崩溃（05:23:37 实机）。补齐后降级为正常的"none of ..."拒绝。附回归用例 `test_every_candidate_page_is_a_declared_ui_state`。
  - 入口新增**显式引导刷新** `ensure_fresh(origin="run_bootstrap")`。R3-D 的两条规则在启动期互锁：观测过期 → 判据看持久化 phase 认为"无待处理任务" → 不许导航 → 观测永远无法变新。05:35:24 实机一条 tick 都没跑就 `STOPPED_FAIL_CLOSED`。引导刷新由**需要该状态的调用方**声明，符合 R3-B 的归属模型，不改判据。
  - `--wait-for-return` 容忍 fail-closed tick。等待返航期大部分 tick 本来就没有可信观测（witness 30s 过期、返航在 60s lead 之外），拒绝行动是**正确答案**；此前一次拒绝就终止整轮，等于保证永远等不到返航。现在记 `PRODUCTION_TICK_OBSERVATION_UNAVAILABLE` 后继续下一个 tick，仍受 `--max-ticks` 约束；单次模式（非等待）照旧首拒即停。
- **VERIFIED（首次拿到完整归属证据）:** 05:27:40 `ui_refresh_audit`：`origin=monitor_tick`、`outcome=refreshed`、`witness_verified=true`、`waited_seconds=20.47`、`clicks_sent=3`；紧随其后的 05:27:40 `origin=dispatch_preflight` 为 **`already_fresh`、0 额外导航**。这正是最初"派遣前执行两次出击-远征"问题的消失证据：一次刷新产出被下一次调用直接消费，而不是各扫一遍。
- **VERIFIED（返航路径）:** 05:42:19 `monitor_refresh_requested`，reason 为 `fleet 3 is due to return at 05:43:18`（返航前 60s 内才刷新）；05:42:35 导航到 `EXPEDITION_LIST`，05:42:43 `HOME_PORT`，05:42:49 `HOME_PORT_RETURN_NOTICE`，05:42:44 Poi status 2 → `confirm_returned`，05:43:02 `RETURN_RESULT` 实拍确认并推进一步。刷新时机与返航时刻的关系与 `refresh_lead_time=60s` 的设计一致。
- **FINDING 1（结算链断点）:** `BONUS_ITEMS` 指纹与当前布局不匹配。实拍画面确为「獲得ボーナス／獲得アイテム」，但 `classify_screenshot_fingerprints` 连判 6 次 UNKNOWN（`Expected BONUS_ITEMS; no stable fingerprint match was obtained after 6 attempts`），05:44:43 fail-closed。现有模板 crop 为 `[280,280,320,160]`，与该页实际版面不符，需实机重采。
- **FINDING 2（跨舰队归属错误，安全相关）:** 两支舰队返航间隔仅 6 秒（fleet 3 `05:43:18.968`、fleet 2 `05:43:24.698`）。为 **Fleet 3** 跑的结算，实际取走的是 **Fleet 2** 的结果与奖励：实拍奖励页为「練習航海」（任务 1）且舰娘为初風 Lv17 / 旗風 Lv15 / 松風 Lv14 / 神風 Lv31（Fleet 2 编成，Fleet 3 为 88/92/76/74）；事后 Poi 显示 fleet 2 `[0,0,0,0]`（已被取走）、fleet 3 仍 `[2,3,…]`。返航提示页**不携带舰队标识**，而 1/2/3 队同名（ブラック羽川3），因此只能靠编成舰 ID 区分；现有 `_other_fleets_awaiting_result()` 只在"另一队仍在等待结果"时拒绝，上述时序下两队的提示同时存在，未触发。
- **FINDING 3（BLOCKED 无解除路径）:** `ContinuousScheduler` 只有 `block()`、没有 `unblock()`；`adopt_observed_mission()` 显式拒绝 BLOCKED（只接受 IDLE/READY/RESUPPLY_REQUIRED）；monitor 对 BLOCKED 直接 `blocked_hold` 短路。结果是：**派遣点击已成功、仅因派遣后确认失败而被标 BLOCKED 的舰队，没有任何受支持的方式恢复**。本轮两支舰队（05:23:17 fleet 3 / 05:28:22 fleet 2）都是这种情况，`--adopt-observed-mission` 对其无效，必须换新库才能继续。
- **FINDING 4（派遣后确认长期缺口，非本轮引入）:** 派遣后的回母港检查点无法稳定识别舰队页：`FLEET_2_SELECTED` 与 `FLEET_3_SELECTED` 模板仅裁 `50x60`（x=565/615, y=145），该区域不覆盖舰队标签，二者得分差约 0.01，低于 `minimum_margin=0.04`，同一屏时而判 `FLEET_2_SELECTED`、时而 UNKNOWN。`adopt` 历史库里三次派遣有两次（03:24、03:53）同样在 `dispatch_input_completed` 之后 block，再由下一轮 `fleet_mission_adopted` 补认——说明这是既有缺口，不是 R3-D 引入。
- **TESTED:** `PYTHONPATH=src python -m unittest discover -s tests` → **1226 tests OK / 2 skip**（上轮 1225，新增 1 条枚举回归用例）；`compileall -q src tests tools` OK；`tests.test_one_expedition_ui_loop` 70 项 OK（1 skip）；`tests.test_continuous_expedition_ui` + `tests.test_ui_state_gate` 34 项 OK。
- **SAFETY:** 未改 freshness 判据、`max_state_age`、cooldown、导航三步序列；未删任何导航步骤；引导刷新与服务同样走 `ensure_fresh(origin=...)`，未新增旁路；等待模式的容忍只影响"是否继续等"，不含任何额外点击（`game_actions_sent=0`）。
- **LIMITS:** ① R3-E **未完成**：缺 `BONUS_ITEMS` 之后的补给与再派遣证据；奖励页停在屏幕上未推进。② FINDING 2 的跨舰队归属错误属安全问题，在修好前**不应让两支舰队同时返航**。③ 本轮未验证 `EXPEDITION_LIST` 可裁剪。④ 引导刷新使 `run_bootstrap` 成为新的 origin，尚未纳入 origin 枚举文档。
- **NEXT（建议顺序）:** ① 结算前后用编成舰 ID 校验归属，不匹配则拒绝（FINDING 2，安全优先）；② 实机重采 `BONUS_ITEMS` 指纹（FINDING 1）；③ 给 BLOCKED 增加受支持的解除路径（FINDING 3）；④ 重采/改用 marks 识别舰队页（FINDING 4）；⑤ 再跑一轮单舰队闭环取证补给与再派遣。

## 2026-11-05 — R3-E 补充：实机重采 `BONUS_ITEMS` 指纹，并改用它右下角的「次」推进奖励页

- **用户指令:** 游戏画面停在 Fleet 2 的「獲得ボーナス」页，可以采集；并指出右下角有「次」按钮。
- **IMPLEMENTED（两处，均为实机证据驱动）:**
  - `BONUS_ITEMS` 指纹重采：`crop [280,280,320,160] -> [600,502,182,48]`、`grid [24,16] -> [24,6]`、`samples 1 -> 5`。旧裁剪框落在**左侧舰娘列表**（随编成变化），实机在同一页只得 **0.9021**，低于阈值 0.97；新裁剪框只覆盖右栏「獲得アイテム」标签与其周围的固定底纹，不含立绘与图标。
  - 新增 `BONUS_NEXT = RecordedPoint(1144, 665, "bonus-next")`，`_advance_reward_screen()` 在 `before == "BONUS_ITEMS"` 时用它推进；前两步（返航提示→结果、结果→奖励）继续用原 `REWARD_ADVANCE (600,400)`。原注释称"三个奖励页点击任意位置都能推进"，但奖励页这一步**从未被执行到过**（每次都停在指纹校验），因此不为它假设可用性。
- **VERIFIED（实机，13:53 起）:**
  - 采集后同一画面分类：**`BONUS_ITEMS` = 1.0000**，次优 `HOME_PORT` = 0.7945，margin 0.2055（阈值 0.04）；13:45 的旧截图同样判 1.0000，说明该区域不随时间变化。
  - 负样本（远征列表页）在同一裁剪下相似度 **0.3927**，且整页分类仍为 `UNKNOWN`。
  - 点击 `(1144,665)` 一次：1.5 秒后画面由 `BONUS_ITEMS`（0.8002）变为 **`HOME_PORT`（0.9984）**，即奖励页确实以「次」离开。
- **TESTED:** 全量 **1226 tests OK / 2 skip**；`compileall -q src tests tools` OK；Node `tests/*.test.cjs` 19/19；`tests.test_continuous_expedition_ui_settlement` + `test_continuous_expedition_ui` + `test_visible_ui_refresh` 共 61 项 OK。新增断言：奖励页推进点必须是 `BONUS_NEXT`；三步链路合计 2 次空区点击 + 1 次「次」点击。
- **SAFETY:** 未改 freshness 判据、`max_state_age`、cooldown、导航序列；未新增点击次数（仍是每奖励页一击）；`(1144,665)` 在点击前用分类器确认页面为 `BONUS_ITEMS`，点击后确认到达 `HOME_PORT`。
- **LIMITS:** ① 「空区点 (600,400) 在奖励页不可用」**未被证伪**——本轮是直接改用「次」，未在奖励页做 A/B。② 新指纹只在本账号 Fleet 2 的「練習航海」奖励页采到；其他任务若奖励页有额外元素（如多行物品），需复测。③ `RETURN_RESULT` 仍在用 `[280,280,320,160]` 这套同样的宽框，未重采。
- **NEXT:** 用新指纹 + 新推进点，对仍待领取的 Fleet 3（`api_mission [2,3,…]`）跑一次单舰队结算，取证「结算→补给→再派遣」。

## 2026-11-05 — R3-E 阻塞：启动即遇奖励页时，监控与结算互相锁死

- **场景（实机复现）:** Fleet 3 返航未领取（`api_mission [2,3,…]`），母港上浮着它的返航提示。执行 `one_expedition_ui_loop.py --settle-only --adopt-observed-mission --wait-for-return`。
- **观测证据:** 合成层 (`from_surface=False`) 分类为 `HOME_PORT_RETURN_NOTICE` **0.9991**；默认层同一时刻只读到纯母港 `HOME_PORT` **0.9980**。即返航提示位于合成浮层，默认层看不到——两层的判定都与事实相符，不是分类器错误。
- **死锁链条（每一步都符合各自规则，合起来却无法前进）:**
  1. 启动探测到奖励页 → `startup_reward_screen_detected`，**不执行母港恢复**（防止盲点推进奖励链，正确），并置 `enable_ui_refresh=false`（导航从 HOME_PORT 出发，奖励页在时不能跑，正确）。
  2. 每 tick 先跑 `monitor.tick()`，Poi witness 未验证 → 抛 `PRODUCTION_TICK_OBSERVATION_UNAVAILABLE`（R3-D 之后 monitor 不隐式导航，正确）。
  3. 采纳只发生在 `monitor._reconcile_fleet()` 内（入口把 `--adopt-observed-mission` 传给 `adopt_unplanned_missions`），而 monitor 在采纳之前就抛了 → **DB 里 `fleet_runtime` 一行都没有，事件 0 条**。
  4. `supervisor.tick()` 第 241 行 `monitor.tick()` 在结算之前执行，因此结算控制器永远轮不到——“屏幕上已经摆着一个待领取的奖励页”这件**不需要任何观测**就能做的事，被观测新鲜度挡在门外。
- **VERIFIED:** 实机跑 1m46s、10+ 个 tick，`game_actions_sent=0`、`dispatch_events` 为空、`fleet_runtime` 为空，无任何推进。
- **IMPACT:** 任何“进程重启时屏幕停在奖励页”的生产情形都会如此。这不是本轮新引入：`startup_reward_screen_detected` 的注释记录同一现象在 2026-11-05 更早已出现过（当时靠人工收尾）。
- **未修复（等待决策）。两条候选路径:**
  - ① 启动时若已证实奖励页，就在进入 tick 循环**之前**由结算控制器按屏幕证据结算（需先把舰队以 `RETURNED_PENDING_UI` 采纳；采纳依据可取启动时已用的 CDP 快照，它不依赖 witness）。改动局限在入口。
  - ② 调整 `supervisor.tick()` 顺序：当持久化 phase 明确要求结算（`RETURNED_PENDING_UI`）而观测不可信时，允许结算先行。改动触及 supervisor 语义，影响面更大。
- **LIMITS:** 本轮未验证「空区点 (600,400) 在奖励页是否也能推进」；`startup_reward_screen_detected` 之后 `enable_ui_refresh=false` 是否会连带禁用后续所有刷新（含返航后）尚未验证。
