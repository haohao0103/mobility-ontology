# Changelog

## 4.0.5 (2026-11-25) — 阶段 1 盲测工具链 + 候选→提案接线器 + 三项目借鉴台账

按 4.0.4 的建议执行：阶段 1 盲测（运力域）+ 候选 JSON → 提议通道自动接线。
另完成 auto-ontology 深挖（用户第二轮提问）与三项目借鉴台账落档。

### 阶段 1 盲测工具链（`tools/blindtest_capacity.py`）
- **输入**：`tools/blindtest_capacity_business_text.md`——运力域业务方原话（人工写的
  口语描述，**不含任何类 id / 英文类名 / 字段名 / 状态名**）。
- **对照**：人工版 `ontology/domains_02_capacity.py`（98 类）。
- **方法**：LLM 按 `tools/modeling_prompt.md`（R1–R10，团队规范那一份，不另存第二份）
  生成候选类清单 → 与人工版逐类对比。
- **口径（分母定义与来源一并落档）**：类召回率 = 匹配类数 / 98（分母=人工版类总数）；
  字段类型准确率 = Σ(匹配类中「两边都有且类型短码相同」属性数) / Σ(匹配类中「两边都有」
  属性数)；属性召回（辅助）= Σ共同属性 / Σ人工属性总数；类匹配 = label 中文精确 → id 精确
  → label 归一化。判据：召回 ≥70%、类型准确率 ≥85%。
- **两种模式都记录**：`single`（整域一次）与 `batch`（按业务方描述的小标题分块），
  取较优者作结论但不隐藏单次上限。原始输出全量落 `tools/_out/blindtest_raw/`（诊断不能
  靠猜）。

### 阶段 1 盲测结果：**不通过**（按预注册规则不进阶段 2）

`domains_02_capacity`（98 类），业务方原话输入，独立 LLM：

| 指标 | 实测 | 判据 | 结果 |
|---|---|---|---|
| 类召回率（类名精确匹配） | **25.5%**（匹配 25 / 人工 98；LLM 产 113 类） | ≥70% | ❌ |
| 字段类型准确率 | **68.0%**（分母 50 条共同属性） | ≥85% | ❌ |
| 属性召回（辅助） | 26.9%（分母 186 条人工属性） | — | — |
| 语义对应率（**诊断口径**，不改判据） | **46.9%**（名称 bigram + 属性集 Jaccard，阈值 0.25） | — | — |

**诊断出的三个差距来源**：①**命名体系不同**（人工是领域约定名 `VehicleCapacity`
核定载重，模型给描述性名 `CapacityApproval` 载量核定记录）；②**粒度更粗**（模型把
"承运商档案/准入/审核/合同/处罚"合并成一个"承运商"类，与 auto-ontology「一表一 Term」
偏好同源）；③**候选格式无迁移动作**（R9 要求状态机完备 → 提案一律
`TRANSITIONS_MISSING`，待人工补；是格式缺口，不是模型能力问题）。

**定位下调**：AI 辅助建模是**起草清单**（省掉从零录入），**不是**能替代人工细粒度
建模的清单生成器。下一杠杆点是**候选对齐检查**（防重复建类 / 提示拆细，借
auto-ontology 孪生识别思路），不是继续调 prompt 追召回。

**过程中的两个真实约束（已写进工具链）**：
- **单次输出装不下一个域**：98 类一次性输出会在 JSON 未闭合处被截断（模型本身守规矩，
  是容量不够）→ 工具链改**两阶段**（A 先类清单 / B 再属性类型），这也是人工建模的顺序。
  首轮 single 模式用 16384 预算跑了 7 分钟仍未收尾（模型无限列举）→ 已放弃该模式。
- **生产 agent 的 `OPENAI_CHAT_STEP_TOKENS=1600`** 对建模场景远远不够；盲测单独放大到
  8192 并在报告里记录该口径。
- MiMo 在 `enable_thinking=False` 时仍偶发把推理写进 content（与 4.0.3 裁决者同款）→
  建模 prompt 增「输出纪律」（首字符必须 `{`、不输出思考过程/围栏）；生效后模型输出
  变为合法 JSON（截断消失，只剩容量问题）。

### 候选 JSON → 提议通道接线器（`tools/candidate_to_proposals.py`）
- 把 `validate_model.py` 的候选 JSON（classes/relations/enums）转成提议通道 payload
  （`class_add`/`relation_add`），**省掉人工转录**（转录正是 R6 类型标注最容易丢的地方）。
- 判定只用**提议通道自己的** `validate_proposal`（不另立一套口径）：可提交进 `ready` 包，
  不过则进 `needs_fix` 包并附**逐条错误码**（缺什么、去哪儿补）。
- 实测（`tools/roundtrip_candidate.json`）：12 条提案 → 4 条可提交 / 8 条待补 /
  3 个 enum 跳过；再过**完整门禁**（apply_to_raw → change_gate → validate_semantic diff）
  同样 4 条通过、8 条被规则层拒绝并留档——"AI 候选 → 接线器 → 提案 → 门禁"闭环成立。
- **发现并如实记录的缺口**：候选 JSON 格式**不含迁移动作**（只有 states/initial/terminal），
  而提议通道要求状态机完备（R9）→ 接线器把 `transitions` 留空并由校验器报
  `TRANSITIONS_MISSING`，**不替人编造迁移边**。这条要靠 prompt 模板补迁移动作或人工补。

### 建模 prompt 强化（`tools/modeling_prompt.md`，团队规范本体）
- 增「输出纪律」硬要求（第一个字符必须 `{`、最后一个必须 `}`、不输出思考过程/解释/
  markdown 围栏、JSON 前后不写文字）。依据实测：MiMo 在 `enable_thinking=False` 时**仍
  偶发把推理写进 content**（与 4.0.3 裁决者同款问题）。

### 三项目借鉴台账（`docs/upstream-ontology-projects-review.md`，新）
- 落档三项目的身份核对、机制证据（带文件:行号）、已落地对应、**明确不搬**的部分与理由。
- **auto-ontology 深挖结论**（用户第二轮提问）：它**有**自动构建能力，但构建的是**目录语义层**
  （Term + 列映射 + 数据边），**不是业务本体**——提示词第 4 条硬令
  "Do not propose IS_A, PART_OF, or ROLE relationships"（`semantic/term_extractor.py:64`），
  一表一 Term、列全分配（`:55,:61`）。故**不能替代**我方建模流程，但给出两条可借鉴路径：
  ①**DB 反向工程作候选来源**（将来接 TMS/ERP 真实库时，走既有提议通道进生产）；
  ②**`term_families` 孪生/镜像边识别算法**（镜像边 + 列集 Jaccard≥0.8 + 证据阈值 +
  同前缀传递扩展）改造成**"新类是否已有同构类变体"检查 + 分类组自动建议**（需从表列集
  改为类属性集+关系端点，不能照抄）。
- **fork 核对**：`/Users/mac/Desktop/apache-code/auto-ontology` 是用户自己的 fork
  （`haohao0103/auto-ontology`，落后上游 2 天），37 处差异**全在 connectors/text-to-SQL 侧**，
  本体/术语侧与上游一致 → 台账结论对 fork 与上游同时成立（引用该副本现状前仍须比对）。

### 验证
- 新增承重测试 10 项（`tests/test_candidate_to_proposals.py`）：转录保真（类型表/why/
  key/group 不丢）、候选缺迁移动作→如实报缺不编造、缺类型/缺 why/未知组/重复类各报对应
  错误码、关系端点与基数校验、往返候选端到端转录。
- 全量 **621 passed / 2 skipped**；语义门禁 32 项全绿。

## 4.0.4 (2026-11-25) — 本体构建通道：结构层提议目录（AI 辅助建模产物进生产的唯一通道）

用户裁定「重点放到本体建模/本体构建模块」后的第一批落地。三项目增量再评估 +
`docs/MODELING_METHODOLOGY.md`（R1–R10 可溯源方法论）+ `tools/validate_model.py`
（原型校验器）入库后，核出并补齐了**AI 候选无进生产通道**这个核心缺口。

### W1 结构层提议目录（`freight/proposals.py`，用户裁定政策 A）
- `MUTATION_CATALOG` 扩三类，全部 **t3（全人审）**：`class_add`（新增对象类型）/
  `relation_add`（新增关系）/ `group_add_type`（既有类挂既有分类组）。
- **目录只开「新增」不开放「删除」**：删除类/关系/规则仍不在目录里，提删除仍被
  `INVALID_MUTATION` 拒绝并留档（4.0.2 的"目录封闭即政策"不变）。
- **why(R10) 必填**：格式 `R编号: 理由`、规则号限 R1–R10、理由 ≥4 字；缺失/
  格式非法/越界分别报 `WHY_MISSING`/`WHY_FORMAT`/`WHY_RULE`/`WHY_TOO_SHORT`。
- AI 候选**永不自动进生产**：`auto_merge_eligible` 恒 False，verdict 恒
  `NEEDS_HUMAN_REVIEW`；采纳永远是「人改 build 源 → 重 build 出新 pin」。

### W2 结构层编译层（`apply_to_raw` 支持三类结构层变异）
- `class_add` → class 条目（rid/iri/展示元数据复用 `ontology` 包推导函数，与 v3
  渲染器同源）+ **property_types 注册**（同名属性合并 `used_by`，类型冲突即拒）+
  **object_type_groups 归属** + **state_machines 声明** + **actions 迁移边**。
- `relation_add` → relation 条目（domain/range/cardinality 必填显式，展示元数据
  按 domain 类属域补齐，boundary 交 v4 映射脚本计算）。
- `group_add_type` → 分类组成员追加。
- **重活交给 `validate_semantic`**：编译出的 after 草案由既有门禁做逐键 diff
  （只认 after 引入的新错误，存量问题不算提案的错）——**单一事实源，不写第二套
  校验口径**。
- 结构层提案的 review 报告**带 after 草案**（人审要看编译后的本体长什么样，
  不是提案原文）。

### W3 结构层裁决合同（`ADJUDICATION_INSTRUCTIONS_STRUCTURAL`）
- 合同按 mutation_type 分流：非结构层用原合同，结构层用新合同。
- 判别重点 R1/R2/R3（AI 最常犯的三错）：**为角色建类**（R2 职责不建对象）、
  **用状态字段糊事件**（R3 事件应建类并声明状态机）、**本该是关系的概念塞成属性**。
- 另含 R6 属性类型、R7 身份、R8 基数、R9 状态机完备、R10 理由实质。

### R9 状态机完备前置校验
- statuses 非空则 `state_machine`（initial/terminal）+ `transitions` 必填；
  每个非终态（非外部态）须有迁出、每个非初态（非外部态）须有迁入
  （`STATE_MACHINE_SINK`/`STATE_MACHINE_ORPHAN`）；迁移边须带 roles
  （对齐发布物 `ACTION_ROLES_INVALID`）。

