#!/usr/bin/env python3
"""把 Canonical 语义层导出为标准 SHACL NodeShape（TTL）。

JSON/YAML DSL 仍是唯一编写源；本工具生成派生 SHACL 产物，供 pySHACL 在 CI 中
做标准闭世界校验。核心 SHACL 表达类属性白名单、状态枚举、关系 Domain/Range、
最小基数；无法用核心 SHACL 表达的数据约束用标准 SHACL-SPARQL 表达。

示例：
  python tools/export_shacl.py semantic_layer.json \
      --base-iri https://w3id.org/mobility-ontology/core# \
      --out build/mobility.shacl.ttl
"""
from __future__ import annotations

import argparse
import json
import os
import sys

try:
    from rdflib import BNode, Graph, Literal, Namespace, URIRef
    from rdflib.collection import Collection
    from rdflib.namespace import RDF, RDFS, XSD
except ImportError as exc:  # pragma: no cover
    raise SystemExit("缺少 rdflib，请先安装: pip install 'rdflib>=7,<8'") from exc

SH = Namespace("http://www.w3.org/ns/shacl#")

DEFAULT_BASE = "https://w3id.org/mobility-ontology/core#"


def _list(graph: Graph, items) -> BNode:
    head = BNode()
    Collection(graph, head, list(items))
    return head


def _bnode(graph: Graph, pairs) -> BNode:
    node = BNode()
    for predicate, obj in pairs:
        graph.add((node, predicate, obj))
    return node


def class_properties(layer: dict) -> dict:
    """兼容两种写法：顶层 properties 映射（mobility）与类内 properties 数组（freight）。"""
    mapping = layer.get("properties") or {}
    out = {}
    for cls in layer.get("classes") or []:
        cid = cls.get("id")
        if not cid:
            continue
        declared = mapping.get(cid)
        if declared is None:
            declared = cls.get("properties") or []
        if isinstance(declared, dict):
            declared = list(declared)
        out[cid] = [p for p in declared if isinstance(p, str)]
    return out


def class_key(cls: dict, properties: list) -> str | None:
    key = cls.get("key")
    if key:
        return key
    for prop in properties:
        if prop.endswith("_id"):
            return prop
    return None


def lifecycle_states(layer: dict, class_id: str) -> list:
    for name, spec in (layer.get("lifecycle") or {}).items():
        if name.lower() == class_id.lower():
            return list(spec.get("states") or [])
    return []


def status_states(layer: dict, cls: dict) -> list:
    return lifecycle_states(layer, cls["id"]) or list(cls.get("statuses") or [])


def build_shapes(layer: dict, base_iri: str = DEFAULT_BASE) -> Graph:
    graph = Graph()
    graph.bind("sh", SH)
    graph.bind("xsd", XSD)
    graph.bind("rdf", RDF)
    graph.bind("rdfs", RDFS)
    graph.bind("mo", Namespace(base_iri))

    def iri(name: str) -> URIRef:
        return URIRef(base_iri + name)

    classes = layer.get("classes") or []
    properties = class_properties(layer)
    relations = layer.get("relations") or []
    rel_by_id = {rel["id"]: rel for rel in relations}
    outgoing: dict = {}
    for rel in relations:
        outgoing.setdefault(rel.get("domain"), []).append(rel["id"])

    # SHACL-SPARQL 的 mo: / xsd: 前缀声明
    prefixes = iri("_prefixes")
    graph.add((prefixes, SH.declare, _bnode(graph, [
        (SH.prefix, Literal("mo")),
        (SH.namespace, Literal(base_iri, datatype=XSD.anyURI)),
    ])))
    graph.add((prefixes, SH.declare, _bnode(graph, [
        (SH.prefix, Literal("xsd")),
        (SH.namespace, Literal(str(XSD), datatype=XSD.anyURI)),
    ])))

    # 类形状：属性白名单、主键基数、状态枚举、出边取值范围
    for cls in classes:
        cid = cls["id"]
        shape = iri(f"{cid}Shape")
        graph.add((shape, RDF.type, SH.NodeShape))
        graph.add((shape, SH.targetClass, iri(cid)))
        if cls.get("label"):
            graph.add((shape, RDFS.label, Literal(cls["label"])))

        data_props = properties.get(cid, [])
        out_rels = outgoing.get(cid, [])
        if data_props or out_rels:
            graph.add((shape, SH.closed, Literal(True)))
            graph.add((shape, SH.ignoredProperties, _list(graph, [RDF.type])))

        key = class_key(cls, data_props)
        states = status_states(layer, cls)
        for prop in data_props:
            prop_shape = _bnode(graph, [(SH.path, iri(prop))])
            graph.add((shape, SH.property, prop_shape))
            if prop == key:
                graph.add((prop_shape, SH.minCount, Literal(1)))
                graph.add((prop_shape, SH.maxCount, Literal(1)))
            if prop == "status" and states:
                graph.add((prop_shape, SH["in"], _list(graph, [Literal(s) for s in states])))

        for rid in out_rels:
            rel = rel_by_id[rid]
            prop_shape = _bnode(graph, [
                (SH.path, iri(rid)),
                (SH["class"], iri(rel["range"])),
            ])
            graph.add((shape, SH.property, prop_shape))

    # 关系定义域/值域形状（C8）：不依赖实例是否声明 rdf:type
    for rel in relations:
        rid = rel["id"]

        range_shape = iri(f"{rid}RangeShape")
        graph.add((range_shape, RDF.type, SH.NodeShape))
        graph.add((range_shape, SH.targetSubjectsOf, iri(rid)))
        graph.add((range_shape, SH.property, _bnode(graph, [
            (SH.path, iri(rid)),
            (SH["class"], iri(rel["range"])),
            (SH.message, Literal(f"C8 关系 {rid} 值域必须是 {rel['range']}")),
        ])))

        domain_shape = iri(f"{rid}DomainShape")
        graph.add((domain_shape, RDF.type, SH.NodeShape))
        graph.add((domain_shape, SH.targetObjectsOf, iri(rid)))
        inverse = _bnode(graph, [(SH.inversePath, iri(rid))])
        graph.add((domain_shape, SH.property, _bnode(graph, [
            (SH.path, inverse),
            (SH["class"], iri(rel["domain"])),
            (SH.message, Literal(f"C8 关系 {rid} 定义域必须是 {rel['domain']}")),
        ])))

    # 显式业务约束
    builders = {
        "min_count": _constraint_min_count,
        "required_fields": _constraint_required_fields,
        "no_overlap": _constraint_no_overlap,
        "compare": _constraint_compare,
        "no_active_double": _constraint_no_active_double,
        "relation_domain_range": None,  # 已由关系形状覆盖
    }
    for constraint in layer.get("constraints") or []:
        ctype = constraint.get("type")
        if ctype not in builders:
            raise ValueError(f"未支持的约束类型: {ctype} ({constraint.get('id')})")
        builder = builders[ctype]
        if builder is not None:
            builder(graph, iri, prefixes, constraint, layer)

    return graph


