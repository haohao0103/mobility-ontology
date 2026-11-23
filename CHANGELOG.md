# Changelog

## 3.6.0 (2026-11-23) — 计价决策表（pricing matrix 唯一参数源）+ 多维计价实验室

真实计价是「车型 × 时段 × 天气」多维组合，单公式+单维覆盖表达不了；而参数过去
存在三处（实例副本、`by_vehicle_type`、顶层标量），改一处漏一处就会漂移。这一版把
定价参数收敛成**本体声明的一张决策表**，并让演示能当场翻转维度看命中行变化。

### 新增
- **决策表 `pricing.<fee_type>.matrix`**（`freight/semantic_layer.json`）：行 =
  条件组合（`conditions`）+ 参数（`params`），**首行命中**，`conditions` 为空的行是
  默认行（必须放最后）。`dimensions` 声明计价维度，`time_windows` 声明时段
  （peak/night/normal，按 `accepted_at` 归窗）。`waiting` 新增大货车·雨天行
  （`per_block` 4.0 → 4.8，恶劣天气等候加价），排在单车种行之前。
- **多维计价实验室**（`freight/demo_labs.py::pricing_lab`，API `POST /pricing/lab`）：
  只读求值零写库，展示当前维度组合命中的行与参数，翻转一个维度（晴/雨、跨车型）
  应计当场变——大货车 53 分钟：晴天 8.0 元 → 雨天 9.6 元。
- 门禁新增 `check_pricing_matrix`：决策表行序校验——默认行不在最后、
  或更具体的行（条件超集）排在更通用的行之后，一律拦下。这类 bug 运行时静默
  （首行命中先挡住具体行），只有行序校验能发现。
- TBox 图谱（`web/tbox-graph.js`）pricing 节点展示维度、行数与「首行命中」说明。

### 修正（承认并清理）
- **参数三处存放 = 双口径**：定价规则实例曾抄一份参数副本（`PR-WAIT-01` 等 7 条），
  seed 已改为只存版本元信息；`by_vehicle_type` 与顶层重复标量随迁移删除
  （`scripts/_migrate_pricing_matrix.py`，一次性）。**教训：清副本不能用固定
  键名名单**——`per_km`/`base_fare_by_vehicle_type`/`source` 同样是参数，
  首版迁移漏删这三个，已在矩阵默认行之外留下第二口径，按「默认行携带的全部
  非结构键」规则清掉。
- **「实例没有 params」误报**：`_validate_pricing_rule` 还按旧口径要求实例携带
  参数副本，迁移后主运单一次诊断报 8 条 `PRICING_RULE_PARAMS_MISSING`（假警报）。
  改为校验真正的参数源：语义层 `pricing.<fee_type>` 解不出参数（无 matrix 且无
  默认标量）才报——删掉 matrix 会立刻现形。
- **车型费率对照表清空**：`_waiting_tariffs` 读 `by_vehicle_type`，迁移后返回 0 行。
  改为从 matrix 行条件收集车型，走与真实计价同一个 `_fee_context` 首行命中求值——
  对照表不再可能和计价漂移。

### 兼容（历史回放不漂移）
- 无 matrix 的历史发布快照（如 `releases/1.3.1-demo`）按旧口径回放：默认标量 +
  `by_vehicle_type` 覆盖，逐值一致——1.3.1 的 4.2米厢货 `per_block=3.0` 与当前
  工作副本的 2.4 不同，回放必须用当时值。

### 补齐（存量演示库收敛到决策表口径）
诊断推理链里看不到变化的三处遗留，本轮全部收口：
- **规则说明还是旧口径**：`R-WAIT-01.judge.explain` 只描述「按车型取费率」，
  推理链第 3 步（tbox.describe）直接展示它——已改写为决策表口径
  （车型 × 天气首行命中、truck-rain 行、公式不变），版本 2.1.0 → 2.2.0。
- **天气样本落不进存量库**：`seed()` 只在空库上跑，11-21 建的演示库拿不到
  迁移后新增的天气维度。新增 `scenarios.ensure_pricing_samples`（幂等，每次带
  场景启动都收敛）：主运单显式 `weather=normal`；WB20261106002 补
  `weather=rain`/等候 53 分钟 + 等候费 F101（实收 8.00）+ 证据 E101；
  V003 车型对齐当前 seed（大货车）。
- **★实例参数副本覆盖决策表（最隐蔽）**：存量库 7 个 PricingRule 实例还带着
  参数副本（`by_vehicle_type` + 30/5/2.4），而 `_fee_context` 的显式参数通道
  **优先级高于 matrix**——大货车+雨天样本被盖回 12.00（4.2米厢货行），
  天气维度白补。ensure 现在清空全部实例副本（新 seed 已不写），诊断恢复
  **应计 9.60**（truck-rain 行：40 分钟免费、每 10 分钟 4.8 元）。
  教训：迁移只改声明不够，**存量数据里的副本同样是第二口径**，且它的优先级
  比新口径高——不清就会静默盖掉矩阵。

### 全面复查（同型问题全仓扫荡）
死键 / 第二口径 / 假承诺三类，逐一定位并清除：
- **计算不再供实例参数**：`fee_analysis` 曾把 PricingRule 实例 params 喂进
  `compute_fee`，优先级高于 matrix——上面那起事故的传导链。现在计算只认声明
  （`params={}`），实例仅供版本元信息校验（rule_id / 生效期 / fee_type）。
  历史回放同理改由旧快照自己的 legacy 解析供数：喂实例参数反而会让**今天的
  副本覆盖历史标准**。
