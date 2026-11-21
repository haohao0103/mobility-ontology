#!/usr/bin/env python3
"""OWL 2 推理实测：HermiT（标准 OWL 2 reasoner）直接 CLI 调用。

评测逻辑：本体如果只能被人读、不能被标准 reasoner 消费，就是死的目录。
这里验证三件事：
  1. 导出的 OWL 能被标准工具加载（HermiT）；
  2. 一致性检查通过（无矛盾公理导致不可满足本体）；
  3. 分类推理可执行（-c 输出类层级）。

实现说明：owlready2 的 sync_reasoner 内部给 HermiT CLI 传了无效选项
（-I），Java 侧报错——这是 owlready2 0.40.2 + HermiT 的组合 bug，
因此这里直接用 subprocess 调 HermiT CLI（已实测可用）。

边界说明：OWL 2 官方 Test Suite 用例文件托管在 GitHub（本机不可达），
需要 OWLAPI 级 runner；官方用例层面的覆盖在此如实登记为未执行。
本项证明的是「我们的本体可被标准 OWL 2 reasoner 消费且判定一致」。

真实发现（2026-11-21）：
  1. xsd:date 不在 OWL 2 datatype map——直接映射会让 HermiT 抛
     UnsupportedDatatypeException；导出器已改为 xsd:dateTime。
  2. 实例字面量不带 ^^datatype 时，与 rdfs:range 的 xsd:decimal/integer
     冲突，HermiT 判本体不一致；导出器已按声明类型补 datatype。
     两条都是序列化层互操作问题（本体内部类型自洽），已修复并回归。

用法：python3 scripts/benchmark/owl2_reason.py
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "scripts/_out/benchmark"
TTL = OUT / "ontology.ttl"
OWL = OUT / "ontology.owl"
HERMIT = Path("/Users/mac/Library/Python/3.9/lib/python/site-packages/"
              "owlready2/hermit/HermiT.jar")


def main() -> int:
    if not TTL.exists():
        print(f"缺少 {TTL}，先运行 scripts/benchmark/export_owl.py --with-instances")
        return 1

    steps = []
    # 1) RDF/XML 转换（HermiT CLI 更认 OWL/XML）
    t0 = time.time()
    from rdflib import Graph
    g = Graph().parse(TTL, format="turtle")
    g.serialize(destination=str(OWL), format="xml")
    steps.append({"step": "load", "ok": True,
                  "note": f"RDF/XML 转换成功，{len(g)} triples",
                  "elapsed_s": round(time.time() - t0, 2)})

    # 2) HermiT 一致性 + 分类
    t0 = time.time()
    r = subprocess.run(["java", "-Xmx3000M", "-cp", str(HERMIT),
                        "org.semanticweb.HermiT.cli.CommandLine", "-c", "-l", str(OWL)],
                       capture_output=True, text=True, timeout=300)
    err = (r.stderr or "") + (r.stdout or "")
    if "InconsistentOntologyException" in err:
        steps.append({"step": "consistency", "ok": False,
                      "note": "HermiT 判定本体不一致（存在矛盾公理）"})
    elif "UnsupportedDatatypeException" in err:
        steps.append({"step": "consistency", "ok": False,
                      "note": "HermiT 拒绝加载：存在 OWL 2 datatype map 之外的类型"})
    elif r.returncode == 0:
        steps.append({"step": "consistency", "ok": True,
                      "note": "HermiT 判定本体一致，且完成分类推理（-c）",
                      "elapsed_s": round(time.time() - t0, 2)})
    else:
        steps.append({"step": "consistency", "ok": False,
                      "note": f"HermiT 非零退出（rc={r.returncode}）：{err[-200:]}"})

    ok = all(s["ok"] for s in steps)
    for s in steps:
        print(f"[{'OK ' if s['ok'] else 'FAIL'}] {s['step']}: {s['note']}")
    print(f"RESULT {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
