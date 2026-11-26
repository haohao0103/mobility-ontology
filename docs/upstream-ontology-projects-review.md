# 三项目借鉴台账（OntoSmith / ontogeny / auto-ontology）

> 用途：外部项目调研结论的**落档**（避免重复调研、口径可追溯）。每条结论都带文件:行号证据。
> 更新：2026-11-26（新增 §5 星标实测建账、§6 11 项开源本体项目分级独立复核；§0 表加星标列）。
> 关联：长文交叉验证见 `docs/ARTICLE_20261126_CROSSCHECK.md`（含长文点名的 utopia /
> semantica / operational-ontology / omnigraph 星标实测）。

---

## 0. 身份核对（先验户口，再谈借鉴）

| 项目 | 上游 | 本地位置 | 星标（2026-11-26 实测） | 核对结论 |
|---|---|---|---|---|
| OntoSmith | `github.com/Dongbumlee/ontosmith` | 仅历史克隆（已清理） | 300 | 4.0.1 已落地 pin + 引用溯源 |
| ontogeny | `github.com/apache/hugegraph-ai`（子项目 `ontogeny/`） | `/tmp/hugegraph-ai/ontogeny` | 149（父仓） | 4.0.2/4.0.3 已落地 evolve+documents 机制 |
| auto-ontology | `github.com/NVIDIA/auto-ontology` | `/tmp/auto-ontology`（上游）+ `/Users/mac/Desktop/apache-code/auto-ontology`（**用户 fork**） | 14,936 | ★ 见下 |

★ **fork 核对结论**（2026-11-25）：`/Users/mac/Desktop/apache-code/auto-ontology` 是用户自己的
fork（`git@github.com:haohao0103/auto-ontology.git`），基于 NVIDIA 上游、落后 2 天；
37 处文件差异**全部在 `connectors/` 与 `retrieval/text_to_sql/` 侧**（bigquery connector、
sqlglot 升级、fk_values 等），**本体/术语侧与上游一致**。因此本台账对 auto-ontology 的结论
对 fork 与上游同时成立；引用该副本的任何"现状"前仍应比对（fork 分叉会走偏）。

---

## 1. auto-ontology：它有"自动构建本体"的能力，但构建的是**目录语义层**（不是业务本体）

### 1.1 它的"本体"是什么

证据（上游 `/tmp/auto-ontology`）：

- `auto_ontology/semantic/term_extractor.py:5` — 文件 docstring：**"LLM Term extraction —
  names and column display-name assignments only"**。
- `auto_ontology/semantic/models.py:47` — `RawTermProposal`：**"LLM output: one business Term
  with column assignments only"**，且 `model_config = ConfigDict(extra="forbid")`。
- `auto_ontology/semantic/term_extractor.py:50-68` — 提示词五条规则：①强烈偏好**一张表一个
  Term**（只有真 junction 表才拆两个）②Term 名用友好名 ③**每个候选列归属恰好一个 Term**
  ④**"Do not propose IS_A, PART_OF, or ROLE relationships."** ⑤变体表（ARC/HIST/STAGING…）
  用限定词前缀区分。
- `auto_ontology/catalog/ingest.py:28` — `ingest_catalog`：抽取 connector 的 catalog → 写 store
  → 产出 Table/Column 节点（**物理层**）。

即：它的本体元素 = **Term（一张物理表的业务语义名）+ 该 Term 的列归属 + 数据边（FK/join）**，
**没有**对象类型/关系/主键/状态机/分类组。

### 1.2 闭环到哪一步

```
DB ──connectors──▶ catalog/ingest ──▶ Table/Column 节点（物理 schema 入库）
                                          │
                     term_extractor（LLM）  │ 一表一 Term + 列全分配（禁结构断言）
                                          ▼
                     Term 图谱（dal/terms.py）+ 语义嵌入（semantic/embed.py）
                                          │
        term_families（孪生表家族识别）──▶ retrieval（entity_coverage / clarify / text_to_sql）
```

**是"自动构建"，但构建的是可检索的术语目录**：从真实数据库反向工程出"这张表在业务上叫什么、
每列叫什么"，供问答与 text-to-SQL 使用。**它不产出业务本体**（类/关系/状态机），也不做结构断言
——第 ④ 条提示词规则是硬边界。

### 1.3 与我方的差异（决定"能不能替代"，而不是"好不好"）

