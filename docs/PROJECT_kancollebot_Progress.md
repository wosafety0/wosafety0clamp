# KancolleBot 开发进度

## 2026-10-21

### 已完成

- 核实仓库 `wosafety0/KancolleBot` 为私有仓库，默认分支为 `main`。
- 建立 README 初始提交，并创建独立开发分支 `dev/bootstrap-foundation`。
- 添加 Python 包基础结构、舰船/舰队数据模型、安全策略、只读 POI 适配器契约、单元测试和 GitHub Actions CI 工作流。
- 初始实现不建立网络连接，不执行真实游戏操作。
- 创建研究分支 `dev/kcv-research-python-direction`，正式调整为 Python 实现、优先研究 KCV 相关自动化逻辑，KCA 作为辅助对照，POI 作为本地宿主/状态接入目标。

### 验证状态

- 仓库写入：已执行。
- CI：需以实际工作流结果为准；不能把仓库结构存在当作 CI 通过。
- POI 接口：尚未验证；当前仅有协议定义。
- KCV 相关自动化逻辑：正在建立源码证据；必须区分官方 KanColleViewer 主程序和本地 Assistant/自动化相关源码。
- KCA 行为研究：待按模块开展对照。
- 远征自动化：未实现。
- 战斗自动化：未实现。

### 下一步

1. 研究 KCV 官方仓库中的舰队状态、远征计时、状态更新和事件通知模型。
2. 识别本地 `Grabacr07.KanColleViewer.Models.Assistant` 等 Assistant 相关源码所属项目，并追踪自动化入口、决策和执行调用链。
3. 只读研究 POI 插件/数据接口和本地调试边界。
4. 对照 KCA 的远征和战斗流程，明确可迁移的行为设计。
5. 先实现纯数据解析、状态机和模拟测试，不连接真实游戏操作。
