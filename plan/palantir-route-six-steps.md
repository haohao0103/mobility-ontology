# Palantir 式本体路线：六步法实操指南

> 定位：用一个真实问题「这单为什么贵 38 元？」把 Palantir 式本体路线走一遍，每一步都对应 `mobility-ontology` 里的真实制品、接口和输出。
>
> 适用：新同学理解 Palantir 路线；演示前自查；方案评审时逐条对照。
>
> 口径：不宣称「离开本体绝对不可能」；统一说「只给原始数据、不给声明，答不出来；改一行声明，行为立刻翻转，代码零改动」。
>
> 依据：[demo-speech.md](demo-speech.md)、[demo_script.md](demo_script.md)、[../roadmap/palantir-benchmark.md](../roadmap/palantir-benchmark.md)、[../README.md](../README.md)

---

## 0. 一句话

**W3C 路线教你怎么「描述世界」；Palantir 路线教你怎么让世界「可操作」。六步法就是后者最小的可运营闭环。**

```
这单为什么贵 38？
  → 找对象 → 找关系 → 套规则 → 给动作建议 → 附证据 → 标版本
```

这六步不是六个技术模块，而是**一条从问题到可执行结论的路径**。少任何一步，结论都会退化成「看起来对的一段话」。

---

## 1. 六步总览

| 步 | 要回答的问题 | 确定性产出 | 系统落点 | 主要接口 |
|---|---|---|---|---|
| 1 找对象 | 这单是什么？ | 运单对象 + 关键属性 | `FreightSystem.get_object` | `GET /freight/objects/{id}` |
| 2 找关系 | 它挂了哪些费用、报价、证据？ | 对象图 | `graph` / `_relations_of` | `GET /freight/objects/{id}/graph` |
| 3 套规则 | 按规则应该收多少？ | 逐项差异 + 规则依据 | `fee_analysis` / `function.fee_breakdown` | `GET /freight/waybills/{id}/fee-analysis` |
| 4 给动作建议 | 现在能做什么、不能做什么？ | 动作提案 + 阻断原因 | `action_plan` / `action_preflight` | `POST /freight/mcp/call` |
| 5 附证据 | 凭什么这么说？ | 证据链 + 缺口 | `evidence_chain` / `trace` | `POST /freight/mcp/call`、`GET /freight/objects/{id}/trace` |
| 6 标版本 | 哪个版本、哪条规则、何时生效？ | 版本 + 规则 ID + 生效期 + checksum | `semantic_versions` / `semantic_diff` / `activate_version` | `GET /freight/ontology/versions` |

---

## 2. 六步详解

演示对象：`waybill:WB20261105001`，本体版本 `3.0.0`。

### 第 1 步：找对象

**目标**：把自然语言里的「这单」定位到一个确定的业务对象，而不是一张表或一行记录。

**动作**：

- 按业务号检索：`GET /freight/objects?type=Waybill&q=WB20261105001`
- 读取对象详情：`GET /freight/objects/waybill:WB20261105001`

**系统实现**：

- [freight/engine.py](../freight/engine.py) 的 `search` 与 `get_object`
- 对象类型、属性、状态来自 [freight/semantic_layer.json](../freight/semantic_layer.json)，当前注册表 **500 类 / 503 链接 / 1961 属性 / 16 规则 / 15 动作 / 4 函数**，覆盖 35 个业务域

**预期输出（演示库）**：

```json
{
  "object_id": "waybill:WB20261105001",
  "type": "Waybill",
  "status": "DELIVERED",
  "props": {
    "estimated_fee": 480.0,
    "actual_fee": 518.0,
    "variance": 38.0,
    "waiting_minutes": 53,
    "free_wait_minutes": 30
  }
}
```

**判定标准**：能按业务名唯一定位，且返回的是**带类型和状态的业务对象**，不是一行裸数据。

---

### 第 2 步：找关系

**目标**：从运单走到费用项、报价单、结算单、证据，建立可追溯的上下文。

**动作**：

- 取对象图：`GET /freight/objects/waybill:WB20261105001/graph?depth=2`
- 取事实快照：`GET /freight/facts/query/waybill:WB20261105001`

**系统实现**：

- `graph`、`_relations_of`、`_quote_detail_for_waybill`（[freight/engine.py](../freight/engine.py)）
- 演示库关键关系：
  - `waybill_produces_fee` → `fee:F001` ~ `fee:F007`
  - `order_has_quote` → 报价单与 `QuoteItem`
  - `fee_derived_from_quote_item` → 费用与报价明细的对应
  - `fee_included_in_settlement` → 费用归入结算
  - `waybill_has_exception` → 异常工单

**预期输出（演示库）**：

