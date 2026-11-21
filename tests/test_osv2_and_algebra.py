"""OSv2 增强物化 + Object Set 完整集合代数承重测试。

对应 Palantir 基准 P-04（对象物化）与 P-05（Object Set）的补齐：
- 跨表物化视图 / 地理索引 / 聚合预计算 / 自适应计划；
- symmetric_difference / product / pivot / join / fromView / geo；
- 所有能力由本体 JSON 声明驱动（承重：改 JSON、代码零改动）。
"""
from __future__ import annotations

import json

import pytest

from freight import projection_v2
from freight.object_query import ObjectQueryEngine


@pytest.fixture()
def ctx(system, enhanced):
    eng = ObjectQueryEngine(system.sem, enhanced.ext, enhanced.security)
    return eng, system, enhanced


class TestOSv2Materialization:
    def test_materialized_view_from_ontology(self, ctx):
        _, _, enh = ctx
        rows = projection_v2.view_rows(enh.store.conn, "waybill_fee_view")
        assert rows, "本体声明的跨表物化视图应有行"
        assert all("fees" in r for r in rows), "每行都应带 join 进来的 fees"
        assert all(r["type"] == "Waybill" for r in rows)

    def test_aggregate_cache_from_ontology(self, ctx):
        _, _, enh = ctx
        cached = projection_v2.cached_aggregate(
            enh.store.conn, "FeeItem", ["fee_type"], ["count", "sum:actual_amount"])
        assert cached is not None and cached["buckets"]

    def test_geo_index_and_query(self, ctx):
        _, system, enh = ctx
        store = system.facts.store
        store.insert_object(
            "GEO-1", "Waybill", "测试运单", "CREATED",
            {"latitude": 31.23, "longitude": 121.47, "status": "CREATED"},
            "2026-11-21T00:00:00Z")
        stats = projection_v2.stats(store.conn)
        assert stats["geo"] >= 1
        eng, _, _ = ctx
        out = eng.execute({"set": {"geo": {
            "lat0": 31.0, "lon0": 121.0, "lat1": 31.5, "lon1": 121.8}}}, store)
        assert out["total"] >= 1
        assert any(o["object_id"] == "GEO-1" for o in out["objects"])
        assert out["plan"] == projection_v2.PLAN_GEO

    def test_load_bearing_view_declaration(self, ctx):
        _, _, enh = ctx
        raw = json.loads(json.dumps(enh.ext.raw))
        raw["materialized_views"] = []
        enh.ext.raw = raw
        projection_v2.sync_all(enh.store.conn, enh.ext)
        assert projection_v2.view_rows(enh.store.conn, "waybill_fee_view") == []
        # 恢复，避免污染同会话后续用例
        enh.ext.raw = json.load(open(
            "/Users/mac/Desktop/code/mobility-ontology/enhancements/ontology_extension.json",
            encoding="utf-8"))
        projection_v2.sync_all(enh.store.conn, enh.ext)

    def test_consistency_clean(self, ctx):
        _, _, enh = ctx
        assert projection_v2.consistency(enh.store.conn) == []


class TestExtendedSetAlgebra:
    def test_symmetric_difference(self, ctx):
        eng, system, _ = ctx
        store = system.facts.store
        ids = [r["object_id"] for r in eng.execute(
            {"set": {"from": "FeeItem"}, "page": {"limit": 1}}, store)["objects"]]
        out = eng.execute({"set": {"symmetric_difference": [
            {"from": "FeeItem"}, {"ids": ids}]}}, store)
        assert out["total"] >= 1

    def test_product_pairs(self, ctx):
        eng, system, _ = ctx
        store = system.facts.store
        a = eng.execute({"set": {"from": "Shipper"}, "page": {"limit": 1}}, store)["objects"]
        b = eng.execute({"set": {"from": "Driver"}, "page": {"limit": 1}}, store)["objects"]
        assert a and b
        out = eng.execute({"set": {"product": [
            {"ids": [a[0]["object_id"]]}, {"ids": [b[0]["object_id"]]}]}}, store)
        assert out["total"] == 1
        assert any(k.startswith("left.") for k in out["objects"][0]["props"])

    def test_pivot_by_link(self, ctx):
        eng, system, _ = ctx
        out = eng.execute({"set": {"pivot": {
            "set": {"from": "Waybill"}, "byLink": "waybill_produces_fee",
            "direction": "out"}}}, system.facts.store)
        assert out["total"] >= 1
        assert all(o["type"] == "FeeItem" for o in out["objects"])

    def test_join_inner(self, ctx):
        eng, system, _ = ctx
        out = eng.execute({"set": {"join": {
            "left": {"from": "Waybill"}, "right": {"from": "Waybill"},
            "on": [{"left": "status", "right": "status"}], "type": "inner"}}},
            system.facts.store)
        assert out["total"] >= 1
        assert all(any(k.startswith("left.") for k in o["props"])
                   and any(k.startswith("right.") for k in o["props"])
                   for o in out["objects"])

    def test_from_view(self, ctx):
        eng, system, _ = ctx
        out = eng.execute({"set": {"fromView": "waybill_fee_view"}},
                          system.facts.store)
        assert out["total"] >= 1
        assert all("fees" in o["props"] or "view" in o for o in out["objects"])

    def test_plan_observability(self, ctx):
        eng, system, _ = ctx
        out = eng.execute({"set": {"from": "Waybill"},
                           "where": {"status": {"$eq": "DELIVERED"}}},
                          system.facts.store)
        assert out["plan"] in {
            projection_v2.PLAN_FULL_SCAN, projection_v2.PLAN_PROJECTION,
            projection_v2.PLAN_GEO, projection_v2.PLAN_AGG_CACHE,
            projection_v2.PLAN_VIEW,
        }
        plans = projection_v2.stats(system.facts.store.conn)["plans"]
        assert plans, "自适应计划调用统计应非空"