- **legacy 回退键收全**：`_resolve_tariff` 旧声明分支原先只收
  free/block/per 三个键，`per_km`/`per_order`/`base_fare_by_vehicle_type`
  会被静默丢弃——无 matrix 的新费用类型（如验收注入的 remote_area）靠它供数。
  已改为收全部非结构顶层键。
- **四处「改本体参数」演示/测试改的是死键**：`parity_lab` Q6、漂移实验、
  `prevention_demo`、`test_prevention` 都写 `pricing.waiting.free_minutes=20`
  再靠显式参数通道算——层内改动空转，翻转来自参数通道，「只改一处 JSON」
  的叙事是假的。已全部改为**改 matrix 命中行 + 不传显式参数**，翻转真正
  来自声明（漂移实验现状：本体侧 16.8/16.8 同源，属性图侧 16.8/12.0 漂移）。
- **验收场景 2 原测的是被删掉的口径**：给规则实例打 params 副本期望金额
  跟着变——正是第二口径。已改为改决策表行参数，断言随声明变（12/12 保持）。
- **发布工具死键拦截**：`build_release.py` 的 `--set` 示例还在教人写
  `pricing.waiting.free_minutes=20`（死键，不报错不生效）。新增
  `dead_param_keys` 检查直接拒发，示例改为 matrix 行路径。
- **门禁死键判据**：`check_pricing_matrix` 增加 `MATRIX_DEAD_PARAM_KEY`——
  有 matrix 还把参数写在顶层即拦（附负向单测）。
- **维度承诺对齐**：`dimensions` 原先给所有费用类型都写 `vehicle_type`，
  多数只有默认行、没有任何行按车型分支——假承诺。改为从行条件实际字段推导
  （waiting: `[vehicle_type, weather]`，其余: `[]`）；时段归窗机制在位但
  当前无行按时段分支，规则说明明说「预留维度」，不虚报。
- **highway 来源回退**：Agent 目录里「来源 ETC」读顶层 `source`（已迁进
  matrix 默认行），原代码靠硬编码默认值巧合蒙对；改为从默认行取。

### 验证
- 零漂移：主运单 `fee_analysis` 与迁移前**逐键一致**（应计 12.00 / 实收 25.00 /
  差异 13.00，findings 仅 `WAITING_FEE_MISMATCH`；车型对照表 5 行数值不变，
  行序随决策表优先级）。
- 新增 `tests/test_pricing_matrix.py` 27 断言：零漂移 / 首行命中（truck-rain 压过
  单车种行）/ 默认行兜底 / 时段归窗（工作日早高峰、周末、夜间、坏时间戳）/
  旧版回放 / pricing_lab 翻转与零写库 / 门禁行序与死键负向用例 /
  参数源被删现形 / **存量副本不再覆盖矩阵 + ensure 清理 + 幂等**。
- 全量 258 passed；门禁 31 项全绿；`freight_acceptance` 12/12；
  迁移脚本从 HEAD 原件**完整复现**当前产物（含 weather 属性、规则说明改写、
  维度推导）。

## 3.5.0 (2026-11-21) — 缺口检测门禁（第 28 项）+ LLM 退避重试 + 本地部署复测

上一版把来源契约变成门禁（第 27 项），这一版补齐**缺口检测能力**的验证，并把
上轮观察到的推理链偶发 503 治掉。

### 新增
- **缺口探针数据**（`freight/scenarios.py::build_gap_cases`）：人为构造四类来源缺口——
  无人认领 / 口径越界 / 数据过期 / 证据缺失——外加一条「正常被 ETC 认领的 highway」
  作为归属声明的承重探针。**与主诊断剧本严格隔离**：正常运单不出现这四类告警。
- 门禁**第 28 项** `check_gap_detection`：断言 ① 四类缺口报得出来；
  ② 正常诊断数据上不误报；③ **数据修好后告警消失**（补证据 / 改成合法认领）——
  第三条是关键：它证明告警是数据驱动的，不是恒真断言。
- 承重实验 +2 条（L22 去掉 `require_evidence` → 缺证据告警消失；
  L23 去掉 ETC 的 highway 认领 → 该费用变成无人认领），共 **24 条**。

### 修正（承认并清理）
- **门禁第 28 项的第一版负向断言是空转的**：finding 里的 `fee_id` 是**短 id**
  （不带 `fee:` 前缀），断言用了全 id → 永远匹配不上 → 「数据修好告警消失」这条
  实际从未生效。是补单测时发现的。已统一口径，并按**费用粒度**比较
  （别的费用可能报同一个码，只看集合会把「这条修好了」误判成「没修好」）。
- **探针运单缺三样东西导致规则求值直接崩**：车型关系（计价规则按车型选费率）、
  定价规则实例绑定（`pricing_rule_id` + `fee_priced_by_rule` 关系）、
  规则字段（`waiting_minutes` 等）。缺任何一样都会抛「AST 字段不存在」，
  而不是产出告警——**这正是探针的价值：它逼着规则求值链路把前置条件写清楚**。
