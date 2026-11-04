# 提案：README / HANDOFF 基线文档状态最小修订（未经确认，未执行）

**状态：** 提案文档，**未修改** `README.md` / `docs/HANDOFF.md`。
**基线：** `3e2e2ba`
**关联：** P0 文档状态核对

---

## 1. 当前远端事实（本轮 `git ls-remote` 实读）

```
3e2e2ba5d647fdf885041747c28a416f24645955  refs/heads/dev/dispatch-observer-safety-current-mvp-20261103
3e2e2ba5d647fdf885041747c28a416f24645955  refs/heads/feat/closed-loop-settlement-20261104
a4f377764d94c8f0960c931bf94c27bb7e9b5a8e  refs/heads/main
```

`dev` 与 `feat` 远端已同步到 `3e2e2ba`；`main` 未变，仍只含 `.github`。
两次授权推送（`20f78ec`、`3e2e2ba`）及 CI 核验均已归档在
`docs/PROJECT_kancollebot_Progress.md`。

## 2. 冲突性质判定：历史事件快照，不是错误

`README.md` 第 7–28 行、`docs/HANDOFF.md` 第 8–28 / 36 / 151 行描述的是
**2026-11-04 整合事件当时的状态**：那次整合确实是 local-only，`3bd949b`
当时确实未推送，远端 dev 当时确实为 `86f294c`、feat 为 `caa3e5a`。

它们现在与远端事实冲突，是因为**后来用户单独授权了两次推送**，而不是当时写错。
用户已明确要求「保留整合事件快照，不建议为了显示当前 HEAD 再制造一个提交；
以 `git rev-parse` 为准即可」。因此：

- **不建议**把快照里的 SHA 改成 `3e2e2ba`（那会把历史事实抹掉）。
- **建议**保留快照原文，仅补一个「后续状态」小节，把「当前远端事实」与
  「事件快照」显式分离。

## 3. 最小修订方案（待用户确认后执行）

### 3.1 `README.md`（仅新增，不改现有行）

在第 7 行标题下、第 9 行之前插入一段，标题保持为历史事件快照：

```markdown
> **State note (added later):** the table below is a snapshot of the
> 2026-11-04 local integration event, kept for the record. The remote was
> subsequently advanced by two explicitly authorized pushes: `20f78ec` and
> `3e2e2ba`. Both `dev/dispatch-observer-safety-current-mvp-20261103` and
> `feat/closed-loop-settlement-20261104` now point at `3e2e2ba` on GitHub;
> `main` is unchanged at `a4f3777`. For the current state always use
> `git rev-parse HEAD` and `git ls-remote origin <branch>`, not this table.
```

并把第 7 行标题改为
`## Development branches and integration state (2026-11-04 historical snapshot)`
（1 行改动）。

其余行（含第 11 行 `Nothing has been pushed`、第 19 行 `Until a separately
authorized push happens`、第 28 行 `behind ... until the pending push is
authorized`）**全部原样保留**，因为加上上面的 state note 后，它们被明确限定为
「事件当时」的陈述，不再被误读为当前状态。

### 3.2 `docs/HANDOFF.md`（同样只新增，不改现有行）

在第 8 行 `## Current integration state (2026-11-04) — read this first`
下方插入：

```markdown
> **Superseding remote state:** this block records the 2026-11-04 local-only
> integration event. Two later explicitly authorized pushes advanced both
> `dev/...` and `feat/...` to `3e2e2ba` on GitHub; `main` remains `a4f3777`.
> Treat every SHA in this block as historical. Current state: `git rev-parse HEAD`
> and `git ls-remote origin <branch>`. See the progress log for the push/CI
> evidence.
```

第 151 行保持原样（它是 §10 的历史条目，已有第 36 行的「2026-11-03 only」
限定）；若用户希望更清晰，可追加半句
`（superseded: see "Current integration state" above）`，属可选。

### 3.3 明确不做的事

- 不改写快照里的任何 SHA 或 local-only 措辞。
- 不为显示当前 HEAD 制造新提交。
- 不动 `main`、不建 PR、不改 CI 配置。
- 不把本地完成描述为远端完成。

## 4. 影响面

两处均为纯注释性文本插入 + 一处标题措辞，零代码、零测试、零配置影响。
预计 `git diff --check` 可直接通过。执行前需用户明确确认。
