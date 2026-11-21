# Palantir 能力对标矩阵（Ontology 基准）

> 状态：**v3.2 实测基线**（2026-11-20 逐条复核代码，非沿用上版描述）
> 版本：0.4
> 更新：2026-11-20
> 复核方式：每条「本项目现状」都能在代码里指出文件与行；门禁 25 项 / 承重实验 18 条为证
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
| P-01 | 类型声明与元数据 | Object Type / Property / Link Type 一等公民，含 API 名、主键、标题属性、状态、显示元数据、数据源绑定 | **v3.1 已落地**：[semantic_layer.json](../freight/semantic_layer.json) 500 类 / 503 链接 / 1961 属性注册表（类×属性 3527 组合 0 未注册）；每类带 `rid`（不可变）+ `plural_display_name` + `display_metadata`（icon/color/visibility/category）+ `iri` + `owner` + `key` + `statuses`；`id` 承担 API 名语义（不另设 `api_name`，避免三源真相）。**缺**：标题属性（title property）无显式声明；类级生命周期 status（experimental/active/deprecated）无 | Neo4j Ontology Graph Spec、LinkML | 小 | **拼装**：二选一作为 canonical，生成 JSON Schema |
| P-02 | 状态、Interfaces 与多态 | 类型支持 experimental/active/deprecated；Interfaces 声明共享属性，多类型实现，可按接口检索 | **状态机已升为独立层（v3.2）**：顶层 `state_machines` 声明（initial / terminal / external_states），迁移边真源仍在动作 `from_status→to_status`；`SemanticLayer.state_machine()` 出机器图（迁移边带稳定 id `transition:<Type>:<action>`、可达集、外部状态），`validate_state_machines()` 六类完备性判据（来源不明 / 归宿不明 / 终态有迁出边 / 边引用非法状态 / 有动作未声明 / 无动作却声明），门禁第 25 项含 4 条负向用例；REST `/ontology/state-machines`、`/objects/{id}/state-history` + MCP 原语 `ontology.state_machine`。执行侧早已强制（`engine._action_view` INVALID_STATE + 规则阻断 RULE_BLOCKED）。**Interfaces**：显式 `implements` + `property_map` 优先，3 个接口 / 33 个实现，谎报实现由 `IFACE_UNIMPLEMENTED` 拦。**缺**：类级生命周期 status；deprecated 过滤（仅属性级有告警）；标题属性 | LinkML abstract/mixin | 小 | **声明层拼装 + 运行时自研（状态机/接口均已落地）** |
| P-03 | 版本与分支 | 本体版本、分支、变更集、发布与回滚；对象数据侧另有分支 | `freight/releases/` 快照 + checksum 校验（门禁逐个快照跑 schema/规则/SHACL）+ `tools/proposal.py` 分支评审 + `freight/change_gate.py` 破坏性变更分类与逐条签字（`breaking_approvals.json` 16 条）。**缺**：无对象数据侧分支、无变更集、无一键回滚 | TerminusDB（git 式版本）、git + 不可变快照 | 大 | **引入 git/快照 + 自研变更集语义**；不引入 TerminusDB 作存储 |
| P-04 | 对象物化与索引（OSv2） | 对象按 schema 物化，增量索引，多索引（搜索/聚合/地理），索引一致性有保证，对象集合服务直接查索引 | **已落地轻量档（v3.1）**：`freight/projection.py` 属性级行式投影 `object_props` + 数值/字符串双索引 + 增量状态计数；查询引擎用投影收窄候选集后逐行复算；`is_fresh()` 闸门（不新鲜整条回退全表）+ `consistency()` 全量比对。**缺**：跨表物化视图、地理索引、聚合预计算、自适应查询计划 | 无等价物；PG + Elasticsearch/Vespa 可拼 | 大 | **暂缓全量 OSv2**；轻量投影已落地，其余按 3.1 触发条件再上 |
| P-05 | Object Set 与 search-around | Object Set Service：filter / pivot / search-around / aggregate / 分页；对象集合可作为后续操作的输入 | **v3.1 已有**：`freight/object_query.py` Object Set DSL（`from/where/$eq/$in/$gt/$lt/$or` + 字段选择 + 排序 + 分页 + `next_page_token` + `object_set_id`）、`search_around`（多跳 + 关系过滤）、`aggregate`、`ontology.sql` 编译到查询树 IR（对标 Ontology SQL）。**缺**：pivot / 完整集合代数 / 跨源 join | 无统一实现；图库 + 搜索引擎拼装 | 中 | **自研语义 + 拼装存储与检索** |
| P-06 | Action Type 定义 | 参数 schema、必填与类型、submission criteria、表单与规则、权限；Action Type 是一等公民 | **v3.2 已落地**：15 个动作声明 `params`（name/label/type/required，9 个带参）+ `from_status→to_status` 前置状态 + `roles`；执行侧 `engine.execute_action` 校验必填、**按 `type` 校验标量类型**（与属性写入同一份 `SemanticLayer._type_ok`）并**拒收未声明参数**（`PARAM_UNKNOWN`）；`action.plan` 预检返回 blockers 与影响面。**缺**：submission criteria（规则化提交条件）、统一提议契约、表单 | 无 | 中 | **自研**：声明 + preflight 已成；提议契约待补 |
| P-07 | Action 应用与副作用 | applyAction / batch apply、前置校验、edits（增删改对象与链接）、副作用（通知 / webhook / 触发器）、action log 审计、幂等 | **v3.2 更正（上一版此处写「无 apply、无 edits」是错的）**：`freight/engine.py::execute_action/_apply_action` 已实现 apply —— 前置校验（角色 / 状态 / 规则）、edits（属性写回 + 关系与事件写入）、action log 审计（APPROVED / REJECTED 双写 + 失败回滚）、**幂等键 replay**（`audit_by_key`）、证据记录。**缺**：batch apply、跨对象 edits、webhook / 通知 / 触发器等副作用 | 无本体级 | 中 | **自研 apply 已完成**；批量与副作用暂缓 |
| P-08 | Functions | 类型化函数运行时，输入输出绑定本体类型，支持 function-backed actions，供 Workshop / AIP 调用 | **v3.1 已类型化**：4 个确定性函数（fee_breakdown、quote_reconcile、evidence_chain、action_preflight）注册在扩展层 `function_registry`，参数走标量类型校验（与属性写入同一判据）、返回值按 `result_types`（FeeBreakdown / QuoteReconcile / FeeBreakdownBatch）校验缺失/多余/类型（`RETURN_FIELD_*`）；`enhancements/sandbox` 子进程 + timeout + 内存限制；`function-backed action` 已有 | 无本体专用；任意沙箱/serverless 可承载 | 小 | **自研类型绑定 + 引入沙箱执行（已成）** |
| P-09 | 强制访问控制 | markings（组织 / 分级 / 隔舱 / need-to-know）+ 行级 / 列级权限 + 标记沿数据传播继承 + 拒绝审计 | **已落地（v3.1）**：markings 四类 + 行级 `object_security_policies` + 列级 `property_security_policies` + **传播继承**（`marking_trace()`：数据源级 / 类级 → 对象，行级要求持有全部标记）+ `deny_reason()` 拒绝原因 + `guard_output()` Agent 输出闸 + scope token 工具面过滤。**边界**：属性 marking 只管列（置 null），不提升为整行不可见 | OpenFGA（ReBAC）、OPA / Cedar（ABAC/PBAC）；无 marking 传播模型 | 大 | **自研 marking 传播已完成**；授权引擎骨架（OpenFGA/OPA）仍未引入 |
| P-10 | Lineage | 数据源 → 对象 → 动作 → 消费的全链路血缘，可按对象类型/属性追溯 | **v3.2 有推进（2026-11-20 拉取）**：`data_sources` 扩到 20 条，费用类新增**多源连接器契约**（`fee_types` 口径归属 / `freshness_hours` 新鲜度 / `evidence_sources` / `require_evidence`）；`collect_fee_sources()` 按声明逐源认领费用明细，认领不到的进 `unresolved_ids` **缺口显式化**（不静默丢弃）；报价侧 `quote_contains_item` 逐项取数并做**三口径校验**（明细合计 / 报价单总额 / 运单快照），`FeeBreakdown` 契约新增 `quote` / `source_coverage` / `quoted_total_source`；另有 `evidence_chain`、`evidence`/`audit`/`events` 三表与 `usage_graph`。**仍缺**：① 全链路血缘图（数据源 → 对象 → 动作 → 消费）；② 按属性追溯；③ **连接器目前只做口径分流**——TMS/ETC 两个连接器从计费中心的同一批费用索引里按 `source_system` 过滤，没有真实对接外部系统 | OpenLineage / Marquez（数据级） | 中 | **引入 OpenLineage 采集 + 自研本体级映射**；先把一个连接器真接出去 |
| P-11 | OSDK / Ontology API | 由本体生成类型化 SDK（TS/Python/Java）+ REST/GraphQL API | **v3.1 已有**：`tools/gen_osdk.py` 由本体生成 TS + Python 强类型客户端（接基础属性注册表，1961 属性不再退化成 `str`；`decimal→Decimal`）；REST 为 FastAPI + MCP 工具面。**缺**：Java SDK、GraphQL | LinkML generators、OpenAPI 生成器 | 小 | **拼装**：canonical schema → 类型与 SDK 生成 |
| P-12 | Agent 工具面 | 对象、动作、函数统一作为 AIP 工具，带权限、可评测、可观测 | 已成型：本体原语 10 个（catalog / object_type_search / schema / path / query / traverse / search_around / aggregate / sql / **state_machine**）+ 动作 `action.plan` / `action.execute` + 函数；scope token 按令牌裁剪工具面；`guard_output()` 卡 Agent 输出越权。**缺**：工具级评测集与调用观测 | MCP 生态；无本体工具治理 | 中 | **自研**：工具注册、权限、评测、token 预算检索 |
| P-13 | Ontology Manager / 变更治理 | 图形化编辑、审批流、变更预览、影响面分析、冲突检测 | **v3.1/v3.2 治理内核已有**：JSON + Git + CI 门禁（25 项）+ `freight/change_gate.py`（类/属性/类型/基数/状态机变更分类 + 引用影响回溯：函数/规则/动作/数据源/接口/派生类型/OSDK 生成物）+ 逐条签字（`breaking_approvals.json`，路径完全一致才算）+ `tools/proposal.py` 评审 `verdict`；承重实验 18 条（只改 JSON、代码零改动、行为必变）。**缺**：图形化编辑器、审批流 UI | 无等价；Git + CI + schema diff 可拼 | 小 | **拼装 Git/CI + 自研影响面分析**（UI 可暂缓） |

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

