"""反虚胖门禁的负向承重测试：故意造虚胖，断言每一条都能拦住。

跑法：
    python3 tests/smoke_substance_gate.py 2>&1 | tee logs/substance_gate_$(date +%Y%m%d).log
"""
from __future__ import annotations

import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from freight.semantic import validate_ontology_substance  # noqa: E402

LAYER = os.path.join(ROOT, "freight", "semantic_layer.json")


def codes(raw: dict) -> list[str]:
    return [i["code"] for i in validate_ontology_substance(raw)]


def main() -> int:
    with open(LAYER, encoding="utf-8") as f:
        base = json.load(f)

    print(f"基线：类 {len(base['classes'])} / 关系 {len(base['relations'])} / "
          f"属性 {len(base.get('property_types') or [])}")
    clean = codes(base)
    print(f"  [{'PASS' if not clean else 'FAIL'}] 干净本体 -> 0 问题，实际 {len(clean)}")
    ok = not clean

    cases: list[tuple[str, dict, str]] = []

    # 1) 脚本批量生成类（复刻 expand_ontology.py 的手法）
    r = copy.deepcopy(base)
    r["classes"].append({
        "id": "FakeGenerated", "label": "假生成类", "key": "fake_generated_id",
        "description": "假生成类，只是为了凑数的。",
        "aliases": ["假"], "statuses": ["ACTIVE", "INACTIVE"],
        "properties": ["fake_generated_id", "name", "status"],
        "owner": "freight-core", "source": "generated:tools/expand_ontology.py",
        "version": "2.1.0", "iri": "https://x/#FakeGenerated",
        "effective_from": "2026-11-19",
    })
    cases.append(("模板生成类", r, "CLASS_AUTO_GENERATED"))

    # 2) 描述套模板句
    r = copy.deepcopy(base)
    for c in r["classes"]:
        if c["id"] == "OrderLine":
            c["description"] = "订单行，订单域的，按 order_line_id 检索。"
    cases.append(("描述模板句", r, "CLASS_DESCRIPTION_TEMPLATED"))

    # 3) 属性未注册（裸属性名）
    r = copy.deepcopy(base)
    r["property_types"] = [p for p in r["property_types"]
                           if p["name"] != "estimated_fee"]
    cases.append(("属性未注册", r, "PROP_UNREGISTERED"))

    # 4) 状态机千篇一律（复刻「一律 ACTIVE/INACTIVE」）
    r = copy.deepcopy(base)
    for c in r["classes"][: int(len(r["classes"]) * 0.3)]:
        c["statuses"] = ["ACTIVE", "INACTIVE"]
    cases.append(("状态机雷同", r, "CLASS_STATUS_UNIFORM"))

    # 5) 属性注册表整体缺失
    r = copy.deepcopy(base)
    r.pop("property_types", None)
    cases.append(("注册表缺失", r, "PROPERTY_REGISTRY_MISSING"))

    # 6) 关系模板化
    r = copy.deepcopy(base)
    n = int(len(r["relations"]) * 0.5)
    for i, rel in enumerate(r["relations"][:n]):
        rel["id"] = f"fake{i}_belongs_to"
    cases.append(("关系模板化", r, "RELATION_TEMPLATED"))

    print("\n负向用例：")
    for name, raw, expect in cases:
        got = codes(raw)
        hit = expect in got
        ok = ok and hit
        mark = "PASS" if hit else "FAIL"
        print(f"  [{mark}] {name:12s} 期望拦 {expect:28s} 实际 {got}")
        # 漏拦要显式报出来，不能静默
        if not hit:
            print(f"         ^^^ 门禁没拦住：{name} 未被识别")

    print(f"\n结果：{'全部拦住' if ok else '存在漏拦，门禁不可信'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
