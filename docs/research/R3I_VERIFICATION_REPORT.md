# R3-I 验证报告：远征条件检查器

> 日期：2026-11-05　状态：功能验收通过；归档验收在推送/远端回读后单独判定
> 对应规格：`KancolleBot R3-I — 远征条件检查器` WB 开发交接说明
> 本报告只记录**实际跑过的命令与结果**。实现状态、限制与未验证规则均按代码实况书写。
> **R3-I.2 增量（2026-11-05）**：装备/属性可信只读观测、同一 fresh baseline 绑定、真客户端只读取证、
> 延迟/动画/陈旧三类零点击验证，另见 `docs/research/R3I_LIVE_VERIFICATION.md` 与
> `docs/research/R3I_LIVE_EVIDENCE_20261105.json`。下面的第 3、7、9 条限制与 NEXT 已按该增量更新。
> **R3-I.3 增量（2026-11-05）**：用户授权「可见 UI 刷新导航 + 补给、不派遣」后，真客户端跑出一次完整
> PASS 门禁（Fleet 3 / 远征 1，`source_freshness_verified=true`、`same_fresh_baseline=true`），见
> `docs/research/R3I_LIVE_PASS_VERIFICATION.md` 与 `docs/research/R3I_LIVE_PASS_EVIDENCE_20261105.json`。
> 第 7 条限制、测试数量与 NEXT 已按此增量再次更新。

---

## 1. 交付物与架构落点

| 交付物 | 文件 | 作用 |
|---|---|---|
| 上游研究报告 + 规则来源映射表 | `docs/research/R3I_UPSTREAM_RESEARCH.md` | 四个上游项目实际源码/数据读取记录、事实分级、字段映射、冲突清单、可复用/需适配/不应引入 |
| 单一静态规则库 | `src/kancollebot/expeditions/data/expedition_requirements.json` | 47 条规则，`schema_version=r3i-expedition-requirements-1`、`rule_version=2026-11-05.1`，含 provenance / source_refs / confidence / source_conflicts |
| 规则模型与加载器 | `src/kancollebot/expeditions/expedition_rule_data.py` | 加载时逐条校验 `mission_id ↔ catalog_number ↔ catalog_name`、槽位求和、可信度、冲突；提供 legacy adapter |
| 纯函数检查器 | `src/kancollebot/expeditions/expedition_requirement_checker.py` | `PASS/REJECT/UNKNOWN` 三态、逐项 `CheckResult`、只读 normalizer、JSON-safe 事件载荷 |
| 派遣门禁接入 | `src/kancollebot/expeditions/continuous_dispatch_controller.py` | 在 fresh baseline 之后、Taiha 预检与 `prepare_dispatch()` 之前评估；REJECT/UNKNOWN 零点击抛出 |
| 生产组合根 | `tools/one_expedition_ui_loop.py`、`tools/run_continuous_expedition_monitor.py` | 生产入口**无条件注入**检查器（`minimum_confidence="cross_checked"`）；两个入口的硬编码数值改由静态表提供 |
| 真实快照夹具 | `tests/fixtures/r3i_captured_port_snapshot.json` | `poi-port-snapshot-20261029T140534.950Z.json` 的逐字摘录（3 支舰队 / 14 条舰船记录，含真实 `-1` 哨兵） |
| 打包声明 | `pyproject.toml` | `[tool.setuptools.package-data]` 声明 `kancollebot.expeditions` 的 `data/*.json`（以及既有的 poi / calibration 数据），避免 wheel 安装时静态表缺失 |
| 测试 | `tests/test_expedition_rule_data.py`(21)、`tests/test_expedition_requirement_checker.py`(46)、`tests/test_continuous_dispatch_controller.py`(+8)、`tests/test_one_expedition_ui_loop.py`(+6)、`tests/test_one_expedition_production_integration.py`(1 条改写) | 净增 81 条测试函数 |

