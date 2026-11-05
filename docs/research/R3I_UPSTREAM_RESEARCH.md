# R3-I 上游项目研究、远征条件与来源映射

> 状态：VERIFIED（2026-11-05，本轮实际读取源码/数据文件后撰写）
> 目的：为 `ExpeditionRequirementChecker` 选择可追溯的静态规则数据，并明确哪些机制**不应**引入本项目。
> 约束：只研究、只借鉴数据模型；不合并上游项目，不改变 KancolleBot 现有架构。

---

## 1. 实际读取了什么

全部为本次会话内实际下载/抓取的文件，不是依据 README 或记忆推断。

| 来源 | 实际读取位置 | 版本/提交 | 读取方式 |
|---|---|---|---|
| `XVs32/kcauto_custom` | `data/expedition/expedition.json`（42 条）、`kcauto/expedition/expedition_core.py`（36048B）、`kcauto/kca_enums/ship_types.py`、`kcauto/config/expedition.py` | 默认分支 `develop`，HEAD `18d61ab3fe91235ea2476ea186bad5c516572a66` | `git clone --depth 1 --branch develop` + 本地读取 |
| `keji8/KanColle-JSON-Data` | `json/expeditionRequirements.json`（40 条）、`json/expedition.json`、`json/shiptypedata.json`、`jpaentity/ExpeditionRequirements.java`、`README.md` | `master`，HEAD `3e642f2a595691fb4b139176691a3e307c64892c` | raw 文件下载 + 本地读取 |
| `poooi/poi-plugin-ezexped` | `exped-reqs/world-1.es`、`structs/ereq/common.es`、`docs/EReq.md` 所在文件树 | `master`，HEAD `d5051e5a19247847b40e4a29ad2af63ddb37dc70` | raw 文件下载 + GitHub tree API |
| `perryhuynh/kcauto` | `kcauto/expedition/expedition_core.py`、`kcauto/config/expedition.py`、`kcauto/kca_enums/expeditions.py` | `master`，HEAD `fcaf2a6ae1236cbc27d6e6196f82ad4afcbd2124` | raw 文件下载 + 本地读取 |
| KanColle Wiki（日） | `遠征`（仕様/成功条件）、`遠征/詳細一覧表`（鎮守府・北方・南方・南西各表） | Last-modified 2026-10-18 / 2026-09-01 | 网页抓取，逐行核对数值 |
| KanColle Wiki（英） | `en.kancollewiki.net/Expeditions` 的 Success Conditions 段 | 2026-11-05 读取 | 网页抓取 |

---

## 2. 各项目实际做法与异常处理

### 2.1 `XVs32/kcauto_custom`（规则数据 + 自动编队）

- `data/expedition/expedition.json` 每条记录含 `reqFlagLevel`、`reqCombinedLevel`、`reqDrum`、`reqDrumCarriers`、`reqComposition`（如 `"1CL,5DD"`、`"3DD,1NA"`），以及资源收益。
- `reqComposition` 由 `_get_exp_ship_requirement_from_composition()` 解析成 `[ShipTypeEnum, ...]`；源码注释明确这些数字是 `api_mst_stype` 的 `api_stype`，`NA=0` 是该项目自定义的通配码，不是官方舰种。
- `kcauto/kca_enums/ship_types.py` 给出完整 `api_stype` 码表：`DE=1, DD=2, CL=3, CLT=4, CA=5, CAV=6, CVL=7, BB=9, BBV=10, CV=11, SS=13, SSV=14, AV=16, CT=21, AS=20 …`。本项目只需要其中一个子集。
- `_assign_ship()` 会**自动从舰船池挑船、自动装鼓桶、自动换编成**，并在 `assign_fleet.sum_level < req_lv_sum` 时抛 `ExpeditionAssignmentError`。异常处理方式是「换一个候选远征继续」，不是「拒绝派遣」。
- **不应引入**：自动换船、自动装装备、舰船池排序、编队生成。R3-I 规格第 5 节明确禁止。

### 2.2 `keji8/KanColle-JSON-Data`（结构化规则模型）

