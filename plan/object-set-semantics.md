# G2-02 Object Set 语义规范

- 状态：Done
- 版本：0.1
- 日期：2026-11-19
- 输入：[存储与数据源映射规范](storage-data-mapping.md)、[freight/semantic_layer.json](../freight/semantic_layer.json)
- 目标：为 G2-03 的 `freight/object_set.py` 定义可实现的集合语义，先覆盖 filter / pivot / search-around / aggregate / 分页，不引入全量物化。

## 1. 基本定义

**Object Set** 是一次查询得到的、不可变的对象 ID 有序集合，附带来源查询、类型、计数与血缘信息。

| 属性 | 语义 |
|---|---|
| `object_set_id` | 集合身份，由根查询、类型范围、排序键、语义版本与数据快照共同决定的稳定哈希 |
| `object_ids` | 去重后的对象 ID 有序列表 |
| `type_ids` | 集合内实际出现的类型；单类型查询只有一个 |
| `count` | 去重后的对象数量 |
| `query` | 产生该集合的规范化查询 |
| `semantic_version` | 查询时使用的本体版本 |
| `snapshot_id` | 分页与一致性校验使用的数据快照标识 |
| `next_page_token` | 仅分页结果返回；一次性结果不返回 |

约束：

1. 对象身份使用 `<类名 snake_case>:<key 值>`，与 G2-01 一致；
2. 对象 ID 在集合内唯一，重复锚点或重复邻居只保留一次；
3. 集合顺序必须确定：默认 `type ASC, object_id ASC`，显式 `order_by` 优先；
4. 集合只包含对象引用，不内嵌完整对象；完整对象由 `objects` 字段按页返回；
5. 集合本身不持久化到本体库；`object_set_id` 是查询指纹，不是新的事实存储。

## 2. filter

filter 从一个对象类型开始，按属性条件筛选。

### 2.1 输入形态

```json
{
  "type": "Waybill",
  "where": [
    {"property": "status", "op": "eq", "value": "DELIVERED"},
    {"property": "actual_amount", "op": "gte", "value": 1000}
  ]
}
```

- `where` 支持对象或数组；对象等价于逐字段 `eq`；
- 数组元素之间默认 **AND**；v1 不提供 OR / NOT；
- 属性必须是该类型 `classes[].properties` 白名单成员，或对象列 `object_id`、`type`、`name`、`status`、`created_at`、`updated_at`；
- 未声明属性、非法类型、非法操作符返回 `SCHEMA_ERROR`，并带 `allowed_properties` / `allowed_ops`。

### 2.2 操作符与空值

| 操作符 | 语义 | 空值处理 |
|---|---|---|
| `eq` | 等于 | `null` 只匹配显式 `null` |
| `ne` | 不等于 | 排除 `null` 与缺失值，需显式使用 `is_not_null` |
| `gt` / `gte` / `lt` / `lte` | 数值或字典序比较 | 空值不匹配 |
| `in` | 属于非空数组 | 空值不匹配 |
| `not_in` | 不属于非空数组 | 排除空值，避免三值逻辑歧义 |
| `contains` | 字符串包含 | 空值不匹配 |
| `starts_with` | 字符串前缀 | 空值不匹配 |
| `is_null` | 属性缺失或显式 `null` | 匹配 |
| `is_not_null` | 属性存在且非 `null` | 匹配 |

缺失属性与显式 `null` 在 v1 中等价，统一按“空值”处理；需要区分时应新增独立操作符，不在 `ne` 上做隐式扩展。

### 2.3 结果

- `count` 为满足条件的去重对象数；
- `objects` 按确定顺序返回；
- `limit` 只截断返回对象，不改变 `object_set_id` 的查询身份；
- filter 不改变对象类型，不自动沿关系扩展。

## 3. pivot

pivot 在一个 Object Set 上按一个或多个属性分组，并计算指标。

```json
{
  "object_set_id": "os_...",
  "pivot": {
    "group_by": ["status", "vehicle_type"],
    "metrics": [
      {"op": "count", "as": "waybills"},
      {"op": "sum", "property": "actual_amount", "as": "amount"}
    ]
  }
}
```

语义：

1. `group_by` 为空时返回单行全局汇总；
2. 分组键按字典序升序，空值单独成组并排在最前；
3. 指标只支持 `count` / `sum` / `avg` / `min` / `max`；
4. `count` 不指定属性时统计对象数，指定属性时统计非空值数；
5. `sum` 忽略空值；全部为空时返回 `0`；`avg` / `min` / `max` 全为空时返回 `null`；
6. pivot 结果不是 Object Set，不产生 `object_set_id`；
7. pivot 不改变底层集合，不触发额外事实写入。

## 4. search-around

search-around 从一个或多个锚点对象出发，沿指定 link type 取相邻对象。

```json
{
  "object_ids": ["waybill:WB20261105001"],
  "link_type_ids": ["waybill_produces_fee", "fee_included_in_settlement"],
  "direction": "out",
  "depth": 2,
  "limit": 200
}
```

语义：

1. 锚点对象必须存在，否则返回 `OBJECT_NOT_FOUND`，并列出缺失 ID；
2. `link_type_ids` 为空表示所有已声明关系；非法关系返回 `SCHEMA_ERROR`；
3. `direction` 只能是 `out` / `in` / `both`；
4. `depth` 为 1 到 5；每多一跳都从上一跳新增对象继续；
5. 结果包含锚点与所有可达对象，按对象 ID 去重；
6. 只包含当前有效关系（`valid_to IS NULL`）；
7. `limit` 限制对象总数；达到上限时返回 `truncated: true`，已返回部分仍是确定前缀；
8. 结果同时返回 `relations`，关系按 `(subject_id, predicate, object_id, valid_from)` 去重；
9. 结果附带 `link_type_summary`，按 `(link_type_id, direction, neighbor_type)` 汇总关系数与相邻对象数；
10. search-around 产生新的 Object Set，`object_set_id` 由锚点集合、link type、方向、深度与快照决定。

