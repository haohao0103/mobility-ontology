# 计划：交互式演示收口（6 主线 + 4 深挖 · 本体排他性验证）

> 状态：待执行
> 更新：2026-11-21
> 配套文档：[demo_script.md](demo_script.md)、[source-contract-plan.md](source-contract-plan.md)、[abox-depth-plan.md](abox-depth-plan.md)
> 关联页面：[web/order-ontology.html](../web/order-ontology.html)
> 原则：不写工期，只排优先级与依赖；所有数字来自实跑；所有负向实验不得污染 STG 主库

## 0. 背景：现在的演示“覆盖了功能”，但还没“串成证据链”

当前页面已有 8 个交互场景（`bearing/lineage/governance/query/types/agent/evidence/security`），覆盖了规则、血缘、治理、查询、类型、推理、证据、权限。但按“每个核心组件都有可交互问题 + 每个结论离开本体就得不出来”两条硬标准审计，存在三类缺口：

| 问题 | 具体表现 |
|---|---|
| 核心件缺失 | 状态机/动作执行、来源契约/缺口检测/ABox 覆盖、接口多态没有形成追问；工作流、副作用、批量事务、沙箱只有后端能力，没有演示入口 |
| 静态承重 | `types` 的 L3-L8、`governance` 的破坏性门禁只展示静态表，或调用当前状态必过的 `/ontology/validate`，没有真实“改声明 → 翻转” |
| 文案失真 | 标题“六个追问”实际 8 个场景；演示条“6 幕”“不依赖大模型”；正文引用不存在的“第 1/4 个问题” |

本计划把演示从“功能巡礼”收口为“**6 个可答辩的主线追问 + 4 个可展开的深挖证据**”，每个主问题都必须包含：

```text
本体声明 → 真实接口执行 → 输入/输出可见 → 抽掉声明 → 结果翻转 → 收束口径
```

## 1. 目标与硬验收判据

| 编号 | 判据 | 验收方式 |
|---|---|---|
| C1 | 每个核心组件至少有一个真实接口步骤，不用静态表格冒充 | 逐场景检查 `COMP_SCENARIOS`，每步有 `request` 且为真实路径 |
| C2 | 每个主线问题至少有一个“改/删声明 → 结果翻转”的负向验证 | `demo_gate.py --strict` 断言 `flips` 非空 |
| C3 | STG 主库零污染：演示运单、对象数、审计流水不被改动 | 实验前后 checksum/计数对比；lab 返回 `persisted:false` |
| C4 | 不预跑数据、不隐藏输入输出 | 页面每步展示 INPUT/OUTPUT；请求由用户点击触发 |
| C5 | 全链路只在 STG 验收 | 只访问 `http://<stg>:8765`；不本地起服务、不部署 PRD |
| C6 | 文案与实现一致 | 标题/幕数/LLM 依赖/问题编号逐项对照代码 |

## 2. 目标信息架构：6 主线 + 4 深挖

### 2.1 主线 6 问（默认展示，用于完整讲解）

| 序号 | 问题 | 串起的核心件 | 负向验证 | 当前状态 |
|---|---|---|---|---|
| M1 | 同一笔费用，换一版本本体，结论会变吗？ | 规则 AST、版本、生效期、结论绑定版本 | 切 `1.1.0-demo`：应计 12 → 无法计算/BLOCKER | 已有，补声明依赖 |
| M2 | 已送达的运单还能改派吗？ | 状态机、动作前置、角色、参数契约、幂等、审计、事件 | 删 `from_status`/角色声明 → 动作放行；同幂等键重放只执行一次 | 待新增 |
| M3 | 这笔费用谁认领、缺什么证据？ | 数据源契约、费用归属、新鲜度、证据要求、ABox 深度 | 删 TMS 对 `waiting` 的认领 → `FEE_SOURCE_UNRESOLVED` 出现；恢复 → 消失 | 待新增 |
| M4 | 同一结论，为什么不同角色看到的不一样？ | 行策略、列脱敏、marking 传播、Agent 输出闸 | 改 `actual_amount` 的 marking → 可见性翻转；无权限角色输出被拒 | 扩展现有 security |
| M5 | AI 凭什么不编？ | 工具白名单、TBox 圈定、证据 checksum | 抽掉工具/类型声明 → 链路构造失败（确定性校验） | 扩展现有 agent |
| M6 | 这单到底为什么贵 38？ | 规则、证据、版本、可解释结论 | 缺 AST/规则版本 → 不能计算；补证据 → 结论变化 | 已有，补声明依赖 |

### 2.2 深挖 4 题（领导追问时展开）

