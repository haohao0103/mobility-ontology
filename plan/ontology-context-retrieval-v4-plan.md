# 本体上下文与检索 v4 —— 彻底重构落地计划

> 状态：**待批准，未开始开发**。本文档是完整开发计划；批准前不改任何生产代码。
> 依据：业界方案调研（Palantir Foundry / TrustGraph / Text-to-SQL 工业界，见 §2）+ 本仓实测证据（§1）。
> 目标版本：`semantic_layer.json` v4.0.0；涉及 `ontology/`、`tools/`、`freight/`、`scripts/`、`tests/`。

---

## 0. 一句话结论

现在的系统是「35 个按类型名前缀切出来的平行域 + bigram 字符串匹配 + 按域裁目录（裁掉核心域就全链路降级）」。彻底方案是业界已验证的三件事：

1. **域声明重构**：35 平行域 → **6 个限界上下文（context）× 若干聚合（aggregate）**，跨聚合关系升级为**显式边界链接**（永不静默丢弃）；
2. **检索层重写**：bigram 单通道 → **五路召回（精确 / 词法 BM25 / 向量 / 图闭包 / 查询历史）+ RRF 融合 + 归一化阈值 + 敢返回零**；
3. **评估门禁**：从「无标注无指标」→ **80+ 条标注集 + recall@8 / MRR / 零命中准确率 CI 门禁**，本体每次变更强制重跑。

接口签名 `object_type_search(question, top_k, ...)` 保持兼容，换的是实现和声明，不是补丁。

---

## 1. 根因与实测证据（为什么要彻底改）

### 1.1 域声明问题

| 实测项 | 数值 | 结论 |
|---|---|---|
| 业务域总数 | 35 | 前 5 个核心域共 18 类；后 30 个 `*_domain` 是主题文件按 `group=` 字段声明的**类型名前缀族**，不是业务语义边界 |
| 类 / 关系 | 500 / 503 | 规模本身不是问题，**组织方式**是问题 |
| 关系跨域率 | 314/503 = **62%** | 平行域切法下，多数关系的两端不在同一域 |
| 核心 6 类 6 关系 | 全在 `order_transport` | 一旦被预算裁剪裁掉 → 目录「37 类型 0 关系」→ 规划解析失败 → 全链路第 2 步降级（`be967f2` 修复的正是这个） |
| 关系治理字段 | `owner` 全部 = `freight-core`、`source` 全部 = `handwritten` | 单一全局值 = 没有声明任何边界 |
| `ontology/__init__.py:32` | 30 个域配了图标颜色 | 域当前更像**展示分组**，不是限界上下文 |

### 1.2 检索问题（`freight/semantic.py:829-1007`）

| 缺陷 | 具体表现 |
|---|---|
| 2 字滑窗非分词 | 无词典/同义词/语义：「运单」命中 20 个 `Waybill*`；「多收钱了」「等待费」零召回 |
| 无 IDF、无归一化、无上界 | 高频字主导；同 term 在多个命中字段重复累加权重；跨问句分数不可比 |
| 无阈值、无零命中 | `score>0` 即收 + `top_k=8` 硬截 → 永远凑满 8 个假阳性，`NO_ONTOLOGY_MATCH` 纪律被抵消 |
| 召回源单一 | 只扫类 id/label/description/aliases/组文本，不查属性名、关系端点、术语表、历史问句 |
| 全表扫描、不可评估 | 500 类线性可跑，无倒排无向量；无标注集无回归指标，本体一改质量漂移无人知道 |

### 1.3 注入问题（`freight/agent.py:_step_catalog`，已两轮修补）

- seed 域（当前对象所在域）是**业务约束必选**——这是对前缀域的妥协：因为核心类型恰好全在 `order_transport`，才需要「必选」。域语义正确后，seed 应该是**当前对象的聚合闭包**，由声明推出而非靠运气。
- 预算护栏「放不下跳过」已修，但裁剪单位仍是域（14~23 类），粒度太粗，一裁就是几十个类型。

---

## 2. 业界方案对照（调研结论，借鉴什么抄什么）

