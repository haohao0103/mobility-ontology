# 本体价值对照实验开发计划（A/B）

> 状态：Draft（待评审）
> 版本：0.1
> 更新：2026-11-21
> 上游判断：[palantir-route-six-steps.md](palantir-route-six-steps.md)
> 执行清单：[plan/README.md](README.md)
> 相关资产：[load_bearing_demo.py](../scripts/load_bearing_demo.py)、[semantic_gate.py](../scripts/semantic_gate.py)、[freight_acceptance.py](../scripts/freight_acceptance.py)

## 1. 文档目的

本文不讨论「本体能不能回答问题」。这个问题太容易赢。

本文要回答的是：

> **在同样三个真实业务问题上，本体方案（B 组）相比一个合格的属性图方案（A 组），是否在四个可量化指标上明显更优？**

四个指标固定为：

1. 改一条规则，要改几个文件、谁改、多久生效；
2. 新增一个车型/费用类型，要不要动代码；
3. 回答能否稳定带出版本、规则 ID、证据 ID；
4. 非研发人员能否自己确认和发布。

**本实验允许得出「本体没有明显价值」的结论。** 如果 B 组没有赢，就按第 2 节的决策规则执行，不追加新论据、不移动验收标准。

本文不写时间估算，只排任务、依赖和退出标准。

---

## 2. 预注册结论规则

实验开始前先锁定判定规则，避免结果出来后再解释。

| B 组结果 | 决策 |
|---|---|
| 在 M1、M3 上赢，且 M2、M4 至少再赢一项 | 继续投入本体路线，进入第二个垂直闭环 |
| 只在 M1、M3 上赢 | 保留本体作为「治理与审计层」，不扩张引擎能力 |
| 只在 M3 上赢 | 降级为「口径与审计元数据服务」，不再按平台投入 |
| 在 M1、M3 上打平或输 | 停止本体投入，迁移为属性图 + YAML 规则 + 硬编码动作 |

补充约束：

- 任一指标只要 A、B 都做到零代码，就记为平局，不算 B 赢；
- 正确性门禁不通过，四指标对比无效，先修正确性；
- 不比较 LLM 回答质量，不比较图数据库性能，不比较界面美观；
- 所有原始数据、脚本、结果必须可复现；不可复现的结果不进入决策。

---

## 3. 实验设计

### 3.1 控制变量

| 变量 | 控制方式 |
|---|---|
| 事实数据 | 两组使用同一份 FactStore 快照，checksum 一致 |
| 问题集 | 实验前冻结，业务方签字确认 |
| 输出契约 | 两组实现同一个 `answer(question)` 接口，返回同一 schema |
| 运行环境 | 同一台机器、同一 Python 版本、同一进程隔离方式 |
| 评测人 | 评分人不知道哪份结果来自 A 组 |
| 计时口径 | 从执行变更命令开始，到新答案正确返回为止；含重启时间 |
| 重复次数 | 每个测量跑 3 次，取中位数；确定性结果必须完全一致 |

### 3.2 两组定义

| | A 组：属性图基线 | B 组：现有本体系统 |
|---|---|---|
| 数据模型 | 属性图（节点/边/属性），可用 SQLite 或 NetworkX 实现 | `semantic_layer.json` 声明 + FactStore |
| 规则 | YAML 参数 + Python 计算函数 | 规则 AST / `judge.compute` + `compute_fee` |
| 动作 | 硬编码 Python 函数 | `actions` 声明 + `execute_action` / `action_preflight` |
| 版本 | Git 或文件名 | `releases/` + `activate_version` / `rollback_version` |
| 证据 | 响应中按需手工拼装 | `evidence_chain` + 审计 + checksum |
| 变更入口 | 改 YAML / 改代码 / 重启 | 改 JSON / 发布 / 激活 / 回滚 |
| 公平性要求 | 必须是一个合格实现，不能故意做残 | 不允许使用 A 组代码，不允许人工兜底 |

**A 组不是稻草人。** A 组必须由一位认可属性图路线的同学实现，并通过独立评审；否则实验无效。

### 3.3 三个真实业务问题

问题必须来自真实业务方，不能只来自演示脚本。演示问题可以作为代理，但必须在报告中标注「代理问题，未获业务签字」。

