# 可溯源本体建模方法论（重建版）+ AI 快速建模工作流

> **诚实边界**：原文只说"依据我们的方法论"，**未公开方法论原文**。本文是我依据原文对四类建模问题的描述，结合业界成熟方法论（BFO 的 continuant/occurrent/role 区分、PROV-O 的事件溯源、OWL2 的 disjoint/covering）**重建的一套等价可落地规则**。它不是作者方法论的原文复现，而是一套能达成同样效果（每个建模选择可回溯）的替代方案。

---

## 〇、你真正缺的那一环，在哪里

先说清楚现状，避免重复建设。我核查了 `mobility-ontology`：

**已有的（很强的底座）**：

| 环节 | 现有工具 | 做了什么 |
|---|---|---|
| 建模 DSL | `ontology/__init__.py` 的 `C()/L()/I()` | 声明对象类型/链接/接口，属性类型**强制人工标注**，禁止脚本猜 |
| 人工建模 | `ontology/domains_01~06.py` + `core_classes.py` | **500 类、503 关系是人工一个一个 `C()` 写出来的** |
| 渲染 | `tools/build_ontology_v3.py` | 合并建模包 → 注册表 → 结构校验 → `semantic_layer.json`（**唯一渲染器**） |
| 结构层加工 | `tools/build_ontology_v4.py` | 在 v3 渲染结果上做**限界上下文 × 聚合 × 边界链接**（读 `semantic_layer.json` 再写回，幂等）——不是渲染器，别当入口用 |
| 分支评审 | `tools/proposal.py` | create→review→merge，只放行 PASS 分支，生成 release 快照 |
| 变更门禁 | `freight/change_gate.py` + `breaking_approvals.json` | 破坏性变更要签字，路径完全匹配 |
| 多格式导出 | `tools/export_shacl.py`、`scripts/benchmark/export_owl.py` | 已能导出 SHACL-TTL 和 OWL-Turtle（JSON 也是源格式） |
| **AI 候选通道** | `freight/proposals.py`（结构层提议目录） | AI 产出的类/关系/分类归属走封闭目录 + 后置校验 + 独立裁决 + 台账 → 人审 → 人改 build 源 → 重 build 出新 pin；**全 t3 人审，AI 永不自动进生产** |

**唯一真缺的**：从「业务方用自己的话说清对象和关系」到「写出 `C()/L()` 声明」之间那一步。现在这一步是**纯人工**——这就是 500 类写得慢、建模门槛高的根因，也正是文章说"本体建模可全程由 AI 完成，前提是按可溯源方法论"要解决的那一步。

文章里那句话拆开是两个要求：

1. **快**：业务方用自然语言说，AI 整理成模型，不用人手敲 500 个 `C()`；
2. **可回溯**：每个建模选择都能回到方法论的一条理由，事后能复查——**这和"把一段业务描述直接丢给 AI 自己决定怎么分对象"是两回事**。

本文给出满足这两点的方法论 + 工作流 + 可运行校验器原型。

> **2026-11-25 增补**：AI 候选的进生产通道已开通（`freight/proposals.py` 结构层提议
> 目录 `class_add`/`relation_add`/`group_add_type`）。AI 产出的候选不再是"给人看的
> 草稿"，而是**走提议通道**：确定性门禁 → 独立裁决者（语义裁决）→ 台账 → 人审 →
> 人改 build 源 → 重 build 出新 pin。**AI 候选永不自动进生产**；结构层 why(R10)
> 必填，"删除类/关系/规则"仍不在目录里（目录只开「新增」不开放「删除」）。

---

## 一、方法论：10 条可判定的建模规则

每条规则给出：**判定标准**（怎么判断）、**理由编号**（回溯用）、**正反例**。AI 建模时每个输出都要带 `why: R编号 + 一句理由`；校验器会检查每个建模选择是否都有理由。

### 第一类：什么算一个对象（对应原文"什么算业务对象"）

