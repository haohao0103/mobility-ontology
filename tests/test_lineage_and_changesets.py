"""系统级血缘 + 对象数据变更集/分支/回滚测试（P-10 / P-03 补齐）。"""
from __future__ import annotations

import json

import pytest

from freight.change_set import ChangeSetError


@pytest.fixture()
def env(system, enhanced):
    return system, enhanced


class TestLineage:
    def test_graph_has_declarations(self, env):
        _, enh = env
        g = enh.lineage.build()
        kinds = {n["kind"] for n in g["nodes"]}
        assert {"class", "property", "data_source", "rule", "action"} <= kinds
        edge_kinds = {e["kind"] for e in g["edges"]}
        assert {"feeds", "has_property", "links", "used_by", "acts_on"} <= edge_kinds

    def test_property_bidirectional_trace(self, env):
        _, enh = env
        out = enh.lineage.trace_property("FeeItem", "fee_type")
        assert out["property"] == "FeeItem.fee_type"
        assert out["upstream"], "属性应能追到数据源/类"
        assert out["downstream"], "属性应能追到规则/动作"

    def test_summary(self, env):
        _, enh = env
        s = enh.lineage.summary()
        assert s["nodes"] > 0 and s["edges"] > 0
        assert s["by_kind"]["property"] > 0


class TestChangeSets:
    def test_apply_and_rollback(self, env):
        system, enh = env
        cs = enh.changes
        cs.create("CS-T1", "测试属性变更", "alice")
        cs.plan("CS-T1", "set_prop", "fee:F001", "settlement_status", "HOLD")
        preview = cs.preview("CS-T1")
        assert len(preview["items"]) == 1
        assert json.loads(preview["items"][0]["after"]) == "HOLD"
        applied = cs.apply("CS-T1")
        assert applied["status"] == "APPLIED"
        row = system.facts.store.one("SELECT props FROM objects WHERE object_id=?",
                                     ("fee:F001",))
        assert json.loads(row["props"])["settlement_status"] == "HOLD"
        rolled = cs.rollback("CS-T1")
        assert rolled["status"] == "ROLLED_BACK"
        row = system.facts.store.one("SELECT props FROM objects WHERE object_id=?",
                                     ("fee:F001",))
        assert "settlement_status" not in json.loads(row["props"])

    def test_draft_cannot_apply_twice(self, env):
        _, enh = env
        cs = enh.changes
        cs.create("CS-T2", "空变更集")
        assert cs.apply("CS-T2")["status"] == "APPLIED"
        with pytest.raises(ChangeSetError):
            cs.apply("CS-T2")

    def test_branch_snapshot_and_restore(self, env):
        system, enh = env
        cs = enh.changes
        before = cs.create_branch("before-edit", "改动前快照")
        assert before["objects"] > 0
        store = system.facts.store
        store.update_object("fee:F001", {"test_marker": "dirty"}, "x", None,
                            "2026-11-21T00:00:00Z")
        assert "test_marker" in json.loads(store.one(
            "SELECT props FROM objects WHERE object_id=?", ("fee:F001",))["props"])
        restored = cs.restore_branch("before-edit")
        assert restored["restored_objects"] == before["objects"]
        assert "test_marker" not in json.loads(store.one(
            "SELECT props FROM objects WHERE object_id=?", ("fee:F001",))["props"])

    def test_branch_list(self, env):
        _, enh = env
        enh.changes.create_branch("b-list")
        assert any(b["name"] == "b-list" for b in enh.changes.list_branches())
