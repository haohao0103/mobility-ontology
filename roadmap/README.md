# 货拉拉业务本体引擎 Roadmap

> 状态：Draft（待评审后转为 Active）
> 版本：0.2
> 更新：2026-11-19
> 适用项目：`freight`、`mobility`、`profile_ai` 及后续业务域
> 执行计划：见 [plan/README.md](../plan/README.md)
> 能力基准（优先级高于本文）：见 [palantir-benchmark.md](palantir-benchmark.md)

## 0. 一句话目标

把当前“可执行的轻量业务语义层”演进为货拉拉内部可复用的**本体引擎/语义服务**：

- 对 Agent：稳定提供业务概念、关系、规则、动作和证据边界，降低幻觉；
- 对业务：让费用、结算、异常等规则有唯一、可版本化、可追溯的定义；
- 对研发：用一套语义契约连接 TMS、计费、结算、发票等多套系统；
- 对未来：在确有需要时平滑补上 OWL 形式语义、推理和跨系统交换能力。

## 1. 项目定位

### 1.1 我们做的是什么

一个**面向 Agent 和业务系统的语义层服务**，包含：

1. TBox：类、关系、属性、状态、约束、业务规则、动作、定价知识、数据源映射；
2. 语义 API：检索、描述、校验、解释、分类（逐步补齐）；
3. ABox 接入：通过 MCP / Connector 按需读取真实业务事实，不把事实写入本体；
4. Agent 运行时：按问题召回本体范围，结合事实做诊断并输出证据链；
5. 治理能力：版本、Owner、发布快照、审计、变更评审。

### 1.2 我们不是做什么

- 不是通用图数据库/图计算平台；
- 不是数据仓库、指标平台或主数据系统的替代品；
- 不是一开始就追求全公司统一、全量 OWL DL 的“超级本体”；
- 不是让 LLM 自由生成本体并不经评审直接上线；
- 不是把所有业务计算都塞进推理机。

### 1.3 “本体”的当前定义

当前实现属于**轻量业务本体 / 本体式元模型**：

- 符合“对共享概念化的显式规范”；
- 具备类、关系、Domain/Range、状态、属性白名单、规则、动作、实例分离；
- 但尚未使用 RDF/OWL 形式语义，也没有 OWL 推理机。

因此本 Roadmap 的目标不是“推翻重做”，而是**在保留工程可用性的前提下逐步补齐形式语义**。

## 2. 当前基线（As-Is）

### 2.1 TBox

依据 [freight/semantic_layer.json](../freight/semantic_layer.json)：

| 项目 | 当前规模 | 说明 |
|---|---:|---|
| 类 | 18 | Shipper、Order、Waybill、Driver、Vehicle、FeeItem、Settlement、Invoice、ExceptionTicket、Evidence、Quote、QuoteItem、PricingRule、Track、Cargo、Carrier、SubcontractOrder、Stop |
| 关系 | 28 | 覆盖下单、承运、费用、结算、开票、异常、证据、报价与分包 |
| 规则 | 16 | 等候费、高速费、运单、结算、发票、报价与分包等 |
| 动作 | 15 | 接单、改派、确认装货、完成、争议、减免、挂起、冲正、作废、关闭异常等 |
| 定价知识 | 7 | `pricing` 下的费用类型与规则参数 |
| 数据源映射 | 18 | CRM、TMS、计费、结算、发票、工单、证据、图查询等 |
| 确定性函数 | 4 | `fee_breakdown`、`quote_reconcile`、`evidence_chain`、`action_preflight` |
| 通用原语 | 9 | catalog、object_type_search、schema、search、path、query、traverse、search_around、aggregate |
| 对象类型组 | 5 | order_transport、capacity、pricing、settlement、evidence |

已有能力：

