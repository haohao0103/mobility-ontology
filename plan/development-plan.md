# 本体引擎落地开发计划

> 状态：Draft（待评审）
> 版本：0.1
> 更新：2026-11-18
> 上游方向：[roadmap/README.md](../roadmap/README.md)
> 任务跟踪：[plan/README.md](README.md)

## 1. 文档目的

本文回答三个问题：

1. 这个本体引擎最终要落成什么样；
2. 从当前代码到目标形态，需要拆哪些模块、定哪些契约；
3. 每个阶段做什么、做到什么程度算完成。

本文不写代码实现细节，也不写时间估算。具体任务编号和优先级见 [plan/README.md](README.md)。

三份文档的关系：

```
roadmap/README.md       方向和边界：为什么做、做到哪、不做什么
        │
        ▼
plan/development-plan.md  总体方案：怎么拆、怎么接、怎么测、怎么发
        │
        ▼
plan/README.md           执行清单：具体任务、依赖、DoD
```

## 2. 落地目标与成功标准

### 2.1 业务目标

- 让 Agent 在费用、结算、异常等场景中“先查业务语义，再做判断”，减少幻觉；
- 让业务规则有唯一、可版本化、可追溯的定义；
- 让新业务域接入本体时，有固定流程，而不是复制一份 JSON；
- 让跨系统（TMS、计费、结算、发票）的语义口径可对齐。

### 2.2 技术目标

- TBox 可校验、可发布、可回滚；
- 规则从“字符串文档”升级为“可执行 AST + 回归用例”；
- ABox 通过统一 Connector 按需读取，不落本体库；
- Agent 上下文规模不随本体总量线性增长；
- 具备生产级权限、审计、监控和灰度能力；
- 在需要时可按域开启 OWL 推理，而不是全量改造。

### 2.3 组织目标

- 每个业务域有明确的语义 Owner；
- 本体变更走 PR/RFC，不直接改生产文件；
- 语义变更能自动发现不兼容；
- 业务方能看到“本体带来的效果”，而不是只看到一堆模型文件。

### 2.4 验收口径

| 维度 | 验收口径 |
|---|---|
| 语义完整性 | 类、关系、规则、动作均有 Owner、来源、版本 |
| 规则正确性 | 每条规则有正反用例，CI 必须通过 |
| 诊断效果 | 在标注集上达到约定准确率和证据覆盖率 |
| 可解释性 | 结论可回溯到类型、规则、事实、证据、本体版本 |
| 性能 | 满足约定 SLO，单次诊断上下文不随本体规模线性增长 |
| 稳定性 | 本体相关严重线上错误为 0 |
| 可运维性 | 发布、灰度、回滚不依赖人工改文件 |

## 3. 总体技术方案

### 3.1 分层架构

```
使用方：Agent / 业务系统 / 数据治理 / 研发工具
                    │
                    ▼
┌─────────────────────────────────────────────┐
│ 本体服务 API 层                              │
│ search / describe / validate / rules /       │
│ classify / consistency / explain / actions   │
│ 认证、授权、租户、缓存、审计、版本路由        │
└───────────────┬─────────────────────────────┘
                │
┌───────────────▼──────────────┐   ┌──────────────────────────┐
│ 语义运行时                    │   │ 事实接入层                │
│ · 模型加载与索引              │   │ · Connector 框架          │
│ · 检索（关键词/向量）         │   │ · TMS / 计费 / 结算/发票  │
│ · 规则 AST 执行器             │   │ · 证据 / 审计             │
│ · 校验器                      │   │ · HugeGraph / 检索        │
│ · 推理适配器（旁路）          │   │ · 权限、缓存、限流        │
└───────────────▲──────────────┘   └────────────▲─────────────┘
                │                                │
┌───────────────┴────────────────────────────────┴─────────────┐
│ 语义定义与发布层                                              │
│ · Canonical DSL（JSON/YAML）                                  │
│ · JSON Schema + 元数据                                        │
│ · OWL/TTL 生成                                                │
│ · 发布快照、语义 Diff、SemVer、审批                            │
└───────────────────────────────────────────────────────────────┘
```

