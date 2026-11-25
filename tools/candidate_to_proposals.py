#!/usr/bin/env python3
"""候选模型 JSON → 提议通道提案包（省掉人工转录）。

输入：`tools/validate_model.py` 的候选 JSON 格式（classes/relations/enums）
输出：可直接 POST `/agent/ontology-proposal` 的提案包 + 转换报告。

为什么需要它：AI 建模的产出是「候选模型 JSON」，而提议通道吃的是「class_add /
relation_add 提案 payload」。两者字段形态不同（候选的 properties 是列表+类型表、
状态机只有 states 没有迁移动作），人工转录既费时又易错（R6 类型标注在转录中丢）。

★ 判定口径：用**提议通道自己的** `validate_proposal` 逐个校验（不另立一套），
所以"可提交/待补"的结论与门禁一致。
  - 可提交：validate_proposal 通过 → 提案进 `ready` 包
  - 待补：校验不过 → 进 `needs_fix` 包，附**逐条错误码**（缺什么、去哪儿补）

用法：
    python3 tools/candidate_to_proposals.py candidate.json --out proposals.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from freight.proposals import validate_proposal  # noqa: E402
from freight.semantic import SemanticLayer  # noqa: E402

LEGAL_TYPE_CODES = None  # 由 validate_proposal 负责，这里不重复定义类型表


def to_class_proposal(cls: dict) -> dict:
    """候选 class → class_add 提案 payload。"""
    props = cls.get("properties") or []
    types = cls.get("property_types") or {}
    if isinstance(props, dict):          # 容错：已经是 dict 形态
        props_map = {k: v for k, v in props.items()}
    else:
        props_map = {p: types.get(p) for p in props if isinstance(p, str)}
    payload = {
        "id": cls.get("id"),
        "label": cls.get("label"),
        "key": cls.get("key"),
        "description": cls.get("description") or "",
        "properties": props_map,
        "group": cls.get("group"),
        "why": cls.get("why"),
    }
    if cls.get("aliases"):
        payload["aliases"] = list(cls["aliases"])
    sm = cls.get("state_machine") or {}
    states = sm.get("states") or []
    if states:
        payload["statuses"] = list(states)
        initial = sm.get("initial")
        terminal = sm.get("terminal")
        payload["state_machine"] = {
            "initial": [initial] if isinstance(initial, str) else list(initial or []),
            "terminal": ([terminal] if isinstance(terminal, str)
                         else list(terminal or [])),
            "external_states": list(sm.get("external_states") or []),
        }
        # 候选格式没有迁移动作 → transitions 留空（validate 会报缺迁移，
        # 报告里明确列为"待补"，不替人编造迁移边）
        payload["transitions"] = list(cls.get("transitions") or [])
    return {"mutation_type": "class_add", "payload": payload}


def to_relation_proposal(rel: dict) -> dict:
    """候选 relation → relation_add 提案 payload。"""
    payload = {
        "id": rel.get("id"),
        "label": rel.get("label"),
        "domain": rel.get("domain"),
        "range": rel.get("range"),
        "cardinality": rel.get("cardinality"),
        "why": rel.get("why"),
    }
    if "temporal" in rel:
        payload["temporal"] = bool(rel["temporal"])
    return {"mutation_type": "relation_add", "payload": payload}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("candidate")
    ap.add_argument("--out", default=None)
    ap.add_argument("--semantic", default=str(ROOT / "freight" / "semantic_layer.json"))
    args = ap.parse_args()

    cand = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    sem = SemanticLayer(args.semantic)

    proposals: list[dict] = []
    skipped: list[dict] = []
    for cls in cand.get("classes") or []:
        if isinstance(cls, dict):
            proposals.append(to_class_proposal(cls))
    for rel in cand.get("relations") or []:
        if isinstance(rel, dict):
            proposals.append(to_relation_proposal(rel))
    enums = cand.get("enums") or []
    for en in enums:
        skipped.append({
            "kind": "enum", "id": en.get("id") if isinstance(en, dict) else None,
            "reason": "我方语义层没有独立枚举实体（分类组 object_type_groups 与类内 "
                      "statuses 承担枚举职责）；枚举值请落到 group_add_type 或类状态集，"
                      "不走 enum 通道。",
        })

    ready: list[dict] = []
    needs_fix: list[dict] = []
    for p in proposals:
        check = validate_proposal(p, sem)
        if check["valid"]:
            ready.append(p)
        else:
            needs_fix.append({
                "proposal": p,
                "errors": [{"code": e["code"], "message": e["message"]}
                           for e in check["errors"]],
            })

    package = {
        "source_candidate": str(Path(args.candidate).name),
        "ready": ready,
        "needs_fix": needs_fix,
        "skipped": skipped,
        "counts": {"proposals": len(proposals), "ready": len(ready),
                   "needs_fix": len(needs_fix), "skipped": len(skipped)},
        "usage": "ready 里的每个 proposal 可直接 POST /agent/ontology-proposal "
                 "（body = {\"decision\": {\"propose\": <proposal>}}）；"
                 "needs_fix 需按 errors 补齐后再提交。",
    }
    out_path = Path(args.out) if args.out else (
        ROOT / "tools" / "_out" / "candidate_proposals.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(package, ensure_ascii=False, indent=1),
                        encoding="utf-8")

    print(f"候选 → 提案：{len(proposals)} 条"
          f"（可提交 {len(ready)} / 待补 {len(needs_fix)} / 跳过 {len(skipped)}）")
    for item in needs_fix[:10]:
        codes = sorted({e["code"] for e in item["errors"]})
        print(f"  - {item['proposal']['payload'].get('id')}: {codes}")
    if len(needs_fix) > 10:
        print(f"  … 还有 {len(needs_fix) - 10} 条")
    print(f"提案包：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
