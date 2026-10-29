# Poi 只读远征评估组合层（2026-10-29）

## 目的

提供一个不依赖游戏操作接口的只读组合层，将 `ObservedState` 与现有离线 `evaluate_expedition_fleet()` 规划器组合起来，避免把仅有本地时间戳、但没有可信来源新鲜度证据的状态展示成可执行建议。

## 实现内容

- 新增 `src/kancollebot/expeditions/read_only_assessment.py`。
- 新增 `tests/test_read_only_assessment.py`，覆盖来源未验证、观测过期、观测时间位于未来、已验证且新鲜时仍然仅返回规划结果、负最大状态年龄，以及调用方不能通过构造参数将 `dispatch_eligible` 改为 true。
- 只有 `source_freshness_verified=True` 且本地观测年龄在允许窗口内时才运行规划器；否则抑制规划结果并返回原因。
- 结果字段 `dispatch_eligible` 使用 `field(default=False, init=False)`，调用方不能通过构造参数覆盖为 true。该模块不持有 Poi client、adapter、runner 或 journal，也没有派遣方法，不会执行 I/O。

## 实现与验证状态

- **实现状态：** 已提交到目标 GitHub 分支；源码和测试文件已从 GitHub 目标分支回读。
- **验证状态：已通过本次离线验证。** 在与目标分支 HEAD `ecccb6061469e01dbec2aa3104279200d635aa2b` 对齐的独立 detached worktree 中，使用 Python 3.13.16 执行新增测试 6/6 PASS、远征规划器回归 11/11 PASS、core 状态/安全回归 12/12 PASS；完整离线单元测试 154/154 PASS；`py_compile` 语法编译 PASS。完整测试命令需要将仓库根目录与 `src` 同时加入 `PYTHONPATH`。
- **适用限制：** 这是独立的只读评估组件，尚未接入 Web UI 或生产应用组合入口；通过 freshness gate 只允许生成规划结果，不授权派遣；实时 witness 到当前 MCP 状态的端到端验证仍未完成。
- **安全状态：** 没有访问本机 Personal-Windows-MCP，没有刷新、输入或操作 Poi，没有主动请求游戏 API，没有创建真实派遣适配器，也没有改变 live-action 策略。真实派遣仍关闭。

## 后续

1. 审查 UI 如何清晰显示 freshness 未验证状态；只有状态可信时才显示规划结果，且持续将其标记为 planning-only。
2. 在独立分支中补充只读 UI 展示和回归覆盖；不接入任何游戏操作路径。
3. 实时 witness 到当前 MCP 状态的端到端验证仍需另行完成；离线测试通过不代表运行时新鲜度已验收。
