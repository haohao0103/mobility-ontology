"""推理链的降级路径：演示与线上都不能因为模型不给力就整链 503。

覆盖三件事：
1. 模型输出 final JSON 时动作名不规范 → 仍然收下（宽松解析）；
2. 工具预算用尽且模型没给合法 final → 降级出"初步结论"而不是报错；
3. 大模型连续失败 → 降级出"初步结论"而不是 503。
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from freight import agent as agent_mod  # noqa: E402
from freight.agent import LLMError, ReasoningAgent  # noqa: E402
from freight.engine import FreightSystem  # noqa: E402


@pytest.fixture()
def system():
    td = tempfile.mkdtemp(prefix="degrade_")
    sys_ = FreightSystem(os.path.join(td, "facts.db"), fresh=True, seed=True)
    yield sys_
    sys_.conn.close()


def _agent(system) -> ReasoningAgent:
    agent = ReasoningAgent(system, "waybill:WB20261105001", max_steps=4)
    agent.start()
    return agent


class TestLooseParsing:
    def test_final_with_unknown_action_name_accepted(self):
        obj = agent_mod.ReasoningAgent._parse_action(
            '{"action": "conclude", "answer": "结论在此"}')
        assert obj is not None and obj["action"] == "final"
        assert obj["answer"] == "结论在此"

    def test_final_without_action_name_accepted(self):
        obj = agent_mod.ReasoningAgent._parse_action('{"answer": "只有结论字段"}')
        assert obj is not None and obj["action"] == "final"

    def test_json_with_leading_text_accepted(self):
        obj = agent_mod.ReasoningAgent._parse_action(
            '好的，我的结论是：{"action": "final", "answer": "差 38 元"}')
        assert obj is not None and obj["answer"] == "差 38 元"

    def test_tool_action_still_parsed(self):
        obj = agent_mod.ReasoningAgent._parse_action(
            '{"action": "tool", "tool": "ontology.query"}')
        assert obj is not None and obj["action"] == "tool"


class TestDegradedConclusion:
    def test_degraded_final_uses_observations(self, system):
        agent = _agent(system)
        agent.observations.append({
            "tool": "ontology.query", "rows": 1,
            "result": {"total": 7, "actual": 518.0},
        })
        out = agent._degraded_final("")
        assert out["action"] == "final"
        assert out["degraded"] is True
        assert "初步结论" in out["answer"]
        assert "ontology.query" in out["answer"]

    def test_llm_failure_degrades_instead_of_raising(self, system, monkeypatch):
        agent = _agent(system)
        agent.observations.append({"tool": "ontology.query", "rows": 1,
                                  "result": {"total": 3}})

        def boom(*args, **kwargs):
            raise LLMError("大模型网关不可用: 模拟故障")

        monkeypatch.setattr(agent.client, "chat", boom)
        agent.pending = "plan"
        result = agent.next()
        assert result["finished"] is True
        assert result["agent"]["degraded"] is True
        assert result["agent"]["error"]
        steps = result["steps"]
        assert steps and steps[-1]["answer"]

    def test_non_llm_error_still_raises(self, system, monkeypatch):
        """非 LLM 故障不能被降级吞掉——那是真 bug，必须暴露。"""
        agent = _agent(system)
        agent.pending = "plan"

        def boom(*args, **kwargs):
            raise ValueError("不是 LLM 的错误")

        monkeypatch.setattr(agent.client, "chat", boom)
        with pytest.raises(ValueError):
            agent.next()
