# 单帧探针生产接线片段（未验证补丁）

日期：2026-11-06

## 状态

- 实现状态：PARTIAL / INCOMPLETE
- 验证状态：NOT TESTED
- 生产接线状态：NOT INTEGRATED
- 实机状态：NOT VERIFIED

## 产物

- `SINGLE_FRAME_PROBE_WIRING_UNVERIFIED_20261106.patch` 保存中断时 `tools/one_expedition_ui_loop.py` 与 `tests/test_continuous_expedition_ui_settlement.py` 的原始差异。

## 缺失依赖与限制

- 补丁只包含生产入口传入 `probe_current_state` 的片段及其测试草案。
- `CdpContinuousExpeditionUiDriver` 尚未实现 `probe_current_state` 构造参数和 `probe_settlement_page()` 单帧探针逻辑；因此该补丁单独应用后不能运行，也不得合入生产分支。
- 必须先恢复/实现 driver 侧的单帧全状态探针，再进行接口对齐、测试和安全审查。
- 点击前双帧确认门禁不得改变；阈值不得低于 0.97 / 0.04。

此文件仅用于恢复中断上下文，不代表功能实现或验证成功。