### 文档修正（核实 `docs/MODELING_METHODOLOGY.md` / `docs/AIGEN_ROLLOUT_PLAN.md` 后）
1. "24 条承重用例" → **32 项语义门禁**（过时数字，三处）。
2. **两套校验的分工**（新增对照表）：`tools/validate_model.py` 是**起草期自检
   工具**（原型、无测试、不挂 CI），`freight/semantic.py::validate_semantic`
   （96 个检查码）才是**权威单一事实源**——同一批约束两处实现，漂移风险已如实
   写进文档，不把原型当准入闸门引用。
3. **v3/v4 关系**澄清：`build_ontology_v3.py` 是唯一渲染器；`build_ontology_v4.py`
   是读 `semantic_layer.json` 再写回的**结构层加工**（限界上下文×聚合×边界链接），
   不是渲染器、不是入口。
4. **原型路径修正**：文档引用的 `本体快速建模_原型/` 目录不存在，实际落盘在
   `tools/`（`validate_model.py` / `roundtrip_candidate.json` /
   `roundtrip_business_text.md` / `modeling_prompt.md`）。
5. 两份文档增补 **AI 候选通道**说明与工作流 Step 5.5（候选先进提议通道，不直接
   进 DSL）。

### 验证
- 新增承重测试 29 项（`tests/test_proposal_structural.py`）：目录与分级/t3 永不
  自动合并/编译产物落到正确段/why 四类错/状态机三类不完备/类型冲突/端点未知/
  基数必填/分组重复/删除仍封闭/结构层合同分流/结构层裁决 veto-keep-retype。
- 更新 `tests/test_ontology_proposals.py` 两条旧断言（目录 4→7 类、t3 语义变更），
  按其**原意**改写（删除仍封闭 + 结构层新增全 t3）。
- 全量 **611 passed / 2 skipped**；语义门禁 **32 项全绿**；lint 干净。
- 过程记录：门禁抓到本轮一处设计错误——曾把 `statuses` 设计为可选，实际发布物
  要求每类状态集非空（`CLASS_STATUSES_INVALID`），已改为必填。**单一事实源当场
  生效**，这正是"编译进 after 草案再由 validate_semantic 判"的价值。

### 未做（诚实记录）
- **阶段 1 盲测**（`docs/AIGEN_ROLLOUT_PLAN.md` §5 第一件事）：需在 STG 跑独立
  LLM 盲测（`stg-121` DNS 当前不可达；本地跑脚本违反 AGENTS.md「脚本运行/验收
  在 STG」），待网络可达后执行。
- **MCP 工具面**注册 `ontology.propose_mutation`：需本体 functions 段同步声明 +
  重 build（本体变更本身要走提议通道评审），鸡生蛋问题，从 HTTP 端点起步。
- `validate_model.py` 与 `validate_semantic` 的规则合并/对齐（文档已如实标注
  两者分工与漂移风险，未假装统一）。

## 4.0.3 (2026-11-25) — 本体变异独立裁决者（ontosmith Pass D 增量落地）

三项目增量再评估后落地（用户裁定"按建议执行"）：上轮 4.0.2 只搬了 pin/引用溯源/封闭
目录/澄清层，本轮深挖 ontosmith `pipeline/adjudication.py` 的**裁决层**与 ontogeny
domains、auto-ontology mcp 等剩余模块，唯一真空白 = 语义裁决层。

### P5 独立裁决者（Pass D，`freight/proposals.py`）
- 借鉴 ontosmith 的"两遍分离"：提议者（Pass C）与裁决者（Pass D）是两个角色，裁决者
  没写候选。确定性门禁全绿后，追加一个独立 LLM 角色审语义。
- 裁决合同 `ADJUDICATION_INSTRUCTIONS`：**针对本目录四类变异定制的子句**（clause 1~4）
  ——术语须真实非臆造且真属于 type_ref、别名须真同义非嫁接、矩阵新行须独立分档不冲突、
  调参须合理量级非荒谬跳动。决策顺序 keep → retype → veto（veto 是最后手段）。
- 确定性不变量 `_enforce_decision`（语义归裁决者、不变量归代码）：
  - keep 锁定原提案（裁决者不能借 keep 改内容）；
  - retype 必须保持 mutation_type 一致（不能发明变更）且重新过 `validate_proposal`，
    不合法 → 降级 veto（`invalid_retype`）；
  - veto 必须带 `retype_attempt`（最接近的合法提案），缺失补 `unconstructible`。
- 裁决不可用 / 输出非法 → **降级人审兜底**（`NEEDS_HUMAN_REVIEW`），不冒称否决、也不
  自动采纳——veto 表达"语义不成立"的判断，裁决者挂了没有任何判断（较 ontosmith 纯
  fail-closed 到 veto 更贴合我们有"人审"层的架构）。
- `review_proposal(proposal, semantic, chat_fn=None)`：`chat_fn` 缺省走纯确定性路径
  （向后兼容）；retype 合法时以修正提案重走确定性门禁（`adjudicated_retyped`）。
- 端点接入：`POST /agent/ontology-proposal` 的 `_adjudicator()` 在 LLM 凭据可用时才
  构造裁决 chat_fn，否则降级纯确定性（网关不可达不阻塞提议通道）。

### P6 veto 强制 retype_attempt（R4）
- `ProposeOutcome` 新增 `retype_attempt` 字段；`normalize_llm_decision` 解析 declined
  时的最接近合法提案；`ProposalLedger` 记录 `retype_attempt` + `adjudication`。
  负信息从"一句理由"升级为"可操作的修正"，与 decline 不蒸发同源。

### 记录在案（未实施，同 P2 断言溯源性质）
- `docs/replay-and-claim-index-design.md`：ontosmith benchmarks 的 replay 复算
  （发布数字从原始证据重算、漂移非零退出）+ claim index（每个数字一行 scope/harness/
  readout）。出现对外发布指标或 fact_store 接真实数据源时启用。

### 不搬（诚实记录）
- ontosmith 八条裁决纪律原文（针对开放三元组抽取，不适用于我们的封闭目录）——改为
  四类变异的定制子句。
- ontosmith 完整晋升门四条件 / queue.py 持久工作队列 / ontogeny ontology.yaml 入口
  清单式结构 / auto-ontology mcp 服务——分别已覆盖（pin 哲学）、不适用（无批处理
  队列）、不搬（表达力未超 semantic_layer.json）。

### 验证
- 新增承重测试 14 项（`tests/test_proposal_adjudication.py`）：裁决 keep/veto（带与
  不带 retype_attempt）/retype（合法、换类型、非法 payload）/不可用与非法降级人审/
  无 chat_fn 纯确定性 / unconstructible 补全 / declined retype 归一化 / 台账不蒸发。
- 全量 582 passed / 2 skipped；语义门禁 32 项全绿。
- 真实裁决 smoke（本地 MiMo 直连）：裁决者 `veto` 了"甩挂→Waybill"——正确识别
  "甩挂"是运输作业方式而非运单术语（命中 clause 1，附 unconstructible 理由）。
  这正是纯确定性门禁会放行（term 合法、type_ref 存在、不重复）但语义裁决能拦截
  的语义错配，证明裁决层价值。
- 排查并修一个现实问题：MiMo 在 `enable_thinking=False` 时仍偶发把推理文本输出到
  `content`（非 JSON）→ 裁决 prompt 强约束（ONLY JSON, no thinking）+ unparseable
  幂等重试一次；仍失败才降级人审。

## 4.0.2 (2026-11-25) — 本体变异提议通道 + 澄清层（auto-ontology / ontogeny 调研落地）

三项目调研第二轮（auto-ontology=NVIDIA、ontogeny=apache/hugegraph-ai、OntoSmith 已于
4.0.1 落地）综合评估后用户裁定"全部落地"，四件：

### P1 本体变异提议通道（`freight/proposals.py`）
- 借鉴 ontogeny evolve（封闭目录 + 后置校验 + 分级 + decline 不蒸发）与 auto-ontology
  （LLM 只提术语，结构性断言留给人——目录分层落地）。
- `MUTATION_CATALOG` 封闭四种：`glossary_add_term` / `class_add_alias`（t0 非结构层，
  门禁全绿=免内容人审）、`matrix_add_row`（t1 人审）、`rule_adjust_param`（t2 人审+灰度，
  动计费数字最重）。t3（删除类/关系/规则）**不在目录里**——目录封闭即政策，LLM 提删除
  被 `INVALID_MUTATION` 拒绝并留档。
- 后置规则层 `validate_proposal`：提议产生**之后**校验目录/payload/target/值域，
  从不假设模型输出合法（ontogeny `validate_llm_decision` 的原则）。
- `review_proposal` 组合门禁：规则校验 → after 草案（深拷贝纯函数）→ `change_gate.review`
  破坏性分类 → `validate_semantic` 新错误逐键 diff（存量问题不算提案的错）→ 分级 verdict。
- `ProposeOutcome` + `normalize_llm_decision`：LLM 输出归一 propose/declined/invalid 三态，
  **decline 分析不得蒸发**（decline_reason 必入台账）。
- `ProposalLedger`：`runs/ontology_proposals.jsonl`，正信息与负信息同等可查。
- HTTP 入口：`GET /ontology/proposal-catalog`（目录总览）、`POST /agent/ontology-proposal`
  （提交提案 → 门禁 → 台账）。
- **发布钉死不变**：提案绝不写 `semantic_layer.json`（after 是未发布物，pin 保持旧值，
  直接加载会被 ONTOLOGY_PIN_MISMATCH 拦下）；采纳永远 = 人改 build 源
  （`tools/build_ontology_v4.py`）重新 build 出新 pin——提案通道绕不过发布。

### P3 覆盖率数值化（`freight/answerability.py`）
- 借鉴 auto-ontology entity coverage：`check_answerable` 返回新增 `coverage` 块——
  问句实体候选（滑窗切词，滤语法字噪声）的认领率 score、`verdict` 三档
  answer/clarify/refuse（不推翻 answerable 布尔；blocking→refuse、score<0.4→clarify）、
  `clarify_questions`（未认领词抛回用户）、`proposal_hints`（未认领词+子串候选类，
  直接喂 P1 提案通道——clarify 与提议闭环）。

### P4 放弃式答案对账（`freight/agent.py`）
- 借鉴 auto-ontology clarify + `_STUCK_PHRASES`：final 步新增 `_stuck_clarify_check`——
  模型给出"无法回答/信息不足"话术时对照确定性可答性：可答却放弃=check 红（应继续
  取证）；不可答则附 clarify_questions 把球抛回用户（不硬答也不装死）+ 提案线索。
  纯确定性不承重（异常静默，增强不得成为新故障源）。

### P2 断言级溯源设计（`docs/assertion-provenance-design.md`，设计记录未实施）
- 自 ontogeny documents 五表移植的蓝图：断言行/删除语义/来源起源/签名去重/溯源三元组/
  原始输出不可变——接真实数据源（TMS+文档混合源）时启用；现在动代码是超前建设。

### 验证
- 新增承重测试 27 项（`tests/test_ontology_proposals.py` 15 +
  `tests/test_answerability_clarify.py` 12）：目录封闭 / 后置校验 / 分级 / 纯内存 /
  decline 不蒸发 / ledger 往返 / coverage 三档 / agent 对账红绿。