- **LLM 网关无退避重试**：本地部署实测推理链偶发 503（网关瞬时故障），重跑即恢复。
  `freight/agent.py` 已加指数退避重试：只对 429/5xx/网络抖动/空响应重试，
  **认证失败（401/403）与参数错误（400）立即抛出**（重试没有意义，还会浪费额度）；
  thinking 模式默认少给一次重试，避免长推理总时长翻倍。环境变量：
  `OPENAI_CHAT_RETRY_ATTEMPTS` / `_RETRY_BASE` / `_RETRY_MAX`。

### 部署环境（本地）
- STG `stg-121` 仍不可达（DNS 解析失败），本轮继续按用户授权走**本地部署测试**。
- **本地部署复测**（`cli.py serve --port 8765`，全新库）：health 200（500 类/503 链接/
  16 规则/15 动作）、state-machines 8 机器 15 迁移边、state-history 200
  （ACCEPTED → LOADED → DELIVERED）、fee-analysis 200（三口径一致、来源覆盖 7/7
  已解析 0 未解析）、**动作写回** POST /freight/actions/dispute_charge → 409
  （状态前置不满足，正确拒绝重复申诉）且审计 22→23 条、mcp/tools 16 个。
- 顺手清掉一个**端口残留**：上一轮停服务后 8765 仍有旧进程在监听，导致本轮复测
  打到了旧服务（旧库、旧代码、剧本数据缺失），一度误判「剧本没生效」。已定位并清理。

### 验证
- `pytest` **298 passed**（新增 `tests/test_llm_retry.py` 6 项、
  `tests/test_gap_detection.py` 5 项）
- 语义门禁 **28 项 / 17 覆盖 / ok=true**
- 承重实验 **24/24**；freight 验收 12/12；smoke **7/7**；`verify.py` 36/36


## 3.4.0 (2026-11-21) — 来源契约收口（门禁第 27 项）+ 本地部署测试

上一版把运单-费用链做深（深水区 18 类全覆盖），这一版把**来源契约**变成门禁，
并按用户授权完成**本地部署测试**（STG `stg-121` 本轮不可达）。

### 新增
- `freight/source_coverage.py`：来源契约判据内核
  - G1 **归属矩阵**：`pricing` 段的每个 `fee_type` 必须**恰好**被一个
    `data_sources[].fee_types` 认领 —— 漏认领报 `FEE_TYPE_NO_OWNER`，
    两个来源认领报 `FEE_TYPE_MULTI_OWNER`，认领了定价里没有的类型报 `FEE_TYPE_NOT_IN_PRICING`；
  - G2 **费用类型覆盖（V5）**：每个 `fee_type` 在 FeeItem / PricingRule / QuoteItem
    **三方各有 ≥1 实例**，缺一方报 `FEE_TYPE_NO_INSTANCE` / `_NO_RULE` / `_NO_QUOTE_ITEM`。
- 门禁**第 27 项** `check_source_contracts`（含 4 条负向用例：删认领 / 双认领 /
  删报价明细 / 篡改报价明细金额），覆盖目标 15 → **16**。
- `freight/scenarios.py` 之外，服务启动即带剧本：`FreightSystem(with_scenario=True)`，
  `freight/api.py` 默认开启 —— **跑起来的服务本身就是深水区 100% 覆盖的那份数据**，
  与门禁第 26 项度量的是同一批对象。
- 承重实验 +2 条（L20 删认领 → `FEE_TYPE_NO_OWNER`；L21 双认领 → `FEE_TYPE_MULTI_OWNER`），
  共 **22 条**。

### 部署环境（本轮实况）
- **STG `stg-121` 不可达**：实测 `ssh stg-121` 报 `Could not resolve hostname stg-121`，
  仓库与 `/etc/hosts` 均无该主机地址。`AGENTS.md` 已记录本轮**用户授权改用本地部署测试**
  （仅本轮有效；任何情况下不得部署 PRD）。
- **本地部署测试结果**（服务 `cli.py serve --port 8765`）：
  | 接口 | 结果 |
  |---|---|
  | `/freight/health` | 200｜500 类 / 503 链接 / 16 规则 / 15 动作 |
  | `/freight/ontology/state-machines` | 200｜8 个状态机，15 条迁移边 |
  | `/freight/objects/waybill:SCN01/state-history` | 200｜轨迹 ACCEPTED → LOADED → DELIVERED |
  | `/freight/waybills/waybill:WB20261105001/fee-analysis` | 200｜报价 480 / 实收 518 / 差异 38；**报价三口径一致**（明细合计 480 = 报价单总额 480 = 运单快照 480）；来源覆盖：连接器 2 / 费用 7 / 已解析 7 / 未解析 0 |
  | `/freight/mcp/tools` | 200｜16 个工具（含新增 `ontology.state_machine`） |
  | `/freight/mcp/call` `ontology.state_machine` | 200｜FeeItem 状态机 + 迁移边 |
  | `/freight/objects/waybill:SCN01/graph?depth=2` | 200｜13 个节点 |
  | reasoning-chain（MiMo 直连） | 12 步 finished｜三方分工标注完整｜结论由 `mimo-v2.6-pro` 生成 |
- **已知抖动**：推理链偶发 503（网关调用超时），同一服务重跑即恢复；`agent.py` 目前**没有退避重试**，
  建议后续补（本次未改，避免在无 STG 环境里改动网关调用策略）。

### 验证
- `pytest` **287 passed**（新增 `tests/test_source_contracts.py` 11 项）
- 语义门禁 **27 项 / 16 覆盖 / ok=true**
- 承重实验 **22/22**；freight 验收 12/12；smoke **7/7**；`verify.py` 36/36