**R1 — 持续性实体才建对象类型（Continuant）**
- 判定：有独立身份（主键）、可在多个时间点被引用、不依附于某一次具体活动 → 建 `C()`。
- 反例："订单负责人"不是对象——它是司机在某个时间点承担的职责，不建 `OrderOwner` 类（见 R2）。
- 理由格式：`R1: <实体> 有独立主键 X，跨时间稳定，故建对象类型`。

**R2 — 职责不是对象，建成关系/属性限定（Role）**
- 判定："某人当时承担的岗位/角色"若不脱离人独立存在 → **不建类**，建成关系（如 `waybill_assigned_driver`）或带时间的关系端点。
- 判据：问"这个人换岗后，这个东西还在吗？"——不在了就是角色，在才是对象。
- 反例警示：为每个角色建一个类，是类爆炸的主要来源（500 类里若有水分，多半在这）。
- 理由格式：`R2: <职责> 依赖于 <人> 与 <情境>，人换岗后不存在，故不建类，用关系 <rid> 表达`。

**R3 — 已发生、要追责的事建成事件类（Occurrent）**
- 判定：有 `occurred_at`、`actor`、需要回答"谁在什么时候做了什么/为什么状态变了" → 建独立事件类，不用状态字段糊过去。
- 判据：问"这个状态是Enum里改一下就算，还是需要证明它真的发生过？"——后者建事件。
- 正例：`OrderApproval`（有 decided_at/approver_id/decision）是事件；`AuditLog` 是事件。
- 反例：把"是否已审批"只做成 `is_approved:bool` 字段——回答不了"谁批的、凭什么"。
- 理由格式：`R3: <事> 有发生时间与行为人且要追责，故建事件类 <C>`。

### 第二类：证据与边界（对应原文"原始记录只被引用、不被改动"）

**R4 — 原始证据只建引用，不并入字段（Evidence by reference）**
- 判定：来自源系统的原始行/单据是证据 → 只记 `source_x_ref`，**不把证据内容并进推导对象**。
- 与 mobility 对齐：`persisted_by_ontology: false` 红线 + `source_a_ref/source_b_ref` 模式。
- 理由格式：`R4: <证据> 来自源系统 X，只读引用 <ref 字段>，不并入 <对象>`。

**R5 — 分类必须互斥且完备（MECE / disjoint + covering）**
- 判定：同一维度的枚举/概括集，取值之间**不能同时成立**（互斥），且覆盖所有情况（含 `OTHER`/`UNKNOWN` 兜底）。
- 判据：任取两个值问"一个对象能同时是 A 和 B 吗？"——能，则违反互斥。
- 与 mobility 对齐：文章 10 个概括集互斥完备 ↔ mobility 的 enum statuses + `object_type_groups` 35 组。
- 理由格式：`R5: <枚举> 的取值 {..} 两两互斥且含 OTHER 兜底，覆盖完备`。

### 第三类：属性与关系的硬约束（对应原文"哪些分类不能同时成立"和 mobility 既有铁律）

**R6 — 每个属性必须显式标注类型，禁止按命名猜（No type inference）**
- 判定：属性必须指定 mobility 的 11 种类型短码之一（id/str/txt/dec/int/num/bool/dt/date/json/enum）。**AI 也不许猜**——猜类型正是"虚胖"之路。
- 与 mobility 对齐：`ontology/__init__.py` 原文"属性类型必须人工标注，禁止脚本靠命名猜类型"。
- 理由格式：`R6: <属性> 为 <类型码>，依据 <业务语义说明>`。

**R7 — 每个对象必须有稳定主键，身份与可读名分离（rid ≠ label）**
- 判定：类必须有 `key`，且 key 在 props 中；改名不改身份（rid 确定性推导）。
- 与 mobility 对齐：`rid_for()` + `rid_ledger.json` 台账。
- 理由格式：`R7: <类> 主键 <key>，rid 由确定性推导，改名不断链`。

**R8 — 关系必须有 domain/range/cardinality，不许默认糊过去**
- 判定：每条 `L()` 必须显式写基数（one_to_one / one_to_many / many_to_one / many_to_many），根据业务语义定，不用默认值兜。
- 与 mobility 对齐：`LEGACY_CARDINALITY` 注释"基数错会让写入校验放过真实违规"。
- 理由格式：`R8: <关系> 基数 <card>，依据 <业务事实>`。

