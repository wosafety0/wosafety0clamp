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

## 2026-10-24 — POI MCP HTTP bridge source findings

- Read-only inspection of the installed `poi-plugin-mcp` v0.2.29 identified the default bridge port `17777` in `lib/settings.js`.
- The generated DevTools injection script in `mcp-server.js` defines `/fleets` and `/ships` handlers that serialize POI store values `st.info.fleets` and `st.info.ships`. The plugin entry `index.js` wires telemetry and a bridge controller; the active controller/bridge path still needs tracing to distinguish it from the standalone injection path.
- Source scan evidence shows ship instance IDs (`api_id`) and master ship IDs (`api_ship_id`) are distinct. Fleet `api_ship` references should be resolved against ship instance IDs.
- No explicit `flagship` keyword was found in the scanned plugin source. `api_flagship` semantics and any replacement flagship-selection rule remain unverified; do not assume the first `api_ship` slot is flagship.
- Detailed evidence, source locations, limitations, and next read-only checks: [POI MCP HTTP Bridge Findings](research/POI_MCP_HTTP_BRIDGE_FINDINGS.md).
- Safety status of the local scan report: read-only; no network/HTTP requests, game actions, process termination, or plugin file modifications.

## 2026-10-24 — POI active bridge call-chain correlation

- Targeted local source output confirms `lib/bridge-controller.js` imports `createPoiDataBridge`, passes the configured port into its factory, and `ensureStarted()` calls `bridge.start()`.
- `lib/poi-http-bridge.js` creates an HTTP server, binds to `127.0.0.1` using the configured port, and serves `/fleets` from `info.fleets` and `/ships` from `info.ships`.
- The local listener snapshot showed POI renderer PID `21168` listening on both `127.0.0.1:17777` and `127.0.0.1:12450`. This strongly correlates the `17777` listener with the plugin bridge implementation but does not prove the complete lifecycle call chain end-to-end; the owner of port `12450` remains unexplained.
- The targeted check was read-only, reported zero errors, and performed no HTTP/network requests, game actions, process termination, or file modifications. Clipboard verification was not part of this shortened check.
- Detailed source excerpts and evidence limitations: [POI MCP HTTP Bridge Findings](research/POI_MCP_HTTP_BRIDGE_FINDINGS.md).

## 2026-10-24 — POI plugin lifecycle source chain completed

- The latest local read-only source excerpts confirm `index.js` calls `controller.load()` from `pluginDidLoad()`; `bridge-controller.js` reloads settings and, when `settings.enabled` is true, calls `ensureStarted()`; `ensureStarted()` creates the bridge if necessary and awaits `bridge.start()`.
- The bridge factory receives the configured port and its implementation creates a Node HTTP server bound to `127.0.0.1`; its routes serve `/fleets` from `info.fleets` and `/ships` from `info.ships`.
- Together with the previously supplied listener snapshot (POI renderer PID `21168` listening on `127.0.0.1:17777`), the source call chain strongly supports attribution of the `17777` service to `poi-plugin-mcp`. No live HTTP request was made in this verification.
- Port `12450` remains unexplained; PID ownership alone does not identify which component created that listener.
- The local check reported zero errors and no HTTP/network requests, game actions, or file modifications. Clipboard verification was intentionally not performed.
- Details and caveats: [POI MCP HTTP Bridge Findings](research/POI_MCP_HTTP_BRIDGE_FINDINGS.md).