## 3.3.0 (2026-11-21) — 运单-费用链做深做全 + ABox 深度门禁（第 26 项）

上一版把「本体能表达」做完了，这一版做的是另一半：**证明本体是真的**。
深水区（声明了 data_sources / 迁移动作 / 状态机的 18 个对象类型）从
**类覆盖 83%、动作实测 3/15**，做到 **类 18/18、非孤岛 18/18、动作 15/15、
迁移边一致 21/21、规则分支 16/16**，并固化成**门禁第 26 项**（不达标即 FAIL）。

### 修正（承认并清理）
- **5 个动作只有声明、没有运行时实现**：`arrive_stop` / `depart_stop` / `issue_quote` /
  `supersede_quote` / `confirm_subcontract` 执行时抛 `NOT_IMPLEMENTED`。
  这是"声明与执行脱钩"的残余——和之前属性注册表"只登记不执行"同一类病。
  已补齐 5 个实现，并各带一条业务前置校验（未到达不能离开、过期报价不能签发、
  改版目标必须存在、分包金额不得超运单收入）。
- **审计 result 格式分叉**：种子数据手写审计用 `{"status": ...}`，
  `execute_action` 路径用 `{"before_status","after_status"}`——同一张表两种写法，
  导致"迁移与声明一致性"这类度量读不到状态、产生假不一致。已统一为后者。
- **`smoke_no_llm` 的「无 key 场景」根本没测到**：它只 pop 了环境变量，
  而 `LLMClient._load_env_file` 会把仓库 `.env` 里的 key 又塞回来（本地有 .env
  时该 smoke 反而红）。已屏蔽 `.env` 注入，现在两种环境都真测无 key。
- **边覆盖判据设计错误**：原先要求"每个类都有出边"，把货物/司机/车辆/轨迹/证据/
  计价规则这些**业务上的叶子类型**判成缺陷。已改为「不是孤岛」，并只对
  **有迁移动作的类**额外要求双向有边。

### 新增
- `freight/scenarios.py`：**核心业务剧本**——一单多段运输（船东→运单 2 站点→
  分包给承运商→报价 3 版→费用→争议→减免→结算挂起/冲正→发票红冲→工单关闭），
  **真实执行 15 个迁移动作**，留下状态迁移、事件与审计。它是覆盖度度量的数据源，
  也是"这条链到底跑不跑得起来"的活体验证。**边界**：剧本数据不等于生产实况，
  它证明「每个类型有实例、被引用、被执行过」，不证明「货拉拉真有这些对象」。
- `freight/abox.py`：ABox 深度校验内核（V1 实例 / V2 非孤岛 / V3 动作实测 /
  V4 迁移一致 / V6 规则分支）。
- 本体顶层 `abox_targets`（`schema/semantic_layer.schema.json` 放行）：深水区清单
  在本体里，不写死在代码里；改一处判一处。
- 门禁**第 26 项** `check_abox_coverage`（4 条负向用例：删实例 / 删关系 /
  删审计 / 篡改审计结果），覆盖目标 14 → **15**。
- 承重实验 +2 条（L18 清单里加无实例类 → 门禁 FAIL；L19 清单去掉一类 → 判定分母跟随），
  共 **20 条**。

### 决策落地
- **D1：LLM 走 MiMo 直连**——`AGENTS.md` 改为 `https://api.xiaomimimo.com/v1` +
  `mimo-v2.6-pro`（STG 网关降为备用）；STG 出网需放行该域名。
- **D2：不接 ETC 等新外部系统**——ETC 无公开 API，接模拟网关只能证明机制、
  接内网不可控。改为把**已有来源的口径归属与缺口处理**做扎实（Phase C）。

### 实测（2026-11-21）
```
深水区 18 类：实例覆盖 18/18 · 非孤岛 18/18 · 动作实测 15/15
迁移边一致性 21/21 · 规则分支 16/16 · 审计 22 条 · 事件 26 条
全量 500 类：18 类有实例（3.6%）—— 门槛只约束深水区，不逼全量
查询面 9 类形态各有断言（过滤/多条件/排序分页/聚合/search-around/多跳/SQL/证据链/对象图）
```

### 验证
- `pytest` **276 passed**（新增 `tests/test_abox_coverage.py` 18 项、
  `tests/test_query_surface.py` 10 项）
- 语义门禁 **26 项 / 15 覆盖 / ok=true**
- 承重实验 **20/20**；freight 验收 12/12；smoke **7/7**；`verify.py` 36/36


## 3.2.0 (2026-11-20) — 状态机升为独立层 + 动作参数类型化 + 对齐矩阵纠偏

上一版把六项 Palantir 缺口闭合了，但**对齐基准矩阵（`roadmap/palantir-benchmark.md`）
停在改造之前**，其中 4 条与代码不符——包括一条方向性错误（P-07 写「无 apply」，
而引擎里 apply 一直在）。本轮先纠基准，再补能力。

### 修正（承认并清理）
- **对齐矩阵落后于代码**：`roadmap/palantir-benchmark.md` 标 Draft、停在 2026-11-19，
  P-06/P-07/P-08/P-11 四条与实现不符。已按 v3.2 实测逐条重写 13 项现状，
  状态从 Draft 改为「v3.2 实测基线」，并补上此前没人记的两个缺口：
  **类级生命周期 status**、**标题属性（title property）**。