- 7 条费用：基础 380、里程 60、等候 25、高速 13、装卸 20、保险 10、其他 10
- 报价总额 480，实收总额 518，差额 38
- 费用项 `fee:F003`（等候费）状态为 `DISPUTED`

**判定标准**：每条金额都能沿关系回到它的来源（报价明细 / TMS / ETC / 计费中心），不靠模型猜。

---

### 第 3 步：套规则

**目标**：用可执行规则算出「应该收多少」，而不是解释「大概为什么」。

**动作**：

- 费用分析：`GET /freight/waybills/waybill:WB20261105001/fee-analysis`
- 或走确定性函数：`POST /freight/mcp/call`

```json
{
  "tool": "function.fee_breakdown",
  "arguments": { "waybill_id": "waybill:WB20261105001" }
}
```

**系统实现**：

- `fee_analysis`、`_expected_from_rule`、`_validate_pricing_rule`（[freight/engine.py](../freight/engine.py)）
- 规则 `R-WAIT-01`：等候 53 分钟，前 30 分钟免费，超出部分每 5 分钟 2.4 元
- 公式：`ceil((53 - 30) / 5) × 2.4 = 12 元`
- 计价参数来自 [freight/semantic_layer.json](../freight/semantic_layer.json) 的 `pricing.waiting`

**预期输出（演示库）**：

| 费用项 | 报价 | 规则应计 | 实收 | 结论 |
|---|---:|---:|---:|---|
| 基础运费 | 380 | 380 | 380 | 一致 |
| 里程费 | 60 | 60 | 60 | 一致 |
| **等候费** | 0 | **12** | **25** | **超收 13** |
| **高速费** | 0 | 13 | 13 | 据实结算，合理 |
| 装卸 / 保险 / 其他 | 40 | 40 | 40 | 一致 |

**归因结论**：

```
38 = 等候费新增 25 + 高速费新增 13
其中等候费按规则只应 12，超收 13；
高速费 13 有 ETC 扣费证据，属合理据实结算。
```

**判定标准**：金额全部由确定性规则计算，LLM 不参与金额与原因选择；算不出来时明确返回阻塞（如 `ONTOLOGY_COMPUTE_MISSING`），不给假数字。

---

### 第 4 步：给动作建议

**目标**：把「算出来的差异」变成「现在能做什么、需要谁做、会影响到谁」。

**动作**：

- 动作预检（只读，不写回）：

```json
{
  "tool": "function.action_preflight",
  "arguments": {
    "object_id": "waybill:WB20261105001",
    "action_id": "waive_charge"
  }
}
```

- 执行动作（受控写入）：

```http
POST /freight/actions/waive_charge
{
  "object_id": "fee:F003",
  "actor": "finance-01",
  "role": "finance",
  "payload": { "amount": 13.0 },
  "idempotency_key": "waive-fee-F003-20261121"
}
```

**系统实现**：

- `action_plan`、`action_preflight`、`execute_action`（[freight/engine.py](../freight/engine.py)）
- 动作声明在 [freight/semantic_layer.json](../freight/semantic_layer.json) 的 `actions`：
  - 发货主侧：`accept_order`、`reassign_waybill`、`confirm_loading`、`complete`
  - 费用与结算侧：`dispute_charge`、`waive_charge`、`hold_settlement`、`reverse_entry`、`void_invoice`
- 每个动作都带：角色、允许的前置状态、目标状态、参数、副作用、审计

**预期输出（演示库）**：

1. `dispute_charge`：对 `fee:F003` 发起费用申诉，自动创建并关联异常工单
2. `waive_charge`：减免 13 元
3. `reverse_entry`：对已入账结算冲正 13 元
4. `void_invoice`：冲正时同步红冲发票（由动作副作用触发）

被阻断时给出明确原因，例如：已装货不能再改派、已入账不能直接挂起只能冲正。

**判定标准**：动作不只是一个按钮，而是**带前置校验、权限、幂等、审计和副作用的完整事务**；执行前后都能回读业务事实。

---

### 第 5 步：附证据

**目标**：每条结论都能回源，回答「凭什么这么说」。

**动作**：

- 证据链函数：

```json
{
  "tool": "function.evidence_chain",
  "arguments": { "waybill_id": "waybill:WB20261105001" }
}
```

- 对象轨迹：`GET /freight/objects/waybill:WB20261105001/trace`
- 审计记录：`GET /freight/audit?object_id=fee:F003`

**系统实现**：

- `evidence_chain`、`_traceability`、`trace`、`audit`（[freight/engine.py](../freight/engine.py)）
- 证据类型覆盖：司机到达、装货确认、计价规则、ETC 扣费、回单、争议工单等

**预期输出（演示库）**：

