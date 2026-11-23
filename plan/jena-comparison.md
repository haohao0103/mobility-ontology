# 我们 vs Apache Jena：不是同一层的东西

> 日期：2026-11-23｜依据：本仓库代码实况 + Apache Jena 官方文档（最新稳定版 6.2.0，2026-03）
> 一句话：**Jena 是 RDF 三元组存储 + 标准查询 + 推理引擎（语义网基础设施）；
> 我们是 Palantir 范式的业务操作层（对象 + 动作 + 状态机 + 权限 + 治理）。
> 我们不是要替代 Jena——事实上我们已经在用 Jena 生态的推理器（HermiT）做外部背书。**

## 1. 我们现在实际站在哪

```
semantic_layer.json          ← 唯一真源（Palantir 范式：类/属性/动作/状态机/标记）
   │
   ├─ export_shacl.py  ──→ 标准 SHACL ──→ pyshacl        （CI 闭世界校验 + 官方套件）
   ├─ export_owl.py    ──→ 标准 OWL   ──→ HermiT CLI     （一致性检查 + 分类推理）
   └─ sparql_interop  ──→ Turtle     ──→ rdflib SPARQL  （对标 LUBM 14 条查询语义）
```

**三个要点**：

1. **我们没有用 Jena**：仓库里没有任何 Jena/Fuseki 代码，只有 `plan/README.md` 提到过。
   SPARQL 用的是 rdflib，OWL 推理是 **subprocess 直调 HermiT CLI**（因为 owlready2 0.40.2 + HermiT
   有组合 bug，见 `scripts/benchmark/owl2_reason.py` 的说明），SHACL 用的是 pyshacl。
2. **但 HermiT 本身就是 Jena 生态最常用的 OWL 2 reasoner** —— 所以我们**已经站在 Jena 生态上**，
   只是绕过了 Jena 本体（我们真源是 JSON，RDF 只是**派生产物**）。
3. 因此准确的定位是：**我们做的是「业务语义真源 + 导出到标准语义网栈」，
   Jena 做的是「语义网栈本身」。** 我们是 Jena 的**使用方与互补方**，不是竞品。

## 2. 逐项对比

| 维度 | Apache Jena 6.x | mobility-ontology | 谁强 |
|---|---|---|---|
| 数据模型 | 通用 RDF 三元组（**无类型强制**，subject/predicate/object） | 带类型的业务对象（属性白名单 / key / statuses / domain-range） | 各有 |
| 持久化 | **TDB2：工业级三元组存储，ACID + MVCC 写时复制** | SQLite 单机 | **Jena** |
| 查询 | **SPARQL 1.1 全标准**（联邦查询、属性路径、聚合、UPDATE） | Object Set DSL（业务语义，约 10 个算子 + 分页契约） | **Jena**（覆盖面） |
| 推理 | 内置 RDFS/OWL 规则集 + 可接 HermiT/JFact/Pellet | 规则 AST 运行时求值（16 条）+ 接 HermiT 做一致性/分类 | **Jena**（表达力与成熟度） |
| 约束校验 | SHACL（闭世界，**事后工具**） | SHACL（**已过 W3C 官方套件**）+ **写入侧闸门** | **我们**（拦得更早） |
| 动作与写回 | **无**（推理结果是虚拟的蕴含三元组，只读） | 15 动作 + 幂等键 + 审计 + 事件 | **我们** |
| 状态机 | **无** | 8 状态机 / 15 迁移边，前置不满足当场拒绝 | **我们** |
| 权限 | Security 模块实验性、实践中几乎不用 | marking 四级传播（源→类→对象→列→输出）+ 行级/列级策略 | **我们** |
| 变更治理 | **无** | 不可变快照 + checksum + 变更集 + 破坏性变更签字 + 分支恢复 | **我们** |
| Agent 集成 | **无**（Jena 生态里没有 Agent 层） | MCP 工具面 + 14 步推理链，**工具白名单由本体裁剪** | **我们** |
| 查询写法 | 要写三元组模式 | 写本体类型与属性名（`from: FeeItem, where: status=DISPUTED`） | **我们**（业务方能读） |
| 标准化程度 | **RDF / OWL / SPARQL / SHACL 全套 W3C 标准** | 仅导出侧对齐 | **Jena** |
| 生态与社区 | **Apache 顶级项目，20+ 年，Java 生态与人才池** | 无 | **Jena** |