**v3.1 落地状态**：传播已自研完成（`enhancements/security.py::marking_trace` /
`guard_output`），声明式配置在 `ontology_extension.json` 的
`data_source_markings` / `class_markings`。OpenFGA / OPA 仍未引入——
当前策略面小（3 标记 / 1 行级 / 2 列级 / 1 数据源），自研成本低于接入成本；
策略条数上到几十条再评估。

### 3.4 状态机（P-02）：迁移边留在动作，声明只补动作说不清的三件事

Palantir 的 Object Type 自带状态机（状态 + 迁移 + 事件），Agent 与工作台都按它渲染。
本项目的动作本来就带 `from_status→to_status`，缺的不是流转本身，而是**流转之外那三件事**：
起点是哪些状态、终点是哪些状态、哪些状态是外部系统写的（我们写不出来）。

因此采用**单真源 + 显式补充**：

- **迁移边真源仍在动作**（不把 `from/to` 复制到第二处，避免二源真相）；
- 顶层 `state_machines` 只声明 `initial` / `terminal` / `external_states`；
- 完备性判据据此检查：状态**来源**说得清（起点 / 有迁入边 / 外部可写）、
  状态**归宿**说得清（有迁出边 / 终态 / 外部可写）、终态不得再有迁出边、
  声明里的状态必须在类的状态集内、有动作必须声明、无动作不许声明；
