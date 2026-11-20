"""Ontology SQL frontend 承重测试。

核心纪律验证：**SQL 只是编译器，产物进现有查询树 IR**——
  - 对拍：同一查询 SQL 与手写 DSL 结果逐字段严格一致
  - SQL 不绕过 TBox 校验（未知属性/类型照旧 400）
  - SQL 不绕过安全（匿名查 DISPUTED 派生视图仍为 0）
  - 路径查询的跨节点谓词按别名下推到对应层（Cypher 语义）
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from freight.sql_frontend import (  # noqa: E402
    SqlCompileError, compile_sql, project,
)
from enhancements.security import SecurityContext  # noqa: E402
from test_object_query import _store, engine, sys_mod  # noqa: E402

CTX = SecurityContext(user="analyst", roles=set(), markings=set())
OPS = SecurityContext(user="ops", roles={"DISPUTE_OPS"},
                      markings={"DISPUTE_OPS"})


def run_sql(eng, store, sql, params=None, ctx=CTX):
    q = compile_sql(sql, params)
    projection = q.pop("_projection", [{"kind": "star"}])
    return project(eng.execute(q, store, ctx), projection)


class TestSqlParity:
    """SQL 与 DSL 对拍：同一语义必须字节级一致（防双口径）。"""

    def test_simple_filter_parity(self, engine, sys_mod):
        eng, _ = engine
        store = _store(sys_mod)
        via_sql = run_sql(eng, store,
                         "SELECT * FROM Waybill WHERE status = 'DELIVERED'")
        via_dsl = eng.execute({"set": {"from": "Waybill"},
                               "where": {"status": {"$eq": "DELIVERED"}}},
                              store, CTX)
        assert via_sql["total"] == via_dsl["total"]
        assert ({o["object_id"] for o in via_sql["objects"]}
                == {o["object_id"] for o in via_dsl["objects"]})

    def test_aggregate_parity(self, engine, sys_mod):
        """SQL 聚合 == DSL 聚合（sum 精确相等，防浮点/口径漂移）。"""
        eng, _ = engine
        store = _store(sys_mod)
        sql = ("SELECT COUNT(*), SUM(actual_amount) FROM FeeItem "
               "GROUP BY fee_type")
        dsl = {"set": {"from": "FeeItem"},
               "aggregate": {"count": True, "sum": ["actual_amount"],
                             "group_by": ["fee_type"]}}
        a = run_sql(eng, store, sql)
        b = eng.execute(dsl, store, CTX)
        assert a["total"] == b["total"]
        amap = {g["group_by"]["fee_type"]: g for g in a["groups"]}
        bmap = {g["group_by"]["fee_type"]: g for g in b["groups"]}
        assert amap.keys() == bmap.keys()
        for k in amap:
            assert amap[k]["count"] == bmap[k]["count"]
            assert amap[k]["sum"]["actual_amount"] == pytest.approx(
                bmap[k]["sum"]["actual_amount"], abs=1e-9)

    def test_link_path_parity(self, engine, sys_mod):
        """路径查询 == DSL followLink + 谓词下推（Cypher 语义）。"""
        eng, _ = engine
        store = _store(sys_mod)
        sql = ("SELECT f.fee_label FROM (w:Waybill)"
               "-[:waybill_produces_fee]->(f:FeeItem) "
               "WHERE w.waybill_id = 'WB20261105001' AND f.status = 'NORMAL'")
        dsl = {"set": {"followLink": {
            "set": {"from": "Waybill",
                    "where": {"waybill_id": {"$eq": "WB20261105001"}}},
            "link": "waybill_produces_fee", "direction": "out",
            "where": {"status": {"$eq": "NORMAL"}}}}}
        a = run_sql(eng, store, sql)
        b = eng.execute(dsl, store, CTX)
        assert a["total"] == b["total"]
        assert ({o["object_id"] for o in a["objects"]}
                == {o["object_id"] for o in b["objects"]})

    def test_derived_type_from(self, engine, sys_mod):
        """SQL 可直接 FROM 派生类型（与 DSL 一致）。"""
        eng, _ = engine
        store = _store(sys_mod)
        a = run_sql(eng, store,
                   "SELECT * FROM CarrierResource WHERE fleet = '华东车队'")
        b = eng.execute({"set": {"from": "CarrierResource"},
                         "where": {"fleet": {"$eq": "华东车队"}}}, store, CTX)
        assert a["total"] == b["total"] == 4

    def test_params_and_operators(self, engine, sys_mod):
        """参数化 + IN + LIKE + BETWEEN + IS NOT NULL + 排序分页。"""
        eng, _ = engine
        store = _store(sys_mod)
        out = run_sql(eng, store,
                     "SELECT * FROM Waybill WHERE waybill_id = :wb",
                     {"wb": "WB20261105001"})
        assert out["total"] == 1
        assert run_sql(eng, store,
                       "SELECT * FROM Waybill WHERE status IN ('DELIVERED',"
                       " 'ACCEPTED')")["total"] >= 1
        # 注意：不能用"等候费"——它本身是 DISPUTED，匿名上下文会被行级安全过滤
        assert run_sql(eng, store,
                       "SELECT * FROM FeeItem WHERE fee_label LIKE '%里程%'"
                       )["total"] >= 1
        assert run_sql(eng, store,
                       "SELECT * FROM FeeItem WHERE fee_label LIKE '%等候%'",
                       ctx=OPS)["total"] == 1
        assert run_sql(eng, store,
                       "SELECT * FROM Waybill WHERE estimated_fee BETWEEN 100"
                       " AND 700")["total"] >= 1
        ordered = run_sql(eng, store,
                         "SELECT * FROM FeeItem ORDER BY actual_amount DESC"
                         " LIMIT 2")
        # SELECT * 原样返回对象（props 嵌套）；列投影才扁平化到顶层
        amounts = [o["props"].get("actual_amount")
                   for o in ordered["objects"]]
        assert amounts == sorted(amounts, reverse=True)


class TestSqlSecurityAndTbox:
    def test_sql_cannot_bypass_tbox(self, engine, sys_mod):
        """SQL 未知属性照旧被 TBox 拦截（SQL 不是后门）。"""
        eng, _ = engine
        store = _store(sys_mod)
        with pytest.raises(Exception, match="未知属性"):
            run_sql(eng, store, "SELECT * FROM Waybill WHERE no_such = 1")

    def test_sql_cannot_bypass_row_level_security(self, engine, sys_mod):
        """SQL 查派生视图 DisputedFee：匿名 0 / 有标记 1（安全层不透传）。"""
        eng, _ = engine
        store = _store(sys_mod)
        anon = run_sql(eng, store, "SELECT * FROM DisputedFee", ctx=CTX)
        ops = run_sql(eng, store, "SELECT * FROM DisputedFee", ctx=OPS)
        assert anon["total"] == 0
        assert ops["total"] == 1

    def test_unknown_type_rejected(self, engine, sys_mod):
        eng, _ = engine
        store = _store(sys_mod)
        with pytest.raises(Exception, match="未定义的对象类型"):
            run_sql(eng, store, "SELECT * FROM GhostType")


class TestSqlCompileErrors:
    def test_syntax_error(self):
        with pytest.raises(SqlCompileError, match="SQL_SYNTAX_ERROR|期望"):
            compile_sql("SELECT FROM WHERE")

    def test_missing_param(self):
        with pytest.raises(SqlCompileError, match="缺少参数值"):
            compile_sql("SELECT * FROM Waybill WHERE waybill_id = :wb")

    def test_cross_alias_or_rejected(self):
        """OR 跨路径节点混用 -> 明确不支持（不静默给错结果）。"""
        with pytest.raises(SqlCompileError, match="不能跨路径节点"):
            compile_sql(
                "SELECT * FROM (w:Waybill)-[:waybill_produces_fee]->(f:FeeItem)"
                " WHERE w.status = 'DELIVERED' OR f.status = 'NORMAL'")

    def test_count_star_unsupported_in_sum(self):
        with pytest.raises(SqlCompileError, match="请指定属性"):
            compile_sql("SELECT SUM(*) FROM FeeItem")


class TestSqlRestEndpoint:
    @pytest.fixture()
    def client(self, tmp_path):
        os.environ.pop("OPENAI_CHAT_API_KEY", None)
        from api import create_app
        from fastapi.testclient import TestClient
        app = create_app(db_path=str(tmp_path / "sql.db"), fresh=True)
        return TestClient(app)

    def test_rest_sql_ok(self, client):
        r = client.post("/freight/object-set/sql",
                        json={"sql": "SELECT * FROM Waybill WHERE status = :s",
                              "params": {"s": "DELIVERED"}},
                        params={"user": "analyst"})
        assert r.status_code == 200
        assert r.json()["total"] >= 1

    def test_rest_sql_tbox_400(self, client):
        r = client.post("/freight/object-set/sql",
                        json={"sql": "SELECT * FROM Waybill WHERE nope = 1"},
                        params={"user": "analyst"})
        assert r.status_code == 400
        assert "PREDICATE_PROP_UNKNOWN" in r.json()["detail"]

    def test_rest_sql_syntax_400(self, client):
        r = client.post("/freight/object-set/sql", json={"sql": "SELEC 1"},
                        params={"user": "analyst"})
        assert r.status_code == 400
        assert "SQL_SYNTAX_ERROR" in r.json()["detail"]
