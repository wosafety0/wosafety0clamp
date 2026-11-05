# R3-J 阶段一下：规格核查与实施规划（2026-11-05）

> 状态：**R3-J 已启动，当前处于阶段一（规格/现状核查与方案设计）**。
> 本轮**没有实现**自动选队、自动组舰、动态改派或任何实机调度代码。
> 规格来源：`wosafety0/KancolleBot-path-index@main:KancolleBot_R3_WorkBuddy_Handoff.md` 第 8 节（私有库，经 `git show origin/main:` 读取）。
> gate 证据：`docs/research/R3I_LIVE_PASS_VERIFICATION.md`。

---

## 1. gate 已满足

| 用户设定的 R3-J 启动条件 | 证据 |
|---|---|
| 真客户端完整 PASS 门禁 | `R3I_LIVE_PASS_EVIDENCE_20261105.json`：`overall_result=PASS`、13 项全 PASS、`source_freshness_verified=true` |
| 派遣前检查使用同一份 fresh baseline | `same_baseline_binding.same_fresh_baseline=true` + 确定性测试锁定 intent 同源 |
| 保留 REJECT / UNKNOWN 零点击验证 | R3-I.2 证据与 `R3IFreshnessAndLatencyGateTests` 未删改，1515 条测试通过 |
| 实机证据归档 | `docs/research/` 两份证据 + 两份报告 + 权威进度章节 |
| GitHub 远端回读 | 本轮执行（见交付回复五项状态） |

---

## 2. 规格要点（原文归纳，不改写语义）

- 参考 `XVs32/kcauto_custom`，可借鉴：自动选择空闲舰队、自动组舰、舰队条件筛选、按资源收益选择远征、按资源缺口动态调整任务。
- **三个阶段**：
  1. **离线纯函数模拟**——输入「当前舰队状态 / 资源库存 / 远征候选 / 舰船条件 / 预计时间 / 补给成本」，输出「推荐舰队 / 推荐远征 / 决策理由 / 输入快照 / 预期资源收益」。
  2. **历史样本回放**——用真实历史快照测试策略，**不执行游戏操作**。
  3. **实机集成**——只有前两阶段通过后才允许接入实机调度。
- 必须遵守：结算串行、归属明确、多舰队待领取保护、派遣前条件检查、所有操作经过 UI 状态验证。
- 优化目标排序：**① 安全闭环完成率 → ② 结算正确率 → ③ 资源收益 → ④ 调度效率 → ⑤ 吞吐量**；「不能为了提高吞吐量牺牲结算安全性」。

> 结论：规格把「自动换船 / 动态调度」明确放在第三阶段。本轮只读核查与阶段一设计，**不提前触碰**。

---

## 3. 生产现状核查（已证事实）

### 3.1 可直接复用的纯函数与契约

| 模块 | 可复用点 | 边界 |
|---|---|---|
| `expeditions/expedition_rule_data.py` | `DEFAULT_RULE_SET`：47 条规则，带 `confidence` / `source_refs` / `source_conflicts` / `composition_unstructured` | 加载时按 `DEFAULT_CATALOG` 校验身份；规则是**条件**，不是收益表 |
| `expeditions/expedition_requirement_checker.py` | 三态 `PASS/REJECT/UNKNOWN` + `NormalizedFleetSnapshot` + 逐项 `CheckResult` + JSON-safe `as_dict()` | 零 I/O、零副作用；只有 PASS 放行 |
| `adapters/poi/full_supply.py` | `FleetSupplyCost` / `build_fleet_supply_plan()` / `FullSupplyPlan`：从舰船当前值 + master 上限算**补给成本** | 需要 fresh witness 才能用于实机动作；阶段一可作为纯输入 |
| `expeditions/planner.py` | `evaluate_expedition_fleet()` / `ExpeditionPlan` | 单舰队是否可派，不是跨舰队选优 |
| `expeditions/rotation.py` | `select_next_expedition_fleet()` / `FleetRotationPlan` | 轮转顺序，不是按收益选优 |
| `expeditions/mission_eligibility.py` | `evaluate_mission_composition()` / `MissionCompositionRequirement` | 编成判定，与静态规则表并行存在 |
| `expeditions/fleet_composition_planner.py` / `dock_composition_planner.py` | `plan_flagship_exchange()` / `plan_dock_composition()` | **已存在纯规划**，但产出的是「建议动作」，尚未接入实机执行；R3-J 阶段一**不得**把它们变成执行 |
| `core/observation_contracts.py` | `ObservedFleetSnapshot`（含 R3-I.2 的装备/属性/战意/入渠证据）、`ObservedState.require_fresh()` | 同一 baseline 契约，R3-I.3 已实机验证 |
| `runtime_config.py` | `dispatchable_fleet_ids` / `deferred_fleet_ids` / `non_dispatchable_fleet_ids` / `max_state_age_seconds=30` / `refresh_cooldown_seconds=30` | 权限字段**只属于 EXPEDITION 任务**，文件头有 SCOPE WARNING |
| `expeditions/continuous_scheduler.py` | `FleetPhase` 状态机、`confirm_*` / `recover_blocked_*`、UI 仲裁 `UiOperationArbiter` | 结算串行的既有权威；R3-J 不得绕过 |
| R3-G / R3-H | `blocked_recovery.py` / `mission_result_witness.py` | 归属与恢复白名单，未改 |

