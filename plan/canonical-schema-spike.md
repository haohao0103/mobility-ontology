# G0-03 Canonical schema 选型 spike

- 状态：Done
- 日期：2026-11-19
- 输入：[freight/semantic_layer.json](../freight/semantic_layer.json) v2.1.0
- 规模：558 类 / 568 关系 / 35 个对象类型组 / 16 条规则 / 15 个动作 / 18 条数据源映射
- 样本：手写核心域 + 24 个批量扩展业务域，覆盖订单、运单、仓储、关务、合规、维保、客服等

## 1. 结论

**二选一：选 LinkML。**

- LinkML 作为类型声明、校验与 SDK 生成的 schema 层；
- 现有 JSON DSL 继续作为运行时知识文档与事实绑定载体，不把 Neo4j 当作 schema 源；
- Neo4j Ontology Graph Spec 只作为 LPG 图投影目标，服务于实例检索与 search-around，不承担 TBox 编写、规则与动作语义。

选择 LinkML 的核心理由：

1. 能原生表达类、slot、枚举、标识符、唯一键、domain/range、subsets，覆盖当前声明层主体；
2. 能生成 JSON Schema、OWL、SHACL、Pydantic/TS 类型，和 G1-01、G6-03 的方向一致；
3. 对规则、动作、公式等执行语义虽然不能完整承载，但可以用 annotations 保留引用，执行语义继续留在 JSON 运行时；
4. Neo4j 只能表达节点标签、关系类型和属性约束，无法承载当前 16 条规则、15 个动作、费用公式、closure 与 diagnosis，作为 canonical 会直接丢失语义。

## 2. 评估口径

| 维度 | 说明 |
|---|---|
| 声明能力 | 类、slot、枚举、标识符、唯一键、关系 domain/range、分组 |
| 执行语义 | 规则公式、compute、severity、动作前置状态、函数、primitives |
| 生成能力 | JSON Schema、OWL、SHACL、Pydantic/TS SDK |
| 图存储适配 | 是否能直接投影为 LPG 节点/关系/约束 |
| 迁移成本 | 字段是否需要补类型、IRI、owner、version 等 |
| CI 可校验 | 能否在提交时阻断非法本体 |

## 3. LinkML 字段映射

| 当前 JSON 字段 | LinkML 落点 | 可迁移性 | 说明 |
|---|---|---|---|
| `meta.name` / `version` / `scope` / `note` | schema `annotations` / `description` | 可迁移 | 元数据进入 schema 级 annotations |
| `meta.storage_policy` | schema `annotations` | 可迁移 | `knowledge-only` 作为治理标注保留 |
| `classes[].id` | `classes.<id>` + `class_uri` | 可迁移 | 需要生成稳定 IRI；当前类没有 iri |
| `classes[].label` | `annotations.label` | 可迁移 | LinkML 没有强制的 label 语义，用 annotation 保留 |
| `classes[].description` | `description` | 可迁移 | 直接映射 |
| `classes[].aliases` | `aliases` | 可迁移 | LinkML 元素支持 aliases |
| `classes[].key` | slot `identifier: true` + class `unique_keys` | 可迁移 | 当前 key 是 JSON 属性名，需要先生成 slot |
| `classes[].statuses` | 枚举 + `status` slot | 部分可迁移 | 状态值可生成 enum；类级状态集合需 annotation |
| `classes[].properties` | slots | 部分可迁移 | 当前只有属性名，没有类型、基数、必填；迁移时先全部生成 string slot |
| 类级 lifecycle（experimental/active/deprecated） | class `annotations` / `status` | 当前缺失 | G1-02 补齐后再迁移 |
| 类级 owner / source / version / iri / effective_from | class `annotations` / `class_uri` | 当前缺失 | G1-02 补齐后再迁移 |
| `relations[].id` | slot 名 | 可迁移 | 需要避免与属性 slot 重名 |
| `relations[].label` | `annotations.label` | 可迁移 | 直接保留 |
| `relations[].domain` / `range` | slot `domain` / `range` | 可迁移 | 这是 LinkML 的原生能力 |
| `relations[].temporal` | slot `annotations.temporal` | 可迁移 | 时间关系作为 annotation，由运行时解释 |
| 关系基数 / 反向 / 是否参与闭包 | slot `multivalued` / `inverse` / `annotations` | 部分可迁移 | 当前只有 `temporal` 与 closure 白名单；需 G1-02 补齐 |
| `object_type_groups[]` | LinkML `subsets` + class `subsets` | 可迁移 | 组 ID/标签/描述/别名放 subset metadata；成员关系放 class.subsets |
| `closure` | schema `annotations` | 不可原生迁移 | LinkML 不执行闭包策略，保留在 JSON |
| `diagnosis` | schema `annotations` | 不可原生迁移 | 属于运行时诊断策略，保留在 JSON |
| `pricing` | 独立类/annotations | 部分可迁移 | 费用结构可建模，但公式与费率解释仍属运行时 |
| `rules[].when` | LinkML `rules.preconditions` | 部分可迁移 | 简单 `field/op/value` 条件可映射；嵌套 all/any 当前未完整覆盖 |
| `rules[].judge.formula` | annotation / 外部规则 AST | 不可迁移 | LinkML 不承载计算公式 |
| `rules[].judge.compute` / `explain` / `severity` | annotation | 不可原生迁移 | 保留在 JSON，G1-03 迁移为 AST |
| `actions[]` | class `annotations` / 外部动作模型 | 不可原生迁移 | 前置状态、角色、参数、目标类型均需运行时执行 |
| `functions[]` / `primitives[]` | `annotations` | 不可原生迁移 | 工具/函数签名不是 ontology schema |
| `data_sources[]` | class `annotations` | 部分可迁移 | 可保留 system/tool/key，但绑定关系仍由运行时使用 |
| `relation_source` | `annotations` | 部分可迁移 | 说明性字段，不影响图结构 |

