# 业界本体评测资源盘点：哪些能接、哪些只能借鉴、哪些根本不存在

> 仓库：`/Users/mac/WorkBuddy/2026-11-18-20-40-54/mobility_ontology_audit/src/mobility-ontology-main`
> 日期：2026-11-21 ｜ 目的：为"本体系统的正确性"找**外部证据**，而不是只靠自证

## 0. 结论（三条）

1. **面向"可执行本体"的公开 benchmark 不存在。** Palantir 是闭源商业产品；学术界在做的是"本体学习"（从文本建本体），不是"本体执行"。所以"用现成 benchmark 证明本体 > 属性图"**这条路走不通**——前提不存在。
2. **有三类资源真能用**：W3C/DASH 的 SHACL 一致性套件（证明我们的校验器跑在标准上）、LLMs4OL 的本体学习基准（如果我们用 LLM 建本体）、OAEI/OTTER 的本体对齐基准（接真实数据时用）。
3. **最该做的其实是我们自己发布一个 benchmark**：我们已经有骨架（`experiments/ab_value/` + `freight/parity_lab.py`），把它做成 SHACL 套件那样的形式（manifest + 期望结果 + CI 基线 + 公开协议），**这个题目现在没有别人做过**——它本身就是"值得继续投"的最强论据。

---

## 1. A 类｜能直接接进来的外部权威套件

| 资源 | 内容 | 对我们的**真实**价值 | 成本 |
|---|---|---|---|
| **W3C SHACL Test Suite** | 120 个 approved 测试 + 1 个 proposed，分 core / SPARQL 等节；定义了两级合规（partial = 只对 `sh:conforms`；full = 完整报告比对） | ⚠️ **我们的引擎是 pyshacl（标准实现），直接跑证明的是 pyshacl 的合规性，不是我们的价值**——别拿它当卖点。真用法：① 用套件里的复杂约束当**我们 shapes 生成器的表达力上限测试**；② 用套件里的**反例**当外部 negative control 语料（我们现在的 negative control 是自己造的） | 中（下载 + harness） |
| **DASH SHACL-AF** | SHACL 高级特性（rules / functions / advanced targets）的事实标准——W3C 只出了 WG Note 没出套件，**TopQuadrant 的 Java 实现与 pySHACL 都用它** | 既是**表达力差距的权威清单**，也是我们补齐 `sh:node` / `sh:or` 的依据 | 中 |
| **OWL 2 Test Cases**（owl.semanticweb.org） | OWL 2 分类/一致性推理测试 + Conformance 规范 | 我们不是 OWL reasoner（我们用规则 AST），**适用性有限**；只能借"一致性检查"部分 | 高（不匹配） |
| **LLMs4OL 挑战赛**（ISWC 2024 / 2025 / 2026） | 本体学习基准：Text2Onto、Term Typing、Taxonomy Discovery、Non-Taxonomic Relation Extraction；2026 届改为 Flagship / Reuse / Taxonomy 三赛道；公开数据集与榜单 | 测"LLM 建本体"能力。**最值得抄的是 Burr 的 mapping 口径**（PACMMOD 3(6) 305）：真阳性**按 mapping 算**，把"名字像"和"指对了"分开——正好治我们"看起来一致"的虚高 | 中高 |
| **OAEI / OTTER**（本体对齐评测） | anatomy / conference / family 等基准 | 测"我们的 500 类能不能对齐到行业标准（schema.org / GS1 / FIBO）"——**这是对接真实数据的直接抓手**，不是学术游戏 | 中高 |
| **Competency Questions + OntoMetric** | 本体质量评估方法（Noy & McGuinness 的 CQ 方法）与指标框架 | 评"我们本体建得好不好"（内部质量，不是对外对标） | 低 |
| **CRAG / FRAMES** | RAG 质量基准，**归因式**区分"检索不足 / 检索错误 / 生成错误" | 借鉴它的归因口径来评我们的 Agent 答案质量（我们目前只有"对/错"） | 低（借鉴） |

## 2. B 类｜不存在的（重要发现）

**没有任何公开 benchmark 面向"可执行本体"（本体 + 动作 + 状态机 + 权限 + 治理）。**
Palantir 本体闭源；AIP 的 `Evals` 是 Palantir 自家产品的功能，不是公开套件。
学术界的评测集中在"从文本建本体"（LLMs4OL）、"本体质量"（OntoMetric）、"本体对齐"（OAEI）——
**都不是"本体跑起来对不对"**。

> 这意味着：任何人（包括我们）想证明"可执行本体 > 属性图"，**只能自己做基准**。
> 这既是坏消息（没有现成背书），也是**好消息（这是一个空位）**。

## 3. 我们能不能接：实测的表达力差距

