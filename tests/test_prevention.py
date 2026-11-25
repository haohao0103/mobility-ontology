"""事前保证的回归保护：非法输入必须**当场**被拒，不许被悄悄改松。

对应 scripts/prevention_demo.py 的实证（2026-11-21）。
这些是**运行时强制**，不是事后扫描——所以它们必须被测试钉住：
一旦某个校验被放宽，这里立刻红。
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
from freight.semantic import BusinessReject, OntologyViolation  # noqa: E402


@pytest.fixture(scope="module")
def system():
    td = tempfile.mkdtemp(prefix="prevention_test_")
    sys_ = FreightSystem(os.path.join(td, "facts.db"), fresh=True, seed=True)
    yield sys_
    sys_.conn.close()


class TestRejectedAtWriteTime:
    def test_unregistered_property_rejected(self, system):
        with pytest.raises((OntologyViolation, BusinessReject)):
            system.add_object("Waybill", "waybill:PV01", "非法属性", "CREATED",
                              {"waybill_id": "PV01", "not_a_registered_prop": 1},
                              "2026-11-21T09:00:00+08:00")

    def test_illegal_status_rejected(self, system):
        with pytest.raises((OntologyViolation, BusinessReject)):
            system.add_object("Waybill", "waybill:PV02", "非法状态", "NOT_A_STATUS",
                              {"waybill_id": "PV02"}, "2026-11-21T09:00:00+08:00")

    def test_illegal_relation_range_rejected(self, system):
        with pytest.raises((OntologyViolation, BusinessReject)):
            system.add_relation("waybill_has_stop", "waybill:WB20261105001",
                                "driver:D001", "2026-11-21T09:00:00+08:00")


class TestRejectedAtExecutionTime:
    def test_illegal_transition_rejected(self, system):
        """已 DELIVERED 的运单不能再改派。"""
        with pytest.raises((OntologyViolation, BusinessReject)):
            system.execute_action("reassign_waybill", "waybill:WB20261105001",
                                  "u", "dispatcher", {"new_driver_id": "D003"})

    def test_unauthorized_role_rejected(self, system):
        with pytest.raises((OntologyViolation, BusinessReject)):
            system.execute_action("dispute_charge", "fee:F003", "u", "intern",
                                  {"reason": "越权申诉"})


class TestOntologyIsTheSourceOfTruth:
    def test_changing_rule_param_changes_computed_amount(self, system):
        """改本体里的计价参数 → 同一笔费用的应计值随之改变（代码零改动）。"""
        from freight.semantic import SemanticLayer, evaluate_rule_ast
        raw = json.loads(json.dumps(SemanticLayer().raw))
        # 改决策表命中行的参数：matrix 是唯一源。顶层标量是迁移后的死键，
        # 改它不生效——必须落到 4.2米厢货行（首行命中算这条）。
        for row in raw["pricing"]["waiting"]["matrix"]:
            if row.get("id") == "vehicle:4.2米厢货":
                row["params"]["per_block"] = 3.0     # 2.4 -> 3.0
        # 内存实验通道：发布 pin 只为磁盘发布物把门，不挡改声明看行为的承重测试
        mutated = SemanticLayer.from_raw(raw)
        wprops = json.loads(system.facts.get_row("waybill:WB20261105001")["props"])
        # 不传显式参数：参数必须从声明来，不能由调用方塞
        ctx = mutated._fee_context("waiting", {}, wprops, "4.2米厢货")
        ast_node = (mutated.rules["R-WAIT-01"].get("judge") or {}).get("ast")
        value = evaluate_rule_ast(ast_node, ctx)
        assert value == 15.0, f"改本体参数后应计值应为 15.0（5 块 × 3.0），实际 {value}"
        # 原参数下应为 12.0
        base = system.fee_analysis("waybill:WB20261105001")
        waiting = [b for b in base["breakdown"] if b["fee_type"] == "waiting"]
        assert waiting and waiting[0]["rule_expected"] == 12.0
