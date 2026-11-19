"""Object Set 的确定性身份、快照与分页语义。

本模块只做集合运算与 token 编解码，不读取事实库，也不持久化任何结果。
查询层负责提供同一次查询的完整对象 ID 列表与快照指纹，分页只做稳定切片。
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Any, Iterable

from .semantic import BusinessReject

TOKEN_VERSION = 1
DEFAULT_TTL_SECONDS = 900


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    )


def query_digest(query: dict) -> str:
    return hashlib.sha256(_canonical(query).encode("utf-8")).hexdigest()


def snapshot_digest(parts: Iterable[Any]) -> str:
    return hashlib.sha256(_canonical(list(parts)).encode("utf-8")).hexdigest()


def object_set_id(query: dict, semantic_version: str,
                  snapshot_id: str) -> str:
    seed = f"{semantic_version}\n{query_digest(query)}\n{snapshot_id}"
    return "os_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _encode(payload: dict) -> str:
    raw = _canonical(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_page_token(token: str) -> dict:
    try:
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        )
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise BusinessReject("分页 token 无法解析", "TOKEN_INVALID") from exc
    if not isinstance(payload, dict) or payload.get("version") != TOKEN_VERSION:
        raise BusinessReject("分页 token 版本不支持", "TOKEN_INVALID")
    if int(payload.get("expires_at") or 0) < int(time.time()):
        raise BusinessReject("分页 token 已过期", "TOKEN_EXPIRED")
    return payload


def _next_token(query_hash: str, set_id: str, snapshot_id: str,
                offset: int, last_key: str | None, page_size: int,
                ttl: int = DEFAULT_TTL_SECONDS) -> str:
    now = int(time.time())
    return _encode({
        "version": TOKEN_VERSION,
        "query_hash": query_hash,
        "object_set_id": set_id,
        "snapshot_id": snapshot_id,
        "offset": offset,
        "last_key": last_key,
        "page_size": page_size,
        "issued_at": now,
        "expires_at": now + ttl,
    })


def page_slice(object_ids: list[str], query: dict, semantic_version: str,
               snapshot_id: str, limit: int,
               token: str | None = None) -> dict:
    """按稳定顺序切一页，并返回下一页 token。

    每一页都会重新拿到同一次查询的完整 ID 列表；快照不一致时直接拒绝，
    从而保证同一 token 链上的分页并集与一次性结果一致。
    """
    ordered = list(dict.fromkeys(object_ids))
    set_id = object_set_id(query, semantic_version, snapshot_id)
    digest = query_digest(query)
    size = max(1, min(int(limit), 500))
    offset = 0
    if token:
        payload = decode_page_token(token)
        if payload.get("query_hash") != digest:
            raise BusinessReject(
                "分页 token 与当前查询不匹配", "TOKEN_QUERY_MISMATCH"
            )
        if payload.get("snapshot_id") != snapshot_id:
            raise BusinessReject(
                "数据快照已变化，请重新查询", "TOKEN_EXPIRED"
            )
        if payload.get("object_set_id") != set_id:
            raise BusinessReject(
                "分页 token 与当前集合不匹配", "TOKEN_QUERY_MISMATCH"
            )
        if int(payload.get("page_size") or 0) != size:
            raise BusinessReject(
                "分页 token 与当前 limit 不匹配", "TOKEN_QUERY_MISMATCH"
            )
        offset = int(payload.get("offset") or 0)
        if offset < 0 or offset > len(ordered):
            raise BusinessReject("分页 token 偏移非法", "TOKEN_INVALID")
        if offset and payload.get("last_key") != ordered[offset - 1]:
            raise BusinessReject(
                "分页 token 与集合顺序不一致", "TOKEN_EXPIRED"
            )
    page = ordered[offset:offset + size]
    next_offset = offset + len(page)
    next_token = None
    if next_offset < len(ordered):
        next_token = _next_token(
            digest, set_id, snapshot_id, next_offset,
            page[-1] if page else None, size,
        )
    return {
        "object_set_id": set_id,
        "snapshot_id": snapshot_id,
        "semantic_version": semantic_version,
        "count": len(ordered),
        "page_count": len(page),
        "offset": offset,
        "object_ids": page,
        "next_page_token": next_token,
        "has_next": next_token is not None,
    }


def summarize_links(items: Iterable[dict]) -> list[dict]:
    """按 (link type, direction, neighbor type) 汇总关系和相邻对象。"""
    buckets: dict[tuple[str, str, str], dict[str, set]] = {}
    for item in items:
        key = (
            str(item["link_type_id"]),
            str(item["direction"]),
            str(item["neighbor_type"]),
        )
        bucket = buckets.setdefault(
            key, {"relation_ids": set(), "object_ids": set()}
        )
        bucket["relation_ids"].add(str(item["relation_id"]))
        bucket["object_ids"].add(str(item["object_id"]))
    rows = []
    for (link_type_id, direction, neighbor_type), bucket in sorted(
        buckets.items()
    ):
        rows.append({
            "link_type_id": link_type_id,
            "direction": direction,
            "neighbor_type": neighbor_type,
            "relation_count": len(bucket["relation_ids"]),
            "object_count": len(bucket["object_ids"]),
        })
    return rows