| 序号 | 问题 | 串起的核心件 | 负向验证 | 当前状态 |
|---|---|---|---|---|
| D1 | 改一个属性会波及谁？ | 属性级血缘、对象级血缘、影响面 | 已有属性/对象血缘与影响面 | 已有 |
| D2 | 改本体能不能像代码一样评审？ | 版本快照、破坏性门禁、签字、回滚 | 注入“删属性”：未签字 FAIL，补签 PASS | 待改造 |
| D3 | 查业务语义，不查表名？ | Object Set、Ontology SQL、派生类型、接口多态 | 删接口必需属性 → 实现类减少，多态函数拒绝 | 待补接口多态 |
| D4 | 1961 个属性注册表真的执行吗？ | 属性注册表、写入门禁、函数签名、marking | 改属性类型 → 创建/更新/函数调用分别被拒；改回 → 放行 | 待改造 |

## 3. 后端开发

### 3.1 复用已有能力

| 能力 | 现有入口 | 用途 |
|---|---|---|
| 状态机总览/单类型 | [freight/api.py](../freight/api.py) `/freight/ontology/state-machines` | M2 展示状态集、终态、迁移边、可达集 |
| 对象可用/被阻动作 | [freight/engine.py](../freight/engine.py) `get_object` | M2 展示 `available_actions` / `blocked_actions` |
| 动作执行、角色、前置、幂等 | [freight/engine.py](../freight/engine.py) `execute_action` | M2 真实拒绝/放行/重放 |
| 来源覆盖与缺口 | [freight/engine.py](../freight/engine.py) `_fee_source_coverage` | M3 展示解析状态、来源、证据、新鲜度 |
| ABox 覆盖率 | [freight/abox.py](../freight/abox.py) `measure/check` | M3 展示深水区类/边/动作/规则覆盖 |
| 语义门禁演示 | [freight/engine.py](../freight/engine.py) `semantic_guard_demo` | D2 注入非法规则 |
| 破坏性变更评审 | [freight/change_gate.py](../freight/change_gate.py) `review` | D2 未签字/已签字对照 |
| 权限模拟 | [enhancements/api.py](../enhancements/api.py) `/security/simulate` | M4 行/列可见性 |
| Agent 输出闸 | [enhancements/security.py](../enhancements/security.py) `guard_output` | M4 输出侧拒绝/脱敏 |
| 接口多态 | [enhancements/interfaces.py](../enhancements/interfaces.py) `InterfaceRegistry` | D3 结构匹配与函数路由 |
| 函数调用 | [enhancements/api.py](../enhancements/api.py) `/functions/{id}/call` | D3、D4 验证签名与返回结构 |

### 3.2 新增接口

| ID | 接口 | 行为 | 是否写主库 |
|---|---|---|---|
| B-01 | `GET /freight/contracts/coverage?waybill_id=` | 返回费用归属矩阵、逐费用解析状态、新鲜度、证据覆盖、缺口清单 | 否 |
| B-02 | `POST /freight/contracts/simulate` | 在临时库副本/修改后的本体副本上删认领、删证据要求、删一条证据，返回 before/after findings | 否 |
| B-03 | `GET /freight/ontology/abox-coverage` | 返回深水区类/边/动作/规则覆盖率与问题清单 | 否 |
| B-04 | `POST /freight/actions/lab` | 在 `SAVEPOINT` 内执行错角色、错状态、允许动作、同键重放，最后 `ROLLBACK` | 否 |
| B-05 | `POST /freight/ontology/guard-demo` | 扩展 `case`：`unknown_fee_type`、`drop_property_unsigned`、`drop_property_signed` | 否 |
| B-06 | `POST /freight/ontology/type-lab` | 用修改后的本体副本跑创建/更新/函数调用，返回拒绝码或放行结果 | 否 |
| B-07 | `POST /freight/agent/declaration-check` | 确定性检查工具白名单、可圈定类型、证据 checksum；支持 `omit_tool`/`omit_type` | 否 |
| B-08 | `POST /enhancements/interfaces/simulate` | 删接口必需属性，返回实现类 before/after 与函数是否接受 | 否 |
| B-09 | `POST /enhancements/security/guard-output` | 对指定对象执行 Agent 输出闸，返回脱敏或 403 | 否 |
| B-10 | `POST /enhancements/security/simulate-marking` | 在内存副本上删/改 marking，返回行可见性与列可见性翻转 | 否 |

统一返回约定：

```json
{
  "case": "drop_property",
  "before": {},
  "after": {},
  "flips": [{"code": "FEE_SOURCE_UNRESOLVED", "action": "appeared"}],
  "declaration_paths": ["data_sources[TMS].fee_types[waiting]"],
  "persisted": false
}
```

### 3.3 任务清单