| 来源 | 机制 | 本计划抄的部分 |
|---|---|---|
| **Palantir Foundry**（官方文档） | ① `Groups` 是**多标签**，仅用于 UI 筛选导航，不参与权限与上下文裁剪；② 「检索上下文」= 每条消息**确定性**注入（不是 LLM 决定给不给）；③ Object 查询工具 = 显式配置**对象类型 + 属性白名单 + 链接遍历**；④ 语义搜索 = 对**实例**做（OSv2 向量属性 + KNN），结果作为附加上下文；⑤ 元数据治理：`aliases` 官方定位就是「用户搜索时找到此 Object 类型的其他术语」 | ① 旧 35 域降级为多标签（展示用）；② 上下文注入确定性、轨迹可解释；③ 类型/属性白名单由声明推出；④ 检索信号以治理过的元数据（aliases/glossary）为一等公民 |
| **TrustGraph**（ontology RAG 指南） | 本体太大时**把本体本身存成图**，用向量相似度取「与当前输入相关的**本体切片（ontology slice）**」，LLM 只收到相关切片；OWL 三层保证类型精确；PROV-O 记录可查询的推理轨迹 | ① 切片思想 = 按问句取**聚合切片**而非按域切块；② 推理轨迹结构化（问句→命中通道→类型→路径→结论） |
| **Text-to-SQL 工业界**（Schema-First Retrieval / LinkAlign / DIN-SQL） | schema 检索是**一等检索问题**：语义增强检索 → 无关 schema 隔离 → 表字段抽取；catalog embedding vs 模板 BM25 提升 table recall@5 +32.8；**查询历史**是最强通道之一（52.1%→92.3%）；都有 recall@k 门禁 | ① 多路召回 + BM25 + 向量 catalog；② 查询历史通道；③ 标注集 + recall@8/MRR CI 门禁 |

（数字来自论文页/二手解读的检索摘要，未逐篇核全文；方向性结论多源一致，作为设计依据而非精确预期。）

**共同底线**：没有一家靠「把类型元数据按问题裁到几个域」给 LLM 圈上下文；范围是**显式声明 + 确定性闭包**，检索是**带索引带评估的一等子系统**，裁剪**永远不能裁掉关系**。

---

## 3. 目标与非目标

### 3.1 目标（验收口径）

1. 域声明：35 平行域 → 6 上下文 × 聚合两层结构，映射表人工审核入库；跨聚合关系 100% 显式边界链接。
2. 检索：五路召回 + RRF + 归一化阈值；检索结果带通道证据（每个命中类型标注来自哪个通道）。
3. 注入：任何问句下目录**关系数 > 0 且包含当前对象的聚合闭包**；预算裁剪单位 = 聚合；边界链接端点摘要永不丢失。
4. 评估：≥80 条标注集；**recall@8 ≥ 0.85、MRR@8 ≥ 0.60、零命中准确率 ≥ 0.80**（初版基线实测后可校准一次，校准需登记）；进 pytest 全量套件。
5. 端到端：知识型 / 实例型 / 「为什么这单贵 38」经典链路全部无降级完成（LLM 偶发格式失败除外，需显式标注非本次引入）。
6. STG 部署验证：`stg-121:8765` 真实环境 e2e 三链路 + 前端页面数值实时填充。

### 3.2 非目标（明确不做）

- 不改 ABox 种子事实、定价声明数值（唯一口径来源，动了才是造假）。
- 不做实例级向量检索（Palantir 的 KNN 语义搜索作用于实例；本项目演示重点是 TBox 检索，实例级留 v5）。
- 不做完整 RBAC 权限系统（`markings` 字段先声明占位 + 过滤下推接口，实现留后续版本）。
- 不引入重型依赖（图数据库、ANN 库）；500 类规模用本地文件 + 暴力 cosine 即可。
- 不动 `parity_lab.py` 刻意硬编码的对照组（那是对照实验设计）。

---

## 4. 总体架构

### 4.1 改造前后

