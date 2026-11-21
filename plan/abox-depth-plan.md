# 计划：把运单-费用链做深（ABox 覆盖 · 动作实测 · 真实外部连接）

> 状态：Draft（待评审）
> 更新：2026-11-21
> 配套文档：[roadmap/palantir-benchmark.md](../roadmap/palantir-benchmark.md)（§3.5 ABox 覆盖率）、
> [plan/palantir-gap-plan.md](palantir-gap-plan.md)（优先级顺序）、
> [plan/README.md](README.md)（执行清单约定）
> 判据口径：所有数字来自 `runs/freight_facts.db` 与仓库代码实测；本文不写时间估算，只排优先级与依赖

## 0. 为什么做这件事

仓库里的本体已经能**表达**货拉拉的业务语义（500 类 / 1961 属性 / 状态机 / 动作 / 标记 / 变更签字），
但**没被业务证明过**：全量 500 类里只有 15 类有实例（3.0%），485 个零实例类。

Palantir 模式的本体之所以有说服力，不在于定义得多完整，而在于**它是活的**：
每个类背后有真实业务对象、每条链路有真实边、每个动作每天被真实用户执行。

本计划只做一件事：**把运单-费用这条主链路做深**，并把"做深了没有"变成**可自动判定的门禁**。

## 1. 目标与验收判据

### 1.1 深水区定义（本体声明，不写死在代码里）

深水区 = **有 `data_sources` 声明 ∪ 有迁移动作 ∪ 有状态机声明** 的对象类型集合。
当前实测为 **18 类**：

```
Order · Waybill · Driver · Vehicle · Shipper · Cargo · Stop · Track
Quote · QuoteItem · PricingRule · FeeItem
Settlement · Invoice · ExceptionTicket · Evidence
Carrier · SubcontractOrder
```

### 1.2 六个验收判据（全部可自动判定）

| 编号 | 判据 | 阈值 | 现状（2026-11-21 实测） |
|---|---|---|---|
| G1 | 深水区类实例覆盖 | **18/18 = 100%** | 15/18 = 83%（缺 `Carrier` / `Stop` / `SubcontractOrder`） |
| G2 | 深水区类边覆盖 | 每个类 **≥1 入边 + ≥1 出边** | 21 个谓词有边（503 个链接类型中），逐类检查未做 |
| G3 | 动作实测覆盖 | **15/15** 动作各有 ≥1 条 `APPROVED` 审计 | **3/15**（`accept_order` / `dispute_charge` / `post_settlement`） |
| G4 | 缺口显式化 | 多源取数未覆盖的费用进入 `unresolved_ids` 并落成 finding | 已有（`collect_fee_sources`），缺门禁固化 |
| G5 | 外部连接 | ETC 连接器**走真实 HTTP 边界**（非进程内函数调用） | 未做（当前从计费中心同一批索引按 `source_system` 分流） |
| G6 | **发版门禁** | 门禁第 26 项：G1/G2/G3 任一不达标 → `exit≠0` | 无 |

### 1.3 全量口径（记录用，不做门槛）

全量 500 类 ABox 覆盖 3.0%（48 实例 / 15 类）。**本计划不要求提升全量覆盖**——
485 个零实例类里有大量是"建模完备但业务未启用"的类型，强行造数据只会制造假象。
门槛只约束深水区 18 类。

## 2. 现状基线（实测）

| 维度 | 现状 | 证据 |
|---|---|---|
| 深水区实例 | 48 个对象 / 18 类（15 类有实例） | `runs/freight_facts.db` |
| 深水区缺实例 | `Carrier`、`Stop`、`SubcontractOrder` | 同上 |
| 边实例 | 67 条，21 个谓词有边 | `relations` 表 |
| 动作审计 | 3 条 `APPROVED` | `audit` 表 |
| 种子数据来源 | 手写于 `freight/engine.py::seed()`（54 处 `add_object`/`add_relation`），主剧本：1 船东 / 1 订单 / 1 运单 / 1 车 / 1 司机 / 7 费用 / 7 报价明细 / 7 规则 / 7 证据 / 1 结算 / 1 发票 / 1 异常 | 代码 |
| STG 纪律 | 验收一律在 `stg-121:8765`，禁止本地起服务 | `AGENTS.md` |

