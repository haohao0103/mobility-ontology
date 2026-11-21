# 计划：来源契约收口（口径归属矩阵 · 缺口四场景 · 报价三口径 · 费用类型覆盖）

> 状态：执行中
> 更新：2026-11-21
> 配套文档：[plan/abox-depth-plan.md](abox-depth-plan.md)（ABox 做深计划，Phase C 的上游）、
> [roadmap/palantir-benchmark.md](../roadmap/palantir-benchmark.md)（能力基准，P-10 血缘）
> 判据口径：所有数字来自实跑；不写工期，只排优先级与依赖

## 0. 背景：Phase C 缺的不是功能，是门禁

`d046ae1` 已经把"来源契约"的**机制**做出来了（`freight/engine.py`）：

| 已有能力 | 位置 | 产出 |
|---|---|---|
| 费用类型 × 连接器归属 | `data_sources[].fee_types` + `list_fee_lines` | 连接器不得返回非自己负责的类型 |
| 缺口：无人认领 | `FEE_SOURCE_UNRESOLVED` | 该费用没有可用的源系统连接器 |
| 缺口：口径越界 | `FEE_SOURCE_TYPE_UNDECLARED` | 费用类型不在该来源声明范围内 |
| 缺口：数据过期 | `FEE_SOURCE_STALE` | 源数据时间超过 `freshness_hours` 窗口 |
| 缺口：证据缺失 | `FEE_EVIDENCE_MISSING` | `require_evidence` 要求但没有对应来源证据 |
| 报价三口径 | `QUOTE_TOTAL_MISMATCH` / `QUOTE_SNAPSHOT_DRIFT` / `QUOTE_NOT_FOUND` / `QUOTE_ITEMS_MISSING` | 明细合计 / 报价单总额 / 运单快照三者互校 |

**缺的是把它变成门禁**：目前没有任何检查会问"这个费用类型归谁管""这个类型有没有对应规则和报价明细"。
没有门禁，机制会在下一次改本体时被悄悄绕过——这正是我们这轮反复遇到的问题形态。

## 1. 目标与验收判据

| 编号 | 判据 | 阈值 | 现状 |
|---|---|---|---|
| G1 | **归属矩阵**：定价里的每个 `fee_type` **恰好**被一个 `data_sources` 连接器认领 | 7/7 恰好一个 | 无检查（只有"越界"检查） |
| G2 | **费用类型覆盖**（V5）：每个 `fee_type` 在 FeeItem / PricingRule / QuoteItem **三方各有 ≥1 实例** | 7/7 三方齐 | 实测已达标，但无门禁防退化 |
| G3 | **缺口四场景**：四类缺口各有一条**可触发**的剧本数据与断言 | 4/4 | 机制在，无场景、无断言 |
| G4 | **报价三口径**：一致场景零 finding；造一处不符必须报口径冲突 | 2/2 | 机制在，无断言 |
| G5 | **门禁第 27 项** | 26 → **27 项 / 16 覆盖目标**，含 ≥4 条负向用例 | 无 |

## 2. 现状基线（实测，2026-11-21）

```
pricing 费用类型全集：base / mileage / waiting / highway / loading / insurance / other（7）
连接器认领：TMS(base,mileage,waiting,loading,insurance,other) + ETC(highway)
           → 7 个类型恰好各被认领一次（但没有任何检查在保证这一点）
种子+剧本数据：FeeItem 9 条 / PricingRule 7 条 / QuoteItem 7 条，7 种 fee_type 三方齐全
```

## 3. 任务清单

### Phase C-1：把归属矩阵与覆盖度做成可计算的（判据内核）

