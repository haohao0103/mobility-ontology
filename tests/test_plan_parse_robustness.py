"""规划输出解析的标点鲁棒性（实测事故回归）。

背景：MiMo 在 HTTP 链路上稳定返回一份合法 JSON，但 reason 字段末尾
偶发全角逗号「，}」，`json.loads` 直接判废 → 规划步失败降级成
「工具预算用尽」。进程内跑同一问句不出现，曾被误判为网关偶发 503。
真正的差异是模型输出噪声，不是网络。
"""
import json

import pytest

from freight.agent import ReasoningAgent


def _parse(raw: str):
    return ReasoningAgent._parse_plan(raw)


def test_fullwidth_comma_before_closing_brace():
    """全角逗号结尾（事故原样：reason 后跟「，}」）必须解析成功。"""
    raw = ('{"types":["A","B"],"reason":"其余 fee_types 仅参与合计，'
           '不作重点取证。"，}')
    plan = _parse(raw)
    assert plan is not None
    assert plan["types"] == ["A", "B"]
    assert "不作重点" in plan["reason"]


def test_fullwidth_colon_and_quotes():
    raw = '{"types"：["A"]，“reason":"x"}'
    plan = _parse(raw)
    assert plan is not None
    assert plan["types"] == ["A"]
    assert plan["reason"] == "x"


def test_markdown_fence_with_fullwidth_punct():
    raw = '```json\n{"fee_types":["waiting"]，“focus_reason":"只有 waiting。"，}\n```'
    plan = _parse(raw)
    assert plan is not None
    assert plan["fee_types"] == ["waiting"]


def test_prose_before_json():
    raw = '好的，我按目录圈定范围：\n{"types":["StopWaiting"]}'
    plan = _parse(raw)
    assert plan is not None
    assert plan["types"] == ["StopWaiting"]


def test_plain_json_unchanged():
    raw = '{"types":["A"],"tasks":[],"hypotheses":[]}'
    plan = _parse(raw)
    assert plan is not None
    assert plan["types"] == ["A"]


def test_garbage_returns_none():
    assert _parse("今天天气不错") is None
    assert _parse("") is None
    assert _parse(None) is None
    # 类型不是列表的半份输出：解析器可能救出字典，但绝不能给出可用 plan
    # （types 不是 list → 调用方「没有合法类型」判废）
    plan = _parse('{"types": "not-a-list-but-dict"')
    assert not (isinstance(plan, dict) and isinstance(plan.get("types"), list))


def test_truncated_tail_field_is_salvaged():
    """截断只丢末尾字段（事故原样：reason 中间断）必须救出主体。"""
    raw = ('{"types":["A","B"],"fee_types":["waiting"],'
           '"reason":"问题包含两层：费用构成与差异原因。因此选取')
    plan = _parse(raw)
    assert plan is not None
    assert plan["types"] == ["A", "B"]
    assert plan["fee_types"] == ["waiting"]


def test_truncated_mid_array_is_salvaged():
    raw = '{"types":["A","B"],"tasks":[{"intent":"x","tool_hint":"tbox.describe"'
    plan = _parse(raw)
    assert plan is not None
    assert plan["types"] == ["A", "B"]


def test_truncated_before_any_field_still_rejected():
    """只截出半个键名、没有任何完整字段时仍然判废。"""
    assert _parse('{"ty') is None
    assert _parse('{"types"') is None


def test_types_fallback_when_model_outputs_illegal_types(tmp_path, monkeypatch):
    """零命中场景：模型圈定的类型全不在目录里 → 回退目录锚点并标注来源。

    实测事故：「今天天气怎么样」零命中，目录只剩 seed 聚合，模型仍输出
    一合法 JSON 但 types 全是目录外类型 → 旧逻辑整份判废，规划步降级
    成「工具预算用尽」。兜底后链路继续，结论是诚实的「本体无相关声明」。
    """
    import os

    from freight import agent as agent_mod
    from freight.engine import FreightSystem

    monkeypatch.setattr(
        agent_mod, "__file__", str(tmp_path / "freight" / "agent.py"))

    class _FakeClient:
        def chat(self, messages, **kw):
            return json.dumps({
                "types": ["Weather", "Forecast", "Temperature"],
                "fee_types": ["waiting"],
                "focus_fee_types": ["waiting"],
                "focus_reason": "无关",
                "tasks": [], "hypotheses": [], "reason": "模型幻觉",
            })

    system = FreightSystem(os.path.join(str(tmp_path), "facts.db"),
                           fresh=True, seed=False)
    agent = agent_mod.ReasoningAgent(
        system, "waybill:WB20261105001", 4, question="今天天气怎么样")
    agent._client = _FakeClient()
    plan = agent._plan_tbox()
    # 目录里没有 Weather/Forecast/Temperature，全部被过滤
    assert all(t in system.sem.classes for t in plan["types"])
    assert plan["types"], "回退后必须有类型，否则链路又断"
    assert plan["type_source"] == "catalog_anchors"
    assert not plan["_error"]


def test_debug_dump_written_on_failure(tmp_path, monkeypatch):
    """解析失败必须把模型原始输出落 runs/，诊断不能靠猜。"""
    import os
    import time

    from freight import agent as agent_mod
    from freight.engine import FreightSystem

    # 只改 agent 模块的 __file__，让 runs/ 根落到 tmp_path；
    # 不碰 os.path.dirname（会递归污染 os.makedirs）。
    monkeypatch.setattr(
        agent_mod, "__file__", str(tmp_path / "freight" / "agent.py"))
    monkeypatch.setattr(agent_mod.time, "strftime", lambda *a: "2026-11-24 00:00:00")

    class _FakeClient:
        def chat(self, messages, **kw):
            return "完全不是 JSON 的模型输出"

    system = FreightSystem(os.path.join(str(tmp_path), "facts.db"),
                           fresh=True, seed=False)
    agent = agent_mod.ReasoningAgent(
        system, "waybill:WB20261105001", 4, question="测试问句")
    agent._client = _FakeClient()
    with pytest.raises(agent_mod.LLMError):
        agent._plan_tbox()
    dump = tmp_path / "runs" / "plan_parse_failures.log"
    assert dump.exists()
    assert "完全不是 JSON" in dump.read_text(encoding="utf-8")
