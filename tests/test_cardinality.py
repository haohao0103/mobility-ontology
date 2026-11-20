"""链接基数（cardinality）承重测试——对齐 Palantir Link Type 基数语义。

基数读法 domain->range：
  one_to_many  一个 domain 实例可挂多个 range，range 唯一归属
  many_to_one  domain 实例唯一挂一个 range
  one_to_one   双侧唯一
  many_to_many 不限制（缺省，未声明的历史链接向后兼容）
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from freight.engine import FreightSystem  # noqa: E402
from freight.semantic import OntologyViolation  # noqa: E402

T = "2026-11-05T12:00:00+08:00"


@pytest.fixture()
def sys_mod(tmp_path):
    fs = FreightSystem(db_path=str(tmp_path / "card.db"))
    return fs


def _mk(sys_mod, type_id, oid, key, status="CREATED", extra=None):
    props = {sys_mod.sem.class_of(type_id)["key"]: key}
    if extra:
        props.update(extra)
    sys_mod.add_object(type_id, oid, key, status, props, T)


class TestCardinalityWrite:
    def test_one_to_many_subject_can_have_many(self, sys_mod):
        """one_to_many：同一运单挂 7 条费用合法（seed 已证明），再挂新的也合法。"""
        _mk(sys_mod, "FeeItem", "fee:F999", "F999", "NORMAL",
            {"fee_type": "other"})
        sys_mod.add_relation("waybill_produces_fee",
                             "waybill:WB20261105001", "fee:F999", T)
        # 成功即通过

    def test_one_to_many_object_unique(self, sys_mod):
        """one_to_many：range 唯一归属——第二个运单抢已有费用必须被拒。"""
        _mk(sys_mod, "Waybill", "waybill:WB_CARD1", "WB_CARD1", "DELIVERED",
            {"order_id": "HL20261105001"})
        with pytest.raises(OntologyViolation, match="基数违规"):
            sys_mod.add_relation("waybill_produces_fee",
                                 "waybill:WB_CARD1", "fee:F001", T)

    def test_many_to_one_subject_unique(self, sys_mod):
        """many_to_one：一运单只挂一个司机——第二个司机必须被拒。"""
        _mk(sys_mod, "Driver", "driver:D999", "D999", "IDLE",
            {"fleet": "测试车队"})
        with pytest.raises(OntologyViolation, match="基数违规"):
            sys_mod.add_relation("waybill_assigned_to_driver",
                                 "waybill:WB20261105001", "driver:D999", T)

    def test_many_to_one_object_can_be_shared(self, sys_mod):
        """many_to_one：多个运单可先后挂同一个司机（司机接多单）。"""
        _mk(sys_mod, "Waybill", "waybill:WB_CARD2", "WB_CARD2", "DELIVERED",
            {"order_id": "HL20261105001"})
        sys_mod.add_relation("waybill_assigned_to_driver",
                             "waybill:WB_CARD2", "driver:D001", T)

    def test_one_to_one_both_sides(self, sys_mod):
        """one_to_one：order_has_quote 双侧唯一。"""
        _mk(sys_mod, "Quote", "quote:Q999", "Q999", "ACTIVE")
        with pytest.raises(OntologyViolation, match="基数违规"):
            sys_mod.add_relation("order_has_quote",
                                 "order:HL20261105001", "quote:Q999", T)

    def test_many_to_many_unrestricted(self, sys_mod):
        """many_to_many：证据链接无约束。"""
        _mk(sys_mod, "Evidence", "evidence:E999", "E999", "VALID",
            {"evidence_type": "PRICING_RULE"})
        sys_mod.add_relation("waybill_supported_by_evidence",
                             "waybill:WB20261105001", "evidence:E999", T)

    def test_temporal_close_releases_slot(self, sys_mod):
        """时态关闭后槽位释放：旧关系 close 后可重新挂（调拨场景）。"""
        _mk(sys_mod, "Driver", "driver:D998", "D998", "IDLE",
            {"fleet": "测试车队"})
        # 关闭 WB001 的司机关系，再挂新司机
        sys_mod.facts.store.conn.execute(
            "UPDATE relations SET valid_to=? WHERE predicate=? AND subject_id=?",
            (T, "waybill_assigned_to_driver", "waybill:WB20261105001"))
        sys_mod.facts.store.conn.commit()
        sys_mod.add_relation("waybill_assigned_to_driver",
                             "waybill:WB20261105001", "driver:D998", T)


class TestCardinalityValidation:
    def test_illegal_cardinality_value_rejected(self, sys_mod):
        """非法基数声明在写入校验时被拒。"""
        sys_mod.sem.relations["waybill_produces_fee"]["cardinality"] = "banana"
        _mk(sys_mod, "FeeItem", "fee:F998", "F998", "NORMAL",
            {"fee_type": "other"})
        with pytest.raises(OntologyViolation, match="基数声明非法"):
            sys_mod.add_relation("waybill_produces_fee",
                                 "waybill:WB20261105001", "fee:F998", T)

    def test_undeclared_defaults_to_unrestricted(self, sys_mod):
        """未声明基数的历史链接默认不限制（568 条中仅 16 条声明）。"""
        rel = sys_mod.sem.relations["waybill_supported_by_evidence"]
        assert rel.get("cardinality") == "many_to_many"
