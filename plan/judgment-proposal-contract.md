# 判断与提议输出契约

> 状态：Draft
> 版本：0.1
> 更新：2026-11-19
> 能力基准：[roadmap/palantir-benchmark.md](../roadmap/palantir-benchmark.md)
> 执行清单：[plan/palantir-gap-plan.md](palantir-gap-plan.md)（G5-01，检索闭环稳定后启动）

## 1. 目标与边界

本契约定义本体引擎对外输出的三种结构化结果：**Judgment（判断）**、**Evidence（证据）**、**Proposal（提议）**。

边界：

- 本项目**只做只读判断与提议，不做 Action apply / writeback**；
- Proposal 只描述「如果执行会发生什么」，不产生任何 edits、副作用或业务数据变更；
- 所有判断和提议必须绑定：对象、时点（`as_of`）、本体版本、证据；
- 证据不足时必须返回 `UNKNOWN`，不得默认 `FAIL`，也不得由模型自由发挥。

## 2. Judgment（判断）

一次判断 = 某个规则/约束作用于某个对象（或对象集合）后得到的可复核结论。

### 2.1 结构

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `judgment_id` | string | 是 | 全局唯一，建议 `jdg:{uuid}` |
| `subject` | object | 是 | `{ "type": "Waybill", "object_id": "waybill:..." }` |
| `as_of` | string(ISO-8601) | 是 | 判断所依据的事实时点 |
| `verdict` | enum | 是 | `PASS` / `FAIL` / `UNKNOWN` |
| `rule_id` | string \| null | 否 | 命中的规则 ID；约束判断可为空 |
| `severity` | enum | 否 | `HIGH` / `MEDIUM` / `LOW`，继承规则声明 |
| `summary` | string | 是 | 一句话结论 |
| `reason` | array | 是 | 结构化理由链，见 2.2 |
| `evidence` | array<Evidence> | 是 | 证据列表，见 3 |
| `confidence` | number | 否 | `0..1`；仅在 `UNKNOWN` 或证据不完整时用于表达把握程度 |
| `unknown_reason` | string \| null | 否 | `verdict=UNKNOWN` 时必填 |
| `ontology_version` | string | 是 | 产出该判断的本体版本 |
| `produced_at` | string(ISO-8601) | 是 | 判断生成时间 |

### 2.2 reason 条目

```json
{
  "kind": "fact | rule | constraint | derivation",
  "ref": "waybill:WB20261105001.waiting_minutes",
  "detail": "等候 53 分钟，超过免费 30 分钟"
}
```

### 2.3 verdict 判定规则

| 结果 | 条件 |
|---|---|
| `PASS` | 规则/约束所需的证据齐全，且全部条件满足 |
| `FAIL` | 证据齐全，且至少一个条件明确不满足 |
| `UNKNOWN` | 必需证据缺失、事实读取失败、时点无法对齐，或规则无法判定 |

硬性要求：

1. 缺证据 → `UNKNOWN`，不允许降级为 `FAIL`；
2. `FAIL` 必须在 `reason` 中指出被违反的具体条件与证据；
3. `PASS` 不允许省略证据列表（可以是空数组，但 `evidence_required=false` 时必须显式说明依据）；
4. 多条规则命中且结论冲突时，按规则优先级求值；无优先级声明则整体返回 `UNKNOWN` 并列出冲突规则。

## 3. Evidence（证据）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `evidence_id` | string | 是 | 证据唯一 ID |
| `evidence_type` | string | 是 | 如 `ETC_RECORD`、`POD`、`EVENT_LOG` |
| `source_system` | string | 是 | 来源系统 |
| `occurred_at` | string(ISO-8601) | 是 | 事实发生时点 |
| `version` | string \| null | 否 | 来源数据版本 |
| `checksum` | string \| null | 否 | 内容校验和 |
| `required_by_rule` | boolean | 是 | 是否为本判断的必需证据 |
| `present` | boolean | 是 | 当前是否实际取到 |

## 4. Proposal（提议）

提议 = 对某个已声明动作的**只读预演**。它回答「能做什么、为什么能/不能、做了会怎样、风险多大」。

### 4.1 结构

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `proposal_id` | string | 是 | 全局唯一，建议 `prop:{uuid}` |
| `action_id` | string | 是 | 必须引用本体中已声明的动作 |
| `target` | object | 是 | `{ "type": "FeeItem", "object_id": "fee:F003" }` |
| `actor` | object | 是 | `{ "id": "...", "role": "finance" }` |
| `params` | object | 是 | 按动作 `params` 声明校验；无参数时为 `{}` |
| `expected_effects` | array | 是 | 预期变更，见 4.2；只描述不执行 |
| `preconditions` | array | 是 | 前置校验结果，见 4.3 |
| `blocked_reasons` | array | 是 | 阻断原因，见 4.4；无阻断时为空数组 |
| `risk` | object | 是 | `{ "level": "LOW|MEDIUM|HIGH", "notes": "..." }` |
| `confidence` | number | 否 | `0..1` |
| `evidence` | array<Evidence> | 是 | 支撑提议的证据 |
| `requires_confirmation` | boolean | 是 | 恒为 `true`：提议必须人工确认 |
| `executable` | boolean | 是 | 本项目恒为 `false` |
| `as_of` | string(ISO-8601) | 是 | 依据的事实时点 |
| `ontology_version` | string | 是 | 本体版本 |
| `produced_at` | string(ISO-8601) | 是 | 生成时间 |

### 4.2 expected_effects 条目

```json
{ "op": "set_status", "target": "fee:F003", "from": "DISPUTED", "to": "WAIVED" }
```

