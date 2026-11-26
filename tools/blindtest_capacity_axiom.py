#!/usr/bin/env python3
"""盲测分项补算：三弱项（属性 / 层级 / 公理）+ 第 2 轮判据判定。

★ 口径来源（**判据已事前注册**）：`docs/BLINDTEST_ROUND2_PREREG.md`（2026-11-26 写定，
跑之前定阈值）。本脚本是那份文档的**实现**，两者必须一致——改口径须同步改文档并重新注册。

三项分项：
  1. 公理/状态机（state_machine 声明率 / 初态 / 终态 / 状态集 Jaccard）——观察项
  2. 层级归属（H1 组对一致性：看分组结构不看组名 + 退化检测 + 置换检验 + 样本量守卫；
     H2 业务板块命中率：辅助，不参与判定）——**第 2 轮主判据**
  3. 属性（第 1 轮由主脚本负责，这里不重复）

数据源：
  - 人工基线：ontology/domains_02_capacity.py（98 类，statuses + _group）
  - LLM 原始输出：tools/_out/blindtest_raw/<stamp>_{A,B}*.txt（raw 不入库，属运行产物）

用法：
    python3 tools/blindtest_capacity_axiom.py [--stamp 20261125_201223] [--out FILE]
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import random
import re
import sys
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from freight.agent import _extract_json_object  # noqa: E402

RAW_DIR = ROOT / "tools" / "_out" / "blindtest_raw"
DOMAIN_MODULE = "ontology.domains_02_capacity"

# ── 第 2 轮判据常量（与 docs/BLINDTEST_ROUND2_PREREG.md §2 一一对应）──
LAYER_J_MIN = 0.30            # H1-b 结构一致性下限
LAYER_MIN_SAMPLES = 20        # H1-d 最小样本量守卫
LAYER_MIN_GROUPS = 3          # H1-a 非退化：组数下限
LAYER_MAX_GROUP_SHARE = 0.60  # H1-a 非退化：最大组占比上限
PERM_B = 2000                 # H1-c 置换检验次数
PERM_SEED = 20261126
SEM_SIM_MIN = 0.40            # 宽口径语义对应阈值（严格档用精确名匹配）
ALPHA = 0.01                  # H1-c 显著性水平

# H2 辅助指标用的别名表（人工构建，带主观性；只影响 H2，不影响 H1 判定）
ALIAS_TABLE = {
    "vehicle_domain": ["vehicle", "车辆", "车", "车辆域"],
    "driver_domain": ["driver", "司机", "人", "驾驶员", "人员"],
    "carrier_domain": ["carrier", "承运商", "合作方", "货主", "承运"],
    "subcontract_domain": ["subcontract", "外包", "分包", "承包商"],
    "maintenance_domain": ["maintenance", "维保", "修车", "维修", "保养"],
    "fuel_domain": ["fuel", "能源", "油电", "加油", "油气"],
}


def _load_blindtest_module():
    spec = importlib.util.spec_from_file_location(
        "btc", ROOT / "tools" / "blindtest_capacity.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v else []
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return [str(v)]


def bigrams(text: str) -> set[str]:
    t = re.sub(r"[\s\-_/（）()【】\[\]]", "", str(text or ""))
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def similarity(h: dict, cand: dict, type_map: dict) -> float:
    """与 blindtest_capacity_diagnose.py 同式：0.5×名称 bigram + 0.5×属性集 Jaccard。"""
    name_sim = jaccard(bigrams(h["label"]), bigrams(cand.get("label")))
    h_props = set(h["props"])
    c_props = set((type_map.get(cand.get("id")) or {}).keys())
    if h_props and c_props:
        return round(0.5 * name_sim + 0.5 * jaccard(h_props, c_props), 4)
    return round(name_sim, 4)


def build_wide_mapping(human: list[dict], stage_a: list[dict],
                       type_map: dict) -> list[tuple[dict, dict, float]]:
    """宽口径映射：语义对应 ≥SEM_SIM_MIN，**一对一贪心**（相似度降序），
    避免同一个人工类被多个 LLM 类重复占用而扭曲组对统计。"""
    scored: list[tuple[float, dict, dict]] = []
    for cand in stage_a:
        for h in human:
            s = similarity(h, cand, type_map)
            if s >= SEM_SIM_MIN:
                scored.append((s, h, cand))
    scored.sort(key=lambda x: -x[0])
    used_h: set[str] = set()
    used_l: set[str] = set()
    out: list[tuple[dict, dict, float]] = []
    for s, h, cand in scored:
        if h["id"] in used_h or cand.get("id") in used_l:
            continue
        used_h.add(h["id"])
        used_l.add(cand.get("id"))
        out.append((h, cand, s))
    return out


def pair_counts(items: list[tuple[str, str]]) -> tuple[set, set, int]:
    p_same = {(a, b) for a, b in combinations(range(len(items)), 2)
              if items[a][0] == items[b][0]}
    p_llm = {(a, b) for a, b in combinations(range(len(items)), 2)
             if items[a][1] == items[b][1]}
    return p_same, p_llm, len(p_same & p_llm)


def layer_metrics(pairs: list[tuple[dict, dict]]) -> dict:
    """pairs: [(human_class, llm_class), ...]（llm_class 需带 group）。"""
    items = [(p[0].get("group") or "", p[1].get("group") or "") for p in pairs]
    p_h, p_l, inter = pair_counts(items)
    union = len(p_h | p_l)
    j = inter / union if union else 0.0
    prec = inter / len(p_l) if p_l else 0.0
    rec = inter / len(p_h) if p_h else 0.0
    llm_groups = [g for _h, g in items if g]
    distinct = sorted(set(llm_groups))
    counts = {g: llm_groups.count(g) for g in distinct}
    n = len(items)
    coverage = (len(llm_groups) / n) if n else 0.0
    max_share = (max(counts.values()) / n) if counts else 0.0
    non_degenerate = (len(distinct) >= LAYER_MIN_GROUPS
                      and max_share <= LAYER_MAX_GROUP_SHARE)
    return {
        "comparable_count": n,
        "group_coverage": round(coverage, 4),
        "llm_group_count": len(distinct),
        "llm_group_sizes": counts,
        "max_group_share": round(max_share, 4),
        "non_degenerate": non_degenerate,
        "pair_jaccard": round(j, 4),
        "pair_precision": round(prec, 4),
        "pair_recall": round(rec, 4),
        "human_same_pairs": len(p_h),
        "llm_same_pairs": len(p_l),
        "intersection": inter,
    }


def permutation_test(pairs: list[tuple[dict, dict]]) -> dict:
    """置换检验：置换**人工组标签**（保持类数与人工组大小分布），B=PERM_B 次。"""
    n = len(pairs)
    if n < 2:
        return {"p_value": None, "B": PERM_B, "note": "样本不足"}
    human_groups = [p[0].get("group") or "" for p in pairs]
    llm_groups = [p[1].get("group") or "" for p in pairs]
    p_h, _p_l0, _ = pair_counts(list(zip(human_groups, human_groups)))

    def stat(hg: list[str]) -> float:
        _a, b, inter = pair_counts(list(zip(hg, llm_groups)))
        union = len(p_h | b)
        return inter / union if union else 0.0

    observed = stat(human_groups)
    rng = random.Random(PERM_SEED)
    hits = 0
    null: list[float] = []
    for _ in range(PERM_B):
        shuffled = human_groups[:]
        rng.shuffle(shuffled)
        s = stat(shuffled)
        null.append(s)
        if s >= observed - 1e-12:
            hits += 1
    null.sort()
    return {
        "observed_jaccard": round(observed, 4),
        "p_value": round((1 + hits) / (PERM_B + 1), 4),
        "B": PERM_B,
        "seed": PERM_SEED,
        "null_p95": round(null[int(0.95 * PERM_B)], 4),
        "null_p99": round(null[int(0.99 * PERM_B)], 4),
        "null_max": round(null[-1], 4),
    }


def bootstrap_ci(pairs: list[tuple[dict, dict]], b: int = 1000,
                 seed: int = PERM_SEED) -> dict:
    """对可比类做 bootstrap（重采样有放回），给 Jaccard 的不确定区间。
    ★ **诊断用，不参与判定**（判据是预注册的 4 条条件，见 docs/BLINDTEST_ROUND2_PREREG.md §2）。
    用途：诚实回答"观测值离阈值 0.30 的差距是否在抽样噪声内"——即使 CI 覆盖阈值，
    判定仍是 FAIL（判据按点值执行）。"""
    n = len(pairs)
    if n < 4:
        return {"note": "样本不足"}
    rng = random.Random(seed)
    vals: list[float] = []
    for _ in range(b):
        idx = [rng.randrange(n) for _ in range(n)]
        sub = [pairs[i] for i in idx]
        items = [(p[0].get("group") or "", p[1].get("group") or "") for p in sub]
        p_h, p_l, inter = pair_counts(items)
        union = len(p_h | p_l)
        vals.append(inter / union if union else 0.0)
    vals.sort()
    return {
        "B": b, "seed": seed,
        "ci95_low": round(vals[int(0.025 * b)], 4),
        "ci95_high": round(vals[int(0.975 * b)], 4),
        "median": round(vals[b // 2], 4),
        "covers_threshold_0_30": bool(vals[int(0.025 * b)] <= 0.30 <= vals[int(0.975 * b)]),
        "note": "诊断用，不改变判定（判据按点值执行）",
    }


def alias_map_group(llm_group: str) -> str | None:
    g = str(llm_group or "").strip().lower()
    if not g:
        return None
    for canonical, aliases in ALIAS_TABLE.items():
        for a in aliases:
            if a.lower() in g:
                return canonical
    return None


def board_hit(pairs: list[tuple[dict, dict]]) -> dict:
    """H2 辅助：LLM 组名经别名表映射后，是否与人工板块一致。"""
    hit = miss = unmapped = 0
    details = []
    for h, cand in pairs:
        lg = cand.get("group")
        mapped = alias_map_group(lg)
        if mapped is None:
            unmapped += 1
            details.append({"human": h["id"], "llm": cand.get("id"),
                            "llm_group": lg, "mapped": None, "hit": False})
            continue
        ok = mapped == (h.get("group") or "")
        hit += int(ok)
        miss += int(not ok)
        details.append({"human": h["id"], "llm": cand.get("id"),
                        "llm_group": lg, "mapped": mapped, "hit": ok,
                        "human_group": h.get("group")})
    total = hit + miss + unmapped
    return {
        "board_hit_rate": round(hit / total, 4) if total else None,
        "hit": hit, "miss": miss, "unmapped": unmapped, "total": total,
        "note": "辅助指标，不参与判定（别名表人工构建，带主观性）",
        "details": details,
    }


def verdict_round2(wide: dict, perm: dict) -> dict:
    """第 2 轮判据判定（只看宽口径）。"""
    if wide is None:
        return {"overall": "NOT_APPLICABLE", "reason": "无可比集"}
    checks = {
        "sample_guard": {"ok": wide["comparable_count"] >= LAYER_MIN_SAMPLES,
                         "value": wide["comparable_count"],
                         "threshold": LAYER_MIN_SAMPLES},
        "non_degenerate": {"ok": bool(wide["non_degenerate"]),
                           "llm_group_count": wide["llm_group_count"],
                           "max_group_share": wide["max_group_share"],
                           "rule": f"组数≥{LAYER_MIN_GROUPS} 且最大组占比≤{LAYER_MAX_GROUP_SHARE}"},
        "jaccard": {"ok": wide["pair_jaccard"] >= LAYER_J_MIN,
                    "value": wide["pair_jaccard"], "threshold": LAYER_J_MIN},
        "significance": {"ok": (perm.get("p_value") is not None
                                and perm["p_value"] < ALPHA),
                         "p_value": perm.get("p_value"), "alpha": ALPHA},
    }
    if not checks["sample_guard"]["ok"]:
        overall = "NOT_APPLICABLE"
    elif all(c["ok"] for c in checks.values()):
        overall = "PASS"
    else:
        overall = "FAIL"
    return {"overall": overall, "checks": checks,
            "criteria": "docs/BLINDTEST_ROUND2_PREREG.md §2（2026-11-26 事前注册）"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stamp", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    stamps = sorted({p.name.split("_A")[0] for p in RAW_DIR.glob("*_A1.txt")})
    stamp = args.stamp or (stamps[-1] if stamps else None)
    if not stamp:
        print("找不到盲测原始输出（tools/_out/blindtest_raw/*_A1.txt）")
        return 2

    btc = _load_blindtest_module()
    mod = importlib.import_module(DOMAIN_MODULE)

    human = btc.human_classes()
    grp = {c["id"]: c.get("_group") for c in mod.CLASSES}
    for h in human:
        h["group"] = grp.get(h["id"])

    stage_a: list[dict] = []
    stage_b: list[dict] = []
    parse_fail: list[str] = []
    for p in sorted(RAW_DIR.glob(f"{stamp}_A*.txt")):
        parsed = _extract_json_object(p.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            parse_fail.append(p.name)
            continue
        stage_a.extend(c for c in (parsed.get("classes") or []) if isinstance(c, dict))
    for p in sorted(RAW_DIR.glob(f"{stamp}_B*.txt")):
        parsed = _extract_json_object(p.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            parse_fail.append(p.name)
            continue
        stage_b.extend(c for c in (parsed.get("classes") or []) if isinstance(c, dict))

    sm_by_id = {c.get("id"): c.get("state_machine") for c in stage_b if c.get("id")}
    type_map = {c.get("id"): c.get("property_types") or {} for c in stage_b if c.get("id")}

    # ---------------- 公理 / 状态机（观察项）----------------
    pairs_exact, _missed, _extra = btc.match_classes(human, stage_a)
    matched = len(pairs_exact)
    declared = init_in = term_in = init_pos = exact_states = 0
    jac_vals: list[float] = []
    cover_vals: list[float] = []
    per_class: list[dict] = []
    uni_h: set[str] = set()
    uni_l: set[str] = set()

    for j, hi in pairs_exact.items():
        cid = stage_a[j].get("id")
        sm = sm_by_id.get(cid)
        h_states = set(human[hi]["statuses"])
        uni_h |= h_states
        if not sm:
            continue
        declared += 1
        l_states = {s for s in _as_list(sm.get("states")) if s and s != "OTHER"}
        uni_l |= l_states
        inis = _as_list(sm.get("initial"))
        terms = _as_list(sm.get("terminal"))
        init = inis[0] if inis else ""
        term = terms[0] if terms else ""
        ok_i = init in h_states
        ok_t = term in h_states
        ok_pos = bool(human[hi]["statuses"]) and init == human[hi]["statuses"][0]
        init_in += int(ok_i)
        term_in += int(ok_t)
        init_pos += int(ok_pos)
        inter = l_states & h_states
        union = l_states | h_states
        jac = len(inter) / len(union) if union else 0.0
        jac_vals.append(jac)
        cover = len(inter) / len(h_states) if h_states else 0.0
        cover_vals.append(cover)
        ex = (l_states == h_states)
        exact_states += int(ex)
        per_class.append({
            "human_class": human[hi]["id"], "llm_class": cid,
            "human_states": sorted(h_states), "llm_states": sorted(l_states),
            "initial": init, "terminal": term,
            "initial_in_human_set": ok_i, "terminal_in_human_set": ok_t,
            "initial_equals_first_human_state": ok_pos,
            "states_exact": ex, "states_jaccard": round(jac, 4),
            "human_state_coverage": round(cover, 4),
        })

    # ---------------- 层级 / 分类归属 ----------------
    a_with_group = sum(1 for c in stage_a if c.get("group"))
    wide_pairs = build_wide_mapping(human, stage_a, type_map)
    wide_with_group = [(h, c) for h, c, _s in wide_pairs if c.get("group")]
    strict_pairs = [(human[hi], stage_a[j]) for j, hi in pairs_exact.items()
                    if stage_a[j].get("group")]

    layer = {
        "stage_a_class_count": len(stage_a),
        "stage_a_with_group": a_with_group,
        "group_field_rate": round(a_with_group / len(stage_a), 4) if stage_a else None,
        "wide_mapping_count": len(wide_pairs),
        "wide_mapping_with_group": len(wide_with_group),
        "strict_mapping_count": len(strict_pairs),
    }
    if a_with_group == 0:
        layer["verdict"] = ("Stage A 完全没有 group 字段 ⇒ 层级不可测"
                            "（第 1 轮状态：模板未要求 group）")
        layer["wide"] = None
        layer["strict"] = None
        layer["permutation"] = {"p_value": None, "note": "无 group 字段"}
        layer["board_hit"] = None
        verdict_r2 = {"overall": "NOT_APPLICABLE", "reason": "Stage A 无 group 字段"}
    else:
        layer["wide"] = layer_metrics(wide_with_group)
        layer["strict"] = layer_metrics(strict_pairs)
        layer["permutation"] = permutation_test(wide_with_group)
        layer["bootstrap_ci"] = bootstrap_ci(wide_with_group)
        layer["board_hit"] = board_hit(wide_with_group)
        layer["llm_group_names"] = sorted({str(c.get("group")) for _h, c in wide_with_group})
        layer["verdict"] = "已测（判据见 docs/BLINDTEST_ROUND2_PREREG.md §2）"
        verdict_r2 = verdict_round2(layer["wide"], layer["permutation"])

    res = {
        "stamp": stamp,
        "domain": DOMAIN_MODULE,
        "note": "分项补算：公理/层级（属性由主脚本负责）。第 2 轮判据已事前注册"
                "（docs/BLINDTEST_ROUND2_PREREG.md）；raw 不入库，本文件为可入库的证据摘要",
        "denominators": {
            "human_class_count": len(human),
            "stage_a_class_count": len(stage_a),
            "stage_b_class_count": len(stage_b),
            "matched_classes_exact": matched,
            "declared_state_machine_classes": declared,
            "wide_mapping_count": len(wide_pairs),
            "wide_mapping_with_group": len(wide_with_group),
            "strict_mapping_with_group": len(strict_pairs),
        },
        "parse_fail_files": parse_fail,
        "axiom": {
            "declaration_rate": round(declared / matched, 4) if matched else None,
            "initial_in_human_states": init_in,
            "terminal_in_human_states": term_in,
            "initial_hit_rate": round(init_in / declared, 4) if declared else None,
            "terminal_hit_rate": round(term_in / declared, 4) if declared else None,
            "initial_equals_first_human_state": init_pos,
            "initial_position_hit_rate": round(init_pos / declared, 4) if declared else None,
            "states_exact_match": exact_states,
            "states_exact_rate": round(exact_states / declared, 4) if declared else None,
            "states_jaccard_mean": round(sum(jac_vals) / len(jac_vals), 4) if jac_vals else None,
            "states_jaccard_median": (sorted(jac_vals)[len(jac_vals) // 2] if jac_vals else None),
            "human_state_coverage_mean": (round(sum(cover_vals) / len(cover_vals), 4)
                                          if cover_vals else None),
            "human_state_universe": len(uni_h),
            "llm_state_universe": len(uni_l),
            "universe_jaccard": (round(len(uni_h & uni_l) / len(uni_h | uni_l), 4)
                                 if (uni_h | uni_l) else None),
            "other_removed": True,
            "per_class": per_class,
        },
        "hierarchy": layer,
        "verdict_round2": verdict_r2,
    }

    out = Path(args.out) if args.out else (
        ROOT / "tools" / "_out" / f"blindtest_axiom_{stamp}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    a, h = res["axiom"], res["hierarchy"]
    print(f"=== 盲测分项补算（{stamp}）===")
    print(f"分母：人工 {len(human)} 类 / Stage A {len(stage_a)} 类 / "
          f"精确名匹配 {matched} 类 / 声明 state_machine {declared} 类")
    print(f"[公理·声明率] {a['declaration_rate']}")
    if a["declaration_rate"]:
        print(f"[公理·初态] 集合 {a['initial_hit_rate']} / 位置 "
              f"{a['initial_position_hit_rate']}；[终态] {a['terminal_hit_rate']}")
        print(f"[公理·状态集] 精确一致 {a['states_exact_rate']}；Jaccard 均值 "
              f"{a['states_jaccard_mean']}；全集 Jaccard {a['universe_jaccard']}")
    print(f"[层级] Stage A 带 group {h['stage_a_with_group']}/{h['stage_a_class_count']}"
          f"（{h['group_field_rate']:.1%}）；宽口径可比 {h['wide_mapping_with_group']}、"
          f"严格 {h['strict_mapping_count']}")
    if h.get("wide"):
        w = h["wide"]
        print(f"[层级·宽] J={w['pair_jaccard']} prec={w['pair_precision']} "
              f"rec={w['pair_recall']}｜组数 {w['llm_group_count']} "
              f"最大组占比 {w['max_group_share']}｜退化={not w['non_degenerate']}")
        print(f"[层级·置换] p={h['permutation']['p_value']}（零分布 "
              f"p99={h['permutation']['null_p99']}）")
        ci = h.get("bootstrap_ci") or {}
        if ci.get("ci95_low") is not None:
            print(f"[层级·bootstrap] J 95%CI [{ci['ci95_low']}, {ci['ci95_high']}]"
                  f"（覆盖阈值 0.30：{ci['covers_threshold_0_30']}；诊断用，不改判定）")
        print(f"[层级·LLM 组名] {h.get('llm_group_names')}")
        print(f"[层级·板块命中 H2] {h['board_hit']['board_hit_rate']}"
              f"（命中 {h['board_hit']['hit']}/未映射 {h['board_hit']['unmapped']}"
              f"/错 {h['board_hit']['miss']}）")
    print(f"[第 2 轮判据] {verdict_r2['overall']}")
    try:
        shown = out.relative_to(ROOT)
    except ValueError:
        shown = out
    print(f"\n结果：{shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
