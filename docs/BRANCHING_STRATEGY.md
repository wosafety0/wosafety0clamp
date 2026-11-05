# 分支治理策略（Branching Strategy）

> 状态：DRAFT（本轮治理任务产出，待评审）
> 实测基准日：2026-11-05
> 适用仓库：`wosafety0/wosafety0clamp`
> 权威规则来源：`wosafety0/Agents-global` 的 `main/AGENTS.md`（v2.1.0-rc1，§8.6 远程归档、§10.2 远端授权）

本文档只规定**分支与合并治理**，不重复产品架构（见 `PRODUCT_ARCHITECTURE.md`）与开发路线（见 `DEVELOPMENT_ROADMAP.md`）。

---

## 1. 目标

- 一个稳定 `main`：只接收经过验收的成果。
- 一条长期集成主线：汇聚各功能分支的已核验成果。
- 功能 / 修复 / 文档 / 研究分支职责清晰、生命周期可控。
- 所有远程写操作（推送、PR、合并）遵守 AGENTS §10.2 与 §8.6 的授权与归档门禁。

---

## 2. 当前仓库分支现状（实测，非推测）

### 2.1 `main`
- SHA：`a4f377764d94c8f0960c931bf94c27bb7e9b5a8e`（自基线以来未变）。
- 远端已合并 PR 仅 **1 个**：#9（CI 工作流 `verify-global-rules.yml`）。
- `main` 上**没有 `docs/` 目录**，也没有任何业务代码——目前基本是 bootstrap 状态。
- **分支保护：无**（`branches/main/protection` 返回 404）。**Rulesets：空 `[]`**。

### 2.2 扁平 `dev` 分支不可创建（重要约束）
- 仓库已存在 **50+ 个 `dev/<feature>` 分支**。Git 引用规则禁止 `refs/heads/dev` 与 `refs/heads/dev/...` 共存（目录/文件 D/F 冲突）。
- 因此**无法创建名为 `dev` 的扁平长期集成分支**。这是结构性约束，不是权限问题。
- **替代方案（本轮采用）**：以事实上的集成主干 `dev/dispatch-observer-safety-current-mvp-20261103`（= 共同开发基线 `0b18900`）作为长期集成主线。后续是否迁移到扁平 `dev` 需要先把 `dev/*` 分支重命名/整合，**属高风险操作，需用户明确授权**（见第 9 节）。

### 2.3 现有分支前缀（实测计数）
- `dev/*`：约 50 个（功能集成 / 安全加固 / 扫描会话等短期分支）。
- `feat/*`：含 `feat/closed-loop-settlement-20261104`、`feat/expedition-min-ships-20261105`、`feat/renderer-block-detection-20261103`、`feat/harness-trial-gate*`。
- `fix/*`、`chore/*`、`harness/*`、`research/*`、`test/*`：少量。
- `docs/decision-contract-v1`：决策契约分支（见第 7 节）。

### 2.4 PR 现状（实测 35 个）
- **27 open / 7 closed / 1 merged**。
- 多数 open PR 以 `dev/expedition-closed-loop-mvp` 或 `dev/dispatch-observer-safety-current-mvp-20261103` 为 base。
- 既有 M1 集成 PR：**#35**（`dev/expedition-min-ships-hardening-20261105` → `dev/dispatch-observer-safety-current-mvp-20261103`，open）。
- 本轮新增契约集成 PR：**#36**（`docs/decision-contract-v1` → `dev/dispatch-observer-safety-current-mvp-20261103`，open，纯文档+测试）。

---

## 3. 目标分支模型

| 分支 | 职责 | 生命周期 |
|---|---|---|
| `main` | 稳定版本，只接收经过验收的成果（含 CI 绿、实机或接近实机验证） | 永久 |
| `dev/dispatch-observer-safety-current-mvp-20261103`（当前集成主干，= `0b18900`） | 长期集成主线，汇聚各 `feat/*`/`fix/*`/`docs/*` 的已核验成果 | 长期（待命名迁移讨论） |
| `feat/*` | 独立功能开发（远征、战斗、刷闪等模块功能） | 合并后保留一段时间再清理 |
| `fix/*` | 缺陷修复 | 合并后即清理 |
| `docs/*` | 独立文档 / 契约工作（如本分支、契约分支） | 合并后即清理 |
| `chore/*`、`research/*`、`test/*`、`harness/*` | 维护、研究、测试、工具链 | 合并或废弃后清理 |

> 不为远征、战斗、刷闪分别创建长期公共平台分支；它们作为模块在 `src/kancollebot/` 内演进，统一汇入集成主干。

