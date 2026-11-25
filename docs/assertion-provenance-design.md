# 断言级溯源设计（Assertion-level Provenance Design）

> 状态：**设计记录，未实施**。当前 fact_store 是演示替身（`seed.py` 硬编码种子
> 数据，单一来源），本设计在**接入真实数据源（TMS 接口 / 文档附件混合源）时
> 启用**。现在动代码是超前建设——本文把启用时的关键决策提前钉死，避免届时
> 按错的表结构起步。
>
> 来源：ontogeny（apache/hugegraph-ai）`server/ontogeny/documents/` 审查队列
> 的五表设计，经 2026-11 三项目调研评估确认可移植（评估结论：ontogeny 的
> 断言级 provenance 是"演示替身走向真实数据源"的现成蓝图）。

## 1. 动机：多源并存时的精确撤回

单一演示来源下，"事实从哪来"不是问题（都来自 seed）。真实环境里事实会来自
多个源：

- TMS 结构化接口（运单 / 结算 / 发票行）；
- 文档附件（合同条款、异常处理协议）抽取；
- 人工登记（对账差异备注）。

一旦某份文档被认定作废/修正，正确语义是：**精确撤回"该文档断言过的内容"，
不能碰其他来源断言的同一批对象**。现状的 evidence 表（sha256 checksum +
version）能证明"证据没被改"，但没有"删除时撤回谁"的语义——按对象删会误伤
TMS 断言，不删则作废文档继续支撑结论。

## 2. 核心机制（自 ontogeny documents plane 移植）

| 机制 | ontogeny 做法 | 移植要点 |
|---|---|---|
| **断言行** | `ExtractAssertionRow`：每条抽取字段一行（kind=object/link），带 provenance | fact_store 从"行=对象"细化为"行=对象×来源断言"；同对象多源共存 |
| **删除语义** | 删文档时按 assertion 行精确撤回该文档断言的内容 | 撤回=删断言行，不是删对象行 |
| **来源起源** | `ExtractOriginRow`：永久记录"对象由抽取创建" | 防误归档：**先于文档存在的对象**（人工建的）永不被语料删除牵连 |
| **签名去重** | 候选按内容 signature 去重，rerun 刷新同一 pending 行 | 审查队列不被重跑灌爆 |
| **溯源三元组** | 每次抽取 run 记 `schema_hash + prompt_hash + model` | 同一文档同一提示词同一模型的结果可重放（run 存原始抽取子图） |
| **原始输出不可变** | attempt 的 raw_graph 永不修改；人工修正存 reviewed_graph + EditRow（actor/before/after） | LLM 原始输出与人工修正分离留痕——与结论硬引用溯源（4.0.1）同构哲学 |
| **协议边界** | 核心不 import 抽取器实现，extension 注册 factory，跨边界全 JSON | 抽取器今天进程内明天换子进程，引擎零改动 |

## 3. 映射到 fact_store 现有结构

| 现有 | 保留 | 启用时新增 |
|---|---|---|
| `objects` / `relations` | 不动 | 各加 `origin` 列（'seed' / 'tms-sync' / 'extract:<doc_sha>' / 'manual'） |
| `evidence` | 不动（checksum/继续用于"证据未篡改"） | 新表 `assertions`（对象×字段×来源断言行）、`extract_runs`（三元组+原始子图）、`extract_origins`（创建起源）、`review_candidates`（signature 去重的审查队列） |
| `events` / `audit` | 不动 | audit 追加"撤回事件"类型（撤回也是可审计动作） |

## 4. 启用清单（届时按此实施）

1. `freight/fact_store.py` 加四张表 + 迁移（既有库无新表时自动建，不破坏演示数据）；
2. 事实写入路径分叉：结构化同步走 tms-sync 直写；文档抽取走
   `review_candidates`（pending → accepted 才入库 / rejected 留档含理由）；
3. 撤回 API：按 `extract:<doc_sha>` 前缀删 assertions 行，先查
   `extract_origins` 决定对象行是否归档（非抽取创建的对象保留）；
4. agent 观察层读断言时携带来源标签，最终事实卡展示"该字段由谁断言"；
5. 每次抽取 run 落 run 行（三元组+原始子图 JSON），review 界面可 diff
   重放差异（模型/提示词升级时的回归对照）。

## 5. 与既有机制的边界

- **发布 pin（payload_sha256）**管 TBox（本体发布物）；断言溯源管 ABox
  （事实写入）——两层互不越界，body 本体变更永远走 build 重出新 pin。
- **结论硬引用溯源（4.0.1）**校验"结论引用的 ID 出现在观察语料"；断言级
  溯源回答"语料里的断言本身从哪来、能不能撤"——前向校验与后向溯源互补。
- **审计双规则**：撤回按断言清单逐键执行，禁止以计数吻合替代（撤回 N 条
  断言 ≠ 校验过 N 条内容）；对账必须落到清单。
