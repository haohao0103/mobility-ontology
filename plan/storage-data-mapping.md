# G2-01 存储策略与数据源映射规范

- 状态：Done
- 版本：0.1
- 日期：2026-11-19
- 输入：[freight/semantic_layer.json](../freight/semantic_layer.json) v2.1.0
- 规模：558 类 / 568 关系 / 35 个对象类型组 / 16 条规则 / 15 个动作 / 18 条数据源映射

## 1. 结论

继续维持 `storage_policy: knowledge-only`：

1. **本体库只存知识**：类、关系、属性白名单、规则、动作、函数、primitives、pricing 结构、closure、diagnosis 与数据源映射写入 `freight/semantic_layer.json`；
2. **业务事实不落本体库**：18 个已映射类的事实按主键经 MCP / 外部系统现查，请求结束即释放；
3. **投影不是事实源**：当前没有生产投影；G2-04 若引入只做派生缓存，源系统始终是权威；
4. **不写回业务系统**：只读检索与只读提议，不做 apply / edits / 副作用。

## 2. 存储位置分层

| 存储位置 | 内容 | 权威性 | 生命周期 | 当前状态 |
|---|---|---|---|---|
| canonical TBox | 类、关系、属性名、规则 AST、动作声明、函数与 primitives、pricing 参数、closure 白名单、diagnosis | 唯一知识权威 | 随版本发布，不可变快照回放 | 已有：`freight/semantic_layer.json` |
| live source | 18 个已映射类的对象属性与关系实例 | 业务事实权威 | 由源系统维护，按需读取 | 已有：MCP 现查 |
| request memory | 一次请求内按关系与闭包临时组装的实例图 | 无权威性 | 请求结束释放 | 已有：Agent / FactMCP |
| projection | 对象与关系的最小检索字段、索引 | 派生缓存，非权威 | 可重放、可重建 | **无**，G2-04 评估后再定 |

`knowledge-only` 的精确含义是：**本体库不保存任何业务实例**，不表示“所有类都没有数据源”。已映射类的事实仍可实时查询，只是不写入本体库。

## 3. 类级映射

### 3.1 已映射类：事实来自源系统

以下 18 个类有明确的数据源、工具与身份键。事实读取一律按 `key` 现查，不写回、不做本地持久化。

| 类 | 业务含义 | 系统 | 工具 | 身份键 | 源更新频率（约定） | 读取策略 | 存储位置 |
|---|---|---|---|---|---|---|---|
| Shipper | 货主主数据与结算主体 | CRM | `crm.get_shipper` | `shipper_id` | 每日 / 主数据变更 | 按 key 现查 | live source |
| Order | 运输需求与订单状态 | TMS | `tms.get_order` | `order_id` | 准实时 | 按 key 现查 | live source |
| Waybill | 履约主数据、费用汇总与状态 | TMS | `tms.get_waybill` | `waybill_id` | 准实时 | 按 key 现查 | live source |
| Driver | 司机档案与从业资质 | TMS | `tms.get_driver` | `driver_id` | 每日 / 主数据变更 | 按 key 现查 | live source |
| Vehicle | 车辆档案、车型与载重 | TMS | `tms.get_vehicle` | `vehicle_id` | 每日 / 主数据变更 | 按 key 现查 | live source |
| Cargo | 货物重量、体积、件数与包装 | TMS | `tms.get_cargo` | `cargo_id` | 准实时 | 按 key 现查 | live source |
| Stop | 多点装卸站点、顺序与等候时长 | TMS | `tms.list_stops` | `stop_id` | 准实时 | 按运单现查 | live source |
| FeeItem | 费用项、金额口径与规则依据 | 计费中心 | `billing.list_fee_items` | `fee_id` | 准实时 | 按运单现查 | live source |
| Settlement | 结算单与入账状态 | 结算系统 | `finance.get_settlement` | `settlement_id` | 每日批量 | 按运单现查 | live source |
| Invoice | 发票与开票状态 | 发票系统 | `finance.get_invoice` | `invoice_id` | 每日批量 | 按结算单现查 | live source |
| ExceptionTicket | 异常 / 争议工单 | 客服工单 | `cs.list_exceptions` | `ticket_id` | 事件驱动 | 按运单现查 | live source |
| Evidence | 支撑证据、版本与原始 payload | 证据中心 | `evidence.list` | `evidence_id` | 追加写，不可变 | 按对象现查 | live source |
| Quote | 报价单与当前生效版本 | 计价中心 | `pricing.get_quote` | `quote_id` | 版本发布时 | 按订单现查 | live source |
| QuoteItem | 逐项报价明细与规则版本 | 计价中心 | `pricing.list_quote_items` | `quote_item_id` | 版本发布时 | 按报价单现查 | live source |
| PricingRule | 生效定价规则版本 | 计价中心 | `pricing.get_rule` | `rule_id` | 版本发布时 | 按费用类型 / 城市 / 车型 / 时间现查 | live source |
| Track | 轨迹摘要、里程与时长 | 轨迹平台 | `track.get_track` | `track_id` | 准实时 / 流式 | 按运单现查；GPS 点位按需 | live source |
| Carrier | 承运商主体资质与承运状态 | 承运商中心 | `carrier.get_carrier` | `carrier_id` | 每日 / 主数据变更 | 按 key 现查 | live source |
| SubcontractOrder | 分包承运商、分摊金额与状态 | 分包系统 | `subcontract.get_order` | `subcontract_id` | 准实时 | 按运单或分包单现查 | live source |