### 3.2 核心设计决策

1. **单语义源、双表示**
   - Canonical DSL 是唯一编写源；
   - OWL/TTL 是构建产物，不允许双份维护。

2. **TBox 与 ABox 严格分离**
   - 本体库只存知识；
   - 事实通过 Connector/MCP 读取，带来源、时间戳、版本。

3. **推理与计算分离**
   - OWL 推理负责分类和一致性；
   - 规则 AST 负责费用、状态、权限等确定性计算；
   - 两者不混在一个执行路径里。

4. **渐进式形式化**
   - Phase 0/1 不引入完整 OWL DL；
   - Phase 2 以旁路方式接入推理；
   - Phase 3 只在真实跨系统需求出现时引入 SPARQL/三元组库。

5. **兼容式演进**
   - 现有 `freight` API 与 Agent 链路保持可用；
   - 新能力通过新增 `/tbox/*` 接口和特性开关逐步切换；
   - 不一次性重构目录和调用方。

### 3.3 运行时数据流

诊断场景：

```
用户问题
  → tbox.search(question, top_k)        本地召回，不调用 LLM
  → LLM 规划需要深入的类型
  → tbox.describe(types)                按需加载定义
  → LLM 决策
  → connector 查询事实（MCP）
  → 规则 AST 评估 / 推理旁路（可选）
  → 证据审阅
  → 本地汇总 + 证据链 + 本体版本
```

约束：

- 每一步都记录耗时、版本、输入摘要和返回摘要；
- LLM 输入中不得出现全量本体；
- 结论必须有证据或明确标注“证据不足”；
- 任何工具失败都不能伪造结果。

## 4. 模块拆分与代码落位

采用“先抽核心、后搬目录”的方式，避免一次性大重构。

| 模块 | 当前落位 | 目标落位 | 职责 |
|---|---|---|---|
| 语义定义 | [freight/semantic_layer.json](../freight/semantic_layer.json) | `domains/freight/ontology/` | Canonical DSL、元数据、规则、动作 |
| 元模型 Schema | 无 | `tbox/schema/` | JSON Schema、AST Schema、发布清单 Schema |
| 模型加载与校验 | [freight/semantic.py](../freight/semantic.py) | `tbox/model.py`、`tbox/validator.py` | 加载、索引、Domain/Range、状态、引用校验 |
| 检索 | `SemanticLayer.search` | `tbox/retrieval.py` | 关键词召回，后续可接向量 |
| 规则执行 | `rules_for` + formula 字符串 | `tbox/rules/` | AST 解析、执行、解释、测试 |
| 发布管理 | 无 | `tbox/release.py` | 快照、Diff、SemVer、回滚 |
| 本体 API | [freight/api.py](../freight/api.py) | `tbox/api/` + 兼容路由 | `/tbox/*` 接口、兼容现有 `/freight/*` |
| 事实接入 | [freight/fact_mcp.py](../freight/fact_mcp.py)、[freight/fact_store.py](../freight/fact_store.py) | `tbox/connectors/` + `domains/freight/connectors/` | 统一 Connector、权限、缓存、来源 |
| Agent | [freight/agent.py](../freight/agent.py) | `domains/freight/agent/` | 检索、规划、决策、证据链、审计 |
| 推理 | 无 | `tbox/inference/` | OWL 导出、Sidecar 适配、分类、一致性 |
| Web | [web/order-ontology.html](../web/order-ontology.html) | 保持 | 展示推理链与本体检视 |

迁移原则：

- 新代码优先落在 `tbox/`，旧代码通过适配器调用；
- 不在 Phase 0/1 大规模移动文件；
- 目录调整必须与业务改造解耦，单独提交、单独验证。