```
【现在 v3】
问句 ──bigram 打分(全 500 类)──> top8 锚点 ──> 映射到 35 平行域 ──> 按域累加目录(6000字预算)
                │                                     │
                │                                     └─ 裁掉 order_transport = 0 关系 = 全链路降级
                └─ 「运单」命中 20 个 Waybill*；「多收钱」零命中但仍硬凑 8 个

【目标 v4】
问句 ──┬─ C1 精确通道( id/label/aliases/glossary 术语表 )
       ├─ C2 词法通道( 分词 + BM25, IDF + 字段权重 + 长度归一 )
       ├─ C3 向量通道( 类型卡 MiMo embedding, 离线构建 runs/type_vectors.json )
       ├─ C4 图通道( 命中类型沿关系闭包 1 跳扩展, 衰减 0.5 )
       └─ C5 历史通道( 问句→实际使用类型, runs/retrieval_history.jsonl )
                │
                └─ RRF 融合 + 归一化阈值(低于阈值丢弃, 零命中敢返回空+NO_ONTOLOGY_MATCH)
                │
   seed = 当前对象的【聚合闭包】(声明推出,非运气)     命中类型 → 所属聚合切片
                │                                          │
                └──────────────┬───────────────────────────┘
                               ▼
        目录注入 = seed 聚合 + 问句聚合切片(逐聚合实测字符数,放不下跳过)
                 + 跨聚合【边界链接端点摘要】(永不静默丢弃)
                               ▼
        轨迹: 每个命中类型带通道证据 + RRF 分数 + 阈值判定 + 零命中语义
                               ▼
        评估门禁: 80+ 条标注集, recall@8 / MRR / 零命中准确率, 本体变更必跑
```

### 4.2 域层级模型（v4 核心声明）

```
context（限界上下文, 6 个）
  └─ aggregate（聚合根, 聚合内强一致, 目录注入的基本单位）
       └─ types（聚合根 + 附属类型, 由声明归属而非前缀）
跨聚合关系 = boundary link（显式声明, 注入时保留端点摘要）
旧 35 域 → 降级为 type 上的多标签 labels（UI 筛选导航用, Palantir Groups 模式）
```

---

## 5. 数据模型设计

### 5.1 `semantic_layer.json` 新增/调整节点

```jsonc
{
  "meta": { "version": "4.0.0", ... },

  // 新增：限界上下文（6 个）
  "contexts": [
    {
      "id": "fulfillment",
      "label": "订单履约",
      "description": "从接单到签收的执行过程。唯一写入点：运单状态机。",
      "owner": "fulfillment-team",            // 替代全局 freight-core
      "aggregates": ["order", "waybill", "stop", "cargo", "track", "route", "warehouse"],
      "glossary_ref": ["运单", "单子", "这单", "货", "在途", "签收", "改派", "回单"],
      "version": "4.0.0"
    },
    // ... pricing_billing / capacity / evidence_exception / master_data / operations
  ],

  // 新增：聚合声明（聚合根 + 附属类型显式归属）
  "aggregates": [
    {
      "id": "waybill",
      "context": "fulfillment",
      "root": "Waybill",
      "label": "运单聚合",
      "types": ["Waybill", "WaybillVersion", "WaybillAmendment", "...20 个"],
      "invariants": ["运单金额 = 各费用项之和", "状态转移只能走 state_machines 声明"],
      "labels": ["waybill_domain"]          // 旧域 ID 降级为多标签（兼容 UI）
    },
    // ...
  ],

  // 新增：业务术语表（检索一等公民；来源=类 aliases + 人工审核 + LLM 离线生成候选）
  "glossary": [
    { "term": "等候费", "synonyms": ["等待费", "压车费", "待时费"],
      "colloquial": ["白等了", "等着卸货的钱"],
      "type_ref": "FeeItem.waiting", "context": "pricing_billing" },
    { "term": "多收钱了", "synonyms": ["贵了", "超收", "多扣了"],
      "colloquial": ["被坑了", "怎么这么贵"],
      "type_ref": "FeeVariance", "context": "pricing_billing" }
    // ...
  ],

  // classes[]：新增 primary_aggregate 字段（恰属一个聚合）；旧 group 降级为 labels 数组（可多个）
  // relations[]：新增 boundary 字段（布尔，由构建脚本按聚合归属自动计算并显式写入）
  // object_type_groups：保留 35 个旧域节点（前端展示兼容），但目录注入不再使用它
}
```

