"""候选模型 JSON → 提议通道提案包 承重测试。

契约：
- 转录忠实：properties+property_types → properties dict、key/description/aliases/
  group/why 原样带出，不丢 R10 的 why；
- 候选格式没有迁移动作 → transitions 留空并由提议通道校验器如实报缺（不替人编造）；
- 判定口径只用提议通道自己的 validate_proposal（不另立一套）；
- 我方无独立枚举实体 → enum 跳过并说明去向；
- 关系缺端点/缺基数 → needs_fix（可定位到错误码）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from candidate_to_proposals import (  # noqa: E402
    to_class_proposal,
    to_relation_proposal,
)
from freight.proposals import validate_proposal  # noqa: E402
from freight.semantic import SemanticLayer  # noqa: E402


@pytest.fixture(scope="module")
def sem():
    return SemanticLayer(str(ROOT / "freight" / "semantic_layer.json"))


def _candidate_class(**over):
    cls = {
        "id": "DeliveryReceipt",
        "label": "签收单",
        "key": "receipt_id",
        "description": "承运方交付货物后由收货方签收生成的凭证。",
        "properties": ["receipt_id", "waybill_id", "signed_at"],
        "property_types": {"receipt_id": "id", "waybill_id": "id",
                           "signed_at": "dt"},
        "state_machine": {"initial": "DRAFT", "terminal": "SIGNED",
                          "states": ["DRAFT", "SIGNED"]},
        "group": "order_transport",
        "why": "R1: 签收单有独立主键 receipt_id、跨时间稳定，故建对象类型",
    }
    cls.update(over)
    return cls


def _codes(proposal, sem):
    check = validate_proposal(proposal, sem)
    return {e["code"] for e in check["errors"]}


def test_class_transcription_keeps_types_and_why(sem):
    p = to_class_proposal(_candidate_class())
    payload = p["payload"]
    assert p["mutation_type"] == "class_add"
    assert payload["properties"] == {"receipt_id": "id", "waybill_id": "id",
                                     "signed_at": "dt"}
    assert payload["key"] == "receipt_id"
    assert payload["group"] == "order_transport"
    assert payload["why"].startswith("R1:")
    # 候选的 state_machine 是单值字符串 → 转成列表形态
    assert payload["state_machine"]["initial"] == ["DRAFT"]
    assert payload["state_machine"]["terminal"] == ["SIGNED"]
    assert payload["statuses"] == ["DRAFT", "SIGNED"]


def test_candidate_without_transitions_is_flagged_not_invented(sem):
    p = to_class_proposal(_candidate_class())
    assert p["payload"]["transitions"] == []
    codes = _codes(p, sem)
    assert "TRANSITIONS_MISSING" in codes


def test_missing_property_types_is_flagged(sem):
    p = to_class_proposal(_candidate_class(properties=["receipt_id", "x"],
                                           property_types={"receipt_id": "id"}))
    codes = _codes(p, sem)
    assert "PAYLOAD_TYPE" in codes


def test_missing_why_is_flagged(sem):
    p = to_class_proposal(_candidate_class(why=""))
    codes = _codes(p, sem)
    assert "WHY_MISSING" in codes


def test_unknown_group_is_flagged(sem):
    p = to_class_proposal(_candidate_class(group="no_such_group"))
    codes = _codes(p, sem)
    assert "TARGET_UNKNOWN" in codes


def test_duplicate_class_is_declined_not_error(sem):
    p = to_class_proposal(_candidate_class(id="Waybill", label="运单"))
    check = validate_proposal(p, sem)
    assert "PROPOSAL_ALREADY_DECLARED" in {e["code"] for e in check["errors"]}


def test_relation_transcription(sem):
    p = to_relation_proposal({
        "id": "waybill_issued_by", "label": "运单由货主开具",
        "domain": "Waybill", "range": "Shipper",
        "cardinality": "many_to_one",
        "why": "R8: 一张运单由一个货主开具，基数 many_to_one 依据签发事实"})
    assert p["mutation_type"] == "relation_add"
    assert p["payload"]["cardinality"] == "many_to_one"
    assert p["payload"]["why"].startswith("R8:")


def test_relation_unknown_endpoint_is_flagged(sem):
    p = to_relation_proposal({
        "id": "waybill_issued_by", "label": "运单由货主开具",
        "domain": "NoSuchClass", "range": "Shipper",
        "cardinality": "many_to_one",
        "why": "R8: 一张运单由一个货主开具，基数 many_to_one 依据签发事实"})
    codes = _codes(p, sem)
    assert "TARGET_UNKNOWN" in codes


def test_relation_missing_cardinality_is_flagged(sem):
    p = to_relation_proposal({
        "id": "waybill_issued_by", "label": "运单由货主开具",
        "domain": "Waybill", "range": "Shipper",
        "why": "R8: 一张运单由一个货主开具，基数 many_to_one 依据签发事实"})
    codes = _codes(p, sem)
    assert "PAYLOAD_VALUE" in codes


def test_real_candidate_package_end_to_end():
    """用仓库里的往返候选跑一遍转录：产出提案且类型受限。"""
    cand_path = ROOT / "tools" / "roundtrip_candidate.json"
    if not cand_path.exists():
        pytest.skip("往返候选文件不在")
    cand = json.loads(cand_path.read_text(encoding="utf-8"))
    proposals = [to_class_proposal(c) for c in cand.get("classes") or []
                 if isinstance(c, dict)]
    proposals += [to_relation_proposal(r) for r in cand.get("relations") or []
                  if isinstance(r, dict)]
    assert proposals, "往返候选应产出提案"
    assert {p["mutation_type"] for p in proposals} <= {"class_add",
                                                       "relation_add"}
    # 每个提案都必须带 why（R10 不因转录丢失）
    for p in proposals:
        assert p["payload"].get("why"), p["payload"].get("id")
