# A/B 价值实验 · 执行协议（骨架版）

> 正式计划见 `plan/ab-value-experiment-plan.md`。本目录是可执行骨架：
> 正确性门禁 + Q1-Q3 自动采样 + C1/C2 变更卡 + M1/M2/M3 自动指标已跑通；
> M4 待真人（见 M4_TASK_CARD.md）。

## 运行

```bash
python -m experiments.ab_value.harness.run          # 终端报告
python -m experiments.ab_value.harness.run --json    # 机器可读
```

## 骨架实测（2026-11-21，M1/M2/M3 全通）

| 指标 | A 组（属性图+服务代码规则） | B 组（可执行本体） |
|---|---|---|
| 正确性门禁（Q1/Q2/Q3 黄金答案） | PASS | PASS |
| M1 变更成本（C1 免费等候 30→20） | 改 YAML 1 文件，**需重启生效** | 改声明 1 文件，**无需重启** |
| M2 新增车型（C2 加 7.6米厢货 费率，测试运单 53 分钟） | 改 YAML 1 文件，**需重启**，应计 24.0 ✓ | 改声明 1 文件，**无需重启**，应计 24.0 ✓ |
| M3 可审计性（版本/规则ID/生效期/证据/checksum） | **0/15**（答案无任何审计要素） | **11/15**（带版本/规则ID/生效期/证据/checksum） |

M2 诚实口径：A 组的「按车型取参数」逻辑在服务代码里（YAML 只存参数表），
故加车型只需改 YAML 但**必须重启**；B 组车型语义由声明内建（`by_vehicle_type`
覆盖 + AST 求值），改声明即生效。两组改动面同为 1 个文件——差别在生效方式与
保护机制（B 侧声明受 schema 校验/承重用例保护，A 侧 YAML 无任何校验）。

A 组的「金额上限」规则刻意保留第二处副本（`rules.yaml` 的 `amount_cap`
与 `app.py` 的 `_amount_ok()`），用于演示漂移风险——变更卡若只改其一，两侧
结论即打架；B 组无此问题（单一声明源）。

## 隔离测试

`tests/test_ab_isolation.py`：静态扫描 group_a 源码，出现
`import freight` / 读本体声明文件即失败（防止 A 组偷用本体能力）。

## 待办（正式实验前）

- [ ] M4：找非研发同事按 M4_TASK_CARD.md 执行并回填 results/m4_record.md
- [ ] **A 组换独立实现者重写并通过独立评审**——当前 A 组为本仓自建，
      稻草人风险仅被隔离测试+金标门禁缓解，未被消除；正式结论以此 precondition 为准