| 维度 | auto-ontology | 我方 mobility-ontology |
|---|---|---|
| 本体元素 | Term（表级）+ 列映射 + 数据边 | 对象类型 500 类 + 关系 503 条（domain/range/cardinality）+ 主键 + 状态机 + 35 分类组 |
| 驱动方向 | **数据驱动**（DB → 本体） | **业务驱动**（业务方口语 → 建模） |
| LLM 边界 | 只提术语（禁 IS_A/PART_OF/ROLE） | 提议通道封闭目录 7 类 + 独立裁决者 + 人审（**结构层已开口但全 t3**） |
| 状态机 | 无 | 有（8 个状态机段 + 15 actions 迁移边） |
| 分类/分组 | `term_families`（孪生表家族） | `object_type_groups`（35 组）+ `derived_types` |
| 发布与审计 | 无 pin 概念 | `payload_sha256` pin（fail-closed）+ 变更闸门 + 台账 |

**结论**：auto-ontology **不能替代**我方建模流程（它回答"这张表叫什么"，不回答"这个域有哪些
对象、它们怎么流转"）。但它提供两条我方尚缺的输入路径。

### 1.4 可借鉴的两条（本轮新增结论）

1. **DB 反向工程 → 候选来源**（增量价值高，我方尚无）
   我方将来若接 TMS/ERP 真实库：物理表/列 → LLM 抽业务术语 → **作为 `class_add` 提案的候选
   来源**（走既有提议通道：确定性门禁 → 裁决者 → 人审 → 人改 build 源）。这与"业务方口语"是
   **两个互补输入源**，不是替代关系。注意我方现状：`fact_store` 仍是 `seed.py` 硬编码演示数据，
   真实数据源接入尚未开始（P2 断言溯源蓝图已备）。
2. **`term_families` 的孪生/镜像边识别 → "类家族"识别**（可落地）
   算法（`auto_ontology/semantic/term_families.py:5-31`）：按表名前缀找家族；判定靠
   ①**镜像边**（`P_A.x — P_B.y` 与 `Q_A.x — Q_B.y` 同时存在）②**列集 Jaccard ≥ 0.8**
   （端点为镜像边时 ≥ 0.5）③**证据量达阈值**（"一个偶然同名不算家族"）④**同前缀传递扩展**
   （`DIM_DATE` 不能把家族串过去）；历史表（`*_HIST`）永不孪生。
   映射到我方：我方 500 类里大量同构类（各域的运单/订单/单据），这个算法可改造成
   **"新类是不是已有同构类的变体"检查**（重复建类防护）+ **分类组自动建议**（R5 MECE 辅助），
   可直接挂在提议通道的 `class_add` 前置校验上。**注意需改造**：它比的是"表列集"，我方比的是
   "类属性集 + 关系端点"，不能照抄。

### 1.5 不搬

- `text_to_sql/`（119 文件检索层大半）：我方是 FactMCP 工具调用 + 确定性函数层，路径不同。
- `sql_expression` 指标体系：我方 `semantic.py` 已有确定性函数层。
- `entity_coverage/`、`interactive/clarify.py`：**已于 4.0.2 搬完**（覆盖率数值化 + 澄清循环）。
- `term_families` 直接移植：需改造（表→类），见 1.4-2。

---

## 2. ontogeny（apache/hugegraph-ai）

- **evolve**（封闭变异目录 + 后置校验 + 分级 + decline 不蒸发）→ **4.0.2/4.0.3 已落地**
  （我方 `freight/proposals.py`）。
- **documents 五表**（断言级 provenance / ExtractOrigin / signature 去重 / 原始输出不可变 /
  溯源三元组）→ **未实施**，设计记录在 `docs/assertion-provenance-design.md`（接真实数据源时启用）。
- **不搬**：完整 RSI 自动演进循环（我方无自动演进需求）、`ontology.yaml` 的 apiVersion/kind
  对象模型（表达力未超 `semantic_layer.json`）、`queue.py` 持久工作队列（我方同步 HTTP 无批处理
  队列场景）、promotion gate 四条件（我方 pin 已覆盖"身份必须比对而非回显"）。

---

## 3. OntoSmith（Dongbumlee/ontosmith）

- **发布 pin + 结论引用溯源** → **4.0.1 已落地**。
- **Pass D 裁决层**（八条裁决纪律 / keep→retype→veto / R4 retype_attempt / 裁决输出再过确定性
  不变量 / 入库独立审计）→ **4.0.3 已落地**（我方裁剪为四类变异的定制合同）。
- **未搬**：八条纪律原文（针对开放三元组抽取）、晋升门四条件、replay 复算 + claim index
  （设计记录在 `docs/replay-and-claim-index-design.md`，有对外指标时启用）。

---

## 4. 落账原则（沿用）

- 结论必须带**文件:行号**证据；引用外部项目现状前先验"户口"（本台账 §0）。
- 分母定义与来源一并落档（比率类结论）。
- 同一约束**只留一处实现**：外部项目的校验器与本仓 `validate_semantic` 重叠时，以本仓为
  权威事实源，外部的定位降级为"起草期自检"（见 `docs/MODELING_METHODOLOGY.md` §三）。

