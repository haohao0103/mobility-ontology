"""P1-5 对象搜索：跨类全文/属性过滤 + 分页（对标 Object Explorer 搜索的降级实现）。

只依赖本体类定义（base_classes）：新增类 -> 无需改代码即可被搜到（承重验收）。
"""
from __future__ import annotations

import json


def search_objects(store, ext, q: str | None = None, types: list[str] | None = None,
                   status: str | None = None, property_filter: dict | None = None,
                   limit: int = 20, offset: int = 0) -> dict:
    """搜索事实库对象。

    - q：对 object_id / name / type / status / props 值做子串匹配
    - types：限定对象类型（必须是本体已定义类）
    - property_filter：精确值过滤 {prop: value}
    - 分页：limit/offset + total 计数
    """
    # 类型合法性：只允许本体已定义类（未定义类型直接报 violation 而非静默空结果）
    valid_types = list(ext.base_classes.keys()) if types is None else None
    if types:
        unknown = [t for t in types if t not in ext.base_classes]
        if unknown:
            from enhancements.registry import ExtensionRegistry  # noqa: F811
            raise ValueError(f"未定义的对象类型: {', '.join(unknown)}")
        valid_types = types
        sql = "SELECT * FROM objects WHERE type IN (%s)" % ",".join("?" * len(valid_types))
        args: list = list(valid_types)
    else:
        sql = "SELECT * FROM objects"
        args = []
    rows = store.all(sql, tuple(args))

    total_all = len(rows)

    def match(r: dict) -> bool:
        if status and r.get("status") != status:
            return False
        props = json.loads(r["props"]) if isinstance(r.get("props"), str) else (r.get("props") or {})
        if property_filter:
            for k, v in property_filter.items():
                if props.get(k) != v:
                    return False
        if q:
            blob = " ".join([
                str(r.get("object_id", "")), str(r.get("name", "")),
                str(r.get("type", "")), str(r.get("status", "")),
                json.dumps(props, ensure_ascii=False),
            ]).lower()
            if q.lower() not in blob:
                return False
        return True

    hits = [r for r in rows if match(r)]
    page = hits[offset: offset + limit]
    return {
        "total": len(hits),
        "scanned": total_all,
        "limit": limit,
        "offset": offset,
        "objects": [
            {
                "object_id": r["object_id"],
                "type": r["type"],
                "name": r["name"],
                "status": r["status"],
            }
            for r in page
        ],
    }