## 3. 任务清单

### Phase A：把判据做成门禁（先有尺子，再有数据）

| ID | 任务 | 产出物 | DoD | 依赖 |
|---|---|---|---|---|
| P0-1 | 深水区清单**本体化** | `freight/semantic_layer.json` 新增顶层 `abox_targets`（`core_types` / `required_predicates` / `required_actions`），由 `data_sources` / `actions` / `state_machines` 派生并可覆写 | 清单在本体 JSON 里可查；派生逻辑有单测；改一个类归属会改变清单（承重） | — |
| P0-2 | 门禁第 26 项 `check_abox_coverage` | `scripts/semantic_gate.py` 新增检查：① 深水区类无实例 → `ABOX_CLASS_NO_INSTANCE`；② 类无入边/出边 → `ABOX_EDGE_MISSING`；③ 动作无 `APPROVED` 审计 → `ABOX_ACTION_UNEXERCISED`；④ 含 3 条负向用例（删实例 / 删边 / 删审计必须被抓） | 门禁 26 项 / 15 覆盖目标；负向用例漏拦即 `ABOX_NEGATIVE_LEAKED` | P0-1 |
| P0-3 | 覆盖率报告落盘 | 门禁输出附 `abox_coverage` 段（类级/边级/动作级覆盖率 + 缺口清单） | `logs/` 下能读到逐类缺口清单 | P0-2 |

### Phase B：把数据做深（剧本，不是随机造数）

| ID | 任务 | 产出物 | DoD | 依赖 |
|---|---|---|---|---|
| P1-1 | 种子剧本补齐深水区 | `freight/engine.py::seed()` 扩写：新增承运商（`Carrier`）、装卸点（`Stop` ×2）、分包单（`SubcontractOrder`），并接上对应边（分包→运单、站点→运单、承运商→分包） | 深水区 18/18 有实例（G1 达标）；新对象的 `status` 落在各自状态集内；seed 回放无异常 | P0-1 |
| P1-2 | 动作全量剧本 | `tests/scenarios/` 新增"15 个动作逐个真实执行"剧本：每个动作走完 `from_status → to_status`，产生 `APPROVED` 审计 | G3 达标（15/15）；动作后对象状态与 `state_machines` 声明一致；被规则阻断的动作单独留 `REJECTED` 记录 | P1-1 |
| P1-3 | 边覆盖补齐 | 主链路谓词补齐边：每个深水区类 ≥1 入边 + ≥1 出边 | G2 达标；边端点类型符合 `domain`/`range`；`cardinality` 校验通过 | P1-1 |
| P1-4 | 缺口用例 | 构造"费用类型无人认领"的场景 | `unresolved_ids` 非空 → 引擎落成数据缺口 finding（G4 达标） | P1-1 |

### Phase C：把外部连接做真

| ID | 任务 | 产出物 | DoD | 依赖 |
|---|---|---|---|---|
| P2-1 | 连接器边界改造 | `fact_mcp.list_fee_lines` 改为**经 HTTP 取数**（`data_sources` 声明的 `tool` 对应真实端点），保留进程内实现作为降级但**默认走 HTTP** | 集成测试断言请求真实发出（mock server 收到请求）；失败时进 `unresolved_ids` 而不是抛异常 | P1-4 |
| P2-2 | ETC 网关接入 | 见 §5 决策点 D2 | 契约测试通过：口径越界 / 缺证据 / 数据过期 / 超时 四类异常各有断言 | P2-1 |
| P2-3 | 故障注入 | 连接器超时/5xx/脏数据 的降级行为 | 降级路径有审计与缺口记录；无静默吞错 | P2-1 |

### Phase D：收口