- `json/expeditionRequirements.json` 字段：`minimumNum`、`flagShipMinLevel`、`fleetMinLevel`、`flagshipTypeID`、`shipType1..shipType6`（字符串，逗号分隔表示多种可接受的舰种 ID）、`otherRequirements`（中文自由文本，如「最少3只舰娘装备运输桶」）、`memo`。
- `jpaentity/ExpeditionRequirements.java` 逐字段给了中文注释，是本轮「规则模型」的主要参考。
- **关键陷阱**：它的 `shipTypeN` 用的是**自己的** `typeID`（`shiptypedata.json`：`15=軽巡洋艦`、`17=駆逐艦`），**不是** `api_stype`（`api_stype 17 = 揚陸艦`）。直接把 `17` 当 `api_stype` 会判错全部驱逐舰条件。
- `README.md` 自述：「基于 wikiwiki.jp 整理，非官方数据，也非游戏解包，不能保证 100% 正确」。
- **采用**：字段划分（舰数/旗舰等级/合计等级/旗舰舰种/其他条件 + memo）、来源可信度声明的写法。
- **不采用**：它的舰种 ID 空间（已映射到 `api_stype`）、自由文本 `otherRequirements`（改为结构化数字）。

### 2.3 `poooi/poi-plugin-ezexped`（条件 DSL 与大成功）

- `exped-reqs/world-1.es` 用组合式 DSL：`fslSc(旗舰等级, 舰数)`、`mk.Morale()`、`mk.FleetCompo({DDorDE:3})`、`mk.AnyFleetCompo([...])`、`mk.LevelSum()`、`mk.TotalFirepower/AntiAir/Asw/Los()`、`mk.DrumCount()`。
- `structs/ereq/common.es` 里 `isEqpDrum = equip => equip.mstId === 75` ⇒ **鼓桶装备 master id = 75**；`isShipSparkled = ship => ship.morale >= 50` ⇒ **闪阈值 50**。
- `requireFlagship()` 在无舰时返回 `unSatNoFlagship`；`requireGreaterOrEqual(x,y)` 统一产出 `{sat, extra}`。这是「每项条件可解释」的好范式。
- `AnyFleetCompo` 说明有些远征存在**多套等价编成**，不能用一套简单计数表达。本项目对这类远征返回 UNKNOWN，不臆造一套。

### 2.4 `perryhuynh/kcauto`（职责边界反例）

- `expedition_core.py::send_expeditions()` 只做 `_validate_expeditions()`（检查远征是否解锁），然后 `choice()` 随机选一个远征直接点下去。
- `_dispatch_expedition()` 只检查 `fleet.needs_resupply`，然后点 `expedition_dispatch.png`，用 `api.api.update_from_api()` 读取结果。
- **没有**任何舰数/舰种/等级/装备/属性校验。这正是规格里「避免只执行、不校验」的反面教材。
- `config/expedition.py` 只做配置合法性（远征枚举、舰队 2/3/4 不重复、联合舰队时 Fleet 2 不可派遣）。可借鉴「配置期硬失败」，但校验对象不同。

### 2.5 Wiki（规则依据）

- 成功必要条件：编成舰数 + 舰种、旗舰 Lv、部分远征的舰队合计 Lv、部分远征的鼓桶（「装備舰艇数」与「合计个数」）、部分远征的火力/对空/对潜/索敌合计；**未补给几乎必然失败**；旗舰大破不能出航（随伴舰大破不影响）；疲劳影响成否。
- 大成功：全员战意高扬（闪）；鼓桶类远征另需「闪 4 只 + 超额鼓桶」。**大成功不是派遣必要条件**，本项目只读评估。
- 詳細一覧表 鎮守府 01–08 实测值：旗舰 Lv 依次为 1/2/3/3/3/4/5/6；最低舰数 2/4/3/3/4/4/6/6；01/02/03/06/07/08 舰种自由；04 需「軽1 + (駆+海防)2」，05 需「軽1 + (駆+海防)2 + 他1」。
- 37 東京急行：全 6 只「軽1 + 駆5」，通常成功需「3 只以上带鼓桶、合计 4 个以上」；38 東京急行(弐)：「駆5 + 他1」，通常成功需「4 只以上带鼓桶、合计 8 个以上」。21 北方鼠輸送：需「3 只以上带鼓桶、合计 3 个以上」。

---

## 3. 事实分级（游戏明确 / 社区验证 / 项目推断）

