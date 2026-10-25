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


### 2026-10-25 — Verified uploaded Nanodesu source archive

- User provided the uploaded source path on branch `dev/sparkling-planner-mvp`: `research/poi-plugin-nanodesu-mcp/src`.
- Remote directory read succeeded. All four selected files are present and readable on GitHub; reported byte sizes match the user's local export report:
  - `paths.ts`: 14,391 bytes; Git blob `4da5270a4fd15c6f6f6416ddf30b3ab1343cf369`.
  - `query.ts`: 16,464 bytes; Git blob `7db96bb38fb4a40b1d6fc9eb6ffce1ede79cebed`.
  - `serialize.ts`: 4,096 bytes; Git blob `25e4e4150284e8d4d97c4daa2743102ca9ffbcd7`.
  - `server.ts`: 21,564 bytes; Git blob `1cf0360ad6fbbcb36db21b16a9339a5d46c2a929`.
- Source-of-truth rule: use these uploaded local-version files as authoritative for future analysis. Public upstream is historical/context only.
- Scope note: the uploaded set contains four selected files only; it does not include `hensei.ts`, `tools.ts`, or `poi.ts` from the broader seven-file static review. Do not assume those files are archived in this path.
- Verification was remote file/directory read plus byte-size agreement against the prior local export report. Git blob SHAs are recorded; SHA-256 was not recalculated from downloaded raw bytes in this check, so do not describe this as a full SHA-256 equality check.
- Safety: no plugin execution, MCP/network/WebSocket dispatch, game action, local file modification, or system/proxy change was performed.
- Next: analyze the uploaded files directly from this branch, starting with the read-only query/path boundaries and serialization limits; record findings and evidence before proposing any runtime test.

### 2026-10-25 — Static review of uploaded Nanodesu source (read-only)

Source of truth: the four uploaded files under `research/poi-plugin-nanodesu-mcp/src` on `dev/sparkling-planner-mvp`. This review reads the uploaded branch only; public upstream is not used to fill gaps.

#### Confirmed design boundaries

- `paths.ts` lines 19–31 defines an explicit allowlist of store roots and denies `layout`, `plugins`, `ext` as a whole, `config`, and `ui` (lines 36–42). Plugin state is allowlisted by package name; battle-detail is narrowed to `_.indexes` (lines 48–103).
- `resolveStorePath` (lines 258 onward) enforces the root allowlist and plugin subpath restrictions before walking a requested store path. Wildcard `[]` is rejected for root paths and reserved for projections.
- `query.ts` implements a small lexer/parser/evaluator rather than evaluating arbitrary JavaScript (lexer lines 97 onward, parser from line 240, comparison/evaluation around lines 408–482). Ordering comparisons only work for matching number/number or string/string types; missing fields compare false. Projection is explicit through `select`.
- `serialize.ts` sets a default depth cap of 16 (line 5), drops functions/symbols/undefined object fields, converts BigInt to strings, non-finite numbers to null, and marks cycles/depth overflow (lines 15–65). `fitToBytes` enforces a serialized-size cap by dropping collection elements from the end; a too-large scalar/object returns an error (lines 78 onward).
- `server.ts` defaults to loopback `127.0.0.1` (line 397) and caps request bodies at 1 MiB (line 39 and `readBody` around line 390). It registers four read-oriented tools in the visible builder (`poi_get`, `poi_lookup`, `poi_battle`, `poi_describe`). The only visible write tool, `poi_hensei_save`, is conditionally registered only when both `dispatch` and `hensei` are supplied (around lines 295–365); this does not establish whether the plugin entry point supplies them.

#### Hardening observations (not confirmed exploits)