### 4.1 link_type_summary

```json
[
  {
    "link_type_id": "waybill_produces_fee",
    "direction": "out",
    "neighbor_type": "FeeItem",
    "relation_count": 3,
    "object_count": 3
  }
]
```

- `relation_count` 是该 link type 下的有效关系条数；
- `object_count` 是该 link type 下相邻对象的去重数量；
- 汇总按 `link_type_id, direction, neighbor_type` 排序；
- 同一对对象存在多条有效关系时，关系数可以大于对象数。

## 5. aggregate

aggregate 与 pivot 共享指标计算内核，但面向单类型集合，不要求先构造 Object Set。

语义与 pivot 一致：

- `group_by` 可为空；
- 指标支持 `count` / `sum` / `avg` / `min` / `max`；
- 空值处理与 pivot 相同；
- `limit` 限制分组行数，按分组键升序截断；
- aggregate 不产生 Object Set。

## 6. 分页

### 6.1 输入输出

- 首次请求返回 `next_page_token`；
- 后续请求携带同一查询与 `next_page_token`，不得同时修改 filter / order / limit；
- 最后一页不返回 `next_page_token`；
- 分页结果每页都返回相同的 `object_set_id` 与 `snapshot_id`。

### 6.2 token 语义

`next_page_token` 是模型不可解释的不透明字符串，内部至少包含：

| 字段 | 作用 |
|---|---|
| `version` | token 结构版本 |
| `query_hash` | 规范化查询指纹 |
| `object_set_id` | 集合身份 |
| `snapshot_id` | 数据快照 |
| `last_key` | 上一页最后一个排序键 |
| `offset` | 兼容偏移量，keyset 为主 |
| `issued_at` / `expires_at` | 有效期 |

校验规则：

1. token 与当前查询的 `query_hash` 不一致返回 `TOKEN_QUERY_MISMATCH`；
2. token 过期返回 `TOKEN_EXPIRED`；
3. token 损坏或版本不支持返回 `TOKEN_INVALID`；
4. 同一 token 重复使用必须返回同一页，不得重复消费；
5. 不允许客户端拼接或修改 token 内容。

### 6.3 一致性

- 分页使用 keyset（`last_key`）为主，保证按排序键推进；
- 所有页的并集必须等于同一快照下一次性查询的结果，**不重不漏**；
- `object_set_id` 在整条分页链上保持一致；
- `snapshot_id` 变化时，旧 token 失效并返回 `TOKEN_EXPIRED`，由调用方重新查询；
- 底层源系统在分页期间发生变更时，以快照为准；无法提供快照的源必须降级为一次性查询或明确标记 `consistent=false`。

## 7. 组合与并集

1. filter → search-around → filter 可串联，后一步以前一步的 Object Set 为输入；
2. 同一 `object_set_id` 上的 filter 产生新集合，新 `object_set_id` 包含父集合指纹；
3. 两个同根类型集合可以求并集 / 交集 / 差集，结果按对象 ID 去重并重新排序；
4. 跨类型集合只允许并集，不允许按属性 pivot 混合类型；
5. 集合运算不改变对象事实，不写回源系统；
6. 集合运算的结果必须满足：分页并集 = 一次性结果，集合运算后再分页同样成立。

## 8. 权限与类型范围

- Object Set 必须继承会话的 `allowed_group_ids` 与类型过滤；
- 锚点对象越权时按 `OBJECT_NOT_FOUND` 处理，不泄露对象是否存在；
- filter / pivot / aggregate 的属性必须同时通过类型白名单与权限检查；
- 当前 P0-16 尚未落地，v1 先定义接口预留，不允许在查询层绕过未来权限注入。

## 9. 错误码

| 错误码 | 触发条件 |
|---|---|
| `SCHEMA_ERROR` | 未定义类型、属性、关系、操作符或方向 |
| `ARGUMENT_ERROR` | 缺少锚点、参数类型错误、空数组用于 `in` / `not_in` |
| `OBJECT_NOT_FOUND` | 锚点对象不存在或不可见 |
| `TOKEN_INVALID` | token 损坏、版本不支持 |
| `TOKEN_EXPIRED` | token 过期或快照失效 |
| `TOKEN_QUERY_MISMATCH` | token 与当前查询不匹配 |
| `SET_TYPE_MISMATCH` | 非法集合运算或跨类型 pivot |

## 10. G2-03 验收口径

1. `freight/object_set.py` 提供 filter / pivot / search-around / aggregate / 分页的统一集合对象；
2. `ontology.query` 与 `ontology.search_around` 返回 `object_set_id`、`count`、`next_page_token`（有下一页时）；
3. search-around 返回 `link_type_summary`，与 `relations` 计数一致；
4. 对同一查询：逐页取完的并集 == 一次性 `limit` 取完的结果，对象 ID 不重不漏；
5. 重复锚点、环形关系、跨跳重复邻居只保留一次；
6. 非法 token、过期 token、查询被修改均被拒绝；
7. 所有结果带 `snapshot_id` 与 `semantic_version`，便于回放与审计；
8. 现有 18 个事实类与 568 条关系定义不因 Object Set 改造发生存储位置变化。