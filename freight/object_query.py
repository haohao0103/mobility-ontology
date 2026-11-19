"""组合式 Object Set 查询 DSL（对标 Palantir OSDK 组合语义）。

三层结构（E1/E2/E3）：
- E1 谓词层：MongoDB 风格（{prop: {$op: value}}），$and/$or/$not 组合，
  本体期类型校验（未知属性/值类型不匹配/非法算子 -> 结构化拒绝）。
- E2 集合代数：from / ids / followLink / union / intersect / subtract，
  任意节点可挂 where 后过滤；查询树 digest 派生 set_id（沿用 object_set 身份）。
- E3 管道：orderBy 多字段 + count/sum/avg/min/max + group_by。

设计纪律：求值纯确定性（LLM 只能生成查询树、不能求值）；
安全联动（行级过滤影响 total/聚合、列级脱敏在输出侧）。
"""
from __future__ import annotations

import json
import math
from typing import Any

from .object_set import object_set_id as _object_set_id, query_digest
from .semantic import BusinessReject

OPS = {
    "eq", "neq", "gt", "gte", "lt", "lte",
    "in", "nin", "contains", "startswith", "endswith", "exists",
}
COMBO_KEYS = {"$and", "$or", "$not"}
MAX_DEPTH = 32
NUMERIC_TYPES = {"integer", "number"}


class QueryError(BusinessReject):
    """查询树不合法或求值失败。继承 BusinessReject 复用 MCP/REST 错误通道，
    code 结构化返回，不静默。"""

    def __init__(self, message: str, code: str = "QUERY_INVALID", details: dict | None = None):
        super().__init__(message, code=code, details=details)


# ---------------------------------------------------------------- E1 谓词层

def _op_predicates(raw: dict) -> list[tuple[str, str, Any]]:
    """拆出 {prop: {$op: value}} 的谓词项（忽略组合键）。"""
    out: list[tuple[str, str, Any]] = []
    for key, spec in raw.items():
        if key in COMBO_KEYS:
            continue
        if not isinstance(spec, dict):
            # 简写 {prop: value} 等价 $eq
            out.append((key, "eq", spec))
            continue
        for op, value in spec.items():
            out.append((key, op.lstrip("$"), value))
    return out


def _check_value_type(prop_type: str | None, op: str, value: Any) -> None:
    """本体期值类型校验：数值属性的比较算子必须数值，字符串算子必须字符串。

    未注册属性（legacy）跳过——与属性注册表增量模式一致。
    """
    if prop_type is None:
        return
    numeric_op = op in {"gt", "gte", "lt", "lte"}
    string_op = op in {"contains", "startswith", "endswith"}
    is_num = isinstance(value, (int, float)) and not isinstance(value, bool)
    is_str = isinstance(value, str)
    if prop_type in NUMERIC_TYPES and numeric_op and not is_num:
        raise QueryError(
            f"数值属性使用了非数值比较值: {value!r}",
            "PREDICATE_TYPE_MISMATCH",
            {"expected": prop_type, "op": op, "value": value},
        )
    if prop_type == "string" and string_op and not is_str:
        raise QueryError(
            f"字符串属性使用了非字符串匹配值: {value!r}",
            "PREDICATE_TYPE_MISMATCH",
            {"expected": "string", "op": op, "value": value},
        )
    if prop_type in NUMERIC_TYPES and op in {"eq", "neq"} and not (is_num or value is None):
        raise QueryError(
            f"数值属性使用了非数值等值: {value!r}",
            "PREDICATE_TYPE_MISMATCH",
            {"expected": prop_type, "op": op, "value": value},
        )


def validate_predicate(pred: Any, allowed_props: dict[str, str],
                       depth: int = 0) -> None:
    """递归校验谓词树。allowed_props: {prop_name: value_type|None}。"""
    if depth > MAX_DEPTH:
        raise QueryError(f"谓词嵌套超过 {MAX_DEPTH} 层", "QUERY_DEPTH_EXCEEDED")
    if not isinstance(pred, dict) or not pred:
        raise QueryError("谓词必须是非空对象", "PREDICATE_INVALID")
    for combo in COMBO_KEYS & set(pred):
        value = pred[combo]
        if combo == "$not":
            validate_predicate(value, allowed_props, depth + 1)
        else:
            if not isinstance(value, list) or not value:
                raise QueryError(f"{combo} 必须是非空数组", "PREDICATE_INVALID")
            for sub in value:
                validate_predicate(sub, allowed_props, depth + 1)
    for prop, op, value in _op_predicates(pred):
        if prop not in allowed_props:
            raise QueryError(
                f"未知属性: {prop}",
                "PREDICATE_PROP_UNKNOWN",
                {"prop": prop, "allowed": sorted(allowed_props)},
            )
        if op not in OPS:
            raise QueryError(
                f"不支持的操作符: {op}",
                "PREDICATE_OP_UNKNOWN",
                {"op": op, "allowed": sorted(OPS)},
            )
        if op in ("in", "nin") and (not isinstance(value, list) or not value):
            raise QueryError(f"${op} 的值必须是非空数组", "PREDICATE_INVALID")
        _check_value_type(allowed_props[prop], op, value)


