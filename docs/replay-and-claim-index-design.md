# 发布指标的可重放复算 + 数字索引（设计记录，未实施）

**借鉴来源**：ontosmith（Dongbumlee/ontosmith）`benchmarks/` 的 evidence-and-replay 机制。

**性质**：与 `assertion-provenance-design.md`（P2）同类的设计记录——**接真实数据源 /
出现对外发布指标时启用**，现在动代码是超前建设。不写进 `semantic_gate.py`。

---

## 一、两个机制

### 1. replay 复算（drift 即失败）

ontosmith 的 `examples/demo/replay_scores.py`：从 `benchmarks/appendix/` 的**原始证据
数据**（裁决样本、verdict 集、采样 manifest、raw census scores）重新计算每一个发布头条
数字，**漂移非零退出**（`exits non-zero on drift`）。

含义：任何一个发布数字（如"本体覆盖率 0.91"）都必须在仓库里留下**能重算它的原始数据 +
重算脚本**。改了数据、改了口径、改了脚本，只要对不上就报警——数字的可信度建立在"可重放"
而不是"有人说过"。

### 2. claim index（每个数字一行）

ontosmith `benchmarks/README.md` 的 Claim index：每个发布数字一行：

| Claim | Headline | Scope | Harness | Readout |
|---|---|---|---|---|
| corrected precision | 0.9110 | FULL-500, ruled contract | verification scoring + audit cores | whitepaper §5.2 |

四个字段钉死：**数字 → 口径（scope）→ 产出/校验它的工具（harness）→ 数字住在哪（readout）**。
"audit numbers are data, visible per version"——审计数字是数据，随版本可见、可复核。

---

## 二、与我方现状对照

| 我方已有 | 对应 | 缺口 |
|---|---|---|
| `scripts/semantic_gate.py`（32 项检查，全绿才放行） | ontosmith promotion gate 的检查腿 | 它查的是**本体合法**，不是**指标数字的可重放** |
| `docs/ontology-benchmark-evaluation-20261121.md`（五项业界评测 + `scripts/benchmark/run_all.py`） | ontosmith whitepaper + harness | 有"一键复现"雏形，但没有**每个数字的 claim 行**（scope/harness/readout 四元组） |
| 审计双规则（"衍生数字必须落到算式"、"对账必须逐键"） | replay 的哲学同源 | 规则是人记的，replay 是**脚本强制的** |

**结论**：我们已有"复现"的种子（`run_all.py`），但没有把"每个发布数字 → 可重算脚本"制度化。

---

## 三、启用清单（出现以下任一条件时落地）

1. **对外发布指标数字**（如"本体实体覆盖率 X%"、"fact_store 抽取准确率 Y%"）——
   此时必须同步落一份 claim index + 每个数字的 replay 脚本，否则数字就是不可复核的
   一次性陈述。
2. **fact_store 接真实数据源**（与 P2 断言溯源同触发点）——抽取准确率这类数字出现后，
   它的"机器裁决样本 + 原始 census"就是 replay 的输入数据。

落地形态（届时）：
- `docs/claim-index.md`：每行一个数字的 scope/harness/readout；
- `scripts/benchmark/replay_*.py`：从 `_out/` 或 `benchmarks/appendix/` 重算，漂移 `sys.exit(1)`；
- 接入 CI/门禁：发布数字改动必须 replay 全绿（与 `semantic_gate.py` 并列，不是替代）。

---

## 四、不搬的部分（诚实记录）

- ontosmith 的 whitepaper 长文形态（数字随 scope/method/failure anatomy 走长文档）——
  我们的规模不需要独立 whitepaper，`CHANGELOG.md` + claim index 足够。
- "机器裁决数字标注 machine-adjudicated"——等我们有机器裁决的对外数字时再引入这个标注约定。