| ID | 业务问题 | 黄金答案（基线版本 3.0.0） |
|---|---|---|
| Q1 | 运单 `WB20261105001` 为什么比预估贵 38 元？ | 报价 480、实收 518、差异 38；等候费实收 25、规则应计 12、超收 13；高速费 13 有 ETC 证据、属合理；结论带 `R-WAIT-01`、版本、证据 |
| Q2 | 把等候免费时长从 30 分钟改成 20 分钟，这单结论怎么变？影响哪些运单？ | 规则应计从 12 变为 16.8（`ceil((53-20)/5)*2.4`）；超收从 13 变为 8.2；总差异仍为 38，但归因变化；至少影响 `WB20261105001`，并列出所有等候时长大于 20 分钟的运单 |
| Q3 | 这单要退 13 元，结算已入账，能不能直接挂起？应该走哪条动作链？ | `hold_settlement` 被 `R-SETTLE-01` 阻断；`waive_charge` 减免 13 后必须继续 `reverse_entry`；冲正后结算从 518 调整为 505，关联发票红冲为 `VOIDED` |

### 3.4 两个变更任务

| ID | 变更内容 | 期望结果 |
|---|---|---|
| C1 | `pricing.waiting.free_minutes` 从 30 改为 20，同步所有车型覆盖 | Q1 等候应计变 16.8；Q2 影响面正确；可回滚 |
| C2 | 新增车型 `7.6米货车`：免费 30 分钟、每 5 分钟 3.0 元 | 新车型运单等候应计 15 元；旧车型结论不变；可回滚 |

C2 的测试运单在实验前由两组共同准备，事实数据一致；只比较「让新车型参与计价」所需的知识层改动。

### 3.5 四个指标

| 指标 | 采集项 | 计算方式 |
|---|---|---|
| M1 规则变更成本 | 改动文件数、代码文件数、代码行数、是否重启、耗时、操作人角色、回滚步骤 | 从变更命令到新答案正确返回；`git diff --stat` 自动采集 |
| M2 新增车型成本 | 代码文件数、代码行数、新增声明数、是否新增测试、影响面是否自动给出、耗时 | 同 M1；影响面由系统自动返回才算通过 |
| M3 答案可审计性 | 版本、规则 ID、生效期、证据 ID、证据可解析、checksum | `audit_score = 已具备且有效的字段数 / 5`，每条答案单独打分 |
| M4 非研发可用性 | 是否独立完成、研发介入次数、操作步数、出错次数、回滚是否成功、主观信心 1-5 | 观察记录表；同一任务卡、同一培训口径 |

M3 的五个字段：

```json
{
  "ontology_version": "3.0.0",
  "rule_ids": ["R-WAIT-01"],
  "effective_from": "2026-10-01",
  "evidence_ids": ["evidence:E001"],
  "evidence_checksums": {"evidence:E001": "..."}
}
```

证据 ID 必须能在事实库中解析到对象，否则该字段记 0 分。

### 3.6 判定阈值

| 指标 | B 组赢的条件 | 平局条件 |
|---|---|---|
| M1 | B 零代码文件、≤1 个声明文件；A 至少 1 个代码文件或 ≥2 个文件 | 两组都零代码 |
| M2 | B 零代码新增车型，且自动返回影响面；A 需要改代码 | 两组都零代码且都无影响面 |
| M3 | B 的 `audit_score` 为 100%，证据全部可解析；A 低于 100% | 两组都是 100% |
| M4 | B 由非研发独立完成，研发介入 0 次，回滚成功；A 至少介入 1 次 | 两组都 0 次介入 |

---

## 4. 目录与接口

实验代码全部放在 `experiments/ab_value/`，不修改 `freight/` 生产路径。

```text
experiments/ab_value/
  protocol.md                 # 本计划的实验执行版（含签字）
  contract.py                 # Candidate 接口与 Answer schema
  fixtures/
    questions.json            # 三个问题
    golden.json               # 黄金答案与容差
    change_cards.json         # C1/C2 变更卡
    dataset.sha256            # 事实快照校验和
  group_a/
    app.py                    # 属性图实现
    rules.yaml                # YAML 规则
    actions.py                # 硬编码动作
    change_cli.py             # 变更与回滚入口
  group_b/
    adapter.py                # 调用现有 FreightSystem
  harness/
    run.py                    # 跑问题、变更、计时
    score.py                  # 正确性门禁与四指标计算
    report.py                 # 生成 report.md
  results/
    raw/                      # 原始 JSON
    report.md                 # 最终报告
```

统一接口：

```python
class Candidate(Protocol):
    def reset(self) -> None: ...
    def answer(self, question: dict) -> dict: ...
    def apply_change(self, change: dict) -> dict: ...
    def rollback(self, change_id: str) -> dict: ...
```