逻辑链路：`Poi ObservedState → normalize_observed_fleets / normalize_raw_port_payload → NormalizedFleetSnapshot → ExpeditionRuleSet → ExpeditionRequirementChecker.evaluate → RequirementEvaluation → 现有派遣流程`。

---

## 2. 必测场景对照表

| # | 规格场景 | 具体测试（`tests/test_expedition_requirement_checker.py`） | 结果 |
|---|---|---|---|
| 1 | 舰队完全满足要求 → PASS | `PassAndExplainabilityTests.test_a_fully_satisfied_fleet_passes_and_the_verdict_is_explainable` | PASS ✔ |
| 2 | 舰船数量不足 → REJECT | `RejectionTests.test_too_few_ships` | REJECT ✔ |
| 3 | 必需舰种不足 → REJECT | `RejectionTests.test_missing_required_ship_type` | REJECT ✔ |
| 4 | 旗舰等级不足 → REJECT | `RejectionTests.test_flagship_level_below_requirement` | REJECT ✔ |
| 5 | 舰队总等级不足 → REJECT | `RejectionTests.test_fleet_total_level_below_requirement` | REJECT ✔ |
| 6 | 装备数量不足 → REJECT | `RejectionTests.test_missing_equipment_is_rejected_when_evidence_exists` | REJECT ✔ |
| 7 | 属性不满足 → REJECT | `RejectionTests.test_stat_totals_below_requirement_are_rejected` | REJECT ✔ |
| 8 | 燃料/弹药不足 → REJECT | `RejectionTests.test_fuel_and_ammunition_shortfalls_are_rejected` | REJECT ✔ |
| 9 | 舰队状态不可用 → REJECT | `RejectionTests.test_unavailable_fleet_states_are_rejected`（7 种子状态） | REJECT ✔ |
| 10 | Poi 数据陈旧 → UNKNOWN | `UnknownDataTests.test_stale_poi_data_forces_unknown_even_when_a_failure_is_visible` + `NormalizerTests.test_stale_unverified_or_foreign_observations_are_unknown`（未验证源/外来源/过期/未来时戳 4 例） | UNKNOWN ✔ |
| 11 | 舰船关键字段缺失 → UNKNOWN | `UnknownDataTests.test_missing_ship_key_fields_force_unknown`（等级/舰种/油/弹/HP 5 例） | UNKNOWN ✔ |
| 12 | 远征规则缺失 → UNKNOWN | `UnknownDataTests.test_missing_mission_rule_forces_unknown` | UNKNOWN ✔ |
| 13 | 规则来源冲突 → UNKNOWN | `UnknownDataTests.test_a_source_conflict_forces_unknown_even_for_a_perfect_fleet`（21/37/38，其中 37 的其它条件全满足，仅因冲突被拦） | UNKNOWN ✔ |
| 14 | 非目标舰队数据混入 → 不得错误放行 | `test_non_target_fleet_snapshot_is_never_used_to_clear_another_fleet`、`test_ambiguous_fleet_identity_is_unknown`、`NormalizerTests.test_duplicate_or_missing_fleet_records_are_ambiguous`、`test_ship_ids_shared_with_another_fleet_are_ambiguous`、`CapturedPortPayloadTests.test_mixed_ship_ids_between_fleets_are_flagged` | 均不放行 ✔ |
| 15 | 输入顺序变化 → 判定一致 | `DeterminismAndBoundariesTests.test_input_order_does_not_change_the_verdict`（装备/属性/战意映射逆序）+ `NormalizerTests.test_fleet_record_order_does_not_change_the_verdict` | `as_dict()` 完全相同 ✔ |
| 16 | 重复检查 → 无副作用 | `DeterminismAndBoundariesTests.test_repeated_evaluation_has_no_side_effects_on_inputs_or_state`、`test_evaluate_performs_no_file_socket_or_database_io`（`open`/`sqlite3.connect`/`socket.socket` 全部打桩为抛错） | 3 次判定一致、输入不变、零 I/O ✔ |