- 运单与费用项共关联证据与事件，按对象归集
- 演示页面返回 **8 个时间线事件、14 条关系**
- 每条证据带：`evidence_id`、来源系统、发生时间、payload、checksum
- 缺失证据会进入 `missing_evidence_ids`，不静默放过

**判定标准**：结论中的每个数字都能找到「哪条规则 + 哪个事件 + 哪个系统」，而不是「模型记得」。

---

### 第 6 步：标版本

**目标**：说明结论属于哪个本体版本、哪条规则、哪个生效期；并证明「改声明，行为会翻转」。

**动作**：

- 列版本：`GET /freight/ontology/versions`
- 看差异：`GET /freight/ontology/diff?from_version=3.0.0&to_version=1.1.0-demo`
- 看影响面：`GET /freight/ontology/impact?from_version=3.0.0&to_version=1.1.0-demo`
- 切版本：`POST /freight/ontology/activate`，body `{"version": "1.1.0-demo"}`
- 回滚：`POST /freight/ontology/rollback`

**系统实现**：

- `semantic_versions`、`semantic_for`、`activate_version`、`rollback_version`、`semantic_diff`、`semantic_impact`（[freight/engine.py](../freight/engine.py)）
- 版本产物在 [freight/releases/](../freight/releases/)：`1.1.0-demo`、`1.3.1-demo`

**预期输出（演示库）**：

| 版本 | 免费等候 | 规则应计 | 行为 |
|---|---:|---:|---|
| `3.0.0` | 30 分钟 | 12 元 | 正常给出差异与归因 |
| `1.1.0-demo` | 20 分钟 | 规则缺 `judge.compute`；当前 `rule_expected=null`，但 `expected_amount` 仍回落到事实值 12 | 已产生 `ONTOLOGY_COMPUTE_MISSING` BLOCKER，但结论仍写「未发现规则超收」——**这是缺陷，不是演示效果** |

激活版本本身**不改任何代码文件**，只切换运行时加载的 `semantic_layer.json`；但当前 `1.1.0-demo` 的结论没有尊重 BLOCKER，修复前不能拿它证明「本体能拒绝给结论」。

**已知缺陷（AB-02 收口）**：`expected_amount` 仍取事实值而不是 `rule_expected`，且 `conclusion` 在存在 BLOCKER 时仍可能输出「未发现规则超收」。修复标准见 [ab-value-experiment-plan.md](ab-value-experiment-plan.md) 的 AB-02。

**判定标准**：结论必须带四样东西——**版本号、规则 ID、生效期、证据 checksum**；缺一样，结论不可审计。

---

## 3. 六步 ↔ 六个问题 ↔ 系统制品

| 六步 | 对应的本体问题 | 系统制品 |
|---|---|---|
| 找对象 | 有哪些东西？ | `classes`、`data_sources` |
| 找关系 | 它们怎么连？ | `relations`、`Link Type` |
| 套规则 | 守什么规矩？ | `rules`、`pricing`、规则 AST |
| 给动作建议 | 能做什么？ | `actions`、`execute_action`、副作用 |
| 附证据 | 凭什么证明？ | `evidence`、`events`、`audit`、checksum |
| 标版本 | 哪个版本、谁能改？ | `meta.version`、releases、diff/impact、权限 |

这张表就是 Palantir 式本体的全部内容：**对象、关系、规则、动作、权限与证据、版本**。它没有发明新数学，只是把这六件事收进同一份声明。

---

## 4. 反例对照：裸大模型为什么会答错

同一份原始数据，不给本体，直接丢给大模型：

| 问题 | 裸大模型 | 本体系统 |
|---|---|---|
| 等候费应计多少 | 把报价单里的 `0` 当成应计值，回答 0 元 | 按 `R-WAIT-01` 算出 12 元 |
| 实收多少 | 25 元 | 25 元 |
| 差额性质 | 「多收 25，凭不上」 | 「规则内 12 + 超收 13」 |
| 依据 | 无 | 规则 ID、版本、生效期、证据 ID、checksum |
| 动作建议 | 无或泛泛而谈 | 申诉 / 减免 / 冲正 / 红冲，带阻断条件 |

**差距不在数据里，在声明里。** 数据只告诉你「收了多少」，声明才告诉你「应该收多少、为什么、找谁、怎么改」。

---

## 5. 与 W3C 路线的边界

两条路线不是替代关系，是分层关系：

| 能力 | W3C（RDF/OWL/SHACL） | Palantir 式本体 |
|---|---|---|
| 概念形式化、类层次 | ✅ 强项 | ⚠️ 够用即可 |
| 逻辑蕴含（子类继承） | ✅ 强项 | ❌ 不做 |
| 算术与金额计算 | ❌ 不做 | ✅ 规则/函数 |
| 受控动作与写回 | ❌ 不做 | ✅ Action |
| 权限传播与审计 | ❌ 不做 | ✅ Role / Marking / Audit |
| 结构性约束校验 | ✅ SHACL | ✅ 也可导出 SHACL |