| ID | 任务 | 产出物 | DoD | 依赖 |
|---|---|---|---|---|
| P3-1 | 矩阵与文档同步 | `roadmap/palantir-benchmark.md` §3.5 更新实测数字；`CHANGELOG.md` 新版本条目；`README.md` 纪律红线新增第 9 条"核心域 ABox 覆盖率不达标不得发版" | 文档数字与实测一致（可复算） | P0-3 / P1-4 / P2-2 |
| P3-2 | STG 全量验收 | STG `stg-121:8765` 部署后跑门禁 + 验收 + smoke | STG 上门禁 26 项全绿；`freight_acceptance` 12/12；smoke 7/7；报告如实写明 STG 结果 | P3-1 |

## 4. DoD 汇总（验收清单）

```
pytest            ≥ 265（新增：覆盖率判据 8 + 动作剧本 + 连接器契约）
semantic_gate     26 项 / 15 覆盖目标 / ok=true
深水区实例         18/18 = 100%（G1）
深水区边           每类 ≥1 入边 + ≥1 出边（G2）
动作审计           15/15 有 APPROVED（G3）
缺口显式化         未认领费用 → unresolved_ids → finding（G4）
ETC 连接器         真实 HTTP 出站（G5），四类异常各有断言
STG 验收           门禁 + 验收 + smoke 全绿（严禁本地替身）
```

## 5. 决策点（需要拍板，阻塞对应任务）

### D1：LLM 走哪个网关？（阻塞 STG 验收与 P3-2）

`AGENTS.md` 写"大模型/Embedding **只连 STG 网关** `http://bdi-gpt-stg.myhll.cn/v1`（`qwen3.6-plus`）"，
但 2026-11-20 在本地 `.env` 配的是 **MiMo 直连** `https://api.xiaomimimo.com/v1`（`mimo-v2.6-pro`）。
两者冲突，必须二选一：

- **选项 A（守纪律）**：STG 部署用 STG 网关 + `qwen3.6-plus`，MiMo 直连仅限本地开发；
- **选项 B（换纪律）**：把 `AGENTS.md` 改为"允许 MiMo 直连"，并确认 STG 侧出网策略允许访问 `api.xiaomimimo.com`。

### D2：ETC 连接器接什么？（阻塞 P2-2）

ETC 无公开 API，三条路：

- **A（推荐，可立即做）**：起一个**本地/测试环境 HTTP 网关模拟 ETC**（走真实 HTTP + JSON 契约），
  证明 `data_sources` 契约能落到网络边界，并配四类故障注入；**必须标注"契约级验证，非生产对接"**；
- **B（最真实）**：接内网 ETC 网关（需地址与凭据，且只能部署 STG）；
- **C（换样板）**：改接一个真实公开 API（如路线/气象服务）证明连接器机制通用，**但不贴 ETC 业务**。

## 6. 风险与边界（诚实写清，不自欺）

1. **造数据 ≠ 业务真实性。** 本计划证明的是"每个类有实例、被关系引用、被动作执行过"，
   **不能**证明"货拉拉真有这些对象"。所有产出物必须标注数据来源（剧本数据 / 生产数据），
   报告里不得把剧本数据的覆盖率说成业务覆盖率。
2. **门禁只看深水区**，不看全量 500 类——否则永远红，且会诱导造垃圾数据。
3. **STG 纪律**：本计划所有验收一律在 `stg-121:8765` 执行；本地只跑单测与承重实验
   （2026-11-21 之前曾在本地起服务做 MiMo 端到端验证，与此纪律不符，已在 §7 记录）。
4. **连接器降级路径**：HTTP 取数失败必须落缺口，不得让"取不到"变成"没有这笔费用"。
5. **剧本扩张的副作用**：seed 数据变大会拖慢每次建库；P1-1 完成后需测量建库耗时，
   超过阈值（建议 3 秒）时把剧本拆成"核心 seed + 可选 fixture"两级。

## 7. 执行记录

| 日期 | 事件 |
|---|---|
| 2026-11-21 | 计划初版；实测基线：深水区 15/18 有实例、边 21 谓词、动作 3/15；全量 ABox 3.0% |
| 2026-11-20 | 修正纪律偏差：曾在本地 `8765` 起服务做 MiMo 端到端验证（`AGENTS.md` 禁止本地起服务做验收），后续一律走 STG |
