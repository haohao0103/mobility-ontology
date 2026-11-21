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

import base64
import hashlib
import hmac
import json
import math
import os
import time
from typing import Any

from . import projection, projection_v2
from .object_set import object_set_id as _object_set_id, query_digest
from .semantic import BusinessReject

PAGE_TOKEN_VERSION = 1
PAGE_TOKEN_TTL = int(os.environ.get("OBJECT_SET_PAGE_TTL", "3600"))
PAGE_SECRET = os.environ.get(
    "OBJECT_SET_PAGE_SECRET", "ontology-object-set-demo-secret").encode()


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def query_hash(query: dict) -> str:
    """规范化查询指纹：token 本身不参与，其余查询体（含 limit）全部参与。"""
    body = {k: v for k, v in query.items() if k != "page"}
    page = dict(query.get("page") or {})
    page.pop("token", None)
    if page:
        body["page"] = page
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def encode_page_token(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode()
    body = _b64e(raw)
    sig = hmac.new(PAGE_SECRET, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def decode_page_token(token: str) -> dict:
    if not isinstance(token, str) or token.count(".") != 1:
        raise QueryError("分页 token 损坏", "TOKEN_INVALID")
    body, sig = token.split(".", 1)
    expect = hmac.new(PAGE_SECRET, body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expect):
        raise QueryError("分页 token 签名不合法", "TOKEN_INVALID")
    try:
        payload = json.loads(_b64d(body).decode())
    except (ValueError, UnicodeDecodeError) as exc:
        raise QueryError("分页 token 内容损坏", "TOKEN_INVALID") from exc
    if not isinstance(payload, dict):
        raise QueryError("分页 token 结构不合法", "TOKEN_INVALID")
    if payload.get("v") != PAGE_TOKEN_VERSION:
        raise QueryError(
            f"不支持的分页 token 版本: {payload.get('v')}",
            "TOKEN_INVALID", {"supported": PAGE_TOKEN_VERSION})
    if int(payload.get("exp", 0)) < int(time.time()):
        raise QueryError("分页 token 已过期", "TOKEN_EXPIRED")
    return payload

OPS = {
    "eq", "neq", "gt", "gte", "lt", "lte",
    "in", "nin", "contains", "startswith", "endswith", "exists",
}
COMBO_KEYS = {"$and", "$or", "$not"}
MAX_DEPTH = 32
NUMERIC_TYPES = {"integer", "number", "decimal"}


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

def _ids_sql(ids: set[str]) -> str:
    return ",".join("?" for _ in ids) if ids else "NULL"


SET_KEYS = {
    "from", "ids", "followLink", "union", "intersect", "subtract", "where",
    # E2+ 完整代数与平台算子
    "symmetric_difference", "product", "pivot", "join", "fromView", "geo",
}


class ObjectQueryEngine:
    """组合式查询的校验 + 求值。sem: SemanticLayer；ext: 扩展注册表（可选）。"""

    def __init__(self, sem, ext=None, security=None):
        self.sem = sem
        self.ext = ext
        self.security = security
        # 派生对象类型：命名查询视图（Palantir derived object type）
        self.derived_types: dict[str, dict] = {
            d["id"]: d for d in sem.raw.get("derived_types", [])
        }
        # 本轮求值是否吃到了投影加速（可观测性：没吃到就是全表扫描）
        self.last_projection_hit = False
        # 投影不新鲜导致回退（可观测性：出现说明写入口被绕过或库没回填）
        self.last_projection_stale = False
        # OSv2 自适应计划选路结果（可观测性）
        self.last_plan = projection_v2.PLAN_FULL_SCAN

    def _derived_of(self, type_id: str, stack: tuple = ()) -> dict | None:
        """取派生类型定义；带循环检测（派生链不得指回自身）。"""
        d = self.derived_types.get(type_id)
        if d is None:
            return None
        if type_id in stack:
            raise QueryError(
                f"派生类型循环引用: {' -> '.join(stack + (type_id,))}",
                "DERIVED_CYCLE")
        return d

    def _props_of(self, class_id: str) -> dict[str, str | None]:
        derived = self.derived_types.get(class_id)
        if derived is not None:
            out: dict[str, str | None] = {
                p: None for p in derived.get("properties", [])}
            if self.ext is not None:
                for p in out:
                    pt = self.ext.property_types.get(p)
                    if pt is not None:
                        out[p] = pt.get("value_type")
            return out
        cls = self.sem.class_of(class_id)
        out = {p: None for p in cls.get("properties", [])}
        if self.ext is not None:
            for p in out:
                pt = self.ext.property_types.get(p)
                if pt is not None:
                    out[p] = pt.get("value_type")
        return out

    def validate_set(self, node: Any, depth: int = 0, _stack: tuple = ()) -> str:
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
            derived = self._derived_of(class_id, _stack)
            if derived is not None:
                # 派生类型：递归校验 derivation 树（循环检测靠栈）
                self.validate_set(derived["derivation"], depth + 1,
                                  _stack + (class_id,))
            else:
                self.sem.class_of(class_id)  # 未知类型 -> OntologyViolation
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
            at = spec.get("at")
            if direction not in ("out", "in"):
                raise QueryError("followLink.direction 只能是 out/in", "SET_INVALID")
            if at is not None and not isinstance(at, str):
                raise QueryError("followLink.at 必须是 ISO 时间字符串", "SET_INVALID")
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
        for algebra in ("symmetric_difference", "product"):
            if algebra in node:
                operands = node[algebra]
                if not isinstance(operands, list) or len(operands) != 2:
                    raise QueryError(f"{algebra} 需要两个集合", "SET_INVALID")
                for sub in operands:
                    self.validate_set(sub, depth + 1)
                return "*"
        if "pivot" in node:
            spec = node["pivot"]
            if not isinstance(spec, dict) or "set" not in spec or "byLink" not in spec:
                raise QueryError("pivot 需要 set 与 byLink", "SET_INVALID")
            link = self.sem.relations.get(spec["byLink"])
            if link is None:
                raise QueryError(f"pivot 引用未定义关系: {spec['byLink']}", "LINK_UNKNOWN")
            self.validate_set(spec["set"], depth + 1)
            return link["range"] if spec.get("direction", "out") == "out" else link["domain"]
        if "join" in node:
            spec = node["join"]
            if not isinstance(spec, dict) or "left" not in spec or "right" not in spec:
                raise QueryError("join 需要 left 与 right", "SET_INVALID")
            if not isinstance(spec.get("on"), list) or not spec["on"]:
                raise QueryError("join.on 必须是非空数组", "SET_INVALID")
            if spec.get("type", "inner") not in ("inner", "left"):
                raise QueryError("join.type 只能是 inner/left", "SET_INVALID")
            self.validate_set(spec["left"], depth + 1)
            self.validate_set(spec["right"], depth + 1)
            return "*"
        if "fromView" in node:
            view_id = node["fromView"]
            if not isinstance(view_id, str) or not view_id:
                raise QueryError("fromView 必须是视图 ID 字符串", "SET_INVALID")
            return "*"
        if "geo" in node:
            spec = node["geo"]
            if not isinstance(spec, dict) or not {"lat0", "lon0", "lat1", "lon1"} <= set(spec):
                raise QueryError("geo 需要 lat0/lon0/lat1/lon1", "SET_INVALID")
            return "*"
        raise QueryError(
            "集合表达式缺 from/ids/followLink/union/intersect/subtract/"
            "symmetric_difference/product/pivot/join/fromView/geo", "SET_INVALID")

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
        unknown = set(page) - {"limit", "offset", "token"}
        if unknown:
            raise QueryError(
                f"page 未知键: {', '.join(sorted(unknown))}", "QUERY_INVALID",
                {"allowed": ["limit", "offset", "token"]},
            )
        if "limit" in page and (not isinstance(page["limit"], int)
                                or not 1 <= page["limit"] <= 500):
            raise QueryError("page.limit 必须在 1..500", "QUERY_INVALID")
        if "offset" in page and (not isinstance(page["offset"], int)
                                or page["offset"] < 0):
            raise QueryError("page.offset 必须是非负整数", "QUERY_INVALID")
        if "token" in page and not isinstance(page["token"], str):
            raise QueryError("page.token 必须是字符串", "QUERY_INVALID")
        if "token" in page and agg is not None:
            raise QueryError("聚合查询不支持分页 token", "QUERY_INVALID")

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
            # 派生类型用其声明的属性表（含跨源类型并集）
            if main_type in self.derived_types:
                allowed = self.derived_types[main_type].get("properties", [])
                if prop not in allowed:
                    raise QueryError(
                        f"聚合属性 {prop} 不属于派生类型 {main_type}",
                        "AGGREGATE_PROP_UNKNOWN", {"prop": prop},
                    )
            else:
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
    def _resolve_set(self, node: dict, store, depth: int = 0,
                     _stack: tuple = ()) -> list[dict]:
        """求值集合表达式 -> [{object_id, type, props}]（未过滤行级安全）。"""
        if depth > MAX_DEPTH:
            raise QueryError(f"集合嵌套超过 {MAX_DEPTH} 层", "QUERY_DEPTH_EXCEEDED")
        rows: list[dict]
        if "from" in node:
            type_id = node["from"]
            derived = self._derived_of(type_id, _stack)
            if derived is not None:
                # 派生类型：递归求值 derivation 树（行级安全在 execute 层统一过滤）
                rows = self._resolve_set(derived["derivation"], store,
                                         depth + 1, _stack + (type_id,))
            else:
                rows = self._rows_of_type(store, type_id, node.get("where"))
            where = node.get("where")
            if where is not None:
                # 投影只负责收窄候选集；语义正确性必须在这里复算一遍。
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
            at = spec.get("at")
            self.sem.relations[link]  # 已过校验
            if at is None:
                all_links = store.all(
                    "SELECT subject_id, object_id FROM relations "
                    "WHERE predicate=? AND valid_to IS NULL", (link,))
            else:
                # E6 时态回放：只取 at 时刻活跃的关系（valid_from <= at < valid_to）
                all_links = store.all(
                    "SELECT subject_id, object_id FROM relations "
                    "WHERE predicate=? "
                    "AND (valid_from IS NULL OR valid_from <= ?) "
                    "AND (valid_to IS NULL OR valid_to > ?)", (link, at, at))
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
        elif "symmetric_difference" in node:
            first, second = (
                self._resolve_set(sub, store, depth + 1)
                for sub in node["symmetric_difference"])
            a = {r["object_id"]: r for r in first}
            b = {r["object_id"]: r for r in second}
            rows = [r for oid, r in a.items() if oid not in b] \
                + [r for oid, r in b.items() if oid not in a]
        elif "product" in node:
            first, second = (
                self._resolve_set(sub, store, depth + 1)
                for sub in node["product"])
            rows = []
            for l in first:
                for r in second:
                    if l["object_id"] == r["object_id"]:
                        continue
                    merged = dict(l)
                    merged["object_id"] = f"{l['object_id']}~{r['object_id']}"
                    merged["props"] = {
                        **{f"left.{k}": v for k, v in l["props"].items()},
                        **{f"right.{k}": v for k, v in r["props"].items()},
                    }
                    rows.append(merged)
        elif "pivot" in node:
            spec = node["pivot"]
            start = self._resolve_set(spec["set"], store, depth + 1)
            link, direction = spec["byLink"], spec.get("direction", "out")
            ids = {r["object_id"] for r in start}
            if direction == "out":
                linked = store.all(
                    "SELECT object_id FROM relations WHERE predicate=? "
                    "AND subject_id IN (%s) AND valid_to IS NULL" % _ids_sql(ids),
                    (link, *ids))
            else:
                linked = store.all(
                    "SELECT subject_id FROM relations WHERE predicate=? "
                    "AND object_id IN (%s) AND valid_to IS NULL" % _ids_sql(ids),
                    (link, *ids))
            rows = []
            id_key = "object_id" if direction == "out" else "subject_id"
            for item in linked:
                r = self._row(store, item[id_key])
                if r is not None:
                    rows.append(r)
            dedup = {}
            for r in rows:
                dedup.setdefault(r["object_id"], r)
            rows = list(dedup.values())
        elif "join" in node:
            spec = node["join"]
            left = self._resolve_set(spec["left"], store, depth + 1)
            right = self._resolve_set(spec["right"], store, depth + 1)
            pairs = spec["on"]
            rows = []
            for l in left:
                matched = [r for r in right if all(
                    l["props"].get(p["left"]) == r["props"].get(p["right"])
                    for p in pairs)]
                if not matched:
                    if spec.get("type", "inner") == "left":
                        merged = dict(l)
                        merged["props"] = {
                            **{f"left.{k}": v for k, v in l["props"].items()},
                        }
                        rows.append(merged)
                    continue
                for r in matched:
                    merged = dict(l)
                    merged["object_id"] = f"{l['object_id']}~{r['object_id']}"
                    merged["props"] = {
                        **{f"left.{k}": v for k, v in l["props"].items()},
                        **{f"right.{k}": v for k, v in r["props"].items()},
                    }
                    rows.append(merged)
        elif "fromView" in node:
            rows = [
                {"object_id": v["object_id"], "type": v.get("type", ""),
                 "name": v.get("name", ""), "status": v.get("status"),
                 "props": v.get("props", {}), "view": v}
                for v in projection_v2.view_rows(store.conn, node["fromView"])]
        elif "geo" in node:
            ids = projection_v2.geo_candidates(store.conn, node["geo"])
            rows = []
            for oid in ids or []:
                r = self._row(store, oid)
                if r is not None:
                    rows.append(r)
        else:
            raise QueryError("集合表达式缺算子", "SET_INVALID")
        where = node.get("where")
        if where is not None:
            rows = [r for r in rows if eval_predicate(r["props"], where)]
        return rows

    def _rows_of_type(self, store, class_id: str, where: dict | None = None) -> list[dict]:
        """取某类型的对象行；where 可先经投影收窄候选集。

        收窄只是**减少反序列化行数**。正确性边界：复算能滤掉多余候选，
        但救不回被漏掉的候选，所以 `_projected_ids` 必须先过新鲜度闸门，
        不新鲜就整条回退全表扫描。
        """
        candidate_ids = self._projected_ids(store, class_id, where)
        if candidate_ids is None:
            rows = store.all("SELECT * FROM objects WHERE type=?", (class_id,))
        else:
            self.last_projection_hit = True
            if not candidate_ids:
                return []
            placeholders = ",".join("?" * len(candidate_ids))
            rows = store.all(
                f"SELECT * FROM objects WHERE type=? AND object_id IN ({placeholders})",
                (class_id, *candidate_ids),
            )
        out = []
        for r in rows:
            out.append({
                "object_id": r["object_id"], "type": r["type"],
                "name": r["name"], "status": r["status"],
                "props": json.loads(r["props"]),
            })
        return out

    # 投影能支撑的算子：等值 / 集合 / 数值区间。其余（neq/nin/contains/模糊匹配/
    # exists）一律回退全表——用投影硬凑这些算子只会得到近似结果，而近似就是错。
    _PROJECTION_OPS = {"eq", "in", "gt", "gte", "lt", "lte"}

    def _projected_ids(self, store, class_id: str,
                       where: dict | None) -> list[str] | None:
        """用 object_props 投影求候选 object_id；不可用时返回 None（回退全扫描）。

        只处理「扁平 AND」形态：{prop: {op: value}} 且不含 $and/$or/$not。
        组合谓词涉及集合代数，交给上层逐行求值，不在这里猜测语义。
        """
        if not where or not hasattr(store, "all"):
            return None
        if any(k in COMBO_KEYS for k in where):
            return None
        # 新鲜度闸门：不新鲜就不要用投影收窄，否则会静默少行。
        # 走 store 的线程本地只读连接，绝不在读线程里碰写连接。
        checker = getattr(store, "projection_is_fresh", None)
        if checker is not None and not checker():
            self.last_projection_stale = True
            return None
        clauses: list[str] = []
        args: list = []
        for prop, spec in where.items():
            if not isinstance(spec, dict):
                spec = {"eq": spec}
            for op, value in spec.items():
                op = op.lstrip("$")
                if op not in self._PROJECTION_OPS:
                    return None
                if op == "in":
                    if not isinstance(value, list) or not value:
                        return None
                elif value is None:
                    return None
                kind, num, text = projection._classify(
                    value if op != "in" else value[0])
                if kind not in ("num", "str"):
                    return None
                col = "num_value" if kind == "num" else "str_value"
                base = ("SELECT object_id FROM object_props "
                        "WHERE class=? AND prop=? AND value_kind=?")
                if op == "eq":
                    clauses.append(base + f" AND {col}=?")
                    args += [class_id, prop, kind, num if kind == "num" else text]
                elif op in ("gt", "gte", "lt", "lte"):
                    if kind != "num":
                        return None
                    sym = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
                    clauses.append(base + f" AND {col}{sym}?")
                    args += [class_id, prop, kind, num]
                else:  # in
                    kinds = {projection._classify(v)[0] for v in value}
                    if kinds != {kind}:
                        return None
                    ph = ",".join("?" * len(value))
                    clauses.append(base + f" AND {col} IN ({ph})")
                    args += [class_id, prop, kind]
                    if kind == "num":
                        args += [projection._classify(v)[1] for v in value]
                    else:
                        args += [projection._classify(v)[2] for v in value]
        if not clauses:
            return None
        sql = " INTERSECT ".join(clauses)
        try:
            return [r["object_id"] for r in store.all(sql, tuple(args))]
        except Exception:  # noqa: BLE001 - 投影表缺失/结构不符一律回退
            return None

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

    # -------------------------------------------------- 快照与游标
    @staticmethod
    def _snapshot_id(store) -> str:
        """数据快照指纹：对象与关系全量规范化哈希。

        分页期间快照变化 -> 旧 token 失效（TOKEN_EXPIRED），
        保证「分页并集 = 同一快照一次性结果」。
        """
        digest = hashlib.sha256()
        for table in ("objects", "relations"):
            rows = [dict(r) for r in store.all(f"SELECT * FROM {table}")]
            rows.sort(key=lambda r: json.dumps(
                r, sort_keys=True, ensure_ascii=False, default=str))
            digest.update(table.encode())
            digest.update(json.dumps(
                rows, sort_keys=True, ensure_ascii=False,
                separators=(",", ":"), default=str).encode())
        return digest.hexdigest()[:16]

    @staticmethod
    def _row_cursor(row: dict, order: list[dict]) -> dict:
        """游标：排序键 + 对象 ID 兜底，保证 keyset 推进不重不漏。"""
        return {
            "id": row["object_id"],
            "key": [row["props"].get(item["prop"]) for item in order],
        }

    @staticmethod
    def _cursor_index(rows: list[dict], cursor: dict,
                      order: list[dict]) -> int | None:
        oid = cursor.get("id")
        for index, row in enumerate(rows):
            if row["object_id"] == oid:
                if order:
                    key = [row["props"].get(item["prop"]) for item in order]
                    if key != cursor.get("key"):
                        return None
                return index
        return None

    # -------------------------------------------------- 主执行
    def execute(self, query: dict, store, ctx: Any = None) -> dict:
        """校验 -> 求值 -> 安全过滤 -> 排序 -> 聚合/分页 -> 输出。"""
        self.validate(query)
        set_node = query["set"]
        main_type = self.validate_set(set_node)
        checker = getattr(store, "projection_is_fresh", None)
        fresh = bool(checker()) if checker is not None else False
        self.last_plan = projection_v2.choose_plan(
            set_node, query.get("aggregate"), store.conn, fresh)
        rows = self._resolve_set(set_node, store)
        snapshot_id = self._snapshot_id(store)

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

        identity = {k: v for k, v in query.items() if k != "page"}
        set_id = _object_set_id(
            {"q": identity}, str(self.sem.raw.get("meta", {}).get("version", "0")),
            query_digest({"query": identity}),
        )

        agg = query.get("aggregate")
        if agg is not None:
            return self._aggregate(agg, rows, set_id, self.last_plan)

        page = query.get("page") or {}
        limit = page.get("limit", 100)
        start = page.get("offset", 0)
        token = page.get("token")
        qh = query_hash(query)
        if token:
            data = decode_page_token(token)
            if data.get("qh") != qh:
                raise QueryError(
                    "分页 token 与当前查询不一致（filter/order/limit 不得修改）",
                    "TOKEN_QUERY_MISMATCH")
            if data.get("sid") != set_id:
                raise QueryError("分页 token 不属于当前集合", "TOKEN_QUERY_MISMATCH")
            if data.get("snap") != snapshot_id:
                raise QueryError(
                    "分页快照已失效，请重新发起查询", "TOKEN_EXPIRED",
                    {"token_snapshot": data.get("snap"),
                     "current_snapshot": snapshot_id})
            index = self._cursor_index(rows, data.get("last") or {}, order)
            start = index + 1 if index is not None else int(data.get("off", start))

        page_rows = rows[start: start + limit]
        out_objects = []
        for r in page_rows:
            props = r["props"]
            if self.security is not None and ctx is not None:
                props = self.security.mask_properties(r["type"], props, ctx)
            item = {
                "object_id": r["object_id"], "type": r["type"],
                "name": r["name"], "status": r["status"], "props": props,
            }
            if "view" in r:
                item["view"] = r["view"]
            out_objects.append(item)

        next_page_token = None
        next_offset = start + len(page_rows)
        if page_rows and next_offset < len(rows):
            next_page_token = encode_page_token({
                "v": PAGE_TOKEN_VERSION,
                "qh": qh,
                "sid": set_id,
                "snap": snapshot_id,
                "last": self._row_cursor(page_rows[-1], order),
                "off": next_offset,
                "lim": limit,
                "exp": int(time.time()) + PAGE_TOKEN_TTL,
            })
        return {
            "set_id": set_id,
            "object_set_id": set_id,
            "snapshot_id": snapshot_id,
            "total": len(rows),
            "offset": start,
            "limit": limit,
            "objects": out_objects,
            "next_page_token": next_page_token,
            "consistent": True,
            "plan": self.last_plan,
        }

    @staticmethod
    def _aggregate(agg: dict, rows: list[dict], set_id: str, plan: str = "full_scan") -> dict:
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
            "plan": plan,
        }