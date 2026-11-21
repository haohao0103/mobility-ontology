"""增强层负向实验室：接口多态 / Agent 输出闸 / marking 翻转（B-08~B-10）。

所有实验在内存声明副本上做，不写主库、不改仓库 JSON。返回统一 flips 结构。
"""
from __future__ import annotations

import copy
import json
from typing import Any

from enhancements.security import SecurityContext, AccessDenied
from enhancements.interfaces import InterfaceRegistry, InterfaceViolation


def _flips(before: dict, after: dict) -> list[dict]:
    out: list[dict] = []

    def codes(obj: Any) -> dict[str, int]:
        acc: dict[str, int] = {}
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "code" and isinstance(v, str):
                    acc[v] = acc.get(v, 0) + 1
                else:
                    for kk, vv in codes(v).items():
                        acc[kk] = acc.get(kk, 0) + vv
        elif isinstance(obj, list):
            for item in obj:
                for kk, vv in codes(item).items():
                    acc[kk] = acc.get(kk, 0) + vv
        return acc

    b, a = codes(before), codes(after)
    for code in sorted(set(b) | set(a)):
        if code not in b and code in a:
            out.append({"code": code, "action": "appeared"})
        elif code in b and code not in a:
            out.append({"code": code, "action": "disappeared"})
        elif b[code] != a[code]:
            out.append({"code": code, "action": "count_changed",
                        "before": b[code], "after": a[code]})
    return out


def _result(case: str, before: dict, after: dict, paths: list[str]) -> dict:
    return {"case": case, "before": before, "after": after,
            "flips": _flips(before, after), "declaration_paths": paths,
            "persisted": False}


def interface_simulate(enh, case: str = "drop_required_property") -> dict:
    """B-08: 删接口必需属性 → 实现类减少 → 多态函数从接受到拒绝。"""
    ext = copy.deepcopy(enh.ext)
    paths: list[str] = []

    if case == "drop_required_property":
        iface = (ext.interfaces or {}).get("Chargeable")
        if iface is None:
            return {"error": "INTERFACE_NOT_FOUND", "case": case}
        # 删 FeeItem 的显式 implements：回退结构匹配后，
        # 再删接口必需属性 status → FeeItem 不再 conforms → 实现类减少、函数拒绝
        impls = iface.get("implements") or []
        iface["implements"] = [i for i in impls
                               if i.get("object_type") != "FeeItem"]
        paths.append("interfaces[Chargeable].implements[FeeItem]")
        reqs = iface.get("required_properties") or []
        if "status" in reqs:
            reqs.remove("status")
            paths.append("interfaces[Chargeable].required_properties[status]")
        # 给 FeeItem 的类副本删 status 属性，使结构匹配失败
        cls = (ext.base_classes or {}).get("FeeItem")
        if cls and "status" in (cls.get("properties") or []):
            cls["properties"].remove("status")
    else:
        return {"error": "UNKNOWN_CASE", "case": case}

    def probe(registry: InterfaceRegistry) -> dict:
        impls = registry.implementations("Chargeable")
        findings = []
        try:
            registry.check_function_arg("chargeable_variance", "FeeItem")
            accepted = True
        except InterfaceViolation as exc:
            accepted = False
            findings.append({"code": "INTERFACE_VIOLATION", "detail": str(exc)[:160]})
        return {"implementations": impls, "impl_count": len(impls),
                "function_accepted": accepted, "findings": findings}

    before = probe(InterfaceRegistry(enh.ext))
    after = probe(InterfaceRegistry(ext))
    return _result("interface_" + case, before, after, paths)


def guard_output_lab(enh, user: str = "anonymous", roles: str = "",
                     object_id: str = "exception:EX20261105001") -> dict:
    """B-09: 对指定对象执行 Agent 输出闸，返回脱敏或 403。"""
    from freight.api import get_system

    system = get_system()
    row = system.facts.get_row(object_id)
    if not row:
        return {"error": "OBJECT_NOT_FOUND", "object_id": object_id}
    props = json.loads(row["props"]) if isinstance(row["props"], str) else row["props"]
    role_set = {r.strip() for r in roles.split(",") if r.strip()}
    ctx = SecurityContext.with_roles(user, list(role_set))

    findings = []
    try:
        out = enh.security.guard_output(row["type"], props, ctx)
        masked = [k for k, v in out.items() if v is None and k in props and props[k] is not None]
        status = "MASKED" if masked else "ALLOWED"
        result = {"status": status, "masked_fields": masked, "output": out}
    except AccessDenied as exc:
        status = "DENIED"
        findings.append({"code": exc.code, "detail": str(exc)[:160]})
        result = {"status": status, "masked_fields": [], "output": None}
    return {"case": "guard_output", "user": user, "roles": sorted(role_set),
            "object_id": object_id, "status": status, "findings": findings,
            "declaration_paths": [], "persisted": False, "result": result}


def simulate_marking(enh, case: str = "drop_amount_marking",
                     class_id: str = "FeeItem") -> dict:
    """B-10: 在内存副本改 marking，对比行/列可见性翻转。"""
    from freight.api import get_system

    ext = copy.deepcopy(enh.ext)
    system = get_system()
    paths: list[str] = []

    if case == "drop_source_marking":
        # 删数据源级整行标记（客服工单 DISPUTE_OPS），匿名可见性应变化
        for item in ext.data_source_markings:
            if item.get("system") == "客服工单":
                item["marking"] = None
                paths.append("data_source_markings[客服工单].marking")
        raw = getattr(ext, "raw", {}) or {}
        for item in raw.get("data_source_markings", []):
            if item.get("system") == "客服工单":
                item["marking"] = None
                paths.append("raw.data_source_markings[客服工单].marking")
    else:
        return {"error": "UNKNOWN_CASE", "case": case}

    rows = [dict(r) for r in system.facts.all(
        "SELECT * FROM objects WHERE type=? ORDER BY object_id", (class_id,))]
    for r in rows:
        try:
            r["props"] = json.loads(r["props"]) if isinstance(r["props"], str) else r["props"]
        except (TypeError, ValueError):
            r["props"] = {}

    def probe(security) -> dict:
        ctx = SecurityContext.with_roles("anonymous", [])
        vis = security.simulate(class_id, rows, ctx)
        findings = []
        if vis["visible_rows"] < vis["total_rows"]:
            findings.append({"code": "ROW_POLICY_HIDDEN",
                             "count": vis["total_rows"] - vis["visible_rows"]})
        return {"visible_rows": vis["visible_rows"], "total_rows": vis["total_rows"],
                "findings": findings}

    from enhancements.security import SecurityEngine

    before = probe(enh.security)
    after_probe = probe(SecurityEngine(ext, getattr(enh, "data_sources", None)))
    return _result("marking_" + case, before, after_probe, paths)