## 5. 关键契约

### 5.1 TBox 元模型 v2

在现有结构上补充以下字段，所有对象统一适用：

| 字段 | 必填 | 说明 |
|---|---|---|
| `iri` | 是 | 全局唯一标识，如 `hl://freight/Class/Waybill` |
| `owner` | 是 | 业务或技术 Owner |
| `source` | 是 | 口径来源系统或文档 |
| `version` | 是 | 该对象首次引入的版本 |
| `effective_from` | 是 | 生效日期 |
| `deprecated` | 否 | 是否废弃及替代项 |
| `subClassOf` | 否 | 父类，Phase 2 使用 |
| `equivalentTo` | 否 | 等价类，Phase 2 使用 |
| `disjointWith` | 否 | 不相交类，Phase 2 使用 |
| `constraints` | 否 | 基数、枚举、范围等结构化约束 |

类、关系、属性、规则、动作、定价知识、数据源映射都必须携带 Owner 和来源。

### 5.2 规则 AST

目标是把现有 `formula` 字符串改成可解析、可校验、可测试的结构。

示例：

```json
{
  "id": "R-WAIT-01",
  "owner": "freight-pricing",
  "when": {
    "all": [
      { "field": "fee_type", "op": "eq", "value": "waiting" }
    ]
  },
  "calc": {
    "target": "actual_amount",
    "unit": "CNY",
    "rounding": "none",
    "expression": {
      "op": "mul",
      "args": [
        {
          "op": "ceil",
          "args": [
            {
              "op": "div",
              "args": [
                {
                  "op": "max",
                  "args": [
                    0,
                    {
                      "op": "sub",
                      "args": [
                        { "field": "waiting_minutes" },
                        { "field": "free_wait_minutes" }
                      ]
                    }
                  ]
                },
                5
              ]
            }
          ]
        },
        2.4
      ]
    }
  },
  "explain": "等候费按每 5 分钟 2.4 元计，前 30 分钟免费。",
  "severity": "HIGH"
}
```

要求：

- 操作符白名单，禁止任意代码执行；
- 字段必须存在于对应类型的属性白名单；
- 单位、精度、舍入规则显式声明；
- 每条规则至少 2 个正例、2 个反例；
- 规则结果包含中间值和解释，便于审计。

### 5.3 本体服务 API

| 接口 | 阶段 | 用途 |
|---|---|---|
| `POST /tbox/search` | Phase 0 | 按问题召回 top-k 类型与费用类型 |
| `POST /tbox/describe` | Phase 0 | 按类型返回字段、关系、状态、动作、规则摘要 |
| `POST /tbox/validate` | Phase 0 | 校验对象属性、关系、状态、规则引用 |
| `POST /tbox/rules/evaluate` | Phase 0 | 执行规则 AST，返回结果与解释 |
| `GET /tbox/actions` | Phase 0 | 按类型和状态返回可用动作 |
| `GET /tbox/meta` | Phase 0 | 返回本体版本、域、校验状态 |
| `POST /tbox/classify` | Phase 2 | 基于公理和事实做分类 |
| `POST /tbox/check-consistency` | Phase 2 | 检查模型/事实一致性 |
| `POST /tbox/explain` | Phase 2 | 返回推导链和依据 |
| `GET /tbox/releases` | Phase 1 | 查询发布版本 |
| `GET /tbox/releases/{version}` | Phase 1 | 查询版本清单与校验结果 |

统一响应要求：

```json
{
  "trace_id": "...",
  "ontology_version": "1.1.0",
  "domain": "freight",
  "data": {},
  "error": null
}
```

错误结构：

```json
{
  "trace_id": "...",
  "ontology_version": "1.1.0",
  "error": {
    "code": "ONTOLOGY_VALIDATION_FAILED",
    "message": "关系 domain 不匹配",
    "details": {}
  }
}
```

### 5.4 Connector 契约

所有事实接入统一实现：