- **P-07 方向性错误**：矩阵写「当前无 apply、无 edits、无副作用」，实际
  `engine.execute_action/_apply_action` 已实现前置校验、属性写回、事件、审计、
  **幂等键 replay**。矩阵会骗人——比代码 bug 更贵，因为它决定所有后续排期。
- **`datetime` / `timestamp` 口径分叉**：属性值类型用 `datetime`（200 个属性），
  函数参数类型用 `timestamp`，两个名字指同一件事——谁没被枚举覆盖到就悄悄不校验。
  现在 `SemanticLayer._type_ok` 同时认两个名字并按 ISO 校验；
  `function_registry._scalar_ok` 改为**委托**同一实现（原来是自己抄的一份）。

### 新增（P2-4 状态机独立层）
- `freight/semantic_layer.json` 顶层 **`state_machines`** 声明（`initial` / `terminal` /
  `external_states`，8 个对象类型）。**迁移边真源仍在动作的 `from_status→to_status`**——
  不把流转复制到第二处，避免二源真相；声明只补动作说不清的三件事。
- `SemanticLayer.state_machine()`：机器图（状态集 / 起点 / 终态 / 外部状态 /
  迁移边（稳定 id `transition:<Type>:<action>`）/ 可达集 / 来源不明 / 归宿不明）。
- `SemanticLayer.validate_state_machines()`：六类完备性判据——来源不明、归宿不明、
  终态仍有迁出边、迁移边引用非法状态、有动作却未声明、无动作却声明。
- 门禁**第 25 项** `check_state_machines`（含 4 条负向用例），覆盖目标 13 → **14**。
- 运行时暴露：REST `GET /ontology/state-machines`、`/ontology/state-machines/{type}`、
  `/objects/{id}/state-history`；MCP 原语 **`ontology.state_machine`**（本体 JSON 声明，
  代码零改动即生效）。
- 对象状态轨迹 `state_history`：只取「状态变更」事件（事件类型 ∈ 该类状态集）——
  events 表里混着证据与并发测试事件，不按状态集过滤就会把不相干事件当成状态变化。

### 新增（P2-5 动作参数类型化）
- `engine.execute_action`：参数校验从「只查必填」升级为 **查必填 + 查类型 + 拒未声明参数**
  （`PARAM_UNKNOWN`）。类型口径复用 `SemanticLayer._type_ok`——动作与函数、动作与属性
  三处不再各判各的。
- 承重实验新增 2 条（L16 状态机声明改动 → 门禁 FAIL；L17 参数类型改动 → 同调用被拒），
  两条都只改 JSON、代码零改动。

### 验证
- `pytest` **239 passed**（新增 `tests/test_state_machine.py` 20 项）
- 语义门禁 **25 项 / 14 覆盖目标 / ok=true**
- 承重实验 **18/18**（改回原样后门禁 exit=0）
- `scripts/freight_acceptance.py` 12/12；7 个 smoke 全过；`verify.py` 36/36


## 3.1.0 (2026-11-20) — 六项 Palantir 对齐缺口闭合（契约层 / 展示层 / 运行时三件套）

上一版把属性升成了一等公民，但只做到「登记」。本轮把**声明与执行**接起来，
并补上展示层、物化层、标记传播与破坏性变更审查。六项缺口逐条对应实测证据。

### 修正（承认并清理）
- **扩展层此前完全没有 JSON Schema**：往 `ontology_extension.json` 塞
  `totally_bogus_top_key` / `bogus_field_xyz` / `bogus_param_key` /
  `value_type="not_a_real_type"` 全部加载成功。扩展层是追齐层的第二份本体，
  没有契约就等于垃圾键静默入库。
- **属性注册表只登记不执行**：`validate_object` 只校验属性名白名单 + 状态机，
  `estimated_fee="abc"`（decimal）能直接写进去。注册表在门禁层是权威、在运行层是装饰。
- **扩展层与基础注册表类型分叉 4 处**：`estimated_fee`/`actual_fee`/`variance`
  被写成 `integer` + `unit=cents`，`status` 被写成 `string`（基础层是 `decimal/CNY`、`enum`）。
  扩展层覆盖后，金额实际按「整数分」解释。
- **函数参数类型是自由字符串且连枚举都不校验**：`validate_signature` 只查
  必填/未知参数，`fee_breakdown(waybill_id=12345)` 一路通过。
- **OSDK 生成器没接基础属性注册表**：1950+ 个属性被判「未注册」并退化成
  `str | None`——「强类型客户端」其实是弱类型，且看不出来。
- **`smoke_concurrency` 长期红**（HEAD 上 6 个错误）：`_read_conn` 在写事务进行中
  把**写连接**借给其他读线程，触发 SQLite 连接不可跨线程共享的硬约束。改为
  只有写事务的发起线程才回落到写连接。
- 种子数据两处类型脏值：`actual_duration_min`/`duration_min` 写 `218.0`（integer 字段）；
  `closed_at` 用 `""` 表示「未关闭」（datetime 字段）。改为 `218` / `None`。
- **`RID_LEDGER_DRIFT` 是死检查**（承重实验 L9 抓出）：`ledger_key = f"{kind[:-1]}:{oid}"`
  对 `classes` 求前缀得到 `"classe"`，台账里是 `"class:Waybill"`，`if expected` 直接短路——
  **类 rid 被手改也一声不响**。改为显式映射 `{classes: class, relations: relation}`，
  并新增 `RID_LEDGER_MISSING`：台账缺项不能当「没问题」，没有基线就无从判断是否被改。