- 全量 568 passed / 2 skipped；语义门禁 32 项全绿（含 honesty_scan——本轮 7 处
  DECORATIVE_CLAIM_FIELD 已按"计算式而非常量"整改）。

### 未做（诚实记录）
- MCP 工具面注册 `ontology.propose_mutation`：需本体 functions 段同步声明+重 build
  （本体变更本身要走提议通道评审，鸡生蛋从 HTTP 端点起步）；待下一轮本体发布。
- 多轮 clarify 对话态（跨轮累积 grounded knowledge）：当前 API 单轮，clarify_questions
  随响应返回由调用方呈现；多轮会话形态确定后再落。
- P2 设计的实施（见其文档"启用清单"）。

## 4.0.1 (2026-11-25) — 本体发布钉死 + 结论引用溯源（借鉴 OntoSmith）

调研 `Dongbumlee/ontosmith`（MIT，2026-10 创建）后落地的两项工程借鉴。它的两个核心机制与
本项目短板正好互补：**发布即钉死**（我们的 semantic_layer.json 缺“改了没人知道”）与
**证据随行**（我们的 fact_store 是演示替身、LLM 结论引用无人校验）。

### 发布钉死（payload_sha256 pin）
- `tools/build_ontology_v4.py` 渲染时把内容摘要写进 `meta.payload_sha256`；摘要函数
  `freight.semantic.ontology_pin_digest` 为 build 与加载**单一事实源**，排除 pin 字段
  自身避免自引用。
- `SemanticLayer.__init__`（磁盘加载）逐字节复核，不符 **fail-closed** raise
  `ONTOLOGY_PIN_MISMATCH`：「改了内容却没重新发布」从此不可能静默进到检索/注入/规则链路。
- `from_raw`（demo lab / 改声明看行为的承重测试）只记录 `pin_verified=False` 不抛——
  实验沙箱不是发布通道；parity_lab、semantic_gate 负向用例、test_prevention /
  test_source_contracts / test_state_machine / test_abox_coverage 的“改本体验行为”
  统一改走该通道。
- pin 暴露：`/freight/health` 返回 `payload_sha256` + `pin_verified`；推理链目录步
  resolved 带 `ontology_pin` / `pin_verified`（结论可回溯到本体版本字节）。
- schema 接纳 `meta.payload_sha256`（64 位小写 hex）；门禁 35 项全绿。
- 回归 `tests/test_ontology_pin.py` 8 项：发布物自洽 / 改一字节即拒 / 更新 pin 放行 /
  无 pin 历史版跳过并标注 / from_raw 只记录 / pin 排除自身 / build 端到端自洽。

### 结论引用溯源（quote 逐字校验的工程化版）
- `ReasoningAgent._answer_citation_check`：最终答案里引用的**对象 ID / 证据 ID / 规则 ID**
  必须逐字出现在本次观察语料（observation.result + arguments + fee_analysis 确定性汇总）
  里；编造引用被确定性抓住，红在 final 步的「结论硬引用可溯源」check 行上。
- 金额是软引用（合计/差额是合法派生值）：只列「待复核」不判 fail——不做假精确。
- 回归 `tests/test_answer_citation_check.py` 8 项：命中通过 / 编对象 ID、编规则 ID 被抓 /
  analysis 里的真规则 ID 放行 / 金额软引用 / 空答案 noop / final 步带 check 行 / 编造即红。

### 未做（诚实记录）
- OntoSmith 的 attestation 内容哈希幂等键、manifest↔图对账：数据模型差异大，本轮不搬；
  已评估可搬性（见对话记录），留待接真实语料时再上。
- STG `stg-121` 仍 DNS 不可达，本轮为本地验证。

## 4.0.0 (2026-11-24) — 上下文检索 v4：聚合注入 + 五路召回 + 边界链接不丢

按 `plan/ontology-context-retrieval-v4-plan.md` 执行（D1-D7 用户已批准）。旧 v3 实现整体删除、不留双口径，tag `v3-final` 可回滚。

### 本体声明（M1）
- 6 个限界上下文 × 30 个聚合完整映射 500 类（`tools/build_ontology_v4.py` 可复现，归属表落档 `plan/v4_type_membership.md`）；旧 35 域降级为 UI 多标签，不再参与裁剪与闭包。
- 57 条跨聚合关系显式边界链接（`boundary`）。核对结论：v3 统计的 314 条「跨域关系」里 257 条是前缀域把核心类与其附属类人为切开（如 `Order↔OrderLine`），聚合划分让其重归一体，真实业务边界只有 57 条。
- 45 条业务术语表（term/synonyms/colloquial → type_ref），检索一等公民。
- `scripts/semantic_gate.py` 新增 `contexts_v4` 五项校验（聚合恰属一个上下文、类恰属一个聚合、boundary 声明与计算一致、防空壳聚合、glossary type_ref 可解析）+ negative control。

### 检索层（M2，`freight/retrieval.py`，旧 bigram 整体删除）
- 五路召回：精确术语表（四层强度：token 相等 / 键是问句子串 / 词法亲和 / 口语覆盖）/ 字段加权 BM25（jieba，停用词过滤）/ 类型卡向量 / 图 1 跳扩展 / 查询历史。
- MiMo `/embeddings` 实测 404（models 列表亦无 embedding 模型）→ C3 向量通道进降级链，轨迹显式标注，不阻塞；`tools/build_type_vectors.py` 已就绪，端点可用即生效。
- 加权 RRF 融合（exact 通道内 tier 偏移）+ 归一化阈值 + 聚合配额（每聚合最多 6 席）+ 敢返回零。
- 评估门禁 `tests/test_retrieval_eval.py`（81 条标注集）：**recall@8=0.944**（门槛 0.85）/ **MRR@8=0.864**（0.60）/ **零命中 12/12**（0.80）/ 上下文命中 68/69（0.80），四项全部达标。标注集按「检索锚定级」口径：明细/延伸类型由注入层聚合闭包保证，不要求检索层 top8 全中。

### 注入层（M3）
- `_step_catalog` 聚合模式：seed=当前对象聚合闭包（声明推出，不靠「核心类恰好都在」的运气），问句聚合按 RRF 保序注入，逐聚合实测预算（放不下的跳过、不连坐）。
- 跨聚合边界链接随目录注入：v3 在组内关系过滤处直接丢弃跨组关系（一端不在即 skip），是「0 条关系 → 规划器无法解析 → 演示问题降级」事故的根源；v4 边界链接带两端聚合名，永不静默丢弃。
- 目录预算 6000→16000 重标定：聚合是语义完整注入单元，6000 连一个核心聚合（waybill≈6k 字符）都装不下，会把问句聚合全部裁掉、目录恒定只剩 seed。16000≈5k token，仍远低于全量目录（107k 字符），「装不下才缩聚合」设计不变。
- LLM 规划提示词说明「上下文→聚合→类型」层级与边界链接语义；`answerability` 分诊纳入 glossary 派生；推理结束回写查询历史（`runs/retrieval_history.jsonl`，不入库）。
- 聚合模式实测（本地服务）：默认问句 → waybill+fee（39 类型/58 关系/21 边界链接）；「发票和结算单是怎么关联的」→ waybill+invoice+settlement（51/71/21）；「等候费怎么计费」→ waybill+fee。

### HTTP 实测事故修复（验收阶段抓到，见 `efcc633`）
- **规划输出解析救截断/全角标点**：MiMo 返回的合法 JSON 偶发全角逗号「，}」或在 reason 字段中间被网关截断（实测 len=2545 断），旧逻辑整份判废 → 规划步稳定降级。现归一全角标点、清尾逗号、按括号栈 salvage 截断（事故原样救回 15 类型/6 任务/5 假设）。
- **types 兜底**：零命中场景模型输出目录外类型名时，回退目录锚点并标注 `type_source=catalog_anchors`（与 focus/tasks 兜底同构），链路给出诚实的「本体无相关声明」结论而非降级。
- **解析失败落盘** `runs/plan_parse_failures.log`（不入库）：两个根因都是靠它抓到的，此前只有「无法解析」四个字。
- **环境问题**：8765 端口上改动前的旧服务进程绑定 `127.0.0.1`，遮蔽新代码服务，所有请求打到 v3 旧代码（health 报 3.0.0）。已按用户指示全部杀掉，只保留 v4 服务。
- 回归测试 `tests/test_plan_parse_robustness.py` 11 项。

### 测试隔离治本（「database is locked」间歇失败终局）
- 根因链：`api.create_app(db_path=tmp)` 只隔离 MobilitySystem；挂载的 `/freight/*` 与
  `/enh/*` 路由走 `freight.api` 模块级单例，默认绑**共享库** `runs/freight_facts.db`
  （fresh=False + with_scenario=True，还会写库）。测试进程与本地运行的服务并发写同一
  sqlite 文件 → `database is locked`，且是竞态（有时过有时挂），此前多轮被误标为
  「环境性 flaky / 未定位」——实际唯一来源就是它。
- 修复：`create_app` 新增 `freight_db_path` 参数（传了就为 freight/enh 构造独立系统并
  注入单例，测试夹具在 teardown 恢复原单例防跨模块污染；服务模式不传，行为零变化）。
  `test_new_api_smoke` / `test_osdk_observability` 已接入。
- 验证：**服务运行中**跑全量不再锁死；此前必须停服务才能全绿。

### 验证
- `pytest` 全量 exit=0；`semantic_gate.py` 35 项全绿（schema 已接纳 v4 字段）。
- 本地服务（STG `stg-121` 仍 DNS 不可达，非 STG 验收）：默认问句 13 步工具调用收口、知识型问句按声明收口、零命中问句显式标注不降级。
- 已知环境性 flaky：长时间连续全量跑时 `sandbox`/`codegen` 间歇失败，单独跑均过，未定位到与本次改动的因果关系。

## 3.7.2 (2026-11-24) — 总览补阶段间因果：交接说明 + 决策机制口头版

用户反馈：总览每段只写「做了什么」，读者**推不出下一步是什么、凭什么推进」。
两处补齐：

- **阶段间交接显式化**：六段骨架各加 `handoff` 字段（谁触发 + 带走什么），
  前端在产出单元格下渲染 `⤵` 小字行。三条固定管道写明「代码固定推进 →
  入参是什么」，取证循环写明「大模型决定收口 → 全部观察随对话历史交给
  结论汇总」，动作出口写明「等人工确认 → 确认前不写任何业务数据」。
- **「下一步」提示补决策机制**：取证循环中显示「每轮大模型看到全部已取回
  的观察：信息不够就继续调工具，够了就给最终结论触发收口」——不再只说
  "调用 LLM 决定下一步"却不说怎么决定。

同批修正（上一轮遗留，用户追问戳中）：
- TBox 阶段声明计数恒显 0 行：聚合读了顶层 `rows`，而 TBox 步骤声明在
  `data.datasets[0].rows`——改从 datasets 取，显示真实条数。
- 骨架文案「命中规则、状态机、口径与来源契约」言过其实：describe() 不
  返回独立状态机声明（类 statuses 只是取值域）——改为「命中类定义、计价
  规则、关系与定价参数」。

