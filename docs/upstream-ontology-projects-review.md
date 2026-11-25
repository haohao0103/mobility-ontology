# 三项目借鉴台账（OntoSmith / ontogeny / auto-ontology）

> 用途：外部项目调研结论的**落档**（避免重复调研、口径可追溯）。每条结论都带文件:行号证据。
> 更新：2026-11-25（auto-ontology 深挖 + 副本核对）。

---

## 0. 身份核对（先验户口，再谈借鉴）

| 项目 | 上游 | 本地位置 | 核对结论 |
|---|---|---|---|
| OntoSmith | `github.com/Dongbumlee/ontosmith` | 仅历史克隆（已清理） | 4.0.1 已落地 pin + 引用溯源 |
| ontogeny | `github.com/apache/hugegraph-ai`（子项目 `ontogeny/`） | `/tmp/hugegraph-ai/ontogeny` | 4.0.2/4.0.3 已落地 evolve+documents 机制 |
| auto-ontology | `github.com/NVIDIA/auto-ontology` | `/tmp/auto-ontology`（上游）+ `/Users/mac/Desktop/apache-code/auto-ontology`（**用户 fork**） | ★ 见下 |

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