def _constraint_min_count(graph, iri, prefixes, c, layer) -> None:
    shape = iri(f"{c['target']}Shape")
    rel = next(
        (r for r in layer.get("relations") or [] if r.get("id") == c["relation"]),
        None,
    )
    path = iri(c["relation"])
    # 目标类若在关系值域侧（如 Order 之于 initiates），需要反向路径
    if rel and rel.get("range") == c["target"] and rel.get("domain") != c["target"]:
        path = _bnode(graph, [(SH.inversePath, path)])
    graph.add((shape, SH.property, _bnode(graph, [
        (SH.path, path),
        (SH.minCount, Literal(int(c["min"]))),
        (SH.message, Literal(f"{c['id']} {c['label']}")),
    ])))


def _constraint_required_fields(graph, iri, prefixes, c, layer) -> None:
    """状态命中时字段必填；状态不在集合内时整体放行。"""
    shape = iri(f"{c['id']}Shape")
    graph.add((shape, RDF.type, SH.NodeShape))
    graph.add((shape, SH.targetClass, iri(c["target"])))

    statuses = list(c.get("when_status") or [])
    status_enum = _bnode(graph, [
        (SH.path, iri("status")),
        (SH["in"], _list(graph, [Literal(s) for s in statuses])),
    ])
    branches = [_bnode(graph, [(
        SH["not"], _bnode(graph, [(SH.property, status_enum)])
    )])]

    for status in statuses:
        members = [_bnode(graph, [(SH.property, _bnode(graph, [
            (SH.path, iri("status")),
            (SH.hasValue, Literal(status)),
        ]))])]
        for field in c.get("fields") or []:
            members.append(_bnode(graph, [(SH.property, _bnode(graph, [
                (SH.path, iri(field)),
                (SH.minCount, Literal(1)),
            ]))]))
        branches.append(_bnode(graph, [(SH["and"], _list(graph, members))]))

    graph.add((shape, SH["or"], _list(graph, branches)))
    graph.add((shape, RDFS.comment, Literal(f"{c['id']} {c['label']}")))


def _constraint_no_overlap(graph, iri, prefixes, c, layer) -> None:
    """车辆占用区间不得重叠：用 Trip 的 vehicle_id/started_at/ended_at 表达。"""
    query = """
SELECT $this ?other WHERE {
  $this mo:vehicle_id ?vehicle ; mo:started_at ?start1 .
  ?other mo:vehicle_id ?vehicle ; mo:started_at ?start2 .
  FILTER ($this != ?other && ?start1 < ?start2)
  OPTIONAL { $this mo:ended_at ?end1 }
  BIND (COALESCE(?end1, "9999-12-31T23:59:59"^^xsd:dateTime) AS ?limit)
  FILTER (?start2 < ?limit)
}
"""
    _add_sparql_shape(graph, iri, prefixes, c, "Trip", query)