```python
class Connector:
    def get_object(self, type_id: str, key: dict, context) -> dict: ...
    def list_relations(self, subject: dict, predicate: str, direction: str, context) -> list[dict]: ...
    def query_evidence(self, subject: dict, context) -> list[dict]: ...
    def health(self) -> dict: ...
```

每个返回对象必须携带：

- `source_system`
- `source_id`
- `occurred_at`
- `version` 或 `updated_at`
- `trace_id`

连接器必须支持超时、重试、限流、缓存和权限上下文；禁止在 TBox 库中写入事实。

### 5.5 发布清单

每次发布生成不可变目录：

```
releases/freight/1.1.0/
  semantic_layer.json
  ontology.ttl
  manifest.json
  validation.json
  checksums.txt
  CHANGELOG.md
```

`manifest.json` 至少包含：域、版本、发布时间、提交号、Schema 版本、校验结果、生成器版本、兼容性说明。

## 6. 分阶段开发计划

### Phase 0：语义契约与垂直闭环

**进入条件**：Roadmap 评审通过；场景确定为运单费用差异诊断。

**范围**：

- 元模型 Schema；
- 元数据补齐；
- 规则 AST 与 5 条规则迁移；
- 规则回归用例；
- `validate`、`rules/evaluate` 接口；
- CI 语义门禁；
- 基线快照 `v1.1.0`；
- OWL 导出与推理选型 spike（只做技术验证）。

**交付物**：

- `tbox/schema/`；
- `tbox/validator.py`；
- `tbox/rules/`；
- 兼容现有 `/freight/*` 的 `/tbox/*` 接口；
- `tests/rules/`；
- CI 配置；
- 基线发布目录。

**退出标准**：

- 现有本体通过 Schema 和引用完整性校验；
- 5 条规则均可执行且有正反用例；
- 人为注入的错误能被 `validate` 稳定发现；
- 每个类型、关系、规则、动作都有 Owner 和来源；
- 基线快照可回滚；
- Agent 仍能跑通现有费用诊断链路。

**演示**：

- 输入“为什么这笔运单比预估贵 38”，展示 `search → describe → 规则评估 → 证据链`；
- 展示一次错误规则在 CI 中被拦截。

### Phase 1：生产级运行时

**进入条件**：Phase 0 退出标准全部满足。

**范围**：

- Connector 框架与真实只读接入；
- 替换演示 SQLite 与全局锁；
- 无状态服务、缓存、限流、超时；
- 类/属性/动作级权限和字段脱敏；
- 审计与调用链；
- CI/CD、STG 金丝雀、回滚；
- 多域命名空间。

**交付物**：

- `tbox/connectors/`；
- `domains/freight/connectors/`；
- 权限与审计模块；
- 发布流水线；
- 监控面板与告警规则；
- 域注册机制。

**退出标准**：

- STG 使用真实只读事实完成端到端诊断；
- 发布不依赖手工同步文件；
- 权限矩阵可测试；
- 单次诊断可回溯到每个工具、每条事实和本体版本；
- 达到约定 SLO。

### Phase 2：形式语义与推理

**进入条件**：Phase 1 稳定运行；出现自动分类或一致性检查的真实需求。

**范围**：

- OWL/TTL 导出；
- 推理 Sidecar；
- 子类、等价类、不相交、基数约束；
- 分类、一致性、推导解释 API；
- 影子模式与性能基线。

**交付物**：

- `tbox/inference/`；
- `tools/export_owl.py`；
- `ontology.ttl` 构建流程；
- 分类/一致性测试集；
- 影子对比报告。

**退出标准**：

- 典型条件类自动归属用例通过；
- 矛盾模型在 CI 或发布前被拦截；
- 推理失败不影响主链路；
- 推理结果可解释。

### Phase 3：跨系统语义交换（条件触发）

**进入条件**：至少两个系统需要通过共享语义对接，或通用 SPARQL 查询成为刚需。