统一答案 schema：

```json
{
  "question_id": "Q1",
  "answer": "文本结论",
  "numbers": {},
  "rule_ids": [],
  "ontology_version": "",
  "effective_from": "",
  "evidence_ids": [],
  "evidence_checksums": {},
  "actions": [],
  "blocked_reasons": []
}
```

隔离要求：

- A 组禁止 `import freight.*`，禁止读取 `freight/semantic_layer.json`；
- B 组禁止读取 `experiments/ab_value/group_a/`；
- 增加 `tests/test_ab_isolation.py`，在 CI 中静态扫描 import 与路径引用。

---

## 5. 开发任务

### 5.1 阶段 0：基线与公平性

| ID | 任务 | 产出物 | DoD | 依赖 |
|---|---|---|---|---|
| AB-01 | 评审并冻结实验协议 | 本文 + 签字记录 | 决策规则、问题、指标、阈值全部锁定；打 tag `ab-experiment-v1` | 无 |
| AB-02 | 收口声明承重与拒绝语义 | 修改 [engine.py](../freight/engine.py)：规则存在时 `expected_amount` 必须取 `rule_expected`，不得回落到事实 `expected`；存在 BLOCKER 时 `conclusion` 必须写「无法判定」，不得输出「未发现规则超收」；清掉 `rule_wait_fee` 的硬编码初值；补回归 | 只改 `pricing.waiting.free_minutes`，Q1 答案翻转；激活 `1.1.0-demo` 时必须返回 `ONTOLOGY_COMPUTE_MISSING` + `expected_amount=null` + 结论「无法判定」，不得出现「未发现规则超收」；`load_bearing_demo.py` 新增对应用例 | AB-01 |
| AB-03 | 冻结事实与问题集 | `fixtures/questions.json`、`golden.json`、`dataset.sha256` | 三个问题有业务 Owner 签字；黄金答案可机器比对；数据 checksum 固定 | AB-01 |
| AB-04 | 建立统一契约与隔离 | `contract.py`、`tests/test_ab_isolation.py` | 两组实现同一接口；隔离测试在 CI 通过 | AB-02/03 |
| AB-05 | 实现 A 组 | `group_a/app.py`、`rules.yaml`、`actions.py`、`change_cli.py` | 三个问题全部通过正确性门禁；独立评审确认「不是稻草人」 | AB-04 |
| AB-06 | 适配 B 组 | `group_b/adapter.py` | 使用隔离临时库，三个问题全部通过；不修改 `freight/` 核心逻辑 | AB-04 |
| AB-07 | 实现评分器 | `harness/run.py`、`score.py`、`report.py` | 一条命令产出原始 JSON 与 Markdown；结果可重复 | AB-04 |

### 5.2 阶段 1：四指标测量

| ID | 任务 | 产出物 | DoD | 依赖 |
|---|---|---|---|---|
| AB-08 | C1 规则变更实验 | `results/raw/m1_c1.json` | 两组完成 30→20 变更；记录文件数、代码行、耗时、重启、回滚；Q1/Q2 结果正确 | AB-05/06/07 |
| AB-09 | C2 新增车型实验 | `results/raw/m2_c2.json` | 两组新增 `7.6米货车`；记录代码改动与影响面；旧车型结论不变 | AB-05/06/07 |
| AB-10 | M3 审计评分 | `results/raw/m3_audit.json` | 三问题 × 基线/C1/C2 全部采集；证据 ID 可解析；评分人盲评 | AB-07/08/09 |
| AB-11 | M4 非研发测试 | `results/raw/m4_usability.json`、观察记录 | 至少 2 名非研发完成同一任务卡；记录介入次数、步数、错误、回滚；两组同口径 | AB-05/06 |

### 5.3 阶段 2：报告与决策

| ID | 任务 | 产出物 | DoD | 依赖 |
|---|---|---|---|---|
| AB-12 | 生成对比报告 | `results/report.md` | 包含结论摘要、四指标对比、变更明细、异常样本、决策建议 | AB-08/09/10/11 |
| AB-13 | 独立复核 | 复核意见 | 复核人不在本体团队；能复跑全部脚本；无异议或已记录异议 | AB-12 |
| AB-14 | 决策落地 | Roadmap/Plan 更新 | 按第 2 节规则给出继续/收缩/停止结论，并落到 [plan/README.md](README.md) | AB-13 |

---

## 6. 执行顺序与退出标准

### 6.1 顺序

