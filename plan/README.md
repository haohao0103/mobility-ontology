# 本体引擎执行计划

> 配套文档：[roadmap/README.md](../roadmap/README.md)
> 总体开发方案：[development-plan.md](development-plan.md)
> 能力基准：[roadmap/palantir-benchmark.md](../roadmap/palantir-benchmark.md)
> 优先级顺序：[palantir-gap-plan.md](palantir-gap-plan.md)（取代本文第 7 节）
> 判断/提议契约：[judgment-proposal-contract.md](judgment-proposal-contract.md)
> 状态：Draft
> 更新：2026-11-19

## 1. 使用方式

- 本文是**执行清单**，路线图和设计原则以 Roadmap 为准；
- 任务以 `P{阶段}-{序号}` 编号，完成后保留记录，不删除；
- 每个任务必须写清：产出物、验收标准（DoD）、依赖；
- 阶段退出标准未满足前，不进入下一阶段；
- 不在此文档中写时间估算，只排优先级和依赖。

## 2. 工作流

| 编号 | 工作流 | 负责角色 | 主要产出 |
|---|---|---|---|
| W1 | 语义元模型 | 本体平台组 + 领域 Owner | Schema、公理、规则 AST |
| W2 | 本体服务运行时 | 平台后端 | search/describe/validate/classify/explain |
| W3 | 推理与规则 | 平台 + 算法 | 规则执行器、推理 Sidecar |
| W4 | 事实接入 | 数据/后端 | MCP Connector、权限、缓存 |
| W5 | Agent 运行时 | 算法/应用 | 检索、规划、证据链、审计 |
| W6 | DevOps/SRE | SRE | CI/CD、灰度、监控、回滚 |
| W7 | 治理与推广 | 产品/架构 | Owner 机制、RFC、培训、场景复制 |

## 3. Phase 0：语义契约与垂直闭环

**阶段目标**：让本体“可评审、可校验、可回归”，并锁定第一个垂直场景。

**场景**：运单费用差异诊断（why 比预估贵）。

### 3.1 任务清单

| ID | 任务 | 产出物 | DoD | 依赖 |
|---|---|---|---|---|
| P0-01 | 确认垂直场景与验收指标 | 场景说明、标注样本、指标定义 | 至少 30 条标注样本；明确准确率/证据覆盖率口径 | 无 |
| P0-02 | 定义元模型 JSON Schema | `schema/semantic_layer.schema.json` | 覆盖 meta/classes/relations/pricing/data_sources/rules/actions；CI 可校验现有 JSON | 无 |
| P0-03 | 补必填元数据 | 更新 [semantic_layer.json](../freight/semantic_layer.json) | 每个类/关系/规则/动作有 `owner`、`source`、`version`、`iri`、`effective_from` | P0-02 |
| P0-04 | 设计规则 AST | AST 规范文档 + Schema | 支持字段、操作符、常量、单位、舍入、边界；不再依赖自由字符串 | P0-02 |
| P0-05 | 迁移 5 条现有规则到 AST | 规则定义 + 执行器 | `R-WAIT-01`、`R-HIGHWAY-01`、`R-WAYBILL-01`、`R-SETTLE-01`、`R-INVOICE-01` 均可执行 | P0-04 |
| P0-06 | 建立规则测试用例 | `tests/rules/*.json` | 每条规则至少 2 正 2 反；CI 全部通过 | P0-05 |
| P0-07 | 新增 `validate` 能力 | `SemanticLayer.validate()` + API | 返回结构化错误：类型、关系、状态、规则、AST、引用完整性 | P0-02 |
| P0-08 | 建立 CI 语义门禁 | CI 配置 | Schema、Domain/Range、AST、规则用例、引用完整性、SHACL 全部阻断式校验 | P0-02/06/07 |
| P0-09 | 生成基线快照 | `releases/freight/v1.1.0.json` | 快照不可变；包含 DSL + 校验结果 + 变更说明 | P0-08 |
| P0-10 | OWL 导出与推理选型 spike | 选型结论 | 对比 HermiT/Pellet 与 Owlready2；确定 Phase 2 方案 | P0-03 |
| P0-11 | 更新开发文档 | 本体编写指南、评审模板 | 新同学可按文档新增一个类/关系/规则并跑通 CI | P0-08 |
| P0-12 | 私有约束导出为标准 SHACL | `tools/export_shacl.py` + `cli.py shacl` | 8 条约束全部映射为 SHACL 形状（类形状内联 + 独立 NodeShape）；pySHACL 空数据图通过、负例被拦截；产物可加载 | P0-07/08 |
| P0-13 | 收口模型可见工具面 | [semantic_layer.json](../freight/semantic_layer.json)、[fact_mcp.py](../freight/fact_mcp.py)、[agent.py](../freight/agent.py) | 从 primitives 移除 `ontology.search`；`ontology.schema` 不再暴露 `closure`，强闭包仅内部开关可用；提示词与工具描述同步；模型可见工具集不含旧闭包入口 | P0-08 |
| P0-14 | Object lookup / Object search | [fact_mcp.py](../freight/fact_mcp.py)、`tests/` | 支持按类型 + 主键/对象 ID 精确读取；支持跨实例自然语言检索并按类型分组，返回 `object_set_id`；语义检索明确标注“暂不支持” | P0-13 |
| P0-15 | Object Set 与 search-around 改造 | `freight/object_set.py`、[fact_mcp.py](../freight/fact_mcp.py) | `search_around` 作为 Object Set 变换；支持 `object_set_id`、`next_page_token`、`link_type_summary`；分页并集与一次性结果一致，不重不漏 | P0-14 |
| P0-16 | Object Type Group 范围与权限 | `SessionScope`、[fact_mcp.py](../freight/fact_mcp.py)、[engine.py](../freight/engine.py) | `allowed_group_ids` 随会话注入；catalog / object_type_search / object_lookup / object_search / object_set / search_around / query / traverse 统一过滤；越权请求有回归测试与审计 | P0-14/15 |