验证：全量 `tests/`（122 项）+ semantic_gate/honesty_scan 通过；新增交接
字段断言（六段均有、facts 提 final、action 提人工确认）与 Playwright
交接行可见断言。

## 3.7.1 (2026-11-24) — 总览改执行轨迹台账：从售前示意图到可审计

「整条链一眼看完」原来是一张写死的五段示意图，跑起来与真实执行对不上。
对照真实轨迹（离线重放 12~18 步 tag 序列）暴露三处失真，全部修正：

### 失真一：阶段文案被覆盖 + LLM 决策归错阶段
- 原实现把 `LLM · 决策 / LLM · 复核` 全归「链路规划」，且每阶段只保留
  最后一步的 action——跑完后 02「链路规划」显示「大模型复核结论」，
  自相矛盾。重写 `_planned_stage_of`：LLM 决策属于「业务事实取证」
  （它决定取什么数）；复核/预算穷尽归纳/最终结论/降级属于「结论汇总」；
  动作提案属于「动作出口」。阶段文案固定来自骨架，执行只更新状态/
  计数/明细，**永不被步骤改写**。

### 失真二：「出现过就算完成」，看不出链真正发生过什么
- 每阶段聚合真实计数（步骤数/工具调用/大模型调用/取回行数，前端徽章），
  明细逐步可回看（工具行保留系统/参数/行数，其余行给步骤序号与 tag）；
  状态语义真实化：链结束后没到的阶段标「未到达」，动作出口恒为
  「等人工确认」（动作从不自动执行）。

### 失真三：降级时状态错
- 原降级 tag 没映射进阶段：结论已出但 05 仍「待执行」，还多出第 6 行
  计划外阶段。现在降级/预算穷尽映射到「结论汇总」并显式标「已降级」
  （产出前缀「降级收口 ·」），六段骨架恒定不变。

### 业务贴近度：末段落到「谁去做什么」
- 新增第 6 段「动作出口 · 按角色」：谁补证/谁退费/找哪个系统，
  产出「N 条角色提案 · 未决项 M 条 · 等人工确认」（确定性计数）。
- 参与方改为按真实步骤 who 聚合（带系统后缀的按主干归并），
  真实会出现「Agent → 人工」——不再按写死文案去重。

### 验证
- 新增 `TestStageSummaries` 5 项：文案固定不被覆盖且决策归取证循环、
  动作出口等人工确认、降级映射与「未到达」、current 跟随真实 pending、
  参与方按真实步骤聚合。全量 `tests/`（122 项）与门禁通过。
- 离线重放三场景（start 初始 / 12 步完整链 / 规划瞬断降级）行为符合设计。
- Playwright 真实浏览器端到端（真实大模型，18 步/90 秒）**19/19 全过**：
  六段骨架、动作出口等人工确认、假设裁定、循环计数、裁剪率、
  规划行文案固定、参与方名单（本体引擎/大模型/业务系统/Agent → 人工/
  大模型 + 业务数据）全部真实渲染，无 JS 报错。

## 3.7.0 (2026-11-24) — 规划驱动取证：假设回应用收口，规划不再只是背景板

推理链的「规划」步骤原来是装饰：LLM 圈完范围就扔，取证工具调用全靠临场发挥，
结论收口也软（一句"尚未完成全部交叉核对"糊过去）。这一版把规划变成
**可度量、可对照、可审计**的合同，结论必须按假设逐条回应。

### 硬伤一：规划名不副实（裁剪率可度量）
- `_plan_tbox` 新增 `focus_fee_types`（取证重点，1-3 个）与 `focus_reason`；
  系统提示加规划质量硬约束：focus 只列直接相关的费用类型、tasks 每条给
  intent / tool_hint / target / why、hypotheses 每条给 id / claim / verify_by。
- LLM 缺省或越界时确定性兜底：focus 非法 → 问题里点名的费用类型 → 再退声明范围
  （规划永远有焦点，裁剪率永远可度量）。
- 规划步骤 facts 展示「取证重点 N / M 种（裁剪率 X%）」+ 5 项规划质量检查
  （焦点非空 / 已裁剪 / 任务非空 / 工具合法 / 假设非空）。

### 硬伤二：规划未驱动执行（计划 vs 执行对照）
- 规划产出结构化任务清单（`_normalize_tasks`：tool_hint 必须在可用能力集内，
  缺省确定性补齐三条标准取证任务）。
- 执行阶段任务追踪：`_sync_task_reminder` 每次决策前注入未完成任务（替换式，
  不累积）；`_task_for_tool` 给服务任务的工具调用打 `serves` 标记；
  `_plan_execution_review` 在结论阶段出对照表——已执行（带调用次数与行数）/
  未执行（带理由）/ 计划外执行（被调用但未服务本任务）。

### 软肋一：收口太软（假设驱动取证）
- 规划产出可证伪假设清单（`_normalize_hypotheses` 缺省兜底 H1 费率口径 /
  H2 报价口径 / H3 证据缺失）。
- `_hypothesis_verdicts`：每条假设给 成立/排除/待补证，证据全部来自确定性
  检查（finding 码 / rule_delta 计数），不是模型措辞——假设回应可复核。
- 复核与预算穷尽路径的 instruction 统一要求三段式：① 逐条回应假设
  ② 已核对的事实 ③ 未决项；降级结论同步三段式并保留「初步结论」标注。

### 软肋二：出口不清（角色动作 + 未决项）
- `_role_exports`：从 `action.plan` 的确定性提案提取按角色的下一步
  （谁 / 做什么 / 对谁 / 依据），不是模型编的。
- `_open_items`：结构化未决项（缺什么 / 建议找谁 / 哪个系统 / 阻塞哪条结论）。
- 结论步骤注入收口数据集：假设核对 / 计划执行对照 / 角色出口 / 未决项。

### 修正（本轮端到端实测暴露的三个预存 bug）
- **「一键跑到底」按钮守卫反向**（`55ea951` 引入）：`runChainToEnd` 把
  `data.finished === false` 当拦截条件——新会话 finished 恒为 false，
  按钮从会话创建起就静默无效（点了没反应、无报错）。改为已完成才拦，
  拦时给提示。症状：演示现场/自动化点「一键跑到底」永远不动。
- **大模型降级步骤缺字段**：`next()` 的 LLMError 降级步骤是裸字典，
  没有 `query` 等前端必需字段——网关瞬断（实测 MiMo 偶发
  `Connection refused`）触发降级时，`renderChainStep` 在
  `step.query.label` 上直接崩，结论面板渲染中断。两段重复的死代码
  except 分支一并合并为 `_degraded_step`；前端 `renderChainStep`
  对缺失 query 也加了兜底。
- **降级路径没有收口**：收口三件套原来只在 `_final_step` 构造，
  降级步骤不带——大模型挂了假设核对/执行对照/角色出口全部消失。
  抽成共用 `_closing_datasets()`，降级步骤同样注入；规划 LLM 失败
  导致 `planned` 缺失时兜底到标准假设清单（"全部待补证、全部未执行"
  本身就是诚实的收口）。前端：展开全部模式与结论步骤的 `<details>`
  自动展开，收口表格真实可见（原来折叠在技术细节里 innerText 不可见）。
- 规划步骤的两张表在页面上不渲染：`_step_plan` 把任务/假设清单挂在
  `step["datasets"]`，而前端 `renderChainData` 只读 `step["data"]["datasets"]`
  （新增测试 `test_plan_step_has_trim_rate_and_datasets` 抓获）。已挂到正确键。
- `_step_exhausted` instruction 字符串结尾引号错误（语法错误，编译期即暴露）。

### 验证
- 新增 `tests/test_plan_driven_forensics.py` 16 项：规划质量（裁剪率/清单兜底/
  越界过滤）、任务追踪（未执行/已执行/计划外/提醒不累积）、假设裁定
  （每条有裁定、成立必附证据、收口数据集落位）、复核 instruction 含假设清单、
  角色出口与未决项结构、降级步骤字段与收口。全量 `tests/` 与门禁通过。
- Playwright 真实浏览器端到端（真实大模型推理链一键跑到底，14 步/100 秒）
  **12/12 全过**：规划步骤裁剪率/任务清单/假设表、结论步骤假设核对/
  执行对照/角色出口/未决项全部真实渲染，无 JS 报错
  （`scripts/_chain_browser_check.py`，可重复执行）。
- 降级路径实测：MiMo 瞬断（Connection refused）时链降级不崩、
  收口三件套照常展示（9/12 项过，缺的 3 项属规划步骤——规划 LLM
  本身失败时不存在规划步骤，为诚实行为非缺陷）。

## 3.6.1 (2026-11-23) — 本体知识图支持拖拽平移与缩放

演示页「本体知识图 · 全量 TBox」原来是张静态 SVG：500 个类 + 503 条关系 +
下半部分规则/动作/定价/数据源分层，一屏装不下，现场想看细节只能靠浏览器
页面缩放（字一起放大，节点还是挤在一起）。本轮给它加上画布级交互。

### 新增
- **滚轮缩放**（以光标为锚点，0.25×~6×）、**拖拽平移**、**双击空白复位**；
  卡片头部新增 ＋ / － / ⤢ 三个按钮，页脚标注交互提示。
- 触屏支持双指捏合缩放（Pointer Events，`touch-action: none`）。
- 实现取 **viewBox 操控**而非 DOM transform：平移改视图原点、缩放按锚点收放宽高。
  节点 hover / 选中 / 下钻 / 关系点击等既有交互零改动；画布尺寸在建图时钉死，
  避免 `height:auto` 随宽高比变化引起布局抖动。
- 交互不破坏原有语义：**任何位置起拖都平移**（含节点上——位移超 5px 才算拖拽，
  没位移仍是点击下钻）；全量图 ↔ 邻域图切换后按各自基准自愈
  （邻域图画布高 1000，复位回 `0 0 1180 1000`）。

### 修正（浏览器兼容性，实测复现后重写）
首版用 Pointer Events 做平移、且「节点上起拖不平移」，在部分环境
（嵌入式浏览器 / 用户实际操作）不生效，本轮重写：
- 单指平移改**鼠标事件**（mousedown/mousemove/mouseup，document 级）——
  全浏览器通用；指针事件只留给双指捏合。
- **节点上起拖也平移**：原实现把节点排除在平移外，用户"抓住节点拖"毫无反应，
  与"拖拽"的直觉相反。现为位移阈值判定：超 5px = 平移（吞掉随后 click），
  没位移 = 点击（下钻）。
- **修一个真 bug：抑制标志跨交互泄漏**。原实现用位移累加值判断是否吞 click，
  拖拽后 click 没落回 svg（拖出画布外）时标志没清零，会把**下一次**点击也吞掉
  （实测复现：拖完图再点节点，下钻突然失灵）。改为一次性标志
  （`kbPan.suppressClick`，用完即弃 + 600ms 兜底清零 + mousedown 重置）。
- 滚轮监听同时绑 svg 与容器（捕获相）；document 级监听改为**模块级单绑**
  （重绘换 svg 后旧监听器不再累积）；画布加 `user-select:none` 防拖拽选中文本。

