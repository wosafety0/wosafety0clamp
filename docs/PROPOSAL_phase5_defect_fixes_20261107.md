# 阶段5 实机缺陷修复方案（PROPOSAL，待审查）

**Status:** PROPOSAL — 只写方案，未动任何代码
**Date:** 2026-11-07
**依据:** `EXPEDITION_AUTOMATION_CORE_LOGIC.md`（权威业务基线）；阶段5 实机证据（`logs/run_log_phase5/`、`docs/PROJECT_kancollebot_Progress.md` 阶段5 三节）
**原则:** 每条修复都收窄或维持现有安全门禁，不放宽任何一处；结算完成判定一律以文档不变量 6（母港 + 全舰队清空同时成立）为准。

---

## 缺陷 1 — drain 时长上限短于实测链路

- **证据**：B 段（17:51:58 起）drain 3 次点击共 46.072s（三页 7.1 / 26.4 / 10.7s…），`final_page=home_port` 但 `--drain-timeout-seconds` 默认 45.0 先到 ⇒ `reached_home=false / error=timeout`。
- **根因**：时长上限是按「每动作 ~10s」拍的静态值，未按「每页一次点击 + 页面过渡可达 ~30s」建模。
- **方案**：默认上调到 150s（本次实测闭环所用值）。它是**时长**不是点击授权——点击仍受 `max_actions=24`、每击前 Poi settled 守卫、双帧确认约束，上调不放宽任何门禁。
- **测试**：保留 timeout 语义断言；新增「46s 链在默认参数下完成」回归。

## 缺陷 2 — 长睡眠后首个 Poi 读数不可信，无有界重试

- **证据**：D 段 18:13:41 / 18:14:01 连续两次 `monitor_failed: Poi observation is not trusted: Source data freshness has not been verified`，零点击 fail-closed，第三次尝试才进入刷新。
- **根因**：计时器长睡后，Poi 缓存的自然端口响应已过期；monitor 把「数据过期」直接当「监测失败」，没有「先重建新鲜度再判定」的路径。
- **方案**：监测前先走既有 freshness 机制**有界重建**（重新索取自然端口响应，最多 2 次重试、带退避）；重建失败仍 fail-closed。**不降低新鲜度校验本身**。
- **测试**：过期→重建成功→继续；重建两次失败→仍 fail-closed；重建次数有界。

## 缺陷 3 — 可见刷新不受页面证据约束（安全价值最高）

- **证据**：D 段 18:14:46 `VisibleUiRefreshError — after open_sortie: RETURN_RESULT stable on 2 consecutive frames`。`open_sortie` 的点击发生在页面证据判定**之后**、点击**之前**无第二次「此刻是什么页」检查；本次点击落在 HOME_PORT 未推进页面，但顺序上不排除点穿结果页（结果页点击即推进）。
- **根因**：阶段3/4 的否决与 MODAL 扣留只覆盖 supervisor tick，bootstrap 刷新路径是独立旧代码，只认「期望 SORTIE_MENU」。
- **方案**：把阶段3 的 `PageEvidence` 门禁延伸到刷新路径——刷新点击前**重新观测**当前页；结果是 MODAL/奖励页 ⇒ 扣留刷新、交给结算控制器（走 `page_navigation` 的 `hold_for_business_handler` 语义）；MENU ⇒ 按阶段4 用既有母港导航归位后再刷新。**不新写点击逻辑**，复用 `_recover_home_port` 与指纹识别。
- **测试**：刷新前观测到 RETURN_RESULT ⇒ 零点击、交结算；观测到 HOME_PORT ⇒ 照常刷新；观测 UNKNOWN ⇒ 重观测不点击。

## 缺陷 4 — 启动结算对正常页面推进过严

- **证据**：E 段 18:15:52 `STOPPED_FAIL_CLOSED — Expected RETURN_RESULT; Ooi fingerprint classified BONUS_ITEMS`：启动结算把结果页推进到奖励页后，仍按「必须看到 RETURN_RESULT」校验，把正常推进判成指纹不符，奖励页留在屏上需要再来一个进程。
- **根因**：校验写死了「单一期望页」，而文档 Step 5 的语义本来就是「点击→识别→循环直到母港」，页面按 RETURN_RESULT → BONUS_ITEMS → HOME_PORT 推进是**预期路径**。
- **方案**：启动结算改为文档语义的循环识别：每轮点击前识别当前页并记录，接受文档内的推进序列；**只在**识别不出/歧义/与 Poi settled 守卫冲突时 fail-closed。每击仍受识别 + Poi 守卫约束，不盲点。
- **测试**：三页推进序列全过；未知页 fail-closed；Poi 已 settled 时不再点击。

## 缺陷 5 — 账本与屏幕证据脱钩（互锁根因，最优先）

- **证据**：`dispatch_identities` 末条 18:36:13 被 settle-only 的可见刷新按「Poi 无在途」写成 `settled`，而屏幕结果页未被推进；同时 `fleet_runtime` 进 `RESUPPLY_REQUIRED` 时 `ship_ids_json` 置 NULL ⇒ 启动结算 `expected_ship_ids=[] / source=null` ⇒ 四条路径全部 `recorded_composition_unavailable`，零点击互锁。
- **根因**：两处违反文档。①不变量 6 要求结算完成 = 母港 + 全舰队清空**同时**成立，settle-only 只看了 Poi 一半证据；②文档 §1「游戏状态是唯一业务事实源，账本不得比服务器更有权威」——账本比屏幕先宣布完成。
- **方案**：
  1. `settled` 只在结算后成立（屏幕确认离开结算画面 + Poi 清空）才置位；只凭 `api_mission[0]==0` 一条证据不得置位。
  2. `ship_ids_json` 在 `RESUPPLY_REQUIRED` 保留最后一次派遣编成，直到补给完成；不再中途置 NULL。
  3. 编成证据保留可重建来源：run log 的 `dispatch_confirmed` 事件已含 `final_time_ms + ship_ids`（`blocked_recovery.py` 已有重建逻辑），启动结算在账本缺失时可按事件重建——**不放宽 `startup_attribution` 本身**。
- **测试**：Poi 清空但屏幕在结算页 ⇒ 不置 settled；置 NULL 回归移除；事件重建路径补齐。

## 附带发现 — v1 解析器是死代码且字段读错

`adapters/poi/expedition_state.py` 把 `api_mission[2]` 当任务 ID、`[3]` 当秒级时间，与 v2（`[2]`=epoch 毫秒，生产唯一使用方）矛盾；全仓库无引用。方案：随缺陷5 修复一并删除或对齐，走正常审查，不单独动。

## 流程裁决（写给后续所有阶段）

等待与刷新的任何变体（settle-only、手工 sleep、自算到期）都不得绕过循环已实现的 Step 3/4；新增调度模式必须先改 `EXPEDITION_AUTOMATION_CORE_LOGIC.md` 再动代码（文档 §5）。计时器唯一权威来源 = Poi `api_mission[2]`（epoch 毫秒），循环已接线（`plan_timer_sleep`，`one_expedition_ui_loop.py:2151`）。

## 建议顺序

缺陷 5（互锁根因）→ 缺陷 3（安全价值最高）→ 缺陷 4 → 缺陷 2 → 缺陷 1（一行默认值）。全部完成后进入下一轮单舰队实机验收，验收标准不变：自动化通过≠实机通过，单次成功≠连续稳定。