### 3.2 阶段退出标准

- [ ] Schema 校验覆盖全部本体文件；
- [ ] 5 条规则全部可执行且有回归用例；
- [ ] 每个类型、关系、规则、动作都有 Owner 和来源；
- [ ] `validate` 能发现人为注入的 Domain/Range、状态、AST 错误；
- [ ] 私有约束已导出为标准 SHACL，且 pySHACL 在 CI 中阻断不合格形状；
- [ ] 基线快照 `v1.1.0` 已生成并可回滚；
- [ ] 任一 PR 未经 CI 门禁不能合并。

## 4. Phase 1：生产级运行时

**阶段目标**：让本体服务具备上生产的基本条件。

| ID | 任务 | 产出物 | DoD | 依赖 |
|---|---|---|---|---|
| P1-01 | 定义 Connector 接口规范 | 接口文档 + 基类 | 统一鉴权、超时、重试、分页、字段映射、来源标记 | P0-09 |
| P1-02 | TMS 只读 Connector | `freight/connectors/tms.py` | 可查订单、运单、司机、车辆；带来源和时间戳 | P1-01 |
| P1-03 | 计费只读 Connector | `freight/connectors/billing.py` | 可查费用项、计费依据、费用状态 | P1-01 |
| P1-04 | 结算/发票只读 Connector | `freight/connectors/settlement.py` | 可查结算单、发票、冲正记录 | P1-01 |
| P1-05 | 证据与审计接入 | 证据查询、审计日志 | 每个结论可回溯证据 ID、来源、版本、checksum | P1-02/03/04 |
| P1-06 | 替换演示 SQLite | 生产存储/连接池 | 并发压测通过；无全局锁；故障可恢复 | P0-09 |
| P1-07 | 权限模型 | ACL 设计 + 实现 | 类/属性/动作级授权；字段脱敏；拒绝有审计 | P1-06 |
| P1-08 | 缓存与性能 | 缓存层、压测报告 | TBox 读取不成为瓶颈；P95 达到约定 SLO | P1-06 |
| P1-09 | CI/CD 与灰度 | 流水线 | 构建、测试、STG 金丝雀、生产发布、一键回滚 | P0-08 |
| P1-10 | 可观测性 | 指标、日志、Tracing | 能定位一次诊断的每一步耗时、工具、版本 | P1-09 |
| P1-11 | 多域命名空间 | 域注册机制 | `freight` 之外可新增业务域且互不污染 | P0-03 |
| P1-12 | 检索升级评估 | 评估结论 | 判断关键词召回是否足够；不足时接向量召回，接口不变 | P1-08 |
| P1-13 | 类型元数据补 `status` / `interfaces` | [semantic_layer.json](../freight/semantic_layer.json)、[semantic.py](../freight/semantic.py) | 类级增加 `status`、`interfaces`；object type search 默认过滤 deprecated；命中时同时返回匹配接口 | P0-03 |
| P1-14 | 目录预算改 token 计数 | [semantic.py](../freight/semantic.py)、[semantic_layer.json](../freight/semantic_layer.json) | 以模型 token 计预算，字符数作 fallback；全量目录保留短描述与别名；超预算自动切换 search-first | P1-13 |
| P1-15 | 规划器可选 + clarifying + context cleanup | [agent.py](../freight/agent.py) | `AGENT_PLANNER=auto/on/off`；决策协议支持澄清问题与上下文清理；关闭规划器时链路仍可完成 | P0-13 |
| P1-16 | citations 与结果依赖图 | [agent.py](../freight/agent.py)、[web/order-ontology.html](../web/order-ontology.html) | 每个工具结果分配 `result_id`；final 强制返回 `citations`；前端可点击引用回看原始结果 | P0-14/15 |

### 4.1 阶段退出标准

- [ ] STG 全链路使用真实只读事实；
- [ ] 不再手工 scp 发布；
- [ ] 权限矩阵和脱敏可验证；
- [ ] 有明确 SLO 和告警；
- [ ] 单次诊断可完整回溯：本体版本、召回类型、规则、事实、证据、结论。

## 5. Phase 2：形式语义与推理

**阶段目标**：补齐自动分类、隐含知识推导、一致性检查。