### 5.2 完整映射表（30 个 `*_domain` + 5 个核心域 → 6 上下文 → 聚合）

| 上下文 | 聚合（≈旧域，层级变了） | 类数合计 |
|---|---|---|
| `fulfillment` 订单履约 | order(23) · waybill(20) · stop(18) · cargo(18) · track(16) · route(16) · warehouse(16) + 核心域 order_transport(6) 并入对应聚合 | 133 |
| `pricing_billing` 计价与结算 | quote(16) · pricing_rule(16) · fee(17) · settlement(14) · invoice(14) + 核心 pricing(3)/settlement(2) | 82 |
| `capacity` 运力 | vehicle(18) · driver(17) · carrier(17) · subcontract(15) · maintenance(16) · fuel(15) + 核心 capacity(4) | 102 |
| `evidence_exception` 证据与异常 | evidence(15) · exception(16) · insurance(15) · compliance(15) + 核心 evidence(3) | 64 |
| `master_data` 主数据 | organization(15) · contract(15) · geo(14) · customs(14) · document(15) · device(16) | 89 |
| `operations` 运营支撑 | service(16) · metric(14) | 30 |

合计 500 ✓。**每个核心域的类并入最贴近的聚合**（如 `Waybill/FeeItem/Stop` → waybill/fee/stop 聚合，`Shipper/Order/Cargo` → order 聚合）。此映射表是**决策点 D1**，批准后写入构建脚本，逐类归属表落档供人工抽查。

### 5.3 构建与门禁

- `tools/build_ontology_v4.py`（新）：在 v3 基础上生成 contexts/aggregates/glossary/boundary；**逐类归属表**输出 `plan/v4_type_membership.md` 供审核。
- `scripts/semantic_gate.py` 新增校验（v4 纪律）：
  1. 每个类恰属一个 `primary_aggregate`；每个聚合恰属一个上下文；
  2. 每条跨聚合关系 `boundary=true`（构建时自动计算，声明与计算一致）；
  3. glossary 条目的 `type_ref` 必须解析到存在的类型/属性；
  4. 每个聚合至少含 1 条聚合内关系（防「空壳聚合」）；
  5. **禁止类型名前缀作为聚合归属依据**（构建脚本映射表必须显式，不允许 `startswith` 推断——脚本里唯一例外是人工审核通过的映射表本身）。

---

## 6. 检索层设计（`freight/semantic.py` 重写 `object_type_search`）

### 6.1 五路召回

| 通道 | 实现 | 说明 |
|---|---|---|
| C1 精确 | id/label/aliases/glossary 的归一化匹配（小写、去空白、全半角） | glossary 命中直达 `type_ref`；权重最高 |
| C2 词法 | `jieba` 分词 + BM25（k1=1.5, b=0.75）；文档=每类型拼接字段；字段权重 label 3.0 / aliases 2.5 / glossary 2.0 / property names 1.5 / description 1.0 | 需要 IDF + 长度归一；中文字符 2-gram 仅作分词失败的兜底 |
| C3 向量 | 类型卡（label+description+aliases+规则摘要）→ MiMo embedding（走现有 `OPENAI_CHAT_API_BASE` 的 `/embeddings`），**离线**构建 `runs/type_vectors.json`；在线暴力 cosine（500 类无需 ANN） | LLM/embedding **不参与在线打分**，只离线建索引；索引带内容指纹，本体变更自动失效重建 |
| C4 图 | C1-C3 命中类型沿关系 1 跳扩展，分数 ×0.5；边界链接同样扩展（跨上下文可达） | 保证「命中司机资质 → 车辆/资质校验类型」被带出 |
| C5 历史 | `runs/retrieval_history.jsonl`：每轮推理后回写 `{question, effective_types, ts}`；在线用 BM25 相似度 > 0.3 的历史问句回传其类型 | 论文实测最强通道之一；冷启动为空，自然生长 |

