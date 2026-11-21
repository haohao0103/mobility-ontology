# A/B 价值实验 · 执行协议（骨架版）

> 正式计划见 `plan/ab-value-experiment-plan.md`。本目录是可执行骨架：
> 正确性门禁 + Q1-Q3 自动采样 + C1 变更卡 + M1/M3 自动指标已跑通；
> M2 待测试运单、M4 待真人（见 M4_TASK_CARD.md）。

## 运行

```bash
python -m experiments.ab_value.harness.run          # 终端报告
python -m experiments.ab_value.harness.run --json    # 机器可读
```

## 骨架实测（2026-11-21）

| 指标 | A 组（属性图+服务代码规则） | B 组（可执行本体） |
|---|---|---|
| 正确性门禁（Q1/Q2/Q3 黄金答案） | PASS | PASS |
| M1 变更成本（C1 免费等候 30→20） | 改 YAML 1 文件，**需重启生效** | 改声明 1 文件，**无需重启** |
| M3 可审计性（版本/规则ID/生效期/证据/checksum） | 0/7（答案无审计要素） | 3/7（带版本与规则ID） |

A 组的「金额上限」规则刻意保留第二处副本（`rules.yaml` 的 `amount_cap`
与 `app.py` 的 `_amount_ok()`），用于演示漂移风险——变更卡若只改其一，两侧
结论即打架；B 组无此问题（单一声明源）。

## 隔离测试

`tests/test_ab_isolation.py`：静态扫描 group_a 源码，出现
`import freight` / 读 `semantic_layer.json` 即失败（防止 A 组偷用本体能力）。

## 待办（正式实验前）

- [ ] M2：准备含「7.6米货车」的测试运单（见 fixtures/change_cards.json C2）
- [ ] M4：找非研发同事按 M4_TASK_CARD.md 执行并回填 results/m4_record.md
- [ ] A 组换独立实现者重写并通过独立评审（当前 A 组为本仓自建，存在稻草人风险）