---

## 5. 星标实测建账（2026-11-26）

首次建账（此前本仓**未记录任何外部项目星标**，全仓 grep `644` 零命中 ⇒ 不存在"沿用旧数字"）。
数据源：GitHub API `GET /repos/{owner}/{repo}` 的 `stargazers_count`，实测日 2026-11-26。

| 项目 | repo | 星标 | 说明 |
|---|---|---|---|
| Graphify | `Graphify-Labs/graphify` | 153,395 | 长文所称"吃 45.8%"的仓库；做"代码/文档/SQL schema → 可查询知识图谱"，属**图谱检索增强**侧 |
| auto-ontology | `NVIDIA/auto-ontology` | 14,936 | 本台账 §1 项目 |
| semantica | `semantica-agi/semantica` | 14,818 | 长文点名；"Graph-Native Semantic Layer"，★ 与 auto-ontology 星标极接近，**极易互串** |
| omnigraph | `ModernRelay/omnigraph` | 3,283 | 长文点名 |
| OntoSmith | `Dongbumlee/ontosmith` | 300 | 本台账 §3 项目 |
| operational-ontology | `gura105/operational-ontology` | 221 | 长文点名；"业务对象/属性/动作/权限"四件事结构 |
| hugegraph-ai | `apache/hugegraph-ai` | 149 | 本台账 §2 父仓 |
| utopia | `maxfraieho/utopia-arm64-deploy` / `baranberkay96/utopia-railway` | 0 / 0 | 长文点名的"双时钟"技术范例；**星标非其论据** |
| Ontology-Playground | `microsoft/Ontology-Playground` | 3,105 | 长文点名（2026-11-26 补核：微软的**本体教学** web app）；"Ontology-Playground 未唯一定位"已于本次定位 |

★ 数字串台纠正（跨项目数字严禁混用）：长文核到的 **14,174 是 `semantica`**，不是 `auto-ontology`；
两者量级相同（14,818 vs 14,936），引用时必须写全 repo 全名。

★ 引用纪律：**星标只作事实记录，不作借鉴排序依据**（长文自己亦反对"星标=价值"；
我方的借鉴排序依据是能力核对，见 §1.3 对照表）。

★ "45.8% 不自洽"其实是**跨时点混算**，不是原文错：原文 45.8% 与总星标 330,715 同取 2026-11-21，
自洽；用 2026-11-26 实测的 153,395 ÷ 11-21 的 330,715 得 46.4%。引用须带时点。

---

## 6. 11 项开源本体项目分级（2026-11-26，能力核对 + 星标实测）

> 背景：另一项目（货拉拉 PoC，见文末说明）对 11 个开源本体项目做了重分级与候选池筛选，
> 分级依据四维度：**是否自动学习本体结构 / 是否输出 OWL/RDF / 是否有形式化推理 /
> 是否仅为图基础设施**。本节是我方对该分级的**独立复核**（README 原文关键词 + GitHub API）。
> ★ 该 PoC 的报告文件**不在本机**（`huolala_ontology_poc/` 在 `/Users/mac/Desktop/code` 下不存在）
> ⇒ 本节只核对用户转述的摘要，**原文表格未逐行核对**。

### 6.1 逐项复核（★ = 我方相对其分级的修正/精确化）

| 项目 | repo | 星标 | README 实测到的关键声明 | 分级复核 |
|---|---|---|---|---|
| Graphify | `Graphify-Labs/graphify` | 153,395 | 代码/文档/SQL schema → 可查询知识图谱；tree-sitter AST | D ✅ |
| LightRAG | `HKUDS/LightRAG` | 40,870 | "Simple and Fast Retrieval-Augmented Generation" | D ✅ |
| GraphRAG | `microsoft/graphrag` | 36,757 | "graph-based Retrieval-Augmented Generation" | D ✅ |
| semantica | `semantica-agi/semantica` | 14,818 | SHACL constraints / OWL generation / SKOS / PROV-O / Rete+Datalog+SPARQL；"deterministic infrastructure (no LLM required)" | B ✅（非本体学习器） |
| auto-ontology | `NVIDIA/auto-ontology` | 14,936 | 无 OWL/RDF/推理关键词（§1 已核） | B→**D**（按 OWL 标准）✅ |
| omnigraph | `ModernRelay/omnigraph` | 3,283 | "typed ontology" 的对象存储原生图库 | D ✅ |
| Ontology-Playground | `microsoft/Ontology-Playground` | 3,105 | 教学用 web app | C ✅ |
| open-ontologies | `fabio-rovai/open-ontologies` | 2,963 | owl-rl 推理 + **derivation certificate**（"names each rule and each premise"）+ blast_radius 计算 | C ✅（★ 它**有**推理与证书） |
| Helix Foundry | `HelixDB/helix-foundry` | 2,193 | agent 起草 **types/links/rules** → "You review it, edit it" → 版本化；值带来源（表行/文档引文）；文档冲突挂起待审 | A ✅ |
| EvoOntology | `ruc-datalab/EvoOntology` | 1,332 | "builder initializes grounded semantic objects from the workload and underlying data"；Gated versioning | A ✅（★ 门禁性质见 6.3-2） |
| utopia | `maxfraieho/utopia-arm64-deploy` / `baranberkay96/utopia-railway` | **0 / 0** | "Utopia Knowledge Base & Temporal Graph Engine"（双时态吻合） | B ✅（★ 生产不可依赖，见 6.3-3） |

