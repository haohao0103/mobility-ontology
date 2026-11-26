#!/usr/bin/env python3
"""层级指标的随机基线（事前定阈值用；不依赖任何 LLM 结果，故可先于盲测第 2 轮计算）。

要回答的问题：在"人工分组结构已知"的条件下，一个**随机分组器**能拿到多少
pairwise 一致性？阈值必须显著高于这个基线，否则指标会被"随便分也高分"污染。

指标定义（组对一致性，看结构不看组名）：
  P_h = {(a1,a2) : 人工组相同}      （人工同类对）
  P_l = {(l1,l2) : LLM 组相同}      （LLM 同组对）
  J   = |P_h ∩ P_l| / |P_h ∪ P_l|    （组对 Jaccard）
  precision = |交集| / |P_l|   （LLM 判同组的对里，人工也同组的比例）
  recall    = |交集| / |P_h|   （人工同组的对里，LLM 也判同组的比例）

随机基线：把 n 个类随机分成 k 组，组大小按人工组的相对比例分配，B 次重抽样取分布。
退化情形另计：全分一组 / 全各自分一组（pairwise 一致性均为 0，作为下界参照）。
"""
from __future__ import annotations

import random
from itertools import combinations

HUMAN_GROUPS = {
    "vehicle_domain": 18, "carrier_domain": 17, "driver_domain": 17,
    "maintenance_domain": 16, "fuel_domain": 15, "subcontract_domain": 15,
}
K = len(HUMAN_GROUPS)


def pair_jaccard(n: int, assign: list[int], human_assign: list[int]) -> tuple[float, float, float]:
    """返回 (J, precision, recall)。assign/human_assign 长度相同。"""
    p_h = {(i, j) for i, j in combinations(range(n), 2)
           if human_assign[i] == human_assign[j]}
    p_l = {(i, j) for i, j in combinations(range(n), 2)
           if assign[i] == assign[j]}
    inter = len(p_h & p_l)
    union = len(p_h | p_l)
    j = inter / union if union else 0.0
    prec = inter / len(p_l) if p_l else 0.0
    rec = inter / len(p_h) if p_h else 0.0
    return j, prec, rec


def random_assign(n: int, sizes: list[int], rng: random.Random) -> list[int]:
    idx = list(range(n))
    rng.shuffle(idx)
    out = [0] * n
    pos = 0
    for gi, s in enumerate(sizes):
        for c in idx[pos:pos + s]:
            out[c] = gi
        pos += s
    while pos < n:
        out[idx[pos]] = 0
        pos += 1
    return out


def human_assign_for(n: int) -> list[int]:
    """按人工真实比例截取 n 个类时的组标签（取每组前若干，保持均衡）。"""
    assign = []
    for gi, (_g, s) in enumerate(HUMAN_GROUPS.items()):
        assign.extend([gi] * s)
    return assign[:n] if n <= len(assign) else assign


def baseline(n: int, b: int = 2000, seed: int = 20261126) -> dict:
    rng = random.Random(seed)
    human = human_assign_for(n)
    raw = [s * n / sum(HUMAN_GROUPS.values()) for s in HUMAN_GROUPS.values()]
    sizes = [int(x) for x in raw]
    sizes[0] += n - sum(sizes)
    js, ps, rs = [], [], []
    for _ in range(b):
        a = random_assign(n, sizes, rng)
        j, p, r = pair_jaccard(n, a, human)
        js.append(j)
        ps.append(p)
        rs.append(r)
    js.sort()
    ps.sort()
    rs.sort()

    def q(v, p):
        return round(v[min(len(v) - 1, int(p * len(v)))], 4)

    all_one = [0] * n
    all_distinct = list(range(n))
    deg_j, deg_p, deg_r = pair_jaccard(n, all_one, human)
    deg2_j, deg2_p, deg2_r = pair_jaccard(n, all_distinct, human)
    return {
        "n": n, "groups": K, "sizes": sizes, "B": b,
        "random_jaccard": {"mean": round(sum(js) / len(js), 4),
                            "p50": q(js, .5), "p90": q(js, .9), "p95": q(js, .95),
                            "p99": q(js, .99), "max": round(js[-1], 4)},
        "random_precision": {"mean": round(sum(ps) / len(ps), 4), "p95": q(ps, .95),
                              "p99": q(ps, .99)},
        "random_recall": {"mean": round(sum(rs) / len(rs), 4), "p95": q(rs, .95),
                          "p99": q(rs, .99)},
        "degenerate_all_one_group": {"jaccard": deg_j, "precision": deg_p, "recall": deg_r},
        "degenerate_all_distinct": {"jaccard": deg2_j, "precision": deg2_p,
                                     "recall": deg2_r},
    }


if __name__ == "__main__":
    import json
    for n in (25, 40, 46, 60):
        print(f"===== n={n}（6 组）=====")
        print(json.dumps(baseline(n), ensure_ascii=False, indent=1))