| 结论 | 分级 | 依据 |
|---|---|---|
| 旗舰大破不能出航；未补给会失败；远征有旗舰 Lv、舰数、舰种条件 | 游戏内可见规则，由社区 Wiki 记录 | wiki 仕様 + 英文 Wiki Success Conditions |
| 01–08 的旗舰 Lv / 舰数 / 舰种 | 两来源一致的社区验证结论 | wiki 詳細一覧表 + kcauto_custom 数据，逐行一致 |
| `api_stype` 码表、`DRUM master id = 75`、闪阈值 50 | 上游项目静态常量（可复核源码） | `ship_types.py`、`ereq/common.es` |
| `reqComposition` 里 `NA` 表示自由位、`0` 是项目自定义码 | 上游项目自定义约定 | `_get_exp_ship_requirement_from_composition` 注释 |
| 「自由位为 0 时禁止额外舰船」 | **项目推断，未作为通用规则** | 只有 37 等少数远征被 wiki 明确标为「自由枠がない」；本项目只对 37 启用 |
| 「(駆+海防)」的 DE 代用 | 社区验证，但仅对 wiki 明确写出的远征启用 | wiki 詳細一覧表 |
| 「未补给 = 必须满油满弹」 | **项目策略**，不是游戏硬性规则的精确表述 | wiki 说「失败要因，精确条件未查明」；本项目保守要求满补给 |

---

## 4. 规则字段映射表

| 规格检查类别 | 上游字段 | 本项目 `ExpeditionRequirement` 字段 | 缺失数据时 |
|---|---|---|---|
| A 舰数 | `reqComposition` 总槽数 / `minimumNum` | `minimum_ships`、`allow_extra_ships` | 不缺失（纯静态） |
| A 必需舰种 | `reqComposition` 各码 / `shipType1..6` | `required_stype_groups`（`api_stype` 组 + 数量）、`free_slots` | 舰种元数据缺失 → UNKNOWN |
| A 旗舰舰种 | `flagshipTypeID` / wiki「旗艦固定」 | `flagship_stypes` | 舰种元数据缺失 → UNKNOWN |
| A 旗舰有效 | `requireFlagship()` | `ship_ids[0]` 存在性 + `ship_levels/ship_types` 完整 | 旗舰缺失 → UNKNOWN |
| B 旗舰等级 | `reqFlagLevel` / `flagShipMinLevel` | `flagship_min_level` | 等级缺失 → UNKNOWN |
| B 舰队合计等级 | `reqCombinedLevel` / `fleetMinLevel` / `mk.LevelSum` | `fleet_min_total_level` | 等级缺失 → UNKNOWN |
| C 鼓桶个数 | `reqDrum` / `mk.DrumCount` | `required_drums` | 装备数据缺失 → UNKNOWN |
| C 携鼓桶舰数 | `reqDrumCarriers` / `mk.DrumCarrierCount` | `required_drum_carriers` | 装备数据缺失 → UNKNOWN |
| C 特定装备 | `otherRequirements`（自由文本） | `required_equipment_master_ids` | 装备数据缺失 → UNKNOWN |
| D 火力/对空/对潜/索敌 | `mk.TotalFirepower/AntiAir/Asw/Los` | `minimum_total_stats` | 属性合计缺失 → UNKNOWN |
| E 出航状态 | `api_mission` / `at_base` | `mission_status`、`reserved`、`in_dock_ship_ids` | 状态不明 → UNKNOWN |
| F 燃料/弹药 | `needs_resupply` / 满补给说明 | `fuel_ready`、`ammo_ready`、`unsupplied_ship_ids` | 当前值或最大值缺失 → UNKNOWN |
| G 数据可信度 | 上游无统一机制 | `state_trusted`、`identity_ambiguous`、`source_conflicts`、`confidence` | 陈旧/冲突/混入 → UNKNOWN |
| 闪 / 大成功 | `mk.Morale`、`isShipSparkled` | `SparkleAssessment`（只读） | 不作为门禁 |

---

## 5. 真实的来源冲突（不静默择一）

| 远征 | 字段 | 来源 A | 来源 B | 处理 |
|---|---|---|---|---|
| 21 北方鼠輸送作戦 | 通常成功所需鼓桶总数 | kcauto_custom `reqDrum=4` | wiki「合計3個以上必要；合計4個以上は大成功」 | 规则标记冲突 → 判定 UNKNOWN |
| 37 東京急行 | 通常成功所需鼓桶总数 | kcauto_custom `reqDrum=5` | wiki「合計4個以上必要；合計5個以上は大成功」 | 规则标记冲突 → 判定 UNKNOWN |
| 37 東京急行 | 携鼓桶舰数 | kcauto_custom `reqDrumCarriers=3` | keji8「最少4只舰娘装备运输桶」 | 一并写入冲突说明 |
| 38 東京急行(弐) | 通常成功所需鼓桶总数 | kcauto_custom `reqDrum=10` | wiki「合計8個以上必要；合計10個以上は大成功」；keji8 亦为 8 | 规则标记冲突 → 判定 UNKNOWN |