### 6.2 融合与阈值

- **RRF**（k=60）合并五通道排名；输出每个类型的 `rrf_score`（归一化 0~1）+ 通道证据 `matched_channels`。
- **阈值** `retrieval_threshold`（默认 0.02，可配）：低于阈值丢弃，宁缺毋滥。
- **零命中语义**：全部通道无命中 → 返回空 `types` + `zero_hit=true`，`check_answerable` 沿用 `NO_ONTOLOGY_MATCH` 纪律（对齐 answerability 现有机制）。
- **降级链**（诚实标注进轨迹 `retrieval.channels_used`）：embedding 索引缺失/过期 → C1+C2+C4+C5（纯确定性）；分词词典缺失 → 字符 2-gram 兜底。**任何降级都在轨迹里显式可见**。

### 6.3 兼容与验证

- 签名保持 `object_type_search(question, top_k=8, group_ids=None)`，新增 `context_ids`/`aggregate_ids` 可选参数；旧 `group_ids` 语义映射到聚合标签（兼容旧调用方）。
- 旧 bigram 实现整体删除（不留开关，不留双口径——遵循「错误的口径直接删除」纪律）；保留于 git 历史。

---

## 7. 上下文注入层设计（`freight/agent.py:_step_catalog` 重写）

1. **seed = 当前对象的聚合闭包**：`primary_aggregate(Waybill) = waybill 聚合` + 该聚合的边界链接端点摘要。由声明推出，不再依赖「核心类恰好都在 order_transport」的运气。
2. **问句切片**：检索命中类型 → 所属聚合（按 RRF 分数排序、去重）→ 逐聚合**实测字符数**累加（沿用「放不下跳过、不连坐」算法，裁剪单位从域改为聚合，粒度 14~23 类不变但语义正确）。
3. **边界链接永不丢**：目录输出包含 `boundary_links` 节——每个跨聚合关系一行 `{id, label, from聚合.类型, to聚合.类型, cardinality}`（每条约 40 字符），无论两端聚合是否被选中。**这是 62% 跨域关系不再静默消失的保证**，也是规划器能跨域圈类型的前提。
4. **目录结构**：`contexts → aggregates → types(含 key/label/一句话) → boundary_links`；属性白名单只含 key + label + 检索命中的属性名（token 效率，Palantir Object 查询工具模式）。
5. **轨迹**：`resolved` 增加 `retrieval: {channels_used, per_channel_hits, rrf_top, threshold, zero_hit}`、`seed_aggregate`、`question_aggregates`、`skipped_aggregates`、`boundary_link_count`。
6. **answerability.py 对齐**：`_declaration_terms` / `_match` 改读 glossary + aggregates（术语表成为分诊的一等信号源，替代从声明文本切 bigram）。

---

## 8. 评估体系

### 8.1 标注集 `tests/fixtures/retrieval_eval.jsonl`

- 规模 ≥80 条：6 上下文各 ≥10 条真实问法（含口语：「这单咋这么贵」「司机证件齐不齐」「发票多久开」）+ ≥10 条**零命中反例**（问天气/股票/无关话题）+ ≥10 条跨上下文问句（「运费谁承担，多久结算」应同时命中 fulfillment+pricing_billing）。
- 每条：`{"question": ..., "expected_types": [...], "expected_contexts": [...], "expect_zero": bool}`。
- 产出方式：我从现有演示问句 + 各上下文典型问法起草，**逐条人工审核**（这是标注集质量的唯一保证，不用 LLM 自评自标）。

### 8.2 指标与门禁

| 指标 | 初版门槛 | 说明 |
|---|---|---|
| recall@8 | ≥ 0.85 | 标注类型出现在 top8 的比例 |
| MRR@8 | ≥ 0.60 | 排序质量 |
| 零命中准确率 | ≥ 0.80 | 反例问句正确返回空的比例 |
| 上下文命中率 | ≥ 0.80 | expected_contexts 命中率（注入层正确性） |

