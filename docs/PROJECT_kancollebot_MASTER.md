# KancolleBot 项目规则与架构

## 目标

构建一个运行在用户本地 Windows/POI 环境中的舰队收藏自动化 Bot。GitHub 用于代码托管、审查与自动测试，不承担持续运行游戏客户端的职责。

## 参考项目的正确身份与优先级

1. **KCV = 非提助手**，用户指定的官网为 https://kancolleassistant.mechland.cn/ 。这是首要研究对象；重点学习其自动化流程、决策、任务调度、远征与出击逻辑。
2. **KCA = kcauto_custom**，仓库 https://github.com/XVs32/kcauto_custom 。作为补充实现：当非提助手的源码难以理解、缺少证据或某项机制需要交叉验证时，参考 KCA 的实现，不以 KCA 替代 KCV 的设计方向。
3. **POI** 是目标本地运行宿主及状态接入目标。优先验证 POI 实际提供的受支持数据接口，不假设其它项目的 API 可以直接用于 POI。
4. Python 是我们自己的 Bot 的主要实现语言。研究 C# 源码是为了理解业务逻辑，不意味着必须用 C# 重写。
5. 不要把 KCV（非提助手）与 Grabacr07/KanColleViewer 官方浏览器项目混为一谈；后者不是用户指定的首要自动化参考。
6. 将已证实事实、推断和待验证事项分开记录，技术结论尽可能附源文件、符号、调用链或测试证据。

## 开发顺序

- 先追踪 KCV（非提助手）的自动化入口、调度循环、状态同步、舰队就绪判断、远征和出击流程、错误恢复及停止条件。
- 对难以确认的行为，再使用 KCA/kcauto_custom 对照。
- 在 Python 中先实现纯状态模型、策略函数和离线模拟测试。
- POI 读取适配器验证通过后，再逐步研究操作接口。
- 自动远征优先；自动战斗在状态读取、模拟测试和安全层通过验证后再推进。

## 安全边界

- 默认禁止真实游戏操作；当前基础代码不连接 POI、不发出操作请求。
- 不公开 POI 调试端口，不将本地调试服务绑定到公网。
- 不提交账号密码、Cookie、令牌、API 密钥、个人游戏会话数据或不必要的请求内容。
- 不复制或上传完整的 POI/KCA/KCV 第三方源代码树；仅保存必要的研究笔记、引用和可验证证据，第三方源码的使用须符合其许可。
- 不因测试通过就宣称功能已在真实游戏中验证。
- 新依赖、付费 API、付费服务或本地软件安装必须先说明成本/收益并取得用户明确同意。
- 真实游戏操作须在接口验证、模拟测试、失败恢复与停止机制验证后分阶段启用。

## 质量门槛

- 每个模块具有独立测试。
- 状态数据经过输入校验。
- 任何未来的操作适配器必须具备前置条件检查、明确停止方式和审计日志。
- CI 通过仅代表当前自动检查通过，不代表游戏行为正确或可安全运行。


## Cross-project documentation and rule isolation

- Before KancolleBot work, read `docs/PROJECT_kancollebot_MASTER.md` and `docs/PROJECT_kancollebot_Progress.md` on branch `dev/expedition-closed-loop-mvp`.
- If the actual branch differs, verify the current branch and file tree before choosing paths; never guess a path.
- For UmaAI work, first read `docs/PROJECT_UMAAI_Progress.md` on `analysis/qianye-reverse-engineering-20261025`, then the relevant topic-level `PROGRESS.md`.
- Do not transfer project-specific rules or constraints across repositories. Use the receiving project's own authoritative documents and explicit user decisions; if rule ownership is unclear, verify its source before treating it as binding.
- These are workflow and documentation rules; they do not imply that code, runtime settings, or game state have been changed.
