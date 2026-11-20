"""E1-E5 组合式 Object Set 查询承重测试。

对标 Palantir OSDK 组合语义：谓词/集合代数/followLink/聚合管道，
每项配承重验证（改本体 JSON -> 行为变、代码零改动）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile

import pytest

from freight.object_query import (
    ObjectQueryEngine, QueryError, validate_predicate, eval_predicate,
)


@pytest.fixture(scope="module")
def sys_mod():
    from freight.engine import FreightSystem
    td = tempfile.mkdtemp(prefix="oq_")
    yield FreightSystem(os.path.join(td, "f.db"), fresh=True, seed=True)


@pytest.fixture()
def engine(sys_mod, make_ext):
    from enhancements.runtime import EnhancedSystem
    enh = EnhancedSystem(sys_mod)
    return ObjectQueryEngine(sys_mod.sem, enh.ext, enh.security), enh


def _store(sys_mod):
    return sys_mod.facts.store


# ---------------------------------------------------------------- E1 谓词层

class TestPredicateOps:
    def test_eq_and_in(self, engine, sys_mod):
        eng, _ = engine
        out = eng.execute({"set": {"from": "FeeItem", "where": {
            "fee_type": {"$eq": "waiting"}}}}, _store(sys_mod))
        assert out["total"] >= 1
        assert all(o["props"]["fee_type"] == "waiting" for o in out["objects"])

    def test_gt_gte_lt_lte(self, engine, sys_mod):
        eng, _ = engine
        out = eng.execute({"set": {"from": "Waybill", "where": {
            "waiting_minutes": {"$gte": 0, "$lte": 10000}}}}, _store(sys_mod))
        assert out["total"] == 3  # 种子数据 3 张运单

    def test_contains(self, engine, sys_mod):
        eng, _ = engine
        out = eng.execute({"set": {"from": "Shipper", "where": {
            "shipper_name": {"$contains": "供应链"}}}}, _store(sys_mod))
        assert out["total"] >= 1

    def test_and_or_not_combo(self, engine, sys_mod):
        eng, _ = engine
        out = eng.execute({"set": {"from": "Waybill", "where": {
            "$and": [
                {"status": {"$exists": True}},
                {"$or": [{"status": {"$eq": "DELIVERED"}},
                         {"status": {"$eq": "LOADED"}}]},
                {"$not": {"status": {"$eq": "CREATED"}}},
            ]}}}, _store(sys_mod))
        assert out["total"] >= 1
        assert all(o["status"] in ("DELIVERED", "LOADED") for o in out["objects"])

    def test_shorthand_eq(self, engine, sys_mod):
        eng, _ = engine
        out = eng.execute({"set": {"from": "Waybill", "where": {
            "status": "DELIVERED"}}}, _store(sys_mod))
        assert all(o["status"] == "DELIVERED" for o in out["objects"])


class TestPredicateValidation:
    @pytest.mark.parametrize("bad,code", [
        ({"no_such_prop": 1}, "PREDICATE_PROP_UNKNOWN"),
        ({"estimated_fee": {"$weird": 1}}, "PREDICATE_OP_UNKNOWN"),
        ({"estimated_fee": {"$in": []}}, "PREDICATE_INVALID"),
        ({"estimated_fee": {"$gt": "abc"}}, "PREDICATE_TYPE_MISMATCH"),
        ("not a dict", "PREDICATE_INVALID"),
        ({}, "PREDICATE_INVALID"),
    ])
    def test_rejections(self, make_ext, bad, code):
        from freight.semantic import SemanticLayer
        sem = SemanticLayer()
        ext = make_ext()
        eng = ObjectQueryEngine(sem, ext)
        props = {p: None for p in sem.classes["Waybill"]["properties"]}
        for name in props:
            pt = ext.property_types.get(name)
            if pt:
                props[name] = pt.get("value_type")
        with pytest.raises(QueryError) as ei:
            validate_predicate(bad, props)
        assert ei.value.code == code

    def test_numeric_type_check_via_registry(self, engine, sys_mod):
        """E1 承重：注册表值类型驱动校验——estimated_fee 注册为数值，
        字符串比较值必须被拒（删注册则跳过校验）。"""
        eng, _ = engine
        with pytest.raises(QueryError, match="非数值比较"):
            eng.execute({"set": {"from": "Waybill", "where": {
                "estimated_fee": {"$gte": "four hundred"}}}}, _store(sys_mod))

    def test_load_bearing_delete_registration_skips_type_check(self, sys_mod, make_ext):
        """承重：删掉 estimated_fee 注册 -> 值类型校验随之消失（legacy 宽容）。"""

        def mutate(raw):
            raw["property_types"] = [
                p for p in raw["property_types"] if p["name"] != "estimated_fee"
            ]
            return raw

        ext = make_ext(mutate)
        eng = ObjectQueryEngine(sys_mod.sem, ext)
        # 数值注册删除后：字符串比较值不再被类型校验拦（谓词求值会 False 但不报错）
        out = eng.execute({"set": {"from": "Waybill", "where": {
            "estimated_fee": {"$gte": "four hundred"}}}}, _store(sys_mod))
        assert out["total"] == 0  # 求值为 False，全部过滤


# ---------------------------------------------------------------- E2 集合代数

class TestSetAlgebra:
    def test_union_inclusion_exclusion(self, engine, sys_mod):
        """union 数学性质：|A∪B| = |A| + |B| - |A∩B|。"""
        eng, _ = engine
        store = _store(sys_mod)
        a = eng.execute({"set": {"from": "Waybill"}}, store)["total"]
        b = eng.execute({"set": {"from": "FeeItem"}}, store)["total"]
        ab = eng.execute({"set": {"intersect": [
            {"from": "Waybill"}, {"from": "FeeItem"}]}}, store)["total"]
        u = eng.execute({"set": {"union": [
            {"from": "Waybill"}, {"from": "FeeItem"}]}}, store)["total"]
        assert u == a + b - ab

    def test_subtract(self, engine, sys_mod):
        eng, _ = engine
        store = _store(sys_mod)
        all_wb = eng.execute({"set": {"from": "Waybill"}}, store)["total"]
        delivered = eng.execute({"set": {"from": "Waybill", "where": {
            "status": {"$eq": "DELIVERED"}}}}, store)["total"]
        rest = eng.execute({"set": {"subtract": {
            "left": {"from": "Waybill"},
            "right": {"from": "Waybill", "where": {"status": {"$eq": "DELIVERED"}}}}}},
            store)["total"]
        assert rest == all_wb - delivered

    def test_follow_link_out(self, engine, sys_mod):
        eng, _ = engine
        out = eng.execute({"set": {"followLink": {
            "set": {"from": "Waybill", "where": {"waybill_id": {"$eq": "WB20261105001"}}},
            "link": "waybill_produces_fee", "direction": "out"}}}, _store(sys_mod))
        assert out["total"] >= 1
        assert all(o["type"] == "FeeItem" for o in out["objects"])

    def test_follow_link_vs_graph_parity(self, engine, sys_mod):
        """对拍：followLink 派生集与 REST graph 遍历结果一致。"""
        eng, _ = engine
        store = _store(sys_mod)
        via_dsl = {o["object_id"] for o in eng.execute({"set": {"followLink": {
            "set": {"from": "Waybill", "where": {"waybill_id": {"$eq": "WB20261105001"}}},
            "link": "waybill_produces_fee", "direction": "out"}}}, store)["objects"]}
        via_graph = set()
        for rel in sys_mod._relations_of("waybill:WB20261105001", "out"):  # noqa: SLF001
            if rel["predicate"] == "waybill_produces_fee" and rel.get("active", True):
                via_graph.add(rel["object_id"])
        assert via_dsl and via_dsl == via_graph

    def test_link_direction_violation(self, engine, sys_mod):
        eng, _ = engine
        with pytest.raises(QueryError, match="定义域"):
            eng.execute({"set": {"followLink": {
                "set": {"from": "Shipper"},
                "link": "waybill_produces_fee", "direction": "out"}}}, _store(sys_mod))

    def test_union_substitutes_inheritance(self, engine, sys_mod):
        """Palantir 无 OWL 继承的补偿：概念父类查询 = union 子类实例集。

        查"承运资源"（概念上的父类 = 司机 ∪ 车辆）不需要类型系统推理，
        直接组合代数 union 即可，且对拍：union total == 两子类分别查询之和。
        """
        eng, _ = engine
        store = _store(sys_mod)
        union = eng.execute({"set": {"union": [{"from": "Driver"},
                                               {"from": "Vehicle"}]}}, store)
        d = eng.execute({"set": {"from": "Driver"}}, store)
        v = eng.execute({"set": {"from": "Vehicle"}}, store)
        assert union["total"] == d["total"] + v["total"]
        types = {o["type"] for o in union["objects"]}
        assert types == {"Driver", "Vehicle"}
        # 统一视图上继续谓词过滤（父类视图的用法）
        idle = eng.execute({
            "set": {"union": [{"from": "Driver"}, {"from": "Vehicle"}]},
            "where": {"status": {"$eq": "IDLE"}}}, store)
        assert idle["total"] <= union["total"]

    def test_union_substitutes_inheritance(self, engine, sys_mod):
        """Palantir 无 OWL 继承的补偿：概念父类查询 = union 子类实例集。

        查"承运资源"（概念上的父类 = 司机 ∪ 车辆）不需要类型系统推理，
        直接组合代数 union 即可，且对拍：union total == 两子类分别查询之和。
        """
        eng, _ = engine
        store = _store(sys_mod)
        union = eng.execute({"set": {"union": [{"from": "Driver"},
                                               {"from": "Vehicle"}]}}, store)
        d = eng.execute({"set": {"from": "Driver"}}, store)
        v = eng.execute({"set": {"from": "Vehicle"}}, store)
        assert union["total"] == d["total"] + v["total"]
        types = {o["type"] for o in union["objects"]}
        assert types == {"Driver", "Vehicle"}
        # 统一视图上继续谓词过滤（父类视图的用法）
        idle = eng.execute({
            "set": {"union": [{"from": "Driver"}, {"from": "Vehicle"}]},
            "where": {"status": {"$eq": "IDLE"}}}, store)
        assert idle["total"] <= union["total"]

    def test_ids_set(self, engine, sys_mod):
        eng, _ = engine
        out = eng.execute({"set": {"ids": ["waybill:WB20261105001"]}}, _store(sys_mod))
        assert out["total"] == 1

    def test_ids_unknown_object(self, engine, sys_mod):
        eng, _ = engine
        with pytest.raises(QueryError, match="对象不存在"):
            eng.execute({"set": {"ids": ["waybill:NOPE"]}}, _store(sys_mod))


# ---------------------------------------------------------------- E3 管道

class TestPipeline:
    def test_order_by_desc(self, engine, sys_mod):
        eng, _ = engine
        out = eng.execute({
            "set": {"from": "Waybill"},
            "orderBy": [{"prop": "actual_fee", "dir": "desc"}],
        }, _store(sys_mod))
        fees = [o["props"]["actual_fee"] for o in out["objects"]]
        assert fees == sorted(fees, reverse=True)

    def test_aggregate_vs_sql_parity(self, engine, sys_mod):
        """对拍：DSL 聚合与 SQL 直算逐项相等。"""
        eng, _ = engine
        store = _store(sys_mod)
        dsl = eng.execute({
            "set": {"from": "Waybill"},
            "aggregate": {"count": True, "sum": ["actual_fee"],
                          "avg": ["actual_fee"], "min": ["actual_fee"],
                          "max": ["actual_fee"], "group_by": ["status"]},
        }, store)["groups"]
        sql_groups = store.all(
            "SELECT status, COUNT(*) n, SUM(json_extract(props,'$.actual_fee')) s, "
            "AVG(json_extract(props,'$.actual_fee')) a, "
            "MIN(json_extract(props,'$.actual_fee')) mn, "
            "MAX(json_extract(props,'$.actual_fee')) mx FROM objects WHERE type='Waybill' "
            "GROUP BY status")
        sql_by_status = {r["status"]: r for r in sql_groups}
        assert len(dsl) == len(sql_groups)
        for g in dsl:
            row = sql_by_status[g["group_by"]["status"]]
            assert g["count"] == row["n"]
            assert g["sum"]["actual_fee"] == pytest.approx(row["s"], abs=1e-6)
            assert g["avg"]["actual_fee"] == pytest.approx(row["a"], abs=1e-6)
            assert g["min"]["actual_fee"] == pytest.approx(row["mn"], abs=1e-6)
            assert g["max"]["actual_fee"] == pytest.approx(row["mx"], abs=1e-6)

    def test_aggregate_no_group_single_bucket(self, engine, sys_mod):
        eng, _ = engine
        out = eng.execute({
            "set": {"from": "Waybill"}, "aggregate": {"count": True},
        }, _store(sys_mod))
        assert len(out["groups"]) == 1
        assert out["groups"][0]["count"] == out["total"]

    def test_aggregate_unknown_prop(self, engine, sys_mod):
        eng, _ = engine
        with pytest.raises(QueryError, match="不属于类"):
            eng.execute({
                "set": {"from": "Waybill"},
                "aggregate": {"sum": ["no_such_prop"]}}, _store(sys_mod))

    def test_aggregate_non_numeric(self, engine, sys_mod):
        """sum/avg 属性非数值（注册表 value_type=string）-> 拦截。"""
        eng, _ = engine
        with pytest.raises(QueryError, match="不是数值类型"):
            eng.execute({
                "set": {"from": "Shipper"},
                "aggregate": {"sum": ["shipper_name"]}}, _store(sys_mod))

    def test_page(self, engine, sys_mod):
        eng, _ = engine
        store = _store(sys_mod)
        p1 = eng.execute({"set": {"from": "FeeItem"}, "page": {"limit": 3}}, store)
        p2 = eng.execute({"set": {"from": "FeeItem"}, "page": {"limit": 3, "offset": 3}},
                         store)
        assert p1["total"] == p2["total"]
        ids1 = {o["object_id"] for o in p1["objects"]}
        ids2 = {o["object_id"] for o in p2["objects"]}
        assert not (ids1 & ids2)

    def test_set_id_stable(self, engine, sys_mod):
        eng, _ = engine
        q = {"set": {"from": "Waybill"}, "page": {"limit": 2}}
        s1 = eng.execute(q, _store(sys_mod))["set_id"]
        s2 = eng.execute(q, _store(sys_mod))["set_id"]
        assert s1 == s2 and s1.startswith("os_")


# ---------------------------------------------------------------- E4 安全联动

class TestSecurityIntegration:
    def test_row_level_filters_total(self, engine, sys_mod):
        """行级安全影响集合：无 DISPUTE_OPS 标记的用户看不到 DISPUTED FeeItem。"""
        eng, enh = engine
        from enhancements.security import SecurityContext
        store = _store(sys_mod)
        ctx_plain = SecurityContext(user="nobody")
        ctx_rich = SecurityContext.with_roles(
            "ops", {"DISPUTE_OPS", "PII", "FINANCE"})
        q = {"set": {"from": "FeeItem"}}
        plain = eng.execute(q, store, ctx=ctx_plain)["total"]
        rich = eng.execute(q, store, ctx=ctx_rich)["total"]
        assert plain < rich

    def test_column_level_masks_props(self, engine, sys_mod):
        eng, _ = engine
        from enhancements.security import SecurityContext
        out = eng.execute(
            {"set": {"from": "Shipper"}, "page": {"limit": 5}},
            _store(sys_mod), ctx=SecurityContext(user="nobody"))
        assert out["objects"]
        assert all(o["props"]["shipper_name"] is None for o in out["objects"])

    def test_scope_token_blocks_class(self, engine, sys_mod):
        from enhancements.security import SecurityContext, AccessDenied
        eng, enh = engine
        ctx = SecurityContext.with_roles("agent", {"freight-core"})
        ctx.scope_token = enh.ext.scope_tokens["default_token"]
        with pytest.raises(AccessDenied):
            eng.security.check_scope(ctx, "class", "Quote")  # 令牌外类型


class TestRestEndpoint:
    @pytest.fixture()
    def client(self, tmp_path):
        from fastapi.testclient import TestClient
        from api import create_app
        os.environ.pop("OPENAI_CHAT_API_KEY", None)
        app = create_app(db_path=str(tmp_path / "f.db"), fresh=True)
        return TestClient(app)

    def test_rest_query(self, client):
        r = client.post("/freight/object-set/query", json={
            "set": {"from": "Waybill", "where": {"status": {"$eq": "DELIVERED"}}},
            "orderBy": [{"prop": "actual_fee", "dir": "desc"}],
        })
        assert r.status_code == 200
        assert r.json()["total"] >= 1

    def test_rest_rejects_bad_predicate(self, client):
        r = client.post("/freight/object-set/query", json={
            "set": {"from": "Waybill", "where": {"ghost": 1}}})
        assert r.status_code == 400
        assert "PREDICATE_PROP_UNKNOWN" in r.text

    def test_rest_security_masks(self, client):
        r = client.post(
            "/freight/object-set/query?user=nobody",
            json={"set": {"from": "Shipper"}, "page": {"limit": 5}})
        d = r.json()
        assert all(o["props"]["shipper_name"] is None for o in d["objects"])

    def test_rest_agent_scope_blocked(self, client):
        """agent 令牌查询令牌外类型 -> 403。"""
        r = client.post(
            "/freight/object-set/query?user=agent-readonly&roles=freight-core",
            json={"set": {"from": "Quote"}})
        assert r.status_code == 403

    def test_mcp_query_tree(self, client):
        r = client.post("/freight/mcp/call", json={
            "tool": "ontology.query",
            "arguments": {"type": "Waybill", "query_tree": {
                "set": {"from": "Waybill", "where": {"status": {"$eq": "DELIVERED"}}},
                "aggregate": {"count": True},
            }}})
        assert r.status_code == 200
        blob = r.text
        assert '"groups"' in blob and '"count"' in blob

    def test_mcp_flat_still_works(self, client):
        """向后兼容：扁平参数路径不受 query_tree 影响。"""
        r = client.post("/freight/mcp/call", json={
            "tool": "ontology.query",
            "arguments": {"type": "Driver", "where": [{"property": "fleet", "op": "eq",
                                                       "value": "华东车队"}], "limit": 5}})
        assert r.status_code == 200


# ---------------------------------------------------------------- E5 Schema

class TestSchemaAndOsdk:
    def test_schema_file_valid(self):
        import jsonschema
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "schema", "object_query.schema.json")
        with open(path, encoding="utf-8") as f:
            schema = json.load(f)
        jsonschema.Draft7Validator.check_schema(schema)
        good = {"set": {"from": "Waybill", "where": {"status": {"$in": ["A"]}}}}
        jsonschema.validate(good, schema)

    def test_generated_ts_has_builders(self):
        gen = importlib_helper()
        content = open(os.path.join(gen, "typescript.ts"), encoding="utf-8").read()
        assert "export interface WaybillFilters" in content
        assert "export function waybillQuery" in content
        assert "$gte" in content

    def test_generated_py_has_builders(self):
        gen = importlib_helper()
        content = open(os.path.join(gen, "python_client.py"), encoding="utf-8").read()
        assert "def waybill_query" in content
        assert "WaybillFilters" in content

    def test_load_bearing_new_class_queryable_zero_code(self, sys_mod, make_ext):
        """承重：本体加新类 -> 组合查询零代码可查（本体驱动查询面）。"""
        import copy
        ext = make_ext()
        sem = sys_mod.sem
        classes = copy.deepcopy(sem.classes)
        ghost = copy.deepcopy(classes["Driver"])
        ghost["id"] = "QueryGhost"
        ghost["properties"] = ["ghost_id", "ghost_name", "status"]
        ghost["key"] = "ghost_id"
        classes["QueryGhost"] = ghost
        # 用克隆 classes 的伪 sem
        fake_sem = copy.copy(sem)
        fake_sem.classes = classes
        eng = ObjectQueryEngine(fake_sem, ext)
        # 插一条事实
        store = _store(sys_mod)
        store.conn.execute(
            "INSERT INTO objects(object_id,type,name,status,props,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("ghost:G1", "QueryGhost", "幽灵", "ACTIVE",
             '{"ghost_id": "G1", "ghost_name": "幽灵", "status": "ACTIVE"}',
             "2026-11-19T00:00:00Z", "2026-11-19T00:00:00Z"))
        store.conn.commit()
        try:
            out = eng.execute({"set": {"from": "QueryGhost",
                                       "where": {"ghost_name": {"$contains": "幽"}}}},
                              store)
            assert out["total"] == 1
        finally:
            store.conn.execute("DELETE FROM objects WHERE type='QueryGhost'")
            store.conn.commit()


class TestTemporalFollowLink:
    """E6 时态回放：followLink.at 只取当时活跃的关系。"""

    def _inject_temporal_link(self, sys_mod):
        """注入一条已关闭的历史 link：历史时刻可见、现在不可见。"""
        store = _store(sys_mod)
        cur = store.one(
            "SELECT created_at FROM objects WHERE object_id=?",
            ("waybill:WB20261105001",))
        closed = store.conn.execute(
            "INSERT INTO relations(subject_id,predicate,object_id,subject_type,"
            "object_type,source_system,valid_from,valid_to,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("waybill:WB20261105001", "waybill_produces_fee", "fee:F001", "Waybill",
             "FeeItem", "ontology", "2026-11-05T08:00:00Z",
             "2026-11-05T12:00:00Z", cur["created_at"])).lastrowid
        store.conn.commit()
        return closed

    def test_at_replays_history(self, engine, sys_mod):
        eng, _ = engine
        store = _store(sys_mod)
        rel_id = self._inject_temporal_link(sys_mod)
        try:
            start = {"from": "Waybill",
                     "where": {"waybill_id": {"$eq": "WB20261105001"}}}
            base = {"set": {"followLink": {
                "set": start,
                "link": "waybill_produces_fee", "direction": "out"}}}
            now = eng.execute(base, store)["total"]
            historical = eng.execute({"set": {"followLink": {
                "set": start, "link": "waybill_produces_fee",
                "direction": "out", "at": "2026-11-05T10:00:00Z"}}}, store)["total"]
            assert historical >= 1
            # 关闭时间之后的历史时刻：注入关系已不可见，等于当前集
            after_close = eng.execute({"set": {"followLink": {
                "set": start, "link": "waybill_produces_fee",
                "direction": "out", "at": "2026-11-06T00:00:00Z"}}}, store)["total"]
            assert after_close == now
        finally:
            store.conn.execute("DELETE FROM relations WHERE id=?", (rel_id,))
            store.conn.commit()

    def test_at_rejects_non_string(self, engine, sys_mod):
        eng, _ = engine
        with pytest.raises(QueryError, match="at 必须是"):
            eng.execute({"set": {"followLink": {
                "set": {"from": "Waybill"},
                "link": "waybill_produces_fee", "direction": "out",
                "at": 123}}}, _store(sys_mod))


def importlib_helper() -> str:
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = os.path.join(root, ".pytest_tmp", "osdk_gen")
    subprocess.run([sys.executable, os.path.join(root, "tools", "gen_osdk.py"),
                    "--out", out], capture_output=True, text=True, cwd=root, check=True)
    return out


import sys  # noqa: E402
