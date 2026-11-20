"""派生对象类型（Derived Object Type）承重测试。

Palantir 无 OWL 继承推理的补偿机制最后一块：派生类型 = 命名查询视图
（组合代数/谓词），本体声明 derivation 查询树，查询引擎 from:派生类型
时递归求值。验证：
  1. 派生集 = derivation 树求值（union 组合 / 谓词过滤）
  2. 行级安全透过派生继续生效（DisputedFee 匿名不可见）
  3. 派生上聚合 / 谓词 / 未知属性拦截
  4. 循环引用 / 引用不存在类型 -> 结构化拒绝
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from freight.object_query import QueryError  # noqa: E402
from enhancements.security import SecurityContext  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_object_query import _store, engine, sys_mod  # noqa: E402


class TestDerivedQuery:
    def test_union_derived_matches_direct_union(self, engine, sys_mod):
        """派生类型 CarrierResource 与手写 union 求值完全一致（对拍）。"""
        eng, _ = engine
        store = _store(sys_mod)
        via_derived = eng.execute({"set": {"from": "CarrierResource"}}, store)
        via_union = eng.execute(
            {"set": {"union": [{"from": "Driver"}, {"from": "Vehicle"}]}}, store)
        ids_d = {o["object_id"] for o in via_derived["objects"]}
        ids_u = {o["object_id"] for o in via_union["objects"]}
        assert ids_d == ids_u and len(ids_d) == 6
        assert {o["type"] for o in via_derived["objects"]} == {"Driver", "Vehicle"}

    def test_predicate_on_derived(self, engine, sys_mod):
        """派生视图上继续谓词过滤（父类视图用法）。"""
        eng, _ = engine
        store = _store(sys_mod)
        out = eng.execute({
            "set": {"from": "CarrierResource"},
            "where": {"fleet": {"$eq": "华东车队"}}}, store)
        assert out["total"] >= 1
        assert all(o["props"].get("fleet") == "华东车队"
                   for o in out["objects"])

    def test_predicate_derived_prop_validation(self, engine, sys_mod):
        """派生类型未知属性 -> PREDICATE_PROP_UNKNOWN（TBox 管派生声明）。"""
        eng, _ = engine
        store = _store(sys_mod)
        with pytest.raises(QueryError, match="未知属性"):
            eng.execute({"set": {"from": "CarrierResource"},
                         "where": {"no_such": {"$eq": 1}}}, store)

    def test_aggregate_on_derived(self, engine, sys_mod):
        """聚合管道作用在派生视图上（group_by 派生声明属性）。"""
        eng, _ = engine
        store = _store(sys_mod)
        out = eng.execute({
            "set": {"from": "CarrierResource"},
            "aggregate": {"count": True, "group_by": ["status"]}}, store)
        assert out["total"] == 6
        assert sum(g["count"] for g in out["groups"]) == 6

    def test_aggregate_unknown_prop_on_derived(self, engine, sys_mod):
        """派生上聚合未知属性 -> 拒绝。"""
        eng, _ = engine
        store = _store(sys_mod)
        with pytest.raises(QueryError, match="不属于派生类型"):
            eng.execute({"set": {"from": "CarrierResource"},
                         "aggregate": {"sum": ["no_such"]}}, store)

    def test_row_level_security_through_derived(self, engine, sys_mod):
        """行级安全透过派生类型继续生效：DisputedFee 匿名不可见。"""
        eng, _ = engine
        store = _store(sys_mod)
        anon = SecurityContext(user="analyst", roles=set(), markings=set())
        ops = SecurityContext(user="ops", roles={"DISPUTE_OPS"},
                              markings={"DISPUTE_OPS"})
        out_anon = eng.execute({"set": {"from": "DisputedFee"}}, store, anon)
        out_ops = eng.execute({"set": {"from": "DisputedFee"}}, store, ops)
        assert out_anon["total"] == 0
        assert out_ops["total"] == 1
        assert out_ops["objects"][0]["props"]["fee_label"] == "等候费"


class TestDerivedValidation:
    def test_derived_cycle_rejected(self, engine, sys_mod):
        """循环引用 A -> B -> A -> DERIVED_CYCLE。"""
        eng, _ = engine
        eng.derived_types["BadA"] = {"id": "BadA",
                                     "derivation": {"from": "BadB"},
                                     "properties": ["status"]}
        eng.derived_types["BadB"] = {"id": "BadB",
                                     "derivation": {"from": "BadA"},
                                     "properties": ["status"]}
        store = _store(sys_mod)
        with pytest.raises(QueryError, match="循环引用"):
            eng.execute({"set": {"from": "BadA"}}, store)

    def test_derived_unknown_source_rejected(self, engine, sys_mod):
        """derivation 引用不存在类型 -> 拒绝。"""
        eng, _ = engine
        eng.derived_types["BadC"] = {"id": "BadC",
                                     "derivation": {"from": "NopeType"},
                                     "properties": ["status"]}
        store = _store(sys_mod)
        with pytest.raises(Exception, match="未定义的对象类型"):
            eng.execute({"set": {"from": "BadC"}}, store)

    def test_derived_unknown_type_in_from(self, engine, sys_mod):
        """from 一个既非实体类也非派生的类型 -> 拒绝。"""
        eng, _ = engine
        store = _store(sys_mod)
        with pytest.raises(Exception, match="未定义的对象类型"):
            eng.execute({"set": {"from": "GhostType"}}, store)


class TestDerivedTypeDiscovery:
    def test_object_type_search_finds_derived(self, sys_mod):
        """类型发现能搜到派生类型且 kind=derived（用户能锚定派生视图）。"""
        r = sys_mod.sem.object_type_search("承运资源", top_k=3)
        ids = [t["id"] for t in r["types"]]
        kinds = {t["id"]: t["kind"] for t in r["types"]}
        assert "CarrierResource" in ids
        assert kinds["CarrierResource"] == "derived"
        r2 = sys_mod.sem.object_type_search("争议费用", top_k=3)
        assert "DisputedFee" in [t["id"] for t in r2["types"]]


class TestDerivedTypeDiscovery:
    def test_object_type_search_finds_derived(self, sys_mod):
        """类型发现能搜到派生类型且 kind=derived（用户能锚定派生视图）。"""
        r = sys_mod.sem.object_type_search("承运资源", top_k=3)
        ids = [t["id"] for t in r["types"]]
        kinds = {t["id"]: t["kind"] for t in r["types"]}
        assert "CarrierResource" in ids
        assert kinds["CarrierResource"] == "derived"
        r2 = sys_mod.sem.object_type_search("争议费用", top_k=3)
        assert "DisputedFee" in [t["id"] for t in r2["types"]]