- `tests/test_retrieval_eval.py`：进全量套件；阈值写死在测试里（改动需登记理由）。
- 附加不变量门禁：任何问句下 `_step_catalog` 输出**关系数 > 0、boundary_links 非空、当前对象聚合在 seed 里**。

---

## 9. 分阶段开发计划

### M1 · 本体声明 v4（域重构 + 术语表）——先立地基

| 任务 | 内容 | 验收 |
|---|---|---|
| M1-1 | `tools/build_ontology_v4.py`：按 §5.2 映射表生成 contexts/aggregates/classes.primary_aggregate/relations.boundary；输出逐类归属表 `plan/v4_type_membership.md` | 500 类全部归属恰一个聚合；归属表人工审核签字 |
| M1-2 | glossary 初始版：抽取全部类 `aliases` + pricing label + 人工补 30+ 口语条目；`tools/build_glossary.py` 提供 LLM 离线生成候选（人工审核后入库，**在线零 LLM**） | glossary ≥150 条；type_ref 100% 可解析 |
| M1-3 | `freight/semantic.py`：加载 v4 节点（contexts/aggregates/glossary/boundary），`group_ids_for_types` 保留兼容 + 新增 `aggregate_for_type/context_of_aggregate` | 单元测试通过 |
| M1-4 | `scripts/semantic_gate.py` v4 校验（§5.3 五条）+ negative control | 门禁 31+5 项全绿 |

工作量 ≈ 2 天。风险：映射表审核发现归属争议 → 在归属表上人工改，不动代码。

### M2 · 检索层重写（五路召回 + 评估）——可独立并行

| 任务 | 内容 | 验收 |
|---|---|---|
| M2-1 | 验证 MiMo `/embeddings` 端点（curl 实测；不可用则 C3 整体进降级链，不阻塞） | 实测记录落档 |
| M2-2 | `freight/retrieval.py`（新模块）：C1/C2/C4/C5 + RRF + 阈值 + 零命中；`semantic.object_type_search` 委托给它，旧 bigram 删除 | 单元测试：通道命中、RRF 排序、阈值丢弃、零命中、降级链标注 |
| M2-3 | `tools/build_type_vectors.py`：类型卡 embedding → `runs/type_vectors.json`（带内容指纹，本体变更失效） | 500 类型卡全部向量化；重建幂等 |
| M2-4 | 标注集 80+ 条（起草 → 人工审核）→ `tests/test_retrieval_eval.py` 门禁 | 三指标达 §8.2 门槛（未达先报实测再校准，校准登记） |

工作量 ≈ 3 天（含标注审核）。依赖：jieba（纯 Python 零编译依赖，`requirements` 加一条）。

### M3 · 注入层 + 分诊对齐

| 任务 | 内容 | 验收 |
|---|---|---|
| M3-1 | `agent.py:_step_catalog` 重写：seed=聚合闭包、问句切片按聚合、边界链接注入、轨迹新字段 | 任何问句：关系>0、boundary_links 非空、seed 含当前聚合（新不变量测试） |
| M3-2 | LLM 规划提示词适配新目录结构（contexts/aggregates 层级 + boundary_links） | e2e 三链路规划步不再因目录格式解析失败 |
| M3-3 | `answerability.py`：`_declaration_terms`/`_match` 改读 glossary+aggregates | 现有 `test_answerability_and_tiers.py` 适配后全绿；分诊用术语表命中可解释 |
| M3-4 | 历史回写：推理结束后写 `runs/retrieval_history.jsonl`（问句+effective_types） | C5 通道第二轮查询生效（同问句重跑召回更准） |
| M3-5 | 全量回归修复：涉及 catalog 目录断言的既有测试（`test_no_hardcoded_routing.py` 等）按 v4 语义更新 | 495+ 测试全绿 |

工作量 ≈ 2~3 天。

### M4 · e2e + STG 部署 + 文档