- **二分真相缺一道闸**（承重实验 L12b 抓出）：同一属性在基础注册表标了 `PII`、
  扩展层写 `null` 时，运行时取扩展层那份 ⇒ **脱敏静默消失**，而两份文件各自都合法、
  schema 也都过。门禁补 `PROP_MARKING_DIVERGED` / `PROP_TYPE_DIVERGED`。

### 新增（P0 契约层）
- `schema/ontology_extension.schema.json`：扩展层元模型契约。顶层
  `additionalProperties:false` 锁死 10 个键；`$defs.propertyType` 与基础 schema
  枚举一致（门禁做漂移比对）；`$defs.functionParam` 扩到 7 键
  （`name/label/description/type/required/of/interface`）且 `type` 走枚举，
  `object_set`/`object_ref` 强制要求 `of`。
- `freight/semantic.py`：`validate_object` 增加 `_check_value_types()`，按注册表
  `value_type` + `nullable` 执行；`datetime`/`date` 校验 ISO 形态。
- `freight/fact_mcp.py::update_object`：写入收口。动作里 10+ 处直连 UPDATE 也过类型校验——
  只校验创建路径等于「新建管、改一条就绕过」。
- `property_registry_policy` 收口为 `enforcement=strict` + `legacy_grace=false`；
  门禁新增 `POLICY_STRICT` 断言，改回 incremental 会红。

### 新增（P1 对齐层）
- 类/链接补 **`rid`**（不可变标识，`ri.mobility.main.object-type|link-type.<uuid5>`）+
  **`plural_display_name`** + **`display_metadata`**（icon/color/visibility/category）。
  `freight/rid_ledger.json` 台账接管 rid：改名时在台账写一行即可续链，身份不断。
- **函数签名类型化**：参数标量类型校验（与 `SemanticLayer._type_ok` 同口径）；
  新增 `result_types` 注册表（`FeeBreakdown`/`QuoteReconcile`/`FeeBreakdownBatch`），
  `returns` 必须是标量类型 / 已注册结果类型 / 对象类型；运行时按 Result Type
  校验返回字段的缺失、多余与类型。
- **接口显式 implements**（对标 `ImplementsInterfaces2`）：`match` 支持
  `structural`/`explicit`，显式声明优先并支持 `property_map` 属性映射；
  谎报实现（声明 Shipper 实现 Chargeable 但 Shipper 没金额属性）由
  `IFACE_UNIMPLEMENTED` 拦。
- OSDK 类型映射补齐 `decimal→Decimal`（Python）/`number`（TS）、`datetime`/`date`/`json`/`enum`。
  金额不再退化成 `str`。

### 新增（P2 运行时三件套）
- **物化**：`freight/projection.py` 轻量对象投影（`object_props` 属性级行式表 +
  数值/字符串双索引 + 增量状态计数）。写入路径同一事务内同步；查询引擎用投影
  **收窄候选集**后仍逐行复算。**正确性边界写清楚**：复算能滤掉多余候选，
  救不回被漏掉的候选，所以收窄前过 `is_fresh()` 闸门，不新鲜整条回退全表。
  `consistency()` 做全量逐属性比对（值级过期只有它能查）。
- **标记传播**：`SecurityEngine.marking_trace()` 实现数据源级 + 类级标记向对象继承，
  行级可见要求「持有**全部**有效标记」（Palantir 语义）。**属性标记不参与行级判定**——
  属性 marking 管列（置 null），提升成整行不可见是过度管控。新增 `guard_output()`
  防 Agent 输出外泄。
- **破坏性变更**：`freight/change_gate.py` 分类 类/属性/类型/基数 变更，
  回溯引用方（函数/规则/动作/数据源/接口/派生类型/OSDK 生成物），
  `freight/breaking_approvals.json` 逐条签字（路径完全一致才算）。
  门禁第 15 项 + `tools/proposal.py` 的 `review.json` 增加 `breaking` 字段。

### 门禁
- 20 项 → **24 项**，新增 `#schema`(扩展层) / `#policy` / `#display` / `#breaking`；
  每项都带负向用例（不是空绿）。

### 承重实验（`scripts/load_bearing_demo.py`）
单测的 `make_ext(mutator)` 只改内存 dict，证明不了「本体文件是唯一真相」——代码里
完全可以再写一份默认值兜底而测试照绿。本脚本换更硬的口径：把仓库拷到
`runs/load_bearing_<ts>/`，**用 `open()/json.dump` 真改 JSON**，再在拷贝里用子进程跑探针，
比对「改前/改后」。16 条用例覆盖六项缺口，改前/改后结论见 `logs/load_bearing_*.json`：