1. AB-01 冻结协议；
2. AB-02 收口声明承重，确保 B 组代表「本体做对」而不是「实现债」；
3. AB-03 冻结问题与黄金答案；
4. AB-04 建契约与隔离；
5. AB-05/06 并行实现两组；
6. AB-07 评分器先跑通空结果；
7. AB-08/09 做变更实验；
8. AB-10/11 做审计与非研发测试；
9. AB-12/13 出报告并复核；
10. AB-14 按预注册规则决策。

### 6.2 实验退出标准

- [ ] 三个问题在两组上都通过正确性门禁；
- [ ] M1、M2 的文件数、代码行、耗时、重启、回滚全部有原始记录；
- [ ] M3 每条答案都有审计评分，证据 ID 全部可解析；
- [ ] M4 至少 2 名非研发完成测试，观察记录完整；
- [ ] 报告可由第三方复跑；
- [ ] 决策按预注册规则给出，未修改阈值；
- [ ] 若结论是「停止」，已给出迁移到属性图的步骤与资产保留清单。

---

## 7. 防作弊与风险控制

| 风险 | 控制手段 |
|---|---|
| A 组被做成稻草人 | A 组由认可属性图路线的人实现，独立评审；必须通过同一正确性门禁 |
| B 组享受特殊照顾 | B 组不得人工拼答案；不得读取 A 组代码；所有辅助代码计入 B 组成本 |
| 问题被挑过 | 问题在实现前冻结，业务 Owner 签字；演示问题标注为代理 |
| 黄金答案被改 | `golden.json` 记录 checksum；实验开始后只允许修 bug，不允许改期望值 |
| 计时不公平 | 同机、同进程隔离、冷启动口径一致、3 次取中位数 |
| 评分有偏见 | 输出去掉组标签；评分人不知道 A/B；复核人独立 |
| 数据泄漏 | A 组禁止 import `freight.*`；CI 静态扫描；两组使用同一份事实快照 |
| 只测顺利路径 | C1/C2 必须包含回滚；Q3 必须包含被阻断的分支 |
| 结论事后解释 | 第 2 节规则预注册；报告必须引用原始 JSON |

---

## 8. 报告模板

`results/report.md` 必须包含以下章节，顺序固定：

1. **结论摘要**：一句话结论 + 是否达到预注册的继续条件；
2. **实验设置**：版本、数据 checksum、问题来源、参与人；
3. **正确性门禁**：三个问题的实际输出与黄金答案对比；
4. **四指标对比表**：A/B 并排，附差值；
5. **变更成本明细**：C1/C2 的文件列表、代码行、耗时、重启、回滚；
6. **审计评分明细**：每条答案的五个字段；
7. **非研发测试记录**：完成率、介入次数、步数、错误、回滚；
8. **异常与反例**：失败样本、意外行为、对结论的影响；
9. **决策建议**：严格按第 2 节规则给出；
10. **附录**：原始 JSON 路径、脚本命令、环境信息。

报告里不允许出现：

- 没有原始数据支撑的结论；
- 用「演示效果好」替代四指标；
- 用「长期看会更好」解释当期没有赢；
- 把 A 组的实现缺陷当作属性图路线本身的缺陷。

---

## 9. 与现有系统的关系

本实验直接复用现有资产，不另起一套业务系统：

- 事实与场景：[freight/scenarios.py](../freight/scenarios.py)、[freight/engine.py](../freight/engine.py) 的 seed；
- 零代码变更证据：[scripts/load_bearing_demo.py](../scripts/load_bearing_demo.py)；
- 语义校验：[scripts/semantic_gate.py](../scripts/semantic_gate.py)；
- 验收回归：[scripts/freight_acceptance.py](../scripts/freight_acceptance.py)；
- 版本与发布：[freight/releases/](../freight/releases/)、[scripts/build_release.py](../scripts/build_release.py)；
- 动作与证据：`action_preflight`、`execute_action`、`evidence_chain`。

本实验明确不做：

- 不引入 OWL 推理机；
- 不引入新的图数据库；
- 不部署生产环境；
- 不比较 LLM 回答质量；
- 不为实验结果修改业务事实；
- 不把演示脚本当成真实业务问题。

---

## 10. 一句话

**这个实验的目的不是证明本体有价值，而是给「继续投入」设一个可以被证伪的门槛。** 如果 B 组在改规则、新增车型、审计、非研发发布这四件事上都不能明显赢，那它就不值得继续按平台投入；如果赢了，赢在哪里、赢多少，报告里必须有原始数据。