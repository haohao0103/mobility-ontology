#!/usr/bin/env python3
"""属性图 vs 可执行本体：同题同数据的对照实验（CLI）。

核心实现在 freight/parity_lab.py（与 API /freight/ontology/property-graph-parity
共用同一实现、同一口径，本脚本只是终端美化输出）。

用法：
    python scripts/property_graph_parity.py
    python scripts/property_graph_parity.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from freight.parity_lab import build_report  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        report = build_report()
    except FileNotFoundError as exc:
        print(str(exc))
        return 1
    except ImportError as exc:
        print(f"缺少对照实验依赖（networkx）：{exc}")
        return 1

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print("=" * 96)
    print("属性图 vs 可执行本体：同数据同题的对照实验")
    print("=" * 96)
    for q in report["questions"]:
        print(f"\n【{q['id']}】{q['question']}　（考察：{q['probes']}）")
        print(f"  属性图 : {q['property_graph']['answer']}")
        print(f"          ↳ {q['property_graph']['note']}")
        print(f"  本体   : {q['ontology']['answer']}")
        print(f"          ↳ {q['ontology']['note']}")
        print(f"  结论：{q['dimension']}")
    print("\n" + "=" * 96)
    print(f"属性图方案把业务知识硬编码在服务代码里的行数："
          f"{report['property_graph_hardcoded_business_lines']} 行（9 组常量）")

    print("\n变更成本扫描（本仓库中业务参数出现的文件数）：")
    for token, info in report["change_cost_scan"].items():
        print(f"  {token!r}: {info['files']} 个文件，例如 {info['sample'][:4]}")

    print("\n【漏改实验】改免费等候 30->20，属性图方案只改诊断服务一处：")
    d = report["drift_experiment"]
    print(f"  属性图: 诊断说 {d['property_graph']['diagnosis']} 元 / 预检说 "
          f"{d['property_graph']['preflight']} 元 -> 一致？{d['property_graph']['consistent']}"
          f"（无任何机制能发现）")
    print(f"  本体  : 诊断与预检都读同一份声明 -> 同一来源？{d['ontology']['same_source']}"
          f"（{d['ontology']['note']}）")

    print("\n【完整对齐估算】属性图方案要硬编码我们这套本体的全部声明，约需：")
    est = report["full_alignment_estimate"]
    for k, v in est["breakdown"].items():
        print(f"  {k:22s} {v:6d} 行")
    print(f"  {'合计':22s} {est['total_estimated_lines']:6d} 行"
          f"（且无 schema 校验 / 门禁 / 承重保护）")
    print("=" * 96)
    print("结论：Q1 属性图完全能做（诚实承认，不当卖点）；Q2/Q3/Q5 的差别是"
          "『知识在声明里还是在代码里』；Q4 缺口语义属性图没有；"
          "Q6 变更成本与漏改风险是分水岭。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