| 用例 | 只改这一处 JSON | 改前 → 改后 |
|---|---|---|
| L1/L2 | 扩展层塞垃圾键 / `value_type` 非法 | 门禁 PASS → `EXT_SCHEMA_INVALID` |
| L3 | `waiting_minutes.value_type: number→boolean` | 种子回放成功(47 对象) → `OntologyViolation` 启动失败 |
| L4/L5 | 同上 | 创建/更新该字段成功 → 类型不符被拒 |
| L6 | `policy` 改回 `incremental` | PASS → `POLICY_NOT_STRICT` |
| L7 | `waybill_id` 参数 `string→integer` | 调用成功 → `SIGNATURE_INVALID` |
| L8 | Result Type 删 `conclusion` | 调用成功 → `RETURN_FIELD_UNDECLARED` |
| L9 | 手改 `Waybill.rid` 末位 | PASS → `RID_LEDGER_DRIFT`（修 bug 前：仍是 PASS） |
| L10 | 改 `label`/`plural_display_name` | 仍 PASS——改名不断链 |
| L11 | 加「CRM → FINANCE」数据源标记 | Shipper 可见 → 整行不可见，拒绝原因指认 CRM |
| L12 | 扩展层给 `waiting_minutes` 标 PII | 行仍可见，只该列置 null |
| L12b | 只改基础层 marking（扩展层 null） | PASS → `PROP_MARKING_DIVERGED`（修 bug 前：仍是 PASS） |
| L13/L14 | 删 `Waybill.properties[waiting_minutes]` | 评审 PASS → FAIL(未签字 1) → 补签字回 PASS |
| L15 | 手工删投影行 | 闸门判不新鲜 → 回退全表，条数与基线一致（不静默少行） |

### 验证
- `pytest` **219 passed**（`tests/test_palantir_alignment.py` 47 项，全部对应曾经的缺口）
- 语义门禁 **24/24**；承重实验 **16/16**（改回原样后门禁 exit=0）
- 7 个 smoke 全过（`smoke_concurrency` 由红转绿）；`scripts/freight_acceptance.py` 12/12

## 3.0.0 (2026-11-20) — 本体元模型对齐 Palantir 三件套 + 500 类真实建模（清掉 540 个模板空壳）

### 修正（承认并清理）
- **删除 540 个模板生成的空壳类 / 540 条模板关系**：这些类由 `tools/expand_ontology.py`
  用同一模板复制（固定 6 属性 `xxx_id+name+status+created_at+updated_at+父id`、描述套
  `"xx域的，按 xxx_id 检索"`、状态机一律 ACTIVE/INACTIVE），脚本里甚至写着
  `if len(classes) < 500: raise SystemExit("扩展规模不足 500")`。ABox 里 48 个实例
  **没有一个落在生成类上**，即 0 条真实数据承载。
- 真实规模重述：此前对外报的「558 类 / 568 链接 / 3407 属性」中，真实手写仅
  **18 类 / 28 链接 / 167 属性**。

### 新增（schema v3：Property Type 升为一等公民）
- `freight/semantic_layer.json` 顶层新增 **`property_types` 注册表（1961 个属性）**，
  每个属性含 `value_type / base_type / unit / marking / timeseries / nullable /
  owner / version / iri / effective_from / used_by`——对标 Palantir Property Type。
  类只引用属性名，业务复杂度靠属性数量承接，不给类加元模型字段。
- `schema/semantic_layer.schema.json`：新增 `$defs.propertyType` + 顶层 `property_types`；
  `value_type` 枚举 `string/text/integer/number/decimal/boolean/datetime/date/json/enum`，
  `base_type` 映射到 `string/int/double/boolean/timestamp/date`。
- `ontology/` 建模包（人工建模 DSL，属性必须显式标注类型，禁止脚本按命名猜类型）：
  - `ontology/__init__.py`：`C() / L() / I()` + 类型短码表 + 单位/标记推断规则
  - `ontology/core_classes.py`：18 个核心类补类型标注（属性名与状态机一字未改）
  - `ontology/domains_01_order.py` 订单/运单/货物/装卸 79 类
  - `ontology/domains_02_capacity.py` 车辆/司机/承运商/分包/维保/能源 96 类
  - `ontology/domains_03_money.py` 报价/计价/费用/结算/发票 77 类
  - `ontology/domains_04_risk.py` 证据/异常/保险/关务/合规 75 类
  - `ontology/domains_05_infra.py` 仓储/线路/地理/设备/单证 77 类
  - `ontology/domains_06_ops.py` 轨迹/客服/组织/合同/指标 76 类
- `tools/build_ontology_v3.py`：只做「合并 + 注册表构建 + 结构校验」，业务内容全部来自
  `ontology/`；同名属性类型冲突**直接报错拒绝写盘**（本轮共拦下 30+ 处真实语义冲突）
- 结果：**500 类 / 503 链接 / 1961 属性 / 35 业务域**，属性数分布 5~21（非固定 6），
  状态机长度分布 2~7（非一律二元），每类至少挂 1 条真实语义关系，链接全部带
  `cardinality`，时态链接 100+ 条

### 新增（反虚胖门禁）
- `freight/semantic.py::validate_ontology_substance()` + 门禁第 20 项：
  - `PROPERTY_REGISTRY_MISSING` 属性注册表缺失
  - `PROP_UNREGISTERED` 类属性未注册（裸属性名）
  - `CLASS_DESCRIPTION_MISSING/TEMPLATED` 描述缺失或套模板
  - `CLASS_AUTO_GENERATED` 存在脚本批量生成的类（彻底堵住 expand 那条路）
  - `CLASS_STATUS_UNIFORM` 通用状态机占比 > 5%
  - `RELATION_TEMPLATED` `xxx_belongs_to_yyy` 形式关系 > 20%
- `tests/smoke_substance_gate.py`：6 条负向承重用例，逐条证明断言真的会拦（不是空绿）

### 修正（真实 bug）
- `freight/object_query.py`：`NUMERIC_TYPES` 补 `decimal`——schema v3 引入 decimal 后
  数值类型校验/聚合校验会对金额字段失效