核了 `tools/export_shacl.py`（360 行，把语义层导出为标准 SHACL NodeShape，CI 里用 pyshacl 校验）：

| SHACL 特性 | 支持 | SHACL 特性 | 支持 |
|---|---|---|---|
| `sh:closed` + `sh:ignoredProperties`（字段封闭） | ✓ | `sh:sparql`（自定义约束） | ✓ |
| `sh:minCount` / `sh:maxCount`（基数） | ✓ | `sh:path`（属性路径） | ✓ |
| `sh:in`（枚举） | ✓ | `sh:class`（类约束） | ✓ |
| `sh:property` / `sh:targetClass` | ✓ | `sh:targetSubjectsOf` / `sh:targetObjectsOf`（domain/range） | ✓ |
| **`sh:node`（嵌套形状）** | **✗** | **`sh:or` / `sh:and` / `sh:not`（逻辑组合）** | **✗** |
| **`sh:datatype` / `sh:minInclusive`（值域）** | **✗** | SHACL-AF：rules / functions / advanced targets | ✗ |

**已覆盖 SHACL Core 的大部分 + SPARQL 约束**；缺三块：嵌套形状、逻辑组合、值域约束。

⚠️ **其中"值域"这一项暴露了一个真问题**：属性类型（`decimal` / `datetime` / 枚举取值）在我们的
**写入侧**由属性注册表执行，但在 **SHACL 产物里没有对应约束** → 同一个约束在两层各写一遍，
**正是我们今天"漏改实验"暴露的那类"两处真相"风险**（诊断说 16.8、预检说 12.0）。
→ 修法二选一：**① 在导出器里补 `sh:datatype` / `sh:minInclusive`；② 明确写下"值域只在写入侧强制"，
并让门禁检查"SHACL 产物与写入侧规则不重叠"。**

## 4. 行动建议（按 ROI 排序）

| # | 事项 | 成本 | 价值 | 何时 |
|---|---|---|---|---|
| 1 | **补 `sh:datatype`/`sh:minInclusive` 或明确边界 + 门禁防重叠** | 低 | 消除"两处真相"风险；顺带拿到 DASH 表达力清单 | 演示后 |
| 2 | **用 W3C 套件的反例当外部 negative control 语料** | 中 | 我们的负例检测从此有外部语料，不再只靠自己造 | 演示后一周 |
| 3 | **自建公开 benchmark**（manifest + 期望结果 + CI 基线 + 协议 + 发布） | 中（骨架已有） | **最强论据**：不是我们说本体有价值，是我们发布一个让别人复现的基准；题目目前无人做 | 立项 |
| 4 | DASH SHACL-AF 约束清单 → 逐条核 → 表达力差距报告 | 低 | 对外可发的技术材料；指导补齐顺序 | 与 #1 合并做 |
| 5 | OAEI 对齐（对 schema.org / GS1） | 中高 | **接真实数据的抓手**：证明我们的类能对到行业标准 | 接入真实数据时 |
| 6 | LLMs4OL 参赛（若我们用 LLM 建本体） | 中高 | 对外可比的分数；但要先用 Burr 的 mapping 口径 | 用 LLM 建本体时 |

## 5. 诚实的边界（别越说）

- **没有公开 benchmark 能直接证明"本体 > 属性图"**——它不存在，别暗示存在。
- **跑 W3C 套件证明的是 pyshacl 的合规性，不是我们系统的价值。** 真价值在"用它的反例验我们的负例检测"和"用它的约束清单量我们的表达力"。
- 我们能拿外部证据支撑的三件事：① 我们的校验器**跑在标准实现**上（SHACL 是 W3C 推荐）；② 我们的本体声明**用标准 SHACL 表达**；③ 我们的负例检测**经得起外部反例**（做完 #2 之后才能这么说）。
- 剩下的价值主张（一致性、漏改不可能、改一处全链路变）目前**只能靠我们自己的可复现实验**（`parity_lab` + A/B 协议）支撑——**这正是 #3 存在的理由**。

## 6. 参考

- W3C SHACL Test Suite: https://w3c.github.io/data-shapes/data-shapes-test-suite/
- DASH SHACL-AF: https://github.com/tggo/goRDFlib/tree/main/testdata/dash-af
- OWL 2 Test Cases: http://owl.semanticweb.org/page/OWL_2_Test_Cases
- LLMs4OL 2026: https://arxiv.org/html/2608.27101v2 ；LLMs4OL 2025 综述（TIB OA）: https://oa.tib.eu/renate/handle/123456789/40886
- 本体评测综述（Semantic Web 17(4), 2026）与 Burr 的 mapping 口径：见 https://icorer.com/icorer_blog/posts/ai/ontology-evolution
- OntoTune（WWW 2025，用 SNOMED CT）: https://openreview.net/forum?id=d7spHwemKX