| ID | 任务 | 产出物 | DoD | 依赖 |
|---|---|---|---|---|
| P0-1 | 归属矩阵计算器 | `freight/source_coverage.py`：`fee_type_universe()`（全集取 `pricing` 段，权威在本体）/ `fee_type_owners()`（认领关系）/ `check_fee_type_ownership()` | 判据：无认领 → `FEE_TYPE_NO_OWNER`；多认领 → `FEE_TYPE_MULTI_OWNER`；不在定价全集 → `FEE_TYPE_NOT_IN_PRICING`；有单测 | — |
| P0-2 | 费用类型覆盖度量（V5） | 同模块 `measure_fee_type_coverage()` / `check_fee_type_coverage()`：三方实例覆盖 | 缺 FeeItem → `FEE_TYPE_NO_INSTANCE`；缺规则 → `FEE_TYPE_NO_RULE`；缺报价明细 → `FEE_TYPE_NO_QUOTE_ITEM`；有单测 | P0-1 |
| P0-3 | 缺口四场景剧本 | `freight/scenarios.py` 增补：无人认领的费用、越界口径的费用、过期源数据、缺证据的费用 | 四类场景各有可复现的数据构造；有断言 | P0-1 |

### Phase C-2：固化成门禁

| ID | 任务 | 产出物 | DoD | 依赖 |
|---|---|---|---|---|
| P1-1 | 门禁第 27 项 `check_source_contracts` | `scripts/semantic_gate.py` 新增检查：归属矩阵 + V5 覆盖 + 缺口四场景 + 报价三口径 | 门禁 27 项 / 16 覆盖；负向用例漏拦即 `SOURCE_NEGATIVE_LEAKED` | P0-1 / P0-2 / P0-3 |
| P1-2 | 负向用例四条 | ① 删掉一个类型的认领 → `FEE_TYPE_NO_OWNER`；② 给两个来源同时认领 → `FEE_TYPE_MULTI_OWNER`；③ 删掉一类 QuoteItem → `FEE_TYPE_NO_QUOTE_ITEM`；④ 改一处报价明细金额 → `QUOTE_TOTAL_MISMATCH` | 四条分别被抓住 | P1-1 |
| P1-3 | 报价三口径正反断言 | 一致场景（seed 数据）零 finding；改金额场景报口径冲突 | 有单测 | P1-1 |

### Phase C-3：承重与收口

| ID | 任务 | 产出物 | DoD | 依赖 |
|---|---|---|---|---|
| P2-1 | 承重实验 +2 | L20 删认领 → 门禁 FAIL；L21 双认领 → 门禁 FAIL | 只改 JSON、代码零改动、行为翻转 | P1-1 |
| P2-2 | 文档同步 | `CHANGELOG.md` / `README.md` / `roadmap` §3.5 / 本计划执行记录 | 数字与实测一致 | P2-1 |
| P2-3 | **STG 部署与验收** | rsync + systemctl restart + STG 门禁/验收/smoke | STG 上门禁 27 项全绿 | **阻塞**：见 §5 |

## 4. DoD 汇总

```
pytest            ≥ 290（新增归属/覆盖/缺口/三口径用例）
semantic_gate     27 项 / 16 覆盖目标 / ok=true
归属矩阵          7 个 fee_type 恰好各被认领一次（0 漏 0 重）
费用类型覆盖      7/7 三方齐（FeeItem + PricingRule + QuoteItem）
缺口四场景        4/4 可触发并被断言
报价三口径        一致场景零 finding / 不符场景报口径冲突
承重实验          22 条
STG 验收          门禁 + 验收 + smoke 全绿（当前阻塞：主机不可达）
```

## 5. 阻塞项与风险

1. **STG 不可达（阻塞 P2-3）**：2026-11-21 实测 `ssh stg-121` 报
   `Could not resolve hostname stg-121`，仓库与 `/etc/hosts` 均无该主机地址。
   → 本轮所有验证在本地完成；上线前需在能访问 STG 的网络里执行：
   ```bash
   rsync -az --delete --exclude .env --exclude runs/ \
     <repo>/ stg-121:/opt/mobility-ontology/
   ssh stg-121 'sudo systemctl restart mobility-ontology.service'
   curl -s http://stg-121:8765/freight/health
   ```
   另需确认 **STG 出网放行 `api.xiaomimimo.com`**（D1 决策后的 MiMo 直连）。
