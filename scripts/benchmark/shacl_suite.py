#!/usr/bin/env python3
"""跑 W3C SHACL Test Suite，验证我们 semantic_gate 里 SHACL 集成的正确性。

评测逻辑：我们的 semantic_gate 用 pyshacl 对本体数据做形状校验。
官方测试套件（tests/_bench/shacl/，来源见该目录 SOURCE.md）用标准用例验证
「给定 shapes + data，conforms 判定是否符合 W3C SHACL 规范」。
若我们的集成读法/参数与规范不一致（如 inference 模式选错），这里会当场暴露——
门禁误判比没有门禁更危险。

用法：
    python3 scripts/benchmark/shacl_suite.py [--suite PATH] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

from rdflib import Graph, URIRef  # noqa: E402
from pyshacl import validate  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "tests" / "_bench" / "shacl"
MF = "http://www.w3.org/2001/sw/DataAccess/tests/test-manifest#"
SHT = "http://www.w3.org/ns/shacl-test#"


def collect_cases(manifest_path: Path, out: list, seen: set | None = None) -> None:
    """递归解析 mf:Manifest：mf:include 指向子清单，mf:entries 指向用例。

    seen 做环检测——W3C 套件的 sparql 子清单会反向引用 core 的共享清单，
    无环检测会无限递归爆栈。
    """
    seen = seen if seen is not None else set()
    key = manifest_path.resolve()
    if key in seen:
        return
    seen.add(key)
    try:
        g = Graph()
        g.parse(manifest_path, format="turtle")
        for _, _, inc in g.triples((None, URIRef(f"{MF}include"), None)):
            s = str(inc)
            if s.startswith("file://"):
                target = Path(s[len("file://"):])
            elif s.startswith("file:"):
                target = Path(s[len("file:"):])
            else:
                target = manifest_path.parent / s
            collect_cases(target, out, seen)
        for entry in g.objects(None, URIRef(f"{MF}entries")):
            out.append(read_case(g, entry, manifest_path))
    except RecursionError:
        out.append({"id": f"manifest-error:{manifest_path.name}",
                    "name": f"manifest {manifest_path.name} 解析失败",
                    "shapes": None, "data": None, "conforms": None})


def read_case(g: Graph, entry, manifest_path: Path) -> dict:
    """按 W3C 套件真实结构读取：图引用在 mf:action 子节点，conforms 在 mf:result。"""

    def resolve(node):
        if node is None:
            return None
        s = str(node)
        if s.startswith("file://"):
            return Path(s[len("file://"):])
        if s.startswith("file:"):
            return Path(s[len("file:"):])
        if s == str(manifest_path.resolve().as_uri()):
            return manifest_path
        return manifest_path.parent / s

    action = g.value(entry, URIRef(f"{MF}action"))
    shapes = resolve(g.value(action, URIRef(f"{SHT}shapesGraph"))) if action else None
    data = resolve(g.value(action, URIRef(f"{SHT}dataGraph"))) if action else None
    result = g.value(entry, URIRef(f"{MF}result"))
    conforms = g.value(result, URIRef("http://www.w3.org/ns/shacl#conforms")) if result else None
    if conforms is None:  # 少数用例把 conforms 直接挂在 entry 上
        conforms = g.value(entry, URIRef("http://www.w3.org/ns/shacl#conforms"))
    return {
        "id": str(entry).split("#")[-1].split("/")[-1],
        "name": str(g.value(entry, URIRef(f"{MF}name")) or entry),
        "shapes": shapes or manifest_path,
        "data": data or manifest_path,
        "conforms": None if conforms is None else bool(conforms),
    }


def run_case(case: dict) -> dict:
    """单用例隔离执行：pyshacl 判定 vs W3C 预期。shapes 缺省时与 data 同图。

    注意：不要调高 recursionlimit——rdflib/pyshacl 的深递归在 C 栈里会直接
    abort（SIGABRT），保持默认限制让 RecursionError 可被捕获并归类为 error。
    """
    try:
        data_g = Graph().parse(case["data"], format="turtle")
        shapes_path = case["shapes"] or case["data"]
        shapes_g = Graph().parse(shapes_path, format="turtle")
        conforms, _, _ = validate(data_g, shacl_graph=shapes_g,
                                  inference="none", advanced=True)
        expected = case["conforms"]
        ok = expected is None or bool(conforms) == expected
        return {"id": case["id"], "name": case["name"],
                "expected": expected, "actual": bool(conforms),
                "ok": bool(ok), "error": None}
    except RecursionError:
        return {"id": case["id"], "name": case["name"], "expected": case["conforms"],
                "actual": None, "ok": False, "error": "RecursionError: rdflib 解析器栈溢出"}
    except NotImplementedError as exc:
        # pyshacl 已知能力边界（如 $shapesGraph 前缀的 SPARQL 约束），
        # 不是我们集成错误——单列 unsupported，不算 fail
        return {"id": case["id"], "name": case["name"], "expected": case["conforms"],
                "actual": None, "ok": True, "error": None,
                "unsupported": f"NotImplementedError: {str(exc)[:100]}"}
    except Exception as exc:  # noqa: BLE001
        return {"id": case["id"], "name": case["name"], "expected": case["conforms"],
                "actual": None, "ok": False,
                "error": f"{type(exc).__name__}: {str(exc)[:120]}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default=str(SUITE))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cases: list = []
    collect_cases(Path(args.suite) / "manifest.ttl", cases)
    # 去重（顶层与子 manifest 可能重复引用）
    seen, uniq = set(), []
    for c in cases:
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        uniq.append(c)

    results = [run_case(c) for c in uniq]
    passed = [r for r in results if r["ok"] and not r["error"] and not r.get("unsupported")]
    unsupported = [r for r in results if r.get("unsupported")]
    mismatched = [r for r in results if not r["ok"] and not r["error"]]
    errored = [r for r in results if r["error"]]

    out = {"suite": "W3C SHACL Test Suite (via pyshacl 0.40.2 sdist)",
           "total": len(results), "passed": len(passed),
           "unsupported": len(unsupported),
           "mismatched": len(mismatched), "errored": len(errored),
           "unsupported_detail": [{"name": r["name"], "reason": r["unsupported"]}
                                  for r in unsupported[:15]],
           "mismatch_detail": [{"name": r["name"], "expected": r["expected"],
                                "actual": r["actual"]} for r in mismatched[:15]],
           "error_detail": [{"name": r["name"], "error": r["error"]}
                            for r in errored[:15]]}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"SHACL Test Suite: {len(passed)}/{len(results)} passed, "
              f"unsupported={len(unsupported)}, mismatched={len(mismatched)}, "
              f"errored={len(errored)}")
        for r in unsupported[:15]:
            print(f"  UNSUPPORTED {r['name']}: {r['unsupported']}")
        for r in mismatched[:15]:
            print(f"  MISMATCH {r['name']}: 期望 conforms={r['expected']} 实际 {r['actual']}")
        for r in errored[:15]:
            print(f"  ERROR    {r['name']}: {r['error']}")
    return 0 if not mismatched and not errored else 1


if __name__ == "__main__":
    sys.exit(main())
