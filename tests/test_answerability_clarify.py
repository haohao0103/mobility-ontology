"""澄清层承重测试（借鉴 auto-ontology entity coverage + clarify + _STUCK_PHRASES）。

契约：
- 覆盖率是纯确定性数值（问句实体候选的认领率），0 LLM；
- verdict 三档 answer / clarify / refuse 不推翻既有 answerable 布尔；
- 未认领词生成 clarify_questions 抛回用户 + proposal_hints 喂提议通道
  （clarify 与 P1 提议在此闭环）；
- agent final 步：放弃式答案 ↔ 可答性对账——可答却放弃=红；
  不可答则附澄清问题（不硬答也不装死）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from freight.agent import ReasoningAgent  # noqa: E402
from freight.answerability import (  # noqa: E402
    CLARIFY_COVERAGE_MIN,
    check_answerable,
    is_stuck_answer,
)
from freight.engine import FreightSystem  # noqa: E402


@pytest.fixture(scope="module")
def system():
    db = ROOT / "runs" / "facts_clarify_test.db"
    if db.exists():
        db.unlink()
    s = FreightSystem(str(db), fresh=True, seed=True, with_scenario=True)
    yield s
    s.conn.close()
    if db.exists():
        db.unlink()


@pytest.fixture()
def agent(tmp_path, system):
    a = ReasoningAgent(
        system, "waybill:WB20261105001", 6,
        question="运单的等候费怎么计算")
    a.planned = {"hypotheses": [], "tasks": []}
    a.degraded = False
    a.question_kind = "instance"
    a.messages = []
    return a


def test_coverage_block_present(system):
    report = check_answerable(system, "运单的等候费怎么计算")
    coverage = report["coverage"]
    assert 0.0 <= coverage["score"] <= 1.0
    assert coverage["verdict"] in ("answer", "clarify", "refuse")
    assert coverage["thresholds"]["clarify_coverage_min"] \
        == CLARIFY_COVERAGE_MIN
    assert isinstance(coverage["clarify_questions"], list)


def test_full_hit_question_has_high_coverage(system):
    report = check_answerable(system, "运单的等候费怎么计算")
    assert report["coverage"]["score"] > 0.5


def test_off_topic_question_refuses_with_clarify_questions(system):
    report = check_answerable(system, "今天的天气怎么样")
    coverage = report["coverage"]
    assert coverage["verdict"] == "refuse"
    assert not report["answerable"]
    assert coverage["clarify_questions"]  # 抛回用户，不让对话死掉
    # 未知词必须出现在澄清问题里（auto-ontology clarify 的核心行为）
    assert any("天气" in q for q in coverage["clarify_questions"])


def test_mixed_question_flags_uncovered_terms(system):
    report = check_answerable(system, "运单的等候费和油价补贴有什么关系")
    coverage = report["coverage"]
    uncovered = "".join(coverage["uncovered_terms"])
    assert "油价" in uncovered or "补贴" in uncovered


def test_proposal_hints_feed_proposal_channel(system):
    report = check_answerable(system, "今天的天气怎么样")
    hints = report["coverage"]["proposal_hints"]
    assert hints
    for hint in hints:
        assert hint["term"]
        assert isinstance(hint["suggested_type_refs"], list)


def test_is_stuck_answer_detection():
    assert is_stuck_answer("根据现有信息无法确定该运单的费用构成")
    assert is_stuck_answer("没有找到相关证据")
    assert not is_stuck_answer("该运单等候费 25 元，依据 R-WAIT-01 规则")


def test_stuck_check_passes_when_not_stuck(agent):
    res = agent._stuck_clarify_check("该运单等候费 25 元，依据规则 R-WAIT-01。")
    assert res == {"checked": 0}


def test_stuck_check_red_when_answerable_but_giving_up(agent):
    # 问句全覆盖 + answerable，模型却说无法确定 → 放弃是错误结论
    res = agent._stuck_clarify_check("根据现有数据无法确定。")
    assert res["checked"] == 1
    assert res["check"]["pass"] is False
    assert "放弃" in res["check"]["label"]


def test_stuck_check_clarifies_when_off_topic(agent):
    agent.question = "今天的天气怎么样"
    res = agent._stuck_clarify_check("无法回答该问题。")
    assert res["checked"] == 1
    assert res["check"]["pass"] is True
    facts = res.get("facts") or []
    assert any("澄清" in f["label"] for f in facts)
    assert any("提案" in f["label"] or "ontology-proposal" in f["value"]
               for f in facts)