- 类属性白名单与状态枚举；
- 关系 Domain/Range 校验；
- `actions_for` 按类型和状态返回可用动作；
- `rules_for` 按属性条件匹配规则；
- `describe` 输出受控的本体定义；
- `catalog` 在预算内注入紧凑目录，超预算走 `object_type_search` 按业务域缩域召回；
- `object_type_search` 按 ID、名称、描述、别名、业务域召回类型（[semantic.py](../freight/semantic.py)）；
- 已有基于锚点对象的 `search_around`，但缺少跨实例 object lookup/search 与 Object Set；
- `storage_policy: knowledge-only`，本体只存知识，不存事实。

### 2.2 规则与约束

- 规则结构为 `when.all/any` + `judge.formula/explain/severity`；
- 约束目前主要是 Domain/Range、状态枚举、属性白名单；mobility 域另有 8 条私有 Python 约束（[constraints.py](../mobility/constraints.py)）；
- 已新增 [export_shacl.py](../tools/export_shacl.py)，把上述约束导出为标准 SHACL NodeShape，并由 pySHACL（依赖 `rdflib` + `pyshacl`）纳入 CI 语义门禁；
- `formula` 仍是字符串，**没有 AST、没有执行器、没有回归用例**；
- 没有子类、等价类、不相交类、基数约束等 OWL 公理。

### 2.3 ABox 与事实接入

- [freight/fact_store.py](../freight/fact_store.py) 是演示用 SQLite 事实库；
- [freight/fact_mcp.py](../freight/fact_mcp.py) 提供 MCP 风格的事实查询边界；
- [profile_ai/hugegraph.py](../profile_ai/hugegraph.py) 使用 HugeGraph 属性图；
- 事实（对象、关系、证据、事件、审计）与本体知识分离。

### 2.4 Agent 运行时

[freight/agent.py](../freight/agent.py) 已形成：

```
catalog(full_if_fits)（全量紧凑目录，装得下直接注入）
  → 超预算时 object_type_search 按业务域缩域并召回锚点
  → LLM 决策（按需 tbox.describe 补字段/规则）
  → 业务事实查询（MCP）
  → 证据审阅 / 规则匹配
  → 本地汇总输出
```

特点：

- 小本体：全量紧凑目录一次性注入，规模由 token 预算约束；
- 大本体：超预算自动切换 search-first，不把全量灌入作为默认路径；
- 大模型可在诊断过程中主动调用 `object_type_search` 扩大召回；
- 当前缺口：自然语言 → 锚点实例 → search-around 的 Object API 尚未打通；
- 每一步明确区分“调用 LLM”与“本地查询”；
- 输出包含步骤、工具、事实、证据与结论。

### 2.5 服务与部署

- FastAPI 服务入口：[api.py](../api.py)；
- 路由与 web 页面：[freight/api.py](../freight/api.py)、[web/order-ontology.html](../web/order-ontology.html)；
- STG：`stg-121:8765`，systemd 服务 `mobility-ontology.service`；
- 当前部署以手工同步文件 + 重启服务为主。

### 2.6 关键缺口

| 缺口 | 影响 | 优先级 |
|---|---|---|
| 规则公式是字符串，不可执行/不可校验 | 规则可能只是文档，线上无法保证一致 | P0 |
| 没有规则回归用例 | 改本体容易引入静默错误 | P0 |
| 私有约束未标准化为 SHACL | 约束只能在本项目内生效，跨系统校验和工具链复用困难 | P0 |
| 单文件 JSON 无发布快照/Owner/版本审批 | 多人协作后难以追溯和回滚 | P0 |
| 演示 SQLite + 单连接锁 | 不能支撑生产并发 | P0 |
| 缺少跨实例 Object lookup/search 与 Object Set | 自然语言无法落到锚点实例，search-around 链路断开 | P0 |
| 对象类型组未作为强制搜索范围与权限边界 | 目录、类型检索、实例遍历可能绕过授权 | P0 |
| 没有类层级/公理 | 无法做自动分类、一致性检查 | P1 |
| 没有 IRI/命名空间 | 跨系统语义对齐困难 | P1 |
| 没有 CI/CD 与灰度 | 发布依赖人工，风险高 | P1 |
| 没有形式推理 | 隐含知识无法自动推导 | P2 |
| 没有跨系统语义映射/SPARQL | 难以做通用语义交换 | P3 |

