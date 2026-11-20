"""最小 Ontology SQL frontend：SQL 编译到现有查询树 IR。

对标 Palantir Ontology SQL（2026-11-12 GA）的最小可用形态，设计红线：
  **SQL 只做编译器，不新建执行引擎**——产物直接进 ObjectQueryEngine.execute，
  TBox 校验（类/属性/类型/算子/链接方向）、行级列级安全、时态回放、聚合全部复用。
  非法 SQL 因此照旧被 TBox 拦截（400 结构化报错），不会绕过本体。

支持子集：
  SELECT [* | prop, ... | COUNT(*)/SUM(p)/AVG(p)/MIN(p)/MAX(p)]
  FROM 对象类型 | 派生类型 | 链接路径 (a:TypeA)-[:link]->(b:TypeB)[-[:link2]->(c:TypeC)]
  [WHERE 谓词]  =, !=, <>, >, >=, <, <=, IN, NOT IN, LIKE, BETWEEN, IS [NOT] NULL,
                AND, OR, NOT, 括号
  [GROUP BY prop, ...]
  [ORDER BY prop [ASC|DESC], ...]
  [LIMIT n [OFFSET m]]

参数：:name（值参数化——编译期以 params 中的值内联，不做字符串拼接）。

示例：
  SELECT * FROM Waybill WHERE status = 'DELIVERED' LIMIT 10
  SELECT COUNT(*), SUM(actual_amount) FROM FeeItem
      WHERE status = :st GROUP BY fee_type
  SELECT f.fee_label, f.actual_amount
      FROM (w:Waybill)-[:waybill_produces_fee]->(f:FeeItem)
      WHERE w.waybill_id = :wb ORDER BY f.actual_amount DESC
"""
from __future__ import annotations

import re
from typing import Any


class SqlCompileError(Exception):
    """SQL 编译错误（语法/不支持构造）。400 层结构化报错。"""

    def __init__(self, message: str, code: str = "SQL_SYNTAX_ERROR",
                 pos: int | None = None):
        super().__init__(message)
        self.code = code
        self.pos = pos

    def __str__(self) -> str:  # noqa: D105
        base = super().__str__()
        return f"{base}（位置 {self.pos}）" if self.pos is not None else base


# ---------------------------------------------------------------- 词法
KEYWORDS = {
    "SELECT", "FROM", "WHERE", "GROUP", "BY", "ORDER", "ASC", "DESC",
    "LIMIT", "OFFSET", "AND", "OR", "NOT", "IN", "LIKE", "BETWEEN",
    "IS", "NULL", "AS", "COUNT", "SUM", "AVG", "MIN", "MAX",
}

TOKEN_RE = re.compile(r"""
    (?P<WS>\s+)
  | (?P<PARAM>:[A-Za-z_][A-Za-z0-9_]*)
  | (?P<NUMBER>-?\d+(?:\.\d+)?)
  | (?P<STRING>'(?:[^']|'')*')
  | (?P<OP><>|!=|>=|<=|=|<|>)
  | (?P<DASH>-)
  | (?P<LBRACKET>\[|\])
  | (?P<PUNCT>[(),*;.])
  | (?P<IDENT>[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*)
""", re.VERBOSE)


class Tok:
    __slots__ = ("kind", "value", "pos")

    def __init__(self, kind: str, value: Any, pos: int):
        self.kind = kind
        self.value = value
        self.pos = pos

    def __repr__(self) -> str:  # noqa: D105
        return f"Tok({self.kind},{self.value!r})"


def tokenize(sql: str) -> list[Tok]:
    out: list[Tok] = []
    i = 0
    while i < len(sql):
        m = TOKEN_RE.match(sql, i)
        if not m:
            raise SqlCompileError(f"无法识别的字符: {sql[i]!r}",
                                  "SQL_SYNTAX_ERROR", i)
        kind = m.lastgroup
        raw = m.group()
        if kind != "WS":
            if kind == "IDENT" and raw.upper() in KEYWORDS:
                out.append(Tok("KW", raw.upper(), i))
            elif kind == "IDENT":
                out.append(Tok("IDENT", raw, i))
            elif kind == "STRING":
                out.append(Tok("STRING", raw[1:-1].replace("''", "'"), i))
            elif kind == "NUMBER":
                val = float(raw) if "." in raw else int(raw)
                out.append(Tok("NUMBER", val, i))
            elif kind == "PARAM":
                out.append(Tok("PARAM", raw[1:], i))
            elif kind == "OP":
                out.append(Tok("OP", raw, i))
            elif kind == "DASH":
                out.append(Tok("DASH", raw, i))
            elif kind == "LBRACKET":
                out.append(Tok("LBRACKET", raw, i))
            elif kind == "PUNCT":
                out.append(Tok("PUNCT", raw, i))
        i = m.end()
    out.append(Tok("EOF", None, len(sql)))
    return out