### 验证
- Playwright 真实浏览器端到端 **17 项全过**：初始基准 / 滚轮放大缩小 /
  拖拽平移 / 拖拽不误触下钻 / 按钮三态 / 双击复位 / 点节点下钻 /
  **节点上起拖也平移 / 节点上拖拽不误触下钻 / 节点上滚轮缩放** /
  **拖拽后再次下钻（泄漏复现序列）** / 邻域图缩放复位 / 返回全量图 / 无 JS 报错。
- `tests/` 全量与门禁不受影响（页面改动只涉及 kb 画布交互层）。

## 3.6.2 (2026-11-23) — 规则详情面板展示可执行公式（judge.ast 渲染）

点开「本体知识图」里的 R-WAIT-01，详情面板的「判定 JUDGE」显示"—"，
只剩一大段说明文字——**规则到底怎么算的看不出来**。原因：面板只渲染
`judge.formula` 字符串字段，而 R-WAIT-01 这类规则的可执行部分是
`judge.ast`（AST 表达式），没有 formula 字符串。

### 修正
- 新增 `kbAstText`：把 judge.ast 递归渲染成可读公式。R-WAIT-01 现在显示
  `ceil(max(0, waiting_minutes - tariff.free_minutes) / tariff.block_minutes)
  × tariff.per_block`，与引擎 `evaluate_rule_ast` 实际求值的表达式一致。
- 判定段补充：应计目标（`calc.target` = actual_amount）、中间量绑定
  （blocks / free_minutes / block_minutes / per_block / vehicle_type），
  并注明"引擎对 AST 求值，参数取自定价决策表命中行"。
- 带 `judge.formula` 字符串的规则不受影响（优先用原字符串）。

### 验证
- Playwright 真实浏览器 6 项全过：点 R-WAIT-01 芯片后面板显示规则名 /
  公式文本 / actual_amount / 中间量 / 触发条件，无 JS 报错。
- `tests/` 全量与门禁不受影响。

## 3.6.0 (2026-11-23) — 计价决策表（pricing matrix 唯一参数源）+ 多维计价实验室

真实计价是「车型 × 时段 × 天气」多维组合，单公式+单维覆盖表达不了；而参数过去
存在三处（实例副本、`by_vehicle_type`、顶层标量），改一处漏一处就会漂移。这一版把
定价参数收敛成**本体声明的一张决策表**，并让演示能当场翻转维度看命中行变化。

### 新增
- **决策表 `pricing.<fee_type>.matrix`**（`freight/semantic_layer.json`）：行 =
  条件组合（`conditions`）+ 参数（`params`），**首行命中**，`conditions` 为空的行是
  默认行（必须放最后）。`dimensions` 声明计价维度，`time_windows` 声明时段
  （peak/night/normal，按 `accepted_at` 归窗）。`waiting` 新增大货车·雨天行
  （`per_block` 4.0 → 4.8，恶劣天气等候加价），排在单车种行之前。
- **多维计价实验室**（`freight/demo_labs.py::pricing_lab`，API `POST /pricing/lab`）：
  只读求值零写库，展示当前维度组合命中的行与参数，翻转一个维度（晴/雨、跨车型）
  应计当场变——大货车 53 分钟：晴天 8.0 元 → 雨天 9.6 元。
- 门禁新增 `check_pricing_matrix`：决策表行序校验——默认行不在最后、
  或更具体的行（条件超集）排在更通用的行之后，一律拦下。这类 bug 运行时静默
  （首行命中先挡住具体行），只有行序校验能发现。
- TBox 图谱（`web/tbox-graph.js`）pricing 节点展示维度、行数与「首行命中」说明。

### 修正（承认并清理）
- **参数三处存放 = 双口径**：定价规则实例曾抄一份参数副本（`PR-WAIT-01` 等 7 条），
  seed 已改为只存版本元信息；`by_vehicle_type` 与顶层重复标量随迁移删除
  （`scripts/_migrate_pricing_matrix.py`，一次性）。**教训：清副本不能用固定
  键名名单**——`per_km`/`base_fare_by_vehicle_type`/`source` 同样是参数，
  首版迁移漏删这三个，已在矩阵默认行之外留下第二口径，按「默认行携带的全部
  非结构键」规则清掉。
- **「实例没有 params」误报**：`_validate_pricing_rule` 还按旧口径要求实例携带
  参数副本，迁移后主运单一次诊断报 8 条 `PRICING_RULE_PARAMS_MISSING`（假警报）。
  改为校验真正的参数源：语义层 `pricing.<fee_type>` 解不出参数（无 matrix 且无
  默认标量）才报——删掉 matrix 会立刻现形。
- **车型费率对照表清空**：`_waiting_tariffs` 读 `by_vehicle_type`，迁移后返回 0 行。
  改为从 matrix 行条件收集车型，走与真实计价同一个 `_fee_context` 首行命中求值——
  对照表不再可能和计价漂移。

### 兼容（历史回放不漂移）
- 无 matrix 的历史发布快照（如 `releases/1.3.1-demo`）按旧口径回放：默认标量 +
  `by_vehicle_type` 覆盖，逐值一致——1.3.1 的 4.2米厢货 `per_block=3.0` 与当前
  工作副本的 2.4 不同，回放必须用当时值。

### 补齐（存量演示库收敛到决策表口径）
诊断推理链里看不到变化的三处遗留，本轮全部收口：
- **规则说明还是旧口径**：`R-WAIT-01.judge.explain` 只描述「按车型取费率」，
  推理链第 3 步（tbox.describe）直接展示它——已改写为决策表口径
  （车型 × 天气首行命中、truck-rain 行、公式不变），版本 2.1.0 → 2.2.0。
- **天气样本落不进存量库**：`seed()` 只在空库上跑，11-21 建的演示库拿不到
  迁移后新增的天气维度。新增 `scenarios.ensure_pricing_samples`（幂等，每次带
  场景启动都收敛）：主运单显式 `weather=normal`；WB20261106002 补
  `weather=rain`/等候 53 分钟 + 等候费 F101（实收 8.00）+ 证据 E101；
  V003 车型对齐当前 seed（大货车）。
- **★实例参数副本覆盖决策表（最隐蔽）**：存量库 7 个 PricingRule 实例还带着
  参数副本（`by_vehicle_type` + 30/5/2.4），而 `_fee_context` 的显式参数通道
  **优先级高于 matrix**——大货车+雨天样本被盖回 12.00（4.2米厢货行），
  天气维度白补。ensure 现在清空全部实例副本（新 seed 已不写），诊断恢复
  **应计 9.60**（truck-rain 行：40 分钟免费、每 10 分钟 4.8 元）。
  教训：迁移只改声明不够，**存量数据里的副本同样是第二口径**，且它的优先级
  比新口径高——不清就会静默盖掉矩阵。

### 全面复查（同型问题全仓扫荡）
死键 / 第二口径 / 假承诺三类，逐一定位并清除：
- **计算不再供实例参数**：`fee_analysis` 曾把 PricingRule 实例 params 喂进
  `compute_fee`，优先级高于 matrix——上面那起事故的传导链。现在计算只认声明
  （`params={}`），实例仅供版本元信息校验（rule_id / 生效期 / fee_type）。
  历史回放同理改由旧快照自己的 legacy 解析供数：喂实例参数反而会让**今天的
  副本覆盖历史标准**。
- **legacy 回退键收全**：`_resolve_tariff` 旧声明分支原先只收
  free/block/per 三个键，`per_km`/`per_order`/`base_fare_by_vehicle_type`
  会被静默丢弃——无 matrix 的新费用类型（如验收注入的 remote_area）靠它供数。
  已改为收全部非结构顶层键。
- **四处「改本体参数」演示/测试改的是死键**：`parity_lab` Q6、漂移实验、
  `prevention_demo`、`test_prevention` 都写 `pricing.waiting.free_minutes=20`
  再靠显式参数通道算——层内改动空转，翻转来自参数通道，「只改一处 JSON」
  的叙事是假的。已全部改为**改 matrix 命中行 + 不传显式参数**，翻转真正
  来自声明（漂移实验现状：本体侧 16.8/16.8 同源，属性图侧 16.8/12.0 漂移）。
- **验收场景 2 原测的是被删掉的口径**：给规则实例打 params 副本期望金额
  跟着变——正是第二口径。已改为改决策表行参数，断言随声明变（12/12 保持）。
- **发布工具死键拦截**：`build_release.py` 的 `--set` 示例还在教人写
  `pricing.waiting.free_minutes=20`（死键，不报错不生效）。新增
  `dead_param_keys` 检查直接拒发，示例改为 matrix 行路径。
- **门禁死键判据**：`check_pricing_matrix` 增加 `MATRIX_DEAD_PARAM_KEY`——
  有 matrix 还把参数写在顶层即拦（附负向单测）。
- **维度承诺对齐**：`dimensions` 原先给所有费用类型都写 `vehicle_type`，
  多数只有默认行、没有任何行按车型分支——假承诺。改为从行条件实际字段推导
  （waiting: `[vehicle_type, weather]`，其余: `[]`）；时段归窗机制在位但
  当前无行按时段分支，规则说明明说「预留维度」，不虚报。
- **highway 来源回退**：Agent 目录里「来源 ETC」读顶层 `source`（已迁进
  matrix 默认行），原代码靠硬编码默认值巧合蒙对；改为从默认行取。

### 验证
- 零漂移：主运单 `fee_analysis` 与迁移前**逐键一致**（应计 12.00 / 实收 25.00 /
  差异 13.00，findings 仅 `WAITING_FEE_MISMATCH`；车型对照表 5 行数值不变，
  行序随决策表优先级）。
- 新增 `tests/test_pricing_matrix.py` 27 断言：零漂移 / 首行命中（truck-rain 压过
  单车种行）/ 默认行兜底 / 时段归窗（工作日早高峰、周末、夜间、坏时间戳）/
  旧版回放 / pricing_lab 翻转与零写库 / 门禁行序与死键负向用例 /
  参数源被删现形 / **存量副本不再覆盖矩阵 + ensure 清理 + 幂等**。
- 全量 258 passed；门禁 31 项全绿；`freight_acceptance` 12/12；
  迁移脚本从 HEAD 原件**完整复现**当前产物（含 weather 属性、规则说明改写、
  维度推导）。

## 3.5.0 (2026-11-21) — 缺口检测门禁（第 28 项）+ LLM 退避重试 + 本地部署复测

上一版把来源契约变成门禁（第 27 项），这一版补齐**缺口检测能力**的验证，并把
上轮观察到的推理链偶发 503 治掉。

### 新增
- **缺口探针数据**（`freight/scenarios.py::build_gap_cases`）：人为构造四类来源缺口——
  无人认领 / 口径越界 / 数据过期 / 证据缺失——外加一条「正常被 ETC 认领的 highway」
  作为归属声明的承重探针。**与主诊断剧本严格隔离**：正常运单不出现这四类告警。
- 门禁**第 28 项** `check_gap_detection`：断言 ① 四类缺口报得出来；
  ② 正常诊断数据上不误报；③ **数据修好后告警消失**（补证据 / 改成合法认领）——
  第三条是关键：它证明告警是数据驱动的，不是恒真断言。
