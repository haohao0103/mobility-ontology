"""来源契约：费用类型 × 连接器归属矩阵 + 覆盖度 + 缺口场景判定。

为什么要有这个模块
------------------
`d046ae1` 把"来源契约"的**机制**做出来了（连接器按 `fee_types` 认领、
缺口显式化、报价三口径互校），但没有任何检查会问：

- 这个费用类型**归谁管**？——没人认领就等于没有数据来源；
- 有没有**两个来源都认领**同一个类型？——口径会打架；
- 这个类型在 FeeItem / PricingRule / QuoteItem **三方是否齐全**？——缺一方就核不出来；
- 缺口与口径冲突，**检测能力是否真的在**？

没有门禁，机制会在下一次改本体时被悄悄绕过。本模块把这几条做成可计算的判据，
由门禁第 27 项强制。

口径说明
--------
- 费用类型**全集**取自本体 `pricing` 段（权威在本体，不在代码里）；
- 认领关系来自 `data_sources[].fee_types`；
- 本版按"**恰好一个**认领来源"判定（多源共同供给的场景需要改判据，
  改判据必须同步改门禁负向用例）。
"""
from __future__ import annotations

import json
import sqlite3


def fee_type_universe(layer) -> list[str]:
    """费用类型全集：本体 pricing 段的键（权威定义在本体，不在代码里）。"""
    pricing = layer.pricing if isinstance(getattr(layer, "pricing", None), dict) \
        else (layer.raw.get("pricing") or {})
    return sorted(pricing.keys())


def fee_type_owners(layer) -> dict[str, list[dict]]:
    """{fee_type: [认领声明]}，认领声明来自 data_sources[].fee_types。"""
    owners: dict[str, list[dict]] = {}
    for ds in layer.raw.get("data_sources") or []:
        for fee_type in ds.get("fee_types") or []:
            owners.setdefault(fee_type, []).append({
                "system": ds.get("system"),
                "tool": ds.get("tool"),
                "class": ds.get("class"),
            })
    return owners


def check_fee_type_ownership(layer) -> list[dict]:
    """G1 归属矩阵：每个费用类型恰好被一个连接器认领。"""
    issues: list[dict] = []
    universe = fee_type_universe(layer)
    owners = fee_type_owners(layer)

    for fee_type in universe:
        claimed = owners.get(fee_type, [])
        if not claimed:
            issues.append({
                "code": "FEE_TYPE_NO_OWNER", "path": f"pricing[{fee_type}]",
                "message": (f"费用类型 {fee_type} 没有任何 data_sources 连接器认领"
                            f"——取不到明细，也无从校验口径"),
            })
        elif len(claimed) > 1:
            systems = sorted({str(c.get("system")) for c in claimed})
            issues.append({
                "code": "FEE_TYPE_MULTI_OWNER", "path": f"pricing[{fee_type}]",
                "message": (f"费用类型 {fee_type} 被多个来源认领: {systems}"
                            f"——同一费用类型两个口径，必须收敛到一个"),
            })
    for fee_type in sorted(owners):
        if fee_type not in universe:
            issues.append({
                "code": "FEE_TYPE_NOT_IN_PRICING", "path": f"pricing[{fee_type}]",
                "message": (f"连接器认领了费用类型 {fee_type}，但 pricing 段没有它"
                            f"——费用类型全集与连接器声明对不上"),
            })
    return issues


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def measure_fee_type_coverage(layer, db_path: str) -> dict:
    """G2 费用类型覆盖（V5）：每个费用类型在三方各有多少实例。"""
    universe = fee_type_universe(layer)
    conn = _connect(db_path)
    try:
        def by_type(type_id: str) -> dict[str, int]:
            out: dict[str, int] = {}
            for r in conn.execute("SELECT type, props FROM objects WHERE type=?",
                                  (type_id,)):
                try:
                    props = json.loads(r["props"])
                except Exception:
                    props = {}
                fee_type = str(props.get("fee_type") or "")
                if fee_type:
                    out[fee_type] = out.get(fee_type, 0) + 1
            return out

        fees = by_type("FeeItem")
        rules = by_type("PricingRule")
        quote_items = by_type("QuoteItem")
    finally:
        conn.close()
    per_type = []
    for fee_type in universe:
        per_type.append({
            "fee_type": fee_type,
            "fee_items": fees.get(fee_type, 0),
            "pricing_rules": rules.get(fee_type, 0),
            "quote_items": quote_items.get(fee_type, 0),
        })
    complete = sum(1 for t in per_type
                   if t["fee_items"] and t["pricing_rules"] and t["quote_items"])
    return {
        "universe": universe,
        "per_type": per_type,
        "complete": complete,
        "total": len(per_type),
        "ratio": (complete / len(per_type)) if per_type else 0.0,
    }


def check_fee_type_coverage(layer, db_path: str) -> list[dict]:
    """G2 判据：每个费用类型三方齐全（缺一方就核不出来）。"""
    issues: list[dict] = []
    for t in measure_fee_type_coverage(layer, db_path)["per_type"]:
        if not t["fee_items"]:
            issues.append({
                "code": "FEE_TYPE_NO_INSTANCE", "path": f"pricing[{t['fee_type']}]",
                "message": f"费用类型 {t['fee_type']} 没有任何 FeeItem 实例",
            })
        if not t["pricing_rules"]:
            issues.append({
                "code": "FEE_TYPE_NO_RULE", "path": f"pricing[{t['fee_type']}]",
                "message": f"费用类型 {t['fee_type']} 没有对应的 PricingRule 实例",
            })
        if not t["quote_items"]:
            issues.append({
                "code": "FEE_TYPE_NO_QUOTE_ITEM", "path": f"pricing[{t['fee_type']}]",
                "message": f"费用类型 {t['fee_type']} 没有对应的 QuoteItem 明细",
            })
    return issues
