"""后天演示就绪度测试：本体痕迹显性化 / 六幕叙事线 / LLM 预检 / 六卡 API 可用。"""
from __future__ import annotations

import os
import re
import tempfile

import pytest


@pytest.fixture(scope="module")
def demo_client():
    from fastapi.testclient import TestClient
    from api import create_app

    os.environ.pop("OPENAI_CHAT_API_KEY", None)
    app = create_app(db_path=os.path.join(tempfile.mkdtemp(), "demo.db"), fresh=True)
    return TestClient(app)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, "web", "order-ontology.html")


def page() -> str:
    with open(PAGE, encoding="utf-8") as fh:
        return fh.read()


class TestOntologySignalsVisible:
    def test_signal_renderer_and_default_placement(self):
        text = page()
        assert "function renderOntologySignals(step)" in text
        assert "${renderOntologySignals(step)}" in text
        assert "本体痕迹" in text
        assert "osig block" in text or ".osig.block" in text

    def test_guide_points_to_ontology_evidence(self):
        text = page()
        assert "先看「本体痕迹」" in text
        assert "命中了哪条规则" in text

    def test_signal_kinds_cover_demo_needs(self):
        text = page()
        for kind in ("class", "relation", "rule", "action", "pricing", "evidence"):
            assert f"{kind}:" in text


class TestStoryline:
    MAIN_IDS = ["bearing", "machine", "contracts", "security", "agent", "evidence"]
    DEEP_IDS = ["lineage", "governance", "query", "types"]

    def test_six_main_acts_exist(self):
        text = page()
        story = re.search(r"const DEMO_STORY = \[(.*?)\n\];", text, re.S)
        assert story is not None
        acts = re.findall(r"act: 'ACT (\d) / 6", story.group(1))
        assert acts == ["1", "2", "3", "4", "5", "6"]

    def test_story_scenarios_match_main_cards(self):
        text = page()
        story = re.search(r"const DEMO_STORY = \[(.*?)\n\];", text, re.S).group(1)
        scenario_ids = re.findall(r"scenario: '(\w+)'", story)
        assert scenario_ids == self.MAIN_IDS
        cards = text.split("const COMP_SCENARIOS = [")[1].split("function compScenario")[0]
        card_ids = re.findall(r"^    id: '(\w+)'", cards, re.M)
        for sid in scenario_ids:
            assert sid in card_ids

    def test_story_has_script_and_pointer(self):
        text = page()
        story = re.search(r"const DEMO_STORY = \[(.*?)\n\];", text, re.S).group(1)
        for field in ("hook:", "point:", "say:"):
            assert story.count(field) == 6, field

    def test_story_controls_wired(self):
        text = page()
        for token in ("id=\"demoStory\"", "id=\"demoNarration\"", "function gotoDemoAct",
                      "function bindDemoEvents", "data-demo=\"start\"",
                      "data-demo=\"next\"", "bindDemoEvents();"):
            assert token in text, token

    def test_scenarios_layered_six_main_four_deep(self):
        text = page()
        cards = text.split("const COMP_SCENARIOS = [")[1].split("function compScenario")[0]
        tiered = re.findall(r"^    id: '(\w+)', tier: '(main|deep)'", cards, re.M)
        assert [sid for sid, tier in tiered if tier == "main"] == self.MAIN_IDS
        assert [sid for sid, tier in tiered if tier == "deep"] == self.DEEP_IDS
        assert len(tiered) == 10

    def test_deep_toggle_and_dependency_wired(self):
        text = page()
        for token in ("compShowDeep", "s.tier || 'main') === 'main'", "comp-dependency",
                      "step.dependency", "声明依赖"):
            assert token in text, token


class TestLlmPreflight:
    def test_preflight_reports_consistent_state(self, demo_client):
        r = demo_client.get("/freight/agent/preflight")
        assert r.status_code == 200
        body = r.json()
        assert "available" in body and isinstance(body["available"], bool)
        if body["available"]:
            assert body.get("model")
            assert "可演示" in body["message"]
        else:
            assert "OPENAI_CHAT_API_KEY" in str(body.get("error", "")) or body.get("error")
            assert "确定性问答场景" in body["message"]

    def test_free_question_guarded_in_page(self):
        text = page()
        assert "ensureLlmReady" in text
        assert "if (!pre.available)" in text
        assert "讲解模式" in text

    def test_start_does_not_touch_llm(self, demo_client):
        r = demo_client.post(
            "/freight/waybills/waybill:WB20261105001/reasoning-chain/start")
        assert r.status_code == 200
        assert r.json().get("session_id")


class TestSixCardsApi:
    """六张卡依赖的确定性接口逐个探测，避免演示当天才发现某条链路挂了。"""

    def test_all_card_endpoints(self, demo_client):
        wb = "waybill:WB20261105001"
        endpoints = [
            f"/freight/objects/{wb}",
            "/freight/ontology/state-machines/Waybill",
            f"/freight/objects/{wb}/trace",
            f"/freight/objects/{wb}/impact",
            f"/freight/objects/{wb}/graph",
            f"/freight/waybills/{wb}/fee-analysis",
            "/freight/ontology",
            "/freight/ontology/guard-demo",
        ]
        failures = []
        for path in endpoints:
            r = demo_client.get(path)
            if r.status_code != 200:
                failures.append((path, r.status_code, str(r.json())[:120]))
        assert not failures, failures
