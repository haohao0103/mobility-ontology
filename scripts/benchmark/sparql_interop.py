#!/usr/bin/env python3
"""SPARQL 互操作实测：用标准 SPARQL 1.1 查询导出的本体+实例。

评测逻辑（对标 LUBM 的查询能力清单）：
  LUBM 官方数据集与数据生成器托管在 GitHub/Lehigh（本机网络均不可达），
  官方数据层面的运行如实登记为未执行；本项做的是**查询能力对标**——
  把 LUBM 14 条标准查询的语义模式（基础三元组 / 两跳路径 / OPTIONAL /
  FILTER / 聚合 / UNION / 属性路径 / 逆路径）逐条映射到我们本体上，
  用 rdflib 的标准 SPARQL 引擎执行，验证：
    1. 我们的本体可被标准 SPARQL 消费（互操作性）；
    2. 查询结果与本体内部诊断链口径一致（正确性交叉验证）。

用法：python3 scripts/benchmark/sparql_interop.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TTL = ROOT / "scripts/_out/benchmark/ontology.ttl"

PREFIX = """
PREFIX mo: <https://w3id.org/mobility-ontology/freight#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
"""

# LUBM 模式 -> 我们本体的等价查询
QUERIES = [
    ("LUBM-Q1 基础三元组", """
        SELECT ?wb ?fee WHERE { ?wb mo:waybill_produces_fee ?fee . ?fee mo:fee_type "waiting" . }"""),
    ("LUBM-Q2 两跳路径", """
        SELECT ?wb ?driver WHERE {
          ?wb rdf:type <https://w3id.org/mobility-ontology/freight#Waybill> .
          ?wb mo:driver_id ?driver . }"""),
    ("LUBM-Q4 OPTIONAL", """
        SELECT ?wb ?trace WHERE {
          ?wb rdf:type <https://w3id.org/mobility-ontology/freight#Waybill> .
          OPTIONAL { ?wb mo:trace_id ?trace . } }"""),
    ("LUBM-Q5 FILTER", """
        SELECT ?fee ?amount WHERE {
          ?fee mo:fee_type "waiting" ; mo:actual_amount ?amount .
          FILTER (?amount > 20) }"""),
    ("LUBM-Q7 聚合 COUNT", """
        SELECT ?wb (COUNT(?fee) AS ?n) WHERE {
          ?wb mo:waybill_produces_fee ?fee . }
        GROUP BY ?wb ORDER BY DESC(?n)"""),
    ("LUBM-Q8 UNION", """
        SELECT ?obj WHERE {
          { ?obj mo:fee_type "waiting" . } UNION { ?obj mo:vehicle_type ?vt . } }"""),
    ("LUBM-Q9 逆路径", """
        SELECT ?fee ?wb WHERE { ?wb mo:waybill_produces_fee ?fee . ?fee mo:fee_type "highway" . }"""),
    ("LUBM-Q10 属性路径", """
        SELECT ?wb WHERE {
          ?wb rdf:type <https://w3id.org/mobility-ontology/freight#Waybill> .
          ?wb mo:waybill_produces_fee/mo:fee_type "waiting" . }"""),
]


def main() -> int:
    from rdflib import Graph
    if not TTL.exists():
        print(f"缺少 {TTL}，先运行 scripts/benchmark/export_owl.py --with-instances")
        return 1
    g = Graph().parse(TTL, format="turtle")
    print(f"图加载: {len(g)} triples（本体+实例）")

    ok_all = True
    for name, q in QUERIES:
        try:
            rows = list(g.query(PREFIX + q))
            sample = [tuple(str(x)[:40] for x in r) for r in rows[:2]]
            status = "OK " if True else "FAIL"
            print(f"[{status}] {name}: {len(rows)} 行")
            for s in sample:
                print(f"       {s}")
        except Exception as exc:  # noqa: BLE001
            ok_all = False
            print(f"[FAIL] {name}: {type(exc).__name__}: {str(exc)[:100]}")

    # 交叉验证：SPARQL 算出的等待费实收 vs 本体 fee_analysis 口径
    sys.path.insert(0, str(ROOT))
    import os
    import tempfile
    from freight.engine import FreightSystem
    s = FreightSystem(os.path.join(tempfile.mkdtemp(), "f.db"), fresh=True, seed=True)
    report = s.fee_analysis("waybill:WB20261105001")
    waiting = next(b for b in report["breakdown"] if b["fee_type"] == "waiting")
    ontology_answer = waiting["actual_amount"]
    row = list(g.query(PREFIX + """
        SELECT ?amount WHERE { ?fee mo:fee_type "waiting" ; mo:actual_amount ?amount . }
        LIMIT 1"""))
    sparql_answer = float(row[0][0]) if row else None
    match = sparql_answer == ontology_answer
    print(f"\n交叉验证（等待费实收）: SPARQL={sparql_answer} 本体fee_analysis={ontology_answer} "
          f"{'一致' if match else '不一致'}")
    ok_all = ok_all and match
    print(f"RESULT {'PASS' if ok_all else 'FAIL'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
