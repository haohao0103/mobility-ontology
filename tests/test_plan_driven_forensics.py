"""规划驱动取证：规划不再只是喂给模型的背景板。

覆盖四件事：
1. 规划产出结构化任务/假设清单，模型缺省时确定性兜底（清单永远非空）；
2. 执行阶段任务对照：服务任务的调用打标，计划 vs 执行逐条可对照；
3. 假设裁定：每条假设给 成立/排除/待补证，证据来自确定性检查而非模型措辞；
4. 收口三件套进结论步骤：假设核对 / 计划执行对照 / 角色出口 / 未决项。
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from freight.agent import LLMError, ReasoningAgent  # noqa: E402
from freight.engine import FreightSystem  # noqa: E402


@pytest.fixture()
def system():
    td = tempfile.mkdtemp(prefix="plan_driven_")
    sys_ = FreightSystem(os.path.join(td, "facts.db"), fresh=True, seed=True)
    yield sys_
    sys_.conn.close()


def _agent(system) -> ReasoningAgent:
    agent = ReasoningAgent(system, "waybill:WB20261105001", max_steps=4)
    agent.start()
    return agent


def _apply_plan(agent: ReasoningAgent, planned: dict) -> None:
    """离线注入规划结果（绕过 LLM），等价于 _step_plan 完成后的状态。"""
    agent.planned = planned
    agent.pending = "tbox"


def _offline_plan(agent: ReasoningAgent, raw_plan: str) -> None:
    """用固定的模型输出跑规划步骤（不触网）。"""

    def canned(*args, **kwargs):
        return raw_plan

    agent.client.chat = canned
    agent._step_plan()


class TestNormalizeTasks:
    def test_fallback_never_empty_and_tools_legal(self, system):
        agent = _agent(system)
        tasks = agent._normalize_tasks(None, ["freight"])
        assert tasks, "任务清单缺省时必须确定性兜底，不能为空"
        assert all(t["tool_hint"] in agent.catalog for t in tasks)
        assert all(t["intent"] and t["target"] for t in tasks)

    def test_illegal_tool_hint_dropped(self, system):
        agent = _agent(system)
        legal = next(iter(agent.catalog))
        tasks = agent._normalize_tasks([
            {"intent": "查报价", "tool_hint": "function.quote_reconcile",
             "target": "waybill:WB20261105001", "why": "核对三口径"},
            {"intent": "瞎调", "tool_hint": "not.a.tool"},
            {"intent": "", "tool_hint": legal},
            "不是字典的条目",
        ], ["freight"])
        assert [t["tool_hint"] for t in tasks] == ["function.quote_reconcile"]

    def test_duplicate_tool_kept_once(self, system):
        agent = _agent(system)
        tasks = agent._normalize_tasks([
            {"intent": "第一次", "tool_hint": "function.fee_breakdown"},
            {"intent": "第二次", "tool_hint": "function.fee_breakdown"},
        ], ["freight"])
        assert len(tasks) == 1 and tasks[0]["intent"] == "第一次"


class TestNormalizeHypotheses:
    def test_fallback_is_standard_three(self, system):
        agent = _agent(system)
        hypos = agent._normalize_hypotheses(None)
        assert [h["id"] for h in hypos] == ["H1", "H2", "H3"]
        assert all(h["claim"] and h["verify_by"] for h in hypos)

    def test_model_hypotheses_kept_with_ids(self, system):
        agent = _agent(system)
        hypos = agent._normalize_hypotheses([
            {"claim": "高速费被重复计", "verify_by": "fee_breakdown"},
            {"claim": ""},
            {"id": "HX", "claim": "证据缺失"},
        ])
        assert len(hypos) == 2
        assert hypos[0]["id"] == "H1"  # 无 id 时按序号补
        assert hypos[1]["id"] == "HX"


class TestPlanStepQuality:
    def test_plan_step_has_trim_rate_and_datasets(self, system):
        agent = _agent(system)
        agent._step_catalog()
        raw = (
            '{"action":"plan","types":["Waybill"],'
            '"fee_types":["loading","highway"],"focus_fee_types":["loading"],'
            '"tasks":[{"intent":"核对费率口径","tool_hint":"function.fee_breakdown",'
            '"target":"waybill:WB20261105001","why":"定位差异"}],'
            '"hypotheses":[{"id":"H1","claim":"差异来自费率口径",'
            '"verify_by":"fee_breakdown 的 rule_delta"}]}'
        )
        _offline_plan(agent, raw)
        assert agent.planned["focus_fee_types"] == ["loading"]
        step = agent.steps[-1]
        facts = {f["label"]: f["value"] for f in step["facts"]}
        assert "裁剪率" in facts.get("取证重点", ""), "规划质量必须可度量（裁剪率）"
        assert facts.get("取证任务") == "1 条"
        assert facts.get("待验证假设") == "1 条"
        checks = {c["label"]: c["pass"] for c in step["checks"]}
        assert checks.get("取证重点非空") is True
        assert checks.get("取证重点已裁剪（不是全部费用类型）") is True
        captions = [d["caption"] for d in step["data"]["datasets"]]
        assert any("任务" in c for c in captions)
        assert any("假设" in c for c in captions)

    def test_llm_focus_filtered_to_pricing(self, system):
        """模型编造不存在的费用类型时，必须被过滤并回退到确定性焦点。"""
        agent = _agent(system)
        agent._step_catalog()
        raw = (
            '{"action":"plan","types":["Waybill"],'
            '"fee_types":["loading"],"focus_fee_types":["不存在的类型"]}'
        )
        _offline_plan(agent, raw)
        catalog_fees = set(agent.sem.pricing)
        assert set(agent.planned["focus_fee_types"]) <= catalog_fees
        assert agent.planned["focus_fee_types"], "过滤后必须仍有焦点"


class TestTaskTracking:
    def test_unexecuted_and_served_and_out_of_plan(self, system):
        agent = _agent(system)
        _apply_plan(agent, {
            "types": [], "fee_types": [], "focus_fee_types": [],
            "focus_reason": "",
            "tasks": [
                {"intent": "核对费率", "tool_hint": "function.fee_breakdown",
                 "target": "x", "why": "w"},
                {"intent": "核对报价", "tool_hint": "function.quote_reconcile",
                 "target": "x", "why": "w"},
            ],
            "hypotheses": [],
        })
        rows = agent._plan_execution_review()
        assert [r["status"] for r in rows] == ["未执行", "未执行"]
        assert all(r["reason"] != "—" for r in rows)

        agent.observations.append({
            "tool": "function.fee_breakdown", "rows": 3, "serves": "核对费率",
            "result": {},
        })
        rows = agent._plan_execution_review()
        assert rows[0]["status"] == "已执行"
        assert "3 行" in rows[0]["evidence"]

        # 同一工具被再次调用但未打标 → 计划外执行，不能冒充已执行
        agent.observations.append({
            "tool": "function.quote_reconcile", "rows": 1, "result": {},
        })
        rows = agent._plan_execution_review()
        assert rows[1]["status"] == "计划外执行"

    def test_task_for_tool_marks_first_uncovered(self, system):
        agent = _agent(system)
        _apply_plan(agent, {
            "types": [], "fee_types": [], "focus_fee_types": [],
            "focus_reason": "",
            "tasks": [
                {"intent": "核对费率", "tool_hint": "function.fee_breakdown",
                 "target": "x", "why": "w"},
            ],
            "hypotheses": [],
        })
        assert agent._task_for_tool("function.fee_breakdown")["intent"] == "核对费率"
        agent.observations.append({
            "tool": "function.fee_breakdown", "rows": 1, "serves": "核对费率",
            "result": {},
        })
        assert agent._task_for_tool("function.fee_breakdown") is None

    def test_reminder_replaces_not_accumulates(self, system):
        agent = _agent(system)
        _apply_plan(agent, {
            "types": [], "fee_types": [], "focus_fee_types": [],
            "focus_reason": "",
            "tasks": [
                {"intent": "核对费率", "tool_hint": "function.fee_breakdown",
                 "target": "x", "why": "w"},
            ],
            "hypotheses": [],
        })
        agent._sync_task_reminder()
        agent._sync_task_reminder()
        tagged = [m for m in agent.messages
                  if str(m.get("content") or "").startswith(agent.TASK_REMINDER_TAG)]
        assert len(tagged) == 1, "任务提醒必须替换上一条，不能在上下文里累积"


class TestHypothesisVerdicts:
    def test_every_hypothesis_gets_verdict(self, system):
        agent = _agent(system)
        _apply_plan(agent, {
            "types": [], "fee_types": [], "focus_fee_types": [],
            "focus_reason": "", "tasks": [],
            "hypotheses": agent._normalize_hypotheses(None),
        })
        rows = agent._hypothesis_verdicts()
        assert len(rows) == 3
        assert all(r["verdict"] in {"成立", "排除", "待补证"} for r in rows)
        for r in rows:
            if r["verdict"] == "成立":
                assert r["evidence"], "裁定成立必须给证据"

    def test_verdicts_surface_in_final_step(self, system):
        agent = _agent(system)
        _apply_plan(agent, {
            "types": [], "fee_types": [], "focus_fee_types": [],
            "focus_reason": "",
            "tasks": [
                {"intent": "核对费率", "tool_hint": "function.fee_breakdown",
                 "target": "x", "why": "w"},
            ],
            "hypotheses": agent._normalize_hypotheses(None),
        })
        step = agent._final_step(
            {"action": "final", "title": "结论", "question": "q",
             "thought": "t", "answer": "a"},
            observations=[], index=len(agent.steps) + 1,
            error=None,
        )
        captions = [d["caption"] for d in step["data"]["datasets"]]
        assert any("假设核对" in c for c in captions)
        assert any("计划执行对照" in c or "取证任务" in c for c in captions)
        labels = {f["label"]: f["value"] for f in step["facts"]}
        assert "假设裁定" in labels
        assert "未决项" in labels
        assert any("假设都有裁定" in c["label"] for c in step["checks"])


class TestRoleExportsAndOpenItems:
    def test_role_exports_from_action_plan(self, system):
        agent = _agent(system)
        rows = agent._role_exports()
        assert all(r["role"] and r["action"] for r in rows)
        assert all(r["target"] for r in rows)

    def test_open_items_covers_unexecuted_tasks(self, system):
        agent = _agent(system)
        _apply_plan(agent, {
            "types": [], "fee_types": [], "focus_fee_types": [],
            "focus_reason": "",
            "tasks": [
                {"intent": "核对费率", "tool_hint": "function.fee_breakdown",
                 "target": "x", "why": "w"},
            ],
            "hypotheses": [],
        })
        items = agent._open_items()
        assert any("核对费率" in i["missing"] for i in items)
        assert all(i["owner"] and i["system"] and i["blocks"] for i in items)


class TestReviewInstruction:
    def test_review_requires_hypothesis_responses(self, system, monkeypatch):
        agent = _agent(system)
        _apply_plan(agent, {
            "types": [], "fee_types": [], "focus_fee_types": [],
            "focus_reason": "", "tasks": [],
            "hypotheses": agent._normalize_hypotheses(None),
        })
        captured = {}

        def canned(messages, **kwargs):
            captured["prompt"] = messages[-1]["content"]
            return ('{"action":"final","title":"结论","question":"q",'
                    '"thought":"t","answer":"a"}')

        monkeypatch.setattr(agent.client, "chat", canned)
        agent._step_review()
        assert "逐条回应" in captured["prompt"]
        assert "H1" in captured["prompt"] and "未决项" in captured["prompt"]
        assert agent.reviewed is True


class TestDegradedStepShape:
    def test_degraded_step_has_frontend_required_fields(self, system):
        """降级步骤必须带全前端 renderChainStep 依赖的字段，
        否则大模型瞬断时页面渲染直接崩（query.label 读取 undefined）。"""
        agent = _agent(system)
        step = agent._degraded_step(LLMError("模拟：大模型网关不可用"))
        for key in ("index", "title", "tag", "query", "data", "facts",
                    "checks", "judgment", "conclusion", "source", "basis"):
            assert key in step, f"降级步骤缺字段 {key}"
        assert step["query"]["label"]
        assert "模拟" in step["checks"][0]["actual"]

    def test_degraded_step_carries_closing_datasets(self, system):
        """降级也要收口：假设核对/计划执行对照必须还在——
        链没跑完时"全部待补证/全部未执行"本身就是诚实的收口。"""
        agent = _agent(system)
        step = agent._degraded_step(LLMError("模拟：大模型网关不可用"))
        caps = [d["caption"] for d in step["data"]["datasets"]]
        assert any("假设核对" in c for c in caps)
        assert any("计划执行对照" in c or "取证任务清单" in c for c in caps)
        rows = next(d["rows"] for d in step["data"]["datasets"]
                    if "假设核对" in d["caption"])
        # 裁定来自确定性检查：种子运单有 38 元差异，H1 应被裁定（非全待补证）
        assert all(r["verdict"] in {"成立", "排除", "待补证"} for r in rows)
        assert any(r["verdict"] in {"成立", "排除"} for r in rows)


# ── 总览阶段表：执行轨迹台账（不是售前示意图）────────────────────────────

def _replay(agent: ReasoningAgent, steps_spec: list[tuple]) -> None:
    """按真实链的 tag 序列离线重放执行轨迹（不触网）。

    步骤结构与真实一致：没有 who 字段——who 由 _summary_for_step
    从 llm / proposal / query.api 等字段派生。
    """
    for i, (tag, title, rows, extra) in enumerate(steps_spec):
        step = {"tag": tag, "title": title, "rows": rows,
                "index": i + 1, "tool": None}
        step.update(extra or {})
        agent.steps.append(step)


_FULL_RUN = [
    # (tag, title, rows, 触发 who 派生的真实字段)
    ("TBox · 紧凑目录", "目录装载", 0, {"query": {"scope": "catalog"}}),
    ("LLM · 本体规划", "大模型规划本体范围", 0, {"llm": {"elapsed_ms": 900}}),
    ("TBox · 问答前置", "本体定义查询", None, {
        "data": {"datasets": [{"rows": [{} for _ in range(12)]}]}}),
    ("LLM · 决策", "决策：费用拆解", 0, {"llm": {"elapsed_ms": 800}}),
    ("Agent · function.fee_breakdown", "费用拆解", 9, {
        "tool": "function.fee_breakdown",
        "query": {"api": "/freight/mcp/call · 计价系统",
                  "expression": "function.fee_breakdown({})"}}),
    ("LLM · 决策", "决策：差异对账", 0, {"llm": {"elapsed_ms": 700}}),
    ("Agent · function.quote_reconcile", "差异对账", 4, {
        "tool": "function.quote_reconcile",
        "query": {"api": "/freight/mcp/call · 结算系统",
                  "expression": "function.quote_reconcile({})"}}),
    ("LLM · 复核", "大模型复核结论", 0, {"llm": {"elapsed_ms": 1200}}),
    ("动作 · 权限 / 前置校验", "动作提案", 0, {"proposal": {}}),
    ("Agent · Final", "综合判断与结论", 0, None),
]


class TestStageSummaries:
    def _stages_by_id(self, agent):
        return {st["id"]: st for st in agent._stage_summaries()}

    def test_stage_text_stable_and_llm_decisions_in_facts(self, system):
        """阶段文案固定不被步骤覆盖；LLM 决策归「业务事实取证」，
        不再把复核/决策堆进「链路规划」——原来跑完后 02 会显示
        "大模型复核结论"，自相矛盾。"""
        agent = _agent(system)
        agent.planned = {
            "types": ["Waybill"],
            "fee_types": ["fuel", "toll", "base", "wait", "park"],
            "focus_fee_types": ["base", "wait"],
            "hypotheses": agent._normalize_hypotheses(None),
            "tasks": [],
        }
        _replay(agent, _FULL_RUN)
        agent.finished = True
        agent.pending = "done"
        stages = self._stages_by_id(agent)
        assert "取数计划" in stages["plan"]["action"]
        assert "复核" not in stages["plan"]["action"]
        facts = stages["facts"]
        assert facts["counts"]["llm"] == 2   # 两次决策都在取证循环
        assert facts["counts"]["tool"] == 2
        assert "大模型决策" in facts["who"]
        assert "裁剪率" in stages["plan"]["output"]
        assert "假设裁定" in stages["final"]["output"]
        # TBox 声明条数从 datasets 取（顶层无业务 rows），不再是"0 行声明"
        assert stages["tbox"]["counts"]["rows"] == 12
        assert "12 行声明" in stages["tbox"]["output"]
        # 骨架文案不再宣称"状态机"——describe 不返回独立状态机声明
        assert "状态机" not in stages["tbox"]["action"]

    def test_action_exit_stage_proposed(self, system):
        """动作出口阶段：有提案步骤时状态为「等人工确认」——
        动作从不自动执行，这是链末段业务语义（谁去补证/谁去退费）。"""
        agent = _agent(system)
        _replay(agent, _FULL_RUN)
        agent.finished = True
        agent.pending = "done"
        stages = self._stages_by_id(agent)
        assert stages["action"]["status"] == "proposed"
        assert "等人工确认" in stages["action"]["output"]
        assert "角色提案" in stages["action"]["output"]

    def test_degraded_maps_to_final_and_skips_unreached(self, system):
        """降级步骤映射到「结论汇总」并显式标注；链结束后没到的
        阶段标「未到达」；不再冒出第 7 行计划外阶段。"""
        agent = _agent(system)
        agent.steps.append({"tag": "TBox · 紧凑目录", "title": "目录装载",
                            "query": {"scope": "catalog"}, "index": 1})
        agent.steps.append(agent._degraded_step(LLMError("模拟网关瞬断")))
        agent.finished = True
        agent.pending = None
        stages = agent._stage_summaries()
        by_id = {st["id"]: st for st in stages}
        assert len(stages) == 6                      # 六段骨架，无计划外追加
        assert by_id["final"]["status"] == "degraded"
        assert "降级收口" in by_id["final"]["output"]
        assert by_id["plan"]["status"] == "skipped"
        assert by_id["facts"]["status"] == "skipped"
        assert by_id["catalog"]["status"] == "done"

    def test_current_follows_pending(self, system):
        """进行中阶段按真实 pending 映射：decide → 业务事实取证。"""
        agent = _agent(system)
        agent.pending = "decide"
        stages = self._stages_by_id(agent)
        assert stages["facts"]["status"] == "current"
        assert stages["plan"]["status"] == "pending"

    def test_participants_from_real_steps(self, system):
        """参与方按真实步骤聚合（带系统后缀的归并主干），
        不再按写死阶段文案去重——真实会出现 Agent → 人工。"""
        agent = _agent(system)
        _replay(agent, _FULL_RUN)
        agent.finished = True
        agent.pending = "done"
        participants = agent.result()["summary"]["participants"]
        assert "Agent → 人工" in participants
        assert "业务系统（ABox）" in participants       # · 计价/· 结算 归并
        assert "本体引擎（TBox）" in participants