推测（不作为事实）：kcauto_custom 可能把「大成功」的鼓桶数写成了通常成功条件。但本项目**不猜测、不择一**，按规格把来源冲突判为 UNKNOWN。

---

## 6. 可复用 / 需适配 / 不应引入

**可直接复用**：`api_stype` 码表；鼓桶 master id 75；闪阈值 50；「舰数 + 旗舰 Lv + 合计 Lv + 舰种槽 + 鼓桶 + 属性合计」的字段划分；`{sat, extra}` 式可解释判定结果；配置期硬失败。

**需适配**：上游舰种 ID → `api_stype`（keji8 用的是自有 ID，`17` 不是驱逐舰）；`NA` 通配位 → 自由槽计数；自由文本 `otherRequirements` → 结构化数字；`reqCombinedLevel=0` / `fleetMinLevel=1` 表示「无合计等级要求」→ `None`；`AnyFleetCompo` 多套编成 → `composition_unstructured` 并判 UNKNOWN；上游「满补给」布尔 → 本项目保留缺失时为 UNKNOWN 的三态。

**不应引入**：自动选船/换船、自动装鼓桶、舰船池、按收益排序或随机选远征（属 R3-J 且规格禁止）、用 `api.api.update_from_api()` 主动构造/读取游戏请求、`choice()` 式无校验派遣、上游自有的舰种 ID 空间、把游戏内执行层绕过。

---

## 7. 本项目静态规则库

- 文件：`src/kancollebot/expeditions/data/expedition_requirements.json`（`schema_version=r3i-expedition-requirements-1`，`rule_version=2026-11-05.1`）。
- 47 条规则：42 条来自 kcauto_custom `develop@18d61ab`，5 条（A2–A6 / id 101–105）来自 poi-plugin-ezexped `world-1.es`（含合计等级与属性合计）。A1（id 100）沿用 kcauto_custom，与 ezexped `fslSc(5,4)+LevelSum(10)+DDorDE:3` 一致。
- 名称/编号**以仓库内** `DEFAULT_CATALOG` 为准，加载时逐条校验 `mission_id ↔ catalog_number ↔ catalog_name`，防止规则表与目录漂移。
- 每条规则带 `evidence_class`、`confidence`（`cross_checked` / `single_source`）和 `source_refs`；可信度低不等于可信，只是不隐瞒。
- 规则库是**静态参考资料**，不是实时状态；实时状态一律来自 Poi 只读快照并受 freshness 门约束。

### 未验证 / 明确受限的规则清单

1. 01–08 与 A5/A6 有 wiki 交叉核对；其余 39 条为**单来源**，未按 wiki 逐条复核。
2. A2/A3/A4（101/102/103/104 中的 101–103）的属性合计来自 ezexped 单来源；A2–A6 中 102–105 的编成是「多套等价编成」，本项目标记 `composition_unstructured=True`，一律判 UNKNOWN。
3. `(駆+海防)` 的 DE 代用只对 wiki 明确写出的 4/5/9/24/102–105 启用；其他远征的 DD 组按 DD-only 处理（可能**过严**，只会误拒、不会误放）。
4. 「自由枠なし」只对 37 启用；其余远征默认允许额外舰船。
5. 旗舰舰种限制只收录 24（軽）、32（練巡）、40（軽）；wiki 还列出 43/45/B6/D1/D2/D3/E1 等，本轮未收录，故这些远征**无规则 → UNKNOWN**。
6. 装备与属性合计数据**不在**当前两条只读 CDP 表达式里（`READ_ONLY_FLEET_SNAPSHOT_EXPRESSION` / `READ_ONLY_PORT_SUPPLY_SNAPSHOT_EXPRESSION` 都不含 `api_slot`/装备派生属性）。因此凡规则要求鼓桶或属性，生产路径**绝不会 PASS**：证据缺失时该项为 UNKNOWN；若同时存在其他明确不满足的条件，总判定为 REJECT（两者都零点击封锁）。只有补上可信的装备/属性观测后才可能放行。
7. 41/43–46、B/D/E 系列、月次远征与交战型远征没有规则记录，判 UNKNOWN。
8. 上游数据均为社区整理，非游戏解包；本轮未做实机成功/失败回归来验证规则数值。
