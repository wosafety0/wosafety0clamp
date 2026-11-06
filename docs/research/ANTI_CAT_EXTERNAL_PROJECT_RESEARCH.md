# 防猫外部项目调研：检测、重试与恢复策略

- **状态：RESEARCHED（源码/文档级静态调研）**
- **实现状态：NOT_IMPLEMENTED**
- **验证状态：未在 KancolleBot 中验证**
- **日期：** 2026-11-06
- **关联设计：** [ANTI_CAT_RECOVERY_PLAN.md](./ANTI_CAT_RECOVERY_PLAN.md)
- **范围：** Poi、KC3Kai、kcauto-kai、kcauto_custom、KanColleViewer 与玩家故障排查资料。
- **注意：** 外部项目代码和文档代表其自身实现/说明，不代表适用于当前 Kancolle 服务端或 KancolleBot 的安全策略。

## 1. 调研结论

外部项目的“防猫”不是一个单一机制，而是至少三种不同能力：

1. **传输层重试**：请求失败或超时后，在有限次数内重试网络请求（Poi 文档描述的网络错误重试）。
2. **客户端错误检测**：从 API 响应码、浏览器/CEF 崩溃事件、网络异常和猫画面识别故障类型（KC3Kai、kcauto_custom）。
3. **自动化脚本恢复**：暂停当前自动化，尝试关闭阻塞弹窗、处理结果页、刷新游戏页面、重新进入游戏（kcauto-kai、kcauto_custom）。

KancolleBot 应采用“检测和安全恢复”架构，不直接照搬自动化脚本的激进 F5 重试流程。尤其不能把刷新后游戏画面消失、重新出现当作派遣/结算状态已恢复。

## 2. 项目证据

### 2.1 Poi：网络错误重试是独立于脚本恢复的功能

