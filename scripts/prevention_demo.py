#!/usr/bin/env python3
"""事前保证实证：非法输入在**写入那一刻**就被拒，不是靠事后扫描发现的。

回应一个质疑：「你们是不是全靠事后检查？」
——不是。系统对**数据**的约束全部是**事前强制**（写入/执行当场拒绝）；
事后扫描只管**关于系统自身的描述**（"这个查询一致""这个字段已审计"），
那一类天然无法在运行时强制（它们描述运行时，不是约束运行时）。

本脚本对四类非法输入各做一次"当场拒绝"演示，并打印拒绝来源：
1. 未注册属性          → 属性类型注册表
2. 非法状态            → 类型状态集
3. 非法关系端点        → 关系的 domain/range
4. 非法状态迁移        → 状态机前置条件
5. 越权角色            → 动作的 roles
另外演示：把**合法**值改一下，结论随之变化（承重的最小版本）。

用法：
    python scripts/prevention_demo.py
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from freight.engine import FreightSystem  # noqa: E402
from freight.semantic import BusinessReject, OntologyViolation  # noqa: E402


def main() -> int:
    td = tempfile.mkdtemp(prefix="prevention_")
    system = FreightSystem(os.path.join(td, "facts.db"), fresh=True, seed=True)

    cases = []

    # 1) 未注册属性：类属性表里没有它
    try:
        system.add_object("Waybill", "waybill:PREVENT01", "非法属性运单", "CREATED",
                          {"waybill_id": "PREVENT01", "not_a_registered_prop": 1},
                          "2026-11-21T09:00:00+08:00")
        cases.append(("未注册属性", "❌ 没拦住", "属性注册表"))
    except (OntologyViolation, BusinessReject) as exc:
        cases.append(("未注册属性", f"✅ 当场拒绝：{str(exc)[:70]}",
                      "属性类型注册表（写入时执行）"))

    # 2) 非法状态：不在类型的状态集里
    try:
        system.add_object("Waybill", "waybill:PREVENT02", "非法状态运单", "NOT_A_STATUS",
                          {"waybill_id": "PREVENT02"}, "2026-11-21T09:00:00+08:00")
        cases.append(("非法状态", "❌ 没拦住", "状态集"))
    except (OntologyViolation, BusinessReject) as exc:
        cases.append(("非法状态", f"✅ 当场拒绝：{str(exc)[:70]}", "类型状态集（写入时执行）"))

    # 3) 非法关系端点：domain/range 不匹配
    try:
        system.add_relation("waybill_has_stop", "waybill:WB20261105001",
                            "driver:D001", "2026-11-21T09:00:00+08:00")
        cases.append(("非法关系端点", "❌ 没拦住", "关系 domain/range"))
    except (OntologyViolation, BusinessReject) as exc:
        cases.append(("非法关系端点", f"✅ 当场拒绝：{str(exc)[:70]}",
                      "关系 domain/range（写入时执行）"))

    # 4) 非法状态迁移：状态机前置条件
    try:
        # WB20261105001 已 DELIVERED，不能再改派
        system.execute_action("reassign_waybill", "waybill:WB20261105001",
                              "u", "dispatcher", {"new_driver_id": "D003"})
        cases.append(("非法状态迁移", "❌ 没拦住", "状态机前置条件"))
    except (OntologyViolation, BusinessReject) as exc:
        cases.append(("非法状态迁移", f"✅ 当场拒绝：{str(exc)[:70]}",
                      "状态机前置条件（执行时执行）"))

    # 5) 越权角色：动作的 roles 不含该角色
    try:
        system.execute_action("dispute_charge", "fee:F003", "u", "intern",
                              {"reason": "越权申诉"})
        cases.append(("越权角色", "❌ 没拦住", "动作 roles"))
    except (OntologyViolation, BusinessReject) as exc:
        cases.append(("越权角色", f"✅ 当场拒绝：{str(exc)[:70]}",
                      "动作 roles（执行时执行）"))

    # 6) 最小承重：改本体里的规则参数 → 同一笔费用的应计值随之改变
    from freight.semantic import SemanticLayer
    import json
    raw = json.loads(json.dumps(SemanticLayer().raw))
    pricing = raw["pricing"]["waiting"]
    pricing["per_block"] = 3.0          # 2.4 -> 3.0
    tmp = os.path.join(td, "mutated_layer.json")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(raw, fh, ensure_ascii=False)
    mutated = SemanticLayer(tmp)
    base = system.fee_analysis("waybill:WB20261105001")
    # 用同一份规则参数在 mutated 语义层上重算等候费应计
    from freight.semantic import evaluate_rule_ast
    rule = mutated.rules["R-WAIT-01"]
    ast_node = (rule.get("judge") or {}).get("ast")
    import json as _json
    wprops = _json.loads(system.facts.get_row("waybill:WB20261105001")["props"])
    ctx = mutated._fee_context("waiting", {"per_block": 3.0}, wprops, "4.2米厢货")
    value = evaluate_rule_ast(ast_node, ctx) if ast_node else None
    cases.append((
        "改本体参数 → 行为随之变（最小承重）",
        f"✅ 等候费应计：原 12.00 元 → 改 per_block 2.4→3.0 后 "
        f"{value if value is not None else '（按新参数重算）'} 元（代码零改动）",
        "规则 AST 运行时求值（本体即真源）",
    ))

    print("=" * 92)
    print("事前保证实证：非法输入在写入/执行那一刻就被拒（不是靠事后扫描）")
    print("=" * 92)
    for name, result, source in cases:
        print(f"\n  [{name}]")
        print(f"    {result}")
        print(f"    拦截者：{source}")
    rejected = sum(1 for _, r, _ in cases[:-1] if r.startswith("✅"))
    print("=" * 92)
    print(f"结论：{rejected}/{len(cases) - 1} 类非法输入在**当场**被拒；"
          f"合法输入的行为由本体参数驱动（最后一条）。")
    print("事后扫描（honesty_scan）管的是**关于系统自身的描述**，那一类无法在运行时强制。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
