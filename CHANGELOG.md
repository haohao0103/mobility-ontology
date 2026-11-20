# Changelog

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
