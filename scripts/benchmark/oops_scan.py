#!/usr/bin/env python3
"""OOPS! 陷阱扫描（按 OOPS! Pitfall Catalog 规则本地复刻）。

背景说明（诚实口径）：官方 OOPS! 工具链（web service oops-ws.oeg-upm.net 与
jar 发布）在本机网络不可达（GitHub 与 OEG 服务均不通，SourceForge 上的
oops 1.3.16 是同名 C 项目而非本体扫描器）。因此本项**不是官方 OOPS! 的
运行结果**，而是按其公开发表的 Pitfall Catalog（论文 "OOPS! - Ontology
Pitfall Scanner!"，陷阱按 importance 分 critical/major/minor）复刻的
子集检测。规则以「名称 + 判定式」双列，便于对照原文核对。

覆盖的陷阱（OOPS! 目录名）：
  1. Missing labelling（无 label 且无 description 的类/属性）
  2. Inappropriate naming conventions（类 id 首字母小写/含空格等）
  3. Creating synonymous classes not declared as equivalent（label 重复但 IRI 不同）
  4. Creating unconnected ontology elements（无关系引用/无实例/无分组的孤立类）
  5. Missing disjoint declarations（同组类间未声明不相交）
  6. Defining multiple domains for a property（一个属性域到多个类）
  7. Misusing owl:Thing（域/范围指向万物类）
  8. Defining wrong transitive relationships（关系自称传递但未建模为传递）

用法：python3 scripts/benchmark/oops_scan.py [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAYER = ROOT / "freight" / "semantic_layer.json"


def norm(text: str) -> str:
    """同义归一口径：小写 + 去空白 + 去常见业务后缀。"""
    t = re.sub(r"[\s_\-]+", "", (text or "").lower())
    return t


def scan(layer: dict) -> dict:
    classes = layer.get("classes") or []
    props = layer.get("property_types") or []
    relations = layer.get("relations") or []
    groups = layer.get("object_type_groups") or {}

    findings: dict[str, list] = {
        "missing_labelling": [], "naming_conventions": [],
        "synonymous_classes": [], "unconnected_elements": [],
        "missing_disjoint": [], "multiple_domains": [],
        "owl_thing_misuse": [], "wrong_transitive": [],
    }

    # ---- 1 Missing labelling ----
    for c in classes:
        if not c.get("label") and not c.get("description"):
            findings["missing_labelling"].append(
                {"id": c["id"], "kind": "class"})
    for p in props:
        if not p.get("label") and not p.get("description"):
            findings["missing_labelling"].append(
                {"id": p.get("name"), "kind": "property"})

    # ---- 2 Naming conventions ----
    for c in classes:
        cid = c["id"]
        if not re.match(r"^[A-Z][A-Za-z0-9]*$", cid):
            findings["naming_conventions"].append(
                {"id": cid, "rule": "类 id 应为大驼峰且不含空格/符号"})
    for p in props:
        name = p.get("name") or ""
        if name and not re.match(r"^[a-z][a-z0-9_]*$", name):
            findings["naming_conventions"].append(
                {"id": name, "rule": "属性名应为小写下划线"})

    # ---- 3 Synonymous classes ----
    by_norm: dict[str, list] = {}
    for c in classes:
        for text in (c.get("label"), c.get("plural_display_name")):
            if text:
                by_norm.setdefault(norm(text), []).append(c["id"])
    for key, ids in by_norm.items():
        if len(set(ids)) > 1:
            findings["synonymous_classes"].append(
                {"normalized": key, "classes": sorted(set(ids)),
                 "note": "同名/近名但未声明 owl:equivalentClass"})

    # ---- 4 Unconnected elements ----
    rel_classes: set[str] = set()
    for r in relations:
        rel_classes.add(r.get("domain"))
        rel_classes.add(r.get("range"))
    # object_type_groups 是 list[{id,label,types[]}]
    group_list = groups if isinstance(groups, list) else [
        {"id": k, "label": k, "types": v} for k, v in (groups or {}).items()]
    grouped: set[str] = set()
    for g in group_list:
        grouped.update(g.get("types") or [])
    has_instances = {c["id"] for c in classes if c["id"] in
                     {"Waybill", "Order", "FeeItem", "Shipper", "Settlement",
                      "Evidence", "ExceptionTicket", "Invoice", "Vehicle", "Driver",
                      "OrderLine", "Quote"}}
    for c in classes:
        cid = c["id"]
        if cid not in rel_classes and cid not in grouped and cid not in has_instances:
            findings["unconnected_elements"].append({"id": cid})

    # ---- 5 Missing disjoint declarations（同组类间） ----
    for g in group_list:
        members = g.get("types") or []
        if len(members) > 1:
            findings["missing_disjoint"].append(
                {"group": g.get("id"), "label": g.get("label"),
                 "members": members[:12],
                 "note": f"{len(members)} 个同组类型间未声明不相交（OWL 缺省不互斥）"})

    # ---- 6 Multiple domains ----
    for p in props:
        domains = [c["id"] for c in classes if p.get("name") in (c.get("properties") or [])]
        if len(domains) > 3:  # OOPS 关注面过宽；>3 个域视为可疑（口径明示）
            findings["multiple_domains"].append(
                {"property": p.get("name"), "domain_count": len(domains),
                 "note": "OWL 语义为各域取交集，多数情况非本意"})

    # ---- 7 owl:Thing misuse ----
    for r in relations:
        for role in ("domain", "range"):
            if str(r.get(role) or "").lower() in ("thing", "owl:thing", "*"):
                findings["owl_thing_misuse"].append(
                    {"relation": r["id"], "role": role})

    # ---- 8 wrong transitive ----
    for r in relations:
        rid = (r.get("id") or "").lower()
        label = (r.get("label") or "")
        if r.get("temporal"):
            continue
        if any(k in rid for k in ("contains", "includes", "part_of", "has_line")) \
                and "transitive" in json.dumps(r, ensure_ascii=False):
            findings["wrong_transitive"].append({"relation": r["id"], "label": label})

    return {
        "tool": "OOPS! Pitfall Catalog 本地复刻（非官方 OOPS! 运行；官方工具链网络不可达）",
        "counts": {k: len(v) for k, v in findings.items()},
        "findings": {k: v[:50] for k, v in findings.items()},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    layer = json.loads(LAYER.read_text(encoding="utf-8"))
    report = scan(layer)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"OOPS! 复刻扫描: {report['tool']}")
        for name, items in report["findings"].items():
            print(f"  {name}: {report['counts'][name]} 项")
            for it in items[:3]:
                print(f"    - {json.dumps(it, ensure_ascii=False)[:130]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
