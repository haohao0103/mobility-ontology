"""P1-5 搜索 + P4-4 使用度量承重测试。"""
from __future__ import annotations

import copy

import pytest

from enhancements.search import search_objects
from enhancements.usage_graph import UsageGraph
from enhancements.runtime import EnhancedSystem


class TestSearch:
    def test_search_by_query(self, enhanced):
        out = enhanced.search(q="张")
        assert out["total"] >= 1

    def test_search_type_filter(self, enhanced):
        out = enhanced.search(types=["Driver"], q="张")
        assert all(o["type"] == "Driver" for o in out["objects"])

    def test_search_unknown_type_rejected(self, enhanced):
        with pytest.raises(ValueError, match="未定义的对象类型"):
            enhanced.search(types=["Ghost"])

    def test_search_property_filter(self, enhanced):
        out = enhanced.search(types=["FeeItem"], status="DISPUTED")
        assert all(o["status"] == "DISPUTED" for o in out["objects"])

    def test_search_pagination(self, enhanced):
        out1 = enhanced.search(limit=3)
        out2 = enhanced.search(limit=3, offset=3)
        assert out1["limit"] == 3
        ids1 = {o["object_id"] for o in out1["objects"]}
        ids2 = {o["object_id"] for o in out2["objects"]}
        assert not (ids1 & ids2)

    def test_load_bearing_new_class_searchable_without_code(self, enhanced):
        """承重：本体加新类（只改定义）+ 插一条事实 -> 搜索无需改代码即可命中。"""
        ext = enhanced.ext
        ext.base_classes = dict(ext.base_classes)
        ghost = copy.deepcopy(ext.base_classes["Driver"])
        ghost["id"] = "TestTag"
        ghost["properties"] = ["tag_id", "tag_name", "status"]
        ghost["key"] = "tag_id"
        ext.base_classes["TestTag"] = ghost
        enhanced.store.conn.execute(
            "INSERT INTO objects(object_id,type,name,status,props,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("tag:T1", "TestTag", "幽灵标签", "ACTIVE",
             '{"tag_id": "T1", "tag_name": "幽灵标签", "status": "ACTIVE"}',
             "2026-11-19T00:00:00Z", "2026-11-19T00:00:00Z"),
        )
        enhanced.store.conn.commit()
        try:
            out = enhanced.search(types=["TestTag"])
            assert out["total"] == 1
            assert out["objects"][0]["object_id"] == "tag:T1"
        finally:
            enhanced.store.conn.execute("DELETE FROM objects WHERE type='TestTag'")
            enhanced.store.conn.commit()


class TestUsageGraph:
    def test_waybill_has_dependents(self, enhanced):
        g = UsageGraph(enhanced.ext, enhanced.system.sem.raw)
        deps = g.dependents("Waybill")
        kinds = {d["kind"] for d in deps}
        assert "relation" in kinds
        assert g.usage_summary()  # summary 可计算

    def test_dependents_orphan_class(self, enhanced):
        ext = enhanced.ext
        ext.base_classes = dict(ext.base_classes)
        orphan = copy.deepcopy(ext.base_classes["Driver"])
        orphan["id"] = "LonelyClass"
        ext.base_classes["LonelyClass"] = orphan
        g = UsageGraph(ext, enhanced.system.sem.raw)
        assert g.dependents("LonelyClass") == []
        summary = g.usage_summary()
        assert "LonelyClass" in summary["orphans"]

    def test_summary_structure(self, enhanced):
        out = UsageGraph(enhanced.ext, enhanced.system.sem.raw).usage_summary()
        assert out["total_classes"] > 500
        assert isinstance(out["orphans"], list)
        assert out["top_referenced"][0]["refs"] > 0

    def test_load_bearing_adding_dependency_updates_graph(self, enhanced, make_ext):
        """承重：本体加行级策略引用某类 -> 该类依赖数 +1（只改 JSON）。"""
        before = len(UsageGraph(enhanced.ext, enhanced.system.sem.raw)
                     .dependents("Driver"))
        ext = make_ext(lambda raw: {**raw, "object_security_policies": raw[
            "object_security_policies"] + [{
            "id": "osp-driver-test", "object_type": "Driver",
            "row_filter": {"property": "status", "equals": "OFFLINE"},
            "required_marking": "PII"}]})
        g = UsageGraph(ext, enhanced.system.sem.raw)
        after = len(g.dependents("Driver"))
        assert after == before + 1