映射约束：

1. 每个类的 `key` 必须与 `data_sources[].key` 一致，且在同一类内唯一；
2. 对象 ID 统一为 `<类名 snake_case>:<key 值>`，例如 `waybill:WB20261105001`；
3. 工具返回的字段必须落在该类的 `properties` 白名单内，白名单只描述字段名，不保存字段值；
4. 只允许按 key 或业务查询条件读取，不允许全量扫描后落库。

### 3.2 未映射类：默认 canonical TBox only

558 个类中除 3.1 的 18 个类外，其余 **540 个类一律只有 TBox 定义，没有事实位置**：

- 它们参与目录、类型检索、关系定义与规则域校验；
- 不返回实例，不伪造“空事实”，不把定义当作数据；
- 其 `properties` 字段是结构占位，不代表已有实例或已有数据源；
- 30 个扩展业务域共 540 个未映射类，均属于此边界：每个域 18 个未映射类；其中 16 个域另含 1 个已映射核心类，14 个域只有这 18 个类。覆盖仓储、线路、地理、合同、保险、关务、合规、维保、能源、设备、客服、组织、单证、指标，以及订单 / 运单 / 货物 / 装卸 / 车辆 / 司机 / 承运商 / 分包 / 报价 / 计价 / 费用 / 结算 / 发票 / 证据 / 轨迹 / 异常等域中的生成型细分类。

新增事实映射的准入条件：

1. 在 `data_sources` 增加一条映射，声明 `class`、`system`、`tool`、`key`、`description`；
2. 该类的 `key` 同时写入 `classes[].key`；
3. 类与映射必须已有 `owner`、`source`、`version`、`iri`、`effective_from`；
4. 通过 CI 语义门禁后才能发布，禁止在运行时临时注册。

### 3.3 关系映射

- **关系类型**（domain / range / temporal / closure 方向）属于 canonical TBox；
- **关系实例**统一走 `relation_source`：`graph.list_relations`，按主语、谓语与方向现查；
- 关系实例的身份键为 `(subject_id, predicate, object_id, valid_from)`；
- 关系实例不进入本体库；闭包只沿 `closure.relations` 白名单展开，与存储位置无关。

## 4. 属性级映射

| 属性类别 | 示例 | 存放位置 | 更新方式 | 血缘要求 |
|---|---|---|---|---|
| 定义属性 | `id`、`label`、`description`、`aliases`、`statuses`、`properties`、`domain`、`range`、`temporal` | canonical TBox | 版本发布 | `owner`、`source`、`version`、`iri`、`effective_from` |
| 身份属性 | `classes[].key`、`data_sources[].key` | canonical TBox | 版本发布，修改需迁移 | 与源系统主键一致 |
| 规则与公式 | `when`、`judge.ast`、`judge.calc`、`severity` | canonical TBox | 版本发布 | `rule_id` + 本体版本 |
| 动作声明 | 参数、前置状态、角色、阻断规则 | canonical TBox | 版本发布 | `action_id` + 本体版本 |
| 事实属性 | 金额、状态、时间、司机、车辆、站点、里程等实例字段 | live source | 按 key 现查，不缓存 | `source_system`、`source_object_id`、`source_updated_at`、`observed_at` |
| 派生属性 | 规则应计金额、差异、KPI、汇总 | 请求内存实时计算 | 每次按规则 AST 计算 | 输入对象 / 证据 ID、`rule_id`、本体版本 |
| 证据属性 | 原始 payload、版本、校验和 | 证据中心 | 追加写，不可变 | `source_system`、`occurred_at`、`version`、`checksum` |
| 关系实例属性 | 主语、谓语、宾语、有效期 | live source | 按需现查 | `source_system`、`valid_from`、`valid_to` |

属性规则：

1. **定义属性可以持久化，事实属性不可以持久化到本体库**；
2. 派生属性不得覆盖源事实，只能作为带公式与输入证据的计算结果返回；
3. 同一属性如果既需要定义又需要事实值，定义放 TBox，值放源系统，两者不混存；
4. 属性命名统一 snake_case，源系统字段到本体属性的改名必须记录在映射说明或证据中。

## 5. 身份键与更新频率

