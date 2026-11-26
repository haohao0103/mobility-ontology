"""盲测层级分项指标的回归测试（2026-11-26 第 2 轮）。

★ 为什么要有这个文件：第 2 轮实现里出过一处真 bug——`pair_counts` 把集合交集写成
`len(p_same) & len(p_llm)`（整数按位与，而非 `len(p_same & p_llm)`），
导致 Jaccard 与置换检验全部算错（p 值假性 = 1.0）。本文件把该 bug 钉死，
同时把"预注册文档 ↔ 代码常量"的一致性钉死（判据与实现不得漂移）。

不依赖 tools/_out 下的 raw（raw 不入库），全部用合成数据。
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_axiom():
    spec = importlib.util.spec_from_file_location(
        "bc_axiom", ROOT / "tools" / "blindtest_capacity_axiom.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ax = _load_axiom()


# ── 1. pair_counts 的交集必须是集合交集（防按位与 bug）──
def test_pair_counts_intersection_is_set_intersection():
    items = [("a", "x"), ("a", "y"), ("b", "z"), ("c", "w")]
    p_same, p_llm, inter = ax.pair_counts(items)
    assert len(p_same) == 1          # 人工同类对：(0,1)
    assert len(p_llm) == 0           # LLM 组全不同
    assert inter == 0
    assert inter == len(p_same & p_llm)


def test_pair_counts_intersection_detects_bitwise_and_collision():
    """关键例：len(p_same)=1、len(p_llm)=1，但**真实交集=0**
    ⇒ 整数按位与会给出 1 & 1 = 1（假交集），集合交集给 0。"""
    items = [("a", "x"), ("a", "y"), ("b", "x")]
    p_same, p_llm, inter = ax.pair_counts(items)
    assert len(p_same) == 1          # 人工同类对：(0,1)
    assert len(p_llm) == 1           # LLM 同组对：(0,2)
    assert inter == 0, "按位与会算成 1 & 1 = 1；真实交集是 0"
    assert inter == len(p_same & p_llm)


# ── 2. 指标的基本性质 ──
def _pairs(groups: list[tuple[str, str]]):
    return [({"group": h}, {"group": g}) for h, g in groups]


def test_layer_metrics_perfect_alignment_is_one():
    """6 个类 3 组各 2 个：组数 3 ≥ LAYER_MIN_GROUPS、最大组占比 1/3 ≤0.6 ⇒ 非退化。"""
    m = ax.layer_metrics(_pairs([("g1", "g1"), ("g1", "g1"),
                                  ("g2", "g2"), ("g2", "g2"),
                                  ("g3", "g3"), ("g3", "g3")]))
    assert m["pair_jaccard"] == 1.0
    assert m["pair_precision"] == 1.0
    assert m["pair_recall"] == 1.0
    assert m["non_degenerate"] is True


def test_layer_metrics_two_groups_counts_as_degenerate():
    """组数 < LAYER_MIN_GROUPS(3) ⇒ 判退化（哪怕结构完全一致）。
    这条说明 H1-a 不只是防"全塞一组"，也防"组数过少"。"""
    m = ax.layer_metrics(_pairs([("g1", "g1"), ("g1", "g1"),
                                  ("g2", "g2"), ("g2", "g2")]))
    assert m["pair_jaccard"] == 1.0
    assert m["non_degenerate"] is False
    assert m["llm_group_count"] == 2


def test_layer_metrics_all_distinct_is_zero():
    m = ax.layer_metrics(_pairs([("g1", "a"), ("g2", "b"), ("g3", "c")]))
    assert m["pair_jaccard"] == 0.0


def test_layer_metrics_detects_degenerate_all_one_group():
    """退化"全塞一组"在 Jaccard 上虚高（人工组均衡 ⇒ recall=1.0），
    必须靠 non_degenerate 拦下——这正是 H1-a 存在的理由。"""
    m = ax.layer_metrics(_pairs([("g1", "all"), ("g2", "all"), ("g3", "all"),
                                  ("g1", "all"), ("g2", "all")]))
    assert m["llm_group_count"] == 1
    assert m["max_group_share"] == 1.0
    assert m["non_degenerate"] is False
    assert m["pair_recall"] == 1.0


def test_layer_metrics_finer_partition_gives_high_precision_low_recall():
    """第 2 轮实测形态：模型把人工一个组拆成多个主题组 ⇒ 精度高、召回低。
    构造：veh 3 个类被拆成 3 个主题组（veha/vehb/vehc）⇒ 人工 3 个同类对全部丢失；
    drv 2 个类仍同组（drvx/drvx）⇒ 保留 1 对。"""
    m = ax.layer_metrics(_pairs([("veh", "veha"), ("veh", "vehb"), ("veh", "vehc"),
                                  ("drv", "drvx"), ("drv", "drvx")]))
    assert m["pair_precision"] == 1.0     # LLM 同组的那一对确实是人工同类对
    assert m["pair_recall"] == 0.25       # 人工 4 个同类对只保住 1 个
    assert m["non_degenerate"] is True


# ── 3. 置换检验 ──
def test_permutation_p_value_small_when_structure_matches():
    """n=12（4 组 × 3 类）时零分布分辨率足够（12! 排列空间），
    完全一致的概率 ~6.5e-5 ⇒ p 可 <0.01。
    ★ 注意：小样本下置换 p 有分辨率下限（n=6 时 ~1/720，p<0.01 不可达），
    这是置换检验的固有性质，不是实现缺陷。"""
    pairs = _pairs([(f"g{g}", f"g{g}") for g in range(4) for _ in range(3)])
    perm = ax.permutation_test(pairs)
    assert perm["p_value"] < 0.01
    assert perm["observed_jaccard"] == 1.0


def test_permutation_p_value_resolution_floor_on_tiny_sample():
    """n=6 时零分布只有 720 种不同置换 ⇒ 最小可达 p ≈ 1/720，p<0.01 不可达。"""
    pairs = _pairs([(f"g{g}", f"g{g}") for g in range(3) for _ in range(2)])
    perm = ax.permutation_test(pairs)
    assert perm["observed_jaccard"] == 1.0
    assert perm["p_value"] > 0.005      # 落在分辨率下限附近，而非 0


def test_permutation_p_value_not_significant_for_unrelated_partition():
    perm = ax.permutation_test(_pairs([("g1", "g1"), ("g2", "g2"), ("g3", "g3"),
                                        ("g4", "g4"), ("g5", "g5"), ("g6", "g6")]))
    assert perm["p_value"] > 0.01


# ── 4. 判据判定逻辑 ──
def test_verdict_not_applicable_when_sample_guard_fails():
    wide = {"comparable_count": 10, "non_degenerate": True, "llm_group_count": 5,
            "max_group_share": 0.3, "pair_jaccard": 0.9}
    v = ax.verdict_round2(wide, {"p_value": 0.0001})
    assert v["overall"] == "NOT_APPLICABLE"
    assert v["checks"]["sample_guard"]["ok"] is False


def test_verdict_fail_on_degenerate_even_with_high_jaccard():
    wide = {"comparable_count": 40, "non_degenerate": False, "llm_group_count": 1,
            "max_group_share": 1.0, "pair_jaccard": 0.6}
    v = ax.verdict_round2(wide, {"p_value": 0.0001})
    assert v["overall"] == "FAIL"
    assert v["checks"]["non_degenerate"]["ok"] is False


def test_verdict_pass_when_all_four_checks_ok():
    wide = {"comparable_count": 40, "non_degenerate": True, "llm_group_count": 6,
            "max_group_share": 0.25, "pair_jaccard": 0.42}
    v = ax.verdict_round2(wide, {"p_value": 0.0005})
    assert v["overall"] == "PASS"
    assert all(c["ok"] for c in v["checks"].values())


def test_verdict_fail_when_jaccard_below_threshold():
    wide = {"comparable_count": 40, "non_degenerate": True, "llm_group_count": 6,
            "max_group_share": 0.25, "pair_jaccard": 0.26}
    v = ax.verdict_round2(wide, {"p_value": 0.0005})
    assert v["overall"] == "FAIL"
    assert v["checks"]["jaccard"]["ok"] is False
    assert v["checks"]["significance"]["ok"] is True


# ── 5. 预注册文档 ↔ 代码常量 必须一致（防漂移）──
def test_prereg_thresholds_match_code_constants():
    doc = (ROOT / "docs" / "BLINDTEST_ROUND2_PREREG.md").read_text(encoding="utf-8")

    def grab(pattern: str) -> str:
        m = re.search(pattern, doc)
        assert m, f"预注册文档里找不到：{pattern}"
        return m.group(1)

    assert float(grab(r"J ≥ \*\*([0-9.]+)\*\*")) == ax.LAYER_J_MIN
    assert int(grab(r"\\\|S\\\| ≥ \*\*(\d+)\*\*")) == ax.LAYER_MIN_SAMPLES
    assert int(grab(r"组数 ≥(\d+)")) == ax.LAYER_MIN_GROUPS
    assert float(grab(r"最大组占比 ≤([0-9.]+)")) == ax.LAYER_MAX_GROUP_SHARE
    assert int(grab(r"B=(\d+)")) == ax.PERM_B
    assert float(grab(r"p < \*\*([0-9.]+)\*\*")) == ax.ALPHA
    assert float(grab(r"相似度 ≥ \*\*([0-9.]+)\*\*")) == ax.SEM_SIM_MIN