附加动态冲突与门禁测试：`test_two_rule_sets_that_disagree_force_unknown_without_picking_one`（两套规则表分歧 → 不择一）、`test_a_single_source_rule_can_be_held_to_a_cross_checked_standard`、`test_unstructured_alternative_compositions_are_never_guessed`、`test_equipment_and_stat_evidence_absent_forces_unknown_not_pass`、`RuleTableValidationTests` 的 11 类篡改数据失败关闭。

### 边界（恰好达到要求）

| 边界 | 测试 | 结果 |
|---|---|---|
| 舰数 = 最低值、旗舰等级 = 最低值 | `test_exact_threshold_ship_count_and_flagship_level_pass`（3 舰 / Lv3 跑远征 3） | PASS ✔ |
| 总等级 = 200、旗舰等级 = 50、鼓桶 2 个 / 1 艘携带、属性 = 50/70/180 | `test_exact_threshold_total_level_stats_and_drums_pass` | PASS ✔ |
| 各阈值少 1 | `test_one_below_each_threshold_is_rejected` | REJECT ✔ |
| 无自由位时必须恰好 N 艘 | `test_no_free_slots_means_exactly_the_required_ship_count`（自建规则表） | REJECT ✔ |
| 真实快照的 `-1` 哨兵 | `CapturedPortPayloadTests.test_real_fleet_2_is_normalized_without_inventing_missing_fields` | 过滤为 `(1,4,8,25)` ✔ |

### 真实舰队快照

夹具是 2026-10-29 `api_port/port` 只读捕获的逐字摘录，`verificationStatus=CAPTURED_UNDER_STATICALLY_REVIEWED_PROBE`、`gameInputSent=false`、`gameApiRequestInitiated=false`。用 `normalize_raw_port_payload()` 归一化 Fleet 2 后：

- 舰船 ID `(1,4,8,25)`，`api_mission=[0,0,0,0]`，HP 齐全、无大破，`api_cond` 可读（53/49/49/49）；
- **不臆造**裸 port 载荷没有的 `max_fuel/max_bull`、`api_lv`、`api_stype`：`fuel_ready/ammo_ready=None`、`ship_levels/ship_types=()`；
- 评估远征 1 → **UNKNOWN 且 `allows_dispatch=False`**，`supply` / `flagship_level` / `flagship_valid` 均为 UNKNOWN，`game_clicks_sent=0`。这正是「陈旧/缺字段数据不能当满足」的实样。

### 派遣门禁（零点击）

`tests/test_continuous_dispatch_controller.py::R3IRequirementGateTests`：

- `PASS` 才进入既有派遣流程：断言 `dispatch_requirement_evaluation` 事件**早于** `dispatch_intent_persisted`，`ui.dispatches == [(2,1)]`，`dispatch_confirmed_by_poi` 存在；
- `REJECT`（燃料不足）/ `UNKNOWN`（等级元数据缺失、规则缺失、舰队身份混入、未解锁、政策不允许）统一断言：`ui.dispatches=[]`、`ui.home_calls=[]`、仅 1 次 Poi 读、无 intent 持久化、两个审计事件都带 `dispatch_clicks_sent=0` / `game_api_requests_sent=0` / `fleet_composition_changes_proposed=False`；
- 检查器默认不注入，故旧控制器测试行为不变（向后兼容另有专门断言）；
- 生产接线守卫：`tests/test_one_expedition_ui_loop.py::R3IProductionGateWiringTests` 用源码检查确认生产入口始终注入检查器、使用 `cross_checked`、`unlocked/dispatchable` 三个事实均已传入，且**没有**关闭门禁的 CLI 开关、没有私有 `KNOWN_COMPOSITION` 表。

---

## 3. 命令与实测输出