### 5.1 身份键

- 所有类都有 `key`，当前 558 个类均不缺身份键；
- 事实对象 ID = `<类名 snake_case>:<key 值>`，本体不生成第二套 ID；
- 关系端点只引用对象 ID；
- 证据 ID 自成一类，不参与对象身份键。

### 5.2 更新频率口径

| 频率档位 | 适用数据源类别 | 读取要求 |
|---|---|---|
| 主数据每日 / 变更驱动 | Shipper、Driver、Vehicle、Carrier | 每次现查；展示 `observed_at`，不假设本地缓存最新 |
| 准实时 | Order、Waybill、Cargo、Stop、FeeItem、Track、SubcontractOrder | 每次现查；金额与状态判断不得复用旧请求结果 |
| 每日批量 | Settlement、Invoice | 每次现查；跨日对账需同时记录批次时点 |
| 版本发布时 | Quote、QuoteItem、PricingRule | 每次按生效时间与版本现查；历史判断按 `effective_from` 回放 |
| 事件驱动 | ExceptionTicket | 每次现查 |
| 追加写 / 不可变 | Evidence | 按对象现查；以 `version` 与 `checksum` 校验 |

刷新频率是源系统的更新约定，不等于允许本地缓存。当前运行时对所有事实采用 **per-request 现查**。

## 6. 血缘字段

### 6.1 统一字段

| 层级 | 必填血缘字段 |
|---|---|
| 对象事实 | `source_system`、`source_object_id`、`source_updated_at`、`observed_at` |
| 关系事实 | `source_system`、`valid_from`、`valid_to`、`observed_at` |
| 证据 | `source_system`、`occurred_at`、`version`、`checksum` |
| 规则判断 | `rule_id`、`semantic_version`、输入对象 ID、输入证据 ID、`generated_at`、`trace_id` |
| 动作提议 | `action_id`、`semantic_version`、目标对象 ID、输入证据 ID、`generated_at`、`trace_id` |

### 6.2 当前实现对照

| 位置 | 已具备 | 缺口 |
|---|---|---|
| `objects` 演示表 | `object_id`、`type`、`props`、`created_at`、`updated_at` | 缺 `source_system`、`source_object_id`、`source_updated_at`、`observed_at`；`source_object_id` 暂可由对象 ID 后缀推导 |
| `relations` 演示表 | `source_system`、`valid_from`、`valid_to`、`created_at` | 缺 `observed_at` |
| `evidence` 演示表 | `source_system`、`occurred_at`、`version`、`checksum` | 无 |
| `audit` 演示表 | 本地决策审计 | 不等同于跨系统数据血缘 |

缺口属于演示事实库与生产 Connector 的差距，由 G5-04 统一接入 OpenLineage 时补齐；本规范先锁定字段口径，不把缺口解释为允许省略血缘。

## 7. knowledge-only 与投影边界

| 判断项 | knowledge-only | 投影（G2-04 之后） |
|---|---|---|
| 存什么 | 只存 TBox 知识与映射 | 只存对象 / 关系的最小检索字段与索引 |
| 是否权威 | 知识权威 | 派生缓存，永不权威 |
| 冲突处理 | 以本体版本为准 | 以源系统为准，投影可重建 |
| 规则 / 动作 / closure / diagnosis | 必须保留 | 禁止进入投影 |
| 证据原始 payload | 不进入本体库 | 默认不复制，按需回源 |
| 写入方向 | Git / 发布流程 | 源系统 → 投影单向同步 |

投影准入沿用 [基准 3.1](../roadmap/palantir-benchmark.md) 的三个触发条件：

1. 单类型对象量超过约定阈值；
2. 一次诊断涉及跨源 join 超过约定次数；
3. Object Set 查询 P95 超出 SLO。

任一条件未命中时继续现查。即使命中，投影也只服务检索与分页，不改变“源系统是事实权威、本体库只存知识”的边界。

## 8. 验收与变更

- [x] 18 个事实类均有系统、工具、身份键；
- [x] 540 个未映射类明确为 canonical TBox only，不伪造事实；
- [x] 属性按定义 / 事实 / 派生 / 证据 / 关系实例分类，并给出存放位置；
- [x] 18 个数据源给出源更新频率与现查策略；
- [x] 对象、关系、证据、判断、提议的血缘字段已定义；
- [x] knowledge-only 与投影的权威性、冲突处理与准入条件已划清。

后续变更遵循：

1. 新增类或属性：先改 canonical TBox 与 `owner/source/version/iri/effective_from`，过 CI；
2. 新增事实映射：按 3.2 准入条件补 `data_sources`，不得在运行时注册；
3. 引入投影：由 G2-04 出设计与触发评估，G2-05 做同步与一致性校验；
4. 投影与现查双路径结果不一致时，以现查为准并触发重建，不允许反向覆盖源系统。