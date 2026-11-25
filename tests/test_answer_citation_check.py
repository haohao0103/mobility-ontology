"""结论引用溯源（借鉴 OntoSmith quote 逐字校验的工程化版本）。

契约：LLM 最终答案里引用的**对象 ID / 证据 ID / 规则 ID**必须逐字出现在
本次观察语料（observation.result + arguments + fee_analysis 确定性汇总）里；
编造引用被确定性抓住并红在 final 步的 check 行上。金额是软引用（合计/差额
是合法派生值），只列不判。
"""
import os

import pytest

from freight.agent import ReasoningAgent
from freight.engine import FreightSystem


@pytest.fixture()
def agent(tmp_path):
    system = FreightSystem(os.path.join(str(tmp_path), "f.db"),
                           fresh=True, seed=True)
    agent = ReasoningAgent(system, "waybill:WB20261105001", 6)
    agent.planned = {"hypotheses": [], "tasks": []}
    agent.degraded = False
    agent.question_kind = "instance"
    agent.messages = []
    return agent


OBS = [{
    "tool": "ontology.query",
    "arguments": {"type": "FeeItem", "where": {"fee_type": "waiting"}},
    "ok": True,
    "rows": 3,
    "result": {"objects": [
        {"object_id": "fee:F003", "type": "FeeItem",
         "properties": {"fee_type": "waiting", "actual_amount": 25.0}},
    ]},
}, {
    # 规则 ID 的合法来源：tbox.describe 的观察结果
    "tool": "tbox.describe",
    "arguments": {"type": "FeeItem"},
    "ok": True,
    "rows": 1,
    "result": {"rules": [{"id": "R-WAIT-01", "label": "等候费规则"}]},
}]


def test_citations_present_in_observations_pass(agent):
    res = agent._answer_citation_check(
        "费用项 fee:F003 实收 25.00 元，依据规则 R-WAIT-01。", OBS, None)
    assert res["checked"] >= 2
    assert res["unmatched_hard"] == []


def test_fabricated_object_id_is_caught(agent):
    res = agent._answer_citation_check(
        "证据显示 fee:F999 被重复计费，见 EV-DEADBEEF。", OBS, None)
    assert "fee:F999" in res["unmatched_hard"]
    assert "EV-DEADBEEF" in res["unmatched_hard"]


def test_fabricated_rule_id_is_caught(agent):
    res = agent._answer_citation_check(
        "按规则 R-WAIT-99 应收 12.00 元。", OBS, None)
    assert "R-WAIT-99" in res["unmatched_hard"]


def test_real_rule_id_from_analysis_passes(agent):
    analysis = {"findings": [{"rule_id": "R-WAIT-01", "expected": 12.0}],
                "breakdown": [{"fee_id": "F003", "actual_amount": 25.0}],
                "evidence": [], "waiting_rule": {}}
    res = agent._answer_citation_check(
        "R-WAIT-01 判定的应计为 12.00 元，F003 实收 25.00 元。", OBS, analysis)
    assert res["unmatched_hard"] == []


def test_money_is_soft_not_hard(agent):
    """合计/差额是合法派生值：不判 fail，只进 soft_unmatched。"""
    res = agent._answer_citation_check(
        "预估 480.00 元、实收 518.00 元，差额 +38.00 元。", OBS, None)
    assert res["unmatched_hard"] == []
    assert res["soft_unmatched"], "派生金额应被列出供人工复核"


def test_empty_answer_is_noop(agent):
    res = agent._answer_citation_check("", OBS, None)
    assert res["checked"] == 0


def test_final_step_carries_citation_check(agent):
    """final 步的 checks 里必须出现「结论硬引用可溯源」行。"""
    agent.observations = list(OBS)
    step = agent._final_step(
        {"action": "final", "answer": "费用项 fee:F003 实收 25.00 元。"},
        agent.observations, 1, None)
    labels = [c["label"] for c in step["checks"]]
    assert any("硬引用可溯源" in lb for lb in labels)
    row = next(c for c in step["checks"] if "硬引用可溯源" in c["label"])
    assert row["pass"] is True


def test_final_step_flags_fabricated_citation(agent):
    agent.observations = list(OBS)
    step = agent._final_step(
        {"action": "final", "answer": "费用项 fee:F999 实收 25.00 元。"},
        agent.observations, 1, None)
    row = next(c for c in step["checks"] if "硬引用可溯源" in c["label"])
    assert row["pass"] is False
    assert "fee:F999" in row["actual"]