def _as_number(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def eval_predicate(props: dict, pred: dict) -> bool:
    """确定性求值（内存，纯函数）。"""
    for combo in COMBO_KEYS & set(pred):
        if combo == "$and":
            if not all(eval_predicate(props, sub) for sub in pred[combo]):
                return False
        elif combo == "$or":
            if not any(eval_predicate(props, sub) for sub in pred[combo]):
                return False
        else:  # $not
            if eval_predicate(props, pred[combo]):
                return False
    for prop, op, value in _op_predicates(pred):
        actual = props.get(prop)
        if op == "exists":
            if bool(actual is not None) != bool(value):
                return False
            continue
        if op == "eq" and actual != value:
            return False
        if op == "neq" and actual == value:
            return False
        if op in ("gt", "gte", "lt", "lte"):
            a, b = _as_number(actual), _as_number(value)
            if a is None or b is None:
                return False
            if op == "gt" and not a > b:
                return False
            if op == "gte" and not a >= b:
                return False
            if op == "lt" and not a < b:
                return False
            if op == "lte" and not a <= b:
                return False
        if op in ("in", "nin") and not isinstance(value, list):
            return False
        if op == "in" and actual not in value:
            return False
        if op == "nin" and actual in value:
            return False
        if op in ("contains", "startswith", "endswith"):
            s = actual if isinstance(actual, str) else (
                json.dumps(actual, ensure_ascii=False) if actual is not None else None)
            if not isinstance(value, str) or s is None:
                return False
            if op == "contains" and value not in s:
                return False
            if op == "startswith" and not s.startswith(value):
                return False
            if op == "endswith" and not s.endswith(value):
                return False
    return True


# ---------------------------------------------------------------- E2 集合代数

SET_KEYS = {"from", "ids", "followLink", "union", "intersect", "subtract", "where"}


class ObjectQueryEngine:
    """组合式查询的校验 + 求值。sem: SemanticLayer；ext: 扩展注册表（可选）。"""

    def __init__(self, sem, ext=None, security=None):
        self.sem = sem
        self.ext = ext
        self.security = security

    # -------------------------------------------------- 校验
    def _props_of(self, class_id: str) -> dict[str, str | None]:
        cls = self.sem.class_of(class_id)
        out: dict[str, str | None] = {p: None for p in cls.get("properties", [])}
        if self.ext is not None:
            for p in out:
                pt = self.ext.property_types.get(p)
                if pt is not None:
                    out[p] = pt.get("value_type")
        return out

    def validate_set(self, node: Any, depth: int = 0) -> str:
        """校验集合表达式，返回该集的（主）类型 ID。"""
        if depth > MAX_DEPTH:
            raise QueryError(f"集合嵌套超过 {MAX_DEPTH} 层", "QUERY_DEPTH_EXCEEDED")
        if not isinstance(node, dict) or not node:
            raise QueryError("集合表达式必须是非空对象", "SET_INVALID")
        unknown = set(node) - SET_KEYS
        if unknown:
            raise QueryError(
                f"集合表达式未知键: {', '.join(sorted(unknown))}",
                "SET_KEY_UNKNOWN", {"allowed": sorted(SET_KEYS - {"where"})},
            )
        if "from" in node:
            class_id = node["from"]
            self.sem.class_of(class_id)  # 未知类型 -> OntologyViolation -> 包一层
            where = node.get("where")
            if where is not None:
                validate_predicate(where, self._props_of(class_id))
            return class_id
        if "ids" in node:
            ids = node["ids"]
            if not isinstance(ids, list) or not ids:
                raise QueryError("ids 必须是非空数组", "SET_INVALID")
            return "*"
        if "followLink" in node:
            spec = node["followLink"]
            if not isinstance(spec, dict):
                raise QueryError("followLink 必须是对象", "SET_INVALID")
            link = spec.get("link", "")
            direction = spec.get("direction", "out")
            if direction not in ("out", "in"):
                raise QueryError("followLink.direction 只能是 out/in", "SET_INVALID")
            rel = self.sem.relations.get(link)
            if rel is None:
                raise QueryError(
                    f"未定义的关系: {link}", "LINK_UNKNOWN",
                    {"link": link},
                )
            start_type = self.validate_set(spec.get("set"), depth + 1)
            if direction == "out" and start_type != "*" and start_type != rel["domain"]:
                raise QueryError(
                    f"followLink 起点类型 {start_type} 与关系 {link} 的定义域 {rel['domain']} 不符",
                    "LINK_DIRECTION_VIOLATION",
                )
            if direction == "in" and start_type != "*" and start_type != rel["range"]:
                raise QueryError(
                    f"followLink 起点类型 {start_type} 与关系 {link} 的值域 {rel['range']} 不符",
                    "LINK_DIRECTION_VIOLATION",
                )
            return rel["range"] if direction == "out" else rel["domain"]
        for algebra in ("union", "intersect"):
            if algebra in node:
                operands = node[algebra]
                if not isinstance(operands, list) or len(operands) < 2:
                    raise QueryError(f"{algebra} 至少需要两个集合", "SET_INVALID")
                for sub in operands:
                    self.validate_set(sub, depth + 1)
                return "*"
        if "subtract" in node:
            spec = node["subtract"]
            if not isinstance(spec, dict) or "left" not in spec or "right" not in spec:
                raise QueryError("subtract 需要 left 与 right", "SET_INVALID")
            self.validate_set(spec["left"], depth + 1)
            self.validate_set(spec["right"], depth + 1)
            return "*"
        raise QueryError("集合表达式缺 from/ids/followLink/union/intersect/subtract", "SET_INVALID")

    def validate(self, query: dict) -> None:
        """校验完整查询体（集合 + 顶层谓词 + 排序 + 聚合 + 分页）。"""
        if not isinstance(query, dict):
            raise QueryError("查询必须是对象", "QUERY_INVALID")
        set_node = query.get("set")
        if set_node is None:
            raise QueryError("查询缺少 set", "QUERY_INVALID")
        main_type = self.validate_set(set_node)
        where = query.get("where")
        if where is not None:
            # 主类型未知（代数结果）时用宽松校验：属性必须属于任一已知类
            if main_type == "*":
                all_props: dict[str, str | None] = {}
                for cid in self.sem.classes:
                    all_props.update(self._props_of(cid))
                validate_predicate(where, all_props)
            else:
                validate_predicate(where, self._props_of(main_type))
        order = query.get("orderBy") or []
        if not isinstance(order, list):
            raise QueryError("orderBy 必须是数组", "QUERY_INVALID")
        for item in order:
            if not isinstance(item, dict) or "prop" not in item:
                raise QueryError("orderBy 每项需要 prop", "QUERY_INVALID")
            if item.get("dir", "asc") not in ("asc", "desc"):
                raise QueryError("orderBy.dir 只能是 asc/desc", "QUERY_INVALID")
        agg = query.get("aggregate")
        if agg is not None:
            self._validate_aggregate(agg, main_type)
        page = query.get("page") or {}
        if not isinstance(page, dict):
            raise QueryError("page 必须是对象", "QUERY_INVALID")
        if "limit" in page and (not isinstance(page["limit"], int)
                                or not 1 <= page["limit"] <= 500):
            raise QueryError("page.limit 必须在 1..500", "QUERY_INVALID")

    def _validate_aggregate(self, agg: Any, main_type: str) -> None:
        if not isinstance(agg, dict) or not agg:
            raise QueryError("aggregate 必须是非空对象", "QUERY_INVALID")
        allowed_keys = {"count", "sum", "avg", "min", "max", "group_by"}
        unknown = set(agg) - allowed_keys
        if unknown:
            raise QueryError(
                f"aggregate 未知键: {', '.join(sorted(unknown))}",
                "AGGREGATE_KEY_UNKNOWN", {"allowed": sorted(allowed_keys)},
            )
        for key in ("sum", "avg", "min", "max"):
            props = agg.get(key)
            if props is not None:
                if not isinstance(props, list) or not props:
                    raise QueryError(f"aggregate.{key} 必须是非空属性数组", "QUERY_INVALID")
                for p in props:
                    self._check_agg_prop(p, main_type, numeric=(key in ("sum", "avg")))
        group = agg.get("group_by")
        if group is not None:
            if not isinstance(group, list) or not group:
                raise QueryError("aggregate.group_by 必须是非空属性数组", "QUERY_INVALID")
            for p in group:
                self._check_agg_prop(p, main_type, numeric=False)

    def _check_agg_prop(self, prop: str, main_type: str, numeric: bool) -> None:
        if main_type != "*":
            cls = self.sem.class_of(main_type)
            if prop not in cls.get("properties", []):
                raise QueryError(
                    f"聚合属性 {prop} 不属于类 {main_type}",
                    "AGGREGATE_PROP_UNKNOWN", {"prop": prop},
                )
            if numeric:
                vt = (self.ext.property_types.get(prop) or {}).get("value_type") \
                    if self.ext else None
                if vt is not None and vt not in NUMERIC_TYPES:
                    raise QueryError(
                        f"sum/avg 属性 {prop} 不是数值类型（{vt}）",
                        "AGGREGATE_PROP_NOT_NUMERIC", {"prop": prop},
                    )

    # -------------------------------------------------- 求值
    def _resolve_set(self, node: dict, store, depth: int = 0) -> list[dict]:
        """求值集合表达式 -> [{object_id, type, props}]（未过滤行级安全）。"""
        if depth > MAX_DEPTH:
            raise QueryError(f"集合嵌套超过 {MAX_DEPTH} 层", "QUERY_DEPTH_EXCEEDED")
        rows: list[dict]
        if "from" in node:
            rows = self._rows_of_type(store, node["from"])
            where = node.get("where")
            if where is not None:
                rows = [r for r in rows if eval_predicate(r["props"], where)]
            return rows
        if "ids" in node:
            rows = []
            for oid in node["ids"]:
                r = self._row(store, oid)
                if r is None:
                    raise QueryError(f"对象不存在: {oid}", "OBJECT_NOT_FOUND")
                rows.append(r)
            where = node.get("where")
            if where is not None:
                rows = [r for r in rows if eval_predicate(r["props"], where)]
            return rows
        if "followLink" in node:
            spec = node["followLink"]
            start_rows = self._resolve_set(spec["set"], store, depth + 1)
            link, direction = spec["link"], spec.get("direction", "out")
            self.sem.relations[link]  # 已过校验
            all_links = store.all(
                "SELECT subject_id, object_id FROM relations "
                "WHERE predicate=? AND valid_to IS NULL", (link,))
            if direction == "out":
                subject_ids = {r["object_id"] for r in start_rows}
                linked = {r["object_id"] for r in all_links
                          if r["subject_id"] in subject_ids}
            else:
                object_ids = {r["object_id"] for r in start_rows}
                linked = {r["subject_id"] for r in all_links
                          if r["object_id"] in object_ids}
            rows = []
            for oid in linked:
                r = self._row(store, oid)
                if r is not None:
                    rows.append(r)
            where = node.get("where")
            if where is not None:
                rows = [r for r in rows if eval_predicate(r["props"], where)]
            return rows
        if "union" in node:
            seen: dict[str, dict] = {}
            for sub in node["union"]:
                for r in self._resolve_set(sub, store, depth + 1):
                    seen.setdefault(r["object_id"], r)
            rows = list(seen.values())
        elif "intersect" in node:
            sets = [
                {r["object_id"] for r in self._resolve_set(sub, store, depth + 1)}
                for sub in node["intersect"]
            ]
            common = set.intersection(*sets)
            first = self._resolve_set(node["intersect"][0], store, depth + 1)
            rows = [r for r in first if r["object_id"] in common]
        elif "subtract" in node:
            left = self._resolve_set(node["subtract"]["left"], store, depth + 1)
            right = {r["object_id"]
                     for r in self._resolve_set(node["subtract"]["right"], store, depth + 1)}
            rows = [r for r in left if r["object_id"] not in right]
        else:
            raise QueryError("集合表达式缺算子", "SET_INVALID")
        return rows

    def _rows_of_type(self, store, class_id: str) -> list[dict]:
        out = []
        for r in store.all("SELECT * FROM objects WHERE type=?", (class_id,)):
            out.append({
                "object_id": r["object_id"], "type": r["type"],
                "name": r["name"], "status": r["status"],
                "props": json.loads(r["props"]),
            })
        return out

    def _row(self, store, object_id: str) -> dict | None:
        r = store.one("SELECT * FROM objects WHERE object_id=?", (object_id,))
        if r is None:
            return None
        return {
            "object_id": r["object_id"], "type": r["type"],
            "name": r["name"], "status": r["status"],
            "props": json.loads(r["props"]),
        }

    @staticmethod
    def _row_subject(store, link: str, object_id: str) -> str | None:
        r = store.one(
            "SELECT subject_id FROM relations WHERE predicate=? AND object_id=? "
            "AND valid_to IS NULL LIMIT 1", (link, object_id))
        return r["subject_id"] if r else None

    # -------------------------------------------------- 主执行
    def execute(self, query: dict, store, ctx: Any = None) -> dict:
        """校验 -> 求值 -> 安全过滤 -> 排序 -> 聚合/分页 -> 输出。"""
        self.validate(query)
        set_node = query["set"]
        main_type = self.validate_set(set_node)
        rows = self._resolve_set(set_node, store)

        where = query.get("where")
        if where is not None:
            rows = [r for r in rows if eval_predicate(r["props"], where)]

        # 行级安全：无标记用户不可见的行，从集合中剔除（影响 total 与聚合）
        if self.security is not None and ctx is not None:
            rows = [r for r in rows
                    if self.security.visible_object(r["type"], r["props"], ctx)]

        # 排序（多字段稳定排序；null 恒排最后，asc/desc 只影响非空部分）
        order = query.get("orderBy") or []
        for item in reversed(order):
            prop, desc = item["prop"], item.get("dir", "asc") == "desc"

            def key(r, prop=prop):
                v = r["props"].get(prop)
                if isinstance(v, bool):
                    return (int(v), "")
                if isinstance(v, (int, float)):
                    return (float(v), "")
                return (0, v if isinstance(v, str)
                        else json.dumps(v, ensure_ascii=False))

            non_null = [r for r in rows if r["props"].get(prop) is not None]
            nulls = [r for r in rows if r["props"].get(prop) is None]
            non_null.sort(key=key, reverse=desc)
            rows = non_null + nulls

        set_id = _object_set_id(
            {"q": query}, str(self.sem.raw.get("meta", {}).get("version", "0")),
            query_digest({"query": query}),
        )

        agg = query.get("aggregate")
        if agg is not None:
            return self._aggregate(agg, rows, set_id)

        page = query.get("page") or {}
        limit = page.get("limit", 100)
        offset = page.get("offset", 0)
        page_rows = rows[offset: offset + limit]
        out_objects = []
        for r in page_rows:
            props = r["props"]
            if self.security is not None and ctx is not None:
                props = self.security.mask_properties(r["type"], props, ctx)
            out_objects.append({
                "object_id": r["object_id"], "type": r["type"],
                "name": r["name"], "status": r["status"], "props": props,
            })
        return {
            "set_id": set_id,
            "total": len(rows),
            "offset": offset,
            "limit": limit,
            "objects": out_objects,
        }

    @staticmethod
    def _aggregate(agg: dict, rows: list[dict], set_id: str) -> dict:
        group_props = agg.get("group_by")
        if group_props:
            groups: dict[tuple, list[dict]] = {}
            for r in rows:
                key = tuple(str(r["props"].get(p)) for p in group_props)
                groups.setdefault(key, []).append(r)
            buckets = [
                (dict(zip(group_props, key)), members)
                for key, members in groups.items()
            ]
            buckets.sort(key=lambda kv: kv[0][group_props[0]])
        else:
            buckets = [({}, rows)]

        def _nums(members: list[dict], prop: str) -> list[float]:
            return [float(m["props"][prop]) for m in members
                    if isinstance(m["props"].get(prop), (int, float))
                    and not isinstance(m["props"][prop], bool)]

        out_groups = []
        for group_values, members in buckets:
            g: dict = {"group_by": group_values, "count": len(members)}
            for key in ("sum", "avg", "min", "max"):
                props = agg.get(key)
                if props is None:
                    continue
                g[key] = {}
                for p in props:
                    nums = _nums(members, p)
                    if key == "sum":
                        g[key][p] = math.fsum(nums) if nums else None
                    elif key == "avg":
                        g[key][p] = math.fsum(nums) / len(nums) if nums else None
                    elif key == "min":
                        g[key][p] = min(nums) if nums else None
                    else:
                        g[key][p] = max(nums) if nums else None
            out_groups.append(g)
        return {
            "set_id": set_id,
            "total": len(rows),
            "groups": out_groups,
        }
