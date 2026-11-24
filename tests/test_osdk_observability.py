"""OSDK Java/GraphQL 生成 + Agent 工具观测/评测集测试（P-11 / P-12 补齐）。"""
from __future__ import annotations

import pytest


class TestOSDKTargets:
    def test_java_generation(self, system, enhanced):
        from tools.gen_osdk import generate_java
        code = generate_java(system.sem, enhanced.ext)
        assert "package com.mobility.ontology.generated;" in code
        assert "public class Waybill {" in code
        assert "BigDecimal" in code
        assert "List<FeeItem>" in code

    def test_graphql_generation(self, system, enhanced):
        from tools.gen_osdk import generate_graphql
        sdl = generate_graphql(system.sem, enhanced.ext)
        assert "scalar Decimal" in sdl
        assert "type Waybill {" in sdl
        assert "waybill(objectId: ID!): Waybill" in sdl
        assert "type Query {" in sdl

    def test_rest_targets(self, system):
        from fastapi.testclient import TestClient
        from api import create_app
        import os
        import tempfile

        os.environ.pop("OPENAI_CHAT_API_KEY", None)
        # 同 test_new_api_smoke：/enh 绑 freight 模块单例，注入 tmp 库隔离
        #（不注入会与本地运行的服务抢 runs/freight_facts.db → locked）。
        import enhancements.api as ea
        import freight.api as fa

        old_freight, old_enh = fa._SYSTEM, ea._SYSTEM
        try:
            td = tempfile.mkdtemp()
            app = create_app(
                db_path=os.path.join(td, "f.db"), fresh=True,
                freight_db_path=os.path.join(td, "freight.db"))
            client = TestClient(app)
            for target, needle in (("java", "public class Waybill"),
                                   ("graphql", "type Waybill")):
                r = client.get(f"/enh/osdk?target={target}")
                assert r.status_code == 200
                assert needle in r.json()["code"]
        finally:
            fa._SYSTEM, ea._SYSTEM = old_freight, old_enh


class TestObservability:
    def test_invocations_recorded(self, system):
        before = system.observability.stats()["total"]
        system.mcp_call("ontology.catalog", {})
        after = system.observability.stats()
        assert after["total"] == before + 1
        assert "ontology.catalog" in after["by_tool"]
        assert after["by_tool"]["ontology.catalog"]["calls"] >= 1

    def test_error_recorded(self, system):
        with pytest.raises(Exception):
            system.mcp_call("no.such.tool", {})
        recent = system.observability.recent(1)
        assert recent[0]["status"] == "ERROR"
        assert "no.such.tool" == recent[0]["tool"]

    def test_evalset(self, system):
        from freight.observability import list_evalset, run_eval
        samples = list_evalset()
        assert len(samples) >= 8
        out = run_eval(lambda tool, args: system.mcp_call(tool, args))
        assert out["samples"] == len(samples)
        assert "object_location" in out["by_kind"]
        assert out["passed"] >= 1, "至少应有一个样本通过"
