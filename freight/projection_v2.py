"""OSv2 增强档：跨表物化视图 / 地理索引 / 聚合预计算 / 自适应查询计划。

设计纪律（与 freight/projection.py 的轻量档一致）：
1. 本模块只做「加速」，不做「语义」——所有结果仍由 object_query 的 eval_predicate
   和 _resolve_set 复算，索引收窄错了只会少候选，所以每个索引都有新鲜度闸门。
2. 视图 / 空间属性 / 预聚合声明全部来自本体 JSON（承重：只改 JSON 即可启用/改变），
   代码里不硬编码任何业务类型或属性名。
3. 事实仍不进本体库：这些表建在 FactStore 同一个外部事实库里。

本体声明入口（extension JSON 的顶层键，缺省时全部关闭，行为与旧版一致）：
  "materialized_views": [{"id","root","joins":[{"link","direction","as"}],"properties":[...]}]
  "spatial_properties": {"lat":"latitude","lon":"longitude"}   # prop -> 语义角色
  "aggregate_precompute": [{"class","group_by":[...],"metrics":["count","sum:amount"]}]
"""
from __future__ import annotations

import json
import math
import time
from typing import Any

V2_SCHEMA = """
CREATE TABLE IF NOT EXISTS object_view_rows (
  view_id TEXT NOT NULL,
  root_id TEXT NOT NULL,
  payload TEXT NOT NULL,
  PRIMARY KEY (view_id, root_id)
);
CREATE INDEX IF NOT EXISTS idx_view_rows_view ON object_view_rows(view_id);

CREATE TABLE IF NOT EXISTS object_geo (
  object_id TEXT NOT NULL,
  class TEXT NOT NULL,
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  cell TEXT NOT NULL,
  PRIMARY KEY (object_id)
);
CREATE INDEX IF NOT EXISTS idx_geo_cell ON object_geo(class, cell);

CREATE TABLE IF NOT EXISTS aggregate_cache (
  cache_key TEXT PRIMARY KEY,
  class TEXT NOT NULL,
  group_by TEXT NOT NULL,
  metrics TEXT NOT NULL,
  buckets TEXT NOT NULL,
  source_count INTEGER NOT NULL,
  built_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_stats (
  plan TEXT PRIMARY KEY,
  calls INTEGER NOT NULL,
  last_used TEXT NOT NULL
);
"""

DEFAULT_CELL_DEG = 0.01
PLAN_FULL_SCAN = "full_scan"
PLAN_PROJECTION = "projection_seek"
PLAN_VIEW = "materialized_view"
PLAN_GEO = "geo_index"
PLAN_AGG_CACHE = "aggregate_cache"


def _raw_config(ext: Any) -> dict:
    if ext is None:
        return {}
    raw = getattr(ext, "raw", None)
    return raw if isinstance(raw, dict) else {}


def views(ext: Any) -> list[dict]:
    return [v for v in _raw_config(ext).get("materialized_views", [])
            if isinstance(v, dict) and v.get("id") and v.get("root")]


def spatial_roles(ext: Any) -> dict[str, str]:
    raw = _raw_config(ext).get("spatial_properties", {})
    return raw if isinstance(raw, dict) else {}


def precompute_specs(ext: Any) -> list[dict]:
    return [a for a in _raw_config(ext).get("aggregate_precompute", [])
            if isinstance(a, dict) and a.get("class")]


def cell_of(lat: float, lon: float, precision: float = DEFAULT_CELL_DEG) -> str:
    """经纬度网格编码（geohash 的降级档：等距网格，足以做 bbox 收窄）。"""
    return f"{lat:.6f}:{lon:.6f}:{math.floor(lat / precision)}:{math.floor(lon / precision)}"


def _sync_views(conn, ext: Any) -> int:
    count = 0
    for view in views(ext):
        vid, root = view["id"], view["root"]
        conn.execute("DELETE FROM object_view_rows WHERE view_id=?", (vid,))
        rows = conn.execute(
            "SELECT object_id, props FROM objects WHERE type=?", (root,)).fetchall()
        joins = view.get("joins", [])
        for oid, raw in rows:
            try:
                payload = {"object_id": oid, "type": root,
                           "props": json.loads(raw or "{}")}
            except (TypeError, ValueError):
                continue
            for j in joins:
                link, direction = j.get("link"), j.get("direction", "out")
                alias = j.get("as") or link
                if not link:
                    continue
                if direction == "out":
                    linked = conn.execute(
                        "SELECT object_id FROM relations WHERE predicate=? "
                        "AND subject_id=? AND valid_to IS NULL", (link, oid)).fetchall()
                else:
                    linked = conn.execute(
                        "SELECT subject_id FROM relations WHERE predicate=? "
                        "AND object_id=? AND valid_to IS NULL", (link, oid)).fetchall()
                members = []
                for (other,) in linked:
                    orow = conn.execute(
                        "SELECT type, props FROM objects WHERE object_id=?",
                        (other,)).fetchone()
                    if orow is None:
                        continue
                    try:
                        members.append({"object_id": other, "type": orow[0],
                                        "props": json.loads(orow[1] or "{}")})
                    except (TypeError, ValueError):
                        continue
                payload[alias] = members
            keep = view.get("properties")
            if keep:
                payload["props"] = {k: v for k, v in payload["props"].items() if k in keep}
            conn.execute(
                "INSERT OR REPLACE INTO object_view_rows(view_id,root_id,payload) "
                "VALUES (?,?,?)", (vid, oid, json.dumps(payload, ensure_ascii=False)))
            count += 1
    return count