**范围**：

- IRI 注册表；
- 跨系统映射；
- SPARQL 或虚拟 RDF 选型；
- 交换治理与版本兼容。

**交付物**：

- IRI 注册表；
- 映射文件；
- 查询端点（如采用）；
- 跨域发布规范。

**退出标准**：

- 两个系统通过共享语义完成一次真实对接；
- 映射冲突有处理机制；
- 跨域版本兼容可验证。

## 7. 测试策略

| 测试类型 | 范围 | 门禁 |
|---|---|---|
| Schema 测试 | 本体 JSON 是否符合元模型 | 必须通过 |
| 引用完整性 | 类、关系、规则、动作、数据源引用 | 必须通过 |
| 规则单测 | 每条规则正反例、边界值、单位 | 必须通过 |
| 语义回归 | 基线快照对比，发现意外变更 | 必须通过 |
| 检索测试 | 典型问题 top-k 召回 | 必须通过 |
| 契约测试 | `/tbox/*` 请求/响应/错误码 | 必须通过 |
| 集成测试 | Connector 与真实只读数据源 | STG 必须通过 |
| 端到端测试 | 诊断链路、证据链、审计 | STG 必须通过 |
| 推理测试 | 分类、一致性、解释 | Phase 2 必须通过 |
| 性能测试 | P95、并发、缓存命中 | Phase 1 起必须通过 |
| 安全测试 | 越权、脱敏、注入 | Phase 1 起必须通过 |

测试数据原则：

- 规则用例与业务样本分离；
- 标注样本有版本，不随意修改；
- 失败用例保留，作为回归资产；
- 生产数据脱敏后才能进入测试集。

## 8. 发布与环境

### 8.1 环境

| 环境 | 用途 | 数据 |
|---|---|---|
| DEV | 开发自测 | 本地/脱敏样本 |
| STG | 集成验证、灰度 | 真实只读或脱敏数据 |
| PRD | 生产 | 真实数据，严格权限 |

### 8.2 发布流程

```
提交 → Schema/规则/单测/契约测试
     → 构建本体快照与 OWL 产物
     → STG 部署 + 金丝雀
     → 业务验收样本回归
     → 生产灰度
     → 全量
     → 监控观察
```

### 8.3 回滚

- 本体版本与代码版本分离回滚；
- 发布快照不可变，回滚只切换版本指针；
- Connector 故障时降级为“只读缓存/明确报错”，不伪造事实；
- 回滚演练纳入发布检查清单。

## 9. 安全与权限

- 认证：接入内部统一身份；
- 授权：类、属性、动作三级权限；
- 脱敏：手机号、地址、联系人等字段按角色返回；
- 审计：记录谁在什么时间、用什么版本、查了哪些对象；
- 数据最小化：Agent 只拿完成任务所需字段；
- 提示词注入防护：工具返回内容不作为指令执行；
- 供应链：依赖锁定、镜像扫描、密钥不入库。

## 10. 可观测性与 SLO

必须采集：

- 请求量、错误率、P50/P95/P99；
- 检索召回数量、命中原因分布；
- 规则执行次数、失败率、耗时；
- Connector 成功率、超时率、缓存命中率；
- LLM 调用次数、token 消耗、失败率；
- 本体版本分布；
- 诊断结论证据覆盖率。

建议 SLO（具体数值评审确定）：

- 本体查询可用性；
- `search + describe` P95；
- 单次诊断 P95；
- 本体相关严重错误为 0；
- 发布回滚可在分钟级完成。

## 11. 迁移与兼容

- `/freight/*` 现有接口保持兼容；
- 新能力优先新增 `/tbox/*`；
- 通过 `ontology_version` 请求头或参数支持版本路由；
- 旧字段废弃前保留过渡期，并在响应中标记 `deprecated`；
- Agent 逐步从直接依赖 `SemanticLayer` 切换到 `tbox` 客户端；
- Web 页面保持现有协议，必要时增加版本展示。