| 门禁命令 | 结果 |
|---|---|
| `PYTHONPATH=src python -m unittest discover -s tests` | R3-I 基线 **Ran 1473 tests — OK (skipped=2)**；R3-I.2 增量后 **Ran 1507 tests — OK (skipped=2)**（净增 34）；R3-I.3 新增 `test_verify_r3i_live_pass_gate.py`(8) 后 **Ran 1515 tests — OK (skipped=2)** |
| R3-G/R3-H 定向回归（9 个模块：`test_blocked_recovery`、`test_mission_result_witness*`、`test_mission_result_observer`、`test_startup_settlement_gate`、`test_dispatch_recovery`、`test_continuous_settlement_controller*`、`test_continuous_supervisor_settlement`） | **Ran 185 tests — OK** |
| `python -m compileall -q src tests tools` | OK |
| `git diff --check` | OK |
| `node --test tests/*.test.cjs`（仓库门禁命令，4 个文件） | **25/25 pass, 0 fail** |

> 全量测试过程中曾出现 1 条失败：`test_one_expedition_production_integration.py::test_fleet_4_is_no_longer_statically_deferred` 仍期待旧的 `no verified composition requirement`。已按 R3-I 语义改写（Fleet 4 + 远征 3 现在由静态表解析；Fleet 4 + 目录中无规则的远征 41 仍在门禁失败关闭），复跑后全绿。测试报告与实现状态一致。

---

## 4. 安全边界逐条对照

1. 只检查、不自动换船 —— 检查器内没有编成写入；`safety.fleet_composition_changes_proposed=False` 恒定。✔
2. 不自动拆编 / 不改成员 —— 输入是 frozen dataclass；重复判定输入不变有测试锁定。✔
3. 不构造/发送游戏 API —— `evaluate()` 的文件 / socket / sqlite I/O 均被桩拦截，零 I/O 有测试锁定。✔
4. 不绕过 Poi/CDP/UI 执行层 —— 检查器接在既有 `ContinuousDispatchController` 事务内，UI 点击仍由既有 `ui_driver` 发出。✔
5. UNKNOWN 不当成 PASS —— `allows_dispatch` 只在 `overall_result is PASS` 时为真；门禁对 REJECT/UNKNOWN 一律抛出。✔
6. 陈旧数据不作为依据 —— `state_trust` 判新鲜度 + `source_freshness_verified` + `poi-redux:` 来源，违背即 blocking UNKNOWN。✔
7. 规则缺失不默认满足 —— 无规则 → `rule_source` UNKNOWN 且多项检查 UNKNOWN；生产 `_mission_requirement()` 直接 `LoopError`。✔
8. 检查器无游戏内副作用 —— `safety` 载荷 + 零点击门禁测试。✔
9. 保留 R3-G/R3-H 门禁 —— `blocked_recovery.py` / `mission_result_witness.py` 未改；185 条定向回归通过。✔
10. append-only 事件与持久化不变 —— 只新增两个只读审计事件 `dispatch_requirement_evaluation` / `dispatch_blocked_requirement_check`，拒绝路径在 `prepare_dispatch()` 之前，不写 intent。✔

---

## 5. 实现限制与未验证规则清单

