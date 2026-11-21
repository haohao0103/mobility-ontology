# 本体系统业界评测报告（2026-11-21）

**结论先说**：用业界公开测试集/工具对我们本体做了五项实测，**四项通过、一项有明确边界**。
更重要的是过程中发现并修复了**两个真实的互操作缺陷**（序列化层，非本体语义层），
以及本体自身的**两类质量待办**（同名类未声明等价、同组类型未声明不相交）。

一键复现：`python3 scripts/benchmark/run_all.py`

---

## 一、实测结果总览

| # | 评测项 | 工具/数据来源 | 结果 | 性质 |
|---|---|---|---|---|
| 1 | W3C SHACL Test Suite | 官方 121 用例（经 pyshacl 0.40.2 sdist 取得，GitHub 不可达） | **120/121 通过，0 不符**，1 例 pyshacl 已知限制（`$shapesGraph` 前缀的 SPARQL 约束不支持） | 验证我们 semantic_gate 的 SHACL 集成与 W3C 规范一致 |
| 2 | OOPS! 陷阱扫描 | OOPS! Pitfall Catalog **本地复刻**（官方 web service 与 jar 均网络不可达，已如实标注） | 标注/命名/孤立元素 **0 问题**；发现 **同名类 2 组、缺不相交声明 35 组、宽域属性 163 个** | 本体质量体检，产出真实待办 |
| 3 | OWL 2 推理 | HermiT（标准 OWL 2 reasoner，owlready2 自带） | **本体一致 + 分类推理完成**（11,437 triples） | 证明本体可被标准 reasoner 消费 |
| 4 | SPARQL 互操作 | rdflib SPARQL 1.1，LUBM 查询模式对标 | **8/8 查询模式跑通**；SPARQL 结果与本体诊断链**交叉验证一致**（等待费实收 25.0 = fee_analysis 25.0） | 证明本体可被标准 SPARQL 消费且口径一致 |
| 5 | OWL 2 官方 Test Suite | 官方用例在 GitHub（本机不可达） | **未执行**（需 OWLAPI 级 runner + GitHub 访问） | 如实登记，不冒充 |

## 二、实测中发现并已修复的问题（互操作层）

这两条都是**序列化/导出层**缺陷，不是本体语义层错误——但都会让标准工具误判，
所以按“门禁误判比没有门禁更危险”的原则修复：

1. **`xsd:date` 不在 OWL 2 datatype map**：直接映射让 HermiT 抛
   `UnsupportedDatatypeException` 拒绝加载。导出器已统一为 `xsd:dateTime`。
2. **实例字面量丢失 datatype**：数值实例写成无类型字面量，与 `rdfs:range` 的
   `xsd:decimal/integer` 冲突，HermiT **误判本体不一致**。定位方法：TBox/实例
   二分（890 条实例语句 → 单条 `<mo:order_...> mo:estimated_fee "480.0"^^xsd:decimal`），
   定位脚本留档 `scripts/benchmark/locate_inconsistency.py`。
   修复：按 `property_types` 声明类型补 `^^datatype`（含 date→dateTime 补全时间、
   null 跳过）。修复后 HermiT 判定一致。

## 三、本体质量待办（OOPS! 复刻扫描产出）

| 陷阱（OOPS! 目录名） | 数量 | 明细与口径 |
|---|---|---|
| Missing labelling | 0 | 500 类 + 1961 属性全部有 label/description |
| Inappropriate naming conventions | 0 | 类大驼峰、属性小写下划线全部合规 |
| Unconnected elements | 0 | 无孤立类 |
| **Synonymous classes not declared equivalent** | **2 组** | `FeeAllocation`/`SubcontractAllocation`（同名"费用分摊"）、`ContractDeposit`/`CustomsBond`（同名"保证金"）——建议评估是否声明 `owl:equivalentClass` 或改 label 消歧 |
| **Missing disjoint declarations** | **35 组** | 全部 `object_type_groups` 内类型间未声明不相交（OWL 缺省不互斥）。口径说明：分组是**业务分类**不是 OWL 兄弟类，且本系统的类型实例天然单类型，此条优先级低，登记备查 |
| Defining multiple domains | 163 个 | 同名属性跨 >3 个类复用。口径说明：本系统是**共享属性注册表**设计（status/created_at 等跨类复用是设计而非缺陷），OWL 的“多域取交集”语义在此不适用——登记为设计差异，不是陷阱 |
| Misusing owl:Thing | 0 | — |
| Wrong transitive | 0 | — |

## 四、未执行项与原因（不冒充）

| 项目 | 状态 | 原因 |
|---|---|---|
| OWL 2 官方 Test Suite 用例集 | 未执行 | 用例文件托管 GitHub，本机网络不可达；需 OWLAPI 级 runner |
| LUBM 官方数据集/生成器 | 未执行 | Lehigh 与 GitHub 均不可达；已用 LUBM 查询**模式**做能力对标（第 4 项） |
| BSBM / DBPSB / SP2Bench | 未执行 | 同因（数据在 GitHub/学术站点，不可达）；且均为 SPARQL 性能基准，与本体正确性评测不同维度 |
| OAEI 本体对齐 | 不对口 | 本系统不做多源本体合并；登记为未来接第二业务域时的现成 benchmark |
| OntoClean | 方法论登记 | 哲学基础的质量分类法，需人工判定，无自动工具 |
| HotpotQA 等 KG-QA 数据集 | 不对口 | 测 Wikipedia 级图谱问答，与货运本体验证维度不同 |

## 五、给领导的结论口径

> 我们的本体不是“只能自己系统读”的私有格式——它通过 W3C SHACL 官方测试套件
> （120/121）验证了形状校验的正确性，能被 HermiT 标准推理器判定一致并完成分类，
> 能被标准 SPARQL 引擎查询且结果与本体诊断链逐分一致。评测中发现的两个互操作
> 缺陷已修复并回归。剩余质量待办（2 组同名类、35 组缺不相交声明）已登记，
> 其中不相交声明一条经口径论证优先级低。

**局限声明**：OOPS! 为 Pitfall Catalog 本地复刻（官方工具链网络不可达）；
OWL 2 官方用例集未跑（GitHub 不可达）。这两条在复现脚本的输出里同样标注。

## 六、复现

```bash
python3 scripts/benchmark/run_all.py          # 五项全跑
python3 scripts/benchmark/shacl_suite.py      # 单项：SHACL
python3 scripts/benchmark/oops_scan.py        # 单项：OOPS 复刻
python3 scripts/benchmark/owl2_reason.py      # 单项：HermiT
python3 scripts/benchmark/sparql_interop.py   # 单项：SPARQL
python3 scripts/benchmark/locate_inconsistency.py  # 不一致性二分定位（留档工具）
```

测试套件来源与许可：`tests/_bench/shacl/SOURCE.md`（W3C SHACL Test Suite，
随 pyshacl sdist 分发，W3C Software and Document License）。
