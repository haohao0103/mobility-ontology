# Palantir 能力对标矩阵（Ontology 基准）

> 状态：Draft（待评审）
> 版本：0.3
> 更新：2026-11-19
> 基准对象：Palantir Foundry / AIP Ontology
> 适用项目：`freight`、`mobility`、`profile_ai` 及后续业务域
> 配套文档：[roadmap/README.md](README.md)、[plan/README.md](../plan/README.md)

## 0. 本文件的作用

本项目的能力基准是 **Palantir Ontology**，不是 OWL/SHACL 的形式完备性。

- 本文件是**能力基准**，回答“每项能力 Palantir 怎么做、我们现在在哪、该拼装还是自研”；
- [roadmap/README.md](README.md) 是**工程执行路线**，回答“分几个阶段、每阶段交付什么”；
- 两者冲突时，以本文件为准；roadmap 的任务优先级需按本矩阵的差距重新映射；
- 本文件不写工期，只写判断与触发条件。

> 说明：Palantir 的具体行为按公开产品能力整理，逐项复核以官方文档为准；本矩阵每季度重基线一次。

## 1. 判定口径

| 结论 | 含义 |
|---|---|
| 拼装 | 由 2 个以上开源组件 + 少量胶水代码组合实现 |
| 引入 | 采用单个开源组件并做适配，不自研核心 |
| 自研 | 开源生态无可用等价物，必须自己实现 |
| 暂缓 | 明确当前不做，并写清升级触发条件 |

差距分级：**小** = 补齐配置即可；**中** = 需要新增模块但路径清晰；**大** = 无开源等价物或需要新增存储/运行时；**无** = 当前完全缺失。

## 2. 能力对标矩阵

