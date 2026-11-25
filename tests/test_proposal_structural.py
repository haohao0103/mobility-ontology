"""结构层提议（AI 辅助建模产物）承重测试。

契约（用户裁定 2026-11-25：目录开口只开「新增」，删除仍封闭）：
- 目录新增三类结构层变异：class_add / relation_add / group_add_type，全 t3；
- t3 = 全人审：auto_merge_eligible 恒 False，AI 候选永不自动进生产；
- why(R10) 必填：缺失/格式非法/规则号越界一律拒绝；
- R9 状态机完备：statuses 非空则 state_machine + transitions 必填，
  每个非终态有迁出、每个非初态有迁入；
- 编译层产出必须通过 validate_semantic 的 after 草案 diff（单一事实源）；
- 删除类/关系/规则仍然不在目录里（INVALID_MUTATION）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from freight.proposals import (  # noqa: E402
    MUTATION_CATALOG,
    VERDICT_ACCEPTED,
    VERDICT_DECLINED,
    VERDICT_REVIEW,
    adjudicate_proposal,
    build_adjudication_prompt,
    review_proposal,
    validate_proposal,
)
from freight.semantic import SemanticLayer  # noqa: E402


@pytest.fixture(scope="module")
def sem():
    return SemanticLayer(str(ROOT / "freight" / "semantic_layer.json"))


def _class_payload(**over):
    payload = {
        "id": "DeliveryReceipt",
        "label": "签收单",
        "key": "receipt_id",
        "description": "承运方交付货物后由收货方签收生成的凭证。",
        "properties": {"receipt_id": "id", "waybill_id": "id",
                       "signed_at": "dt", "receiver_name": "str"},
        "group": "order_transport",
        "statuses": ["DRAFT", "SIGNED", "VOID"],
        "state_machine": {"initial": ["DRAFT"], "terminal": ["SIGNED", "VOID"],
                          "external_states": []},
        "transitions": [
            {"action_id": "sign_receipt", "label": "签收", "roles": ["receiver"],
             "from_status": ["DRAFT"], "to_status": "SIGNED",
             "description": "收货方签收"},
            {"action_id": "void_receipt", "label": "作废", "roles": ["supervisor"],
             "from_status": ["DRAFT"], "to_status": "VOID",
             "description": "签收前作废"},
        ],
        "why": "R1: 签收单有独立主键 receipt_id、跨时间稳定，故建对象类型",
    }
    payload.update(over)
    return payload


def _class_proposal(**over):
    return {"mutation_type": "class_add", "payload": _class_payload(**over)}


def _codes(report: dict) -> set[str]:
    return {e["code"] for e in
            (report.get("validation") or {}).get("errors") or []}


# --- 目录与分级 -----------------------------------------------------------

def test_structural_types_are_in_catalog_and_t3():
    for name in ("class_add", "relation_add", "group_add_type"):
        assert name in MUTATION_CATALOG
        assert MUTATION_CATALOG[name]["tier"] == "t3"
        assert MUTATION_CATALOG[name]["layer"] == "structural"


def test_delete_still_closed(sem):
    report = review_proposal({"mutation_type": "class_delete",
                              "payload": {"id": "Waybill"}}, sem)
    assert report["verdict"] == "INVALID"
    assert "INVALID_MUTATION" in _codes(report)


def test_class_add_is_never_auto_merge(sem):
    report = review_proposal(_class_proposal(), sem)
    assert report["verdict"] == VERDICT_REVIEW
    assert report["tier"] == "t3"
    assert report["auto_merge_eligible"] is False
    assert report["breaking"] == 0
    assert report["new_issues"] == []


def test_class_add_compiles_into_after_draft(sem):
    report = review_proposal(_class_proposal(), sem)
    after = report.get("after") or {}
    assert after, "after 草案应随报告返回"
    cls = next(c for c in after["classes"] if c["id"] == "DeliveryReceipt")
    assert cls["key"] == "receipt_id"
    assert cls["source"] == "ai-assisted"
    assert cls["rid"].startswith("ri.mobility.main.object-type.")
    assert "DeliveryReceipt" in after["state_machines"]
    assert any(a.get("id") == "sign_receipt" for a in after["actions"])
    reg = {p["name"] for p in after["property_types"]}
    assert "receipt_id" in reg
    group = next(g for g in after["object_type_groups"]
                 if g["id"] == "order_transport")
    assert "DeliveryReceipt" in group["types"]


# --- R10 why 必填 ---------------------------------------------------------

def test_class_add_requires_why(sem):
    payload = _class_payload()
    payload.pop("why")
    report = review_proposal({"mutation_type": "class_add",
                              "payload": payload}, sem)
    assert report["verdict"] == "INVALID"
    assert "WHY_MISSING" in _codes(report)


def test_class_add_why_format_is_checked(sem):
    report = review_proposal(
        _class_proposal(why="因为需要它"), sem)
    assert "WHY_FORMAT" in _codes(report)


def test_class_add_why_rule_must_be_known(sem):
    report = review_proposal(
        _class_proposal(why="R99: 这条规则不存在"), sem)
    assert "WHY_RULE" in _codes(report)


def test_class_add_why_too_short(sem):
    report = review_proposal(_class_proposal(why="R1: 短"), sem)
    assert "WHY_TOO_SHORT" in _codes(report)


# --- R9 状态机完备 --------------------------------------------------------

def test_class_add_requires_statuses(sem):
    report = review_proposal(_class_proposal(statuses=[]), sem)
    assert "PAYLOAD_MISSING" in _codes(report)


def test_class_add_requires_state_machine(sem):
    report = review_proposal(_class_proposal(state_machine=None), sem)
    assert "STATE_MACHINE_INCOMPLETE" in _codes(report)


def test_class_add_requires_transitions(sem):
    report = review_proposal(_class_proposal(transitions=[]), sem)
    assert "TRANSITIONS_MISSING" in _codes(report)


def test_class_add_detects_status_without_exit_edge(sem):
    payload = _class_payload(transitions=[
        {"action_id": "sign_receipt", "label": "签收", "roles": ["receiver"],
         "from_status": ["DRAFT"], "to_status": "SIGNED",
         "description": "收货方签收"}])
    payload["statuses"] = ["DRAFT", "SIGNED", "PENDING"]
    payload["state_machine"] = {"initial": ["DRAFT"],
                                "terminal": ["SIGNED"], "external_states": []}
    report = review_proposal({"mutation_type": "class_add",
                              "payload": payload}, sem)
    assert "STATE_MACHINE_SINK" in _codes(report)


def test_class_add_detects_status_without_entry_edge(sem):
    payload = _class_payload(transitions=[
        {"action_id": "sign_receipt", "label": "签收", "roles": ["receiver"],
         "from_status": ["DRAFT"], "to_status": "SIGNED",
         "description": "收货方签收"}])
    payload["statuses"] = ["DRAFT", "SIGNED", "ORPHAN"]
    payload["state_machine"] = {"initial": ["DRAFT"],
                                "terminal": ["SIGNED", "ORPHAN"],
                                "external_states": []}
    report = review_proposal({"mutation_type": "class_add",
                              "payload": payload}, sem)
    assert "STATE_MACHINE_ORPHAN" in _codes(report)


def test_transition_requires_roles(sem):
    payload = _class_payload(transitions=[
        {"action_id": "sign_receipt", "label": "签收", "roles": [],
         "from_status": ["DRAFT"], "to_status": "SIGNED",
         "description": "收货方签收"},
        {"action_id": "void_receipt", "label": "作废", "roles": ["supervisor"],
         "from_status": ["DRAFT"], "to_status": "VOID",
         "description": "签收前作废"}])
    report = review_proposal({"mutation_type": "class_add",
                              "payload": payload}, sem)
    assert "PAYLOAD_TYPE" in _codes(report)


# --- 目标与冲突 -----------------------------------------------------------

def test_class_add_rejects_duplicate_id(sem):
    report = review_proposal(_class_proposal(id="Waybill"), sem)
    assert report["verdict"] == VERDICT_DECLINED
    assert "PROPOSAL_ALREADY_DECLARED" in _codes(report)


def test_class_add_rejects_bad_type_code(sem):
    report = review_proposal(
        _class_proposal(properties={"receipt_id": "id", "x": "money"}), sem)
    assert "PAYLOAD_TYPE" in _codes(report)


def test_class_add_rejects_key_not_in_properties(sem):
    report = review_proposal(_class_proposal(key="not_a_property"), sem)
    assert "PAYLOAD_VALUE" in _codes(report)


def test_class_add_rejects_unknown_group(sem):
    report = review_proposal(_class_proposal(group="no_such_group"), sem)
    assert "TARGET_UNKNOWN" in _codes(report)


def test_class_add_rejects_property_type_conflict(sem):
    # receipt_id 已注册为 id（string），提案声明为 int → 同名属性必须同类型
    payload = _class_payload(properties={"receipt_id": "id", "signed_at": "dt"})
    payload["properties"]["signed_at"] = "dt"
    payload["properties"]["another_id"] = "id"
    payload["key"] = "another_id"
    # 复用既有属性名并声明冲突类型：accepted_at 既有类型为 datetime
    payload["properties"]["accepted_at"] = "int"
    report = review_proposal({"mutation_type": "class_add",
                              "payload": payload}, sem)
    assert "PAYLOAD_VALUE" in _codes(report)


# --- relation_add ---------------------------------------------------------

def test_relation_add_is_review_only(sem):
    proposal = {"mutation_type": "relation_add", "payload": {
        "id": "waybill_issued_by", "label": "运单由货主开具",
        "domain": "Waybill", "range": "Shipper",
        "cardinality": "many_to_one",
        "why": "R8: 一张运单由一个货主开具，基数 many_to_one 依据签发事实"}}
    report = review_proposal(proposal, sem)
    assert report["verdict"] == VERDICT_REVIEW
    assert report["tier"] == "t3"
    assert report["new_issues"] == []


def test_relation_add_requires_explicit_cardinality(sem):
    proposal = {"mutation_type": "relation_add", "payload": {
        "id": "waybill_issued_by", "label": "运单由货主开具",
        "domain": "Waybill", "range": "Shipper",
        "why": "R8: 一张运单由一个货主开具，基数 many_to_one 依据签发事实"}}
    report = review_proposal(proposal, sem)
    assert "PAYLOAD_VALUE" in _codes(report)


def test_relation_add_rejects_unknown_endpoint(sem):
    proposal = {"mutation_type": "relation_add", "payload": {
        "id": "waybill_issued_by", "label": "运单由货主开具",
        "domain": "NoSuchClass", "range": "Shipper",
        "cardinality": "many_to_one",
        "why": "R8: 一张运单由一个货主开具，基数 many_to_one 依据签发事实"}}
    report = review_proposal(proposal, sem)
    assert "TARGET_UNKNOWN" in _codes(report)


# --- group_add_type -------------------------------------------------------

def _a_pair_not_yet_linked(sem):
    """找一对（目标组, 待挂入的类型）：类型存在但不在该组内（动态取）。"""
    groups = {g["id"]: set(g.get("types") or [])
              for g in sem.raw.get("object_type_groups") or []}
    for gid, types in groups.items():
        for other_gid, other_types in groups.items():
            if gid == other_gid:
                continue
            for cid in sorted(types):
                if cid not in other_types:
                    return other_gid, cid
    return None


def test_group_add_type_is_review_only(sem):
    pair = _a_pair_not_yet_linked(sem)
    if pair is None:
        pytest.skip("找不到可挂入的既有类")
    gid, cid = pair
    proposal = {"mutation_type": "group_add_type", "payload": {
        "group_id": gid, "type_id": cid,
        "why": "R5: 该类型属于此业务分类组，按业务归属挂入"}}
    report = review_proposal(proposal, sem)
    assert report["verdict"] == VERDICT_REVIEW
    assert report["new_issues"] == []


def test_group_add_type_rejects_duplicate_membership(sem):
    groups = {g["id"]: (g.get("types") or [])
              for g in sem.raw.get("object_type_groups") or []}
    for gid, types in groups.items():
        if not types:
            continue
        proposal = {"mutation_type": "group_add_type", "payload": {
            "group_id": gid, "type_id": types[0],
            "why": "R5: 该类型属于此业务分类组，按业务归属挂入"}}
        report = review_proposal(proposal, sem)
        assert report["verdict"] == VERDICT_DECLINED
        assert "PROPOSAL_ALREADY_DECLARED" in _codes(report)
        return
    pytest.skip("没有可重复挂入的分组")


# --- 结构层裁决合同 -------------------------------------------------------

def test_structural_prompt_uses_structural_contract(sem):
    messages = build_adjudication_prompt(_class_proposal(), sem)
    system = messages[0]["content"]
    assert "结构层" in system
    assert "R1" in system and "R3" in system


def test_non_structural_prompt_uses_base_contract(sem):
    proposal = {"mutation_type": "glossary_add_term",
                "payload": {"term": "甩挂", "type_ref": "Waybill"}}
    messages = build_adjudication_prompt(proposal, sem)
    assert "结构层" not in messages[0]["content"]


def test_structural_veto_is_recorded(sem):
    def _chat(messages):
        return json.dumps({
            "decision": "veto",
            "reason": "这是职责不是对象，应建成关系",
            "clause": 1,
            "final_proposal": None,
            "retype_attempt": {"unconstructible": "职责类不建对象类型"}},
            ensure_ascii=False)
    report = review_proposal(_class_proposal(), sem, chat_fn=_chat)
    assert report["verdict"] == VERDICT_DECLINED
    assert report["adjudication"]["decision"] == "veto"
    assert "unconstructible" in report["retype_attempt"]


def test_structural_keep_keeps_review_verdict(sem):
    def _chat(messages):
        return json.dumps({"decision": "keep", "reason": "确实是独立对象",
                           "clause": 1}, ensure_ascii=False)
    report = review_proposal(_class_proposal(), sem, chat_fn=_chat)
    assert report["verdict"] == VERDICT_REVIEW
    assert report["tier"] == "t3"


def test_structural_retype_keeps_mutation_type(sem):
    payload = _class_payload(label="签收凭证")
    def _chat(messages):
        return json.dumps({
            "decision": "retype", "reason": "label 更准确",
            "clause": 1,
            "final_proposal": {"mutation_type": "class_add",
                               "payload": payload}},
            ensure_ascii=False)
    report = review_proposal(_class_proposal(), sem, chat_fn=_chat)
    assert report["adjudicated_retyped"] is True
    assert report["proposal"]["payload"]["label"] == "签收凭证"
    assert report["verdict"] == VERDICT_REVIEW