```json
{ "op": "set_field", "target": "fee:F003", "field": "waived_amount", "value": 25.0 }
```

当前允许的 `op`：`set_status`、`set_field`、`create_link`、`delete_link`。这些只是**预期效果描述**，不是执行指令。

### 4.3 preconditions 条目

```json
{ "kind": "state | role | rule | constraint | permission", "ok": true, "detail": "FeeItem 当前状态 DISPUTED，允许减免" }
```

### 4.4 blocked_reasons 代码

| code | 含义 |
|---|---|
| `STATE_INVALID` | 当前状态不允许该动作 |
| `ROLE_FORBIDDEN` | 角色无权限 |
| `RULE_REJECTED` | 被业务规则阻断 |
| `CONSTRAINT_VIOLATION` | 违反本体约束（Domain/Range/基数等） |
| `EVIDENCE_MISSING` | 必需证据缺失 |
| `PERMISSION_DENIED` | marking / 访问控制拒绝 |
| `PARAM_INVALID` | 参数缺失或类型错误 |

### 4.5 硬性要求

1. `executable` 恒为 `false`，API 不得提供 apply 入口；
2. `action_id` 必须存在于本体声明，否则返回 `PARAM_INVALID`；
3. `expected_effects` 必须能由动作声明与目标对象状态推导，不允许模型自由生成；
4. `blocked_reasons` 非空时，`preconditions` 中必须有对应 `ok=false` 项；
5. 提议必须携带证据；证据不足时给出 `confidence` 并说明风险。

## 5. 与现有实现的映射

| 现状 | 迁移方向 |
|---|---|
| `freight` 的 `action.plan` / `function.action_preflight` | 输出改为本契约的 Proposal |
| `profile_ai` 的 `proposals` + 人工确认 + 审计 | 复用其留存/确认流程，输出对齐 Proposal |
| `freight` 的 `rules[].judge` | 执行结果改为本契约的 Judgment |
| `evidence_chain` | 输出对齐 Evidence，并标注 `required_by_rule` |

## 6. 校验与 CI

- `schema/judgment.schema.json`、`schema/evidence.schema.json`、`schema/proposal.schema.json` 由本契约生成；
- CI 对所有 Judgment / Proposal 样例做 JSON Schema 校验；
- 每条规则至少 2 正 2 反，并包含至少 1 个 `UNKNOWN` 用例（缺证据）；
- 每个上架动作至少 2 个可提议用例与 2 个被阻断用例；
- 断言重点：`verdict`、`blocked_reasons`、`evidence.required_by_rule/present`、`executable=false`。

## 7. 示例

### 7.1 Judgment

```json
{
  "judgment_id": "jdg:8f1c",
  "subject": { "type": "FeeItem", "object_id": "fee:F003" },
  "as_of": "2026-11-05T12:18:00+08:00",
  "verdict": "PASS",
  "rule_id": "R-WAIT-01",
  "severity": "HIGH",
  "summary": "等候费 25 元符合规则应计",
  "reason": [
    { "kind": "fact", "ref": "waybill:WB20261105001.waiting_minutes", "detail": "等候 53 分钟" },
    { "kind": "rule", "ref": "R-WAIT-01", "detail": "30 分钟免费，每 5 分钟 2.4 元" }
  ],
  "evidence": [
    {
      "evidence_id": "ev:pod-001",
      "evidence_type": "POD",
      "source_system": "TMS",
      "occurred_at": "2026-11-05T12:18:00+08:00",
      "version": "2026.10",
      "checksum": "9f2c...",
      "required_by_rule": true,
      "present": true
    }
  ],
  "confidence": 0.98,
  "unknown_reason": null,
  "ontology_version": "1.4.0",
  "produced_at": "2026-11-19T10:00:00+08:00"
}
```

### 7.2 Proposal

```json
{
  "proposal_id": "prop:2a7d",
  "action_id": "waive_charge",
  "target": { "type": "FeeItem", "object_id": "fee:F003" },
  "actor": { "id": "u:finance-01", "role": "finance" },
  "params": { "amount": 25.0 },
  "expected_effects": [
    { "op": "set_status", "target": "fee:F003", "from": "DISPUTED", "to": "WAIVED" },
    { "op": "set_field", "target": "fee:F003", "field": "waived_amount", "value": 25.0 }
  ],
  "preconditions": [
    { "kind": "state", "ok": true, "detail": "FeeItem 当前 DISPUTED，允许减免" },
    { "kind": "role", "ok": true, "detail": "finance 角色允许" },
    { "kind": "rule", "ok": true, "detail": "无规则阻断" }
  ],
  "blocked_reasons": [],
  "risk": { "level": "MEDIUM", "notes": "结算已入账时需同步冲正" },
  "confidence": 0.95,
  "evidence": [],
  "requires_confirmation": true,
  "executable": false,
  "as_of": "2026-11-16T09:30:00+08:00",
  "ontology_version": "1.4.0",
  "produced_at": "2026-11-19T10:00:00+08:00"
}
```

## 8. 待决策问题

| 编号 | 问题 | 备选 | 需要谁决策 |
|---|---|---|---|
| Q-14 | 提议的留存与确认方式 | 仅请求内返回 / 落库待人工确认 / 不保留 | 产品 + 安全 |
| Q-15 | Judgment 与 Proposal 是否使用同一 `as_of` 口径 | 统一时点 / 各自时点 | 本体平台组 |

## 9. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| 0.1 | 2026-11-19 | 首版：定义 Judgment / Evidence / Proposal 只读契约 |