### 6.2 一条可量化的选型准则（比"星数≠价值"更强）

本样本 11 项中，**星数前三（153,395 / 40,870 / 36,757）全是图检索增强类（D 级）**；
真正的本体构建核心（EvoOntology 1,332、Helix Foundry 2,193）**低 70~115 倍**。
⇒ 在这批项目上，**星数与本体构建能力完全反相关**。选型排序依据只能是能力核对（README/代码），
星标只当关注度指标。

### 6.3 三条该分级未覆盖的风险（本节新增）

1. **OWL/SHACL 校验通过 ≠ 本体连贯** —— `open-ontologies` README 自带活证据：
   它演示 **"The OWL file passes all three checks, and the OWL file is not coherent"**
   （OWL 文件通过三项检查，但本体不连贯）。⇒ 若把"OWL/RDF + SHACL + CI"当作可信度保证书，
   是误读；真正决定可信度的是**变更前后逐键差异是否被审过**（我方 `change_gate` +
   `validate_semantic` 逐键 diff 正是这一层）。
2. **EvoOntology 的 "gated versioning" ≠ 语义门禁** —— README 原文：
   *"Publish a Candidate only when paired evaluation shows a reproducible improvement over its Parent."*
   ⇒ 它是**行为级效果门禁**（新版本在配对评测上更好才发布），
   **不保证新版本结构合法**。作"规则上线门禁"方向对，但不能当结构合法性保证。
3. **utopia 0 星 ⇒ 推理层不宜押它** —— 两个 repo 均 0 星（个人项目量级）。
   PoC 阶段可用作"双时态 + 本体包"设计参考，**生产依赖不可接受**；
   推理层应优先 `semantica`（14,818 星、活跃维护、OWL 生成 + Datalog + SHACL 一套齐）。

### 6.4 与本仓的关系：Helix Foundry 是**同构参照物**，不是补件

| 环节 | Helix Foundry | 本仓 mobility-ontology |
|---|---|---|
| 起草 | agent 起草 types/links/rules | AI 起草候选（提议通道 7 类封闭目录） |
| 审核 | 人 review / edit | 确定性门禁 → 独立裁决者 → **人审（结构层全 t3）** |
| 溯源 | 值级溯源（表行 / 文档引文） | `why(R编号)` 规则级溯源；★ **值级溯源未落地**（`fact_store` 仍是 `seed.py` 演示数据，P2 蓝图见 `docs/assertion-provenance-design.md`） |
| 生效 | 版本化 → load 进图 | 编译 → `payload_sha256` pin fail-closed → 校验 |

⇒ 引入 Helix Foundry **补不上我方缺口**（缺的是真实数据源 + 断言级溯源，不是起草能力）；
它值得看的地方只有一处但很关键：**值级溯源的做法**。
另一路线冲突需先定：**EvoOntology 的"自演化"= 无人审的结构变更**，
与本仓红线（结构变更必人审、`auto_merge` 恒 False）**方向相反** ⇒ 可作对照观察，不建议接回主链。

### 6.5 说明：本节的 PoC 背景

货拉拉 PoC 候选池（骨架 LinkML+Protégé+rdflib/ELK；构建 EvoOntology+Helix Foundry；
推理 semantica/utopia；治理 open-ontologies；排除 Graphify/LightRAG/GraphRAG）属**另一项目**的
选型决策，本仓只做**外部能力复核 + 与本仓关系判断**。
★ 若该 PoC 最终要**接回本仓主链**，则"OWL 中间层"是重复投资
（本仓 `semantic_layer.json` 从不是 OWL，17 个阶段未转 OWL 亦无损失），
真正该引入的是"值级溯源"；若 PoC **独立不回接**，则 OWL 生态兼容与 SHACL 工具链有其合理性。
该分叉需项目方裁定。


