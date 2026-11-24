"""反「自欺欺人」守卫：推理轨迹必须由输入与本体声明产生，不得由常量产生。

覆盖三条曾被发现的缺陷：
1. 目录缩域被「当前对象所在域」锁死 → 任何问题都得到同一条 catalog 轨迹；
2. 分诊/假设/取证清单靠写死词表与固定剧本 → 降级结论与输入无关；
3. 前端固定通过态、本地伪数据、写死金额冒充实测结果。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from freight.agent import ReasoningAgent  # noqa: E402
from freight.answerability import classify_question  # noqa: E402
from freight.engine import FreightSystem  # noqa: E402

QUESTIONS = [
    "等候费是怎么计价的？免费多少分钟，超时后怎么算？",
    "司机资质和车辆类型怎么校验？",
    "发票和结算单是怎么关联的？",
    "运单状态机有哪些状态转移？谁能改派？",
]


@pytest.fixture()
def system():
    td = tempfile.mkdtemp(prefix="honesty_")
    sys_ = FreightSystem(os.path.join(td, "facts.db"), fresh=True, seed=True)
    yield sys_
    sys_.conn.close()


def _catalog(system, question):
    agent = ReasoningAgent(system, "waybill:WB20261105001", 6,
                           question=question)
    agent.start()
    agent._step_catalog()
    return agent, agent.steps[-1]["query"]["resolved"]


class TestQuestionDrivesScope:
    def test_different_questions_select_different_groups(self, system):
        groups = [tuple(_catalog(system, q)[1]["aggregates"]) for q in QUESTIONS]
        assert len(set(groups)) >= 3, f"注入聚合随问句变化，实际：{groups}"

    def test_scope_is_all_groups_not_current_object_group(self, system):
        for question in QUESTIONS:
            _, resolved = _catalog(system, question)
            assert resolved["search_scope"] == "all_contexts"
            assert not resolved["fallback_used"]
            # 必选对象聚合要显式列出来，不许混进「问句召回」里冒充检索结果
            assert resolved["seed_aggregates"]
            assert not (set(resolved["seed_aggregates"])
                        & set(resolved["question_aggregates"]))
            assert len(resolved["question_aggregates"]) >= 1
        # 问句追加聚合必须随输入变化，不能恒定
        assert len({
            tuple(_catalog(system, q)[1]["question_aggregates"]) for q in QUESTIONS
        }) >= 3

    def test_core_object_aggregate_never_trimmed_out(self, system):
        """回归：预算裁剪曾把核心域砍掉，目录变成 0 条关系，
        规划器随即输出无法解析 → 经典演示问题降级。"""
        agent, resolved = _catalog(
            system, "为什么这笔运单比预估贵38",
        )
        seed = agent.sem.aggregate_for_type(agent.object_type)
        assert seed in resolved["aggregates"]
        assert resolved["relations"] > 0, "目录必须带关系端点"
        # v4 承重：跨聚合边界链接必须随目录注入，永不静默丢弃
        assert resolved["boundary_links"] > 0, "边界链接必须注入"
        payload_types = {
            s for s in (agent.steps[-1]["query"]["output"] or {}).get("types") or []
        }
        assert {"Waybill", "FeeItem"} <= payload_types
        assert "all_groups" not in agent.steps[-1]["query"]["expression"]

    def test_search_expression_and_metadata_are_real(self, system):
        agent, resolved = _catalog(system, QUESTIONS[0])
        expression = agent.steps[-1]["query"]["expression"]
        assert "object_type_search(" in expression
        assert "all_groups" not in expression  # 没有虚构的调用参数
        assert resolved["question_source"] == "user"
        assert agent.steps[-1]["query"]["params"]["question_source"] == "user"

    def test_default_question_is_marked_not_passed_off_as_user_input(self, system):
        agent, resolved = _catalog(system, "")
        assert agent.question_is_default is True
        assert resolved["question_source"] == "default"
        assert agent.steps[-1]["query"]["params"]["question_source"] == "default"


class TestNoFixedScript:
    def test_fallback_hypotheses_depend_on_question(self, system):
        a = ReasoningAgent(system, "waybill:WB20261105001", 6,
                           question=QUESTIONS[0])
        b = ReasoningAgent(system, "waybill:WB20261105001", 6,
                           question=QUESTIONS[2])
        ha = [h["claim"] for h in a._normalize_hypotheses(None)]
        hb = [h["claim"] for h in b._normalize_hypotheses(None)]
        assert ha != hb
        assert all(h.get("check") for h in a._normalize_hypotheses(None))

    def test_fallback_tasks_marked_as_not_model_planned(self, system):
        agent = ReasoningAgent(system, "waybill:WB20261105001", 6,
                               question=QUESTIONS[0])
        tasks = agent._normalize_tasks(None, ["waiting"])
        assert tasks
        assert all("非模型规划" in t["why"] for t in tasks)
        assert all(t["tool_hint"] in agent.catalog for t in tasks)

    def test_declaration_hit_uses_ontology_terms_not_wordlist(self, system):
        agent = ReasoningAgent(system, "waybill:WB20261105001", 6,
                               question=QUESTIONS[0])
        assert not hasattr(ReasoningAgent, "_DECL_KEYS")
        assert "free_minutes" in agent._declared_param_keys()

    def test_classify_sourced_from_declarations(self, system):
        result = classify_question("计价", semantic=system.sem)
        assert result["signals"]["routing_source"].startswith(
            "semantic_layer.json")
        assert result["signals"]["declaration_terms"]


class TestHonestFrontend:
    def test_no_fixed_pass_verdict(self):
        text = (ROOT / "web" / "order-ontology.html").read_text(
            encoding="utf-8")
        for line in text.splitlines():
            if "compVerdict(" in line and "function compVerdict" not in line:
                assert ", true)" not in line, line.strip()
        assert "compPass(d" in text
        assert "判定依据：本次接口返回" in text

    def test_no_local_fake_measurement(self):
        text = (ROOT / "web" / "order-ontology.html").read_text(
            encoding="utf-8")
        assert "run: () => ({ bare:" not in text
        assert "'0.00 元（凭不上）'" not in text

    def test_walk_numbers_come_from_api(self):
        text = (ROOT / "web" / "order-ontology.html").read_text(
            encoding="utf-8")
        for key in ("variance", "waiting_actual", "highway_actual",
                    "waiting_minutes", "rule_expected", "fee_count"):
            assert f'data-fact-value="{key}"' in text
        assert 'data-ontology-value="waiting.free_minutes"' in text
        assert "hydrateWalkFacts()" in text
        assert "演示剧本" in text

    def test_profile_trace_marked_as_illustration(self):
        text = (ROOT / "web" / "profile.html").read_text(encoding="utf-8")
        assert "讲解用示意轨迹" in text
        assert "['修复前=BLOCK', '修复后=PASS'" not in text
        assert "依据（示意说明，非本体实时读取）" in text

    def test_no_hardcoded_evidence_source(self):
        text = (ROOT / "freight" / "agent.py").read_text(encoding="utf-8")
        assert 'source = "ETC"' not in text