| ID | 能力项 | Palantir 基准行为 | 本项目现状 | 开源候选 | 差距 | 结论 |
|---|---|---|---|---|---|---|
| P-01 | 类型声明与元数据 | Object Type / Property / Link Type 一等公民，含 API 名、主键、标题属性、状态、显示元数据、数据源绑定 | [semantic_layer.json](../freight/semantic_layer.json) 已有 18 类 / 28 关系，含 key、属性白名单、状态枚举；JSON Schema 待 P0-02，Owner 与 iri 待 P0-03 | Neo4j Ontology Graph Spec、LinkML | 小 | **拼装**：二选一作为 canonical，生成 JSON Schema |
| P-02 | 状态、Interfaces 与多态 | 类型支持 experimental/active/deprecated；Interfaces 声明共享属性，多类型实现，可按接口检索 | 类级 `status` / `interfaces` 待 P1-13；`object_type_search` 默认过滤 deprecated 未实现 | LinkML abstract/mixin | 中 | **拼装声明层 + 自研运行时** |
| P-03 | 版本与分支 | 本体版本、分支、变更集、发布与回滚；对象数据侧另有分支 | `freight/releases/` 已有 demo 快照；正式快照与回滚待 P0-09；无分支、无变更集 | TerminusDB（git 式版本）、git + 不可变快照 | 大 | **引入 git/快照 + 自研变更集语义**；不引入 TerminusDB 作存储 |
| P-04 | 对象物化与索引（OSv2） | 对象按 schema 物化，增量索引，多索引（搜索/聚合/地理），索引一致性有保证，对象集合服务直接查索引 | `storage_policy: knowledge-only`，事实经 MCP 现查 SQLite / Connector / HugeGraph；无物化、无索引 | 无等价物；PG + Elasticsearch/Vespa 可拼 | 大 | **暂缓全量 OSv2**；先做轻量对象投影（见 3.1） |
| P-05 | Object Set 与 search-around | Object Set Service：filter / pivot / search-around / aggregate / 分页；对象集合可作为后续操作的输入 | 已有基于锚点的 `search_around`（[fact_mcp.py](../freight/fact_mcp.py)）；`object_set.py`、`object_set_id`、`next_page_token` 待 P0-15；无完整集合代数 | 无统一实现；图库 + 搜索引擎拼装 | 中 | **自研语义 + 拼装存储与检索** |
| P-06 | Action Type 定义（收敛为只读提议） | 参数 schema、必填与类型、submission criteria、表单与规则、权限；Action Type 是一等公民 | 仅有 `actions_for` 按类型与状态返回可用动作（15 个）；`action_preflight` 已有原型；无参数 schema、无提交条件、无统一提议契约 | 无 | 中 | **自研（只读提议）**：声明 + preflight，不做 apply |
| P-07 | Action 应用与副作用 | applyAction / batch apply、前置校验、edits（增删改对象与链接）、副作用（通知 / webhook / 触发器）、action log 审计、幂等 | 当前无 apply、无 edits、无副作用；审计仅有证据/预检的部分能力 | 无本体级 | — | **暂缓**：本项目不做写回，仅保留只读 preflight 与审计 |
| P-08 | Functions | 类型化函数运行时，输入输出绑定本体类型，支持 function-backed actions，供 Workshop / AIP 调用 | 已有 4 个确定性函数（fee_breakdown、quote_reconcile、evidence_chain、action_preflight），未类型化、未注册为运行时 | 无本体专用；任意沙箱/serverless 可承载 | 中 | **自研类型绑定 + 引入沙箱执行** |
| P-09 | 强制访问控制 | markings（组织 / 分级 / 隔舱 / need-to-know）+ 行级 / 列级权限 + 标记沿数据传播继承 + 拒绝审计 | 对象类型组过滤待 P0-16；无 markings、无行级/列级、无传播继承 | OpenFGA（ReBAC）、OPA / Cedar（ABAC/PBAC）；无 marking 传播模型 | 大 | **引入授权引擎骨架 + 自研 marking 传播** |
| P-10 | Lineage | 数据源 → 对象 → 动作 → 消费的全链路血缘，可按对象类型/属性追溯 | `evidence_chain` 与证据/审计部分实现（P1-05）；无系统级 lineage | OpenLineage / Marquez（数据级） | 中 | **引入 OpenLineage 采集 + 自研本体级映射** |
| P-11 | OSDK / Ontology API | 由本体生成类型化 SDK（TS/Python/Java）+ REST/GraphQL API | FastAPI + MCP 工具面；无由本体生成的类型化 SDK | LinkML generators、OpenAPI 生成器 | 中 | **拼装**：canonical schema → 类型与 SDK 生成 |
| P-12 | Agent 工具面 | 对象、动作、函数统一作为 AIP 工具，带权限、可评测、可观测 | `catalog` + `object_type_search` + `describe` + MCP 事实查询 + 证据链已成型（P0-13 收口）；无工具级权限与评测集 | MCP 生态；无本体工具治理 | 中 | **自研**：工具注册、权限、评测、token 预算检索 |
| P-13 | Ontology Manager / 变更治理 | 图形化编辑、审批流、变更预览、影响面分析、冲突检测 | JSON + Git PR + CI（规划中）；无 UI、无影响面分析 | 无等价；Git + CI + schema diff 可拼 | 中 | **拼装 Git/CI + 自研影响面分析**（UI 可暂缓） |

## 3. 三个决定性判断

### 3.1 对象物化层（P-04）：不做全量复刻，先做轻量投影

三个选项：

| 选项 | 内容 | 成本 | 风险 |
|---|---|---|---|
| A | 维持 knowledge-only，事实全部走 MCP 现查 | 最低 | Object Set 无法做跨源集合运算，P95 不可控 |
| B | 轻量对象投影：PG 表按对象类型落投影 + 增量索引，MCP 作为补齐 | 中 | 投影一致性与增量逻辑需自建 |
| C | 复刻 OSv2：对象物化 + 多索引 + 索引一致性保证 | 最高 | 超出当前团队规模，且与 Palantir 正面竞争无意义 |

**建议：先 B，并设升级触发条件**：

- 单类型对象量超过约定阈值；
- 一次诊断涉及跨源 join 超过约定次数；
- Object Set 查询 P95 超出 SLO。

任一触发再评估 C 的部分能力。**A 作为 B 的降级路径保留。**

### 3.2 判断与提议（P-06）：只读提议，排在存储与检索之后

本项目的近期重心是**本体知识怎么存、怎么查、怎么用**（P-04 / P-05）；判断与提议是检索闭环之上的后续增量。

- P-06 收敛为「Action Type 声明 + 提议 preflight」：参数 schema、前置状态/角色、规则阻断、预期影响与风险，产出可复核的 Proposal；
- 提议不产生 edits、不触发副作用、不写业务数据；P-07 的 apply / 批量执行 / 幂等一律暂缓；
- 判断与提议共用表达式内核（CEL / JSON Logic）与规则 AST，输出结构化 Judgment；
- 判断必须绑定证据与时点；证据不足返回 UNKNOWN，而不是替业务下结论。