### LinkML 迁移结论

- 声明层主体可以迁移；
- 规则、动作、函数、primitives、pricing 公式、closure、diagnosis 不能由 LinkML 独立承载；
- 当前类属性没有类型信息，迁移前必须补 slot 类型，否则 LinkML 只能全部生成 string。

## 4. Neo4j Ontology Graph Spec 字段映射

| 当前 JSON 字段 | Neo4j LPG 落点 | 可迁移性 | 说明 |
|---|---|---|---|
| `classes[].id` | 节点标签 | 可迁移 | 标签是动态字符串，适合作为类名 |
| `classes[].label` / `description` / `aliases` | 节点属性或元数据节点 | 部分可迁移 | 图模式没有类级元数据标准，需要自建 `:OntologyClass` 元节点 |
| `classes[].key` | 唯一性约束 | 可迁移 | `CREATE CONSTRAINT ... IS UNIQUE` |
| `classes[].statuses` | 属性 + 应用校验 | 部分可迁移 | Neo4j 没有 enum 约束；只能靠应用层 |
| `classes[].properties` | 节点属性 | 部分可迁移 | 图属性弱类型；缺少属性级 schema |
| `relations[].id` | 关系类型 | 可迁移 | 关系类型是静态字符串 |
| `relations[].domain` / `range` | 起点/终点标签 | 部分可迁移 | 关系类型本身不约束端点，需要应用层或元数据约束 |
| `relations[].label` | 关系属性或类型名 | 部分可迁移 | 中文 label 不适合直接做关系类型时，放属性 |
| `relations[].temporal` | 关系属性 | 可迁移 | 作为关系属性保存 |
| `object_type_groups[]` | 额外标签或分组节点 | 部分可迁移 | 没有原生 subset 语义 |
| `closure` | 应用配置 | 不可迁移 | Neo4j 不执行闭包白名单 |
| `diagnosis` | 应用配置 | 不可迁移 | 属于诊断逻辑 |
| `pricing` | 节点/关系或应用配置 | 部分可迁移 | 图可存费率结构，公式仍需应用层 |
| `rules[]` | 元数据节点 + 应用执行 | 不可原生迁移 | 图查询不能替代规则公式与判断语义 |
| `actions[]` | 元数据节点 + 应用执行 | 不可原生迁移 | 状态迁移、角色、参数校验都在运行时 |
| `functions[]` / `primitives[]` | 元数据节点 | 不可原生迁移 | 需要外部工具注册表 |
| `data_sources[]` | 元数据节点 | 部分可迁移 | system/tool/key 可存，绑定逻辑仍在应用层 |
| `relation_source` | 元数据属性 | 部分可迁移 | 说明性字段 |

### Neo4j 迁移结论

- Neo4j 适合做**实例图投影**：类 → 标签、关系 → 关系类型、key → 唯一约束；
- 不适合做**TBox 唯一源**：类级元数据、规则、动作、闭包、诊断、费用公式都没有原语；
- 若把 Neo4j 作为 canonical，需要把大量语义塞进 `:OntologyClass` / `:OntologyRelation` 元节点，最终得到一个图数据库里的 JSON DSL，收益低于直接保留 JSON。

## 5. 二选一矩阵

| 维度 | LinkML | Neo4j Ontology Graph Spec |
|---|---|---|
| 类 / slot / 枚举 / 标识符 | 强 | 中 |
| 关系 domain/range | 强 | 弱（需应用层约束） |
| 对象类型分组 | 强（subsets） | 弱（额外标签） |
| 规则 / 公式 / 判断 | 弱（annotations） | 无 |
| 动作 / 状态迁移 | 无 | 无 |
| JSON Schema / OWL / SHACL / SDK 生成 | 强 | 无 |
| 实例图检索 / search-around | 弱 | 强 |
| CI 阻断式校验 | 强 | 弱 |
| 迁移成本 | 中 | 中高 |
| 结论 | **选为 schema 层** | 仅作图投影 |

## 6. 决策与后续

1. **G1-01** 继续产出 `schema/semantic_layer.schema.json`，作为运行时 JSON 的元模型契约；LinkML 是声明层的可生成镜像，不替代运行时契约。
2. **G1-02** 必须补齐 `owner`、`source`、`version`、`iri`、`effective_from`、类 lifecycle 与属性类型；否则 LinkML 迁移只能得到无类型、无治理信息的骨架。
3. **G1-03** 继续把规则公式迁移为自定义 AST；LinkML 只保留规则引用与 annotation，不承载 compute。
4. **G2-01** 再决定 Neo4j 投影范围：只投影类标签、关系类型、唯一键与检索所需属性；规则、动作、closure、diagnosis 不进入图。
5. **G6-03** 的 SDK 生成从 LinkML 出发生成 TS/Python 类型；运行时 JSON 仍通过 G1-01 的 JSON Schema 校验。

## 7. 本次 spike 的验证证据

- `validate_semantic`：0 个问题；
- 全量目录 558 类超出 6000 字符预算，进入 overflow，35 个业务域索引压缩后 5728 字符，仍在预算内；
- 单域缩域目录：订单域 3749 字符、仓储域 3905 字符、关务域 4111 字符、合规域 4260 字符，均 `fits=true`；
- 说明：500+ 类规模下，schema 层必须支持对象类型组与预算受限检索；LinkML subsets 与现有 `object_type_groups` 语义一致，Neo4j 需要额外自建分组元数据。