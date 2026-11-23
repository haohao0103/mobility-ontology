"""问句级 answerability（借鉴 selfgraph）与 tier 分级（借鉴 selfgraph levels）。

这两项的共同点：**让"不知道"与"这次花了什么代价"变成可查询的元数据**，
而不是靠人记、靠模型自觉。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from freight.answerability import (  # noqa: E402
    NO_DECLARED_SOURCE,
    NO_ONTOLOGY_MATCH,
    check_answerable,
)
from freight.tiers import (  # noqa: E402
    TIER_ORDER,
    cheapest_first,
    tier_for,
    with_tiers,
)


@pytest.fixture(scope="module")
def system():
    from freight.engine import FreightSystem
    db = ROOT / "runs" / "facts_answerability_test.db"
    if db.exists():
        db.unlink()
    s = FreightSystem(str(db), fresh=True, seed=True, with_scenario=True)
    yield s
    s.conn.close()
    if db.exists():
        db.unlink()


class TestAnswerability:
    def test_unknown_question_is_not_answerable(self, system):
        """**防假阳性**：没命中本体的问句，绝不能判成"可答"。

        这是最容易出的错：找不到缺口就以为没问题。
        """
        r = check_answerable(system, "今天的天气怎么样？")
        assert r["answerable"] is False
        assert any(m["code"] == NO_ONTOLOGY_MATCH for m in r["missing"])

    def test_measure_with_declared_rule_is_answerable(self, system):
        """「等候费按规则应收多少」→ 本体里有规则声明 → 可答，且指明是哪条规则。"""
        r = check_answerable(system, "这单等候费按规则应收多少？")
        assert r["answerable"] is True, r["missing"]
        assert r["measure"]["status"] == "declared"
        rules = [x["rule_id"] for v in r["measure"]["sources"].values() for x in v]
        assert "R-WAIT-01" in rules

    def test_measure_without_rule_is_blocked(self, system):
        """没有规则声明的度量值 → 报 NO_DECLARED_SOURCE（答案不在数据里，别编）。"""
        r = check_answerable(system, "装货费该收多少？")
        assert r["answerable"] is False
        assert any(m["code"] == NO_DECLARED_SOURCE for m in r["missing"])

    def test_object_question_is_answerable(self, system):
        r = check_answerable(system, "运单现在是什么状态？")
        assert r["answerable"] is True
        assert any(t["id"] == "Waybill" for t in r["matched"]["object_types"])

    def test_missing_items_carry_codes(self, system):
        r = check_answerable(system, "今天的天气怎么样？")
        for item in r["missing"]:
            assert item.get("code"), item
            assert item.get("message"), item

    def test_result_declares_tiers(self, system):
        """结果要报"由哪档产出"——借鉴 selfgraph 的 result.tiers。"""
        r = check_answerable(system, "这单等候费按规则应收多少？")
        assert r["tiers"] == ["declaration", "deterministic"]
        assert "deterministic" in TIER_ORDER

    def test_does_not_call_llm(self, system):
        """纯确定性：把 LLM 客户端换成会炸的替身，仍然要能跑完。"""
        import freight.agent as agent_mod

        class Boom:
            def __getattr__(self, name):
                raise AssertionError(f"answerability 不该调用 LLM：{name}")

        original = getattr(agent_mod, "LLMClient", None)
        agent_mod.LLMClient = Boom
        try:
            r = check_answerable(system, "这单等候费按规则应收多少？")
            assert r["answerable"] is True
        finally:
            if original is not None:
                agent_mod.LLMClient = original


class TestTiers:
    def test_tier_declared_in_ontology(self, system):
        """档位声明在本体里 → 换档不改代码。"""
        assert tier_for(system.sem, "ontology.catalog") == "declaration"
        assert tier_for(system.sem, "ontology.check_answerable") == "declaration"
        assert tier_for(system.sem, "function.fee_breakdown") == "deterministic"

    def test_unknown_tool_falls_back_conservatively(self, system):
        """没声明档位的工具 → 保守标高（标低了会让人以为答案比实际更确定）。"""
        tier = tier_for(system.sem, "some.unknown.tool")
        assert tier in TIER_ORDER

    def test_with_tiers_reports_meaning(self):
        out = with_tiers({"x": 1}, ["deterministic"])
        assert out["tiers"] == ["deterministic"]
        assert out["tier_meaning"] and isinstance(out["tier_meaning"][0], str)

    def test_cheapest_first_orders_by_cost(self):
        """「只送未决」的执行顺序：便宜的档先跑。"""
        ordered = cheapest_first([
            ("function.fee_breakdown", "deterministic"),
            ("ontology.catalog", "declaration"),
            ("reasoning-chain", "agent"),
        ])
        assert [t for _, t in ordered] == ["declaration", "deterministic", "agent"]

    def test_ontology_declares_tier_for_every_primitive(self, system):
        """每个 primitive 与 function 都必须声明档位——否则成本不可见。"""
        for coll in ("primitives", "functions"):
            for item in system.sem.raw.get(coll) or []:
                assert item.get("tier") in TIER_ORDER, (coll, item.get("name"))
