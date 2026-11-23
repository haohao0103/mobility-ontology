#!/usr/bin/env python3
"""运费本体端到端验收：四个固定场景，纯标准库，零依赖。"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from freight.engine import FreightSystem  # noqa: E402
from freight.fact_mcp import BusinessReject  # noqa: E402
from freight.semantic import (  # noqa: E402
    SemanticLayer, evaluate_rule_ast, validate_semantic,
)

WAYBILL_ID = "waybill:WB20261105001"
ACCEPTED_WAYBILL_ID = "waybill:WB20261106001"


def call(system: FreightSystem, tool: str, arguments: dict) -> dict:
    return system.mcp_call(tool, arguments)["result"]


def expect_reject(system: FreightSystem, tool: str, arguments: dict,
                  code: str) -> BusinessReject:
    try:
        system.mcp_call(tool, arguments)
    except BusinessReject as exc:
        if exc.code != code:
            raise AssertionError(
                f"{tool} 预期拒绝码 {code}，实际 {exc.code}: {exc}"
            ) from exc
        return exc
    raise AssertionError(f"{tool} 预期被拒绝，实际执行成功")


def scenario_fee_variance(system: FreightSystem) -> None:
    result = call(system, "function.fee_breakdown", {"waybill_id": WAYBILL_ID})
    assert result["quoted_total"] == 480.0, result["quoted_total"]
    assert result["actual_total"] == 518.0, result["actual_total"]
    assert result["variance"] == 38.0, result["variance"]

    waiting = next(
        row for row in result["breakdown"] if row["fee_type"] == "waiting"
    )
    assert waiting["quoted_amount"] == 0.0, waiting
    assert waiting["actual_amount"] == 25.0, waiting
    assert waiting["rule_expected"] == 12.0, waiting
    assert waiting["status"] == "DISPUTED", waiting

    print(
        "  报价 480.00 / 实收 518.00 / 差异 +38.00；"
        f"等候费 0.00 -> 25.00（规则应为 12.00）"
    )
    print(
        "  公式：ceil(max(0, 53 - 30) / 5) * 2.4 = "
        f"{result['waiting_rule']['expected']:.2f}"
    )


def scenario_quote_reconcile(system: FreightSystem) -> None:
    objects = call(
        system, "ontology.query", {"type": "Waybill", "limit": 50}
    )["objects"]
    missing = []
    for obj in objects:
        reconciliation = call(
            system,
            "function.quote_reconcile",
            {"waybill_id": obj["object_id"]},
        )
        waiting = next(
            (
                row for row in reconciliation["rows"]
                if row["fee_type"] == "waiting"
            ),
            None,
        )
        if waiting and not waiting["included_in_quote"]:
            missing.append({
                "waybill_id": reconciliation["waybill_id"],
                "quote_id": reconciliation["quote_id"],
                "quoted": waiting["quoted_amount"],
                "actual": waiting["actual_amount"],
                "delta": waiting["delta"],
            })

    assert missing, "没有发现报价缺失等候费的运单"
    target = next(
        (item for item in missing if item["waybill_id"] == WAYBILL_ID), None
    )
    assert target is not None, missing
    assert target["quote_id"] == "quote:Q20261105001", target
    assert target["actual"] == 25.0, target

    print(f"  扫描 {len(objects)} 张运单，发现 {len(missing)} 张报价缺失等候费")
    for item in missing:
        print(
            f"  {item['waybill_id']} / {item['quote_id']}："
            f"报价 {item['quoted']:.2f}，实际 {item['actual']:.2f}，"
            f"差异 +{item['delta']:.2f}"
        )


def patch_props(system: FreightSystem, object_id: str, updates: dict) -> None:
    row = system.facts.get_row(object_id)
    if not row:
        raise AssertionError(f"对象不存在: {object_id}")
    props = json.loads(row["props"])
    props.update(updates)
    system.facts.update_object(
        object_id, props, row["name"], row["status"], row["updated_at"]
    )


def scenario_rule_binding_and_generic_conclusion(system: FreightSystem) -> None:
    fee_id = "fee:F003"
    rule_id = "pricing_rule:PR-WAIT-01"
    fee_row = system.facts.get_row(fee_id)
    rule_row = system.facts.get_row(rule_id)
    fee_props = json.loads(fee_row["props"])
    rule_props = json.loads(rule_row["props"])
    try:
        # 1) 改实际金额后，结论必须由 breakdown 重新生成，而不是复用 38 元文案。
        patch_props(system, fee_id, {"actual_amount": 30.0})
        changed = call(system, "function.fee_breakdown", {"waybill_id": WAYBILL_ID})
        assert changed["variance"] == 43.0, changed["variance"]
        assert "43.00" in changed["conclusion"], changed["conclusion"]
        assert "18.00" in changed["conclusion"], changed["conclusion"]
        assert "38.00" not in changed["conclusion"], changed["conclusion"]

        # 2) 改本体声明（决策表命中行）后，应计金额必须随之变化；
        #    规则实例只供版本元信息，不再供参数（唯一源是 matrix）。
        patch_props(system, fee_id, {"actual_amount": 25.0})
        waiting_matrix = system.sem.pricing["waiting"]["matrix"]
        hit_row = next(r for r in waiting_matrix
                       if r.get("id") == "vehicle:4.2米厢货")
        original_per_block = hit_row["params"]["per_block"]
        try:
            hit_row["params"]["per_block"] = 3.0
            rebound = call(system, "function.fee_breakdown",
                           {"waybill_id": WAYBILL_ID})
            waiting = next(
                row for row in rebound["breakdown"] if row["fee_type"] == "waiting"
            )
            assert rebound["waiting_rule"]["pricing_rule_id"] == "PR-WAIT-01"
            assert waiting["rule_expected"] == 15.0, waiting
            assert rebound["waiting_rule"]["expected"] == 15.0, rebound["waiting_rule"]
        finally:
            hit_row["params"]["per_block"] = original_per_block

        # 3) 费用发生时间超出规则生效期时，R-PRICE-01 必须给出拒绝性 finding。
        patch_props(system, rule_id, {"params": rule_props["params"],
                                      "effective_to": "2026-10-31"})
        expired = call(system, "function.fee_breakdown", {"waybill_id": WAYBILL_ID})
        expired_finding = next(
            (
                item for item in expired["findings"]
                if item["code"] == "PRICING_RULE_EXPIRED"
            ),
            None,
        )
        assert expired_finding is not None, expired["findings"]
        assert expired_finding["rule_id"] == "R-PRICE-01", expired_finding
        assert expired_finding["pricing_rule_id"] == "PR-WAIT-01", expired_finding
    finally:
        patch_props(system, fee_id, {
            "actual_amount": fee_props["actual_amount"],
        })
        patch_props(system, rule_id, {
            "params": rule_props["params"],
            "effective_to": rule_props["effective_to"],
        })

    restored = call(system, "function.fee_breakdown", {"waybill_id": WAYBILL_ID})
    assert restored["variance"] == 38.0, restored["variance"]
    assert not any(
        item["code"] == "PRICING_RULE_EXPIRED" for item in restored["findings"]
    ), restored["findings"]

    print("  实际金额改为 30.00：结论自动变为多 43.00、超收 18.00，无 38.00 特判")
    print("  规则参数改为每块 3.00：fee_priced_by_rule 解析后应计变为 15.00")
    print("  规则生效期截止 2026-10-31：R-PRICE-01 返回 PRICING_RULE_EXPIRED")


def scenario_action_governance(system: FreightSystem) -> None:
    preflight = call(
        system, "function.action_preflight", {"object_id": ACCEPTED_WAYBILL_ID}
    )
    available = set(preflight["available"])
    blocked = {item["action_id"] for item in preflight["blocked"]}
    assert "confirm_loading" in available, available
    assert "reassign_waybill" in available, available
    assert "accept_order" in blocked, blocked
    assert "complete" in blocked, blocked

    surface = system.facts.tools_for("Waybill")["tools"]
    names = {tool["name"] for tool in surface}
    assert "action.execute" not in names, names
    assert "action.plan" in names, names
    assert "function.action_preflight" in names, names

    denied = expect_reject(
        system,
        "action.execute",
        {
            "action_id": "confirm_loading",
            "object_id": ACCEPTED_WAYBILL_ID,
            "actor": "acceptance",
            "role": "finance",
            "payload": {},
        },
        "FORBIDDEN_ROLE",
    )
    assert "driver" in str(denied), denied

    executed = call(
        system,
        "action.execute",
        {
            "action_id": "confirm_loading",
            "object_id": ACCEPTED_WAYBILL_ID,
            "actor": "acceptance",
            "role": "driver",
            "payload": {},
        },
    )
    assert executed["after_status"] == "LOADED", executed
    assert executed.get("audit_id"), executed

    print(
        f"  可执行：{', '.join(sorted(available))}；"
        f"被阻断：{', '.join(sorted(blocked))}"
    )
    print("  诊断能力集不含 action.execute；finance 越权被 FORBIDDEN_ROLE 拒绝")
    print(
        "  driver 执行装货确认成功：ACCEPTED -> "
        f"{executed['after_status']}，audit_id={executed['audit_id']}"
    )


def scenario_violation_guard(system: FreightSystem) -> None:
    fabricated = expect_reject(
        system,
        "ontology.query",
        {"type": "远程服务费", "limit": 1},
        "SCHEMA_ERROR",
    )
    assert "Waybill" in fabricated.details["allowed_types"], fabricated.details

    unknown_tool = expect_reject(
        system,
        "query_driver_salary",
        {"driver_id": "D001"},
        "MCP_TOOL_NOT_FOUND",
    )
    assert "MCP" in str(unknown_tool), unknown_tool

    illegal_relation = expect_reject(
        system,
        "ontology.traverse",
        {"object_id": WAYBILL_ID, "predicate": "包含司机薪酬"},
        "SCHEMA_ERROR",
    )
    assert (
        "包含司机薪酬" not in illegal_relation.details["allowed_relations"]
    ), illegal_relation.details

    wrong_target = expect_reject(
        system,
        "action.execute",
        {
            "action_id": "confirm_loading",
            "object_id": "fee:F001",
            "actor": "acceptance",
            "role": "driver",
            "payload": {},
        },
        "ONTOLOGY_VIOLATION",
    )
    assert "Waybill" in str(wrong_target), wrong_target

    print("  编造类型 -> SCHEMA_ERROR")
    print("  越界工具 query_driver_salary -> MCP_TOOL_NOT_FOUND")
    print("  非法关系 包含司机薪酬 -> SCHEMA_ERROR")
    print("  动作作用到错误对象 -> ONTOLOGY_VIOLATION")


def scenario_real_llm_agent(system: FreightSystem) -> None:
    """真实交互式路径：每次 next 只执行一个真实动作，第一步必须选 fee_breakdown。"""
    result = system.agent_chain_start(
        WAYBILL_ID,
        max_steps=6,
        question="这张运单为什么比报价高 38 元？请用确定性函数拆解并核对证据。",
    )
    assert result["mode"] == "agent", result
    assert result["finished"] is False, result
    assert result["steps"] == [], result["steps"]
    session_id = result["session_id"]

    executed = []
    while not result["finished"]:
        before = len(result["steps"])
        result = system.agent_chain_next(session_id)
        assert result["mode"] == "agent", result
        assert len(result["steps"]) == before + 1, (
            f"一次 next 必须只增加一个真实步骤：{before} -> {len(result['steps'])}"
        )
        executed.append(result["steps"][-1]["title"])

    business_tools = [
        step["tool"] for step in result["steps"]
        if step.get("tool") and str(step.get("tag", "")).startswith("Agent · ")
    ]
    assert result["agent"]["ok"], result["agent"].get("error")
    assert business_tools, "Agent 没有发起任何业务查询"
    assert business_tools[0] == "function.fee_breakdown", business_tools
    first = result["steps"][0]
    assert first.get("data"), first

    fee_step = next(
        step for step in result["steps"]
        if step.get("tool") == "function.fee_breakdown"
    )
    assert fee_step["ok"], fee_step
    assert "38" in json.dumps(fee_step, ensure_ascii=False), fee_step
    assert "38" in json.dumps(result["steps"][-1], ensure_ascii=False), result["steps"][-1]

    print(f"  交互步数：{len(executed)} 次 next，每次只执行 1 个动作")
    print(f"  Agent 实际调用顺序：{' -> '.join(business_tools)}")
    print("  第一步 function.fee_breakdown 已返回并写入该步数据集")
    print("  全程 mode=agent，无固定链路、无降级替身，最终结论引用 38 元差异")


def _waiting_row(analysis: dict) -> dict:
    return next(
        row for row in analysis["breakdown"] if row["fee_type"] == "waiting"
    )


def scenario_ontology_removal(system: FreightSystem) -> None:
    """删公式测试：拿掉本体里的可执行公式，12 元结论必须随之消失。"""
    sem = SemanticLayer()
    sem.rules["R-WAIT-01"]["judge"].pop("ast", None)
    analysis = system.fee_analysis(WAYBILL_ID, sem=sem)
    waiting = _waiting_row(analysis)
    assert waiting["rule_expected"] is None, waiting
    assert waiting["rule_delta"] is None, waiting
    codes = {finding["code"] for finding in analysis["findings"]}
    assert "ONTOLOGY_COMPUTE_MISSING" in codes, codes
    assert "WAITING_FEE_MISMATCH" not in codes, codes
    assert not any(
        finding.get("expected") == 12.0 for finding in analysis["findings"]
    ), analysis["findings"]
    print("  删除 R-WAIT-01.judge.ast 后：应计金额不可核算，"
          "不再给出 12.00 元与超收结论")


def scenario_ontology_formula_change(system: FreightSystem) -> None:
    """改公式测试：只改本体公式，业务金额必须跟着变。"""
    sem = SemanticLayer()
    judge = sem.rules["R-WAIT-01"]["judge"]
    judge["ast"] = {
        "op": "mul",
        "args": [judge["ast"], {"op": "const", "value": 2}],
    }
    analysis = system.fee_analysis(WAYBILL_ID, sem=sem)
    waiting = _waiting_row(analysis)
    assert waiting["rule_expected"] == 24.0, waiting
    mismatch = next(
        f for f in analysis["findings"] if f["code"] == "WAITING_FEE_MISMATCH"
    )
    assert mismatch["expected"] == 24.0, mismatch
    assert mismatch["delta"] == 1.0, mismatch
    print("  只把本体公式改为每块 2 倍：应计 12.00 -> 24.00，"
          "超收 13.00 -> 1.00，代码未改")


def scenario_second_consumer(system: FreightSystem) -> None:
    """第二消费者测试：换一个业务出口，同样跟着本体公式变化。"""
    sem = SemanticLayer()
    judge = sem.rules["R-WAIT-01"]["judge"]
    judge["ast"] = {
        "op": "mul",
        "args": [judge["ast"], {"op": "const", "value": 2}],
    }
    answer = system.explain(WAYBILL_ID, "finance", sem=sem)["answer"]
    assert "24.00" in answer, answer
    base = system.explain(WAYBILL_ID, "finance")["answer"]
    assert "12.00" in base, base
    print("  财务解释（第二个消费者）随本体口径从 12.00 变为 24.00")


def scenario_dirty_ontology_rejected(system: FreightSystem) -> None:
    """脏本体测试：引用断裂或公式损坏必须被校验拦下。"""
    raw = copy.deepcopy(system.sem.raw)
    raw["pricing"]["waiting"]["rule_id"] = "R-NOT-EXIST"
    for rule in raw["rules"]:
        if rule["id"] == "R-WAIT-01":
            rule["judge"]["ast"] = {"op": "unknown", "args": []}
    codes = {issue["code"] for issue in validate_semantic(raw)}
    assert "PRICING_RULE_UNKNOWN" in codes, codes
    assert "RULE_AST_OP_INVALID" in codes, codes
    print("  断链 rule_id -> PRICING_RULE_UNKNOWN；"
          "损坏公式 -> RULE_AST_OP_INVALID")


def scenario_unseen_fee_type(system: FreightSystem) -> None:
    """未见问题测试：只加本体定义，不改代码就能算新费用类型。"""
    assert "remote_area" not in system.sem.pricing, system.sem.pricing.keys()
    sem = SemanticLayer()
    sem.pricing["remote_area"] = {"rule_id": "R-REMOTE-01", "per_order": 35.0}
    sem.rules["R-REMOTE-01"] = {
        "id": "R-REMOTE-01",
        "name": "远程服务费按单收取",
        "domain": "FeeItem",
        "when": {"all": [{"field": "fee_type", "op": "eq", "value": "remote_area"}]},
        "judge": {
            "ast": {"op": "field", "path": "params.per_order"},
            "calc": {
                "target": "actual_amount",
                "unit": "CNY",
                "rounding": "2",
                "bindings": {},
            },
            "explain": "远程服务费按订单固定金额收取。",
            "severity": "MEDIUM",
        },
    }
    system.add_object("FeeItem", "fee:R001", "远程服务费", "NORMAL", {
        "fee_id": "R001", "fee_type": "remote_area", "fee_label": "远程服务费",
        "quoted_amount": 0.0, "expected_amount": 0.0, "actual_amount": 35.0,
        "waived_amount": 0.0, "calc_basis": "本体新增费用类型",
        "pricing_rule_id": "PR-REMOTE-01", "pricing_rule_version": "2026.10",
        "occurred_at": "2026-11-05T12:18:00+08:00", "source_system": "TMS",
    })
    system.add_relation("waybill_produces_fee", WAYBILL_ID, "fee:R001",
                        "2026-11-05T12:20:00+08:00")
    system.add_object("PricingRule", "pricing_rule:PR-REMOTE-01", "定价规则 PR-REMOTE-01",
                      "ACTIVE", {
        "rule_id": "PR-REMOTE-01", "fee_type": "remote_area", "city": "上海",
        "vehicle_type": "4.2米厢货", "effective_from": "2026-10-01",
        "effective_to": "2026-12-31", "version": "2026.10", "currency": "CNY",
        "params": {"per_order": 35.0}, "source_system": "计价中心",
    })
    system.add_relation("fee_priced_by_rule", "fee:R001", "pricing_rule:PR-REMOTE-01",
                        "2026-11-05T12:20:00+08:00")
    analysis = system.fee_analysis(WAYBILL_ID, sem=sem)
    row = next(item for item in analysis["breakdown"] if item["fee_id"] == "R001")
    assert row["rule_expected"] == 35.0, row
    assert row["rule_delta"] == 0.0, row
    print("  运行时仅注入 remote_area 的本体定价与公式："
          "新费用应计 35.00 元，Python 无新增分支")


def _run_compute(sem: SemanticLayer, rule_id: str, ctx: dict) -> tuple[object, dict]:
    judge = sem.rules[rule_id]["judge"]
    value = evaluate_rule_ast(judge["ast"], ctx)
    meta = {
        key: evaluate_rule_ast(expr, ctx)
        for key, expr in (judge.get("calc", {}).get("bindings") or {}).items()
    }
    return value, meta


def scenario_carrier_multistop_tbox(system: FreightSystem) -> None:
    """真实业务扩展：承运商分包与多点装卸的类、关系、规则、动作都可执行。"""
    sem = system.sem
    for cid in ("Carrier", "SubcontractOrder", "Stop"):
        assert cid in sem.classes, cid
    for rid in ("waybill_has_stop", "stop_handles_cargo",
                "waybill_has_subcontract", "subcontract_awarded_to_carrier",
                "subcontract_allocates_fee"):
        assert rid in sem.relations, rid

    deps = set(sem.diagnosis_required_types("Waybill"))
    assert {"Carrier", "SubcontractOrder", "Stop", "Cargo"} <= deps, deps

    assert sem.paths("Waybill", "Carrier", max_depth=4), "Waybill -> Carrier 路径缺失"
    assert sem.paths("Waybill", "Stop", max_depth=2), "Waybill -> Stop 路径缺失"

    assert any(rule["id"] == "R-SUBCONTRACT-01"
               for rule in sem.rules_for("SubcontractOrder", {"status": "CONFIRMED"}))
    assert any(rule["id"] == "R-MULTISTOP-01"
               for rule in sem.rules_for("Waybill", {"status": "LOADED"}))

    stop_gap, stop_meta = _run_compute(sem, "R-MULTISTOP-01", {
        "waiting_minutes": 95, "stop_waiting_minutes": [30, 40, 20],
    })
    assert stop_gap == 5, stop_gap
    assert stop_meta["stop_total"] == 90 and stop_meta["stop_count"] == 3, stop_meta

    over, over_meta = _run_compute(sem, "R-SUBCONTRACT-01", {
        "allocated_amount": 850.0, "waybill_revenue": 1000.0, "min_margin_rate": 0.2,
    })
    assert over == 50.0, over
    assert over_meta["max_allocatable"] == 800.0, over_meta

    assert sem.actions["confirm_subcontract"]["to_status"] == "CONFIRMED"
    assert sem.actions["arrive_stop"]["to_status"] == "ARRIVED"
    assert sem.actions["depart_stop"]["to_status"] == "COMPLETED"
    print("  承运商/分包单/装卸点入本体：诊断依赖自动扩展，"
          "分包超额 50.00 元、逐站等候缺口 5 分钟可由本体公式算出")


def scenario_object_set_pagination(system: FreightSystem) -> None:
    """Object Set：分页并集与一次性结果一致，link summary 可核对。"""
    def page_all(tool: str, base: dict, field: str) -> tuple[list[str], dict]:
        one = call(system, tool, {**base, "limit": 500})
        ids = [item["object_id"] for item in one[field]]
        pages: list[str] = []
        token = None
        while True:
            args = {**base, "limit": 3}
            if token:
                args["next_page_token"] = token
            page = call(system, tool, args)
            assert page["object_set_id"] == one["object_set_id"]
            assert page["snapshot_id"] == one["snapshot_id"]
            pages.extend(item["object_id"] for item in page[field])
            token = page["next_page_token"]
            if not token:
                break
        assert pages == ids, (len(pages), len(ids))
        assert len(pages) == len(set(pages)), "分页出现重复对象"
        return pages, one

    query = {"type": "Waybill"}
    query_ids, query_one = page_all("ontology.query", query, "objects")
    assert len(query_ids) == query_one["count"], query_one["count"]

    ordered_ids, ordered_one = page_all(
        "ontology.query",
        {"type": "FeeItem", "order_by": ["quoted_amount desc"]},
        "objects",
    )
    assert ordered_ids == [item["object_id"] for item in ordered_one["objects"]]
    amounts = [
        item["properties"]["quoted_amount"]
        for item in ordered_one["objects"]
    ]
    assert amounts == sorted(amounts, reverse=True), amounts

    search = {
        "object_ids": [WAYBILL_ID],
        "link_type_ids": ["waybill_produces_fee"],
        "direction": "out",
        "depth": 1,
    }
    search_ids, search_one = page_all("ontology.search_around", search, "objects")
    assert len(search_ids) == search_one["count"], search_one["count"]
    recount = sum(
        row["relation_count"] for row in search_one["link_type_summary"]
    )
    assert recount == len(search_one["relations"]), (
        recount, len(search_one["relations"])
    )

    try:
        call(
            system,
            "ontology.query",
            {**query, "limit": 3, "next_page_token": "not-a-token"},
        )
    except BusinessReject as exc:
        assert exc.code == "TOKEN_INVALID", exc.code
    else:
        raise AssertionError("损坏 token 应被拒绝")

    print(
        f"  运单分页 {len(query_ids)} 条、search-around 分页 "
        f"{len(search_ids)} 条；并集与一次性结果一致，"
        "link_type_summary 计数可核对"
    )


SCENARIOS = [
    ("场景 1：费用差异（为什么高 38 元）", scenario_fee_variance),
    ("场景 2：跨对象对账（哪些报价未含等待费）", scenario_quote_reconcile),
    ("场景 3：规则绑定与结论泛化（无 38 特判 / 生效期校验）",
     scenario_rule_binding_and_generic_conclusion),
    ("场景 4：动作治理（可用动作 / 规则阻断 / 越权）", scenario_action_governance),
    ("场景 5：违规注入（编造类型 / 越界工具 / 非法关系）", scenario_violation_guard),
    ("承重 1：删公式测试（删掉本体公式结论必须消失）", scenario_ontology_removal),
    ("承重 2：改公式测试（只改本体金额必须跟着变）", scenario_ontology_formula_change),
    ("承重 3：第二消费者测试（换出口同样跟着本体变）", scenario_second_consumer),
    ("承重 4：脏本体测试（断链/坏公式必须被拦下）", scenario_dirty_ontology_rejected),
    ("承重 5：未见问题测试（只加本体就能算新费用）", scenario_unseen_fee_type),
    ("场景 7：真实业务扩展（承运商分包 / 多点装卸）", scenario_carrier_multistop_tbox),
    ("场景 8：Object Set 分页与血缘", scenario_object_set_pagination),
]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    scenarios = list(SCENARIOS)
    if "--llm" in args:
        scenarios.append((
            "场景 6：真实 LLM 交互式路径（每步单动作，首步 fee_breakdown）",
            scenario_real_llm_agent,
        ))
    with tempfile.TemporaryDirectory(prefix="freight-acceptance-") as tmp:
        db_path = str(Path(tmp) / "facts.db")
        system = FreightSystem(db_path, fresh=True, seed=True)
        failures = 0
        print("=" * 72)
        print("运费本体端到端验收" + ("（含真实 LLM Agent）" if "--llm" in args else ""))
        print("=" * 72)
        for title, scenario in scenarios:
            print(f"\n[RUN] {title}")
            try:
                scenario(system)
                print(f"[PASS] {title}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"[FAIL] {title}: {type(exc).__name__}: {exc}")
                if isinstance(exc, BusinessReject):
                    print(json.dumps(exc.details, ensure_ascii=False, default=str))

        print("\n" + "=" * 72)
        if failures:
            print(f"验收失败：{failures}/{len(scenarios)} 个场景未通过")
            return 1
        print(f"验收通过：{len(scenarios)}/{len(scenarios)} 个场景全部通过")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())