| ID | 任务 | 产出物 | DoD | 依赖 |
|---|---|---|---|---|
| P2-01 | OWL/TTL 导出器 | `tools/export_owl.py` | Canonical DSL 可生成合法 OWL/TTL；CI 校验可加载；仅消费 `classes/relations/properties/lifecycle`，**不映射 `rules/actions/constraints`**（算术、触发式校验、执行语义分别留在规则引擎、SHACL、动作模型） | P1-11 |
| P2-02 | 推理 Sidecar | 推理服务 | 支持按域加载；超时/失败降级；不阻塞主链路 | P2-01 |
| P2-03 | 扩展公理 | Schema + 本体 | 支持 `subClassOf`、`equivalentTo`、`disjointWith`、基数约束；**OWL 公理只表达本体恒真约束**，数据完整性（如 C1/C2/C7）仍由 SHACL 承担，开放世界语义下不用于校验缺失事实 | P2-01 |
| P2-04 | 分类与一致性 API | `/tbox/classify`、`/tbox/check-consistency` | 标准用例通过；矛盾模型返回可定位错误 | P2-02/03 |
| P2-05 | 推导解释 | `/tbox/explain` | 返回推导链：公理、事实、结论 | P2-04 |
| P2-06 | 推理影子模式 | 运行配置 + 对比报告 | 影子结果与人工判断对比；不影响线上延迟 | P2-02 |
| P2-07 | 规则 AST 形式语义 | AST 语义规范 | 规则可解释、可校验、可组合；与 OWL 公理边界清晰 | P0-05 |

### 5.1 阶段退出标准

- [ ] 文章中的“条件类自动归属”类用例可在本项目中复现；
- [ ] 模型矛盾能在 CI 或发布前被拦截；
- [ ] 推理失败不影响诊断主链路；
- [ ] 每个推导结论都有解释。

## 6. Phase 3：跨系统语义交换（条件触发）

**触发条件**：出现至少两个系统需要通过共享语义对接，或需要通用 SPARQL 查询。

| ID | 任务 | 产出物 | DoD | 依赖 |
|---|---|---|---|---|
| P3-01 | IRI/命名空间注册表 | 注册表 + 规范 | 全局唯一、可解析、有 Owner | P2-01 |
| P3-02 | 跨系统映射 | `sameAs`/`exactMatch` 映射 | 覆盖核心类与关键字段；冲突有处理规则 | P3-01 |
| P3-03 | SPARQL/虚拟 RDF 选型 | 选型结论 | 明确 Jena/GraphDB/虚拟化方案与成本 | P3-01 |
| P3-04 | 查询端点 | SPARQL 或统一查询 API | 真实场景验证；权限与限流可用 | P3-03 |
| P3-05 | 交换治理 | 版本兼容与评审流程 | 跨域变更可灰度、可回滚、可审计 | P3-02 |

## 7. 当前优先级（Next）

> 本节顺序已由 [palantir-gap-plan.md](palantir-gap-plan.md) 取代，以下保留为历史记录。

按顺序执行，不同时铺开：

1. `P0-01` 场景与标注样本；
2. `P0-02` 元模型 Schema；
3. `P0-03` 元数据补齐；
4. `P0-04` + `P0-05` 规则 AST 与迁移；
5. `P0-06` 规则回归用例；
6. `P0-07` `validate` 能力；
7. `P0-08` CI 门禁；
8. `P0-12` SHACL 标准校验（导出器与门禁接入已完成，待补回归用例）；
9. `P0-13`~`P0-16` 工具面收口、Object API 与对象类型组权限（垂直闭环前置）；
10. `P0-09` 基线快照。

完成以上 10 项后，再启动 `P1-01` Connector 规范。

## 8. Definition of Done（通用）

任何本体或服务变更，满足以下条件才算完成：

- [ ] Schema 与引用完整性校验通过；
- [ ] 规则/推理用例通过；
- [ ] 有 Owner、来源、版本、生效时间；
- [ ] 有变更说明和兼容性评估；
- [ ] 有回滚方案；
- [ ] 涉及权限/敏感字段时完成安全评审；
- [ ] STG 验证通过；
- [ ] 文档同步更新。

## 9. 待决策问题

| 编号 | 问题 | 备选 | 需要谁决策 |
|---|---|---|---|
| Q-01 | Canonical 用 JSON 还是 YAML | 保持 JSON / 迁 YAML | 本体平台组 |
| Q-02 | 推理机选型 | HermiT/Pellet Sidecar / Owlready2 | 平台 + 算法 |
| Q-03 | 各业务域的真实 Source of Truth | TMS / 计费 / 结算 / 主数据 | 数据 Steward |
| Q-04 | 权限模型 | RBAC / ABAC / 混合 | 安全 + 平台 |
| Q-05 | 是否多租户 | 单租户 / 多租户 | 架构 |
| Q-06 | 生产部署形态 | 虚机 systemd / K8s | SRE |
| Q-07 | SLO | 延迟、可用性、错误率 | 产品 + SRE |
| Q-08 | 是否引入向量召回 | 关键词 / 向量 / 混合 | 算法 + 平台 |