---

## 4. 命名规范
- 功能：`feat/<short-description>-<YYYYMMDD>`（例：`feat/expedition-min-ships-20261105`）。
- 修复：`fix/<short-description>-<YYYYMMDD>`。
- 文档/契约：`docs/<short-description>-<YYYYMMDD>`。
- 集成/加固：`dev/<short-description>-<YYYYMMDD>`（沿用现有约定）。
- 研究/工具：`research/*`、`test/*`、`harness/*`、`chore/*`。

---

## 5. PR 规则
1. 任何 `feat/*`/`fix/*`/`docs/*` 都**必须**通过 PR 合入集成主干，禁止直接 push 到主干或 `main`。
2. base 选择：功能/修复 → 集成主干（`dev/dispatch-observer-safety-current-mvp-20261103`）；若目标是生产验收里程碑 → `main`（需额外验收证据）。
3. PR 标题遵循 `<type>(<scope>): <summary>`。
4. 合并前必须：本地/CI 测试通过；`merge-tree` 冲突检查（不同文件域的 PR 应无冲突）；变更范围与描述一致。
5. 因 `main` 与集成主干当前**均无分支保护**，合并不强制 review；但**强烈建议**关键 PR 在合并前由人工评审（见第 8 节治理建议）。

---

## 6. 合并方式
- 默认 **merge commit**（保留特性分支历史，便于追溯与回滚），不强制 squash。
- 合并动作需明确授权；合并后必须跑对应测试与 CI，并在进度文档记录合并提交 SHA。

---

## 7. 临时分支生命周期与清理规则（本轮已评估）

清理前必须全部满足：① 有效提交已集成或明确保留；② 无独有未集成提交；③ 无未完成 PR；④ 无 CI/工作流依赖；⑤ 无协作者仍在使用；⑥ 符合保护/授权规则。不满足则**保留并记录原因**。

| 分支 | 独有提交 | 未完成 PR | 评估结论 | 原因 |
|---|---|---|---|---|
| `feat/closed-loop-settlement-20261104` | 无（= `0b18900`，与集成主干同源） | 无 | **保留** | 是共同开发基线的可读引用，删除价值低且可能干扰协作者定位基线；非强制。 |
| `docs/decision-contract-v1` | 有（契约文档+测试，5bd0060） | 已由 #36 集成到主干（open） | **保留至 #36 合并** | 集成 PR 尚未合并；合并完成且确认无回归后再考虑删除源分支。 |
| `feat/expedition-min-ships-20261105` | 有（M1 + 用户硬化提交，5290f51） | M1 已由 #35 集成（open） | **保留** | M1 集成 PR #35 仍 open；且远端该分支有用户新增的硬化提交，删除会丢失可追溯来源；**禁止强推/重写**。 |

> 本轮**未删除任何分支**。删除操作被推迟到对应 PR 合并且明确无依赖之后，且需用户确认（删除属不可逆、高影响操作，遵循 AGENTS §2.2 / §10.2）。

---

## 8. 治理建议（非阻塞）
- **为 `main` 与集成主干添加分支保护 / Ruleset**：当前两者均无保护，存在误推风险。建议至少对 `main` 启用保护并要求在合并前通过 CI。
- **统一集成主干命名**：当前 `dev/*` 约定导致扁平 `dev` 无法创建。建议用户决策：要么正式采纳 `dev/<feature>` + 指定 trunk 的模型，要么规划一次性迁移（重命名现有 `dev/*` 为 `feature/*` 或类似，再建扁平 `dev`）。该迁移为高风险，需单独授权。

---

## 9. 待用户决策项
1. 是否接受以 `dev/dispatch-observer-safety-current-mvp-20261103` 作为长期集成主干（替代不可创建的扁平 `dev`）？
2. 是否授权为 `main` / 集成主干添加分支保护？
3. 是否授权未来的 `dev/*` → 扁平 `dev` 命名迁移（高风险）？

---

## 10. GitHub 远程归档要求（依据 AGENTS §8.6）
- 本地保存、提交、推送、远端回读是**四种不同状态**，分别核验，不得混报。
- 归档只保存已核验成功成果与有价值的正向进展；未验证产物须明确标注状态。
- 推送后必须从 GitHub 权威远程重读，核验目标分支 SHA 与文件内容，再报告“已归档”。
- 远程归档失败或无法回读时，**暂停**受归档门禁约束的后续开发，不得声称归档成功。
- 所有远程写操作遵守 §10.2，归档要求**不**等同于绕过权限控制。
