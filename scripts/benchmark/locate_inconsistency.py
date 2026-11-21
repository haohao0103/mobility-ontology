#!/usr/bin/env python3
"""不一致性二分定位：TBox（已证一致）+ 实例子集组合跑 HermiT，定位矛盾三元组。

用法：python3 scripts/benchmark/locate_inconsistency.py
输出：最小矛盾集合（对象/关系/属性断言）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "scripts/_out/benchmark"
JAR = Path("/Users/mac/Library/Python/3.9/lib/python/site-packages/owlready2/hermit/HermiT.jar")


def hermit_consistent(ttl_path: Path) -> bool:
    from rdflib import Graph
    owl = ttl_path.with_suffix(".owl")
    try:
        Graph().parse(ttl_path, format="turtle").serialize(destination=str(owl), format="xml")
    except Exception:
        return True  # 解析失败不当不一致
    r = subprocess.run(["java", "-Xmx3000M", "-cp", str(JAR),
                        "org.semanticweb.HermiT.cli.CommandLine", "-c", "-l", str(owl)],
                       capture_output=True, text=True, timeout=300)
    return "InconsistentOntologyException" not in (r.stderr or "") + (r.stdout or "")


def instance_statements(db_props: bool = True) -> list[str]:
    """把实例层拆成独立语句行（每个对象/关系一条语句）。"""
    import sqlite3
    conn = sqlite3.connect(f"file:{ROOT / 'runs/freight_facts.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    stmts = []
    for row in conn.execute("SELECT object_id, type, name, props FROM objects"):
        iri = f"mo:{row['object_id'].replace(':', '_')}"
        stmts.append(f"<{iri}> a <{row['type']}> .")
        for k, v in (json.loads(row["props"]) or {}).items():
            if isinstance(v, bool):
                stmts.append(f"<{iri}> mo:{k} \"{'true' if v else 'false'}\"^^xsd:boolean .")
            elif isinstance(v, (int, float)):
                stmts.append(f"<{iri}> mo:{k} \"{v}\"^^xsd:decimal .")
            elif isinstance(v, str):
                stmts.append(f"<{iri}> mo:{k} \"{v.replace(chr(34), chr(39))}\" .")
    for row in conn.execute("SELECT subject_id, predicate, object_id FROM relations "
                            "WHERE valid_to IS NULL"):
        s = f"mo:{row['subject_id'].replace(':', '_')}"
        o = f"mo:{row['object_id'].replace(':', '_')}"
        stmts.append(f"<{s}> mo:{row['predicate']} <{o}> .")
    conn.close()
    return stmts


def main() -> int:
    tbox = OUT / "ontology.ttl"
    if not tbox.exists():
        print("先运行 export_owl.py")
        return 1
    tbox_text = tbox.read_text(encoding="utf-8")
    stmts = instance_statements()
    print(f"实例语句 {len(stmts)} 条，开始二分…")

    def check(subset: list[str]) -> bool:
        with tempfile.NamedTemporaryFile("w", suffix=".ttl", delete=False,
                                          encoding="utf-8") as fh:
            fh.write(tbox_text + "\n" + "\n".join(subset) + "\n")
            path = Path(fh.name)
        ok = hermit_consistent(path)
        path.unlink(missing_ok=True)
        Path(str(path).replace(".ttl", ".owl")).unlink(missing_ok=True)
        return ok

    lo, hi = 1, len(stmts)
    # 先确认全集不一致
    if check(stmts):
        print("全集一致——无法定位（可能环境差异）")
        return 0
    # 二分到最小不一致子集（单语句即不一致）
    current = stmts
    while len(current) > 1:
        mid = len(current) // 2
        if not check(current[:mid]):
            current = current[:mid]
        elif not check(current[mid:]):
            current = current[mid:]
        else:
            # 两半各自一致、合起来不一致：需要两半一起——保留前半继续缩
            current = current[:mid]
    print("最小矛盾语句（单条即致不一致）：")
    for s in current:
        print(" ", s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