## 3. 目标架构（To-Be）

```
┌──────────────────────────────────────────────────────────────┐
│                       使用方                                 │
│   Agent 运行时    业务系统    数据治理    分析师/研发          │
└───────────────────────────┬──────────────────────────────────┘
                            │ REST / MCP / SDK
┌───────────────────────────▼──────────────────────────────────┐
│                      本体服务层                               │
│  search / describe / validate / classify / explain / actions  │
│  权限、租户、缓存、审计、版本路由、灰度                        │
└───────────────┬──────────────────────────────┬───────────────┘
                │                              │
┌───────────────▼──────────────┐  ┌────────────▼───────────────┐
│        语义运行时             │  │        事实接入层           │
│  · 确定性规则执行器（AST）    │  │  · MCP Connector           │
│  · 推理 Sidecar（可选/旁路）  │  │  · TMS / 计费 / 结算 / 发票 │
│  · 分类、一致性、解释         │  │  · HugeGraph / 检索         │
└───────────────▲──────────────┘  └────────────▲───────────────┘
                │                              │
┌───────────────┴──────────────────────────────┴───────────────┐
│                    语义定义与发布层                           │
│  · Canonical DSL（JSON/YAML + JSON Schema）                   │
│  · 生成 OWL/TTL（形式语义产物）                               │
│  · 发布快照、语义 Diff、SemVer、Owner、审批                    │
└──────────────────────────────────────────────────────────────┘
```

### 3.1 五个平面

1. **定义平面**：唯一编写源、元模型 Schema、评审流程；
2. **发布平面**：不可变快照、版本路由、语义 Diff、回滚；
3. **推理平面**：OWL 推理与业务计算分离；
4. **事实平面**：MCP/Connector 按需读取，权限与血缘；
5. **服务平面**：面向 Agent 和业务系统的稳定 API。

### 3.2 双表示、单语义源

- **Canonical**：JSON/YAML DSL，负责编写、评审、运行时快速加载；
- **Derived**：OWL/TTL，由 Canonical 自动生成，用于推理、一致性检查和跨系统交换；
- 禁止在 Protégé 和 DSL 中各维护一份；
- 每次发布同时产出 DSL 快照和 OWL 产物。

## 4. 设计原则

1. **契约先行**：本体定义先过 Schema 和评审，再谈接入。
2. **TBox / ABox 分离**：本体只存知识，事实经 MCP 按需查询。
3. **推理与计算分离**：OWL 负责分类和一致性，规则执行器负责费用与状态计算。
4. **预算内按需上下文**：小本体在 token 预算内注入全量紧凑目录（只含类型、关系端点，不含实例）；超预算强制 search-first，先检索再 describe。
5. **可解释**：任何结论都能回溯到类型、规则、事实、证据和版本。
6. **垂直闭环优先**：先做透一个业务场景，再横向复制。
7. **渐进式形式化**：先补约束和规则语义，再上 OWL 推理，最后考虑 SPARQL。
8. **不可变发布**：线上永远运行某个确定版本，不直接改生产文件。

## 5. 阶段路线

### Phase 0：语义契约与垂直闭环

**目标**：把当前本体从“可演示”提升为“可评审、可校验、可回归”。

交付物：

- 元模型 JSON Schema；
- 类/关系/属性/规则/动作的必填元数据：`owner`、`source`、`version`、`iri`、`effective_from`；
- 规则 AST 规范与 5 条现有规则的迁移；
- 每条规则的正反测试用例；
- `validate` API / CLI；
- CI 语义门禁；
- SHACL 约束导出（[export_shacl.py](../tools/export_shacl.py)）与 pySHACL 标准校验纳入 CI；
- 基线版本快照 `v1.1`；
- OWL 导出与推理机选型 spike。

退出标准：

- Schema 校验和 CI 全部通过；
- 所有规则可执行、可测试；
- 所有类型有 Owner 和来源系统；
- 现有私有约束已导出为标准 SHACL，pySHACL 在 CI 中阻断不合格形状；
- 不依赖人工检查即可发现 Domain/Range、状态、规则错误。