这是本项目与「通用本体编辑器」拉开差距的地方：**判断可解释、提议可复核**。

### 3.3 强制访问控制（P-09）：引擎可引入，传播必须自研

- OpenFGA 可提供 ReBAC 骨架（对象/类型/关系级授权）；
- OPA / Cedar 可提供策略求值；
- 但 **markings 的继承与传播**（数据源 → 对象 → 属性 → Agent 输出）没有开源实现，必须自研，并作为 P-06 提议校验与 P-12 工具过滤的共同底座。

## 4. 拼装清单汇总

### 4.1 可以拼装或引入（约 30–40% 工作量）

| 组件 | 承担能力 | 接入位置 |
|---|---|---|
| LinkML（G0-03 已选；Neo4j Spec 仅作图投影） | P-01 声明、P-11 SDK 生成 | canonical schema 层 |
| pySHACL + rdflib | 约束校验、CI 门禁 | 已有 [export_shacl.py](../tools/export_shacl.py) |
| OWL 导出 + HermiT/Pellet/Owlready2 | 推理 Sidecar | 对应 P0-10、P2-01 |
| OpenFGA 或 Cedar | P-09 授权骨架 | 权限层 |
| OpenLineage / Marquez | P-10 血缘采集 | 数据接入层 |
| git + 不可变快照 | P-03 版本与回滚 | 发布治理层 |
| PG + 搜索引擎 | P-04 轻量投影、P-05 检索 | 对象投影层 |

### 4.2 必须自研（约 60–70% 工作量，也是差异所在）

1. 判断与只读提议：结构化 Judgment / Proposal、证据要求、表达式内核（P-06）；不含 Action apply（P-07 暂缓）；
2. 规则 AST 与执行器：[semantic_layer.json](../freight/semantic_layer.json) 中 16 条规则仍为字符串公式，AST 只能自定义；
3. Object Set 语义层：集合代数、分页并集、search-around 语义（P-05）；
4. marking 传播与本体级权限绑定（P-09）；
5. Agent token 预算检索与工具治理（P-12）；
6. 变更影响面分析（P-13）。

## 5. 与现有 roadmap 的关系

- roadmap 的 Phase 0–3 仍然有效，但定位为**执行路线**，不再是能力基准；
- 按本矩阵，roadmap 当前优先级需要调整：
  - P0 阶段继续（schema、AST、validate、SHACL、快照），它们是 P-01 的前置；
  - 近期重点是 P-04 / P-05：知识存哪里、怎么查、怎么被 Agent 用，优先于判断与提议；
  - P-06 判断与提议在检索闭环稳定后再做；P-07（apply / 副作用）明确暂缓；
  - P-09 marking 先做读侧过滤，不阻塞检索，且早于 P-06 提议校验。
- 具体的任务重排另行产出 gap-driven 执行清单，不在本文件展开。

## 6. 总评

- 开源生态能覆盖的是**声明层、校验层、推理层、SDK 生成层**，约占总工作量的 30–40%；
- Palantir 真正的护城河是 **OSv2 + Action 运行时 + markings** 三件套的耦合，开源无等价物；
- 本项目不追求复刻 Action 执行：先做知识存储、检索与使用（P-04 / P-05 / P-12），再做只读判断与提议（P-06），P-07 暂缓；
- 结论：**拼装上半身，重点投入知识存储 / 检索 / 使用与安全；判断与提议次之；Action 执行不做，物化按触发条件渐进。**

## 7. 维护规则

- 每季度对照 Palantir 官方文档复核一次，更新版本号与变更记录；
- 每次新增或替换开源组件，必须在本矩阵登记“承担能力 / 接入位置 / 退出成本”；
- 本文件任何结论变更需评审，不允许直接改结论而不留记录。

## 8. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| 0.1 | 2026-11-19 | 首版：建立 13 项能力对标矩阵与三条决定性判断 |
| 0.2 | 2026-11-19 | 明确不做 Action 执行：P-06 收敛为只读提议，P-07 暂缓 |
| 0.3 | 2026-11-19 | 明确近期重点为知识存储 / 检索 / 使用（P-04 / P-05 / P-12），判断与提议降为后续增量 |