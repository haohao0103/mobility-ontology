"""运单-费用链「做深做全」的查询面覆盖（计划 V7）。

本体做深不只看"有多少数据"，还要看"这些数据能不能被查全、查准"。
本文件对同一套剧本数据跑 **9 类查询形态**，每类至少一条断言：

1. ObjectSet 单条件过滤
2. ObjectSet 多条件 + `$in`
3. ObjectSet 排序 + 分页（`next_page_token`）
4. 聚合（group_by + metrics）
5. search-around（邻域遍历）
6. traverse（多跳遍历）
7. Ontology SQL → 查询树 IR
8. 证据链（evidence_chain）
9. 对象图（graph）

数据来自 `freight/scenarios.py` 的核心剧本：18 类对象、真实动作迁移、真实关系边。
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from freight.engine import FreightSystem  # noqa: E402
from freight.object_query import ObjectQueryEngine  # noqa: E402
from freight.scenarios import run_core_scenario  # noqa: E402


@pytest.fixture(scope="module")
def system():
    td = tempfile.mkdtemp(prefix="query_surface_")
    sys_ = FreightSystem(os.path.join(td, "facts.db"), fresh=True, seed=True)
    run_core_scenario(sys_)
    sys_.conn.commit()
    yield sys_
    sys_.conn.close()


def _engine(system):
    return ObjectQueryEngine(system.sem)


class TestQuerySurfaces:
    def test_01_objectset_single_filter(self, system):
        out = _engine(system).execute(
            {"set": {"from": "FeeItem",
                     "where": {"fee_type": {"$eq": "waiting"}}}},
            system.facts.store)
        assert out["total"] >= 1
        assert all(r["props"].get("fee_type") == "waiting"
                   for r in out["objects"])

    def test_02_objectset_multi_condition_in(self, system):
        out = _engine(system).execute(
            {"set": {"from": "FeeItem",
                     "where": {"fee_type": {"$in": ["waiting", "loading"]},
                               "actual_amount": {"$gt": 0}}}},
            system.facts.store)
        assert out["total"] >= 2
        assert {r["props"]["fee_type"] for r in out["objects"]} <= {
            "waiting", "loading"}

    def test_03_objectset_sort_and_paging(self, system):
        # orderBy / page 是查询顶层键，不在 set 里（集合表达式只认 from/where/…）
        out = _engine(system).execute(
            {"set": {"from": "FeeItem",
                     "where": {"status": {"$eq": "NORMAL"}}},
             "orderBy": [{"prop": "actual_amount", "dir": "desc"}],
             "page": {"limit": 2}},
            system.facts.store)
        assert out["total"] >= 2
        assert len(out["objects"]) == 2
        amounts = [r["props"]["actual_amount"] for r in out["objects"]]
        assert amounts == sorted(amounts, reverse=True)
        assert out.get("next_page_token"), "分页应给出 next_page_token"

    def test_04_aggregate(self, system):
        out = system.facts.aggregate("FeeItem", group_by="fee_type",
                                     metrics=[{"op": "count"},
                                              {"op": "sum",
                                               "property": "actual_amount"}])
        assert out["count"] >= 1
        groups = out.get("rows") or []
        assert groups, "聚合应返回分组结果"
        assert all("count" in g for g in groups)

    def test_05_search_around(self, system):
        out = system.facts.search_around_objects(
            ["waybill:SCN01"], ["waybill_has_stop"], "out", 1)
        nodes = out.get("objects") or []
        assert len(nodes) >= 2, "运单 SCN01 周围应有 2 个站点"

    def test_06_traverse_multi_hop(self, system):
        out = system.facts.traverse_objects("waybill:SCN01", None, "out", 2)
        objects = out.get("objects") or []
        assert len(objects) >= 2, "两跳内应能走到站点与分包单"
        assert {o["type"] for o in objects} & {"Stop", "SubcontractOrder"}

    def test_07_ontology_sql(self, system):
        out = system.facts.sql(
            "SELECT object_id, actual_amount FROM FeeItem "
            "WHERE fee_type = 'waiting'")
        rows = out.get("objects") or out.get("rows") or []
        assert rows, "Ontology SQL 应返回等候费行"
        assert all(r.get("actual_amount") is not None for r in rows)

    def test_08_evidence_chain(self, system):
        out = system.mcp_call("function.evidence_chain",
                              {"waybill_id": "waybill:SCN01"})["result"]
        assert out, "证据链不应为空"

    def test_09_object_graph(self, system):
        out = system.graph("waybill:SCN01", depth=2)
        nodes = out.get("nodes") or []
        assert len(nodes) >= 3, "两跳图应含运单 + 站点 + 分包单"
        assert {n["type"] for n in nodes} & {"Stop", "SubcontractOrder"}


class TestCoverageDepthMetrics:
    """计划里的做深做全维度：V1/V2/V3/V4/V6 的度量必须站得住。"""

    def test_metrics(self, system):
        from freight import abox

        m = abox.measure(system.sem, system.db_path)
        assert m["class_coverage"]["ratio"] == 1.0
        assert m["edge_coverage"]["ratio"] == 1.0
        assert m["action_coverage"]["ratio"] == 1.0
        assert m["transition_consistency"]["consistent"] == \
            m["transition_consistency"]["total"]
        assert m["rules"]["unjudged"] == [], \
            f"未被判定过的规则: {m['rules']['unjudged']}"