2. **归属全集的权威性**：`fee_type` 全集取自本体 `pricing` 段。若将来新增费用类型
   却忘了加连接器认领，门禁会立刻报 `FEE_TYPE_NO_OWNER`——这是期望行为，不是误报。
3. **"恰好一个"可能过严**：现实中同一费用类型可能由多个系统共同提供（如 ETC + TMS 都能给高速费）。
   本版按"恰好一个"实现；若将来出现多源共同供给，需改成"至少一个 + 声明优先级"，
   并同步改判据——**改判据时必须同步改门禁负向用例**，不能只放宽实现。
4. **剧本数据 ≠ 生产实况**：缺口四场景是**构造出来的**异常数据，用于证明检测能力，
   不代表生产里真的存在这些异常。

## 6. 执行记录

| 日期 | 事件 |
|---|---|
| 2026-11-21 | 计划初版。基线：归属矩阵无检查、V5 覆盖实测已达标但无门禁、缺口四场景与三口径机制在但无断言 |
| 2026-11-21 | **Phase C-1/C-2/C-3 执行完成**：`freight/source_coverage.py` 落盘（归属矩阵 + V5 覆盖判据）；门禁第 27 项 `check_source_contracts` 接入（4 条负向用例：删认领 / 双认领 / 删报价明细 / 篡改报价金额）；`tests/test_source_contracts.py` 11 项；承重实验 +L20/L21 共 22 条。**实测**：7 个 fee_type 恰好各被认领一次（0 漏 0 重）、7/7 三方齐、报价三口径一致场景零 finding、篡改金额报 `QUOTE_TOTAL_MISMATCH` |
| 2026-11-21 | **P3-3 部署环境**：STG `stg-121` 实测不可达（DNS 解析失败，仓库与 /etc/hosts 均无地址）。经用户授权，本轮改**本地部署测试**并已把例外写进 `AGENTS.md`（仅本轮有效，不得部署 PRD）。本地 `cli.py serve --port 8765` 实测：health / state-machines / state-history / fee-analysis（三口径一致、来源覆盖 7/7 已解析）/ mcp tools / graph / reasoning-chain（MiMo 直连 12 步 finished）全部 200 |
| 2026-11-21 | **未完成 P2-3（STG 部署与验收）**：阻塞于主机不可达。恢复可达后需执行 rsync + systemctl restart + STG 门禁/验收/smoke，并确认 STG 出网放行 `api.xiaomimimo.com` |
| 2026-11-21 | **P0-3 完成（缺口探针数据）**：`freight/scenarios.py::build_gap_cases` 构造四类来源缺口 + 一条 ETC highway 承重探针；门禁第 28 项 `check_gap_detection`（三断言：报得出 / 不误报 / 修好即消失）；承重 +L22/L23 共 24 条。**过程中发现并修掉一个空转断言**：finding 的 `fee_id` 是短 id，断言用了全 id → 「修好即消失」这条从未生效；已统一口径并按费用粒度比较 |
| 2026-11-21 | **P2-2 之外补做**：`freight/agent.py` 加退避重试（只重试 429/5xx/网络抖动/空响应；401/403/400 立即抛；thinking 模式少给一次重试），治上轮观察到的推理链偶发 503。`tests/test_llm_retry.py` 6 项 |
| 2026-11-21 | **本地部署复测**（干净库）：health / state-machines / state-history / fee-analysis（三口径一致、来源覆盖 7/7）/ 动作写回（409 正确拒绝重复申诉，审计 22→23）/ mcp tools 全通。**教训**：上一轮停服务后 8765 有旧进程残留，本轮复测一度打到旧服务（旧库、剧本缺失），误判「剧本没生效」——本地部署测试前必须先确认端口无残留进程 |
