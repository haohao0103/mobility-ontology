#!/usr/bin/env python3
"""演示问题 · 6 主线覆盖矩阵 + 「离开本体能否解答」对照实验。

回答两个问题：
1. 六条主线各串联了哪些本体要素、命不命中负向翻转（--fast 走确定性 labs，不依赖 LLM）；
2. 这些问题是不是**只有本体能解**——对收束问题做三个对照：
   A. 裸 SQL 直查（绕开语义层、规则、状态机）
   B. 纯大模型（不给任何本体上下文）
   C. 本体全链路（现状）

用法：
    python scripts/demo_coverage_audit.py            # 全量（含裸 LLM + 真实推理链）
    python scripts/demo_coverage_audit.py --fast     # 要素盘点 + 确定性翻转，不跑 LLM
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

# 6 主线：问题 → 本体核心件 → 负向翻转 → 依赖接口（与 web/order-ontology.html 的 DEMO_STORY 对齐）
MAIN_LINES = [
    ("M1 承重", "同一笔费用，换一版本本体，结论会变吗？",
     "规则 AST / 版本 / 生效期", "切 1.1.0-demo → BLOCKER",
     "GET /freight/waybills/{wb}/fee-analysis?version=..."),
    ("M2 状态机", "已送达的运单还能改派吗？",
     "状态机 / 角色 / 参数 / 幂等 / 审计", "FORBIDDEN_ROLE / INVALID_STATE / replayed=true",
     "POST /freight/actions/lab"),
    ("M3 来源契约", "这笔费用谁认领、缺什么证据？",
     "数据源契约 / 证据 / ABox 覆盖", "FEE_SOURCE_UNRESOLVED",
     "POST /freight/contracts/simulate"),
    ("M4 权限", "同一结论，不同角色看到的不一样",
     "marking / 行列策略 / Agent 输出闸", "OUTPUT_MARKING_DENIED / 可见行翻转",
     "POST /enh/security/guard-output / simulate-marking"),
    ("M5 AI", "AI 凭什么不编？",
     "工具白名单 / 类型圈定 / 证据 checksum", "TOOL_UNAVAILABLE / TYPE_UNBOUND",
     "POST /freight/agent/declaration-check"),
    ("M6 收束", "这单到底为什么贵 38？",
     "规则 / 版本 / 证据 / 可解释结论", "裸 LLM 应计 0 vs 本体应计 12",
     "裸 SQL / 裸 LLM / function.fee_breakdown"),
]

DEEP_LINES = [
    ("D1 血缘", "属性级/对象级血缘、影响面", "4097 节点 / 4129 边"),
    ("D2 治理", "版本快照、签字、回滚", "未签字 FAIL / 补签 PASS"),
    ("D3 语义查询", "Object Set / Ontology SQL / 接口多态", "INTERFACE_VIOLATION"),
    ("D4 属性注册表", "属性注册表、写入门禁", "TYPE_MISMATCH"),
]

COMPARE_QUESTION = "这单为什么比预估多 38 元？"
COMPARE_LABEL = "M6 收束"


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


def run_lab_checks(system) -> dict:
    """确定性负向翻转探测：内存本体副本 + SAVEPOINT，不写主库、不调 LLM。"""
    from freight import demo_labs as dl
    from enhancements.demo_labs import guard_output_lab, interface_simulate, simulate_marking
    from enhancements.runtime import EnhancedSystem

    enh = EnhancedSystem(system)

    def codes(payload):
        return [f.get("code") for f in (payload.get("flips") or [])
                if isinstance(f, dict)]

    action = dl.action_lab(system)
    action_codes = {c.get("code") for c in (action.get("cases") or [])}
    marking = simulate_marking(enh, "drop_source_marking", "ExceptionTicket")
    output = guard_output_lab(enh)
    iface = interface_simulate(enh, "drop_required_property")

    return {
        "M2 状态机": {
            "FORBIDDEN_ROLE": "FORBIDDEN_ROLE" in action_codes,
            "审计增量为零": action.get("audit_delta") == 0,
        },
        "M3 来源契约": {
            "FEE_SOURCE_UNRESOLVED": "FEE_SOURCE_UNRESOLVED"
            in codes(dl.contract_simulate(system, "drop_claim")),
        },
        "M4 权限": {
            "OUTPUT_MARKING_DENIED": "OUTPUT_MARKING_DENIED"
            in [f.get("code") for f in (output.get("findings") or [])
                if isinstance(f, dict)],
            "可见行翻转": marking["after"]["visible_rows"] > marking["before"]["visible_rows"],
        },
        "M5 AI": {
            "TOOL_UNAVAILABLE": "TOOL_UNAVAILABLE"
            in codes(dl.declaration_check(system, omit_tool="ontology.catalog")),
            "TYPE_UNBOUND": "TYPE_UNBOUND"
            in codes(dl.declaration_check(system, omit_type="FeeItem")),
        },
        "D2 治理": {
            "未签字FAIL": dl.guard_demo(system, "drop_property_unsigned").get("verdict") == "FAIL",
            "补签PASS": dl.guard_demo(system, "drop_property_signed").get("verdict") == "PASS",
        },
        "D3 语义查询": {
            "INTERFACE_VIOLATION": "INTERFACE_VIOLATION" in codes(iface),
        },
        "D4 属性注册表": {
            "TYPE_MISMATCH": "TYPE_MISMATCH" in codes(dl.type_lab(system)),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="不跑裸 LLM 与真实推理链")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    system = build_system()
    report = {
        "main_lines": [dict(zip(("id", "question", "core", "flip", "endpoint"), row))
                       for row in MAIN_LINES],
        "deep_lines": [dict(zip(("id", "core", "flip"), row)) for row in DEEP_LINES],
        "lab_checks": run_lab_checks(system),
        "bare_sql": {}, "bare_llm": {}, "with_ontology": {},
        "ontology_elements": {},
    }
    facts = bare_sql(system, "waybill:WB20261105001")
    report["bare_sql"][COMPARE_LABEL] = facts
    if not args.fast:
        report["bare_llm"][COMPARE_LABEL] = bare_llm(COMPARE_QUESTION, facts)
        report["with_ontology"][COMPARE_LABEL] = run_with_ontology(system, COMPARE_QUESTION)

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

    checks = [v for group in report["lab_checks"].values() for v in group.values()]
    ok = all(checks)
    report["flips_ok"] = ok

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    print("=" * 92)
    print("6 主线 + 4 深挖 · 本体要素覆盖矩阵（负向翻转走 labs，零写主库）")
    print("=" * 92)
    for row in report["main_lines"]:
        print(f"\n【{row['id']}】{row['question']}")
        print(f"  核心件: {row['core']}")
        print(f"  负向翻转: {row['flip']}")
        print(f"  依赖接口: {row['endpoint']}")
        hits = report["lab_checks"].get(row["id"], {})
        if hits:
            for name, hit in hits.items():
                print(f"  [{'OK ' if hit else 'FAIL'}] {name}")
        else:
            print("  [--] 走接口/裸 LLM 对照（见 demo_gate.py）")

    print("\n" + "=" * 92)
    print("深挖 4 题")
    print("=" * 92)
    for row in report["deep_lines"]:
        print(f"【{row['id']}】{row['core']} → {row['flip']}")
        for name, hit in report["lab_checks"].get(row["id"], {}).items():
            print(f"  [{'OK ' if hit else 'FAIL'}] {name}")

    print("\n" + "=" * 92)
    print("对照 A：裸 SQL 直查（绕开语义层 / 规则 / 状态机）能得到什么")
    print("=" * 92)
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
        print("  （--fast 跳过；实测原文见 plan/demo_script.md §3）")
    else:
        for label, res in report["bare_llm"].items():
            print(f"\n  【{label}】ok={res.get('ok')} {res.get('elapsed_s')}s")
            print("   ", str(res.get("answer") or res.get("error"))[:420])
        for label, res in report["with_ontology"].items():
            print(f"\n  【{label} · 本体链路】ok={res.get('ok')} {res.get('elapsed_s')}s "
                  f"步骤={res.get('steps')} 降级={res.get('degraded')}")

    print("\n" + "=" * 92)
    print("本体要素总量")
    print("=" * 92)
    print("  本体声明:", json.dumps(report["ontology_elements"], ensure_ascii=False))
    print(f"\n结论：负向翻转 {'全部命中' if ok else '存在未命中'}"
          f"（{sum(checks)}/{len(checks)}）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
