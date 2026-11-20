"""状态机独立层（P2-4）+ 动作参数类型化（P2-5）的承重测试。

对应曾经的缺口：
- 状态机只散落在动作的 from_status/to_status 上，没有独立声明、没有完备性校验；
- 动作参数只校验「必填」，类型错一个字节照样进动作，未声明参数也照单全收。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from freight.engine import FreightSystem  # noqa: E402
from freight.semantic import OntologyViolation, SemanticLayer  # noqa: E402


@pytest.fixture(scope="module")
def system():
    td = tempfile.mkdtemp(prefix="sm_test_")
    sys_ = FreightSystem(os.path.join(td, "facts.db"), fresh=True, seed=True)
    yield sys_
    sys_.conn.close()


@pytest.fixture()
def layer():
    return SemanticLayer()


# ------------------------------------------------------------------ 声明层
class TestStateMachineDeclaration:
    def test_every_action_target_declares_machine(self, layer):
        """有迁移动作的对象类型必须声明状态机（否则起点/终点说不清）。"""
        targets = {a["target"] for a in layer.raw["actions"] if a.get("to_status")}
        missing = sorted(targets - set(layer.state_machines))
        assert missing == [], f"有动作但未声明状态机: {missing}"

    def test_no_machine_without_action(self, layer):
        orphan = sorted(set(layer.state_machines) - {
            a["target"] for a in layer.raw["actions"] if a.get("to_status")})
        assert orphan == [], f"声明了状态机但没有迁移动作: {orphan}"

    def test_shipped_state_is_clean(self, layer):
        issues = [i for i in layer.validate_state_machines()
                  if i.get("severity") != "info"]
        assert issues == [], issues

    def test_declared_values_belong_to_status_set(self, layer):
        for type_id, decl in layer.state_machines.items():
            statuses = set(layer.classes[type_id]["statuses"])
            for field in ("initial", "terminal", "external_states"):
                bad = [s for s in decl.get(field, []) if s not in statuses]
                assert bad == [], f"{type_id}.{field} 含非法状态: {bad}"


# ------------------------------------------------------------------ 视图层
class TestStateMachineView:
    def test_transitions_have_stable_ids(self, layer):
        m = layer.state_machine("Waybill")
        ids = [t["id"] for t in m["transitions"]]
        assert ids == ["transition:Waybill:accept_order",
                       "transition:Waybill:reassign_waybill",
                       "transition:Waybill:confirm_loading",
                       "transition:Waybill:complete"]

    def test_reachable_matches_graph(self, layer):
        m = layer.state_machine("Waybill")
        assert set(m["reachable"]) == set(m["statuses"])
        assert m["unreachable"] == [] and m["dead_ends"] == []

    def test_machine_absent_for_plain_types(self, layer):
        """没有动作也没声明的类不该被硬塞进状态机模型（500 类里绝大多数如此）。"""
        assert layer.state_machine("WaybillAmendment") is None

    def test_view_lists_all_machines(self, layer):
        view = layer.state_machines_view()
        assert len(view["machines"]) == len(layer.state_machines)
        assert set(view["declared"]) == set(layer.state_machines)


# ------------------------------------------------------------------ 判据（负向）
class TestStateMachineValidation:
    def _validate_with(self, mutate):
        raw = json.loads(json.dumps(SemanticLayer().raw))
        mutate(raw)
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                          encoding="utf-8")
        json.dump(raw, tmp, ensure_ascii=False)
        tmp.close()
        try:
            sem = SemanticLayer(tmp.name)
            return {i["code"] for i in sem.validate_state_machines()}
        finally:
            os.remove(tmp.name)

    def test_undeclared_machine_caught(self):
        def mutate(raw):
            raw["state_machines"].pop("Waybill")
        assert "STATE_MACHINE_UNDECLARED" in self._validate_with(mutate)

    def test_terminal_with_outgoing_edge_caught(self):
        def mutate(raw):
            raw["state_machines"]["Waybill"]["terminal"] = ["ACCEPTED"]
        assert "STATE_MACHINE_TERMINAL_HAS_OUTGOING" in self._validate_with(mutate)

    def test_status_origin_unknown_caught(self):
        def mutate(raw):
            raw["state_machines"]["Invoice"]["external_states"] = []
        assert "STATE_MACHINE_STATUS_ORIGIN_MISSING" in self._validate_with(mutate)

    def test_unknown_status_in_declaration_caught(self):
        def mutate(raw):
            raw["state_machines"]["Waybill"]["initial"] = ["NOT_A_STATUS"]
        assert "STATE_MACHINE_DECL_INVALID" in self._validate_with(mutate)

    def test_orphan_declaration_caught(self):
        def mutate(raw):
            raw["state_machines"]["Ghost"] = {"initial": [], "terminal": [],
                                             "external_states": []}
        assert "STATE_MACHINE_ORPHAN_DECL" in self._validate_with(mutate)

    def test_gate_has_state_machine_check(self):
        from scripts.semantic_gate import check_state_machines
        item = check_state_machines(ROOT)
        assert item["ok"], item["issues"][:3]


# ------------------------------------------------------------------ 运行时
class TestStateMachineRuntime:
    def test_state_history_only_contains_status_events(self, system):
        hist = system.state_history("waybill:WB20261105001")
        statuses = set(hist["statuses"])
        assert hist["step_count"] > 0
        for step in hist["steps"]:
            assert step["status"] in statuses

    def test_object_view_lists_available_transitions(self, system):
        view = system.state_machine(object_id="waybill:WB20261105001")
        assert view["current_status"] == "DELIVERED"
        assert view["available_transitions"] == []

    def test_mcp_primitive_readable(self, system):
        out = system.mcp_call("ontology.state_machine", {"object_type": "FeeItem"})
        machine = out["result"]["machine"]
        assert machine["object_type"] == "FeeItem"
        assert len(machine["transitions"]) == 2


# ------------------------------------------------------------------ 动作参数
class TestActionParamTyping:
    ROLE = "customer_service"

    @pytest.fixture()
    def fee_id(self, system):
        """一个可申诉的费用项（dispute_charge 的前置状态是 NORMAL）。"""
        oid = "fee:PARAMPROBE"
        system.add_object("FeeItem", oid, "参数探针", "NORMAL",
                          {"fee_id": "PARAMPROBE", "fee_type": "waiting",
                           "actual_amount": 100, "quoted_amount": 100})
        return oid

    def test_wrong_param_type_rejected(self, system, fee_id):
        from freight.semantic import BusinessReject
        with pytest.raises(BusinessReject) as exc:
            system.execute_action("dispute_charge", fee_id, "u", self.ROLE,
                                  {"reason": 12345})
        assert exc.value.code == "VALIDATION_ERROR"
        assert "类型不符" in str(exc.value)

    def test_unknown_param_rejected(self, system, fee_id):
        from freight.semantic import BusinessReject
        with pytest.raises(BusinessReject) as exc:
            system.execute_action("dispute_charge", fee_id, "u", self.ROLE,
                                  {"reason": "x", "amount": 100})
        assert exc.value.code == "PARAM_UNKNOWN"

    def test_correct_param_still_accepted(self, system, fee_id):
        """类型校验不能把正常调用也拦了（承重的另一半）。"""
        out = system.execute_action("dispute_charge", fee_id, "u", self.ROLE,
                                    {"reason": "金额争议"})
        assert out["after_status"] == "DISPUTED"