def _sync_geo(conn, ext: Any) -> int:
    conn.execute("DELETE FROM object_geo")
    roles = spatial_roles(ext)
    if not roles:
        return 0
    lat_keys = {k for k, v in roles.items() if v == "latitude"}
    lon_keys = {k for k, v in roles.items() if v == "longitude"}
    if not lat_keys or not lon_keys:
        return 0
    count = 0
    for oid, cls, raw in conn.execute("SELECT object_id, type, props FROM objects"):
        try:
            props = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        lat = next((props[k] for k in lat_keys
                    if isinstance(props.get(k), (int, float))
                    and not isinstance(props.get(k), bool)), None)
        lon = next((props[k] for k in lon_keys
                    if isinstance(props.get(k), (int, float))
                    and not isinstance(props.get(k), bool)), None)
        if lat is None or lon is None:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO object_geo(object_id,class,lat,lon,cell) "
            "VALUES (?,?,?,?,?)", (oid, cls, lat, lon, cell_of(lat, lon)))
        count += 1
    return count


def _metric_value(props: dict, expr: str) -> float:
    key = expr.split(":", 1)[1] if ":" in expr else expr
    value = props.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _sync_aggregates(conn, ext: Any) -> int:
    count = 0
    for spec in precompute_specs(ext):
        cls, group_by = spec["class"], spec.get("group_by", [])
        metrics = spec.get("metrics", ["count"])
        cache_key = "|".join([cls, ",".join(group_by), ",".join(metrics)])
        buckets: dict[tuple, dict] = {}
        rows = conn.execute("SELECT object_id, props FROM objects WHERE type=?",
                            (cls,)).fetchall()
        for oid, raw in rows:
            try:
                props = json.loads(raw or "{}")
            except (TypeError, ValueError):
                continue
            key = tuple(str(props.get(p)) for p in group_by)
            bucket = buckets.setdefault(key, {"key": dict(zip(group_by, key)), "count": 0, "sums": {}})
            bucket["count"] += 1
            for metric in metrics:
                if metric == "count":
                    continue
                bucket["sums"][metric] = (bucket["sums"].get(metric, 0.0)
                                          + _metric_value(props, metric))
        out = []
        for bucket in buckets.values():
            row = dict(bucket["key"])
            row["count"] = bucket["count"]
            row.update(bucket["sums"])
            out.append(row)
        conn.execute(
            "INSERT OR REPLACE INTO aggregate_cache"
            "(cache_key,class,group_by,metrics,buckets,source_count,built_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (cache_key, cls, json.dumps(group_by), json.dumps(metrics),
             json.dumps(out, ensure_ascii=False), len(rows),
             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        count += 1
    return count


def sync_all(conn, ext: Any) -> dict:
    """全量重建 V2 物化层，返回计数明细。

    注意：这里绝不调用 executescript —— sqlite3 的 executescript 会隐式 COMMIT，
    把调用方（批量/仿真）的 savepoint 直接释放掉。建表只在 FactStore.__init__ 做。
    """
    conn.execute("DELETE FROM object_view_rows")
    conn.execute("DELETE FROM object_geo")
    conn.execute("DELETE FROM aggregate_cache")
    return {"views": _sync_views(conn, ext), "geo": _sync_geo(conn, ext),
            "aggregates": _sync_aggregates(conn, ext)}


def sync_object(conn, object_id: str, ext: Any) -> None:
    """写路径增量维护：任一物化声明命中就整表重建该类（POC 档，避免增量错配）。

    真实 OSv2 会按对象做增量；POC 规模下整表重建更不容易出错，
    且外面有 freshness 闸门兜底。
    """
    if not (views(ext) or spatial_roles(ext) or precompute_specs(ext)):
        return
    sync_all(conn, ext)


def consistency(conn) -> list[dict]:
    """物化层一致性：视图行数 / 聚合源计数是否仍与事实库匹配。"""
    problems: list[dict] = []
    if not _table_exists(conn, "object_view_rows"):
        return problems
    for (vid,) in conn.execute("SELECT DISTINCT view_id FROM object_view_rows").fetchall():
        root_count = conn.execute(
            "SELECT COUNT(*) FROM objects WHERE object_id IN "
            "(SELECT root_id FROM object_view_rows WHERE view_id=?)", (vid,)).fetchone()[0]
        view_count = conn.execute(
            "SELECT COUNT(*) FROM object_view_rows WHERE view_id=?", (vid,)).fetchone()[0]
        if root_count != view_count:
            problems.append({"view_id": vid, "code": "VIEW_ORPHAN",
                             "expected": root_count, "actual": view_count})
    for key, cls, source_count in conn.execute(
            "SELECT cache_key, class, source_count FROM aggregate_cache"):
        current = conn.execute("SELECT COUNT(*) FROM objects WHERE type=?",
                               (cls,)).fetchone()[0]
        if current != source_count:
            problems.append({"cache_key": key, "code": "AGGREGATE_STALE",
                             "expected": source_count, "actual": current})
    return problems


def _table_exists(conn, name: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {name} LIMIT 1").fetchone()
        return True
    except Exception:  # noqa: BLE001
        return False


def is_geo_set(node: Any) -> bool:
    """集合表达式是否为空间查询（用于自适应计划选路）。"""
    return isinstance(node, dict) and "geo" in node


def choose_plan(set_node: Any, agg: Any, conn: Any, projection_fresh: bool) -> str:
    """自适应查询计划：按集合形态与物化可用性选路，并记录调用统计。"""
    plan = PLAN_FULL_SCAN
    if is_geo_set(set_node) and _table_exists(conn, "object_geo") \
            and conn.execute("SELECT COUNT(*) FROM object_geo").fetchone()[0] > 0:
        plan = PLAN_GEO
    elif agg is not None and _table_exists(conn, "aggregate_cache") \
            and conn.execute("SELECT COUNT(*) FROM aggregate_cache").fetchone()[0] > 0:
        plan = PLAN_AGG_CACHE
    elif projection_fresh and isinstance(set_node, dict) \
            and set_node.get("where"):
        plan = PLAN_PROJECTION
    if plan != PLAN_FULL_SCAN:
        try:
            conn.execute(
                "INSERT INTO plan_stats(plan,calls,last_used) VALUES (?,1,?) "
                "ON CONFLICT(plan) DO UPDATE SET calls=calls+1, last_used=excluded.last_used",
                (plan, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        except Exception:  # noqa: BLE001 - 统计失败不影响查询
            pass
    return plan


def geo_candidates(conn, spec: dict) -> list[str] | None:
    """bbox 空间查询：返回候选 object_id（object_geo 有索引）。"""
    if not isinstance(spec, dict):
        return None
    try:
        lat0, lon0, lat1, lon1 = (float(spec["lat0"]), float(spec["lon0"]),
                                  float(spec["lat1"]), float(spec["lon1"]))
    except (KeyError, TypeError, ValueError):
        return None
    rows = conn.execute(
        "SELECT object_id FROM object_geo WHERE lat BETWEEN ? AND ? "
        "AND lon BETWEEN ? AND ?", (min(lat0, lat1), max(lat0, lat1),
                                    min(lon0, lon1), max(lon0, lon1))).fetchall()
    return [r[0] for r in rows]


def view_rows(conn, view_id: str) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT payload FROM object_view_rows WHERE view_id=?", (view_id,)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for (raw,) in rows:
        try:
            out.append(json.loads(raw))
        except (TypeError, ValueError):
            continue
    return out


def cached_aggregate(conn, cls: str, group_by: list[str], metrics: list[str]) -> dict | None:
    key = "|".join([cls, ",".join(group_by), ",".join(metrics)])
    try:
        row = conn.execute(
            "SELECT buckets, source_count FROM aggregate_cache WHERE cache_key=?",
            (key,)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if row is None:
        return None
    current = conn.execute("SELECT COUNT(*) FROM objects WHERE type=?", (cls,)).fetchone()[0]
    if current != row[1]:
        return None
    try:
        return {"buckets": json.loads(row[0]), "source_count": row[1]}
    except (TypeError, ValueError):
        return None


def stats(conn) -> dict:
    out: dict[str, Any] = {}
    for name, table in (("views", "object_view_rows"), ("geo", "object_geo"),
                        ("aggregates", "aggregate_cache")):
        out[name] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] \
            if _table_exists(conn, table) else 0
    out["plans"] = {r[0]: r[1] for r in conn.execute(
        "SELECT plan, calls FROM plan_stats")} if _table_exists(conn, "plan_stats") else {}
    return out