| 任务 | 内容 | 验收 |
|---|---|---|
| M4-1 | 本地 e2e：知识型 / 实例型 / 「为什么这单贵 38」三链路（多次跑，LLM 偶发降级单独标注） | 三链路无「目录/规划」层降级 |
| M4-2 | rsync → `stg-121:/opt/mobility-ontology` → `systemctl restart` → `127.0.0.1:8765/freight/health` + 三链路 e2e（按项目规则，验收一律在 STG 真实执行，MiMo 出网已放行） | STG 实测记录落档 |
| M4-3 | 前端 `order-ontology.html` 目录步渲染适配（seed/问句聚合/边界链接/通道证据展示）；`hydrateWalkFacts` 等既有机制不动 | 页面真实展示通道证据与边界链接 |
| M4-4 | 文档：`plan/` 下开发纪要 + README 检索架构一节 + commit | 推送 origin/main |

工作量 ≈ 1~2 天。

**总计 ≈ 8~10 个工作日**。M1、M2 可并行启动；M3 依赖 M1+M2；M4 收口。

---

## 10. 风险与回滚

| 风险 | 缓解 | 回滚 |
|---|---|---|
| 聚合映射争议 | 逐类归属表人工审核后才生效；争议类型挂「待定」仍归原前缀聚合 | v3 JSON 在 git tag `v3-final`；`build_ontology_v4.py` 不覆盖 v3 生成物路径 |
| MiMo embedding 端点不可用 | C3 整体降级链（C1+C2+C4+C5 纯确定性），功能不缺、语义召回弱一档，轨迹显式标注 | 同上 |
| 新目录格式让 LLM 规划不适应 | M3-2 提示词微调 + 三链路 e2e 盯规划步；实在不行目录可退回平铺类型列表（保聚合与边界链接节点） | git revert M3 提交 |
| 检索指标未达门槛 | 先跑实测基线报数，再决定校准阈值或补标注/术语表（校准需登记，不许悄悄放水） | 阈值在测试里写死，改动即暴露 |
| 既有测试大面积依赖旧目录结构 | M3-5 集中修，预计涉及 5~8 个测试文件，逐文件更新断言到 v4 语义 | 分阶段提交，每阶段全量绿再进下阶段 |

---

## 11. 需要批准的决策点

| # | 决策 | 我的建议 |
|---|---|---|
| D1 | 6 上下文 × 聚合映射表（§5.2，核心域并入对应聚合） | 批准后归属表落档人工抽查 |
| D2 | 旧 35 域降级为类型多标签（UI 兼容），注入层不再使用 | Palantir 模式：标签只导航 |
| D3 | 引入 `jieba`（纯 Python 中文分词，无编译依赖） | 必要；否则 C2 无法脱离 2-gram |
| D4 | embedding 走 MiMo `/embeddings`（与 LLM 同网关，STG 出网已放行该域） | 先实测端点再定 |
| D5 | 查询历史落 `runs/retrieval_history.jsonl`（gitignore，本地生长） | 不入库不跨环境 |
| D6 | 旧 bigram 检索整体删除（不留开关不留双口径） | 遵循「错误口径直接删除」纪律 |
| D7 | 标注集由我起草、你逐条审核（或指定审核人） | 标注质量是评估体系的地基 |

---

## 12. 批准后的执行顺序

M1（声明 v4）∥ M2（检索 + 评估）→ M3（注入 + 分诊 + 回归）→ M4（e2e + STG + 文档）。
每个里程碑：全量测试绿 → 提交推送 → 本地服务硬重启验证 → 才进下一里程碑。任何一步未达验收标准即停下报告，不带病前进。

---

*附：本计划依据的调研来源——Palantir 官方文档（Chatbot Studio 检索上下文 / 工具 / Object 类型创建 / 2023-11 语义搜索 GA 公告，中文页为 AIP 机翻以英文为准）；TrustGraph《Ontologies and Context Graphs》；LinkAlign（EMNLP 2025）、Schema-First Retrieval（arXiv 2606.28387）等 Text-to-SQL schema linking 文献（数字为检索摘要，方向性结论多源一致）。*
