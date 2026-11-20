"""P2-3 破坏性变更门禁（对标 Palantir 本体变更的 breaking-change 审查）。

问题：`semantic_impact` 现在只会算「等候费规则改了影响哪些运单」——那是**一条规则**
的影响面，不是变更审查。真正要回答的是：

- 这次改本体，会不会把已经在跑的 OSDK 客户端、已注册函数、规则、动作打断？
- 打断了，有没有人签字？

本模块只做判定，不做修补：
1. `classify(before, after)` —— 把 diff 归到「破坏 / 兼容」两类，给出原因码；
2. `reference_impact(...)` —— 对每个破坏项，回溯谁在引用它（OSDK 生成物 /
   函数注册表 / 规则 / 动作 / 数据源 / 派生类型）；
3. `review(...)` —— 汇总成评审结论；破坏项没有签字就判 FAIL。

签字放在 `freight/breaking_approvals.json`：每条 {path, approved_by, reason, at}。
门禁比对的是「路径完全一致」，模糊匹配等于没有门禁。
"""
from __future__ import annotations

import json
import os

# 基数收紧判定：新基数比旧基数更严格即为破坏（写入方原本合法的数据会被拒）
_CARDINALITY_STRICTNESS = {
    "many_to_many": 0,
    "many_to_one": 1,
    "one_to_many": 1,
    "one_to_one": 2,
}


def _index(items, key="id"):
    return {i[key]: i for i in (items or []) if isinstance(i, dict) and i.get(key)}


def classify(before: dict, after: dict) -> list[dict]:
    """比对两份本体，输出变更清单（只有 breaking 项带原因码）。"""
    changes: list[dict] = []

    def add(severity, code, path, detail, **extra):
        changes.append({"severity": severity, "code": code, "path": path,
                        "detail": detail, **extra})

    b_cls, a_cls = _index(before.get("classes")), _index(after.get("classes"))
    for cid in sorted(set(b_cls) - set(a_cls)):
        add("breaking", "CLASS_REMOVED", f"classes[{cid}]",
            f"对象类型 {cid} 被删除，引用它的函数/规则/客户端全部失效")
    for cid in sorted(set(a_cls) - set(b_cls)):
        add("compatible", "CLASS_ADDED", f"classes[{cid}]", f"新增对象类型 {cid}")

    for cid in sorted(set(b_cls) & set(a_cls)):
        bp = set(b_cls[cid].get("properties") or [])
        ap = set(a_cls[cid].get("properties") or [])
        for prop in sorted(bp - ap):
            add("breaking", "PROPERTY_REMOVED", f"classes[{cid}].properties[{prop}]",
                f"{cid}.{prop} 被移除，按该字段读取的客户端/规则会拿到 None")
        for prop in sorted(ap - bp):
            add("compatible", "PROPERTY_ADDED", f"classes[{cid}].properties[{prop}]",
                f"{cid} 新增属性 {prop}")
        bs, as_ = set(b_cls[cid].get("statuses") or []), set(a_cls[cid].get("statuses") or [])
        for status in sorted(bs - as_):
            add("breaking", "STATUS_REMOVED", f"classes[{cid}].statuses[{status}]",
                f"{cid} 状态 {status} 被移除，已有该状态的对象将不合法")

    b_pt, a_pt = _index(before.get("property_types"), "name"), _index(
        after.get("property_types"), "name")
    for name in sorted(set(b_pt) - set(a_pt)):
        add("breaking", "PROPERTY_TYPE_REMOVED", f"property_types[{name}]",
            f"属性类型 {name} 从注册表移除（strict 模式下即刻拒绝写入）")
    for name in sorted(set(b_pt) & set(a_pt)):
        for field in ("value_type", "base_type"):
            if b_pt[name].get(field) != a_pt[name].get(field):
                add("breaking", "PROPERTY_TYPE_CHANGED",
                    f"property_types[{name}].{field}",
                    f"{name}.{field}: {b_pt[name].get(field)} -> {a_pt[name].get(field)}",
                    before=b_pt[name].get(field), after=a_pt[name].get(field))
        if b_pt[name].get("nullable") is not False and a_pt[name].get("nullable") is False:
            add("breaking", "PROPERTY_BECAME_REQUIRED",
                f"property_types[{name}].nullable",
                f"{name} 变为不可空，存量含空值的写入方会被拒")

    b_rel, a_rel = _index(before.get("relations")), _index(after.get("relations"))
    for rid in sorted(set(b_rel) - set(a_rel)):
        add("breaking", "RELATION_REMOVED", f"relations[{rid}]",
            f"链接 {rid} 被删除，按该链接遍历的查询会报未定义关系")
    for rid in sorted(set(b_rel) & set(a_rel)):
        for field in ("domain", "range"):
            if b_rel[rid].get(field) != a_rel[rid].get(field):
                add("breaking", "RELATION_ENDPOINT_CHANGED", f"relations[{rid}].{field}",
                    f"{rid}.{field}: {b_rel[rid].get(field)} -> {a_rel[rid].get(field)}",
                    before=b_rel[rid].get(field), after=a_rel[rid].get(field))
        b_card = b_rel[rid].get("cardinality") or "many_to_many"
        a_card = a_rel[rid].get("cardinality") or "many_to_many"
        if (_CARDINALITY_STRICTNESS.get(a_card, 0)
                > _CARDINALITY_STRICTNESS.get(b_card, 0)):
            add("breaking", "RELATION_CARDINALITY_TIGHTENED",
                f"relations[{rid}].cardinality",
                f"{rid} 基数收紧 {b_card} -> {a_card}，存量数据可能违反新基数")
    return changes


