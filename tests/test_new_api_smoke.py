"""新增 REST 端点的进程内冒烟测试（不启动服务，不占用端口）。

覆盖本轮补齐的全部接口面；真实 STG 验收须按 AGENTS.md 在 stg-121:8765 执行。
"""
from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from api import create_app

    os.environ.pop("OPENAI_CHAT_API_KEY", None)
    # /enh/* 路由绑 freight.api 模块单例（默认共享库 runs/freight_facts.db）；
    # 不注入 freight_db_path 的话，测试会与本地运行中的服务抢同一个 sqlite
    # 文件 → database is locked。注入 tmp 库并在 teardown 恢复原单例。
    import enhancements.api as ea
    import freight.api as fa

    old_freight, old_enh = fa._SYSTEM, ea._SYSTEM
    td = tempfile.mkdtemp()
    app = create_app(
        db_path=os.path.join(td, "f.db"), fresh=True,
        freight_db_path=os.path.join(td, "freight.db"))
    with TestClient(app) as c:
        yield c
    fa._SYSTEM, ea._SYSTEM = old_freight, old_enh


class TestNewEndpoints:
    def test_storage_and_lineage(self, client):
        assert client.get("/enh/storage/v2").status_code == 200
        assert client.get("/enh/lineage/summary").status_code == 200
        assert client.get("/enh/lineage/graph").status_code == 200
        assert client.get("/enh/lineage/property/FeeItem/fee_type").status_code == 200

    def test_changesets_and_branches(self, client):
        import uuid
        csid = "API-CS-" + uuid.uuid4().hex[:8]
        assert client.post("/enh/changesets",
                           json={"id": csid, "label": "api smoke"}).status_code == 200
        assert client.get("/enh/changesets").status_code == 200
        assert client.get(f"/enh/changesets/{csid}").status_code == 200
        assert client.post("/enh/branches",
                           json={"name": "api-branch-" + uuid.uuid4().hex[:8]}).status_code == 200
        assert client.get("/enh/branches").status_code == 200

    def test_batch_edits_and_effects(self, client):
        assert client.get("/enh/effects").status_code == 200
        assert client.get("/enh/effects/log").status_code == 200
        assert client.post("/enh/edits",
                           json={"edits": [{"object_id": "fee:F001",
                                            "set": {"calc_basis": "api smoke"}}]}).status_code == 200
        assert client.post("/enh/actions/batch/plan",
                           json={"actions": [{"action_id": "dispute_charge",
                                              "object_id": "fee:F001"}]}).status_code == 200
        assert client.get("/enh/actions/batch/history").status_code == 200

    def test_simulation(self, client):
        r = client.post("/enh/simulate/whatif", json={
            "actions": [{"action_id": "dispute_charge", "object_id": "fee:F001",
                         "role": "shipper", "payload": {"reason": "api"}}]})
        assert r.status_code == 200
        assert r.json()["production_untouched"] is True

    def test_workflow_and_authz(self, client):
        assert client.get("/enh/worksheets").status_code == 200
        assert client.get("/enh/worksheets/ws-disputes").status_code == 200
        assert client.get("/enh/workflows").status_code == 200
        assert client.get("/enh/authz/engines").status_code == 200
        assert client.post("/enh/authz/check", json={
            "engine": "policy", "subject": {"user": "a", "roles": []},
            "action": "read", "resource": {"type": "FeeItem", "props": {}}}).status_code == 200

    def test_observability_and_eval(self, client):
        assert client.get("/enh/agent/observability").status_code == 200
        assert client.get("/enh/agent/invocations").status_code == 200
        assert client.get("/enh/agent/evalset").status_code == 200
        assert client.post("/enh/agent/eval").status_code == 200

    def test_osdk_targets(self, client):
        for target in ("typescript", "python", "java", "graphql", "openapi"):
            assert client.get(f"/enh/osdk?target={target}").status_code == 200

    def test_manager_page_served(self, client):
        r = client.get("/ontology-manager.html")
        assert r.status_code == 200
        assert "Ontology Manager" in r.text
