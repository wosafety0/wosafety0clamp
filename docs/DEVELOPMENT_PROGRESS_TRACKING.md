# 分层开发进度追踪规范

## 权威数据与范围

机器可读的唯一进度基线是 progress/project_plan.json；本文件定义方法，不另建平行任务清单。基线绑定仓库、分支、commit 和日期，不能把远端状态说成本地同步。仓库根目录没有 AGENTS.md；已读取固定版本的全局 AGENTS.md 和 docs/CLAMP_HARNESS_TRIAL_USAGE.md。仓库规范名为 wosafety0clamp；KancolleBot 是过期仓库名。README/历史文件中的旧名称属于遗留内容，不作为仓库身份冲突的证据。当前范围限定为 Clamp 静态源码提取核验及后续 Harness。

## 分解、依赖与权重

- X 为最终成果，A 为静态提取阶段，B 为后续 Harness 阶段；B 依赖 A。
- 每个节点必须有稳定 ID、父节点、正权重、状态、验收条件、依赖和证据引用。
- 同一父节点下的直接子节点权重合计必须为 100；汇总时只计算叶子任务，避免父子重复计数。
- 任务按依赖拓扑顺序推进；依赖未 VERIFIED 时，下游不能被标为 VERIFIED。当前真实快照必须来自用户本机 Poi Hand 沙盒，不得以合成数据替代。

## 状态与计算

| 状态 | 含义 | 工作进度系数 | 验收贡献 |
|---|---|---:|---:|
| PLANNED | 已规划未实施 | 0% | 0% |
| IMPLEMENTED | 已实施，验证未完成 | 40% | 0% |
| TESTED | 已执行指定测试 | 70% | 0% |
| VERIFIED | 验收通过且有 PASS 证据 | 100% | 100% |
| BLOCKED | 明确记录阻塞与解除条件 | 0% | 0% |

工作进度是状态加权估算，不代表成果可用。验收进度只计 VERIFIED 叶子任务。证据覆盖率统计存在可定位 PRESENT/PASS 证据的叶子权重，不等于验收通过率。旧提交的 CI 结果不证明当前提交通过；离线合成测试不证明本机 Poi 兼容。

## 证据、阻塞与归档

证据至少包含 type、status、ref，可附 note。引用应优先指向 commit、文件、测试运行、可复现命令或脱敏产物路径。禁止归档密钥、Cookie、会话凭据和不必要的完整游戏状态。

BLOCKED 任务必须记录 reason 与 resolution；工具会显示下游依赖是否 READY、WAITING_FOR_DEPENDENCY 或 BLOCKED_BY_DEPENDENCY。阶段快照应绑定基线 commit、生成时间、工作进度、验收进度、证据覆盖率、阻塞和下一步。必须区分本地保存、远端提交、CI 测试和真实环境验证。

## 命令

在仓库根目录运行：

- python tools/progress_tracker.py validate
- python tools/progress_tracker.py report
- python tools/progress_tracker.py report --format json
- python tools/progress_tracker.py report --format markdown
- python tools/progress_tracker.py snapshot --out progress/snapshots/2026-10-29.md
- python -m unittest discover -s tests -v

工具只读计划并输出报告/快照，不访问网络、不执行游戏操作、不修改系统设置。snapshot 只写入明确指定的文件。