| ID | 任务 | 产出物 | DoD | 依赖 |
|---|---|---|---|---|
| B-11 | 给 `SemanticLayer` 增加 `from_raw(raw)` | `freight/semantic.py` | 不落临时 JSON 即可构造修改后的本体；现有构造路径不变 | — |
| B-12 | 给 `FreightSystem` 增加可选 `semantic` 注入 | `freight/engine.py` | 仅 lab 使用；默认行为与现有完全一致 | B-11 |
| B-13 | 新建 `freight/demo_labs.py` | 克隆本体、临时库副本、`SAVEPOINT` 包装、契约/动作/类型 lab | 所有 lab 函数返回统一结构；有单测 | B-11/B-12 |
| B-14 | 实现 B-01/B-02/B-03 | `freight/api.py`、`freight/demo_labs.py` | 正常运单零缺口；删认领后出现 `FEE_SOURCE_UNRESOLVED`；恢复后消失；ABox 覆盖率可读 | B-13 |
| B-15 | 实现 B-04 | 同 上 | 错角色 `FORBIDDEN_ROLE`、错状态 `INVALID_STATE`、同键第二次 `replayed=true`、回滚后审计数为 0 | B-13 |
| B-16 | 扩展 B-05 | `freight/engine.py`、`freight/change_gate.py` | 删属性未签字 `FAIL` 且 `impacts` 非空；补签 `PASS`；主版本不变 | B-11 |
| B-17 | 实现 B-06 | `freight/demo_labs.py`、`freight/api.py` | L3/L4/L5/L7/L8 对应用例返回预期错误码；改回声明后放行 | B-12/B-13 |
| B-18 | 实现 B-07 | `freight/engine.py`、`freight/api.py` | `omit_tool` → 工具不可用；`omit_type` → 类型不可圈定；不改主库 | — |
| B-19 | 实现 B-08 | `enhancements/api.py`、`enhancements/interfaces.py` | 删必需属性后实现类减少；`chargeable_variance` 从接受变拒绝 | — |
| B-20 | 实现 B-09/B-10 | `enhancements/api.py`、`enhancements/security.py` | 匿名/无标记输出 403；财务输出脱敏；改 marking 后可见性翻转 | — |
| B-21 | 新增 `tests/test_demo_labs.py` | `tests/` | 覆盖 B-14 至 B-20 的正向与负向断言；断言主库 checksum/计数不变 | B-14~B-20 |

## 4. 前端开发

改造 [web/order-ontology.html](../web/order-ontology.html)：

| ID | 任务 | 具体做法 | DoD |
|---|---|---|---|
| F-01 | 场景分层 | 给 `COMP_SCENARIOS` 增加 `tier: 'main' \| 'deep'`；默认渲染主线 6 个，深挖折叠 | 页面首屏只有 6 个主线问题 |
| F-02 | 新增 `machine` 场景 | 状态机声明 → 对象可用/被阻动作 → 错角色 → 错状态 → 允许动作 → 同键重放 → 审计/状态轨迹 | 6 步全部来自 B-04/B-05；无静态表 |
| F-03 | 新增 `contracts` 场景 | 归属矩阵 → 正常零缺口 → 删认领翻转 → 缺证据翻转 → ABox 覆盖 → 收束 | 每步展示 `before/after` 与 `persisted:false` |
| F-04 | 扩展 `security` | 增加 Agent 输出闸、marking 翻转两步 | 同一结论在匿名/争议/财务/Agent 输出下差异可见 |
| F-05 | 扩展 `agent` | 增加确定性 `declaration-check`；真实 LLM 链只保留最后一击 | `--fast` 不依赖 LLM；抽掉声明可确定性失败 |
| F-06 | 扩展 `query` | 增加接口多态：`interfaces` → 删必需属性 → 实现类减少 → 函数拒绝 | 不再只展示查询语法 |
| F-07 | 改造 `types` | 把静态 L3-L8 表换成 `type-lab` 真实拒绝；保留一条“改回声明→放行” | 页面无静态承重表 |
| F-08 | 改造 `governance` | 把 `/ontology/validate` 换成 `guard-demo` 未签字/已签字对照 | 未签字 FAIL、补签 PASS 均实时返回 |
| F-09 | 声明依赖脚注 | step 增加可选 `dependency` 字段，统一渲染“本步依赖 X；抽掉后 Y” | 每个主线步骤都有声明依赖说明 |
| F-10 | 文案修正 | 标题、演示条、`DEMO_STORY`、正文问题编号统一 | 不再出现 6/8 混用、虚构问题编号、“全程不依赖大模型” |
| F-11 | 演示主线改为 6 幕 | `DEMO_STORY` 只保留主线 6 问；深挖 4 题作为“追问菜单” | 演示条与实际幕数一致 |

## 5. 文档与答辩口径

