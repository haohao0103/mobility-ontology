#!/usr/bin/env python3
"""把 freight/semantic_layer.json 导出为 OWL Turtle（TBox + 可选实例）。

用途：让本体接受外部标准工具评测——
  - OOPS! 类陷阱扫描（读 Turtle 或直接读 JSON，见 oops_scan.py）
  - OWL 2 推理（owlready2，见 owl2_reason.py）
  - SPARQL 互操作（LUBM 风格查询，见 sparql_interop.py）

说明：本体的 IRI 是声明里自带的（https://w3id.org/mobility-ontology/freight#X），
导出保持原 IRI，不做重写。

用法：
    python3 scripts/benchmark/export_owl.py [--out PATH] [--with-instances]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

XSD = "http://www.w3.org/2001/XMLSchema#"
# OWL 2 datatype map 不含 xsd:date（HermiT/FaCT++ 会拒绝加载），
# 故 date 一律导出为 xsd:dateTime（日期时间零点），保证 OWL 2 DL 兼容。
# 这是 2026-11-21 互操作测评的真实发现：直接映射 xsd:date 会让标准
# reasoner UnsupportedDatatypeException。
TYPE_MAP = {
    "string": "xsd:string", "text": "xsd:string", "number": "xsd:decimal",
    "integer": "xsd:integer", "int": "xsd:integer", "boolean": "xsd:boolean",
    "datetime": "xsd:dateTime", "date": "xsd:dateTime", "timestamp": "xsd:dateTime",
    "float": "xsd:double", "double": "xsd:double",
}


def esc(s: str) -> str:
    return (str(s).replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\r", ""))


def iri_of(node: dict, fallback_id: str) -> str:
    return node.get("iri") or f"https://w3id.org/mobility-ontology/freight#{fallback_id}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "scripts/_out/benchmark/ontology.ttl"))
    ap.add_argument("--with-instances", action="store_true",
                    help="同时导出 runs/freight_facts.db 的 objects/relations 实例")
    ap.add_argument("--tbox-only", action="store_true", help="只导声明层（忽略实例）")
    ap.add_argument("--instances-only", action="store_true",
                    help="只导实例层（供不一致性二分定位）")
    args = ap.parse_args()
    if args.tbox_only:
        args.with_instances = False

    layer = json.loads((ROOT / "freight" / "semantic_layer.json").read_text(encoding="utf-8"))
    classes = layer.get("classes") or []
    props = layer.get("property_types") or []
    relations = layer.get("relations") or []
    if args.instances_only:  # 定位模式：只出实例，与 TBox 文件组合测试
        classes, props, relations = [], [], []
        args.with_instances = True

    # 属性名 -> 持有该属性的类（classes[].properties 反查）
    prop_domains: dict[str, list[str]] = {}
    class_iri: dict[str, str] = {}
    for c in classes:
        class_iri[c["id"]] = iri_of(c, c["id"])
        for p in (c.get("properties") or []):
            prop_domains.setdefault(p, []).append(c["id"])

    lines = [
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .",
        "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "@prefix mo: <https://w3id.org/mobility-ontology/freight#> .",
        "",
        "<https://w3id.org/mobility-ontology/freight> a owl:Ontology ;",
        f"    rdfs:label \"mobility-ontology\" ;",
        f"    owl:versionInfo \"{esc(layer.get('meta', {}).get('version', ''))}\" .",
        "",
    ]

    for c in classes:
        lines.append(f"<{class_iri[c['id']]}> a owl:Class ;")
        if c.get("label"):
            lines.append(f"    rdfs:label \"{esc(c['label'])}\"@zh ;")
        if c.get("description"):
            lines.append(f"    rdfs:comment \"{esc(c['description'])}\" ;")
        lines.append("    .")
    lines.append("")

    for p in props:
        name = p.get("name")
        iri = iri_of(p, name)
        rng = TYPE_MAP.get(str(p.get("value_type") or p.get("base_type") or "string").lower(),
                           "xsd:string")
        lines.append(f"<{iri}> a owl:DatatypeProperty ;")
        for cid in prop_domains.get(name, []):
            lines.append(f"    rdfs:domain <{class_iri[cid]}> ;")
        lines.append(f"    rdfs:range {rng} .")
    lines.append("")

    for r in relations:
        iri = iri_of(r, r["id"])
        lines.append(f"<{iri}> a owl:ObjectProperty ;")
        if r.get("domain") in class_iri:
            lines.append(f"    rdfs:domain <{class_iri[r['domain']]}> ;")
        if r.get("range") in class_iri:
            lines.append(f"    rdfs:range <{class_iri[r['range']]}> ;")
        lines.append("    .")
    lines.append("")

    if args.with_instances and not args.instances_only:
        db = ROOT / "runs" / "freight_facts.db"
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        # 属性名 -> 声明的值类型。实例字面量必须带 ^^datatype，否则与
        # rdfs:range 的 xsd:decimal/integer 冲突，HermiT 会判本体不一致
        # （2026-11-21 互操作测评真实发现：序列化丢 datatype 让标准
        # reasoner 拒收，尽管本体内部类型是自洽的）。
        declared = {p.get("name"): TYPE_MAP.get(
            str(p.get("value_type") or p.get("base_type") or "string").lower(),
            "xsd:string") for p in props}

        def literal(k: str, v) -> str:
            if isinstance(v, bool):
                return f"\"{'true' if v else 'false'}\"^^xsd:boolean"
            rng = declared.get(k, "xsd:string")
            if isinstance(v, (int, float)) or rng != "xsd:string":
                s = str(v)
                # date 值补时间为完整 dateTime（"2026-10-01" 对 xsd:dateTime 是 malformed）
                if rng == "xsd:dateTime" and len(s) == 10 and s[4] == "-" and s[7] == "-":
                    s = f"{s}T00:00:00"
                return f"\"{esc(s)}\"^^{rng}"
            return f"\"{esc(v)}\""

        for row in conn.execute("SELECT object_id, type, name, status, props FROM objects"):
            iri = f"https://w3id.org/mobility-ontology/freight#{row['object_id'].replace(':', '_')}"
            lines.append(f"<{iri}> a <{class_iri.get(row['type'], iri_of({'id': row['type']}, row['type']))}> ;")
            if row["name"]:
                lines.append(f"    rdfs:label \"{esc(row['name'])}\" ;")
            for k, v in (json.loads(row["props"]) or {}).items():
                if v is None:  # null 值不导出（否则 "None" 字面量非法）
                    continue
                lines.append(f"    mo:{k} {literal(k, v)} ;")
            lines.append("    .")
        for row in conn.execute(
                "SELECT subject_id, predicate, object_id FROM relations WHERE valid_to IS NULL"):
            s = f"https://w3id.org/mobility-ontology/freight#{row['subject_id'].replace(':', '_')}"
            o = f"https://w3id.org/mobility-ontology/freight#{row['object_id'].replace(':', '_')}"
            p = f"https://w3id.org/mobility-ontology/freight#{row['predicate']}"
            lines.append(f"<{s}> <{p}> <{o}> .")
        conn.close()
        lines.append("")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    n_inst = sum(1 for l in lines if l.startswith("<") and "a owl:Class" not in l)
    print(f"导出完成: {out}  classes={len(classes)} datatype_props={len(props)} "
          f"object_props={len(relations)} instances={'含' if args.with_instances else '无'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