def reference_impact(before: dict, after: dict, changes: list[dict],
                     ext: dict | None = None, root: str | None = None) -> list[dict]:
    """对每个破坏项回溯引用方（谁会被打断）。

    引用来源：函数注册表 / 规则 / 动作 / 数据源 / 派生类型 / OSDK 生成物。
    """
    ext = ext or {}
    impacts: list[dict] = []
    rules = after.get("rules") or []
    actions = after.get("actions") or []
    data_sources = after.get("data_sources") or []
    derived = after.get("derived_types") or []
    functions = ext.get("function_registry") or []
    interfaces = ext.get("interfaces") or []

    for change in changes:
        if change["severity"] != "breaking":
            continue
        path = change["path"]
        name = path.split("[", 1)[1].split("]", 1)[0] if "[" in path else path
        refs: list[str] = []
        if path.startswith("classes["):
            cid = name
            refs += [f"函数 {f['id']} (target)" for f in functions
                     if f.get("target") == cid]
            refs += [f"规则 {r['id']}" for r in rules if r.get("domain") == cid]
            refs += [f"动作 {a['id']}" for a in actions if a.get("target") == cid]
            refs += [f"数据源 {d.get('system')}" for d in data_sources
                     if d.get("class") == cid]
            refs += [f"接口 {i['id']}" for i in interfaces
                     if any(x.get("object_type") == cid for x in i.get("implements") or [])]
            refs += [f"派生类型 {d['id']}" for d in derived
                     if cid in json.dumps(d.get("derivation"), ensure_ascii=False)]
            refs += [f"链接 {r['id']}" for r in after.get("relations") or []
                     if cid in (r.get("domain"), r.get("range"))]
        elif path.startswith("classes[") and ".properties[" in path:
            prop = name
            refs += [f"函数 {f['id']} 参数" for f in functions
                     if any(p.get("field") == prop
                            for p in (f.get("signature") or {}).get("params") or [])]
            refs += [f"规则 {r['id']}" for r in rules
                     if json.dumps(r.get("when"), ensure_ascii=False).find(
                         f'"{prop}"') >= 0]
        elif path.startswith("property_types["):
            refs += [f"属性注册表引用 {name}" if name else ""]
        elif path.startswith("relations["):
            refs += [f"闭包配置包含 {name}" if name in json.dumps(
                after.get("closure"), ensure_ascii=False) else ""]
        refs = [r for r in refs if r]
        if root:
            refs += _osdk_refs(root, name)
        impacts.append({"path": path, "code": change["code"],
                        "references": sorted(set(refs))})
    return impacts


def _osdk_refs(root: str, name: str) -> list[str]:
    """扫描 OSDK 生成物里是否出现该标识（生成物是外部客户端的契约面）。"""
    out: list[str] = []
    osdk = os.path.join(root, "generated", "osdk")
    if not name or not os.path.isdir(osdk):
        return out
    for fn in sorted(os.listdir(osdk)):
        if not fn.endswith(".py"):
            continue
        path = os.path.join(osdk, fn)
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        if name in text:
            out.append(f"OSDK 生成物 {fn}")
    return out


def load_approvals(root: str) -> list[dict]:
    path = os.path.join(root, "freight", "breaking_approvals.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("approvals", [])


def review(before: dict, after: dict, ext: dict | None = None,
           root: str | None = None) -> dict:
    """变更评审结论。破坏项必须逐条签字，签不到就 FAIL。"""
    changes = classify(before, after)
    breaking = [c for c in changes if c["severity"] == "breaking"]
    impacts = reference_impact(before, after, changes, ext, root)
    approved = {}
    if root:
        approved = {a.get("path"): a for a in load_approvals(root)}
    unapproved = [c for c in breaking if c["path"] not in approved]
    return {
        "verdict": "FAIL" if unapproved else "PASS",
        "breaking": bool(breaking),
        "change_count": len(changes),
        "breaking_count": len(breaking),
        "unapproved_count": len(unapproved),
        "changes": breaking,
        "unapproved": unapproved,
        "impacts": impacts,
    }
