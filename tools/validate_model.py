#!/usr/bin/env python3
"""可溯源本体建模校验器（原型）。

输入：AI 按 R1–R10 生成的候选模型 JSON
输出：
  1. 校验报告（逐条规则 R5–R10 的通过/失败）
  2. 若全部通过，生成 mobility DSL 的 C()/L() 声明草案

设计要点（与文章对齐）：
  - 校验器**不判断建模对不对**（那是人/业务方的事），只判断：
    a) 每个建模选择是否带 why（R10）
    b) why 指向的规则编号是否合法
    c) 硬约束是否满足（类型/主键/互斥/基数/状态机）
  - 这就是"可回溯"的落地点：没有理由的选择一律不许进候选模型。

用法：
  python3 validate_model.py example_candidate.json
  python3 validate_model.py example_candidate.json --emit-dsl   # 额外输出 C()/L() 草案
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter

# mobility 的 11 种类型短码（来自 ontology/__init__.py TYPE_MAP）
VALID_TYPES = {"id", "str", "txt", "dec", "int", "num", "bool", "dt", "date", "json", "enum"}
VALID_CARDINALITIES = {"one_to_one", "one_to_many", "many_to_one", "many_to_many"}
# 合法规则编号：R1–R9 是实体规则，R10 是元规则（why 指向 R1–R9 之一）
VALID_RULES = {f"R{i}" for i in range(1, 11)}
# why 格式：R编号: 理由
WHY_RE = re.compile(r"^(R\d+):\s*(.+)$")


class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.passes: list[str] = []

    def fail(self, rule: str, msg: str):
        self.errors.append(f"[{rule}] {msg}")

    def ok(self, rule: str, msg: str):
        self.passes.append(f"[{rule}] {msg}")

    def summary(self) -> str:
        lines = []
        for p in self.passes:
            lines.append("  PASS " + p)
        for e in self.errors:
            lines.append("  FAIL " + e)
        lines.append(f"\n结论：{len(self.passes)} 通过 / {len(self.errors)} 失败"
                     + (" —— 模型不可渲染" if self.errors else " —— 可进入人工确认"))
        return "\n".join(lines)


def check_why(item: dict, label: str, rep: Report) -> bool:
    """R10：每个建模选择必须带 why，且指向合法规则编号。"""
    why = item.get("why")
    if not why:
        rep.fail("R10", f"{label} 缺少 why 字段（每个建模选择必须可回溯）")
        return False
    m = WHY_RE.match(why)
    if not m:
        rep.fail("R10", f"{label} why 格式非法：{why!r}（应为 'R编号: 理由'）")
        return False
    if m.group(1) not in VALID_RULES:
        rep.fail("R10", f"{label} why 指向未知规则 {m.group(1)}")
        return False
    if len(m.group(2).strip()) < 4:
        rep.fail("R10", f"{label} why 理由过短，不足以复查")
        return False
    return True


def validate(model: dict) -> Report:
    rep = Report()
    classes = model.get("classes", [])
    relations = model.get("relations", [])
    enums = model.get("enums", [])

    if not (classes or relations or enums):
        rep.fail("R10", "候选模型为空")
        return rep

    # R7：主键唯一 + key 在 props 中
    keys = []
    for c in classes:
        cid = c.get("id", "?")
        check_why(c, f"类 {cid}", rep)
        key = c.get("key")
        props = c.get("properties", [])
        if not key:
            rep.fail("R7", f"类 {cid} 缺少主键 key")
        elif key not in props:
            rep.fail("R7", f"类 {cid} 主键 {key} 不在 properties 里")
        if key:
            keys.append(key)
    dup = [k for k, n in Counter(keys).items() if n > 1]
    if dup:
        rep.fail("R7", f"主键重复：{dup}（身份不唯一）")
    else:
        rep.ok("R7", f"{len(classes)} 个类主键唯一且均在属性表中")

    # R6：属性类型必须显式、合法（AI 也不许猜）
    bad_type = []
    for c in classes:
        cid = c.get("id", "?")
        for p in c.get("properties", []):
            t = c.get("property_types", {}).get(p)
            if not t:
                bad_type.append(f"{cid}.{p} 无类型")
            elif t not in VALID_TYPES:
                bad_type.append(f"{cid}.{p}={t} 非法类型")
    if bad_type:
        rep.fail("R6", "属性类型违规：" + "；".join(bad_type[:6]))
    else:
        rep.ok("R6", f"所有属性显式标注且类型合法（共 {sum(len(c.get('properties', [])) for c in classes)} 个属性）")

    # R5：枚举互斥且完备（含 OTHER/UNKNOWN 兜底）
    for e in enums:
        eid = e.get("id", "?")
        check_why(e, f"枚举 {eid}", rep)
        vals = e.get("values", [])
        if len(vals) != len(set(vals)):
            rep.fail("R5", f"枚举 {eid} 有重复取值")
        overlap = e.get("overlapping_with", [])
        if overlap:
            rep.fail("R5", f"枚举 {eid} 声明了可同时成立的取值 {overlap}（违反互斥）")
        if not ({"OTHER", "UNKNOWN", "N_A"} & set(vals)):
            rep.fail("R5", f"枚举 {eid} 没有 OTHER/UNKNOWN 兜底（覆盖不完备）")
    if enums:
        rep.ok("R5", f"{len(enums)} 个枚举检查完毕（互斥+兜底）")

    # R8：关系基数合法 + 显式
    for r in relations:
        rid = r.get("id", "?")
        check_why(r, f"关系 {rid}", rep)
        card = r.get("cardinality")
        if card not in VALID_CARDINALITIES:
            rep.fail("R8", f"关系 {rid} 基数非法或缺失：{card!r}")
        if not r.get("domain") or not r.get("range"):
            rep.fail("R8", f"关系 {rid} 缺 domain/range")
    if relations:
        rep.ok("R8", f"{len(relations)} 条关系基数与端点合法")

    # R9：有 status 的类必须声明状态机
    for c in classes:
        cid = c.get("id", "?")
        props = c.get("properties", [])
        if "status" in props:
            sm = c.get("state_machine")
            if not sm or not sm.get("initial") or not sm.get("terminal"):
                rep.fail("R9", f"类 {cid} 有 status 但状态机不完整（缺 initial/terminal）")
    rep.ok("R9", "状态机检查完毕")

    # R4：证据引用不并入（若声明了 evidence_refs，不得并入 properties）
    for c in classes:
        cid = c.get("id", "?")
        ev = c.get("evidence_refs", [])
        overlap = set(ev) & set(c.get("properties", []))
        if overlap:
            rep.fail("R4", f"类 {cid} 证据引用字段同时出现在 properties：{sorted(overlap)}（应只引用不并入）")
    rep.ok("R4", "证据引用检查完毕")

    if not rep.errors:
        rep.ok("R10", "所有建模选择均带可回溯的 why")
    return rep


def emit_dsl(model: dict) -> str:
    """把通过的候选模型渲染成 mobility DSL 的 C()/L() 声明草案。"""
    lines = ["# 自动生成的建模草案（需人工确认后合入 ontology/domains_*.py）",
             "from . import C, L", "", "CLASSES = ["]
    for c in model.get("classes", []):
        props = ", ".join(f"{p}:{c['property_types'][p]}" for p in c.get("properties", []))
        statuses = ",".join(c.get("state_machine", {}).get("states", []))
        lines.append(f"    C(")
        lines.append(f"        {c['id']!r}, {c.get('label', c['id'])!r}, {c.get('key', '')!r},")
        lines.append(f"        {c.get('description', '')!r},")
        lines.append(f"        {props!r},")
        lines.append(f"        {statuses!r},")
        lines.append(f"        aliases={tuple(c.get('aliases', []))}, group={c.get('group')!r},")
        lines.append(f"    ),  # why: {c.get('why', '')}")
    lines.append("]")
    lines.append("")
    lines.append("LINKS = [")
    for r in model.get("relations", []):
        lines.append(
            f"    L({r['id']!r}, {r.get('label', r['id'])!r}, {r['domain']!r}, {r['range']!r}, "
            f"cardinality={r['cardinality']!r}),  # why: {r.get('why', '')}"
        )
    lines.append("]")
    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    path = sys.argv[1]
    emit = "--emit-dsl" in sys.argv
    with open(path, encoding="utf-8") as f:
        model = json.load(f)
    rep = validate(model)
    print("=== 可溯源建模校验报告 ===")
    print(rep.summary())
    if not rep.errors and emit:
        print("\n=== 生成的 C()/L() 声明草案 ===")
        print(emit_dsl(model))
    sys.exit(1 if rep.errors else 0)


if __name__ == "__main__":
    main()