- 门禁第 25 项 + 4 条负向用例；视图与轨迹经 REST / MCP 暴露。

代价与收益：代价是每类多写一小段声明（当前 8 类）；收益是"这个对象怎么走"从
散落在 15 个动作里的隐含知识，变成可查询、可校验、可渲染的一等公民。

### 3.5 ABox 覆盖率：本矩阵之外最该盯的一项

Palantir 模式的本体之所以有价值，不在于定义得多，而在于**它是活的**：每个类背后有真实业务对象、
每条链路有真实边、每个动作每天在被执行。我们已经把这套"活"的机制都造出来了
（状态机 / 动作写回 / marking 传播 / 变更签字 / 承重实验），但：

| 维度 | 现状（2026-11-21 实测 runs/freight_facts.db） |
|---|---|
| TBox | 500 类 / 503 链接 / 1961 属性 / 20 数据源 —— 定义完整 |
| **ABox（深水区 18 类）** | **实例覆盖 18/18、非孤岛 18/18、动作实测 15/15、迁移一致 21/21、规则分支 16/16**（门禁第 26 项强制） |
| **ABox（全量 500 类）** | 79 个对象实例，18/500 类有实例（**3.6%**），482 个零实例类 —— **门槛只约束深水区**，不逼全量 |
| 关系实例 | 89 条（503 个链接类型里绝大多数没有真实边） |
| 有状态机的类 | 8 个（其余 492 类没有状态流转，属正常）；8 个类的 **15 条迁移边全部被真实走过** |

