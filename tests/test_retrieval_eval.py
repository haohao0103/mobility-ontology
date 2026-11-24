"""检索评估门禁（plan/ontology-context-retrieval-v4-plan.md §8）。

标注集 tests/fixtures/retrieval_eval.jsonl（82 条，6 上下文 + 跨上下文 + 零命中反例）。
门槛写死在常量里：改动需登记理由（不许悄悄放水）。

指标：
  recall@8          标注类型出现在 top8 的比例（问句级平均）
  MRR@8             第一个标注类型的排名倒数（问句级平均）
  zero_hit_acc      零命中反例正确返回空的比例
  context_hit       期望上下文在返回结果中被覆盖的比例
"""
from __future__ import annotations

import json
import os

import pytest

from freight.semantic import SemanticLayer

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "retrieval_eval.jsonl")

# 门禁门槛（v4 初版；校准需在 plan/ 开发纪要登记）
RECALL_AT_8_MIN = 0.85
MRR_AT_8_MIN = 0.60
ZERO_HIT_ACC_MIN = 0.80
CONTEXT_HIT_MIN = 0.80

# CI 环境写 runs/ 不可接受时隔离到临时文件（查询历史通道不干扰评估）
HISTORY_ENV = {"FREIGHT_EVAL_ISOLATED": "1"}


def load_cases() -> list[dict]:
    cases = []
    with open(FIXTURE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


@pytest.fixture(scope="module")
def layer() -> SemanticLayer:
    return SemanticLayer()


@pytest.fixture(scope="module")
def results(layer: SemanticLayer) -> list[dict]:
    out = []
    for case in load_cases():
        r = layer.object_type_search(case["question"], top_k=8)
        out.append({"case": case, "result": r})
    return out


def test_eval_fixture_size() -> None:
    cases = load_cases()
    assert len(cases) >= 80, f"标注集不足 80 条: {len(cases)}"
    zero = [c for c in cases if c.get("expect_zero")]
    assert len(zero) >= 10, "零命中反例不足 10 条"
    contexts = {c for case in cases for c in (case.get("expected_contexts") or [])}
    assert len(contexts) == 6, f"应覆盖全部 6 个上下文: {sorted(contexts)}"


def test_recall_at_8(results: list[dict]) -> None:
    recalls = []
    for item in results:
        case = item["case"]
        if case.get("expect_zero"):
            continue
        expected = set(case.get("expected_types") or [])
        if not expected:
            continue
        got = {t["id"] for t in item["result"]["types"]}
        recalls.append(len(expected & got) / len(expected))
    actual = sum(recalls) / max(1, len(recalls))
    assert actual >= RECALL_AT_8_MIN, (
        f"recall@8 = {actual:.3f} < {RECALL_AT_8_MIN}（{len(recalls)} 条非反例）")


def test_mrr_at_8(results: list[dict]) -> None:
    rr = []
    for item in results:
        case = item["case"]
        if case.get("expect_zero"):
            continue
        expected = set(case.get("expected_types") or [])
        if not expected:
            continue
        rank = None
        for i, t in enumerate(item["result"]["types"], start=1):
            if t["id"] in expected:
                rank = i
                break
        rr.append(1.0 / rank if rank else 0.0)
    actual = sum(rr) / max(1, len(rr))
    assert actual >= MRR_AT_8_MIN, f"MRR@8 = {actual:.3f} < {MRR_AT_8_MIN}"


def test_zero_hit_accuracy(results: list[dict]) -> None:
    zeros = [item for item in results if item["case"].get("expect_zero")]
    correct = sum(1 for item in zeros if item["result"].get("zero_hit"))
    actual = correct / max(1, len(zeros))
    assert actual >= ZERO_HIT_ACC_MIN, (
        f"零命中准确率 = {actual:.3f} < {ZERO_HIT_ACC_MIN}"
        f"（{len(zeros) - correct}/{len(zeros)} 条反例误命中）")


def test_context_hit_rate(results: list[dict]) -> None:
    hits = []
    for item in results:
        case = item["case"]
        if case.get("expect_zero") or not case.get("expected_contexts"):
            continue
        expected = set(case["expected_contexts"])
        got = {t["context"] for t in item["result"]["types"] if t.get("context")}
        hits.append(bool(expected & got))
    actual = sum(hits) / max(1, len(hits))
    assert actual >= CONTEXT_HIT_MIN, f"上下文命中率 = {actual:.3f} < {CONTEXT_HIT_MIN}"


def test_channels_declared(results: list[dict]) -> None:
    """每个结果必须声明通道与降级（可解释性不变量）。"""
    for item in results:
        r = item["result"]
        assert "channels_used" in r and "degraded" in r and "zero_hit" in r
        assert "vector" in r["degraded"], "向量端点未配置时应显式标注降级"


def test_threshold_enforced(results: list[dict]) -> None:
    """低于阈值的类型不允许出现在结果里（宁缺毋滥）。"""
    threshold = results[0]["result"]["threshold"] if results else 0.0
    for item in results:
        for t in item["result"]["types"]:
            assert t["rrf_score"] >= threshold, (
                f"{t['id']} 分数 {t['rrf_score']} 低于阈值 {threshold}")


if __name__ == "__main__":
    # 直接跑：python3 tests/test_retrieval_eval.py（打印明细基线）
    layer_ = SemanticLayer()
    stats = {"recall": [], "mrr": [], "zero_ok": 0, "zero_n": 0, "ctx_ok": 0, "ctx_n": 0}
    misses = []
    for case in load_cases():
        r = layer_.object_type_search(case["question"], top_k=8)
        if case.get("expect_zero"):
            stats["zero_n"] += 1
            stats["zero_ok"] += 1 if r.get("zero_hit") else 0
            if not r.get("zero_hit"):
                misses.append((case["question"], [t["id"] for t in r["types"]][:4]))
            continue
        expected = set(case.get("expected_types") or [])
        got = {t["id"] for t in r["types"]}
        if expected:
            stats["recall"].append(len(expected & got) / len(expected))
            rank = next((i for i, t in enumerate(r["types"], 1)
                         if t["id"] in expected), None)
            stats["mrr"].append(1.0 / rank if rank else 0.0)
            if not (expected & got):
                misses.append((case["question"], [t["id"] for t in r["types"]][:4]))
        if case.get("expected_contexts"):
            ctx_expected = set(case["expected_contexts"])
            ctx_got = {t["context"] for t in r["types"] if t.get("context")}
            stats["ctx_n"] += 1
            stats["ctx_ok"] += 1 if (ctx_expected & ctx_got) else 0
    n = max(1, len(stats["recall"]))
    print(f"recall@8   = {sum(stats['recall']) / n:.3f}  (门槛 {RECALL_AT_8_MIN})")
    print(f"MRR@8      = {sum(stats['mrr']) / n:.3f}  (门槛 {MRR_AT_8_MIN})")
    print(f"zero_hit   = {stats['zero_ok']}/{stats['zero_n']}")
    print(f"ctx_hit    = {stats['ctx_ok']}/{stats['ctx_n']}")
    print("零命中误报 / 完全未命中样例：")
    for q, ts in misses[:12]:
        print(f"  {q} -> {ts}")