# ---------------------------------------------------------------- 语法
AGG_FUNCS = {"COUNT", "SUM", "AVG", "MIN", "MAX"}
CMP_MAP = {"=": "$eq", "!=": "$neq", "<>": "$neq",
           ">": "$gt", ">=": "$gte", "<": "$lt", "<=": "$lte"}


class Parser:
    def __init__(self, tokens: list[Tok], params: dict[str, Any] | None):
        self.toks = tokens
        self.i = 0
        self.params = params or {}

    # ---- 基础
    def peek(self, k: int = 0) -> Tok:
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def at(self, kind: str, value: Any = None) -> bool:
        t = self.peek()
        return t.kind == kind and (value is None or t.value == value)

    def eat(self, kind: str, value: Any = None) -> Tok:
        t = self.peek()
        if t.kind != kind or (value is not None and t.value != value):
            raise SqlCompileError(
                f"期望 {value or kind}，实际 {t.value!r}", "SQL_SYNTAX_ERROR", t.pos)
        self.i += 1
        return t

    def eat_colon_ident(self) -> str:
        """消费 `:IDENT` 形式。注意词法层 `:Type` 会被识别成 PARAM，
        这里在 Cypher 路径语境拆开（路径类型标注 vs 参数值）。"""
        t = self.peek()
        if t.kind == "PUNCT" and t.value == ":":
            self.i += 1
            return self.eat("IDENT").value
        if t.kind == "PARAM":
            self.i += 1
            return t.value
        raise SqlCompileError(f"期望 :，实际 {t.value!r}",
                              "SQL_SYNTAX_ERROR", t.pos)

    def maybe(self, kind: str, value: Any = None) -> bool:
        if self.at(kind, value):
            self.i += 1
            return True
        return False

    # ---- 语句
    def parse(self) -> dict:
        query = self.parse_select()
        self.eat("EOF")
        return query

    def parse_select(self) -> dict:
        self.eat("KW", "SELECT")
        select = self.parse_select_list()
        self.eat("KW", "FROM")
        set_node, aliases, path_aliases = self.parse_from()
        query: dict = {"set": set_node}
        if self.maybe("KW", "WHERE"):
            query["where"] = self.parse_expr()
        agg = self.parse_aggregate(select)
        if agg is not None:
            query["aggregate"] = agg
        if self.maybe("KW", "ORDER"):
            self.eat("KW", "BY")
            query["orderBy"] = self.parse_order()
        page: dict = {}
        if self.maybe("KW", "LIMIT"):
            page["limit"] = int(self.eat("NUMBER").value)
        if self.maybe("KW", "OFFSET"):
            page["offset"] = int(self.eat("NUMBER").value)
        if page:
            query["page"] = page
        query["_select"] = select
        query["_aliases"] = aliases
        query["_path"] = path_aliases
        return query

    def parse_select_list(self) -> list[dict]:
        items: list[dict] = []
        if self.maybe("PUNCT", "*"):
            return [{"kind": "star"}]
        while True:
            if self.at("KW") and self.peek().value in AGG_FUNCS:
                fn = self.eat("KW").value
                self.eat("PUNCT", "(")
                if self.maybe("PUNCT", "*"):
                    prop = "*"
                else:
                    prop = self.parse_prop_ref()
                self.eat("PUNCT", ")")
                self.maybe("KW", "AS")
                if self.at("IDENT"):
                    self.eat("IDENT")
                items.append({"kind": "agg", "func": fn, "prop": prop})
            else:
                prop = self.parse_prop_ref()
                self.maybe("KW", "AS")
                if self.at("IDENT"):
                    self.eat("IDENT")
                items.append({"kind": "prop", "prop": prop})
            if not self.maybe("PUNCT", ","):
                break
        return items

    def parse_prop_ref(self) -> str:
        """prop 或 alias.prop（返回 prop，别名在解析时按 from 别名表消解）。"""
        t = self.eat("IDENT")
        name = t.value
        if self.maybe("PUNCT", "."):
            sub = self.eat("IDENT").value
            return f"{name}.{sub}"
        return name

    def parse_from(self) -> tuple[dict, dict[str, str]]:
        # 路径： (a:TypeA)-[:link]->(b:TypeB)
        if self.at("PUNCT", "("):
            return self.parse_link_path()
        t = self.eat("IDENT")
        aliases = {"": t.value, t.value: t.value}
        if self.at("IDENT"):
            alias = self.eat("IDENT").value
            aliases[alias] = t.value
        return {"from": t.value}, aliases, []

    def parse_link_path(self) -> tuple[dict, dict[str, str]]:
        self.eat("PUNCT", "(")
        alias = self.eat("IDENT").value
        type_id = self.eat_colon_ident()
        self.eat("PUNCT", ")")
        aliases = {alias: type_id}
        path_aliases = [alias]
        # 起点用引擎 from 表达式，多跳链式包 followLink。
        # 路径语法（Cypher 风格）：(a:T)-[:L]->(b:T) 出边；
        #                       (a:T)<-[:L]-(b:T) 入边。
        inner: dict = {"from": type_id}
        while self.at("DASH") or self.at("OP", "<"):
            if self.at("OP", "<"):
                self.eat("OP", "<")
                self.eat("DASH")
                direction = "in"
            else:
                self.eat("DASH")
                direction = "out"
            self.eat("LBRACKET", "[")
            link = self.eat_colon_ident()
            self.eat("LBRACKET", "]")
            self.eat("DASH")
            if direction == "out":
                self.eat("OP", ">")
            self.eat("PUNCT", "(")
            next_alias = self.eat("IDENT").value
            next_type = self.eat_colon_ident()
            self.eat("PUNCT", ")")
            aliases[next_alias] = next_type
            path_aliases.append(next_alias)
            inner = {"followLink": {"set": inner, "link": link,
                                    "direction": direction}}
        return inner, aliases, path_aliases

    def parse_aggregate(self, select: list[dict]) -> dict | None:
        aggs = [s for s in select if s["kind"] == "agg"]
        has_group = self.at("KW", "GROUP")
        if not aggs and not has_group:
            return None
        if not aggs:
            raise SqlCompileError("GROUP BY 需要聚合函数（COUNT/SUM/AVG/MIN/MAX）",
                                  "SQL_UNSUPPORTED", self.peek().pos)
        if len([s for s in select if s["kind"] == "prop"]) and not has_group:
            raise SqlCompileError("聚合与普通列混选需要 GROUP BY",
                                  "SQL_UNSUPPORTED", self.peek().pos)
        agg: dict = {}
        for s in aggs:
            fn = s["func"]
            if fn == "COUNT":
                agg["count"] = True
                continue
            key = fn.lower()
            prop = s["prop"]
            if prop == "*":
                raise SqlCompileError(f"{fn}(*) 不支持，请指定属性",
                                      "SQL_UNSUPPORTED", self.peek().pos)
            agg.setdefault(key, []).append(prop)
        if self.maybe("KW", "GROUP"):
            self.eat("KW", "BY")
            group = []
            while True:
                group.append(self.parse_prop_ref())
                if not self.maybe("PUNCT", ","):
                    break
            agg["group_by"] = group
        return agg

    def parse_order(self) -> list[dict]:
        out: list[dict] = []
        while True:
            prop = self.parse_prop_ref()
            direction = "asc"
            if self.at("KW") and self.peek().value in ("ASC", "DESC"):
                direction = self.eat("KW").value.lower()
            out.append({"prop": prop, "dir": direction})
            if not self.maybe("PUNCT", ","):
                break
        return out

    # ---- 表达式
    def parse_expr(self) -> dict:
        return self.parse_or()

    def parse_or(self) -> dict:
        left = self.parse_and()
        while self.maybe("KW", "OR"):
            right = self.parse_and()
            left = ({"$or": left["$or"] + [right]} if "$or" in left
                    else {"$or": [left, right]})
        return left

    def parse_and(self) -> dict:
        left = self.parse_not()
        while self.maybe("KW", "AND"):
            right = self.parse_not()
            left = ({"$and": left["$and"] + [right]} if "$and" in left
                    else {"$and": [left, right]})
        return left

    def parse_not(self) -> dict:
        if self.maybe("KW", "NOT"):
            return {"$not": self.parse_not()}
        if self.at("PUNCT", "("):
            self.eat("PUNCT", "(")
            node = self.parse_expr()
            self.eat("PUNCT", ")")
            return node
        return self.parse_predicate()

    def parse_predicate(self) -> dict:
        left_tok = self.peek()
        # 左值：属性引用或字面量
        if self.at("IDENT"):
            prop = self.parse_prop_ref()
        else:
            raise SqlCompileError("谓词左侧必须是属性名",
                                  "SQL_SYNTAX_ERROR", left_tok.pos)
        # 保留 alias.prop 完整引用：路径查询需按别名把谓词下推到各层，
        # 非路径查询在 compile_sql 末尾统一 _strip_alias。
        prop_name = prop

        if self.at("OP"):
            op = self.eat("OP").value
            val = self.parse_value()
            return {prop_name: {CMP_MAP[op]: val}}
        if self.maybe("KW", "IN"):
            self.eat("PUNCT", "(")
            vals = [self.parse_value()]
            while self.maybe("PUNCT", ","):
                vals.append(self.parse_value())
            self.eat("PUNCT", ")")
            return {prop_name: {"$in": vals}}
        if self.at("KW", "NOT") and self.peek(1).kind == "KW" \
                and self.peek(1).value == "IN":
            self.i += 2
            self.eat("PUNCT", "(")
            vals = [self.parse_value()]
            while self.maybe("PUNCT", ","):
                vals.append(self.parse_value())
            self.eat("PUNCT", ")")
            return {prop_name: {"$nin": vals}}
        if self.at("KW", "NOT") and self.peek(1).kind == "KW" \
                and self.peek(1).value == "LIKE":
            self.i += 2
            return {"$not": {prop_name: self._like(self.eat("STRING").value)}}
        if self.maybe("KW", "LIKE"):
            return {prop_name: self._like(self.eat("STRING").value)}
        if self.maybe("KW", "BETWEEN"):
            low = self.parse_value()
            self.eat("KW", "AND")
            high = self.parse_value()
            return {"$and": [{prop_name: {"$gte": low}},
                             {prop_name: {"$lte": high}}]}
        if self.maybe("KW", "IS"):
            negate = self.maybe("KW", "NOT")
            self.eat("KW", "NULL")
            return {prop_name: {"$neq" if negate else "$eq": None}}
        raise SqlCompileError("谓词缺少比较运算符",
                              "SQL_SYNTAX_ERROR", self.peek().pos)

    @staticmethod
    def _like(pattern: str) -> dict:
        starts = pattern.startswith("%")
        ends = pattern.endswith("%")
        core = pattern.strip("%")
        if starts and ends:
            return {"$contains": core}
        if ends:
            return {"$startswith": core}
        if starts:
            return {"$endswith": core}
        return {"$eq": core}

    def parse_value(self) -> Any:
        t = self.peek()
        if t.kind in ("STRING", "NUMBER"):
            self.i += 1
            return t.value
        if t.kind == "KW" and t.value == "NULL":
            self.i += 1
            return None
        if t.kind == "PARAM":
            self.i += 1
            if t.value not in self.params:
                raise SqlCompileError(f"缺少参数值: :{t.value}",
                                      "SQL_PARAM_MISSING", t.pos)
            return self.params[t.value]
        raise SqlCompileError(f"不支持的取值: {t.value!r}",
                              "SQL_SYNTAX_ERROR", t.pos)