### Phase 1：生产级运行时

**目标**：从演示服务变成可上生产的内部服务。

交付物：

- 真实只读 Connector：TMS、计费、结算、发票、证据；
- Object API 生产化：object type search / lookup / object search / object set（P0-13~P0-16 先完成演示实现，Phase 1 接真实 Connector 并补分页与权限；semantic search 暂缓）；
- Object Type Group 作为搜索范围与权限边界，会话级 `allowed_group_ids`；
- 结论级 citations / result_id 与结果依赖图；
- 用连接池/独立存储替代演示 SQLite；
- 无状态 TBox 服务、缓存、限流、超时、重试；
- 类/属性/动作级权限与字段脱敏；
- 审计与调用链；
- CI/CD、STG 金丝雀、回滚；
- 多业务域命名空间。

退出标准：

- 不需要手工 scp 即可发布；
- STG 全链路稳定，具备 SLO；
- 权限矩阵可验证，越权检索 100% 拦截并留审计；
- 事实查询有来源、时间戳和版本。

### Phase 2：形式语义与推理

**目标**：补齐文章中的自动分类、隐含知识推导和一致性检查。

交付物：

- OWL/TTL 导出器；
- 分层边界：OWL 只承载名词、关系与恒真公理；算术、触发式校验、执行语义分别由规则引擎、SHACL、动作模型承担，不硬塞进 OWL DL；
- 推理 Sidecar（HermiT/Pellet 或 Owlready2）；
- 支持 `subClassOf`、`equivalentTo`、`disjointWith`、基数约束等公理；
- `classify`、`check-consistency`、`explain` API；
- 推理结果缓存与推导依据；
- 影子模式运行，不阻塞主链路。

退出标准：

- 标准推理用例全部通过；
- 矛盾模型能在 CI 被拦截；
- 推理失败不影响主链路可用性；
- 推导结论可解释。

### Phase 3：跨系统语义交换

**目标**：让本体成为多系统之间的语义契约。

交付物：

- IRI/命名空间注册表；
- 与 TMS、计费、结算字段的映射；
- 必要的 SPARQL 端点或虚拟 RDF 层；
- 跨域本体对齐与版本兼容策略；
- 外部交换的治理流程。

退出标准：

- 至少两个系统通过共享语义完成对接；
- 映射覆盖率、冲突处理、版本兼容可验证；
- 通用查询能力经过真实场景验证。

> Phase 3 是**条件触发**：没有真实跨系统交换需求时，不为了“标准”而引入完整 RDF/SPARQL 栈。

## 6. 里程碑

| 里程碑 | 内容 | 通过条件 |
|---|---|---|
| M0 | 语义契约基线 | Phase 0 退出标准全部满足 |
| M1 | 费用诊断垂直闭环 | STG 上使用真实只读事实完成端到端诊断；依赖 P0-13~P0-16（Object API 与范围权限） |
| M2 | 生产加固 | 权限、审计、SLO、CI/CD 全部就绪 |
| M3 | 形式推理影子上线 | 推理旁路运行，分类/一致性用例通过 |
| M4 | 跨系统语义交换 | Phase 3 条件触发并通过验收 |

## 7. 度量体系

| 维度 | 指标 | 目标 |
|---|---|---|
| 语义质量 | 有 Owner/来源/版本的类与关系占比 | 100% |
| 规则质量 | 有正反用例的规则占比 | 100% |
| 业务效果 | 标注集上的诊断准确率 | 持续提升并设基线 |
| 可解释性 | 结论带证据的比例 | 100% |
| Agent 检索 | 工具结果可引用率（citations / 事实类结论） | 100% |
| 上下文成本 | 单次诊断目录 token | 在预算内，不随本体线性增长 |
| 权限 | 越权检索拦截率 | 100%，且留审计 |
| 性能 | P95 延迟、单次诊断 token 数 | 设 SLO，上下文不随本体线性增长 |
| 稳定性 | 本体相关线上错误数 | 0 严重错误 |
| 交付效率 | 语义变更从提交到 STG 的时长 | 持续缩短 |
| 可回滚 | 语义版本回滚耗时 | 分钟级 |