- 承重实验 +2 条（L22 去掉 `require_evidence` → 缺证据告警消失；
  L23 去掉 ETC 的 highway 认领 → 该费用变成无人认领），共 **24 条**。

### 修正（承认并清理）
- **门禁第 28 项的第一版负向断言是空转的**：finding 里的 `fee_id` 是**短 id**
  （不带 `fee:` 前缀），断言用了全 id → 永远匹配不上 → 「数据修好告警消失」这条
  实际从未生效。是补单测时发现的。已统一口径，并按**费用粒度**比较
  （别的费用可能报同一个码，只看集合会把「这条修好了」误判成「没修好」）。
- **探针运单缺三样东西导致规则求值直接崩**：车型关系（计价规则按车型选费率）、
  定价规则实例绑定（`pricing_rule_id` + `fee_priced_by_rule` 关系）、
  规则字段（`waiting_minutes` 等）。缺任何一样都会抛「AST 字段不存在」，
  而不是产出告警——**这正是探针的价值：它逼着规则求值链路把前置条件写清楚**。
- **LLM 网关无退避重试**：本地部署实测推理链偶发 503（网关瞬时故障），重跑即恢复。
  `freight/agent.py` 已加指数退避重试：只对 429/5xx/网络抖动/空响应重试，
  **认证失败（401/403）与参数错误（400）立即抛出**（重试没有意义，还会浪费额度）；
  thinking 模式默认少给一次重试，避免长推理总时长翻倍。环境变量：
  `OPENAI_CHAT_RETRY_ATTEMPTS` / `_RETRY_BASE` / `_RETRY_MAX`。

### 部署环境（本地）
- STG `stg-121` 仍不可达（DNS 解析失败），本轮继续按用户授权走**本地部署测试**。
- **本地部署复测**（`cli.py serve --port 8765`，全新库）：health 200（500 类/503 链接/
  16 规则/15 动作）、state-machines 8 机器 15 迁移边、state-history 200
  （ACCEPTED → LOADED → DELIVERED）、fee-analysis 200（三口径一致、来源覆盖 7/7
  已解析 0 未解析）、**动作写回** POST /freight/actions/dispute_charge → 409
  （状态前置不满足，正确拒绝重复申诉）且审计 22→23 条、mcp/tools 16 个。
- 顺手清掉一个**端口残留**：上一轮停服务后 8765 仍有旧进程在监听，导致本轮复测
  打到了旧服务（旧库、旧代码、剧本数据缺失），一度误判「剧本没生效」。已定位并清理。

### 验证
- `pytest` **298 passed**（新增 `tests/test_llm_retry.py` 6 项、
  `tests/test_gap_detection.py` 5 项）
- 语义门禁 **28 项 / 17 覆盖 / ok=true**
- 承重实验 **24/24**；freight 验收 12/12；smoke **7/7**；`verify.py` 36/36


## 3.4.0 (2026-11-21) — 来源契约收口（门禁第 27 项）+ 本地部署测试

上一版把运单-费用链做深（深水区 18 类全覆盖），这一版把**来源契约**变成门禁，
并按用户授权完成**本地部署测试**（STG `stg-121` 本轮不可达）。

### 新增
- `freight/source_coverage.py`：来源契约判据内核
  - G1 **归属矩阵**：`pricing` 段的每个 `fee_type` 必须**恰好**被一个
    `data_sources[].fee_types` 认领 —— 漏认领报 `FEE_TYPE_NO_OWNER`，
    两个来源认领报 `FEE_TYPE_MULTI_OWNER`，认领了定价里没有的类型报 `FEE_TYPE_NOT_IN_PRICING`；
  - G2 **费用类型覆盖（V5）**：每个 `fee_type` 在 FeeItem / PricingRule / QuoteItem
    **三方各有 ≥1 实例**，缺一方报 `FEE_TYPE_NO_INSTANCE` / `_NO_RULE` / `_NO_QUOTE_ITEM`。
- 门禁**第 27 项** `check_source_contracts`（含 4 条负向用例：删认领 / 双认领 /
  删报价明细 / 篡改报价明细金额），覆盖目标 15 → **16**。
- `freight/scenarios.py` 之外，服务启动即带剧本：`FreightSystem(with_scenario=True)`，
  `freight/api.py` 默认开启 —— **跑起来的服务本身就是深水区 100% 覆盖的那份数据**，
  与门禁第 26 项度量的是同一批对象。
- 承重实验 +2 条（L20 删认领 → `FEE_TYPE_NO_OWNER`；L21 双认领 → `FEE_TYPE_MULTI_OWNER`），
  共 **22 条**。

### 部署环境（本轮实况）
- **STG `stg-121` 不可达**：实测 `ssh stg-121` 报 `Could not resolve hostname stg-121`，
  仓库与 `/etc/hosts` 均无该主机地址。`AGENTS.md` 已记录本轮**用户授权改用本地部署测试**
  （仅本轮有效；任何情况下不得部署 PRD）。
- **本地部署测试结果**（服务 `cli.py serve --port 8765`）：
  | 接口 | 结果 |
  |---|---|
  | `/freight/health` | 200｜500 类 / 503 链接 / 16 规则 / 15 动作 |
  | `/freight/ontology/state-machines` | 200｜8 个状态机，15 条迁移边 |
  | `/freight/objects/waybill:SCN01/state-history` | 200｜轨迹 ACCEPTED → LOADED → DELIVERED |
  | `/freight/waybills/waybill:WB20261105001/fee-analysis` | 200｜报价 480 / 实收 518 / 差异 38；**报价三口径一致**（明细合计 480 = 报价单总额 480 = 运单快照 480）；来源覆盖：连接器 2 / 费用 7 / 已解析 7 / 未解析 0 |
  | `/freight/mcp/tools` | 200｜16 个工具（含新增 `ontology.state_machine`） |
  | `/freight/mcp/call` `ontology.state_machine` | 200｜FeeItem 状态机 + 迁移边 |
  | `/freight/objects/waybill:SCN01/graph?depth=2` | 200｜13 个节点 |
  | reasoning-chain（MiMo 直连） | 12 步 finished｜三方分工标注完整｜结论由 `mimo-v2.6-pro` 生成 |
- **已知抖动**：推理链偶发 503（网关调用超时），同一服务重跑即恢复；`agent.py` 目前**没有退避重试**，
  建议后续补（本次未改，避免在无 STG 环境里改动网关调用策略）。

### 验证
- `pytest` **287 passed**（新增 `tests/test_source_contracts.py` 11 项）
- 语义门禁 **27 项 / 16 覆盖 / ok=true**
- 承重实验 **22/22**；freight 验收 12/12；smoke **7/7**；`verify.py` 36/36


## 3.3.0 (2026-11-21) — 运单-费用链做深做全 + ABox 深度门禁（第 26 项）

上一版把「本体能表达」做完了，这一版做的是另一半：**证明本体是真的**。
深水区（声明了 data_sources / 迁移动作 / 状态机的 18 个对象类型）从
**类覆盖 83%、动作实测 3/15**，做到 **类 18/18、非孤岛 18/18、动作 15/15、
迁移边一致 21/21、规则分支 16/16**，并固化成**门禁第 26 项**（不达标即 FAIL）。

### 修正（承认并清理）
- **5 个动作只有声明、没有运行时实现**：`arrive_stop` / `depart_stop` / `issue_quote` /
  `supersede_quote` / `confirm_subcontract` 执行时抛 `NOT_IMPLEMENTED`。
  这是"声明与执行脱钩"的残余——和之前属性注册表"只登记不执行"同一类病。
  已补齐 5 个实现，并各带一条业务前置校验（未到达不能离开、过期报价不能签发、
  改版目标必须存在、分包金额不得超运单收入）。
- **审计 result 格式分叉**：种子数据手写审计用 `{"status": ...}`，
  `execute_action` 路径用 `{"before_status","after_status"}`——同一张表两种写法，
  导致"迁移与声明一致性"这类度量读不到状态、产生假不一致。已统一为后者。
- **`smoke_no_llm` 的「无 key 场景」根本没测到**：它只 pop 了环境变量，
  而 `LLMClient._load_env_file` 会把仓库 `.env` 里的 key 又塞回来（本地有 .env
  时该 smoke 反而红）。已屏蔽 `.env` 注入，现在两种环境都真测无 key。
- **边覆盖判据设计错误**：原先要求"每个类都有出边"，把货物/司机/车辆/轨迹/证据/
  计价规则这些**业务上的叶子类型**判成缺陷。已改为「不是孤岛」，并只对
  **有迁移动作的类**额外要求双向有边。

### 新增
- `freight/scenarios.py`：**核心业务剧本**——一单多段运输（船东→运单 2 站点→
  分包给承运商→报价 3 版→费用→争议→减免→结算挂起/冲正→发票红冲→工单关闭），
  **真实执行 15 个迁移动作**，留下状态迁移、事件与审计。它是覆盖度度量的数据源，
  也是"这条链到底跑不跑得起来"的活体验证。**边界**：剧本数据不等于生产实况，
  它证明「每个类型有实例、被引用、被执行过」，不证明「货拉拉真有这些对象」。
- `freight/abox.py`：ABox 深度校验内核（V1 实例 / V2 非孤岛 / V3 动作实测 /
  V4 迁移一致 / V6 规则分支）。
- 本体顶层 `abox_targets`（`schema/semantic_layer.schema.json` 放行）：深水区清单
  在本体里，不写死在代码里；改一处判一处。
- 门禁**第 26 项** `check_abox_coverage`（4 条负向用例：删实例 / 删关系 /
  删审计 / 篡改审计结果），覆盖目标 14 → **15**。
- 承重实验 +2 条（L18 清单里加无实例类 → 门禁 FAIL；L19 清单去掉一类 → 判定分母跟随），
  共 **20 条**。

### 决策落地
- **D1：LLM 走 MiMo 直连**——`AGENTS.md` 改为 `https://api.xiaomimimo.com/v1` +
  `mimo-v2.6-pro`（STG 网关降为备用）；STG 出网需放行该域名。
- **D2：不接 ETC 等新外部系统**——ETC 无公开 API，接模拟网关只能证明机制、
  接内网不可控。改为把**已有来源的口径归属与缺口处理**做扎实（Phase C）。

### 实测（2026-11-21）
```
深水区 18 类：实例覆盖 18/18 · 非孤岛 18/18 · 动作实测 15/15
迁移边一致性 21/21 · 规则分支 16/16 · 审计 22 条 · 事件 26 条
全量 500 类：18 类有实例（3.6%）—— 门槛只约束深水区，不逼全量
查询面 9 类形态各有断言（过滤/多条件/排序分页/聚合/search-around/多跳/SQL/证据链/对象图）
```

### 验证
- `pytest` **276 passed**（新增 `tests/test_abox_coverage.py` 18 项、
  `tests/test_query_surface.py` 10 项）
- 语义门禁 **26 项 / 15 覆盖 / ok=true**
- 承重实验 **20/20**；freight 验收 12/12；smoke **7/7**；`verify.py` 36/36