1. **上游资料均为社区整理，非游戏解包或官方数据**；47 条规则中 10 条 `cross_checked`、37 条 `single_source`。生产入口把 `minimum_confidence` 设为 `cross_checked`，因此**37 条单来源远征在实机门禁里一律 UNKNOWN、不可派遣**（纯检查器默认仍允许单来源，便于独立测试与显式放宽）。
2. **21 / 37 / 38 鼓桶数存在来源冲突**（kcauto_custom 疑似把大成功条件写成通常成功条件）——不猜测、不择一，判 UNKNOWN。
3. ~~装备与派生属性合计不在只读观测内~~ **已由 R3-I.2 补齐**：`src/kancollebot/adapters/poi/requirement_evidence.py` 从 Poi Redux 的独立窄投影读取装备实例 ID → `info.equips` 解析为 master id（鼓桶 75）、四个显示值合计、战意与入渠状态，并附着在**同一条** `ObservedFleetSnapshot` 上。实机采集到 4 支舰队的完整证据（Fleet 3 鼓桶 10/4 艘、Fleet 4 鼓桶 8/3 艘）。`equipment` / `fleet_stats` 条件现在会明确 PASS 或 REJECT，不再恒为 UNKNOWN。**但整体 PASS 仍受规则置信度门禁限制**：要求装备/属性的远征分别为 `single_source`（24/40/101/102/103）、来源冲突（21/37/38）或编成未结构化（104/105），生产入口 `minimum_confidence="cross_checked"` 下仍判 UNKNOWN。这是规则数据问题，不是观测缺失；本轮未擅自放宽门禁。
4. **102–105 的多套等价编成未结构化**（`composition_unstructured=True`），判 UNKNOWN；不臆造一套。
5. 41、43–46、B/D/E 系列、月次与交战型远征没有规则记录 → UNKNOWN。旗舰舰种限制只收录了 24/32/40。
6. `(駆+海防)` 的 DE 代用只对 wiki 明确写出的 4/5/9/24/102–105 启用；其余 DD 组按 DD-only 处理，可能**过严**（只会误拒，不会误放）。「自由枠なし」只对 37 启用。
7. **规则数值本身仍未做实机成功/失败回归**。R3-I.2 的只读取证（结算动画页、无 fresh witness → UNKNOWN 零点击拒绝）见 `R3I_LIVE_VERIFICATION.md`；**R3-I.3 已在用户授权「UI 刷新导航 + 补给、不派遣」后跑出一次真客户端完整 PASS 门禁**（Fleet 3 / 远征 1，13 项全 PASS、`source_freshness_verified=true`、`same_fresh_baseline=true`），见 `R3I_LIVE_PASS_VERIFICATION.md`。但「PASS 之后放行到既有派遣流程」的实机链仍未验证（用户未授权派遣）；本项剩余的只是规则数值回归与完整派遣链，不再是「没有实机 PASS」。
8. 判定优先级：state_trust / rule_source / fleet_identity 属 blocking UNKNOWN，会盖过可见的 REJECT；其它 UNKNOWN（装备/属性/补给/HP 证据）不盖过已确证的 REJECT，但两者都不能 PASS，且都记录在 `rejection_reasons`。
9. **未提前实现**自动换船、自动装鼓桶、舰船池、动态舰队与资源调度（R3-J）。`in_dock_ship_ids` 现由 R3-I.2 的 `api_ndock_time` 证据投影提供（>0 视为入渠），但真客户端当前无入渠舰船，尚未实机验证该分支。
10. 闪 / 大成功只做只读评估（`SparkleAssessment`，阈值 `api_cond>=50`），**不是派遣门禁**。

---

## 6. 两种验收分别判定

- **功能验收：通过。** 规则有来源与版本、检查器可独立测试且零 I/O、关键条件可解释、未知数据不放行、拒绝路径零点击、只有 PASS 进入既有派遣流程、R3-G/R3-H 定向回归无退化、测试报告与实现一致。
- **归档验收：不由本报告判定。** 需按项目规则完成本地提交 → 推送独立功能分支 → 从 GitHub 权威远端回读提交 SHA / 分支名 / 文件内容，并在交付回复中分别报告「本地保存 / 本地提交 / 远程推送 / 远程回读 / 测试验证」五项状态。

> NEXT：装备/属性可信只读观测（R3-I.2）与真客户端完整 PASS 门禁（R3-I.3）均已补齐并归档。用户设定的
> R3-J gate（真客户端完整 PASS + 实机证据归档 + GitHub 远端回读）已满足，**R3-J 在本轮启动阶段一**
> （规格核查与实施规划），不提前实现自动换船或动态舰队调度。仍未验证的是「PASS 之后放行到既有派遣
> 流程」的实机链与规则数值回归，二者需另行授权，详见 `R3I_LIVE_PASS_VERIFICATION.md` 第 6 节。
