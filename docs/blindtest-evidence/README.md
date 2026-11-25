# 阶段 1 盲测证据（2026-11-25）

本目录存**阶段 1 盲测的原始报告**（结论的证据，不是产物）。上下文与判据见
`docs/AIGEN_ROLLOUT_PLAN.md` 的「阶段 1 实测结果」。

| 文件 | 内容 |
|---|---|
| `blindtest_capacity_20261125_201223.json` | 主报告：两阶段（A 类清单 / B 属性类型）的逐块明细、类召回、类型准确率、漏/多类清单、类型不符明细、判据判定 |
| `blindtest_diagnose_20261125_201223.json` | 诊断报告：语义对应率（名称 bigram + 属性集 Jaccard，阈值 0.25）、每个未精确匹配类的最佳对应与相似度 |

## 关键数字（口径见报告 `thresholds` 与 `note` 字段）

- 类召回率（**类名精确匹配**，判据口径）**25.5%** = 匹配 25 / 人工 98（LLM 产 113 类）→ 判据 ≥70%，**FAIL**
- 字段类型准确率 **68.0%**（分母 50 条共同属性）→ 判据 ≥85%，**FAIL**
- 属性召回 26.9%（分母 186 条人工属性）
- 语义对应率（**诊断口径，不改判据**）**46.9%**

## 复现

```bash
python3 tools/blindtest_capacity.py --max-tokens 8192        # 主盲测（12 次 LLM 调用）
python3 tools/blindtest_capacity_diagnose.py                 # 诊断（从 raw 重建）
```

- 需要 MiMo 直连可用（凭据在 `.env`，与 STG 同链路）。
- **LLM 原始输出不归档**（12 个文件约 100KB，属运行产物；脚本默认写到
  `tools/_out/blindtest_raw/`，该目录已 gitignore）。诊断脚本从 raw 重建，所以本地
  复跑后可直接重跑诊断。
- 单次生成预算 8192（生产 agent 默认 `OPENAI_CHAT_STEP_TOKENS=1600`，对建模场景不够；
  盲测单独放大并记录在报告 `max_tokens` 字段）。