## 3.2.0 (2026-11-20) — 状态机升为独立层 + 动作参数类型化 + 对齐矩阵纠偏

上一版把六项 Palantir 缺口闭合了，但**对齐基准矩阵（`roadmap/palantir-benchmark.md`）
停在改造之前**，其中 4 条与代码不符——包括一条方向性错误（P-07 写「无 apply」，
而引擎里 apply 一直在）。本轮先纠基准，再补能力。

### 修正（承认并清理）
- **对齐矩阵落后于代码**：`roadmap/palantir-benchmark.md` 标 Draft、停在 2026-11-19，
  P-06/P-07/P-08/P-11 四条与实现不符。已按 v3.2 实测逐条重写 13 项现状，
  状态从 Draft 改为「v3.2 实测基线」，并补上此前没人记的两个缺口：
  **类级生命周期 status**、**标题属性（title property）**。
- **P-07 方向性错误**：矩阵写「当前无 apply、无 edits、无副作用」，实际
  `engine.execute_action/_apply_action` 已实现前置校验、属性写回、事件、审计、
  **幂等键 replay**。矩阵会骗人——比代码 bug 更贵，因为它决定所有后续排期。
- **`datetime` / `timestamp` 口径分叉**：属性值类型用 `datetime`（200 个属性），
  函数参数类型用 `timestamp`，两个名字指同一件事——谁没被枚举覆盖到就悄悄不校验。
  现在 `SemanticLayer._type_ok` 同时认两个名字并按 ISO 校验；
  `function_registry._scalar_ok` 改为**委托**同一实现（原来是自己抄的一份）。

### 新增（P2-4 状态机独立层）
- `freight/semantic_layer.json` 顶层 **`state_machines`** 声明（`initial` / `terminal` /
  `external_states`，8 个对象类型）。**迁移边真源仍在动作的 `from_status→to_status`**——
  不把流转复制到第二处，避免二源真相；声明只补动作说不清的三件事。
- `SemanticLayer.state_machine()`：机器图（状态集 / 起点 / 终态 / 外部状态 /
  迁移边（稳定 id `transition:<Type>:<action>`）/ 可达集 / 来源不明 / 归宿不明）。
- `SemanticLayer.validate_state_machines()`：六类完备性判据——来源不明、归宿不明、
  终态仍有迁出边、迁移边引用非法状态、有动作却未声明、无动作却声明。
- 门禁**第 25 项** `check_state_machines`（含 4 条负向用例），覆盖目标 13 → **14**。
- 运行时暴露：REST `GET /ontology/state-machines`、`/ontology/state-machines/{type}`、
  `/objects/{id}/state-history`；MCP 原语 **`ontology.state_machine`**（本体 JSON 声明，
  代码零改动即生效）。
- 对象状态轨迹 `state_history`：只取「状态变更」事件（事件类型 ∈ 该类状态集）——
  events 表里混着证据与并发测试事件，不按状态集过滤就会把不相干事件当成状态变化。

### 新增（P2-5 动作参数类型化）
- `engine.execute_action`：参数校验从「只查必填」升级为 **查必填 + 查类型 + 拒未声明参数**
  （`PARAM_UNKNOWN`）。类型口径复用 `SemanticLayer._type_ok`——动作与函数、动作与属性
  三处不再各判各的。
- 承重实验新增 2 条（L16 状态机声明改动 → 门禁 FAIL；L17 参数类型改动 → 同调用被拒），
  两条都只改 JSON、代码零改动。

### 验证
- `pytest` **239 passed**（新增 `tests/test_state_machine.py` 20 项）
- 语义门禁 **25 项 / 14 覆盖目标 / ok=true**
- 承重实验 **18/18**（改回原样后门禁 exit=0）
- `scripts/freight_acceptance.py` 12/12；7 个 smoke 全过；`verify.py` 36/36


## 3.1.0 (2026-11-20) — 六项 Palantir 对齐缺口闭合（契约层 / 展示层 / 运行时三件套）

上一版把属性升成了一等公民，但只做到「登记」。本轮把**声明与执行**接起来，
并补上展示层、物化层、标记传播与破坏性变更审查。六项缺口逐条对应实测证据。

### 修正（承认并清理）
- **扩展层此前完全没有 JSON Schema**：往 `ontology_extension.json` 塞
  `totally_bogus_top_key` / `bogus_field_xyz` / `bogus_param_key` /
  `value_type="not_a_real_type"` 全部加载成功。扩展层是追齐层的第二份本体，
  没有契约就等于垃圾键静默入库。
- **属性注册表只登记不执行**：`validate_object` 只校验属性名白名单 + 状态机，
  `estimated_fee="abc"`（decimal）能直接写进去。注册表在门禁层是权威、在运行层是装饰。
- **扩展层与基础注册表类型分叉 4 处**：`estimated_fee`/`actual_fee`/`variance`
  被写成 `integer` + `unit=cents`，`status` 被写成 `string`（基础层是 `decimal/CNY`、`enum`）。
  扩展层覆盖后，金额实际按「整数分」解释。
- **函数参数类型是自由字符串且连枚举都不校验**：`validate_signature` 只查
  必填/未知参数，`fee_breakdown(waybill_id=12345)` 一路通过。
- **OSDK 生成器没接基础属性注册表**：1950+ 个属性被判「未注册」并退化成
  `str | None`——「强类型客户端」其实是弱类型，且看不出来。
- **`smoke_concurrency` 长期红**（HEAD 上 6 个错误）：`_read_conn` 在写事务进行中
  把**写连接**借给其他读线程，触发 SQLite 连接不可跨线程共享的硬约束。改为
  只有写事务的发起线程才回落到写连接。
- 种子数据两处类型脏值：`actual_duration_min`/`duration_min` 写 `218.0`（integer 字段）；
  `closed_at` 用 `""` 表示「未关闭」（datetime 字段）。改为 `218` / `None`。
- **`RID_LEDGER_DRIFT` 是死检查**（承重实验 L9 抓出）：`ledger_key = f"{kind[:-1]}:{oid}"`
  对 `classes` 求前缀得到 `"classe"`，台账里是 `"class:Waybill"`，`if expected` 直接短路——
  **类 rid 被手改也一声不响**。改为显式映射 `{classes: class, relations: relation}`，
  并新增 `RID_LEDGER_MISSING`：台账缺项不能当「没问题」，没有基线就无从判断是否被改。
- **二分真相缺一道闸**（承重实验 L12b 抓出）：同一属性在基础注册表标了 `PII`、
  扩展层写 `null` 时，运行时取扩展层那份 ⇒ **脱敏静默消失**，而两份文件各自都合法、
  schema 也都过。门禁补 `PROP_MARKING_DIVERGED` / `PROP_TYPE_DIVERGED`。

### 新增（P0 契约层）
- `schema/ontology_extension.schema.json`：扩展层元模型契约。顶层
  `additionalProperties:false` 锁死 10 个键；`$defs.propertyType` 与基础 schema
  枚举一致（门禁做漂移比对）；`$defs.functionParam` 扩到 7 键
  （`name/label/description/type/required/of/interface`）且 `type` 走枚举，
  `object_set`/`object_ref` 强制要求 `of`。
- `freight/semantic.py`：`validate_object` 增加 `_check_value_types()`，按注册表
  `value_type` + `nullable` 执行；`datetime`/`date` 校验 ISO 形态。
- `freight/fact_mcp.py::update_object`：写入收口。动作里 10+ 处直连 UPDATE 也过类型校验——
  只校验创建路径等于「新建管、改一条就绕过」。
- `property_registry_policy` 收口为 `enforcement=strict` + `legacy_grace=false`；
  门禁新增 `POLICY_STRICT` 断言，改回 incremental 会红。

### 新增（P1 对齐层）
- 类/链接补 **`rid`**（不可变标识，`ri.mobility.main.object-type|link-type.<uuid5>`）+
  **`plural_display_name`** + **`display_metadata`**（icon/color/visibility/category）。
  `freight/rid_ledger.json` 台账接管 rid：改名时在台账写一行即可续链，身份不断。
- **函数签名类型化**：参数标量类型校验（与 `SemanticLayer._type_ok` 同口径）；
  新增 `result_types` 注册表（`FeeBreakdown`/`QuoteReconcile`/`FeeBreakdownBatch`），
  `returns` 必须是标量类型 / 已注册结果类型 / 对象类型；运行时按 Result Type
  校验返回字段的缺失、多余与类型。
- **接口显式 implements**（对标 `ImplementsInterfaces2`）：`match` 支持
  `structural`/`explicit`，显式声明优先并支持 `property_map` 属性映射；
  谎报实现（声明 Shipper 实现 Chargeable 但 Shipper 没金额属性）由
  `IFACE_UNIMPLEMENTED` 拦。
- OSDK 类型映射补齐 `decimal→Decimal`（Python）/`number`（TS）、`datetime`/`date`/`json`/`enum`。
  金额不再退化成 `str`。

### 新增（P2 运行时三件套）
- **物化**：`freight/projection.py` 轻量对象投影（`object_props` 属性级行式表 +
  数值/字符串双索引 + 增量状态计数）。写入路径同一事务内同步；查询引擎用投影
  **收窄候选集**后仍逐行复算。**正确性边界写清楚**：复算能滤掉多余候选，
  救不回被漏掉的候选，所以收窄前过 `is_fresh()` 闸门，不新鲜整条回退全表。
  `consistency()` 做全量逐属性比对（值级过期只有它能查）。
- **标记传播**：`SecurityEngine.marking_trace()` 实现数据源级 + 类级标记向对象继承，
  行级可见要求「持有**全部**有效标记」（Palantir 语义）。**属性标记不参与行级判定**——
  属性 marking 管列（置 null），提升成整行不可见是过度管控。新增 `guard_output()`
  防 Agent 输出外泄。
- **破坏性变更**：`freight/change_gate.py` 分类 类/属性/类型/基数 变更，
  回溯引用方（函数/规则/动作/数据源/接口/派生类型/OSDK 生成物），
  `freight/breaking_approvals.json` 逐条签字（路径完全一致才算）。
  门禁第 15 项 + `tools/proposal.py` 的 `review.json` 增加 `breaking` 字段。

### 门禁
- 20 项 → **24 项**，新增 `#schema`(扩展层) / `#policy` / `#display` / `#breaking`；
  每项都带负向用例（不是空绿）。

### 承重实验（`scripts/load_bearing_demo.py`）
单测的 `make_ext(mutator)` 只改内存 dict，证明不了「本体文件是唯一真相」——代码里
完全可以再写一份默认值兜底而测试照绿。本脚本换更硬的口径：把仓库拷到
`runs/load_bearing_<ts>/`，**用 `open()/json.dump` 真改 JSON**，再在拷贝里用子进程跑探针，
比对「改前/改后」。16 条用例覆盖六项缺口，改前/改后结论见 `logs/load_bearing_*.json`：

