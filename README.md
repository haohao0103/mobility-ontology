# mobility-ontology

货拉拉订单本体：**可执行语义层 + 确定性规则引擎 + 语义门禁**。架构对标 Palantir Foundry Ontology（Palantir 式业务操作本体，非 W3C/OWL 形式语义路线）。

## 这是什么

- **语义层制品** `freight/semantic_layer.json`：558 类 / 568 关系 / 16 规则 / 15 动作 / 4 函数，覆盖 30 个业务域（订单、运单、计价、结算、证据等）。
- **确定性引擎** `freight/engine.py`：报价复算、账单对账、差异归因——金额全部整数分、规则走 AST，LLM 不参与金额计算。
- **语义门禁** `scripts/semantic_gate.py`：CI 强制入口，8 类覆盖（schema/domain_range/ast/rule_cases/references/shacl/shacl_cases/model_tools）+ 负样本控制。
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

# 根层验证（36 项断言）
python verify.py

# 单元测试
pytest tests/

# 启动 REST 服务
python cli.py serve --port 8765
# 文档页 /docs，语义看板 /profile/order-ontology
```

## enhancements/（Palantir 追齐层，2026-11 新增）

对标 Palantir Ontory 能力的增量层，全部遵循承重验收：**只改本体 JSON、代码零改动即可启用/改变行为**。

| 模块 | 对标 Palantir 能力 | 说明 |
|------|-------------------|------|
| `property_registry` | Shared Property Types | 全局属性注册表（类型/单位/脱敏标记/生命周期/时序） |
| `interfaces` | Interfaces | 对象类型多态，函数可按接口接受实现类 |
| `security` | Markings + Object/Property Security Policies + Scoped Tokens | 强制+自主两层访问控制模型 |
| `function_registry` | Functions（注册表） | 签名/版本/owner/测试用例入本体 JSON |
| `sandbox` | Functions 沙箱 | 子进程执行 + timeout + 内存限制 |
| `usage_graph` | 使用度量 | 类被函数/动作/关系引用的反向查询 |
| `search` | 对象搜索 | 全属性过滤 + 分页 |
| `tools/gen_osdk.py` | OSDK | 从本体生成 TypeScript/Python 类型化客户端 |
| `tools/proposal.py` | Global Branching / proposal | 本体变更分支评审流程 |

## 目录结构

```
freight/          # freight 域：语义层 + 引擎 + REST/MCP
mobility/         # mobility 根域（演示城市出行）
profile_ai/       # 展示层
schema/           # JSON Schema 定义
scripts/          # semantic_gate / freight_acceptance / build_release
enhancements/     # Palantir 追齐层（属性注册表/接口/安全/函数注册表/沙箱/度量）
tools/            # OSDK 代码生成 / proposal 分支治理
tests/            # pytest 套件 + 规则/SHACL 用例数据
verify.py         # 根层 36 项验证
```

## 纪律（红线）

1. 金额整数分、规则确定性，LLM 不碰金额与原因选择。
2. 每个能力必须能"只改本体 JSON、代码零改动"地启用/改变（承重测试）。
3. CI 必过 semantic_gate；本体变更走 proposal（tools/proposal.py）。
4. 事实不入本体库（persisted_by_ontology=false）。

## 部署

仅允许部署 STG（见 AGENTS.md）；LLM 只连 STG 网关。