当前落地方式：

- 主干走 Palantir 式可运营本体；
- 私有约束通过 [tools/export_shacl.py](../tools/export_shacl.py) 导出为标准 SHACL，纳入 CI 语义门禁；
- OWL 推理机（HermiT / Pellet / Owlready2）是 Phase 2 选项，当前未启用；需要时以 Sidecar 外挂，不推翻主干。

---

## 6. 五分钟自检

```bash
# 1. 语义门禁（CI 同款）
python scripts/semantic_gate.py

# 2. freight 层验收（含承重测试）
python scripts/freight_acceptance.py

# 3. 只改本体 JSON、代码零改动，验证行为翻转
python scripts/load_bearing_demo.py
```

服务起来后，按六步逐一验证：

```bash
BASE=http://127.0.0.1:8765

# 1 找对象
curl "$BASE/freight/objects/waybill:WB20261105001"

# 2 找关系
curl "$BASE/freight/objects/waybill:WB20261105001/graph?depth=2"

# 3 套规则
curl "$BASE/freight/waybills/waybill:WB20261105001/fee-analysis"

# 4 动作预检
curl -X POST "$BASE/freight/mcp/call" -H 'Content-Type: application/json' \
  -d '{"tool":"function.action_preflight","arguments":{"object_id":"waybill:WB20261105001","action_id":"waive_charge"}}'

# 5 附证据
curl -X POST "$BASE/freight/mcp/call" -H 'Content-Type: application/json' \
  -d '{"tool":"function.evidence_chain","arguments":{"waybill_id":"waybill:WB20261105001"}}'

# 6 标版本
curl "$BASE/freight/ontology/versions"
```

自检通过的标准：

- [ ] 前 3 步能给出确定数字，且数字之间能对上（480 / 518 / 38 / 12 / 13）
- [ ] 第 4 步能区分「允许 / 阻断」，并给出原因
- [ ] 第 5 步每条结论都有证据，缺失项显式列出
- [ ] 第 6 步切换版本后结论翻转，代码零改动
- [ ] 全程 LLM 不参与金额计算与原因选择

---

## 7. 术语对照：Palantir → 工程 → 本系统

| Palantir 概念 | 你熟悉的工程对应物 | 本系统落点 |
|---|---|---|
| Object Type | 领域模型 / ORM Entity / 表结构 | `classes` |
| Property | 字段 + 类型约束 | `properties` / 属性注册表 |
| Link Type | 外键 / 关联表 / 图的边 | `relations` |
| Interface | 接口 / 抽象类 / 多态 | `enhancements/interfaces.py` |
| Action Type | 带校验、权限、审计的事务写接口 | `actions` / `execute_action` |
| Function | 服务端业务代码 | `functions` / `fee_breakdown` 等 |
| Derived Property | 视图 / 计算字段 | `fee_analysis` 等派生计算 |
| Role / Marking | RBAC / ABAC | `enhancements/security.py` |
| Lineage / Version | 审计日志 / git 快照 | `freight/lineage.py` / `releases/` |
| Ontology（整体） | 领域模型 + 规则引擎 + 工作流 + 权限 + 审计 + 血缘 | 整个 `freight/` |

---

## 8. 常见误区

1. **把本体当图数据库**：图只是关系的一种视图，本体还包括规则、动作、权限、版本。
2. **把本体当文档**：本体必须是可执行的；写不进代码的定义，等于没定义。
3. **把所有计算塞进推理机**：算术归规则引擎，校验归 SHACL，动作归 Action，推理机只做分类与蕴含。
4. **先建全域平台再找场景**：正确顺序是先跑赢 2–3 个垂直闭环，再抽象共性。
5. **让 LLM 参与金额和归因**：LLM 负责表达与编排，数字和原因必须来自确定性规则。

---

## 9. 收束

**这单为什么贵 38？**

> 因为等候费多收 13 元、高速费据实新增 13 元，加上规则内等候费 12 元，合计比报价多 38 元。
>
> 等候费判定的依据是规则 `R-WAIT-01`（版本 `3.0.0`，生效期已核对），证据由 TMS 装货记录与计价规则共同支撑；处理建议是减免 13 元并冲正结算、同步红冲发票。

这段话里的每个数字、每条依据、每个动作，都对应六步中的一步。

**这就是 Palantir 路线：不是把数据画成图，而是让「问题 → 对象 → 关系 → 规则 → 动作 → 证据 → 版本」变成一条可执行、可审计、可复现的路径。**