- `paths.ts` uses JavaScript's `in` operator for presence checks (e.g. line 359), and `getByPath` directly indexes objects (lines 226–255). This includes inherited property names such as `constructor` / `__proto__`; consider rejecting prototype-related path segments and requiring own-property checks. Static review alone does not demonstrate a practical data-exposure or code-execution exploit.
- `server.ts` has no CORS headers and enables DNS-rebinding protection with a loopback allowed-host list (around lines 425–440). The default loopback bind is a meaningful boundary, but CORS alone is not authentication; other local processes can still attempt requests to a loopback service. Confirm the real bind address and client trust assumptions from the missing entry-point/configuration code before judging exposure.
- The HTTP error path returns an exception stack in the response body (around lines 466–476). That may disclose local implementation paths to a caller able to trigger a server error; prefer a generic external error with detailed logging kept local.
- The request body size limit is present, but the oversize branch destroys the request after rejecting (around lines 390–404); error-response behavior under that condition has not been tested.
- Serialization depth/byte caps reduce response size, but this static pass did not execute adversarial cyclic/proxy/getter inputs or measure CPU/memory behavior. Treat them as untested edge cases, not defects established by testing.

#### Coverage gaps / next steps

- The uploaded directory contains only four selected files. `server.ts` imports `battles.ts`, `hensei.ts`, `request-log.ts`, `tools.ts`, and `validator.ts`, and the entry point that constructs `ServerOptions` is not in this uploaded set. Consequently, tool handler validation, actual size-limit constants, live store wiring, and whether write-capable options are passed remain unverified.
- Next safe step: upload/locate only the missing project-owned integration files needed to close these static gaps, then inspect the entry point and handler implementations. Do not start the plugin, send MCP requests, dispatch actions, or perform game actions as part of this review.
- No source execution, network/WebSocket dispatch, game action, local file modification, or system/proxy change was performed.

### 2026-10-25 — Second-pass boundary review and prioritized follow-up

This pass inspected the full relevant sections of the four uploaded files on the authoritative branch. No code was executed and no runtime requests were sent.

#### Additional verified observations

- `query.ts` uses recursive-descent parsing with precedence `not` > `and` > `or`; evaluation short-circuits `and`/`or`. Unknown fields yield `undefined`, and comparisons involving `undefined` return false. This is safer than dynamic expression evaluation, but no explicit query-length, token-count, or AST-depth cap is visible in the reviewed file.
- `applySelect` resolves requested fields with `getByPath` and does not apply the store-root allowlist to each selected field. This is expected for projection within an already-resolved record, but means prototype-related property names should be explicitly considered at the field-path layer too.
- `resolveStorePath` uses `plugin in ALLOWED_EXT_PLUGINS`, and several store-presence checks use `in`; inherited property names can therefore pass presence/membership tests on ordinary JavaScript objects. No practical exploit was demonstrated, and JSON-safe conversion may remove function-valued results. Treat this as a hardening defect candidate, not a confirmed exploit.
- `poi_get` schema requires a positive integer `limit`; `maxBytes` is positive and documents a maximum. `poi_lookup` and `poi_battle` schemas describe capped IDs, but their actual handler enforcement lives in missing `tools.ts` and cannot be verified from the uploaded set.
- `server.ts` binds to `127.0.0.1` by default, permits a configurable host, accepts only POST `/mcp` (plus GET `/health`), uses DNS-rebinding protection with an allowed-host list, and creates a fresh stateless MCP server/transport per request. These controls reduce exposure but are not authentication and do not prove what host the real plugin entry point configures.
- The request handler writes an exception stack to the HTTP 500 response as well as logging it. This is a concrete information-disclosure hardening concern for any caller that can reach the listener; replacing the response body with a generic error is recommended.
- The oversized-body path rejects and destroys the request. Since no runtime test was performed, whether clients receive a clean error response remains unknown.

#### Priority order for future review