| 用例 | 只改这一处 JSON | 改前 → 改后 |
|---|---|---|
| L1/L2 | 扩展层塞垃圾键 / `value_type` 非法 | 门禁 PASS → `EXT_SCHEMA_INVALID` |
| L3 | `waiting_minutes.value_type: number→boolean` | 种子回放成功(47 对象) → `OntologyViolation` 启动失败 |
| L4/L5 | 同上 | 创建/更新该字段成功 → 类型不符被拒 |
| L6 | `policy` 改回 `incremental` | PASS → `POLICY_NOT_STRICT` |
| L7 | `waybill_id` 参数 `string→integer` | 调用成功 → `SIGNATURE_INVALID` |
| L8 | Result Type 删 `conclusion` | 调用成功 → `RETURN_FIELD_UNDECLARED` |
| L9 | 手改 `Waybill.rid` 末位 | PASS → `RID_LEDGER_DRIFT`（修 bug 前：仍是 PASS） |
| L10 | 改 `label`/`plural_display_name` | 仍 PASS——改名不断链 |
| L11 | 加「CRM → FINANCE」数据源标记 | Shipper 可见 → 整行不可见，拒绝原因指认 CRM |
| L12 | 扩展层给 `waiting_minutes` 标 PII | 行仍可见，只该列置 null |
| L12b | 只改基础层 marking（扩展层 null） | PASS → `PROP_MARKING_DIVERGED`（修 bug 前：仍是 PASS） |
| L13/L14 | 删 `Waybill.properties[waiting_minutes]` | 评审 PASS → FAIL(未签字 1) → 补签字回 PASS |
| L15 | 手工删投影行 | 闸门判不新鲜 → 回退全表，条数与基线一致（不静默少行） |

### 验证
- `pytest` **219 passed**（`tests/test_palantir_alignment.py` 47 项，全部对应曾经的缺口）
- 语义门禁 **24/24**；承重实验 **16/16**（改回原样后门禁 exit=0）
- 7 个 smoke 全过（`smoke_concurrency` 由红转绿）；`scripts/freight_acceptance.py` 12/12

## 3.0.0 (2026-11-20) — 本体元模型对齐 Palantir 三件套 + 500 类真实建模（清掉 540 个模板空壳）

### 修正（承认并清理）
- **删除 540 个模板生成的空壳类 / 540 条模板关系**：这些类由 `tools/expand_ontology.py`
  用同一模板复制（固定 6 属性 `xxx_id+name+status+created_at+updated_at+父id`、描述套
  `"xx域的，按 xxx_id 检索"`、状态机一律 ACTIVE/INACTIVE），脚本里甚至写着
  `if len(classes) < 500: raise SystemExit("扩展规模不足 500")`。ABox 里 48 个实例
  **没有一个落在生成类上**，即 0 条真实数据承载。
- 真实规模重述：此前对外报的「558 类 / 568 链接 / 3407 属性」中，真实手写仅
  **18 类 / 28 链接 / 167 属性**。

### 新增（schema v3：Property Type 升为一等公民）
- `freight/semantic_layer.json` 顶层新增 **`property_types` 注册表（1961 个属性）**，
  每个属性含 `value_type / base_type / unit / marking / timeseries / nullable /
  owner / version / iri / effective_from / used_by`——对标 Palantir Property Type。
  类只引用属性名，业务复杂度靠属性数量承接，不给类加元模型字段。
- `schema/semantic_layer.schema.json`：新增 `$defs.propertyType` + 顶层 `property_types`；
  `value_type` 枚举 `string/text/integer/number/decimal/boolean/datetime/date/json/enum`，
  `base_type` 映射到 `string/int/double/boolean/timestamp/date`。
- `ontology/` 建模包（人工建模 DSL，属性必须显式标注类型，禁止脚本按命名猜类型）：
  - `ontology/__init__.py`：`C() / L() / I()` + 类型短码表 + 单位/标记推断规则
  - `ontology/core_classes.py`：18 个核心类补类型标注（属性名与状态机一字未改）
  - `ontology/domains_01_order.py` 订单/运单/货物/装卸 79 类
  - `ontology/domains_02_capacity.py` 车辆/司机/承运商/分包/维保/能源 96 类
  - `ontology/domains_03_money.py` 报价/计价/费用/结算/发票 77 类
  - `ontology/domains_04_risk.py` 证据/异常/保险/关务/合规 75 类
  - `ontology/domains_05_infra.py` 仓储/线路/地理/设备/单证 77 类
  - `ontology/domains_06_ops.py` 轨迹/客服/组织/合同/指标 76 类
- `tools/build_ontology_v3.py`：只做「合并 + 注册表构建 + 结构校验」，业务内容全部来自
  `ontology/`；同名属性类型冲突**直接报错拒绝写盘**（本轮共拦下 30+ 处真实语义冲突）
- 结果：**500 类 / 503 链接 / 1961 属性 / 35 业务域**，属性数分布 5~21（非固定 6），
  状态机长度分布 2~7（非一律二元），每类至少挂 1 条真实语义关系，链接全部带
  `cardinality`，时态链接 100+ 条

### 新增（反虚胖门禁）
- `freight/semantic.py::validate_ontology_substance()` + 门禁第 20 项：
  - `PROPERTY_REGISTRY_MISSING` 属性注册表缺失
  - `PROP_UNREGISTERED` 类属性未注册（裸属性名）
  - `CLASS_DESCRIPTION_MISSING/TEMPLATED` 描述缺失或套模板
  - `CLASS_AUTO_GENERATED` 存在脚本批量生成的类（彻底堵住 expand 那条路）
  - `CLASS_STATUS_UNIFORM` 通用状态机占比 > 5%
  - `RELATION_TEMPLATED` `xxx_belongs_to_yyy` 形式关系 > 20%
- `tests/smoke_substance_gate.py`：6 条负向承重用例，逐条证明断言真的会拦（不是空绿）

### 修正（真实 bug）
- `freight/object_query.py`：`NUMERIC_TYPES` 补 `decimal`——schema v3 引入 decimal 后
  数值类型校验/聚合校验会对金额字段失效
- `ontology/__init__.py::marking_of()`：原用子串匹配把 `fee_label`（费用名称）误标
  FINANCE、把 `cargo_name` 误标 PII，导致安全层把它们脱敏成 `None`（数据丢失而非脱敏）。
  改为「后缀精确匹配 + 自然人姓名白名单」
- **`primary_key` 从 Property Type 注册表移除**：主键是 Object Type 级语义
  （Palantir `primaryKeyProperty`），放注册表会产生 348 个假冲突
  （`agent_id` 在 ServiceAgent 是主键、在 ServiceCall 只是外键）
- **金额字段不打 marking**：marking 是强制列级脱敏，给运费打 FINANCE 会让普通角色
  看不到金额（无法对账）；财务字段可见性应走 `property_security_policies`

### 兼容性
- `ExtensionRegistry` 新增 `base_property_types` 参数：基础本体是属性注册表全量权威，
  扩展层（含 proposal 分支）可覆盖同名定义；`runtime.py` / `conftest.py` / codegen 已接入
- 测试夹具新增 `make_ext_without(drop, mutator)`：schema v3 后属性全部注册，
  验证 legacy/strict 语义需显式从基础注册表摘除属性
- 门禁 **20/20 全绿**；全量 pytest 通过；OSDK codegen 生成 500 类三端；三个 smoke 演示全跑通

## 2.4.0 (2026-11-20) — 查询表达力追齐 Palantir（Object Set DSL + 派生类型 + Ontology SQL）

### 新增
- `freight/object_query.py`（E1-E6）：组合式 Object Set 查询——谓词层（11 算子+组合+本体期类型校验）、
  集合代数（union/intersect/subtract/followLink+set_id 快照）、管道（orderBy/count/sum/avg/min/max/group_by）、
  时态 followLink（`at` 时间点回放 `valid_from<=at<valid_to`）
- `freight/sql_frontend.py`：**Ontology SQL 最小前端**（对标 Palantir Ontology SQL 2026-11-12 GA）——
  `SELECT/FROM/WHERE/GROUP BY/ORDER BY/LIMIT/OFFSET` 子集，FROM 支持对象类型、派生类型与链接路径
  `(a:Type)-[:link]->(b:Type)`；参数 `:name`。**红线：SQL 只做编译器，产物进同一查询树 IR，
  不新建执行引擎**——TBox 校验/行级列级安全/聚合全部复用
- 派生对象类型 `derived_types`（Palantir derived object type）：`CarrierResource`（司机∪车辆）、
  `DisputedFee`（DISPUTED 费用视图）；查询引擎 `from:派生类型` 递归求值 + `DERIVED_CYCLE` 循环检测；
  类型发现（`object_type_search`）kind=derived 可搜到
- 链接基数 `cardinality`（one_to_one/one_to_many/many_to_one/many_to_many）：TBox 声明 +
  写入校验（活跃关系唯一性、时态关闭释放槽位），缺省 many_to_many 向后兼容
- 函数 `object_set` 入参（Palantir Function 的 ObjectSet 形态）：`fee_breakdown_batch`
- 暴露面：REST `POST /freight/object-set/query`、`POST /freight/object-set/sql`；
  MCP `ontology.query(query_tree)`、`ontology.sql`
- 生成物：TS/Python/OpenAPI 三端含命名派生类型（`x-derived`）、`{Name}Filters`、查询工厂

### 修复
- `FunctionRegistry.resolve`：支持 `module.Class.method` 多层回退 + 实例注入
  （此前 `fee_breakdown`/`quote_reconcile` 注册实际调用即 FUNCTION_IMPL_BROKEN）
- 本体派生类型属性表去掉 `object_id`/`name`（行字段非 props，谓词层不可用）

### 承重测试
- 148→165 项 pytest：SQL/DSL 对拍（同语义字节级一致）、SQL 不绕过 TBox/安全、
  派生类型三端生成 + 删派生类型驱动客户端变化、基数写入拦截、tsc 真编译（删类 TS2305）

## 2.2.0 (2026-11-19) — Palantir 追齐层第一轮


### 新增
- `enhancements/` 包：属性类型注册表（Shared Property Types）、Interfaces 对象多态、安全层（markings 强制控制 + 行级/列级安全策略 + scope token + 策略模拟器）、函数注册表、子进程沙箱、使用度量、对象搜索
- `tools/gen_osdk.py`：从本体 JSON 生成 TypeScript + Python 类型化客户端（OSDK-lite）
- `tools/proposal.py`：本体变更分支 + 评审合并流程（proposal）
- pytest 测试套件（`tests/`），每项新能力配承重测试
- `pyproject.toml` 依赖清单、`README.md`、`LICENSE`、本文件

### 变更
- `freight/fact_store.py`：并发修复——WAL 模式 + 线程本地只读连接，读不再阻塞写（写仍由 api 层串行锁保护）
- `freight/fact_mcp.py`：FactStore 初始化传入 db_path

### 暂缓（边界决策 2026-11-19）
- Action 动作治理（通知副作用 / writeback 回写 / submission criteria）——既有 15 个动作维持现状
- WebSocket 对象订阅（依赖 writeback，降级为版本变更事件）

## 2.1.0 — freight 语义层 v2.1.0

558 类 / 568 关系 / 16 规则 / 15 动作；Owner 元数据 100%；发布快照 1.1.0-demo、1.3.1-demo。
