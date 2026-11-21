# mobility-ontology

货拉拉订单本体：**可执行语义层 + 确定性规则引擎 + 语义门禁**。架构对标 Palantir Foundry Ontology（Palantir 式业务操作本体，非 W3C/OWL 形式语义路线）。

## 这是什么

- **语义层制品** `freight/semantic_layer.json`：500 类 / 503 链接 / 1961 属性（注册表）/ 16 规则 / 15 动作 / 4 函数，覆盖 35 个业务域（订单、运单、计价、结算、证据等）。
- **确定性引擎** `freight/engine.py`：报价复算、账单对账、差异归因——金额全部整数分、规则走 AST，LLM 不参与金额计算。
- **语义门禁** `scripts/semantic_gate.py`：CI 强制入口，**24 项检查 / 13 个覆盖目标**（schema/domain_range/ast/rule_cases/references/shacl/shacl_cases/model_tools/query_dsl/extension_schema/property_policy/display_metadata/breaking_changes）+ 负样本控制。
- **REST/MCP 接口**：`api.py` 挂 `/freight/*` 与 `/profile/*` 路由；LLM agent 经 MCP 工具白名单访问。

存储策略：本体只持久化类、关系、规则、动作与数据源映射；业务事实经 MCP/API 按需查询，不写入本体库（`persisted_by_ontology: false`）。

## 快速开始

```bash
# 安装（uv 或 pip 均可）
uv venv .venv && uv pip install -e ".[dev,shacl]"

# 语义门禁（CI 同款入口）
python scripts/semantic_gate.py

# freight 层验收（12 场景，含 5 个承重测试）
python scripts/freight_acceptance.py

# 承重实验：只改本体 JSON、代码零改动，18 条用例逐条证明行为翻转
python scripts/load_bearing_demo.py   # 20 条承重用例

# 根层验证（36 项断言）
python verify.py

# 单元测试
pytest tests/

# 启动 REST 服务
python cli.py serve --port 8765
# 文档页 /docs，语义看板 /profile/order-ontology
```

## enhancements/（Palantir 追齐层，2026-11 新增）

对标 Palantir Ontology 能力的增量层，全部遵循承重验收：**只改本体 JSON、代码零改动即可启用/改变行为**。

| 模块 | 对标 Palantir 能力 | 说明 |
|------|-------------------|------|
| `property_registry` | Shared Property Types | 全局属性注册表（类型/单位/脱敏标记/生命周期/时序），**strict 执行**：写入侧按 `value_type` 校验 |
| `interfaces` | Interfaces / ImplementsInterfaces | 显式 `implements` + `property_map` 优先，structural 形状匹配兜底 |
| `security` | Markings + access-control-propagation | 数据源级 / 类级标记向对象传播（行级要求持有全部标记）+ 属性级列脱敏 + Agent 输出闸 |
| `function_registry` | Functions（注册表 + 类型系统） | 参数标量类型校验 + `result_types` 返回值契约校验 |
| `sandbox` | Functions 沙箱 | 子进程执行 + timeout + 内存限制 |
| `usage_graph` | 使用度量 | 类被函数/动作/关系引用的反向查询 |
| `search` | 对象搜索 | 全属性过滤 + 分页 |
| `freight/projection` | Object Storage v2（轻量档） | 属性级投影 + 双索引 + 新鲜度闸门 + 一致性比对；不做全量 OSv2 |
| 顶层 `state_machines` | Object Type 状态机 | 迁移边真源在动作，声明只补起点/终态/外部状态；完备性判据 + 门禁第 25 项 + REST/MCP 视图与状态轨迹 |
| `freight/change_gate` | Ontology change management | 破坏性变更分类 + 引用影响回溯 + 逐条签字 |
| `tools/gen_osdk.py` | OSDK | 从本体生成 TS/Python 类型化客户端（decimal→Decimal，不再退化成 str） |
| `tools/proposal.py` | Global Branching / proposal | 分支评审 + breaking 字段，未签字的破坏项不得 merge |

两个 JSON Schema 分别管两层，缺一不可：
- `schema/semantic_layer.schema.json` —— 基础本体（类/链接/属性注册表/规则/函数）
- `schema/ontology_extension.schema.json` —— 扩展层（属性覆盖/接口/标记/安全策略/函数注册表）

## 目录结构

```
freight/          # freight 域：语义层 + 引擎 + REST/MCP + 投影 + 变更门禁
mobility/         # mobility 根域（演示城市出行）
profile_ai/       # 展示层
schema/           # JSON Schema 定义（semantic_layer + ontology_extension + query/rule/judgment/evidence/proposal）
scripts/          # semantic_gate / freight_acceptance / load_bearing_demo / build_release
enhancements/     # Palantir 追齐层（属性注册表/接口/安全/函数注册表/沙箱/度量）
ontology/         # 500 类的建模 DSL（人工建模，属性必须显式标注类型）
tools/            # OSDK 代码生成 / ontology 渲染 / proposal 分支治理
tests/            # pytest 套件 + 规则/SHACL 用例数据
verify.py         # 根层 36 项验证
```

## 纪律（红线）

1. 金额整数分、规则确定性，LLM 不碰金额与原因选择。
2. 每个能力必须能"只改本体 JSON、代码零改动"地启用/改变（承重测试）。
3. CI 必过 semantic_gate（26 项，含扩展层 schema / 策略口径 / 展示层 / 破坏性变更 / 状态机完备性 / ABox 深度校验）。
4. 事实不入本体库（persisted_by_ontology=false）。
5. **注册表必须执行**：属性类型、函数签名、返回结构、标记传播，光登记不算。
6. **物化层必须可验真**：投影不新鲜就回退全表，绝不让索引静默少行。
7. **破坏性变更必须签字**：`freight/breaking_approvals.json` 路径完全一致才算数。
8. **状态机必须声明且完备**：有迁移动作的对象类型必须声明 `state_machines`，
   状态的来源与归宿说得清，终态不得再有迁出边（门禁第 25 项）。
9. **本体必须被业务证明过**：深水区（`abox_targets`）的每个类型要有实例、不是孤岛，
   每个迁移动作要有 APPROVED 审计（门禁第 26 项）。门槛只约束深水区，不逼全量覆盖。

## 部署

仅允许部署 STG（见 AGENTS.md）；LLM 只连 STG 网关。
