# 第四组：selfgraph（成熟属性图 SDK）

## 为什么加这一组

基准原本的三方是：**本体（我们） / 属性图（我们自己写的参考实现） / RAG**。

其中"属性图"那一侧是我们自己写的 → 评审清单里明确写了这是**稻草人风险**。
selfgraph 是社区里成熟度很高的属性图/知识图谱 SDK（**603 个测试**、5 种后端、
双时态模型、轻量本体 + MCP、Apache-2.0），把它接进来当第四组，
主张就从「**我们 vs 我们自己写的属性图**」升级为「**我们 vs 工业级属性图 SDK**」。

## 怎么跑

```bash
# 依赖：selfgraph + 一个图后端。内嵌 Kuzu 最省事
pip install "selfgraph[kuzu]"      # 注意 Kuzu 需 Python ≤ 3.13（3.14 无预编译 wheel）

# 跑第四组
cd benchmarks/ontology-execution
python runner.py --impl implementations/selfgraph_ref/impl.py
```

缺依赖时它会**明确报"不可用"（SKIPPED）**，不会用模板伪造答案——与 RAG 组同构。

## 能力边界（源码核查结论，2026-11-23）

| 我们要的能力 | selfgraph 有吗 | 依据 |
|---|---|---|
| 事实图（实体/关系/属性） | ✅ | `EntityInput.properties` / `RelationInput.properties` |
| 属性类型与必填 | ✅ | `PropertySpec(type, required)` |
| 关系端点 | ✅ | `RelationType.from_ / .to` |
| 术语 / 同义词 / 认证事实 | ✅ | `EntityType.synonyms` / `Definition.certified` |
| 版本化事实、as-of / known-at 读历史 | ✅ | `retrieve(as_of=..., known_at=...)`，**双时态** |
| "图里有没有答案"的判断 | ✅ | `check_answerable()` → `report.missing` |
| **业务公式求值** | ❌ | `Definition.expression` 只是声明字符串，**源码里没有表达式求值器** |
| **动作 / 状态机 / 写回** | ❌ | `state_machine` / `action_type` / `writeback` 全库零命中 |
| **权限 / 标记传播** | ❌ | `marking` / `policy` 全库零命中 |
| **本体变更治理（评审/签字/分支）** | ❌ | 只有 schema 的 `version` 字段 |

**结论**：selfgraph 能表达**"图里有什么、怎么查、历史上是什么样"**，
不能表达**"按规则该是多少、谁能改、改了会挡住哪一步"**——
后者正是本基准的 Q2 / Q3 / C1 / C2 / C3 五道题。

## 这个实现的"尽力而为"体现在哪（避免稻草人）

- Q2 不是直接返回"不知道"：它**先把应计公式声明成 definition（术语 + 同义词 + 认证）**，
  再走 selfgraph 自己的检索与 `check_answerable`，最后才如实报告"没有求值器 → 无来源"；
- Q3 同样先问 `check_answerable`，把 `report.missing` 写进解释；
- 变更卡 C1/C2/C3 **真的会改 schema 并重建图**（`restart_required=True`），
  而不是声称改过。

## 实测状态

**代码已就绪，本机尚未跑通**（2026-11-23）：本机 Python 3.14 无 Kuzu 预编译 wheel，
源码编译超时。预期结果（**基于源码核查的推断，非实测**）：

| 用例 | 预期 | 理由 |
|---|---|---|
| Q1 差异归因 | **PASS** | 报价/实收/差额都在图里 |
| Q2 按规则应收多少 | **FAIL** | 无表达式求值器 |
| Q3 减免被谁挡 | **FAIL** | 无状态机/动作/上限声明 |
| C1/C2 变更后应计 | **FAIL** | 无公式 → 变更不改变结论 |
| C3 漂移探针 | **FAIL** | 上限口径无第二处副本，但也没有"上限"这个语义 → 仍 FAIL |

> ⚠️ **上表是推断，不是实测**。在有 Kuzu/Neo4j 的环境跑一次即可证实或推翻；
> 实测前，对外引用请按"推断"表述。

## 顺带：值得我们借鉴的三条设计

1. **双时态（valid time + known time）** —— 我们是单轴（关系时态 + 本体快照）。
   双时态能分别回答"当时如此"与"当时我们以为如此"，对口径互校直接有用。
2. **tiers 分级（light / balanced / max）** —— 一个旋钮控制"要不要花钱"，
   高档先跑低档、只把没定论的往上送，且每个结果都报"由哪档产出"。
   与我们的"能力由本体声明决定"互补：我们管"能做什么"，它管"这次花多少钱"。
3. **answerability / certified facts** —— 与我们的「缺口四类」同族：
   **让"我不知道"成为一等公民**，而不是悄悄算成 0。