def _unwrap_layers(node: dict) -> list[dict]:
    """解包链式 followLink -> [最内层(from), ..., 最外层]（与 path_aliases 同序）。"""
    layers: list[dict] = []
    cur = node
    while "followLink" in cur:
        layers.append(cur)
        cur = cur["followLink"]["set"]
    layers.append(cur)
    layers.reverse()
    return layers


def _split_predicates(node: Any, path_aliases: list[str]) -> tuple[
        dict[str, list], list]:
    """把谓词树按别名拆到各层；or/not 跨层不支持（明确报错，不静默）。"""
    leaves: dict[str, list] = {a: [] for a in path_aliases}
    loose: list = []
    last = path_aliases[-1]

    def walk(n: Any) -> None:
        if isinstance(n, dict):
            if "$and" in n:
                for sub in n["$and"]:
                    walk(sub)
                return
            if "$or" in n or "$not" in n:
                # 跨层逻辑组合语义歧义 -> 要求拆成 AND
                refs = _aliases_in(n)
                if len(refs) > 1 or (refs and refs - {last}):
                    raise SqlCompileError(
                        "OR/NOT 不能跨路径节点混用，请改为 AND 分别过滤",
                        "SQL_UNSUPPORTED")
                loose.append(n)
                return
            key = next(iter(n))
            alias = key.split(".")[0] if "." in key else None
            bare_key = key.split(".")[-1]
            value = n[key]
            if alias is not None and alias in leaves:
                leaves[alias].append({bare_key: value})
            else:
                leaves[last].append({bare_key: value})

    walk(node)
    out = {a: v for a, v in leaves.items()}
    return out, loose