### 3.2 关键缺口（本阶段最重要的发现）

**项目内没有任何「远征预计时间 / 资源收益」的可信静态数据。**

- `calibration/expedition_catalog.py` 的 `CatalogEntry` 只有 `number / name / area`，`DEFAULT_CATALOG` 只做编号↔mission_id 映射。
- `expedition_requirements.json` 的 47 条规则只有**条件**字段（`minimum_ships`、`required_stype_groups`、`flagship_min_level`、鼓桶、属性……）与来源元数据，**没有** `duration_minutes`、`reward_fuel/ammo/steel/bauxite`、大成功收益、消耗。
- 全仓 grep `duration / reward / win_item / resource_gain` 只命中「结算奖励页 UI 推进」相关代码，不是远征收益数据。

因此规格阶段一要求的 6 项输入中，「预计时间」「资源收益」「补给成本」三项里：
- 补给成本：**已有**纯函数可直接算；
- 预计时间 / 资源收益：**缺数据**，必须新增一份带 `provenance` / `rule_version` / `confidence` / `source_conflicts` 的静态参考表，并像 R3-I 一样显式区分「游戏明确 / 社区验证 / 项目推测」。**不得从上游 README 或猜测填入。**

### 3.3 其它已记录的环境约束

- 47 条规则中 37 条 `single_source`、21/37/38 来源冲突、102–105 编成未结构化；生产 `minimum_confidence="cross_checked"` 下这些一律 UNKNOWN。**阶段一推荐器必须继承这条限制**，不能因为「按收益选优」就把 UNKNOWN 当候选。
- R3-I.3 只证明了「门禁会放行」，**没有**执行完整派遣循环；阶段三实机集成仍需单独授权。
- `full_supply_ui_action.py` 的刷新驱动 `settle_seconds=1.0` 在奖励链刚结束时会因淡入转场复证失败（fail-closed，不误操作），本轮仅在验证进程内加宽等待，**生产文件未改**，应作为独立修复项。
- 实机 `info.ships` 按响应体积截断；装备/属性字段**绝不能**并入 8 字段 port-witness 投影（会 225/239 截断并静默破坏 freshness）。阶段二回放若构造大快照必须保留独立窄投影。

---

## 4. 第一阶段实施方案（待下一轮实现，本轮只设计）

新增 `src/kancollebot/expeditions/dispatch_planning.py` 与静态收益表
`src/kancollebot/expeditions/data/expedition_economics.json`，均为**离线纯函数/静态数据**。

### 4.1 输入契约（严格对应规格 6 项）

```python
@dataclass(frozen=True, slots=True)
class DispatchPlanningInput:
    fleets: tuple[ObservedFleetSnapshot, ...]      # 当前舰队状态（含装备/属性证据）
    resources: Mapping[str, int]                   # 资源库存
    candidates: tuple[int, ...]                    # 远征候选 mission_id
    ship_conditions: Mapping[int, ...]             # 舰船条件（编成/等级/损伤/入渠/战意）
    economics: Mapping[int, ExpeditionEconomics]   # 预计时间 + 收益（静态表）
    supply_costs: Mapping[int, FleetSupplyCost]    # 补给成本
    policy: DispatchPlanningPolicy                 # dispatchable / unlocked / 置信度门禁 / 待领取保护
    observed_at: datetime
```

### 4.2 输出契约（严格对应规格 5 项）

```python
@dataclass(frozen=True, slots=True)
class DispatchRecommendation:
    fleet_id: int | None
    mission_id: int | None
    reasons: tuple[str, ...]          # 决策理由，逐条可解释
    input_snapshot_digest: str        # 输入快照 SHA-256，可复现
    expected_resource_gain: Mapping[str, int] | None   # 收益 - 补给成本 - 远征消耗
    decision: str                     # RECOMMEND / ABSTAIN；ABSTAIN 含显式原因
```

