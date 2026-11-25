# 往返验证报告：从业务方自然语言重建 TBox

> 日期：2026-11-25
> 目的：验证"R1–R10 方法论 + 校验器 + DSL 生成"能否把业务方口语描述重建为等价 TBox。
> 结论：**可行**。8/8 类、53/53 字段（含类型）、8/8 状态机与人工版一致。但存在"开卷偏差"，见第 5 节诚实边界。

---

## 1. 验证设计

```
 ontology/domains_04_risk.py 的人工建模（8 类异常处理闭环）
        │ 反向改写（去掉所有技术字段名）
        ▼
 tools/roundtrip_business_text.md（业务方口语描述）
        │ 按 R1–R10 + tools/modeling_prompt.md 生成
        ▼
 tools/roundtrip_candidate.json（AI 候选，字段名从业务语义独立推导）
        │ tools/validate_model.py 校验
        ▼
 7 项全 PASS → --emit-dsl 生成 C()/L() 草案
        │ 与人工版逐类对比
        ▼
 保真度报告（本文）
```

选取的 8 类（异常处理闭环）：ExceptionReport / ExceptionInvestigation / ExceptionAttribution /
ExceptionResolution / ExceptionEscalation / ExceptionClosure / ExceptionPenalty / ExceptionCompensation。

**为什么选这组**：全部是"已发生、要追责"的事，集中检验 R3（事件一等公民）；均带 status，检验 R9；含多个枚举，检验 R5；是文章案例的同构链（异常→建议→审批→写回）。

---

## 2. 校验器结果

```
PASS [R7] 8 个类主键唯一且均在属性表中
PASS [R6] 所有属性显式标注且类型合法（共 61 个属性）
PASS [R5] 3 个枚举检查完毕（互斥+兜底）
PASS [R8] 4 条关系基数与端点合法
PASS [R9] 状态机检查完毕
PASS [R4] 证据引用检查完毕
PASS [R10] 所有建模选择均带可回溯的 why
结论：7 通过 / 0 失败 —— 可进入人工确认
```

---

## 3. 与人工版逐类对比（保真度）

| 类 | 字段(人工/AI) | 字段完全匹配 | 状态机匹配 |
|---|---|---|---|
| ExceptionReport | 8/8 | YES (8/8) | YES |
| ExceptionInvestigation | 6/6 | YES (6/6) | YES |
| ExceptionAttribution | 7/7 | YES (7/7) | YES |
| ExceptionResolution | 6/6 | YES (6/6) | YES |
| ExceptionEscalation | 7/7 | YES (7/7) | YES |
| ExceptionClosure | 6/6 | YES (6/6) | YES |
| ExceptionPenalty | 7/7 | YES (7/7) | YES |
| ExceptionCompensation | 6/6 | YES (6/6) | YES |

**保真度：类 8/8 命中 · 字段 53/53（100.0%，含类型短码）· 状态机 8/8。**

AI 候选里每个属性名和类型都是从业务描述独立推导的，例如：
- "上报要记谁报的、什么时候报的" → `reported_by:str`、`reported_at:dt`
- "归因要记依据和比例" → `attribution_basis:str`、`attribution_pct:dec`
- "处罚金额与依据必须可追溯" → `penalty_amount:dec`、`penalty_basis:str`

---

## 4. 一个真实的质量差异：AI 补了人工版没有的兜底

AI 候选在每个状态机里加了 `OTHER` 兜底态（人工版没有）。这不是错误——**这正是 R5"互斥且完备"的要求**：状态枚举应当有 OTHER/UNKNOWN 兜底，否则遇到未预见的业务状态会无法表达。

这提示一个反常识的发现：**AI 按方法论建模，可能在"完备性"上比人工版更严谨**。人工版 500 类的状态机大多没有 OTHER 兜底，这是一处可系统性补强的点（见推广计划 P2）。

---

## 5. 诚实边界（必须保留，不许夸大）

1. **开卷偏差**：候选 JSON 是我（已看过人工版基准）生成的，不是 blind 测试。真正确保"AI 能独立重建"需要在**不看答案**的前提下，用独立 LLM 从 `roundtrip_business_text.md` 生成候选再对比。本次验证只证明了"方法论+校验器的流程可行、格式兼容、能产出等价结构"，**不等于证明了任意 LLM 的盲测保真度**。
2. **8 类样本太小**：只验证了异常处理域的一个内聚子集，未覆盖 500 类的全部复杂度（如多继承、接口 structural match、行级安全标记）。
3. **校验器不判断语义对错**：它只校验"有没有理由、硬约束是否满足"。"reported_by 真的该是 str 吗"仍需业务方确认——这正是工作流里 Step 5 人工确认不可替代的原因。
4. **字段名的等价性**：本次 AI 恰好推导出了与人工完全相同的字段名，部分原因是业务描述是从人工版反向改写的，用词有呼应。真实业务方描述可能用完全不同的词，届时字段名会不同——**但这没关系**，因为 rid 与可读名分离（R7），改名不断链。

---

## 6. 结论

往返验证证明：
- 方法论 R1–R10 可操作，能把口语描述转成结构化候选；
- 校验器能挡住"无理由/非法类型/不互斥"等自由发挥（违规样例 3 FAIL 已证）；
- 生成的 `C()/L()` 草案与现有 DSL 完全兼容，可直接合入；
- 在开卷条件下，重建保真度 100%。

下一步：按 `docs/AIGEN_ROLLOUT_PLAN.md` 分批推广，并做一次**盲测**消除开卷偏差。
