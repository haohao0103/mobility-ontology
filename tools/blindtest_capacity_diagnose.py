#!/usr/bin/env python3
"""盲测诊断：把「精确名未匹配」的人工类与 LLM 类做**语义对应**分析。

为什么需要：阶段 1 盲测主口径（类名精确匹配）给出召回 25.5%（FAIL），但抽样显示
大量"未匹配"其实是**同一概念、不同命名**（人工 VehicleCapacity 核定载重 ↔ 模型
CapacityApproval 载量核定记录）。主口径如实报 FAIL 不改；本脚本给出**诊断口径**：
「语义对应率」——用名称 bigram 重合 + 属性集 Jaccard 找每个漏类的最佳对应。

★ 口径声明：
  - 本脚本**不改判据**（判据仍是精确名匹配，结论不变）；输出**只作诊断**。
  - 相似度 = 0.5×名称 bigram Jaccard + 0.5×属性集 Jaccard（属性缺失时只用名称项）
  - 「语义对应」阈值 = 0.25（相似度 ≥ 阈值即认为对应上了某个概念）
  - 分母 = 人工版类总数（98）

用法：python3 tools/blindtest_capacity_diagnose.py --stamp 20261125_201223
"""
from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from freight.agent import _extract_json_object  # noqa: E402

RAW_DIR = ROOT / "tools" / "_out" / "blindtest_raw"
DOMAIN_MODULE = "ontology.domains_02_capacity"
SIM_THRESHOLD = 0.25


def bigrams(text: str) -> set[str]:
    t = re.sub(r"[\s\-_/（）()【】\[\]]", "", str(text or ""))
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b)


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip().lower()


def human_classes() -> list[dict]:
    mod = importlib.import_module(DOMAIN_MODULE)
    out = []
    for c in mod.CLASSES:
        props = {name: code for name, code in (c.get("_props_spec") or [])}
        out.append({"id": c["id"], "label": c.get("label"), "props": props})
    return out


def load_stage_a(stamp: str) -> list[dict]:
    classes: list[dict] = []
    for path in sorted(RAW_DIR.glob(f"{stamp}_A*.txt")):
        parsed = _extract_json_object(path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            classes += [c for c in (parsed.get("classes") or [])
                        if isinstance(c, dict)]
    return classes


def load_stage_b(stamp: str) -> dict:
    type_map: dict = {}
    for path in sorted(RAW_DIR.glob(f"{stamp}_B*.txt")):
        parsed = _extract_json_object(path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            for c in parsed.get("classes") or []:
                if isinstance(c, dict) and c.get("id"):
                    type_map[c["id"]] = c.get("property_types") or {}
    return type_map


def similarity(h: dict, cand: dict, type_map: dict) -> float:
    name_sim = jaccard(bigrams(h["label"]), bigrams(cand.get("label")))
    h_props = set(h["props"])
    c_props = set((type_map.get(cand.get("id")) or {}).keys())
    if h_props and c_props:
        return round(0.5 * name_sim + 0.5 * jaccard(h_props, c_props), 4)
    return round(name_sim, 4)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stamp", default=None)
    args = ap.parse_args()

    stamps = sorted({p.name.split("_A")[0] for p in RAW_DIR.glob("*_A1.txt")})
    stamp = args.stamp or (stamps[-1] if stamps else None)
    if not stamp:
        print("找不到盲测原始输出（tools/_out/blindtest_raw/*_A1.txt）")
        return 2

    human = human_classes()
    stage_a = load_stage_a(stamp)
    type_map = load_stage_b(stamp)

    by_label = {normalize(h["label"]): h for h in human}
    by_id = {normalize(h["id"]): h for h in human}
    exact: set[str] = set()
    unmatched_h: list[dict] = []
    for cand in stage_a:
        hit = by_label.get(normalize(cand.get("label"))) or \
            by_id.get(normalize(cand.get("id")))
        if hit:
            exact.add(hit["id"])
        else:
            unmatched_h.append(cand)

    # 诊断：每个未精确匹配上的人工类，找最佳语义对应
    rows = []
    matched_semantic = 0
    for h in human:
        if h["id"] in exact:
            continue
        best, best_sim = None, 0.0
        for cand in stage_a:
            sim = similarity(h, cand, type_map)
            if sim > best_sim:
                best, best_sim = cand, sim
        hit = best_sim >= SIM_THRESHOLD
        matched_semantic += 1 if hit else 0
        rows.append({
            "human_id": h["id"], "human_label": h["label"],
            "best_llm_id": (best or {}).get("id"),
            "best_llm_label": (best or {}).get("label"),
            "similarity": best_sim, "semantic_match": hit,
        })

    result = {
        "stamp": stamp,
        "human_class_count": len(human),
        "llm_class_count": len(stage_a),
        "exact_matched": len(exact),
        "exact_recall": round(len(exact) / len(human), 4),
        "semantic_threshold": SIM_THRESHOLD,
        "semantic_matched": len(exact) + matched_semantic,
        "semantic_coverage": round((len(exact) + matched_semantic) / len(human), 4),
        "note": "诊断口径（不改判据）：主口径仍是精确名匹配；此处只回答"
                "「未匹配的那些类，是不是其实被模型用别的名字说了」。",
        "pairs": sorted(rows, key=lambda r: -r["similarity"])[:120],
    }
    out = ROOT / "tools" / "_out" / f"blindtest_diagnose_{stamp}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                   encoding="utf-8")

    print(f"=== 盲测诊断（{stamp}）===")
    print(f"人工类 {result['human_class_count']} / LLM 类 {result['llm_class_count']}")
    print(f"精确名匹配：{result['exact_matched']} → 召回 {result['exact_recall']:.1%}"
          f"（主口径，判据 ≥70% → FAIL）")
    print(f"语义对应（相似度 ≥{SIM_THRESHOLD}）：{result['semantic_matched']} → "
          f"覆盖 {result['semantic_coverage']:.1%}（诊断口径，非判据）")
    print("\n未精确匹配但语义对应的前 20 对：")
    for r in result["pairs"][:20]:
        if r["semantic_match"]:
            print(f"  {r['human_label']:<12} ↔ {r['best_llm_label']:<14}"
                  f" ({r['similarity']:.2f})")
    print(f"\n报告：{out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