资料：
- 项目：poooi/poi
- 文档：[QuickStart Wiki](https://github.com/poooi/poi/wiki/QuickStart)
- 相关章节：网络设置
- 代码：[network settings UI](https://github.com/poooi/poi/blob/master/views/components/settings/network/index.tsx)
- 代码：[connection-test.tsx](https://github.com/poooi/poi/blob/master/views/components/settings/network/connection-test.tsx)

文档说明：
- Poi 网络设置包含“网络错误重试”选项；
- 该选项的描述是发包错误或超时时重新发包；
- 文档建议谨慎使用。

源码核查：
- 当前 Poi 网络设置 UI 提供 HTTP、Socks5、PAC、无代理选择；
- 连接测试使用 AbortController，测试超时为 15 秒，并在结束时 abort 请求；
- 连接测试仅检查一个外部 HTTPS endpoint 的连通性，不等同于游戏服务器可用性，也不证明游戏 API/登录状态正常。

重要限制：
- QuickStart 是较旧的 Wiki 页面；不能仅凭文档推断当前 Poi 版本的重试次数、重试间隔、幂等规则或所有请求类型的实际行为。
- 当前已检查的网络设置 UI 源码没有展示“网络错误重试”的内部实现。后续如要深入，需定位实际网络代理/请求层及配置读写处。
- KancolleBot 不应自行重放已发送但结果未知的游戏请求。尤其是派遣、结算、补给等可能改变游戏状态的操作，必须通过 Action Journal 和新鲜 Poi 状态对账。

### 2.2 kcauto_custom：最完整的分类恢复实现

项目：
- [XVs32/kcauto_custom](https://github.com/XVs32/kcauto_custom)
- 调研分支：`develop`
- 调研时仓库最近更新时间：2026-11-05
- 核心源码：
  - [kcauto/util/recovery.py](https://github.com/XVs32/kcauto_custom/blob/develop/kcauto/util/recovery.py)
  - [kcauto/startup/kcauto_wrapper.py](https://github.com/XVs32/kcauto_custom/blob/develop/kcauto/startup/kcauto_wrapper.py)
  - [kcauto/api/api_core.py](https://github.com/XVs32/kcauto_custom/blob/develop/kcauto/api/api_core.py)
  - [kcauto/util/exceptions.py](https://github.com/XVs32/kcauto_custom/blob/develop/kcauto/util/exceptions.py)
  - [kcauto/stats/recovery.py](https://github.com/XVs32/kcauto_custom/blob/develop/kcauto/stats/recovery.py)

源码确认的恢复分类顺序：
1. Basic recovery：清除可能阻塞浏览器交互的弹窗，并检查游戏页面是否可识别。
2. Results recovery：识别 next / next_alt 结果页，有限次数点击推进；检测回到母港后接收远征结果。
3. Catbomb recovery：识别猫画面，执行页面刷新与重新进入游戏。
4. Chrome crash recovery：识别 Chrome crash 图片，或从 DevTools 事件 `Inspector.targetCrashed` 识别目标崩溃。

调用关系：
- 主循环捕获 `FindFailed`、`ApiException`、`Catbomb201Exception`、`ChromeCrashException`；
- 不同异常走不同恢复分支；
- 恢复失败时停止自动化主循环，而不是无限执行后续任务。

Catbomb 重试细节：
- 普通猫画面：最多 3 次恢复循环；
- 每次先刷新，再尝试 `start_kancolle()`；
- 仍然看到猫画面时采用 16、64、256 秒递增等待；
- 若刷新后不再是猫画面但无法确认成功启动，则直接失败退出该恢复分支；
- 201 类错误单独处理：首次遇到先等待 300 秒，再从较保守的尝试计数开始；若此前已经出现过 201，或恢复后仍持续为 201，则停止自动化，不继续刷新重试。

API 错误分类：
- `api_result == 201` 被转换为 `Catbomb201Exception`；
- 其他非成功 `api_result` 被转换为一般 `ApiException`；
- `Inspector.targetCrashed` 单独转换为 Chrome crash 异常。

可借鉴：
- 区分一般 API 错误、201、目标崩溃、视觉识别失败和结果页卡住；
- 每类故障有独立策略；
- 限定重试次数；
- 201 特殊熔断；
- 恢复失败时停止自动化；
- 恢复统计分类型累计。

不可直接照搬：
- 其刷新路径包含 F5 / Poi refresh / CDP Page.reload，并在刷新后调用 start_kancolle；
- 其脚本会直接点击结果页 next 并接收远征；
- 这些行为没有 KancolleBot 当前所要求的派遣实例、Action Journal UNKNOWN 屏障、舰船集合唯一归属和新鲜 witness 全链路证明。
- KancolleBot 必须先暂停和对账；不能照搬“刷新后 start_kancolle 返回成功就恢复全部任务”。

### 2.3 kcauto-kai：视觉猫画面与有界指数退避

项目：
- [twisting2017/kcauto-kai](https://github.com/twisting2017/kcauto-kai)
- 分支：`master`
- 核心源码：[kcauto-kai.sikuli/recovery.py](https://github.com/twisting2017/kcauto-kai/blob/master/kcauto-kai.sikuli/recovery.py)
- 资产：`kcauto-kai.sikuli/catbomb.png`

源码行为：
- 在一般视觉定位异常（FindFailed）后进入基本恢复；
- 尝试 ESC、SPACE，检查游戏参考点；
- 若处于结果页，则有限循环点击 next，直到识别游戏参考点或失败；
- 识别 `catbomb.png` 后，最多进行 7 次恢复尝试；
- 每轮发送 F5、SPACE、TAB、SPACE 的按键序列；
- 失败等待时间为 `2^(attempt + 4)` 秒，即从 16 秒开始指数增长；
- 猫画面消失后，再等待并点击 game_start 模板；
- 最终失败则抛出异常。

可借鉴：
- 将猫画面作为明确视觉状态，而不是把任意 FindFailed 当作猫；
- 区分普通 UI 识别失败、结果页和猫画面；
- 重试次数和退避上限明确；
- 每次恢复有日志和成功计数。

风险/限制：
- 项目代码注释标注该恢复模块为 basic / WIP；
- 使用固定按键序列，依赖浏览器/Viewer 焦点和游戏窗口比例；
- 未证明刷新后游戏状态与刷新前自动化动作的对应关系；
- 对 KancolleBot 而言，固定按键刷新不能作为默认恢复方式。

### 2.4 KC3Kai：服务端通信错误的多次重连与 201 风险提示

项目：
- [KC3Kai/kc3-translations](https://github.com/KC3Kai/kc3-translations)
- 资料：[data/en/terms.json](https://github.com/KC3Kai/kc3-translations/blob/master/data/en/terms.json)

文案记录：
- `CatBombServerCommErrorMsg`：一次服务器通信失败被描述为 connectivity error，并说明会尝试 3 次重连；若失败才发生 catbomb。
- `CatBombHardAPIErrorMsg`：启动时 API 返回错误码可能与登录 API link 过期或维护有关。
- `CatBombWrongComputerClockMsg`：时钟不正确可能导致通信问题。
- `CatBombMissingServerTimeMsg`：无法获取服务器时间可能与通信或代理有关。
- `Bomb201Message`：201 可能与同一账号在另一个游戏客户端登录有关，也可能涉及宏检测；反复出现时应谨慎。

可借鉴：
- 在显示“猫”之前，先进行少量、有界的连接恢复尝试；
- 区分连接失败、硬 API 错误、时钟偏差、服务器时间缺失和 201；
- 为错误提供可解释诊断，而不是统一显示“网络异常”。

限制：
- 这是翻译资源中的用户提示文案，不是底层重连算法源码；
- “3 次重连”是该文案描述的行为，尚未从本轮 KC3Kai 核心运行代码独立验证；
- 201 原因文案使用可能性措辞，不能将 201 单独断言为宏检测或封禁证据。

### 2.5 Kancolle Wiki：错误原因排查，不是自动恢复算法

资料：
- [Troubleshooting](https://kancolle.fandom.com/wiki/Tutorial:Troubleshooting)
- [Third-party software](https://en.kancollewiki.net/Help:3rd_Party_Software)

排查资料列举的可能原因包括：
- 服务器维护或服务端负载；
- 本地网络不稳定；
- DMM 会话/API link 失效；
- 系统时间不准确；
- 浏览器缓存问题；
- 代理或连接链路异常；
- 201 等服务端返回错误。

资料还提醒服务器错误时不要频繁刷新，以免增加服务器负载。其部分内容较旧，错误码说明不能当作当前服务端协议保证。

对 KancolleBot 的价值：
- 把“本地网络故障”和“服务端维护/拒绝”分开；
- 记录时钟、代理链路、会话提示、错误码和时间；
- 无法判断时暂停，不通过频繁刷新试图消除服务端错误。

### 2.6 KanColleViewer（KCV）：当前证据边界

项目：
- [donaco/KanColleViewer](https://github.com/donaco/KanColleViewer)
- 其 README 说明基于 CefSharp.Wpf，并捕获游戏通信内容；不修改通信内容或向服务器发送信息。
- 本轮没有在 KCV 当前源码中确认一套可直接复用的猫画面自动恢复流程。
- 已有 KCV 的 AutoExpedition、AutoActionQueue、ActWaitSession 等研究仍适用于任务队列、等待与动作生命周期设计，但不应据此声称 KCV 有防猫实现。

结论：KCV 可作为通信观察、事件驱动和 UI 生命周期参考；当前不列为已确认的防猫算法来源。

## 3. 横向比较

| 项目 | 检测来源 | 恢复策略 | 主要可借鉴点 | 不宜照搬点 |
|---|---|---|---|---|
| 岛风GO | 代理转发链路连接失败 | 检测连接失败后自动尝试重新连接；网络/服务器恢复后继续 | 传输层连接恢复，不要求脚本刷新页面 | 非开源，内部实现与重试细节不可审计；不能处理服务端拒绝的非法操作 |
| Poi | 网络请求错误/超时 | 文档描述重发失败请求 | 传输层错误与页面恢复分离 | 未核实当前重试实现和幂等语义 |
| kcauto_custom | 图片、API result、DevTools crash | 分类处理、刷新、有限重试、失败停止 | 故障分类、201 特殊熔断、恢复统计 | 自动刷新和重新启动游戏后直接继续脚本 |
| kcauto-kai | 视觉模板、FindFailed | 按键刷新、指数退避、game_start | 明确猫模板、有限重试和日志 | 固定 F5/SPACE/TAB 序列、缺少状态账本对账 |
| KC3Kai | 通信错误、硬 API 错误、时钟/服务器时间、201 | 文案描述三次重连后再猫屏 | 错误原因细分、少量连接重试 | 文案不能代替核心算法源码 |
| KCV | 通信捕获、UI/任务机制 | 本轮未确认猫恢复机制 | 被动通信观察、事件驱动 | 不宣称其已具备防猫模块 |

## 4. 对 KancolleBot 的具体设计建议

### 4.1 检测层：多信号融合

不要只依赖猫画面模板。至少融合：
- 视觉帧：猫画面、游戏启动画面、错误提示、结果页、正常母港；
- 网络/observer：自然响应超时、连接关闭、协议错误、HTTP 状态、API result（仅记录已观察到的字段）；
- CDP：target crashed、websocket closed、target destroyed；
- Poi freshness：最近自然 port response 的时间、快照年龄、witness 与 Redux 状态一致性；
- 系统/会话诊断：本地时间偏差、代理连接测试结果（只诊断，不自动改代理）。

每个信号要保留来源、观察时间、置信度/判定规则。单个超时不能立即认定猫；服务端明确拒绝或 201 不应降级成一般网络超时。

### 4.2 策略层：错误分类后选择动作

推荐分类和策略：

| 故障 | 自动动作 | 是否允许自动刷新 |
|---|---|---|
| 单次自然响应超时 | 暂停危险操作，短时等待自然响应 | 否 |
| 短暂连接断开 | 有界重建只读 CDP 观察 | 否 |
| CDP target crashed | 观察连接失效，转入人工/受控客户端恢复 | 不默认 |
| 猫画面且确认普通连接错误 | 暂停、等待、有界重连；刷新需单独策略验收 | 不默认 |
| API result 201 | 立即熔断、保存证据、人工检查会话/服务端提示 | **禁止自动重试刷新** |
| 登录失效/维护提示 | 保持暂停，等待人工处理 | 否 |
| 结果页/结算页 | 交给现有 Settlement Drain 和 Witness | 不用通用恢复点击 |
| 状态未知或冲突 | MANUAL_REQUIRED | 否 |

### 4.3 安全恢复层：必须经过账本对账

任何页面恢复后：
1. 旧 generation 失效，迟到回调零点击；
2. observer/CDP 重新建立只读观察；
3. 捕获新的自然响应 witness；
4. 重新读取 Poi 舰队 mission tuple 和舰船集合；
5. 对账 Dispatch Registry / Action Journal / Settlement Drain；
6. 已确认派遣不得再次派遣；
7. 已返航待结算舰队进入现有 Drain，不得按空闲舰队处理；
8. 存在 UNKNOWN 或归属歧义时转人工；
9. 所有证据通过后才允许恢复 Supervisor。

### 4.4 运行参数建议（待测试，不是既定配置）

- 检测需使用连续帧/多信号确认，阈值通过真实样本校准；
- 网络等待采用有界退避并设置总时长上限；
- 一般连接恢复次数不超过少量固定次数，具体数值通过离线故障注入与受控验证确定；
- 201、登录失效、维护和未知服务端错误不进入普通重试队列；
- 所有重试必须记录 attempt number、等待时长、错误类别、结果和下一步决策；
- 熔断后不自动循环刷新。

## 5. 对现有防猫设计档案的更新建议

建议把外部经验映射到原计划：
- 阶段 A 加入多信号分类和误报验证；
- 阶段 B 采用 Poi/KC3Kai 的“先有限连接恢复，再报告猫”思想，但仅对被动连接/观察器恢复使用；
- 将 kcauto_custom 的 201 特殊熔断纳入显式状态；
- 将 kcauto-kai 的视觉模板和退避日志作为参考，不采用其固定按键脚本；
- 阶段 C 保持 KancolleBot 的 Action Journal / Dispatch Registry / Witness 对账要求；
- 阶段 D 才考虑受控的可见 UI 刷新，必须先验证恢复后任务状态不会重复执行。

## 6. 待进一步研究

1. Poi 当前版本“网络错误重试”选项的真实配置键、默认值、重试次数、请求范围及幂等行为。
2. KC3Kai 核心仓库中三次重连机制的实际代码与错误分类实现；当前本轮只核对翻译文案。
3. kcauto_custom 对 201 的上游触发条件及真实运行统计；区分 API response 201 与视觉猫画面。
4. 采集当前 Poi 12 / Electron 44 的真实猫画面、错误页、target crash 与网络断开样本，建立 KancolleBot 模板测试集。
5. 验证 CDP websocket 断连、target crashed、Poi 进程存活但 WebView 失效时各自的恢复边界。

## 7. 状态声明

- **RESEARCHED：** 已静态检查岛风GO官方用户手册/原理说明、Poi 文档/部分源码、kcauto-kai recovery 源码、kcauto_custom recovery/API/wrapper 源码、KC3Kai 翻译资源、KCV README 和故障排查资料。
- **IMPLEMENTED：** 无 KancolleBot 生产代码修改。
- **TESTED：** 无 KancolleBot 防猫测试。
- **LIVE_VERIFIED：** 无实机防猫验证。
- **限制：** 外部项目实现不保证与当前游戏版本兼容；部分资料较旧；KC3Kai 重连算法和 Poi 当前网络重试实现尚未源码确认。


### 2.7 岛风GO（Shimakaze GO）：网络层防猫，而非脚本层防猫

官方资料：
- [岛风GO 官方用户手册：关于防猫功能](https://daofeng.gowincg.com/Index/userManual)
- [岛风GO 官方原理说明](https://daofeng.gowincg.com/shimakazego/declaration)
- [岛风GO 官方网站](https://daofeng.gowincg.com/)

官方对能力边界的明确说明：
- 防猫功能针对网络引起的访问错误，以及游戏服务器本身的异常情况；
- 如果提交了游戏服务器不允许的非正常操作，仍然会猫；
- 对于非服务端拒绝的操作，即使网络断开或游戏服务器异常，操作可能只是暂时卡住；网络/服务器恢复后，游戏会自动继续；
- 官方原理说明将其描述为：检测到网络连接失败后，自动尝试重新连接。

架构判断：
- 岛风GO的核心价值是**网络传输/代理链路层的连接恢复**，而不是识别游戏猫画面后执行 F5、重新启动游戏或恢复自动化脚本。
- 它尝试让原有访问在网络恢复后继续，因此与 kcauto_custom / kcauto-kai 的视觉识别、页面刷新和脚本恢复属于不同层次。
- 这也解释了为什么“网络断开但操作没有被服务端拒绝”时，页面可能只是等待而不出现猫画面。

对 KancolleBot 的借鉴：
1. 优先区分传输链路失败与服务端明确拒绝；前者可以研究有界连接恢复，后者不能通过重复发送来规避。
2. 网络恢复后仍必须等待新的自然响应 witness，重新读取 Poi 状态并对账 Action Journal / Dispatch Registry。
3. 不能把“代理已重新连通”视为“派遣/补给/结算已成功”，也不能把客户端继续响应视为业务状态已对账。
4. 可把网络链路恢复作为防猫体系的底层能力，但应由 KancolleBot Supervisor 统一暂停危险动作并控制何时恢复调度。

重要限制与安全说明：
- 岛风GO不是开源项目；本轮依据官方用户手册和作者原理说明，未审计其客户端/服务端实现、连接探测方式、重试次数、退避策略、请求缓存或请求幂等行为。
- 官方对“请求未到达服务端所以不会增加服务端压力”的解释属于作者说明，不能视为对所有网络故障场景的独立证明。
- 官方也未承诺能够恢复服务端拒绝、登录会话失效或所有 API 错误。
- 该工具通过代理/中间服务器处理游戏流量，属于额外信任边界；使用前应自行评估服务端可信度、账号凭据和会话数据的暴露风险。
- 本节只记录技术能力，不构成对第三方工具安全性、合规性或当前兼容性的保证。

**结论：岛风GO确实具备官方所称的防猫能力，但其主要机制是网络连接失败后的自动重连/恢复访问。它不能消除服务端拒绝造成的猫，也不能替代 KancolleBot 的业务状态恢复与防重复操作门禁。**


## 8. KancolleBot 当前代码基线审计（2026-11-06）

- **审计基线：** `wosafety0/wosafety0clamp`，功能分支 `feat/production-settlement-resupply-wiring-20261105`，HEAD `a4b7f82ceb0aa4fd2d9259be467de2ae7572ab43`。
- **性质：** GitHub 源码静态审计；未修改生产代码，未启动 Poi/CDP，未执行游戏 UI 操作。
- **注意：** 此处只代表该功能分支的代码状态。其他开发分支可能尚未包含这些提交，不能把该分支的代码能力自动视为已合并到主开发分支。

### 8.1 已有能力（应复用）

| 模块 | 已确认实现 | 对防猫的价值 |
|---|---|---|
| `port_response_observer.py` + JS observer | 子进程连接 loopback CDP，等待自然发生的成功 `/kcsapi/api_port/port` 响应；识别 target discovery、CDP 连接错误/关闭、协议错误、命令失败和自然响应超时 | 已有被动 witness 来源与部分故障分类 |
| `background_port_snapshot_provider.py` | 后台线程持续调用 observer；支持取消；暴露最近状态和最新 witness | 可作为观察服务的现有生命周期基础 |
| `port_freshness.py` | 校验来源、HTTP 200、非缓存标记、接收/捕获时间、数据结构及 Poi 舰队/舰船字段一致性 | 恢复后不能拿旧快照冒充新状态 |
| `visible_ui_refresh.py` + `core/return_refresh.py` | 读取与刷新分离；刷新有 `refresh_id`、冷却、单次导航边界和 observation generation；可显式 invalidate 旧轮次 | 可复用旧轮次失效和迟到回调防点击机制 |
| `continuous_supervisor.py` | 监控/控制器异常会记录 `supervisor_paused`，包含失败阶段、舰队、错误类型和部分完成情况 | 已有安全暂停的日志基础 |
| `sink.py` | `run_started` 记录 `correlation_capable` 和关联来源 | 关联能力可审计，不需要防猫模块重新实现 |
| `dispatch_recovery.py` / `action_journal.py` / `dispatch_registry.py` | 持久化派遣意图与身份；结果未知时阻止再次派遣；恢复依赖新鲜 Poi 证据 | 可用于网络恢复后的业务对账 |
| `blocked_recovery.py` | 只恢复经过审查的结算侧阻塞原因；未知原因保持 BLOCKED | 可作为保守恢复模式的参考，不是通用网络解锁器 |

### 8.2 已确认缺口

1. **没有全局网络状态门禁。** `ContinuousExpeditionSupervisor.tick()` 先执行 monitor，再处理结算/补给/派遣；它没有读取 observer 的网络健康状态，也没有统一的 `WAIT_NETWORK` / `RESUME_ALLOWED` 状态。
2. **网络不通时可能先触发可见导航。** 当 `enable_ui_refresh=True`、Poi observation 不可信且存在待处理舰队工作时，`ContinuousExpeditionMonitor._request_refresh()` 会调用 `FreshnessRefreshingStateReader.ensure_fresh()`。该路径会执行母港→出击→远征列表→母港导航，再等待自然 witness；它没有先验证网络链路已经恢复。因此防猫门禁应放在这条导航路径之前，而不能只在派遣按钮之前。
3. **后台 observer 的快速失败重试没有退避策略。** `BackgroundPortSnapshotProvider` 在每次 capture 返回后固定等待 0.25 秒，再开始下一轮。正常自然响应等待可持续 120 秒，但 target discovery/CDP 连接等快速失败会以固定短间隔重试；没有失败分类对应的指数退避、最大连续失败阈值或熔断状态。
4. **observer 的诊断细节没有完整上送。** JS observer 会统计 `Network.loadingFailed` 数量，但这是聚合计数，未按目标游戏请求路径关联错误；Python wrapper 有 `last_detail`，provider 当前只保存 `last_status`，没有把 detail 暴露给恢复协调器。仅凭一次 `natural_response_timeout` 不能断言游戏网络已断开。
5. **独立的 Python 被动观察 facade 尚未接入当前 runtime。** `CdpPassiveNetworkObserver` 已实现精确路径过滤和有界事件收集，但 `build_runtime()` 实际创建的是 `PassivePortResponseObserver`（Node 子进程包装）；不要把 facade 的实现状态误当成生产运行时已使用它。
6. **现有 generation 不是全局网络恢复 generation。** `ReturnRefreshCoordinator` / `FreshnessRefreshingStateReader` 的 generation 保护刷新观察轮次，能拒绝过期轮次的导航；它没有与 CDP 断连、网络恢复、Supervisor 调度许可建立统一生命周期。
7. **CDP 传输本身不自动重连。** `CdpWebSocketTransport` 在 reader/命令失败后将连接标记为不可用；上层可以重新发现 target 并建立新 transport，但没有统一的退避、连接恢复和旧回调失效协调器。

### 8.3 第一版实现边界

第一版不实现岛风GO式的游戏请求缓存、重放或代理转发，也不自动修改系统代理。KancolleBot 不得重新发送结果未知的游戏请求。可在项目内部实现的是**安全恢复协调**：

1. 将 observer 状态与可见网络错误信号归一化为故障类别；单次无自然响应超时只能是 `SUSPECT/UNKNOWN`，不能直接认定网络断开。
2. 进入 `PAUSED` 时先禁止所有后续 UI 操作（派遣、结算、补给和可见刷新），并 invalidate 当前 observation generation。
3. `WAIT_NETWORK` 阶段只做被动观察与 CDP target/transport 重建；对快速连接失败使用有界指数退避；不刷新页面、不点击、不发送游戏 API 请求。
4. 只有捕获到暂停边界之后的新鲜自然 port witness，且与当前 Poi Redux 状态匹配，才进入业务对账。
5. 对账 Action Journal、Dispatch Registry、派遣意图、舰队 mission tuple 和待结算/补给状态。未知、冲突、缺少 witness 或归属不唯一时进入 `MANUAL_REQUIRED`。
6. 只有业务状态完整对账后才解除 Supervisor 暂停；`NETWORK_RECOVERED` 本身不能授权继续调度。

### 8.4 推荐实施顺序与验收

- **P1：只读网络故障分类器。** 输入 observer 状态、CDP 连接状态和经核验的视觉错误类别；输出故障类别与证据，不执行动作。离线测试误报、单信号不足、快速失败和 201 熔断。
- **P2：恢复协调器与全局暂停门禁。** 先把门禁接到 Supervisor tick 入口及所有 UI 操作入口，确保网络故障时 monitor 不会先触发可见导航。离线故障注入验证零点击、generation 失效和有界退避。
- **P3：只读恢复与业务对账。** 重新建立 observer/CDP，等待新 witness，重新读取 Poi 并对账现有账本；UNKNOWN/冲突保持人工处理。
- **P4：受控实机验证。** 先只读观察网络中断/恢复，不主动断网、不切换代理、不点击；取得真实样本后再单独评估是否需要任何可见 UI 恢复动作。

建议第一项生产代码改动之前，先补齐 P1 的离线测试矩阵和故障状态契约；不要一开始就修改 `BackgroundPortSnapshotProvider` 的重试参数，因为自然 port 响应可能长时间没有发生，不能把“没有请求”误判成“连接坏了”。

### 8.5 审计状态

- **RESEARCHED：** 已读取上述模块及其 runtime wiring，确认被动 witness、刷新 generation、Supervisor 失败日志和持久化派遣对账的当前实现边界。
- **IMPLEMENTED：** 本节没有新增 KancolleBot 生产代码。
- **TESTED：** 未运行测试；本节结论来自静态源码检查，不冒充离线行为验证。
- **LIVE_VERIFIED：** 本轮没有任何防猫实机验证。
- **关键结论：** KancolleBot 已有大部分安全恢复所需的基础积木；当前最关键缺口不是猫画面模板，而是**在任何可见刷新/业务 UI 操作之前建立全局网络故障门禁，并把 observer 恢复与账本对账串成一个受控状态机**。
