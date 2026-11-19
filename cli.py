#!/usr/bin/env python3
"""应用层命令行入口。

  python cli.py demo              完整示例 + 客服追溯
  python cli.py verify            跑 36 项验收并生成报告
  python cli.py seed -n 200       批量真实订单
  python cli.py dict              导出领域词典
  python cli.py rules             查看语义层规则
  python cli.py constraints       查看约束定义
  python cli.py shacl             导出标准 SHACL 形状
  python cli.py trace O1          客服追溯
  python cli.py stats             统计
  python cli.py serve             启动 REST 服务（固定端口 8765）
"""
from __future__ import annotations
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(ROOT, "runs")
os.makedirs(RUNS, exist_ok=True)

from mobility import MobilitySystem  # noqa: E402


def system(name: str, fresh: bool = False) -> MobilitySystem:
    s = MobilitySystem(os.path.join(RUNS, name), fresh=fresh)
    s.bootstrap()
    return s


def jd(x) -> str:
    return json.dumps(x, ensure_ascii=False, indent=2)


def main() -> int:
    ap = argparse.ArgumentParser(description="共享出行互联网本体 · 最小闭环 v1")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("demo", help="完整示例 A/O1/X/V3/T1/C1/P1 + 客服追溯")
    sub.add_parser("verify", help="跑验收套件并生成报告")
    p = sub.add_parser("seed", help="批量真实订单")
    p.add_argument("-n", type=int, default=200)
    p.add_argument("--seed", type=int, default=42)
    sub.add_parser("dict", help="导出领域词典")
    sub.add_parser("rules", help="查看规则")
    sub.add_parser("constraints", help="查看约束")
    p = sub.add_parser("shacl", help="导出标准 SHACL 形状")
    p.add_argument("--layer", default=os.path.join(ROOT, "semantic_layer.json"))
    p.add_argument("--base-iri",
                   default="https://w3id.org/mobility-ontology/core#")
    p.add_argument("--out", default=os.path.join(ROOT, "build", "shacl.ttl"))
    p = sub.add_parser("trace", help="客服追溯")
    p.add_argument("order_id")
    p.add_argument("--db", default="demo.db")
    p = sub.add_parser("stats", help="统计")
    p.add_argument("--db", default="cli_fleet.db")
    p = sub.add_parser("serve", help="启动 REST 服务")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--fresh", action="store_true")

    p = sub.add_parser("profile", help="画像 AI 语义层服务")
    p.add_argument("action", choices=[
        "health", "provision", "dict", "seed", "search", "context", "validate",
        "impact", "evidence", "metrics", "propose", "proposals", "confirm",
        "audit"])
    p.add_argument("target", nargs="?", help="资产 ID / 提议 ID")
    p.add_argument("--level", default="OVERVIEW")
    p.add_argument("--depth", type=int, default=2)
    p.add_argument("--query", default="")
    p.add_argument("--status")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--write-action", dest="write_action")
    p.add_argument("--reason")
    p.add_argument("--actor")
    p.add_argument("--approver")
    p.add_argument("--payload", default="{}")
    p.add_argument("--reject", action="store_true")

    args = ap.parse_args()

    if args.cmd == "demo":
        s = system("demo.db", fresh=True)
        sh = s.run_showcase()
        print(jd(sh))
        print()
        print(s.trace_text("O1"))
        return 0

    if args.cmd == "verify":
        import verify
        return verify.main()

    if args.cmd == "seed":
        from seed import seed_orders
        s = system("cli_fleet.db", fresh=True)
        r = seed_orders(s, n=args.n, seed=args.seed)
        print(jd({"n": r["n"], "scenarios": r["scenarios"],
                  "errors": r["errors"], "stats": r["stats"],
                  "violations": r["violations"]}))
        return 0 if not r["errors"] and not r["violations"] else 1

    if args.cmd == "dict":
        s = system("system.db")
        d = s.sem.dictionary()
        out = os.path.join(RUNS, "domain_dictionary.json")
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=2)
        print(f"领域词典已导出: {out}")
        print(f"对象 {d['class_count']} 类 / 关系 {d['relation_count']} 条 / "
              f"规则 {d['rule_count']} 条 / 约束 {d['constraint_count']} 条")
        for c in d["classes"]:
            print(f"  {c['id']:<14} {c['label']:<6} [{c['kind']}] {c['description']}")
        return 0

    if args.cmd == "rules":
        s = system("system.db")
        for r in s.sem.dictionary()["rules"]:
            print(f"{r['id']:<6} {r['domain']:<10} {r['name']}")
            print(f"       when  = {jd(r['when'])}")
            print(f"       judge = {r['judge']['decision']} / {r['judge'].get('fee_strategy','-')}")
            print(f"       actions = {r['actions']}")
        return 0

    if args.cmd == "constraints":
        s = system("system.db")
        for c in s.sem.constraints:
            print(f"{c['id']:<4} {c['label']}  type={c['type']} severity={c['severity']}")
        return 0

    if args.cmd == "shacl":
        from tools.export_shacl import main as export_main
        return export_main([
            args.layer, "--base-iri", args.base_iri, "--out", args.out])

    if args.cmd == "trace":
        s = system(args.db)
        print(s.trace_text(args.order_id))
        return 0

    if args.cmd == "stats":
        s = system(args.db)
        print(jd(s.stats()))
        print(f"约束违规: {len(s.validate())}")
        return 0

    if args.cmd == "serve":
        import uvicorn
        if args.fresh:
            from api import create_app
            app = create_app(fresh=True)
        else:
            from api import app
        print(f"REST 服务启动: http://{args.host}:{args.port}/profile/order-ontology  (文档 /docs)")
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
        return 0

    if args.cmd == "profile":
        from profile_ai.seed import seed_profile
        from profile_ai.service import ProfileService

        svc = ProfileService()
        a = args.action
        if a == "health":
            print(jd(svc.health()))
        elif a == "provision":
            print(jd(svc.provision()))
        elif a == "dict":
            print(jd(svc.dictionary()))
        elif a == "seed":
            print(jd(seed_profile(svc)))
        elif a == "search":
            print(jd(svc.search_assets(args.query, kind=args.target,
                                       status=args.status, limit=args.limit)))
        elif a == "context":
            print(jd(svc.get_context(args.target, level=args.level)))
        elif a == "validate":
            print(jd(svc.validate_crowd(args.target)))
        elif a == "impact":
            print(jd(svc.impact_analysis(args.target, depth=args.depth)))
        elif a == "evidence":
            print(jd(svc.trace_evidence(args.target)))
        elif a == "metrics":
            print(jd(svc.query_metric(asset_id=args.target,
                                      name=args.query or None, limit=args.limit)))
        elif a == "propose":
            print(jd(svc.propose_change(args.write_action, args.target,
                                        json.loads(args.payload), args.reason,
                                        args.actor)))
        elif a == "proposals":
            print(jd(svc.list_proposals(status=args.status, limit=args.limit)))
        elif a == "confirm":
            print(jd(svc.confirm_change(args.target, args.approver,
                                        approve=not args.reject)))
        elif a == "audit":
            print(jd(svc.list_audit(args.limit)))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())