| ID | 任务 | 产出物 | DoD |
|---|---|---|---|
| D-01 | 更新 [demo_script.md](demo_script.md) | 6 主线 + 4 深挖脚本 | 每问含开场问、声明、负向实验、期望输出、兜底话术 |
| D-02 | 建立“裸系统对照”口径 | 脚本附录 | 每问写清：裸 SQL 能答什么、裸大模型会错在哪、本体多出哪一步声明 |
| D-03 | 统一排他性表述 | 脚本附录 | 统一为“只给原始数据、不给声明时答不出来；改声明行为翻转”，不宣称绝对排他 |
| D-04 | 演示兜底 | 脚本附录 | LLM 不可用时走确定性 `declaration-check`；STG 故障时不降级到本地 |

## 6. 验证与验收

### 6.1 自动化验证

| ID | 任务 | 命令/产物 | DoD |
|---|---|---|---|
| V-01 | 单测 | `pytest` | 新增 lab 用例全过；原有 363+ 用例不回归 |
| V-02 | 语义门禁 | `python scripts/semantic_gate.py` | rc=0；门禁项数不减少 |
| V-03 | 演示自检 | `python scripts/demo_gate.py --fast --strict` | 新接口全 200；每个负向 `flip` 命中 |
| V-04 | 覆盖审计 | `python scripts/demo_coverage_audit.py` | 输出主线 6 问覆盖矩阵 + 裸 SQL/裸 LLM 对照 |
| V-05 | 真实 LLM 链 | `python scripts/demo_gate.py` | 推理链可完成；耗时基线记录；失败有明确错误 |

`demo_gate.py` 扩展要求：

- 新接口全部加入 `CHECKS`；
- 增加 `check_scenarios()`：逐场景调用关键步骤并断言预期 code；
- `--fast` 跳过真实 LLM，但必须包含所有负向翻转；
- `--strict` 任一翻转未出现即失败。

### 6.2 STG 验收顺序

1. 部署到 STG，重启服务；
2. 确认剧本对象仍为 16 个，`waybill:WB20261105001` 状态与关键属性未变；
3. `python scripts/semantic_gate.py` rc=0；
4. `pytest` 全绿；
5. `python scripts/demo_gate.py --fast --strict` 全绿；
6. 浏览器逐题点一遍，确认每步 INPUT/OUTPUT 完整；
7. 最后跑一次含真实 LLM 的 `demo_gate.py`；
8. 记录实验前后主库 checksum/对象数/审计数，确认一致。

## 7. 实施顺序与阶段退出标准

| 阶段 | 内容 | 退出标准 |
|---|---|---|
| M0 | 基线快照与安全约束 | 记录主库对象/审计 checksum；确定 lab 不写主库；STG 可达 |
| M1 | `demo_labs.py` + 新后端接口 | B-11~B-21 完成；单测通过；STG 上能看到第一组真实翻转 |
| M2 | 主线 6 问前端改造 | F-01~F-05、F-09 完成；6 问每问可独立走通 |
| M3 | 深挖 4 题改造 | F-06~F-08 完成；治理/查询/类型不再有静态假承重 |
| M4 | 文案、脚本、答辩口径 | F-10/F-11、D-01~D-04 完成；标题/幕数/LLM 表述一致 |
| M5 | 全链路验收 | V-01~V-05 全过；STG 验收清单全部勾选 |

## 8. 明确不做

- 不新增业务规则、不扩充本体类，除非负向实验必须；
- 不修改 `waybill:WB20261105001` 的任何业务属性或状态；
- 不把 lab 数据写进 STG 主库；
- 不本地部署、不启动本地 8765 替代 STG；
- 不部署 PRD；
- 不在验收前提交或推送未验证改动。

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| 动作实验室污染审计 | 全部 `SAVEPOINT` + `ROLLBACK`；测试断言回滚后审计增量为 0 |
| 契约模拟需要构造缺失证据 | 只改临时库副本，不动主库；返回 `persisted:false` |
| LLM 链不稳定/超时 | 主线 M5 以确定性 `declaration-check` 为主，真实链为辅；`--fast` 不依赖 LLM |
| 页面变长 | 主线 6 + 深挖折叠，默认只渲染主线 |
| 术语“只有本体能解决”过头 | 统一口径：“只给原始数据、不给声明时答不出来；改声明行为翻转” |
| 新增接口影响现有服务 | 只读或事务内实验；默认路径不变；完整回归 |

## 10. 完成定义（DoD）

- [ ] 主线 6 问全部可独立走通，每步有真实 INPUT/OUTPUT；
- [ ] 每个主线问题至少有一个负向翻转，且页面可见；
- [ ] 深挖 4 题不再有静态承重表；
- [ ] `types`、`governance` 的旧静态断言已删除或改为实时接口；
- [ ] 标题、幕数、LLM 依赖、问题编号与代码一致；
- [ ] `pytest`、`semantic_gate.py`、`demo_gate.py --fast --strict` 全绿；
- [ ] STG 主库对象数、审计数、演示运单 checksum 实验前后一致；
- [ ] 全程未使用本地服务，未部署 PRD。