def _constraint_compare(graph, iri, prefixes, c, layer) -> None:
    left_cls, left_field = c["left"].split(".", 1)
    right_cls, right_field = c["right"].split(".", 1)
    left_props = class_properties(layer).get(left_cls, [])
    right_props = class_properties(layer).get(right_cls, [])
    common = sorted(set(left_props) & set(right_props))
    # 优先用对方主键做关联；退化为同名 `_id` 字段
    join = next((p for p in common if p.endswith("_id")), common[0] if common else None)
    if not join:
        raise ValueError(f"{c['id']} 无法推导 {left_cls}/{right_cls} 的关联键")
    violation = {"gte": "<", "lte": ">", "gt": "<=", "lt": ">="}[c["op"]]
    query = f"""
SELECT $this ?other WHERE {{
  $this mo:{left_field} ?left ; mo:{join} ?key .
  ?other a mo:{right_cls} ; mo:{join} ?key ; mo:{right_field} ?right .
  FILTER (?left {violation} ?right)
}}
"""
    _add_sparql_shape(graph, iri, prefixes, c, left_cls, query)


def _constraint_no_active_double(graph, iri, prefixes, c, layer) -> None:
    """与运行时保持一致：CREATED/ARRIVED/ONBOARD 视为进行中。"""
    active = ('"CREATED"', '"ARRIVED"', '"ONBOARD"')
    values = ", ".join(active)
    query = f"""
SELECT $this ?t1 ?t2 WHERE {{
  $this ^mo:executed_by ?t1, ?t2 .
  FILTER (?t1 != ?t2)
  ?t1 mo:status ?s1 .
  ?t2 mo:status ?s2 .
  FILTER (?s1 IN ({values}) && ?s2 IN ({values}))
}}
"""
    _add_sparql_shape(graph, iri, prefixes, c, c["target"], query)


def _add_sparql_shape(graph, iri, prefixes, c, target, query) -> None:
    shape = iri(f"{c['id']}Shape")
    graph.add((shape, RDF.type, SH.NodeShape))
    graph.add((shape, SH.targetClass, iri(target)))
    graph.add((shape, RDFS.comment, Literal(f"{c['id']} {c['label']}")))
    graph.add((shape, SH.sparql, _bnode(graph, [
        (RDF.type, SH.SPARQLConstraint),
        (SH.message, Literal(f"{c['id']} {c['label']}")),
        (SH.prefixes, prefixes),
        (SH.select, Literal(query.strip(), datatype=XSD.string)),
    ])))


def build_negative_control(base_iri: str = DEFAULT_BASE) -> Graph:
    """构造同时违反 C3（核心形状）与 C5（SHACL-SPARQL）的最小数据图。"""
    graph = Graph()
    mo = Namespace(base_iri)

    trip = URIRef(base_iri + "neg_trip")
    graph.add((trip, RDF.type, mo.Trip))
    graph.add((trip, mo.trip_id, Literal("T-NEG-1")))
    graph.add((trip, mo.status, Literal("CREATED")))
    # 缺 driver_id / vehicle_id → C3

    payment = URIRef(base_iri + "neg_payment")
    billing = URIRef(base_iri + "neg_billing")
    graph.add((payment, RDF.type, mo.Payment))
    graph.add((payment, mo.payment_id, Literal("P-NEG-1")))
    graph.add((payment, mo.billing_id, Literal("B-NEG-1")))
    graph.add((payment, mo.amount, Literal("10", datatype=XSD.decimal)))
    graph.add((billing, RDF.type, mo.BillingRecord))
    graph.add((billing, mo.billing_id, Literal("B-NEG-1")))
    graph.add((billing, mo.total, Literal("20", datatype=XSD.decimal)))
    # 实付 10 < 应付 20 → C5

    return graph


def render(layer: dict, source: str, base_iri: str = DEFAULT_BASE) -> str:
    graph = build_shapes(layer, base_iri)
    version = (layer.get("meta") or {}).get("version", "?")
    header = (
        f"# SHACL shapes generated from {os.path.basename(source)} (version {version})\n"
        "# Canonical source remains the JSON DSL; do not edit this file.\n\n"
    )
    return header + graph.serialize(format="turtle")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="导出标准 SHACL NodeShape")
    parser.add_argument("source", nargs="?", default="semantic_layer.json")
    parser.add_argument("--base-iri", default=DEFAULT_BASE)
    parser.add_argument("--out", default=None, help="输出 TTL 路径；缺省打印到 stdout")
    args = parser.parse_args(argv)

    with open(args.source, encoding="utf-8") as f:
        layer = json.load(f)

    text = render(layer, args.source, args.base_iri)
    if args.out:
        out_dir = os.path.dirname(os.path.abspath(args.out))
        os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"SHACL 已导出: {args.out}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())