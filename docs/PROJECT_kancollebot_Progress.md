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
- POI 接口：已在用户本地只读检查中确认 `http://127.0.0.1:17777/fleets` 和 `/ships` 返回 HTTP 200；`/fleets` 为顶层数组、`/ships` 为按舰船 ID 索引的对象，90 条记录。该证据来自用户提供的本地诊断结果，不等于云端 CI 访问了用户的 POI。
- KCV 自动化调用链：待逐项追踪并提供源码证据。
- KCA 行为研究：待按模块开展对照。
- 自动远征：决策规划仅为离线原型，尚未实现操作闭环。
- 自动战斗：未实现。
- 真实游戏操作：仍默认关闭；新鲜度检查只是基础保护层，不代表可以执行游戏操作。

### 2026-10-22：本地产品文件整合

- 新增 `src/kancollebot/core/fleet_parser.py`，解析舰队观察数据并保留原始编成位置。
- 新增 `src/kancollebot/core/expedition_planner.py`，生成单个收取/派遣/等待建议，不执行游戏操作。
- 新增 `tests/test_expedition_planning.py` 与整合记录；对应源码/测试提交的 CI 编译与单元测试通过。
- 上传的 `state_parser.py` 含说明文字与代码混合，不可直接导入；上传的 `scheduler.py` 使用尚未验证的 CDP/WebSocket 路径，因此未接入运行时；`guard.py` 的安全概念仅作参考，未替换现有默认关闭的 `SafetyPolicy`。

### 下一步

1. 在独立开发分支完善 loopback-only、GET-only 的 POI 只读 HTTP 适配器，并使用模拟响应做离线测试。
2. 真实 POI 状态读取只在用户本机验证；云端 CI 不得尝试连接用户的本地服务。
3. 在拿到舰船记录字段证据后，再实现 HP/补给/入渠状态解析和派遣前置条件。
4. 追踪 KCV（非提助手）已有研究材料的自动化调用链，缺失证据时再对照 KCA。
5. 真实游戏操作保持关闭，直到前置条件、失败恢复和停止机制都经过独立验证。
