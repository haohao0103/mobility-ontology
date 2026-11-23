"""演示负向实验室：只改内存本体副本 / SAVEPOINT 内执行，绝不写主库。

每个 lab 返回统一结构：
  {case, before, after, flips:[{code,action}], declaration_paths:[...], persisted:false}

安全纪律（对应计划 C3 / §8）：
- 本体改动走 SemanticLayer.from_raw，不落临时 JSON、不改主本体对象；
- 动作执行走 SAVEPOINT + ROLLBACK，审计/状态/事件零增量；
- lab 结束后 self.sem 恢复原对象，主库 checksum 不变。
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from typing import Any, Callable

from .engine import FreightSystem
from .semantic import SemanticLayer

WB = "waybill:WB20261105001"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _flips(before: dict, after: dict) -> list[dict]:
    """找出 before→after 之间出现的 code / 消失的 code / 计数变化。"""
    out: list[dict] = []

    def codes(obj: Any) -> dict[str, int]:
        acc: dict[str, int] = {}
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "code" and isinstance(v, str):
                    acc[v] = acc.get(v, 0) + 1
                else:
                    for kk, vv in codes(v).items():
                        acc[kk] = acc.get(kk, 0) + vv
        elif isinstance(obj, list):
            for item in obj:
                for kk, vv in codes(item).items():
                    acc[kk] = acc.get(kk, 0) + vv
        return acc

    b, a = codes(before), codes(after)
    for code in sorted(set(b) | set(a)):
        if code not in b and code in a:
            out.append({"code": code, "action": "appeared"})
        elif code in b and code not in a:
            out.append({"code": code, "action": "disappeared"})
        elif b[code] != a[code]:
            out.append({"code": code, "action": "count_changed",
                        "before": b[code], "after": a[code]})
    return out


def result(case: str, before: dict, after: dict, paths: list[str]) -> dict:
    return {"case": case, "before": before, "after": after,
            "flips": _flips(before, after), "declaration_paths": paths,
            "persisted": False}


def _with_sem(system: FreightSystem, sem: SemanticLayer, fn: Callable[[], dict]) -> dict:
    """临时注入本体到 system 与 facts，运行完恢复——即使异常也恢复。

    facts.sem 是 FactMCP 构造时绑定的独立引用，契约/取数走的是它，
    只换 system.sem 会让 lab 看起来"没翻转"。
    """
    original = system.sem
    original_facts = system.facts.sem
    try:
        system.sem = sem
        system.facts.sem = sem
        return fn()
    finally:
        system.sem = original
        system.facts.sem = original_facts


def _clone_sem(system: FreightSystem) -> SemanticLayer:
    return SemanticLayer.from_raw(system.base_sem.raw)


def contract_coverage(system: FreightSystem, waybill_id: str = WB) -> dict:
    """B-01: 费用归属矩阵 / 解析状态 / 新鲜度 / 证据覆盖 / 缺口。"""
    row = system.facts.get_row(waybill_id)
    if not row:
        return {"error": "OBJECT_NOT_FOUND", "waybill_id": waybill_id}
    props = json.loads(row["props"]) if isinstance(row["props"], str) else row["props"]
    return system._fee_source_coverage(waybill_id, props)


def contract_simulate(system: FreightSystem, case: str = "drop_claim",
                      waybill_id: str = WB) -> dict:
    """B-02: 在内存本体副本上删认领/证据要求/证据，返回 before/after findings。"""
    before = contract_coverage(system, waybill_id)
    sem = _clone_sem(system)
    paths: list[str] = []

    if case == "drop_claim":
        # 删 TMS 对 waiting 的认领（data_sources 里 class=FeeItem 且 system=TMS）
        for src in sem.data_sources:
            if src.get("class") == "FeeItem" and src.get("system") == "TMS":
                if "waiting" in (src.get("fee_types") or []):
                    src["fee_types"].remove("waiting")
                    paths.append("data_sources[TMS].fee_types[waiting]")
    elif case == "drop_evidence":
        for src in sem.data_sources:
            if src.get("system") == "TMS":
                src["evidence_required"] = False
                paths.append("data_sources[TMS].evidence_required")
    elif case == "drop_source":
        sem.data_sources = [s for s in sem.data_sources if s.get("system") != "TMS"]
        paths.append("data_sources[TMS]")
    else:
        return {"error": "UNKNOWN_CASE", "case": case}

    after = _with_sem(system, sem, lambda: contract_coverage(system, waybill_id))
    return result(f"contract_{case}", before, after, paths)


def abox_coverage(system: FreightSystem) -> dict:
    """B-03: 深水区 ABox 覆盖率与问题清单。"""
    from .abox import check, measure

    m = measure(system.sem, system.db_path)
    issues = check(system.sem, system.db_path)
    return {"core_types": m.get("core_types", []),
            "class_coverage": m.get("class_coverage"),
            "edge_coverage": m.get("edge_coverage"),
            "action_coverage": m.get("action_coverage"),
            "transitions": m.get("transition_consistency"),
            "rules": m.get("rules"), "issues": issues,
            "issue_count": len(issues)}


def action_lab(system: FreightSystem, waybill_id: str = WB) -> dict:
    """B-04: SAVEPOINT 内跑错角色/错状态/允许动作/同键重放，最后 ROLLBACK。"""
    conn = system.conn
    before_audit = conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    before_status = system.facts.get_row(waybill_id)["status"]
    key = f"lab-replay-{os.getpid()}"
    out: dict[str, Any] = {"waybill_id": waybill_id, "before_status": before_status,
                           "cases": []}

    def attempt(label: str, action: str, actor: str, role: str,
                payload: dict | None = None, idem: str | None = None) -> None:
        try:
            r = system.execute_action(action, waybill_id, actor, role, payload or {}, idem,
                                      defer_commit=True)
            out["cases"].append({"case": label, "ok": True, "result": r})
        except Exception as exc:  # noqa: BLE001
            out["cases"].append({"case": label, "ok": False,
                                 "code": getattr(exc, "code", "ERROR"),
                                 "error": str(exc)[:160]})

    conn.execute("SAVEPOINT action_lab")
    try:
        attempt("wrong_role", "accept_order", "lab", "stranger")
        attempt("wrong_state", "reassign_waybill", "lab", "dispatcher",
                {"new_driver_id": "drv:LAB"})

        def attempt_on(label: str, oid: str, action: str, role: str,
                       payload: dict | None = None, idem: str | None = None) -> None:
            try:
                r = system.execute_action(action, oid, "lab", role, payload or {}, idem,
                                          defer_commit=True)
                out["cases"].append({"case": label, "object_id": oid, "ok": True, "result": r})
            except Exception as exc:  # noqa: BLE001
                out["cases"].append({"case": label, "object_id": oid, "ok": False,
                                     "code": getattr(exc, "code", "ERROR"),
                                     "error": str(exc)[:160]})

        # 动态选一个"有可执行动作"的运单：测试库 SCN02 可用 accept_order，
        # 生产主库没有 SCN02，但 ACCEPTED/LOADED 运单可用这些动作。
        # 优先选取，避免 reassign_waybill 需要真实司机对象。
        lab_target = None
        preferred_actions = ("accept_order", "complete", "confirm_loading")

        def action_decl(act: str) -> dict:
            for decl in (system.sem.raw.get("actions") or []):
                if decl.get("id") == act:
                    return decl
            return {}

        def first_role(act: str) -> str:
            return (action_decl(act).get("roles") or ["dispatcher"])[0]

        def lab_payload(act: str) -> dict:
            """按动作声明补必填字符串参数的占位值（如送达的回单编号）。"""
            return {p["name"]: f"LAB-{p['name']}"
                    for p in (action_decl(act).get("params") or [])
                    if p.get("required") and p.get("type") == "string"}

        rows = system.facts.all(
            "SELECT object_id FROM objects WHERE type='Waybill' AND object_id<>?",
            (waybill_id,))
        candidates = []
        for row in rows:
            oid = row["object_id"]
            try:
                obj = system.get_object(oid)
            except Exception:  # noqa: BLE001
                continue
            names = [a if isinstance(a, str) else (a.get("id") or a.get("name"))
                     for a in (obj.get("available_actions") or [])]
            candidates.append((oid, [n for n in names if n]))
        for oid, names in candidates:
            for act in preferred_actions:
                if act in names:
                    lab_target = (oid, act, lab_payload(act), first_role(act))
                    break
            if lab_target:
                break
        if lab_target is None:
            drivers = system.facts.all(
                "SELECT object_id FROM objects WHERE type='Driver' LIMIT 1")
            if drivers:
                for oid, names in candidates:
                    if names:
                        lab_target = (oid, names[0],
                                      {"new_driver_id": drivers[0]["object_id"]},
                                      first_role(names[0]))
                        break
        if lab_target:
            oid, act, payload, role = lab_target
            attempt_on("allowed", oid, act, role, payload, key)
            attempt_on("replay_1", oid, act, role, payload, key)
            attempt_on("replay_2", oid, act, role, payload, key)
        else:
            for label in ("allowed", "replay_1", "replay_2"):
                out["cases"].append({"case": label, "ok": False, "code": "NO_LAB_TARGET",
                                     "error": "当前库无可用动作的运单对象"})
    finally:
        conn.execute("ROLLBACK TO action_lab")
        conn.execute("RELEASE action_lab")
        conn.rollback()

    out["after_audit"] = conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    out["audit_delta"] = out["after_audit"] - before_audit
    out["after_status"] = system.facts.get_row(waybill_id)["status"]
    out["state_restored"] = out["before_status"] == out["after_status"]
    out["flips"] = _flips({"codes": []}, {"codes": [c.get("code") for c in out["cases"]
                                                   if not c.get("ok")]})
    out["persisted"] = False
    return out


def guard_demo(system: FreightSystem, case: str = "unknown_fee_type") -> dict:
    """B-05: 扩展破坏性/规则门禁 demo 的 case。"""
    if case == "unknown_fee_type":
        return system.semantic_guard_demo()

    from .change_gate import review

    sem = _clone_sem(system)
    before = system.base_sem.raw
    paths: list[str] = []
    if case in ("drop_property_unsigned", "drop_property_signed"):
        cls = next((c for c in sem.classes.values() if c["id"] == "Waybill"), None)
        if not cls:
            return {"error": "CLASS_NOT_FOUND"}
        props = cls.get("properties") or []
        target = "waiting_minutes"
        if target in props:
            props.remove(target)
        else:
            cls["properties"] = [p for p in props if p != target]
        paths.append("classes[Waybill].properties[waiting_minutes]")
    else:
        return {"error": "UNKNOWN_CASE", "case": case}

    root = None
    if case == "drop_property_signed":
        # 临时签名目录：只写 /tmp，不碰仓库的 breaking_approvals.json，
        # 也不改主本体。演示"补签即放行"时用与 classify 完全一致的 path。
        tmp = tempfile.mkdtemp(prefix="lab-approval-")
        os.makedirs(os.path.join(tmp, "freight"), exist_ok=True)
        with open(os.path.join(tmp, "freight", "breaking_approvals.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"approvals": [{
                "path": paths[0], "code": "PROPERTY_REMOVED",
                "approved_by": "demo-lab", "at": "2026-11-21",
                "reason": "演示用临时签字：只存在于 /tmp，不写仓库",
            }]}, fh, ensure_ascii=False)
        root = tmp
    review_out = review(before, sem.raw, ext=None, root=root)
    return {"case": case, "verdict": review_out["verdict"],
            "breaking_count": review_out.get("breaking_count"),
            "unapproved_count": review_out.get("unapproved_count"),
            "changes": review_out.get("changes", []),
            "impacts": review_out.get("impacts", []),
            "declaration_paths": paths, "persisted": False,
            "flips": _flips({"verdict": "PASS"}, review_out)}


def type_lab(system: FreightSystem, case: str = "waiting_minutes_boolean") -> dict:
    """B-06: 改本体副本的属性/函数签名，跑真实写入/调用路径返回拒绝码。"""
    from .semantic import BusinessReject, OntologyViolation

    sem = _clone_sem(system)
    paths: list[str] = []
    if case == "waiting_minutes_boolean":
        for p in sem.raw.get("property_types", []):
            if p.get("name") == "waiting_minutes":
                p["value_type"] = "boolean"
                paths.append("property_types[waiting_minutes].value_type")
    elif case == "function_return_drop":
        for f in sem.raw.get("functions", []):
            if f["id"] == "fee_breakdown":
                f.get("returns", {}).pop("fields", None)
                paths.append("functions[fee_breakdown].returns.fields")
    else:
        return {"error": "UNKNOWN_CASE", "case": case}

    def exercise() -> dict:
        out: dict[str, Any] = {"create": None, "call": None}
        # 创建路径真实校验：engine.add_object 调 sem.validate_object
        try:
            sem.validate_object("Waybill",
                                {"waiting_minutes": 53, "status": "CREATED"})
            out["create"] = {"ok": True}
        except (OntologyViolation, BusinessReject) as exc:
            out["create"] = {"ok": False, "code": getattr(exc, "code", "TYPE_MISMATCH"),
                             "error": str(exc)[:120]}
        except Exception as exc:  # noqa: BLE001
            out["create"] = {"ok": False, "code": type(exc).__name__, "error": str(exc)[:100]}
        # 函数调用路径：fee_analysis 注入被改的 sem，声明变化影响计算
        try:
            r = system.fee_analysis(WB, sem=sem)
            out["call"] = {"ok": True, "variance": r.get("variance")}
        except Exception as exc:  # noqa: BLE001
            out["call"] = {"ok": False, "code": getattr(exc, "code", type(exc).__name__),
                           "error": str(exc)[:100]}
        return out

    after = _with_sem(system, sem, exercise)
    before = {"create": {"ok": True}, "call": {"ok": True}}
    return result(f"type_{case}", before, after, paths)


def declaration_check(system: FreightSystem, omit_tool: str | None = None,
                      omit_type: str | None = None) -> dict:
    """B-07: 确定性检查工具白名单 / 可圈定类型 / 证据 checksum，不调 LLM。"""
    sem = _clone_sem(system)
    paths: list[str] = []
    if omit_tool:
        # 从 primitives 里删掉指定工具声明（mcp_tools 从 primitives 派生）
        before_n = len(sem.primitives)
        sem.raw["primitives"] = [p for p in sem.raw.get("primitives", [])
                                 if p.get("name") != omit_tool]
        sem._index(json.loads(json.dumps(sem.raw)))
        paths.append(f"primitives[{omit_tool}]")
    if omit_type:
        sem.raw["classes"] = [c for c in sem.raw["classes"] if c["id"] != omit_type]
        sem._index(json.loads(json.dumps(sem.raw)))
        paths.append(f"classes[{omit_type}]")

    def probe() -> dict:
        tools = system.mcp_tools("Waybill")
        names = [t.get("name") for t in (tools.get("tools") or [])]
        # 可圈定类型 = 本体 classes 索引；catalog() 默认受 budget 限制会 overflow
        type_ids = sorted(system.sem.classes)
        row = system.facts.get_row(WB)
        ev = system.facts.all("SELECT checksum FROM evidence WHERE object_id=? LIMIT 1", (WB,))
        checksum = ev[0]["checksum"][:12] if ev else None
        findings = []
        if "ontology.catalog" not in names:
            findings.append({"code": "TOOL_UNAVAILABLE", "detail": "ontology.catalog"})
        if "FeeItem" not in type_ids:
            findings.append({"code": "TYPE_UNBOUND", "detail": "FeeItem"})
        if not checksum:
            findings.append({"code": "EVIDENCE_CHECKSUM_MISSING"})
        return {"tools": names, "tool_count": len(names),
                "type_ids": type_ids, "type_count": len(type_ids),
                "evidence_checksum": checksum, "findings": findings,
                "can_plan": bool(type_ids) and bool(names)}

    after = _with_sem(system, sem, probe)
    before_probe = probe()  # 在恢复原始 sem 后调用
    return result("declaration_check", before_probe, after, paths)

def pricing_lab(system, case: str = "truck_rain",
                vehicle_type: str = "大货车", weather: str = "rain",
                waiting_minutes: float = 53, time_at: str = "") -> dict:
    """多维计价实验室：同一条规则，换维度组合看命中行翻转。

    真实计价是「车型 × 时段 × 天气」多维组合（decision table 首行命中）。
    本 lab 不写任何库，只读求值，展示：
      - 当前维度组合命中的 matrix 行与参数；
      - 翻转对照（晴/雨、跨车型）——改一个维度值，应计当场变；
      - 决策表行序校验由 semantic_gate 的 check_pricing_matrix 负责。

    case:
      truck_rain  大货车+雨天 vs 大货车+晴天（默认，演示天气加价）
      vehicle     4.2米厢货 vs 大货车（演示车型分档）
    """
    from .semantic import evaluate_rule_ast

    sem = system.sem
    config = sem.pricing.get("waiting") or {}
    ast_node = (sem.rules.get(str(config.get("rule_id")) or {}) or {}).get(
        "judge", {}).get("ast")

    def evaluate(vt: str, wx: str, minutes: float, at: str) -> dict:
        ctx = sem._fee_context(
            "waiting", {},
            {"waiting_minutes": minutes, "weather": wx,
             "accepted_at": at or "2026-11-06T08:00:00+08:00"}, vt)
        value = evaluate_rule_ast(ast_node, ctx) if ast_node else None
        return {
            "vehicle_type": vt, "weather": wx, "waiting_minutes": minutes,
            "time_window": ctx.get("time_window"),
            "matched_row": ctx.get("tariff_row"),
            "free_minutes": ctx["tariff"].get("free_minutes"),
            "block_minutes": ctx["tariff"].get("block_minutes"),
            "per_block": ctx["tariff"].get("per_block"),
            "expected": round(float(value), 2) if value is not None else None,
        }

    if case == "vehicle":
        before = evaluate("4.2米厢货", "normal", waiting_minutes, time_at)
        after = evaluate("大货车", "normal", waiting_minutes, time_at)
        dimension = "vehicle_type"
    else:  # truck_rain：天气维度翻转
        before = evaluate(vehicle_type, "normal", waiting_minutes, time_at)
        after = evaluate(vehicle_type, "rain", waiting_minutes, time_at)
        dimension = "weather"

    flips = []
    if before["matched_row"] != after["matched_row"]:
        flips.append({"code": "TARIFF_ROW_FLIP",
                      "action": f"{before['matched_row']} → {after['matched_row']}"})
    if before["expected"] != after["expected"]:
        flips.append({"code": "EXPECTED_AMOUNT_FLIP",
                      "action": f"{before['expected']} → {after['expected']} 元"})
    if before["time_window"] != after["time_window"]:
        flips.append({"code": "TIME_WINDOW_FLIP",
                      "action": f"{before['time_window']} → {after['time_window']}"})

    return {
        "case": case, "dimension": dimension,
        "dimensions": config.get("dimensions") or [],
        "before": before, "after": after,
        "flips": flips,
        "declaration_paths": [
            "pricing.waiting.matrix（决策表：条件组合 → 参数，首行命中）",
            "pricing.waiting.dimensions（计价维度：车型/时段/天气）",
            "pricing.waiting.time_windows（时段定义）",
            "rules.R-WAIT-01.judge.ast（公式不变，tariff.* 取命中行参数）",
        ],
        "persisted": False,
    }
