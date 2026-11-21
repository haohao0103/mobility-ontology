"""Worksheet/Workflow、授权引擎骨架与 Ontology Manager UI 测试（产品化层补齐）。"""
from __future__ import annotations

import os

import pytest

from enhancements.workflow import WorkflowEngine, WorkflowError


@pytest.fixture()
def env(system, enhanced):
    return system, enhanced


class TestWorksheetWorkflow:
    def test_worksheet_from_ontology(self, env):
        _, enh = env
        assert any(w["id"] == "ws-disputes" for w in enh.workflow.worksheets())
        rendered = enh.workflow.render_worksheet("ws-disputes")
        assert rendered["view"] == "table"
        assert rendered["objects"]
        assert all(set(o["props"]) <= {"fee_type", "actual_amount", "status"}
                   for o in rendered["objects"])

    def test_workflow_until_approval(self, env):
        _, enh = env
        assert any(w["id"] == "wf-dispute-review" for w in enh.workflow.workflows())
        out = enh.workflow.start("wf-dispute-review", "WF-RUN-1",
                                 {"actor": "manager", "role": "shipper"})
        assert out["status"] == "WAITING"
        detail = enh.workflow.run_detail("WF-RUN-1")
        assert [s["step_type"] for s in detail["steps"]] == ["query", "approval"]
        assert detail["steps"][1]["status"] == "AWAITING_APPROVAL"

    def test_approval_resumes_and_completes(self, env):
        _, enh = env
        enh.workflow.start("wf-dispute-review", "WF-RUN-2",
                           {"actor": "manager", "role": "shipper"})
        out = enh.workflow.approve("WF-RUN-2", "finance-lead")
        assert out["status"] == "COMPLETED"
        detail = enh.workflow.run_detail("WF-RUN-2")
        assert [s["step_type"] for s in detail["steps"]] == ["query", "approval", "action"]
        assert detail["steps"][2]["status"] == "DONE"

    def test_unknown_workflow(self, env):
        _, enh = env
        with pytest.raises(WorkflowError):
            enh.workflow.start("no-such", "X")


class TestAuthzEngines:
    def test_catalog(self, env):
        _, enh = env
        kinds = {e["kind"] for e in enh.authz.catalog()}
        assert {"policy", "openfga", "opa", "cedar"} <= kinds

    def test_policy_engine(self, env):
        _, enh = env
        out = enh.authz.check("policy",
                              {"user": "alice", "roles": []}, "read",
                              {"type": "FeeItem", "props": {"fee_type": "base"},
                               "requires_marking": "FINANCE"})
        assert out["decision"] == "DENY"
        ok = enh.authz.check("policy",
                             {"user": "alice", "roles": ["FINANCE"]}, "read",
                             {"type": "FeeItem", "props": {"fee_type": "base"}})
        assert ok["decision"] == "ALLOW"

    def test_external_engine_skeleton(self, env):
        _, enh = env
        out = enh.authz.check("openfga", {"user": "a"}, "read", {"type": "FeeItem"})
        assert out["decision"] == "DENY"
        assert out["reason"] == "ENGINE_NOT_CONFIGURED"


class TestManagerUI:
    def test_page_exists_with_tabs(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "web", "ontology-manager.html")
        assert os.path.exists(path)
        text = open(path, encoding="utf-8").read()
        for token in ("血缘", "物化", "变更与分支", "工作台与流程", "授权引擎",
                      "/enh/lineage/property/", "/enh/storage/v2",
                      "/enh/workflows/", "/enh/authz/engines"):
            assert token in text