**R9 — 有状态的对象必须声明状态机（State machine completeness）**
- 判定：类有 `statuses` → 必须声明初始态、终态、合法迁移；不能只给一堆枚举值。
- 与 mobility 对齐：L16 承重用例"状态机完备性"。
- 理由格式：`R9: <类> 状态机初态 <s>、终态 <t>，迁移集 {...}`。

### 第四类：可回溯本身（对应原文"每个建模选择都能回到方法论一条理由"）

**R10 — 每个建模选择必须带 why，且指向一条规则（Traceable rationale）**
- 判定：AI 输出的每个 class / relation / enum / property，都要有 `why` 字段，格式为 `R编号: 理由`。**没有 why 的建模选择一律不许进入候选模型**。
- 这是把"AI 自由发挥"和"按方法论建模"区分开的那道闸：自由发挥的模型也能看起来完整，但选择没有可回溯依据。
- 校验器会强制检查：`why` 缺失或格式不符 → 报错，模型不予渲染。
- 理由格式：`R10: 本条选择由 R<n> 支撑，<具体事实>`（R10 是元规则，实际 why 指向 R1–R9 之一）。

---

## 二、AI 快速建模工作流（怎么把业务方的话变成 TBox）

```
业务方自然语言（一段话 / 录音转写 / 需求文档）
        │
        ▼
Step 1  按 R1–R3 做「建模决策清单」
        AI 逐句判断：这是对象？职责？事件？
        每个判断带 why（R1/R2/R3）
        │
        ▼
Step 2  按 R4–R9 补全结构
        证据引用 / 分类互斥完备 / 属性类型 / 主键 / 关系基数 / 状态机
        每个补全带 why
        │
        ▼
Step 3  产出结构化候选 JSON（见原型 schema）
        {classes:[...], relations:[...], enums:[...], decisions:[{选择,why}]}
        │
        ▼
Step 4  ★ 确定性校验器（本原型 validate_model.py）
        逐条检查：why 完整性 / R5 互斥完备 / R6 类型合法 /
                  R7 主键唯一 / R8 基数合法 / R9 状态机
        FAIL → 退回 Step 2 修正；PASS → 进入人工确认
        │
        ▼
Step 5  人工确认（业务方 + 建模者）
        核对每个 why 是否符合业务事实；这一步不能省——"担责任的事留给人"
        │
        ▼
Step 5.5 ★ 提交结构层提议通道（freight/proposals.py，2026-11-25 新增）
        候选 JSON → class_add/relation_add/group_add_type 提案（why(R10) 必填）
        → 确定性门禁：编译 after 草案 → validate_semantic 逐键 diff
        → 独立裁决者（R1/R2/R3 判别：对象？职责？事件？）→ 台账留痕
        → 人审通过后，人把它敲进 ontology/domains_XX.py（AI 永不自动进生产）
        │
        ▼
Step 6  渲染进现有工具链（都是现成的）
        候选 JSON → 生成 ontology/domains_XX.py 的 C()/L() 声明
        → tools/build_ontology_v3.py 渲染 → semantic_layer.json
        → tools/export_shacl.py / scripts/benchmark/export_owl.py 导出多格式
        │
        ▼
Step 7  走既有门禁（都是现成的）
        tools/proposal.py 分支评审 → freight/change_gate.py 签字 → release 快照
```

**关键点**：Step 4 的校验器是"可溯源"的落地点——它不判断建模对不对（那是 Step 5 人的事），只判断**每个选择有没有理由、理由编号是否合法、硬约束是否满足**。这正对应原文"AI 按规则整理成模型，每个选择能回到一条理由，事后可复查"。

---

## 三、原型：可运行的东西（落盘位置 `tools/`）

| 文件 | 作用 |
|---|---|
| `tools/validate_model.py` | 校验器：输入候选 JSON，按 R5–R10 逐条校验，输出 C()/L() 声明草案 + 校验报告 |
| `tools/roundtrip_candidate.json` | 往返验证候选（8 类，字段/状态保真 53/53） |
| `tools/roundtrip_business_text.md` | 盲测/往返验证用的口语样例（业务方原话） |
| `tools/modeling_prompt.md` | AI 生成候选的 system/user prompt 模板 |