## 8. 治理机制

### 8.1 角色

- **本体平台组**：元模型、服务、推理、CI/CD；
- **领域 Owner**：某个业务域本体的正确性和演进；
- **数据 Steward**：字段口径、来源系统、权限；
- **Agent/应用团队**：消费语义 API，反馈召回与解释问题；
- **SRE/安全**：部署、SLO、权限、审计、合规。

### 8.2 变更流程

1. 提 RFC/PR，说明动机、影响范围、兼容性；
2. Schema 与规则测试自动跑；
3. 领域 Owner 评审；
4. 生成发布快照与语义 Diff；
5. STG 金丝雀；
6. 生产发布与回滚预案。

### 8.3 版本策略

- 本体版本采用 SemVer；
- 破坏性变更必须提供迁移说明和过渡期；
- 废弃项先标记 `deprecated`，再移除；
- 线上按版本路由，支持灰度。

## 9. 关键决策（ADR）

| 编号 | 决策 | 理由 |
|---|---|---|
| ADR-001 | JSON/YAML DSL 作为唯一编写源，OWL 由构建生成 | 避免双份维护，保留工程效率 |
| ADR-002 | TBox 只存知识，ABox 经 MCP 读取 | 事实高频变化，且分散在业务系统 |
| ADR-003 | OWL 推理与业务计算分离 | 费用公式需要确定性执行，不适合做本体推理 |
| ADR-004 | 暂不全量引入 RDF/SPARQL | 当前核心诉求是 Agent 语义与规则校验 |
| ADR-005 | 小本体全量紧凑目录 + 大本体 search-first | 控制上下文；全量目录只含类型与关系端点，不注入实例 |
| ADR-006 | 发布快照不可变 | 保证可追溯、可回滚、可审计 |
| ADR-007 | Object API（type search / lookup / object search / object set）为 Agent 一等原语 | 自然语言必须先落到锚点实例，才能做 search-around 与证据链 |
| ADR-008 | Object Type Group 同时是搜索范围与权限边界 | 防止目录、类型检索、实例遍历绕过授权 |

## 10. 风险与对策

| 风险 | 对策 |
|---|---|
| 本体过度建模、业务不用 | 垂直闭环 + 领域 Owner + 以诊断效果验收 |
| 规则字符串歧义 | AST + 单测 + 版本化 |
| 推理机性能拖慢主链路 | 旁路/影子模式，仅对需要的类型推理 |
| LLM 幻觉 | 先查本体再回答，结论必须带证据，证据不足时明确拒绝 |
| 数据权限与 PII | 类/属性/动作级 ACL、脱敏、审计 |
| 多团队修改导致漂移 | 唯一编写源、CI 门禁、发布快照 |
| 过早标准化拖慢交付 | 分阶段形式化，Phase 3 条件触发 |
| 绑定单一模型/供应商 | LLM 适配层，语义服务与模型解耦 |
| 全量目录与按需上下文冲突 | 以 token 预算为闸门；超预算强制 search-first，避免上下文膨胀 |
| 伪 Palantir 对齐 | 文档明确“全量 TBox 注入”是本地小本体优化，不是官方原语；对外表述以此为准 |

## 11. 非目标

- 不在 Phase 0/1 引入完整 OWL DL；
- 不把 HugeGraph 改造成 RDF 三元组库；
- 不追求一次性覆盖全公司业务域；
- 不允许未经评审的自动本体学习直接上线；
- 不把本体服务做成通用图查询平台；
- 不做完整 AIP Analyst 替代品：本项目是只读费用诊断 Agent，Ontology SQL、通用函数/动作执行、语义检索、可视化/地图/PDF 均按需触发，不进入 Phase 0/1。

## 12. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-11-18 | 0.1 | 首版，基于当前 freight 实现与内部落地讨论整理 |
| 2026-11-19 | 0.2 | 更新基线规模与 Agent 数据流；修订上下文原则与 ADR-005；新增 ADR-007/008；补充 Object API、Group 权限、citations 交付物与度量 |