# 可执行本体执行基准（ontology-execution）

> **一句话**：同一份数据、同一组问题，**可执行本体**与**属性图 + 服务代码规则**两条路线各答一遍，
> 用**客观可判定**的规则评分，并测量三条指标：变更成本、可审计性、一致性风险。
>
> 题目说明：目前**没有**面向"可执行本体"的公开基准（Palantir 闭源；学术界做的是本体学习，
> 不是本体执行）。这是我们自己开的一个坑，欢迎第三方实现来跑。

## 1. 三十秒跑起来

```bash
cd benchmarks/ontology-execution
python runner.py                 # 跑内置两个参考实现
python runner.py --json          # 机器可读
python runner.py --check-baseline   # 与基线比对（CI 用，有差异则非 0 退出）
```

只依赖 Python 标准库（≥3.10），**无需安装任何东西**，**无需联网**。

## 2. 现在跑出来的结果

| 指标 | 本体路线 | 属性图路线 |
|---|---|---|
| 用例通过 | **6 / 6** | 5 / 6 |
| M1 变更成本 | 改 3 个文件，**需重启 0 次** | 改 3 个文件，**需重启 3 次** |
| M2 可审计性（答案携带的审计要素） | **5 / 5** | **0 / 5** |
| M3 一致性风险（同一语义被写几遍） | **0 份副本** | **3 份副本** |

失败的唯一用例是 **C3 漂移探针**，而且**它失败正是基准要证明的事**：

> 属性图路线把"减免上限口径"同时写在**代码常量**与**配置文件**里。
> 只改配置、不改代码 → 结论仍是旧口径 → 与期望不符 → **漂移被客观检出**。
> 本体路线只有一处声明 → 改完立即生效且一致。

## 3. 用例一览

| 用例 | 问什么 | 判定 |
|---|---|---|
| Q1 | 这单为什么差 38 元？等候费应计多少、超收多少？ | 报价 480 / 实收 518 / 差 38 / 应计 12 / 超收 13 |
| Q2 | 等候费按规则应收多少？ | 应计 12 / 超收 13 |
| Q3 | 减免 40 元会被哪一步挡住？ | `blocked=true`，码 `AMOUNT_EXCEEDS_ACTUAL` |
| C1 | 变更：4.2 米厢货免费等候 30→20 | 应计 16.8 / 超收 8.2 |
| C2 | 变更：新增 7.6 米厢货费率 | 该车型运单应计 15.0 / 超收 10.0 |
| C3 | 漂移探针：减免上限口径 actual→none | 减免 40 元**不再**被挡（`blocked=false`） |

**判定只看客观值**（数值+容差 / 布尔 / 码值），**不看实现的解释文字**。

## 4. 接入你自己的实现

实现需提供这六个方法（详见 [PROTOCOL.md](PROTOCOL.md)）：

```python
class MyImpl:
    def q1_diff(self) -> dict          # 差异归因
    def q2_waiting(self, waybill_id=...) -> dict
    def q3_roadblock(self, amount=..., waybill_id=...) -> dict
    def apply_change(self, card) -> dict  # 应用变更卡
```

然后：

```bash
python runner.py --impl /path/to/your_impl.py
```

评测器会为**每个用例**准备独立工作区（数据副本 + 你的实现目录副本），
所以你的实现可以放心改自己的文件，不会污染下一次运行。

## 5. 目录

```
benchmarks/ontology-execution/
├── README.md              本文件
├── PROTOCOL.md            接入协议：接口契约、公平性铁律、判定口径、效度前提
├── manifest.json          用例清单与期望结果（机器可读）
├── baseline.json          当前基线（CI 比对用）
├── runner.py              评测器
├── data/                  中立自包含数据集（ontology / facts / change_cards）
├── implementations/       两个参考实现（本体路线 / 属性图路线）
└── results/               每次运行的原始结果
```

## 6. 两条路线的差别在哪（读实现就懂）

- `implementations/ontology_ref/impl.py`：**规则只在声明里**（`data/ontology.json`），
  代码只负责解释声明——一个 40 行的 AST 白名单公式求值器 + 状态机判定 + 审计要素组装。
  改声明即生效，不需要改代码、不需要重启。
- `implementations/property_graph_ref/impl.py`：**图数据 + 服务代码里的规则**，
  费率参数同时存在于代码常量与 `rules.json`（真实项目里极常见的形态）。
  改配置需要重启；答案不带版本/规则ID/生效期/证据 checksum。

## 7. 效度前提（必须一起读）

**本仓库提供的两个实现由本仓库作者编写，因此存在『稻草人』风险。**
正式结论必须满足：属性图路线的实现由**独立第三方**重写并通过独立评审。
详见 [PROTOCOL.md](PROTOCOL.md) §6。

## 8. 引用

```bibtex
@misc{ontology-execution-benchmark,
  title  = {ontology-execution: an execution benchmark for operational ontologies},
  note   = {v0.1.0；同题同数据对比可执行本体与属性图路线},
  year   = {2026}
}
```
