# Changelog

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
