#!/usr/bin/env python3
"""本体评测一键复现入口：五项实测 + 汇总。

用法：python3 scripts/benchmark/run_all.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

STEPS = [
    ("OWL 导出", [sys.executable, str(HERE / "export_owl.py"), "--with-instances"]),
    ("SHACL Test Suite", [sys.executable, str(HERE / "shacl_suite.py")]),
    ("OOPS! 陷阱复刻扫描", [sys.executable, str(HERE / "oops_scan.py")]),
    ("OWL 2 HermiT 推理", [sys.executable, str(HERE / "owl2_reason.py")]),
    ("SPARQL 互操作", [sys.executable, str(HERE / "sparql_interop.py")]),
]


def main() -> int:
    results = []
    for name, cmd in STEPS:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
        results.append({"step": name, "rc": r.returncode,
                        "tail": (r.stdout or "")[-400:]})
        mark = "OK " if r.returncode == 0 else "FAIL"
        print(f"[{mark}] {name}")
    ok = all(r["rc"] == 0 for r in results)
    passed = sum(1 for r in results if r["rc"] == 0)
    print(f"\nRESULT {'PASS' if ok else 'FAIL'}（{passed}/{len(results)}）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
