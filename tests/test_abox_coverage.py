"""ABox 深度校验（门禁第 26 项）+ 核心业务剧本的承重测试。

对应 plan/abox-depth-plan.md 的 V1/V2/V3：
- V1 深水区每个类型有对象实例；
- V2 深水区每个类型不是孤岛；
- V3 每个迁移动作被真实执行过（APPROVED 审计）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from freight import abox, scenarios  # noqa: E402
from freight.engine import FreightSystem  # noqa: E402
from freight.semantic import BusinessReject, SemanticLayer  # noqa: E402


@pytest.fixture(scope="module")
def layer():
    return SemanticLayer()


@pytest.fixture(scope="module")
def scenario_db():
    """种子数据 + 核心剧本的真实执行结果。"""
    td = tempfile.mkdtemp(prefix="abox_test_")
    system = FreightSystem(os.path.join(td, "facts.db"), fresh=True, seed=True)
    summary = scenarios.run_core_scenario(system)
    system.conn.commit()
    yield system, summary
    system.conn.close()


# ------------------------------------------------------------------ V1/V2/V3
class TestCoverage:
    def test_core_types_declared_in_ontology(self, layer):
        """深水区清单在本体 JSON 里（不是写死在代码里）。"""
        target = layer.raw.get("abox_targets")
        assert target, "本体缺少 abox_targets 声明"
        assert target["core_types"], "abox_targets.core_types 为空"

    def test_v1_every_core_type_has_instance(self, scenario_db, layer):
        system, _ = scenario_db
        m = abox.measure(layer, system.db_path)
        missing = [c["type"] for c in m["per_class"] if c["instances"] == 0]
        assert missing == [], f"深水区类型没有实例: {missing}"
        assert m["class_coverage"]["ratio"] == 1.0

    def test_v2_no_isolated_core_type(self, scenario_db, layer):
        system, _ = scenario_db
        m = abox.measure(layer, system.db_path)
        isolated = [c["type"] for c in m["per_class"]
                    if c["inbound_edges"] == 0 and c["outbound_edges"] == 0]
        assert isolated == [], f"深水区出现孤岛类型: {isolated}"

    def test_v3_every_action_exercised(self, scenario_db, layer):
        system, _ = scenario_db
        m = abox.measure(layer, system.db_path)
        not_run = [a["action"] for a in m["per_action"] if not a["exercised"]]
        assert not_run == [], f"动作从未被执行过: {not_run}"
        assert m["action_coverage"]["ratio"] == 1.0

    def test_check_returns_clean(self, scenario_db, layer):
        system, _ = scenario_db
        assert abox.check(layer, system.db_path) == []

    def test_gate_has_abox_check(self):
        from scripts.semantic_gate import check_abox_coverage
        item = check_abox_coverage(ROOT)
        assert item["ok"], item["issues"][:3]
        assert item["coverage"]["class_coverage"]["ratio"] == 1.0


# ------------------------------------------------------------------ 负向（判据不漏）
class TestCoverageNegatives:
    def _db(self):
        return abox.build_db(prefix="abox_neg_test_")

    def test_missing_instance_is_caught(self, layer):
        db = self._db()
        try:
            conn = sqlite3.connect(db)
            conn.execute("DELETE FROM objects WHERE type='Carrier'")
            conn.commit()
            conn.close()
            codes = {i["code"] for i in abox.check(layer, db)}
            assert "ABOX_CLASS_NO_INSTANCE" in codes
        finally:
            os.remove(db)

    def test_isolated_type_is_caught(self, layer):
        db = self._db()
        try:
            conn = sqlite3.connect(db)
            conn.execute("DELETE FROM relations WHERE subject_type='Stop' "
                         "OR object_type='Stop'")
            conn.commit()
            conn.close()
            codes = {i["code"] for i in abox.check(layer, db)}
            assert "ABOX_CLASS_ISOLATED" in codes
        finally:
            os.remove(db)

    def test_unexercised_action_is_caught(self, layer):
        db = self._db()
        try:
            conn = sqlite3.connect(db)
            conn.execute("DELETE FROM audit WHERE action_id='issue_quote' "
                         "AND decision='APPROVED'")
            conn.commit()
            conn.close()
            codes = {i["code"] for i in abox.check(layer, db)}
            assert "ABOX_ACTION_UNEXERCISED" in codes
        finally:
            os.remove(db)

    def test_changing_core_list_changes_verdict(self):
        """承重：改本体的深水区清单 -> 覆盖率判定随之改变（代码零改动）。"""
        from scripts.semantic_gate import check_abox_coverage
        raw = json.loads(json.dumps(SemanticLayer().raw))
        raw["abox_targets"]["core_types"] = raw["abox_targets"]["core_types"] + ["WaybillCost"]
        tmp = os.path.join(tempfile.mkdtemp(prefix="abox_decl_"), "layer.json")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False)
        sem = SemanticLayer(tmp)
        db = abox.build_db(prefix="abox_decl_db_")
        try:
            codes = {i["code"] for i in abox.check(sem, db)}
            assert "ABOX_CLASS_NO_INSTANCE" in codes, "把没实例的类加进清单，判定没反应"
        finally:
            os.remove(db)


# ------------------------------------------------------------------ 剧本
class TestScenario:
    def test_all_actions_executed_once(self, scenario_db):
        _, summary = scenario_db
        ids = {a["action"] for a in summary["actions"]}
        declared = {a["id"] for a in SemanticLayer().raw["actions"]}
        assert ids == declared, f"剧本未覆盖动作: {sorted(declared - ids)}"

    def test_state_transitions_match_declaration(self, scenario_db):
        """每个动作执行后的状态必须等于本体声明的 to_status。"""
        _, summary = scenario_db
        actions = {a["id"]: a for a in SemanticLayer().raw["actions"]}
        for step in summary["actions"]:
            expected = actions[step["action"]].get("to_status")
            assert step["after_status"] == expected, step

    def test_audit_and_events_written(self, scenario_db):
        system, summary = scenario_db
        audits = system.facts.all(
            "SELECT COUNT(*) n FROM audit WHERE decision='APPROVED'")[0]["n"]
        assert audits >= summary["action_count"]
        events = system.facts.all("SELECT COUNT(*) n FROM events")[0]["n"]
        assert events > 0


class TestNewActionImplementations:
    """补齐的 5 个动作运行时实现：业务前置条件必须真的拦得住。"""

    def _sys(self):
        td = tempfile.mkdtemp(prefix="abox_act_")
        return FreightSystem(os.path.join(td, "facts.db"), fresh=True, seed=True)

    def test_depart_stop_requires_arrival(self):
        s = self._sys()
        s.add_object("Stop", "stop:ACT01", "待离开站点", "PLANNED",
                     {"stop_id": "ACT01", "waybill_id": "WB20261105001",
                      "sequence": 1, "stop_type": "PICKUP"}, "2026-11-21T09:00:00+08:00")
        with pytest.raises(BusinessReject) as exc:
            s.execute_action("depart_stop", "stop:ACT01", "u", "driver", {})
        assert exc.value.code == "RULE_REJECTED"

    def test_issue_quote_rejects_expired(self):
        s = self._sys()
        s.add_object("Quote", "quote:ACT01", "过期报价", "DRAFT",
                     {"quote_id": "ACT01", "order_id": "WB20261105001",
                      "version": "1.0", "valid_until": "2020-01-01T00:00:00+08:00"},
                     "2026-11-21T09:00:00+08:00")
        with pytest.raises(BusinessReject) as exc:
            s.execute_action("issue_quote", "quote:ACT01", "u", "dispatcher", {})
        assert exc.value.code == "RULE_REJECTED"

    def test_supersede_quote_requires_existing_target(self):
        s = self._sys()
        s.add_object("Quote", "quote:ACT01", "待改版报价", "ACTIVE",
                     {"quote_id": "ACT01", "order_id": "WB20261105001",
                      "version": "1.0"}, "2026-11-21T09:00:00+08:00")
        with pytest.raises(BusinessReject) as exc:
            s.execute_action("supersede_quote", "quote:ACT01", "u", "dispatcher",
                             {"new_quote_id": "NOPE", "reason": "x"})
        assert exc.value.code == "VALIDATION_ERROR"

    def test_confirm_subcontract_rejects_over_allocation(self):
        s = self._sys()
        s.add_object("SubcontractOrder", "subcontract:ACT01", "倒挂分包", "DRAFT",
                     {"subcontract_id": "ACT01", "waybill_id": "WB20261105001",
                      "allocated_amount": 9999.0, "waybill_revenue": 100.0},
                     "2026-11-21T09:00:00+08:00")
        with pytest.raises(BusinessReject) as exc:
            s.execute_action("confirm_subcontract", "subcontract:ACT01", "u",
                             "dispatcher", {})
        assert exc.value.code == "RULE_REJECTED"

    def test_arrival_then_departure_flows(self):
        s = self._sys()
        s.add_object("Stop", "stop:ACT02", "正常站点", "PLANNED",
                     {"stop_id": "ACT02", "waybill_id": "WB20261105001",
                      "sequence": 2, "stop_type": "DELIVERY"},
                     "2026-11-21T09:00:00+08:00")
        s.execute_action("arrive_stop", "stop:ACT02", "u", "driver", {})
        out = s.execute_action("depart_stop", "stop:ACT02", "u", "driver", {})
        assert out["after_status"] == "COMPLETED"