## 3. 三件"别做"

### ① 别用我们的查询层去替代 Jena
Object Set DSL + 规则 AST 想覆盖 SPARQL 1.1 与推理表达力，等于**重造 TDB2 + ARQ + 推理器，
而且做得更差**。我们的 DSL 的价值恰恰在于它**不是** SPARQL：业务方写的是类型和属性名，不是三元组模式。

### ② 别把 RDF 侧当第二真源（**我们已经有这个风险**）
今天的实测发现：属性类型（decimal / datetime / 枚举取值）在**写入侧**由属性注册表执行，
而导出的 SHACL 里**没有对应约束** → 同一个约束在两层各写一遍。
这与"漏改实验"暴露的是同一类**两处真相**风险，只是发生在 RDF 侧而不是服务代码侧。

**必须明确的纪律**：RDF/OWL/SHACL 一律是**派生产物**；真源永远是 `semantic_layer.json`。
建议加一条门禁：**"导出产物与真源不得有只在一侧存在的约束"**（今天的缺口就是它）。

### ③ 别拿 Jena 的图当业务真源
三元组表达不了"这个动作在什么状态下、由哪个角色做、能不能改派"。
把状态机与权限塞进 RDF，会得到一个**没人愿意维护**的模型。

## 4. 一件该做（成本低、价值高）

**把 SPARQL 互操作验证从 rdflib 换成 Jena/Fuseki 再跑一遍。**

理由与我们接 W3C SHACL 官方套件完全一致：**用外部权威实现验证我们，而不是自己证明自己**。
收益：对外发布基准时，多一条硬证据——"我们的本体不只被 rdflib 消费，**也被 Jena 生态消费**"。

现状与阻塞：
- 脚本：`scripts/benchmark/sparql_interop.py`（当前用 rdflib，对标 LUBM 14 条查询的语义模式）
- 阻塞：**本机 docker 不可用**，起不了 Fuseki；本地装 Jena 6.x 需要 **Java 21+**
- 代价：改一个脚本的连接方式 + 一个启动依赖；LUBM 官方数据集仍托管在 GitHub（本机不可达，
  只能做查询能力对标，不能跑官方数据集 —— 这条限制已如实登记在脚本 docstring 里）

## 5. 什么时候该引入 Jena（决策条件）

出现以下**任一**需求时，把 Jena 引入为**互操作层**（不是底座）：

| 需求 | 对应的 Jena 能力 |
|---|---|
| 要对外提供标准 SPARQL 端点 | Fuseki |
| 本体规模到需要 ACID/MVCC 的图存储 | TDB2 |
| 需要 SWRL / 复杂 DL 推理 | Inference API + HermiT/Pellet |
| 要接 RDF 生态工具（Protégé、OWL API、第三方 SPARQL 客户端） | Ontology API / ARQ |

**现在这四条都不成立**：真源是 JSON，RDF 是投影，单机规模够用，Agent 才是主战场。
所以当前正确姿势是：**继续把 Jena 当外部权威验证器用（HermiT 已经是了），
不把它引入为运行时依赖。**

## 6. 一句话对外口径

> 「Jena 解决的是**标准语义网基础设施**——三元组存储、SPARQL 全标准、OWL 推理，我们已经用它的
> 推理器 HermiT 做外部验证了。
> 我们解决的是**另一层**：把业务规则变成机器可执行、可审计的东西——动作、状态机、权限、
> 变更治理，以及让 Agent 只能在本体划定的边界里干活。
> 两者是互补的：**RDF 是我们的投影，不是我们的真源。**」