1. **P1 — Verify actual startup wiring:** obtain the project-owned plugin entry point and confirm configured host/port, `getStore`, `dispatch`, and `hensei`. This decides whether the write tool is registered and whether the listener is strictly loopback.
2. **P1 — Review write-handler safeguards before any use:** obtain `tools.ts` and `hensei.ts`; inspect title collision/overwrite handling, deck validation, import parsing, and action construction statically. Never call the save handler during this review.
3. **P2 — Harden property-path traversal:** consider rejecting `__proto__`, `prototype`, and `constructor` segments where not needed; use own-property checks for allowlist and object-key membership.
4. **P2 — Reduce externally visible diagnostics:** return a generic HTTP 500 body and retain detailed stack traces only in local logs.
5. **P3 — Add offline tests when the complete source is available:** path parser edge cases, inherited properties, query length/depth, cycles and getters, byte caps, oversized HTTP bodies, and tool-ID cap enforcement. Tests should use synthetic objects and must not connect to POI or dispatch actions.

#### Current coverage status

The uploaded source path contains only `paths.ts`, `query.ts`, `serialize.ts`, and `server.ts`. Attempts to fetch `tools.ts`, `hensei.ts`, `battles.ts`, `request-log.ts`, `validator.ts`, `index.ts`, and `package.json` from this path returned 404; this establishes only that those paths are absent from this uploaded directory, not that the files do not exist locally or elsewhere in the repository.

No plugin execution, MCP request, network/WebSocket dispatch, game action, local file modification, or system/proxy change was performed.


### 2026-10-25 — Repository coverage and Python client static review

#### Repository coverage confirmed

- Read the full recursive Git tree for `dev/sparkling-planner-mvp` at commit `8abc450e10a0cecc9d5c43b14fdb0e666951f634`.
- The uploaded Nanodesu directory contains exactly the four selected files (`paths.ts`, `query.ts`, `serialize.ts`, `server.ts`); no other files are present beneath that directory in this branch. This is a branch/path fact only, not a claim about the user's local installation or other branches.
- The repo does contain the separate integration document, Python adapter, and offline client tests. Reviewed:
  - `docs/research/POI_NANODESU_MCP_INTEGRATION.md`
  - `src/kancollebot/adapters/poi/mcp_client.py`
  - `tests/test_poi_mcp_client.py`

#### Python MCP client observations

- Positive controls: standard-library-only transport; rejects non-loopback endpoint hosts by default; has connection timeout and response-size caps; negotiates MCP before tool calls; allowlists only `poi_describe`, `poi_get`, and `poi_lookup`; rejects tool-level `isError`; offline tests cover endpoint rejection, action-tool denial, initialization notification, and tool errors.
- Hardening candidate: `urllib.request.urlopen` uses the default redirect handling. The code validates the initial hostname but does not explicitly revalidate redirect destinations. Consider disabling redirects or validating every redirect target as loopback before following it.
- Hardening candidate: the constructor validates scheme/host/path and loopback status, but does not constrain the port to the expected bridge port or reject URL user-info/query/fragment components. The default endpoint is safe by construction; stricter endpoint validation would reduce configuration mistakes.
- Coverage gap: the checked-in tests are focused unit tests and mock the transport; they do not prove interoperability against the actual uploaded plugin version. No runtime network call was made in this review.
- The Python client allowlist does not include `poi_battle`, even though the inspected server registers that read-oriented tool. This is conservative, not a defect; only expand the allowlist if the adapter actually needs it and the handler contract is reviewed.
- The integration document correctly leaves Poi 12.0.1 compatibility, actual store freshness/session evidence, and end-to-end operation pending. No claim of runtime compatibility is made here.

#### Next step

- The repository-side source search is exhausted for this branch: the missing plugin entry point and handler files are not present in the uploaded source directory. To continue the plugin-side static review, add only the selected files needed to verify startup wiring and handlers (`index.ts`/entry point, `tools.ts`, `hensei.ts`, plus any directly imported validator/handler file). Until then, treat write-tool registration and handler safeguards as unknown.
- No source execution, plugin startup, MCP request, game action, local file modification, or system/proxy change was performed.