运行：

```bash
python3 tools/validate_model.py tools/roundtrip_candidate.json --emit-dsl
```

预期：PASS 后输出 `C()`/`L()` 声明草案（供人工确认后合入 `ontology/domains_*.py`）。

### ★ 两套校验的分工（2026-11-25 核实补充，别混淆）

| | `tools/validate_model.py` | `freight/semantic.py::validate_semantic` |
|---|---|---|
| 位置 | `tools/`（建模期工具） | `freight/`（运行时库） |
| 查什么 | **候选 JSON 的可溯源性**：why 完整性 / 规则号合法 / 硬约束（类型码、主键在属性表、基数、状态机声明、兜底值） | **发布物的引用完整性与结构约束**：96 个检查码（引用存在性、属性注册、状态机完备、规则 AST、定价矩阵、动作端点……） |
| 地位 | **事前自检工具**（原型：无测试、不挂 CI，仅 `exit 1/0`） | **权威单一事实源**（`scripts/semantic_gate.py` 32 项门禁 + 运行时加载复核） |

**同一批约束存在两处实现，漂移风险是真的**：候选 JSON 通过了 `validate_model.py`
不代表能进发布物——真正的判定是 `validate_semantic` 对 **after 草案**的逐键 diff
（提议通道 `freight/proposals.py` 已把这条链接上：结构层提案编译成 after 草案后，
门禁用 `validate_semantic` 判"这次变更有没有把本体改坏"，只认 after 引入的新错误，
存量问题不算提案的错）。

**`validate_model.py` 现阶段定位是"起草期自检"**，不是准入闸门；在其规则与
`validate_semantic` 合并/对齐之前，不要把它当权威判据引用。

---

## 四、和文章原文的对应关系（逐条对齐，证明这套重建是等价的）

| 原文要求 | 本文规则 | 效果 |
|---|---|---|
| 什么算一个业务对象 | R1 | 持续性实体建对象类型 |
| 什么只是某人当时承担的职责 | R2 | 职责建成关系，不建类 |
| 什么是已经发生、必须留下记录的事 | R3 | 事件一等公民，不用状态字段糊 |
| 哪些分类不能同时成立 | R5 | 互斥且完备 |
| 原始记录只被引用、不被改动 | R4 | 证据引用不并入 |
| 每个建模选择能回到方法论一条理由 | R10 | why 字段强制校验 |
| 导出 JSON/TTL/RDF/OWL，不用手写 OWL | 现有 `export_shacl.py`/`export_owl.py` | 已有 |
| 推理用自然语言校验，核对模型推出来的结论 | mobility 的门禁 + `smoke_tbox_mechanism.py` | 已有（确定性校验，非 LLM 推理） |

**一处差异要诚实说明**：文章说"推理也可以用自然语言交给 AI 校验"，mobility 走的是**确定性门禁**路线（`semantic_gate.py` 32 项语义门禁），不是让 LLM 读模型答题。两条路各有取舍：LLM 校验灵活但不可回放，确定性门禁可回放但不理解新场景。**建议保持 mobility 的确定性路线**——它和"可回溯"更一致；若将来要补 LLM 校验，应让 LLM 只做"提议"，结论仍由门禁裁定。

---

## 五、结论

你要的"快速高效本体建模"，缺的不是工具（渲染、导出、门禁、评审都已现成），而是**把人脑里那套建模判断显式化成 R1–R10 的规则，让 AI 按规则产出、让校验器强制每个选择带理由**。这样：

- **快**：业务方说话 → AI 出候选，不用手敲 500 个 `C()`；
- **可回溯**：每个选择有 `why: R编号`，事后能复查，和"AI 自由发挥"划清界限；
- **不重复建设**：产出直接进现有 `build_ontology_v3.py` → `export_*` → `proposal` → `change_gate` 链路。

下一步：跑原型验证 → 若认可，把 R1–R10 固化成团队建模规范，AI 生成候选时用同一套 prompt 模板，逐步把 500 类的维护成本降下来。