`ABSTAIN` 是常态而非异常：任一候选不能通过 R3-I 三态门禁、baseline 不 fresh、规则置信度不足、多舰队待领取、结算未串行完成、或经济数据缺失/冲突 → **一律 ABSTAIN，绝不给出推荐**。

### 4.3 决策顺序（把规格的优化目标排序落进代码）

1. **安全闭环完成率**：先排除任何处于结算/归属未闭环、BLOCKED、多队待领取、状态不 fresh 的舰队；`UiOperationArbiter` 串行约束不得绕过。
2. **结算正确率**：只考虑 `dispatchable_fleet_ids ∩ unlocked` 且 R3-I 判 **PASS** 的组合。
3. **资源收益**：`净收益 = 远征收益 − 补给成本 − 远征燃料/弹药消耗`；按资源缺口加权（需显式、可解释的权重，不做隐式偏好）。
4. **调度效率**：预计时间参与排序，但**不覆盖**前三条。
5. **吞吐量**：最后才考虑；任何为提高吞吐而放宽前四条的实现都禁止。

### 4.4 硬约束

- 零 I/O：不读文件（静态表由调用方传入）、不开 socket、不连 SQLite、不点击、不发游戏 API、不用 `Runtime.evaluate`；用桩测试锁定。
- 不写游戏状态、不改舰队成员、不自动换船；`dock_composition_planner` / `fleet_composition_planner` 的「建议动作」在阶段一**不得**被转成执行。
- 输入 frozen、输出 frozen、重复调用结果一致、JSON-safe `as_dict()`。
- 静态收益表必须带 `schema_version` / `version` / `provenance` / `confidence` / `source_conflicts`；加载时按 `DEFAULT_CATALOG` 校验 mission_id ↔ 编号；缺项/冲突 → 该远征不参与排序并记入 reasons。
- 与 R3-I 共用同一份 fresh baseline 语义，不引入第二套 freshness 判据。

### 4.5 测试矩阵（阶段一验收）

| 类别 | 用例 |
|---|---|
| 门禁继承 | UNKNOWN / REJECT / 置信度不足 / 来源冲突 → ABSTAIN；只有 PASS 才可能 RECOMMEND |
| freshness | 超龄、`source_freshness_verified=False`、future-dated → ABSTAIN |
| 安全优先 | 多队待领取 / BLOCKED / 结算未闭环 → 即使收益最高也 ABSTAIN |
| 归属 | 舰队身份混入 → ABSTAIN |
| 经济 | 缺收益数据 / 冲突数据 / 补给成本 > 收益 / 恰好相等边界 |
| 排序 | 安全 > 收益 > 时间 > 吞吐 的反例（高收益但不安全必须落选） |
| 纯函数 | 重复调用一致、输入不变、零 I/O 桩、JSON-safe 序列化、输入快照 digest 稳定 |
| 回归 | R3-G/R3-H 9 模块、R3-I 全量门禁不退化 |

---

## 5. 第二 / 第三阶段门槛

- **阶段二（历史回放）**：用 `tests/fixtures/r3i_captured_port_snapshot.json`（2026-10-29 真实 port 摘录）、`R3I_LIVE_EVIDENCE_20261105.json`、`R3I_LIVE_PASS_EVIDENCE_20261105.json` 作为输入样本，验证推荐可复现且不产生动作。**不执行游戏操作**。
- **阶段三（实机集成）**：前两阶段全部通过 + 用户单独授权 + 保留 R3-I 门禁 + 结算串行 + 归属明确 + 多队待领取保护 + 每步 UI 状态验证。R3-I.3 的 PASS 不等于这个授权。

---

## 6. 本轮交付边界

- ✔ 读取 R3-J 规格、核查生产链路、定位关键数据缺口、给出阶段一完整设计与验收矩阵。
- ✘ 未实现 `dispatch_planning.py` / 收益静态表 / 任何自动选队或动态调度代码。
- ✘ 未改任何生产模块；本轮改动只有 R3-I.3 的验证工具、测试与归档文档。
- 开放问题（需下一轮先解决）：① 上游时长/收益数据的**实际源码与冲突核对**（沿用 R3-I 的研究方法，不看 README 就下结论）；② `full_supply_ui_action.py` 转场等待的生产修复是否单独立项；③ 资源缺口权重的定义与可解释性。