## 12. 组织与职责

| 角色 | 职责 |
|---|---|
| 本体平台组 | 元模型、服务、规则引擎、发布、推理 |
| 领域 Owner | 本域概念、规则、口径正确性 |
| 数据 Steward | 数据源、字段口径、权限、质量 |
| Agent/应用团队 | 消费 API、反馈召回与解释问题 |
| SRE | 部署、SLO、监控、容量、回滚 |
| 安全/合规 | 权限、脱敏、审计、合规评审 |

协作机制：

- 本体变更 PR + 领域 Owner 审批；
- 跨域变更走 RFC；
- 每阶段结束做一次复盘，更新 Roadmap 和任务清单；
- 业务验收由领域 Owner 签字。

## 13. 风险与应对

| 风险 | 应对 |
|---|---|
| 本体建模过重，业务不买账 | 垂直闭环、按场景验收、Owner 制 |
| 规则迁移引入偏差 | AST + 正反用例 + 新旧结果对比 |
| Connector 不稳定 | 超时、重试、缓存、降级、明确报错 |
| 推理机性能问题 | 旁路/影子、按需加载、超时降级 |
| 权限设计后补 | Phase 1 前置安全评审 |
| 多域互相污染 | 命名空间、域注册、发布隔离 |
| 过早标准化 | Phase 3 条件触发，不预先堆技术栈 |
| 文档与实现脱节 | 每次发布更新 manifest 和开发文档 |

## 14. 待决策问题

| 编号 | 问题 | 决策时点 |
|---|---|---|
| Q-01 | Canonical 用 JSON 还是 YAML | Phase 0 开始前 |
| Q-02 | 推理机选型 | Phase 0 spike 结束 |
| Q-03 | 各域 Source of Truth | Phase 1 Connector 设计前 |
| Q-04 | 权限模型 RBAC/ABAC | Phase 1 安全评审 |
| Q-05 | 单租户还是多租户 | Phase 1 架构评审 |
| Q-06 | 生产部署形态 | Phase 1 发布设计 |
| Q-07 | SLO 具体数值 | Phase 1 上线前 |
| Q-08 | 是否引入向量召回 | Phase 1 检索评估 |

## 15. 阶段验收清单

### Phase 0

- [ ] 元模型 Schema 可校验现有本体；
- [ ] 元数据补齐；
- [ ] 规则 AST 与 5 条规则迁移完成；
- [ ] 规则正反用例全部通过；
- [ ] `validate` 能发现注入错误；
- [ ] CI 门禁生效；
- [ ] 基线快照 `v1.1.0` 可回滚；
- [ ] Agent 现有链路无回归。

### Phase 1

- [ ] 真实只读 Connector 接入；
- [ ] 生产存储替代演示 SQLite；
- [ ] 权限与脱敏可验证；
- [ ] 审计与调用链可追溯；
- [ ] CI/CD、灰度、回滚可用；
- [ ] SLO 与告警生效；
- [ ] STG 端到端验收通过。

### Phase 2

- [ ] OWL/TTL 导出可用；
- [ ] 推理 Sidecar 影子运行；
- [ ] 分类与一致性用例通过；
- [ ] 推导解释可用；
- [ ] 推理故障不影响主链路。

### Phase 3

- [ ] IRI 注册表可用；
- [ ] 跨系统映射完成；
- [ ] 真实对接验收通过；
- [ ] 跨域版本兼容可验证。

## 16. 下一步

在开始编码前，先完成文档评审并确认：

1. 本文的模块拆分和接口契约是否认可；
2. [plan/README.md](README.md) 中 Phase 0 的任务优先级是否需要调整；
3. Q-01、Q-02 两个技术决策是否现在就定；
4. 第一个垂直场景的验收样本由谁提供、如何标注。

以上确认后，再从 `P0-01` 和 `P0-02` 开始实施。