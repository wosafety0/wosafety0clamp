# 单帧全状态指纹分类器：独立实现与验证记录

日期：2026-11-06

## 状态

- 实现状态：IMPLEMENTED
- 验证状态：TESTED（独立模块测试 23/23 通过；Python 语法编译通过）
- 生产接线状态：NOT INTEGRATED
- 实机状态：NOT VERIFIED

## 已保存产物

- `src/kancollebot/adapters/poi/visual_fingerprint.py`：新增 `FingerprintClassification`，通过 `classify_screenshot_fingerprints_detailed()` 一次计算单帧对所有已登记状态的分数，并返回 best state、best score、top-1 margin、完整分数和门槛。
- `tests/test_visual_fingerprint.py`：新增详细结果与 margin 不足时保持 UNKNOWN 的测试。

## 安全边界

- 原有 `classify_screenshot_fingerprints()` 保持兼容，仍返回 `(state, scores)`。
- 默认阈值仍为 `minimum_score=0.97`、`minimum_margin=0.04`，本次没有降低门槛。
- 单帧分类只提供观测候选，不是点击授权；固定坐标点击前仍必须通过既有双帧状态确认门禁 `two_consecutive_in_memory_ooi_fingerprints`。
- 本提交没有接入生产 `probe_settlement_page`，没有改变生产点击路径，也没有进行实机 UI 验证。

## 后续约束

后续如继续接线，应将单帧结果仅用于探针时机和候选屏筛选；不得用 best candidate 替代 UNKNOWN，不得绕过双帧点击前确认，不得降低 0.97 / 0.04 门槛。