**v3.3 已把深水区做深并固化为门禁**（第 26 项 `check_abox_coverage`）：
18 类实例覆盖 18/18、非孤岛 18/18、15 个迁移动作全部有 APPROVED 审计、
21 条迁移边与声明一致、16 条规则分支全部被真实判定；覆盖度由 `freight/scenarios.py`
的核心剧本产生（真实执行 15 个动作），不是靠声明凑数。

仍未解决的是**全量口径**：482 个零实例类依然没有业务证据。
结论也要相应修正：**深水区已证明「本体是真的」，全量仍只是「本体能表达」。**
下一步若要推全量，需要的是业务侧的对象来源（系统导出或对接），不是建模。

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

## 6. 总评（2026-11-20 复核后）

- 开源生态能覆盖的是**声明层、校验层、推理层、SDK 生成层**，约占总工作量的 30–40%；
- Palantir 真正的护城河是 **OSv2 + Action 运行时 + markings** 三件套的耦合，开源无等价物；
- **声明层与运行时内核已对齐**：类型三件套（P-01）、状态机（P-02）、动作声明与执行（P-06/P-07）、
  类型化函数（P-08）、markings 传播（P-09）、OSDK（P-11）、治理内核（P-13）；
- **平台件仍缺**：OSv2 全量物化（P-04 轻量档）、Object Set 完备代数（P-05）、
  系统级血缘（P-10）、版本分支与回滚（P-03）、批量动作与副作用（P-07）、工具评测集（P-12）；
- 结论：**能力声明层与运行时已够用，剩余差距集中在 Foundry 的平台工程量，不在本体方法论。**
  下一轮优先级：**P-10 收尾（真接一个外部连接器 + 全链路血缘图）→ P-03 变更集 → P-05 pivot**。
  另有一项比以上都重要、但不属于本矩阵：**ABox 覆盖率**——当前 500 类里只有 15 类有实例（3.0%），
  485 个零实例类意味着这些类「能表达」但「没被业务证明过」。详见 §3.5。

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