def _aliases_in(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if k.startswith("$"):
                found |= _aliases_in(v)
            elif "." in k:
                found.add(k.split(".")[0])
            else:
                found |= _aliases_in(v)
    elif isinstance(node, list):
        for x in node:
            found |= _aliases_in(x)
    return found


def _pushdown_where(query: dict, aliases: dict[str, str],
                    path_aliases: list[str]) -> None:
    layers = _unwrap_layers(query["set"])
    if len(layers) != len(path_aliases):
        # 结构不符则保守：整体留在顶层（由引擎按最终类型校验）
        query["where"] = _strip_alias(query["where"], aliases)
        return
    per_layer, loose = _split_predicates(query.pop("where"), path_aliases)
    for i, layer in enumerate(layers):
        preds = per_layer.get(path_aliases[i]) or []
        if i == len(layers) - 1:
            preds += loose
        if not preds:
            continue
        merged = (preds[0] if len(preds) == 1 else {"$and": preds})
        existing = layer.get("where")
        layer["where"] = merged if existing is None else {
            "$and": [existing, merged]}


def _strip_alias(node: Any, aliases: dict[str, str]) -> Any:
    """把 prop 的 alias. 前缀消解成裸属性名（引擎谓词用裸属性）。"""
    if isinstance(node, dict):
        out: dict = {}
        for k, v in node.items():
            if k in ("$and", "$or"):
                out[k] = [_strip_alias(x, aliases) for x in v]
            elif k == "$not":
                out[k] = _strip_alias(v, aliases)
            else:
                out[k.split(".")[-1]] = _strip_alias(v, aliases)
        return out
    return node


def compile_sql(sql: str, params: dict[str, Any] | None = None) -> dict:
    """SQL -> 查询树 IR（可直接喂 ObjectQueryEngine.execute）。

    产物含 _select/_aliases 两个元字段（执行后投影用），execute 前需剔除。
    """
    tokens = tokenize(sql)
    parser = Parser(tokens, params)
    query = parser.parse()
    aliases = query.pop("_aliases", {})
    path_aliases = query.pop("_path", []) or []
    select = query.pop("_select", [{"kind": "star"}])
    if "where" in query and path_aliases:
        # Cypher 语义：WHERE 可引用路径上任一节点别名 -> 按别名下推到对应层
        _pushdown_where(query, aliases, path_aliases)
    elif "where" in query:
        query["where"] = _strip_alias(query["where"], aliases)
    if "orderBy" in query:
        query["orderBy"] = [{"prop": o["prop"].split(".")[-1],
                             "dir": o["dir"]} for o in query["orderBy"]]
    if "aggregate" in query:
        agg = query["aggregate"]
        for key in ("sum", "avg", "min", "max"):
            if key in agg:
                agg[key] = [p.split(".")[-1] for p in agg[key]]
        if "group_by" in agg:
            agg["group_by"] = [p.split(".")[-1] for p in agg["group_by"]]
    query["_projection"] = select
    return query


def project(result: dict, projection: list[dict]) -> dict:
    """按 SELECT 列表裁剪返回对象（star 或含聚合时原样返回）。"""
    if not projection or projection[0].get("kind") == "star":
        return result
    if "groups" in result:  # 聚合结果由引擎直接给出
        return result
    wanted = [p["prop"].split(".")[-1] for p in projection
              if p.get("kind") == "prop"]
    if not wanted:
        return result
    out_objects = []
    for o in result.get("objects", []):
        row = {"object_id": o["object_id"], "type": o["type"],
               "name": o.get("name")}
        row.update({k: o.get("props", {}).get(k) for k in wanted})
        out_objects.append(row)
    return {**result, "objects": out_objects, "projection": wanted}