- `ontology/__init__.py::marking_of()`：原用子串匹配把 `fee_label`（费用名称）误标
  FINANCE、把 `cargo_name` 误标 PII，导致安全层把它们脱敏成 `None`（数据丢失而非脱敏）。
  改为「后缀精确匹配 + 自然人姓名白名单」
- **`primary_key` 从 Property Type 注册表移除**：主键是 Object Type 级语义
  （Palantir `primaryKeyProperty`），放注册表会产生 348 个假冲突
  （`agent_id` 在 ServiceAgent 是主键、在 ServiceCall 只是外键）
- **金额字段不打 marking**：marking 是强制列级脱敏，给运费打 FINANCE 会让普通角色
  看不到金额（无法对账）；财务字段可见性应走 `property_security_policies`

### 兼容性
- `ExtensionRegistry` 新增 `base_property_types` 参数：基础本体是属性注册表全量权威，
  扩展层（含 proposal 分支）可覆盖同名定义；`runtime.py` / `conftest.py` / codegen 已接入
- 测试夹具新增 `make_ext_without(drop, mutator)`：schema v3 后属性全部注册，
  验证 legacy/strict 语义需显式从基础注册表摘除属性
- 门禁 **20/20 全绿**；全量 pytest 通过；OSDK codegen 生成 500 类三端；三个 smoke 演示全跑通

## 2.4.0 (2026-11-20) — 查询表达力追齐 Palantir（Object Set DSL + 派生类型 + Ontology SQL）

### 新增
- `freight/object_query.py`（E1-E6）：组合式 Object Set 查询——谓词层（11 算子+组合+本体期类型校验）、
  集合代数（union/intersect/subtract/followLink+set_id 快照）、管道（orderBy/count/sum/avg/min/max/group_by）、
  时态 followLink（`at` 时间点回放 `valid_from<=at<valid_to`）
- `freight/sql_frontend.py`：**Ontology SQL 最小前端**（对标 Palantir Ontology SQL 2026-11-12 GA）——
  `SELECT/FROM/WHERE/GROUP BY/ORDER BY/LIMIT/OFFSET` 子集，FROM 支持对象类型、派生类型与链接路径
  `(a:Type)-[:link]->(b:Type)`；参数 `:name`。**红线：SQL 只做编译器，产物进同一查询树 IR，
  不新建执行引擎**——TBox 校验/行级列级安全/聚合全部复用
- 派生对象类型 `derived_types`（Palantir derived object type）：`CarrierResource`（司机∪车辆）、
  `DisputedFee`（DISPUTED 费用视图）；查询引擎 `from:派生类型` 递归求值 + `DERIVED_CYCLE` 循环检测；
  类型发现（`object_type_search`）kind=derived 可搜到
- 链接基数 `cardinality`（one_to_one/one_to_many/many_to_one/many_to_many）：TBox 声明 +
  写入校验（活跃关系唯一性、时态关闭释放槽位），缺省 many_to_many 向后兼容
- 函数 `object_set` 入参（Palantir Function 的 ObjectSet 形态）：`fee_breakdown_batch`
- 暴露面：REST `POST /freight/object-set/query`、`POST /freight/object-set/sql`；
  MCP `ontology.query(query_tree)`、`ontology.sql`
- 生成物：TS/Python/OpenAPI 三端含命名派生类型（`x-derived`）、`{Name}Filters`、查询工厂

### 修复
- `FunctionRegistry.resolve`：支持 `module.Class.method` 多层回退 + 实例注入
  （此前 `fee_breakdown`/`quote_reconcile` 注册实际调用即 FUNCTION_IMPL_BROKEN）
- 本体派生类型属性表去掉 `object_id`/`name`（行字段非 props，谓词层不可用）

### 承重测试
- 148→165 项 pytest：SQL/DSL 对拍（同语义字节级一致）、SQL 不绕过 TBox/安全、
  派生类型三端生成 + 删派生类型驱动客户端变化、基数写入拦截、tsc 真编译（删类 TS2305）

## 2.2.0 (2026-11-19) — Palantir 追齐层第一轮


### 新增
- `enhancements/` 包：属性类型注册表（Shared Property Types）、Interfaces 对象多态、安全层（markings 强制控制 + 行级/列级安全策略 + scope token + 策略模拟器）、函数注册表、子进程沙箱、使用度量、对象搜索
- `tools/gen_osdk.py`：从本体 JSON 生成 TypeScript + Python 类型化客户端（OSDK-lite）
- `tools/proposal.py`：本体变更分支 + 评审合并流程（proposal）
- pytest 测试套件（`tests/`），每项新能力配承重测试
- `pyproject.toml` 依赖清单、`README.md`、`LICENSE`、本文件

### 变更
- `freight/fact_store.py`：并发修复——WAL 模式 + 线程本地只读连接，读不再阻塞写（写仍由 api 层串行锁保护）
- `freight/fact_mcp.py`：FactStore 初始化传入 db_path

### 暂缓（边界决策 2026-11-19）
- Action 动作治理（通知副作用 / writeback 回写 / submission criteria）——既有 15 个动作维持现状
- WebSocket 对象订阅（依赖 writeback，降级为版本变更事件）

## 2.1.0 — freight 语义层 v2.1.0

558 类 / 568 关系 / 16 规则 / 15 动作；Owner 元数据 100%；发布快照 1.1.0-demo、1.3.1-demo。
