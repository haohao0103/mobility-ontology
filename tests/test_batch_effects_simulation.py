"""批量 apply / 跨对象 edits / 副作用 / what-if 仿真测试（P-07 / P-12 补齐）。"""
from __future__ import annotations

import json

import pytest

from freight.batch import BatchError


@pytest.fixture()
def env(system, enhanced):
    return system, enhanced


def _status(system, oid):
    row = system.facts.get_row(oid)
    return row["status"] if row else None


class TestBatch:
    def test_plan_and_apply(self, env):
        system, enh = env
        actions = [{"action_id": "dispute_charge", "object_id": "fee:F001",
                    "actor": "shipper-1", "role": "shipper",
                    "payload": {"reason": "金额不符"}}]
        assert enh.batch.plan(actions)["ready"] is True
        out = enh.batch.apply(actions, batch_key="BATCH-OK-1")
        assert out["status"] == "APPLIED" and out["count"] == 1
        assert _status(system, "fee:F001") == "DISPUTED"

    def test_failed_batch_rolls_back(self, env):
        system, enh = env
        actions = [
            {"action_id": "dispute_charge", "object_id": "fee:F002",
             "actor": "shipper-1", "role": "shipper",
             "payload": {"reason": "第一笔"}},
            {"action_id": "dispute_charge", "object_id": "no-such-fee",
             "actor": "shipper-1", "role": "shipper",
             "payload": {"reason": "第二笔必然失败"}},
        ]
        with pytest.raises(BatchError):
            enh.batch.apply(actions, batch_key="BATCH-FAIL-1")
        assert _status(system, "fee:F002") == "NORMAL"

    def test_batch_idempotency(self, env):
        _, enh = env
        actions = [{"action_id": "dispute_charge", "object_id": "fee:F002",
                    "actor": "shipper-1", "role": "shipper",
                    "payload": {"reason": "幂等"}}]
        out1 = enh.batch.apply(actions, batch_key="BATCH-IDEM-1")
        out2 = enh.batch.apply(actions, batch_key="BATCH-IDEM-1")
        assert out1["status"] == out2["status"] == "APPLIED"
        assert any(b["batch_key"] == "BATCH-IDEM-1" for b in enh.batch.history())

    def test_cross_object_edits(self, env):
        system, enh = env
        out = enh.batch.edits([
            {"object_id": "fee:F004", "set": {"calc_basis": "批量修改 A"}},
            {"object_id": "fee:F005", "set": {"calc_basis": "批量修改 B"}},
        ], actor="finance")
        assert out["objects"] == ["fee:F004", "fee:F005"]
        for oid in out["objects"]:
            props = json.loads(system.facts.get_row(oid)["props"])
            assert props["calc_basis"].startswith("批量修改")


class TestEffects:
    def test_declarations_from_ontology(self, env):
        _, enh = env
        decls = enh.effects.declarations("dispute_charge")
        assert any(d["type"] == "notification" for d in decls)
        assert any(d["type"] == "webhook" for d in enh.effects.declarations("hold_settlement"))

    def test_notification_logged_after_action(self, env):
        system, enh = env
        system.execute_action("dispute_charge", "fee:F006", "shipper-1", "shipper",
                              {"reason": "副作用测试"})
        logs = [r for r in enh.effects.log()
                if r["action_id"] == "dispute_charge" and r["effect_type"] == "notification"]
        assert logs and logs[0]["status"] == "NOTIFIED"


class TestSimulation:
    def test_whatif_does_not_touch_production(self, env):
        system, enh = env
        before = system.facts.get_row("fee:F007")["status"]
        out = enh.simulator.run([
            {"action_id": "dispute_charge", "object_id": "fee:F007",
             "actor": "sim", "role": "shipper", "payload": {"reason": "假设"}}])
        assert out["production_untouched"] is True
        assert out["succeeded"] == 1
        assert any(c["object_id"] == "fee:F007" for c in out["changes"])
        assert system.facts.get_row("fee:F007")["status"] == before

    def test_query_diff(self, env):
        _, enh = env
        out = enh.simulator.compare_query(
            [{"action_id": "dispute_charge", "object_id": "fee:F007",
              "actor": "sim", "role": "shipper", "payload": {"reason": "假设"}}],
            {"set": {"from": "FeeItem", "where": {"status": {"$eq": "DISPUTED"}}}})
        assert "fee:F007" in out["added"]
        assert out["production_untouched"] is True
