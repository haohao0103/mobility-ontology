#!/usr/bin/env python3
"""演示问题 · 本体要素覆盖审计 + 「离开本体能否解答」对照实验。

回答两个问题：
1. 三个演示问题实际串联了本体系统的哪些要素（类/关系/规则/函数/动作/来源/状态机）；
2. 这些问题是不是**只有本体能解**——做三个对照：
   A. 裸 SQL 直查（绕开语义层、规则、状态机）
   B. 纯大模型（不给任何本体上下文）
   C. 本体全链路（现状）

用法：
    python scripts/demo_coverage_audit.py            # 全量
    python scripts/demo_coverage_audit.py --fast     # 只跑要素盘点，不跑 LLM 对照
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from freight import scenarios  # noqa: E402
from freight.agent import LLMClient, ReasoningAgent  # noqa: E402
from freight.engine import FreightSystem  # noqa: E402

QUESTIONS = [
    ("A 差异归因", "这单为什么比预估多 38 元？"),
    ("B 假设推演", "这单如果给客户减免 40 元，系统接下来会发生什么？会挡住哪一步？"),
    ("C 数据溯源", "这单的高速费是 ETC 给的还是 TMS 给的？谁认领这条费用？凭证是哪一条？"),
]


def build_system():
    td = tempfile.mkdtemp(prefix="audit_")
    system = FreightSystem(os.path.join(td, "facts.db"), fresh=True, seed=True,
                           with_scenario=True)
    scenarios.build_gap_cases(system)
    system.conn.commit()
    return system


def run_with_ontology(system, question: str) -> dict:
    agent = ReasoningAgent(system, "waybill:WB20261105001", max_steps=6,
                            question=question)
    agent.start()
    steps = 0
    t0 = time.time()
    while steps < 40 and not agent.finished:
        try:
            agent.next()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
        steps += 1
        if time.time() - t0 > 240:
            break
    return {
        "ok": True,
        "elapsed_s": round(time.time() - t0, 1),
        "steps": steps,
        "types": sorted(agent.types_seen),
        "tbox": sorted(agent.tbox_seen.keys()),
        "fee_types": sorted(agent.fee_types_seen),
        "tools": sorted({o.get("tool") for o in agent.observations if o.get("tool")}),
        "degraded": bool(getattr(agent, "degraded", False)),
    }


def bare_sql(system, waybill_id: str) -> dict:
    """对照 A：绕开语义层，用裸 SQL 查同一批表能得出什么。"""
    conn = sqlite3.connect(system.db_path)
    conn.row_factory = sqlite3.Row
    out = {}
    row = conn.execute(
        "SELECT props FROM objects WHERE object_id=?", (waybill_id,)).fetchone()
    props = json.loads(row["props"]) if row else {}
    out["运单上的标量"] = {
        "estimated_fee": props.get("estimated_fee"),
        "actual_fee": props.get("actual_fee"),
        "variance": props.get("variance"),
    }
    fees = conn.execute(
        "SELECT o.object_id, o.props FROM objects o WHERE o.type='FeeItem' "
        "AND o.object_id IN (SELECT object_id FROM relations "
        "WHERE subject_id=? AND predicate='waybill_produces_fee' AND valid_to IS NULL)",
        (waybill_id,)).fetchall()
    out["费用项（裸表）"] = [
        {"fee_id": r["object_id"], **{
            k: v for k, v in json.loads(r["props"]).items()
            if k in ("fee_type", "actual_amount", "quoted_amount", "pricing_rule_id",
                     "source_system", "status")}}
        for r in fees
    ]
    quotes = conn.execute(
        "SELECT o.object_id, o.props FROM objects o WHERE o.type='QuoteItem' "
        "AND o.object_id IN (SELECT object_id FROM relations "
        "WHERE subject_id IN (SELECT object_id FROM relations WHERE subject_id=? "
        "AND predicate='order_has_quote' AND valid_to IS NULL) "
        "AND predicate='quote_contains_item' AND valid_to IS NULL)",
        (waybill_id,)).fetchall()
    out["报价明细（裸表）"] = [
        {"quote_item_id": r["object_id"], **{
            k: v for k, v in json.loads(r["props"]).items()
            if k in ("fee_type", "amount")}}
        for r in quotes
    ]
    ev = conn.execute(
        "SELECT COUNT(*) n FROM evidence WHERE object_id IN "
        "(SELECT object_id FROM relations WHERE subject_id=? AND "
        "predicate='waybill_produces_fee' AND valid_to IS NULL)",
        (waybill_id,)).fetchone()
    out["费用证据条数（裸表）"] = ev["n"]
    conn.close()
    return out


def bare_llm(question: str, facts: dict) -> dict:
    """对照 B：不给任何本体上下文，纯问大模型（附上最原始的事实）。"""
    client = LLMClient()
    facts_text = json.dumps(facts, ensure_ascii=False)[:1500]
    prompt = (
        "你是货运结算专家。下面是系统导出的原始数据（字段名与业务含义未作任何说明）。\n"
        f"问题：{question}\n\n原始数据：\n{facts_text}\n\n"
        "请直接给出结论，并说明你的判断依据。"
    )
    t0 = time.time()
    try:
        out = client.chat([{"role": "user", "content": prompt}])
        return {"ok": True, "elapsed_s": round(time.time() - t0, 1), "answer": out}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "elapsed_s": round(time.time() - t0, 1),
                "error": f"{type(exc).__name__}: {exc}"[:200]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    system = build_system()
    report = {"questions": [], "ontology_elements": {}, "bare_sql": {}, "bare_llm": {}}
    for label, question in QUESTIONS:
        row = {"label": label, "question": question}
        row["with_ontology"] = run_with_ontology(system, question)
        report["questions"].append(row)
        if not args.fast:
            facts = bare_sql(system, "waybill:WB20261105001")
            report["bare_sql"][label] = facts
            report["bare_llm"][label] = bare_llm(question, facts)
        system2 = build_system()  # 每个问题用干净库，避免上一步的状态影响

    # 本体要素清单（从本体 JSON 统计）
    lay = json.load(open(os.path.join(ROOT, "freight", "semantic_layer.json"),
                        encoding="utf-8"))
    ext = json.load(open(os.path.join(ROOT, "enhancements", "ontology_extension.json"),
                        encoding="utf-8"))
    report["ontology_elements"] = {
        "类": len(lay["classes"]),
        "链接": len(lay["relations"]),
        "属性注册表": len(lay["property_types"]),
        "规则": len(lay["rules"]),
        "动作": len(lay["actions"]),
        "状态机": len(lay.get("state_machines") or {}),
        "函数": len(lay["functions"]),
        "数据源": len(lay["data_sources"]),
        "接口": len(ext["interfaces"]),
        "标记": len(ext["markings"]),
        "行级策略": len(ext["object_security_policies"]),
        "列级策略": len(ext["property_security_policies"]),
        "原语(MCP工具)": len(lay["primitives"]),
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print("=" * 92)
    print("演示问题 × 本体要素覆盖审计")
    print("=" * 92)
    for row in report["questions"]:
        r = row["with_ontology"]
        print(f"\n【{row['label']}】{row['question']}")
        if not r.get("ok"):
            print("  本体链路失败:", r.get("error"))
            continue
        print(f"  耗时 {r['elapsed_s']}s / {r['steps']} 步 / 降级={r['degraded']}")
        print(f"  触及类型({len(r['types'])}): {', '.join(r['types'][:12])}")
        print(f"  触及本体项({len(r['tbox'])}): {', '.join(r['tbox'][:14])}")
        print(f"  触及费用类型: {', '.join(r['fee_types'])}")
        print(f"  调用工具: {', '.join(r['tools'])}")

    print("\n" + "=" * 92)
    print("对照 A：裸 SQL 直查（绕开语义层 / 规则 / 状态机）能得到什么")
    print("=" * 92)
    if args.fast:
        print("  （--fast 跳过）")
    else:
        for label, facts in report["bare_sql"].items():
            print(f"\n  【{label}】")
            print(f"    运单标量: {facts['运单上的标量']}")
            print(f"    费用项: {len(facts['费用项（裸表）'])} 条 → "
                  f"{[f['fee_type'] for f in facts['费用项（裸表）']]}")
            print(f"    报价明细: {len(facts['报价明细（裸表）'])} 条")
            print(f"    证据条数: {facts['费用证据条数（裸表）']}")
            print("    ⚠ 裸表拿不到：规则应计值 / 状态机前置 / 来源归属 / 缺口判定")

    print("\n" + "=" * 92)
    print("对照 B：纯大模型（不给本体上下文，只给原始数据）")
    print("=" * 92)
    if args.fast:
        print("  （--fast 跳过）")
    else:
        for label, res in report["bare_llm"].items():
            print(f"\n  【{label}】ok={res.get('ok')} {res.get('elapsed_s')}s")
            print("   ", str(res.get("answer") or res.get("error"))[:420])

    print("\n" + "=" * 92)
    print("本体要素总量 vs 演示问题触及面")
    print("=" * 92)
    print("  本体声明:", json.dumps(report["ontology_elements"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
