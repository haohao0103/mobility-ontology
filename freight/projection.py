"""P2-1 轻量对象投影（对标 Palantir 对象物化层，只做「够用」的那一档）。

Palantir 的 OSv2 会按 Object Type schema 把对象物化进对象存储并建多索引
（搜索 / 聚合 / 地理），并保证索引一致性。我们不复刻那一套，只落地它的**必要内核**：

1. **属性级投影**：把 `objects.props` 这个 JSON blob 展开成 (object_id, prop, value)
   行式表，按 (class, prop, 值) 建索引。JSON 扫描是全表反序列化，投影是索引seek。
2. **一致性可验证**：`consistency()` 逐对象逐属性比对投影与源 JSON，
   返回全部不一致项。物化层如果没有一致性校验，就只是「另一份可能过期的数据」。
3. **写路径维护**：`FactStore.insert_object` / `update_object` 是唯一写入口，
   在同一事务内同步投影 —— 不存在「忘了同步」的窗口。

**不做**的事（写清楚，避免下次又当成都做了）：
- 不做跨表物化视图、不做地理索引、不做聚合预计算；
- 不做索引与查询计划的自适应选择。

**正确性边界（必须说清楚，别自欺）**：
查询引擎用投影**收窄候选集**，然后再用 `eval_predicate` 复算一遍。
复算只能过滤掉「多出来的」候选，**救不回「被漏掉的」候选** —— 投影一旦过期，
结果会静默变少。所以收窄前必须过 `is_fresh()` 新鲜度闸门：对象数与投影覆盖的
对象数不一致就整条回退全表扫描。投影的同步靠 `FactStore` 写入口保证（同一事务内
`sync_object`），`is_fresh()` 是防「绕过写入口直接改库」的兜底。
"""
from __future__ import annotations

import json

PROJECTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS object_props (
  object_id TEXT NOT NULL,
  class TEXT NOT NULL,
  prop TEXT NOT NULL,
  value_kind TEXT NOT NULL,
  num_value REAL,
  str_value TEXT,
  PRIMARY KEY (object_id, prop)
);
CREATE INDEX IF NOT EXISTS idx_objprops_num ON object_props(class, prop, num_value);
CREATE INDEX IF NOT EXISTS idx_objprops_str ON object_props(class, prop, str_value);
CREATE INDEX IF NOT EXISTS idx_objprops_class ON object_props(class);
CREATE TABLE IF NOT EXISTS projection_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  object_count INTEGER NOT NULL,
  row_count INTEGER NOT NULL
);
"""


def _classify(value) -> tuple[str, float | None, str | None]:
    """值 -> (kind, num_value, str_value)。

    投影要能支撑索引比较，所以只保留「可比较」的两类标量；
    对象/数组（json）不建索引：给 JSON 建索引等于假装能按 JSON 全序比较。
    """
    if value is None:
        return "null", None, None
    if isinstance(value, bool):
        return "str", None, "true" if value else "false"
    if isinstance(value, (int, float)):
        return "num", float(value), None
    if isinstance(value, str):
        return "str", None, value
    return "json", None, None


def _read_state(conn) -> tuple[int, int] | None:
    row = conn.execute(
        "SELECT object_count, row_count FROM projection_state WHERE id=1").fetchone()
    return (row[0], row[1]) if row else None


def _write_state(conn, objects: int, rows: int) -> None:
    conn.execute(
        "INSERT INTO projection_state(id, object_count, row_count) VALUES (1,?,?) "
        "ON CONFLICT(id) DO UPDATE SET object_count=excluded.object_count, "
        "row_count=excluded.row_count",
        (objects, rows),
    )


def sync_object(conn, object_id: str) -> None:
    """重算单个对象的投影（幂等），增量维护状态计数。

    计数增量维护而不是每次 COUNT(*)：写路径上做全表计数，等于把「省下来的
    扫描」又加回到每次写入上。
    """
    row = conn.execute(
        "SELECT type, props FROM objects WHERE object_id=?", (object_id,)
    ).fetchone()
    state = _read_state(conn) or (0, 0)
    deleted = conn.execute(
        "DELETE FROM object_props WHERE object_id=?", (object_id,)).rowcount
    objects, rows = state
    if row is None:
        _write_state(conn, max(objects - 1, 0), max(rows - deleted, 0))
        return
    class_id = row[0]
    try:
        props = json.loads(row[1] or "{}")
    except (TypeError, ValueError):
        _write_state(conn, objects, max(rows - deleted, 0))
        return
    values = []
    for prop, value in props.items():
        kind, num, text = _classify(value)
        values.append((object_id, class_id, prop, kind, num, text))
    if values:
        conn.executemany(
            "INSERT OR REPLACE INTO object_props"
            "(object_id,class,prop,value_kind,num_value,str_value) VALUES (?,?,?,?,?,?)",
            values,
        )
    # deleted==0 且对象原本不在投影里 -> 新增一个对象
    added_object = 1 if deleted == 0 else 0
    _write_state(conn, objects + added_object, max(rows - deleted, 0) + len(values))


def sync_all(conn) -> int:
    """全量重建投影，返回同步的对象数。"""
    conn.execute("DELETE FROM object_props")
    rows = conn.execute("SELECT object_id FROM objects").fetchall()
    for (oid,) in rows:
        conn.execute("DELETE FROM object_props WHERE object_id=?", (oid,))
        row = conn.execute("SELECT type, props FROM objects WHERE object_id=?",
                           (oid,)).fetchone()
        if row is None:
            continue
        try:
            props = json.loads(row[1] or "{}")
        except (TypeError, ValueError):
            continue
        conn.executemany(
            "INSERT OR REPLACE INTO object_props"
            "(object_id,class,prop,value_kind,num_value,str_value) VALUES (?,?,?,?,?,?)",
            [(oid, row[0], p, *_classify(v)) for p, v in props.items()],
        )
    total_rows = conn.execute("SELECT COUNT(*) FROM object_props").fetchone()[0]
    _write_state(conn, len(rows), total_rows)
    return len(rows)


def consistency(conn) -> list[dict]:
    """比对投影与源 JSON，返回不一致项（物化层的一致性凭据）。"""
    problems: list[dict] = []
    projected: dict[str, dict[str, tuple]] = {}
    for oid, prop, kind, num, text in conn.execute(
            "SELECT object_id, prop, value_kind, num_value, str_value FROM object_props"):
        projected.setdefault(oid, {})[prop] = (kind, num, text)

    for oid, class_id, raw in conn.execute("SELECT object_id, type, props FROM objects"):
        try:
            props = json.loads(raw or "{}")
        except (TypeError, ValueError):
            problems.append({"object_id": oid, "code": "SOURCE_PROPS_INVALID",
                             "detail": "objects.props 不是合法 JSON"})
            continue
        got = projected.pop(oid, {})
        want = {p: _classify(v) for p, v in props.items()}
        for prop in sorted(set(want) - set(got)):
            problems.append({"object_id": oid, "class": class_id, "prop": prop,
                             "code": "PROJECTION_MISSING"})
        for prop in sorted(set(got) - set(want)):
            problems.append({"object_id": oid, "class": class_id, "prop": prop,
                             "code": "PROJECTION_EXTRA"})
        for prop in sorted(set(got) & set(want)):
            if got[prop] != want[prop]:
                problems.append({"object_id": oid, "class": class_id, "prop": prop,
                                 "code": "PROJECTION_STALE",
                                 "expected": want[prop], "actual": got[prop]})
    for oid in sorted(projected):
        problems.append({"object_id": oid, "code": "PROJECTION_ORPHAN",
                         "detail": "投影存在但对象已删除"})
    return problems


def is_fresh(conn) -> bool:
    """投影是否与源表一致（新鲜度闸门）。

    比对两个计数：状态表里增量维护的期望值 vs 实际值。
    - 对象数不符 -> 有对象没进投影（老库未回填 / 绕过写入口插入）；
    - 属性行数不符 -> 有属性行被删或漏写。
    两者任一不符就整条回退全表扫描，绝不用可疑索引收窄。

    **值级**过期（条数不变但值被改）这里查不出来，由 `consistency()` 负责。
    这条分工写在文档里，不假装一个廉价闸门能覆盖所有失效形态。
    """
    try:
        state = _read_state(conn)
        if state is None:
            return False
        objects = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
        rows = conn.execute("SELECT COUNT(*) FROM object_props").fetchone()[0]
    except Exception:  # noqa: BLE001 - 表不存在/结构不符一律视为不新鲜
        return False
    return state == (objects, rows)


def stats(conn) -> dict:
    return {
        "objects": conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0],
        "projected_rows": conn.execute("SELECT COUNT(*) FROM object_props").fetchone()[0],
        "indexed_props": conn.execute(
            "SELECT COUNT(DISTINCT prop) FROM object_props").fetchone()[0],
    }
